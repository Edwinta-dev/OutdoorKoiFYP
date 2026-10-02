"""Authentication for the digital twin API (issue #11).

Every pond route needs an `Authorization: Bearer <access token>` header
carrying the caller's Supabase session token. A request is accepted when:

1. the token verifies: signature (HS256 with SUPABASE_JWT_SECRET, or
   ES256/RS256 with a key from SUPABASE_JWKS_URL), the configured
   algorithm only, issuer, audience and expiry, and it carries sub and
   session_id. Otherwise 401.
2. the session it names still exists (auth_session_active, migration
   0007), so a signed-out or revoked session stops working before its
   token expires. Otherwise 401.
3. the pond in the path or body is the one linked to the account
   (UserData.auth_uid = sub). Otherwise 403, including when the account
   has no pond linked yet. A pond is never reachable just because its
   numeric id exists.

Path routes are checked before the view runs; body routes call
require_pond once the body has been validated, before any storage call.
/health and /ready stay open.

KOI_AUTH=disabled skips all of this and trusts the caller's user_id, as
the API did before. Settings refuses it unless KOI_ENV=development.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Protocol

import jwt
from flask import Flask, current_app, g, request

from koi.errors import ApiError
from koi.logs import log_event
from koi.settings import Settings
from koi.storage import Storage

log = logging.getLogger(__name__)

# Clock difference tolerated between Supabase Auth and this server.
LEEWAY_SEC = 30

# Endpoints that need no token.
OPEN_ENDPOINTS = {"twin.health", "twin.ready"}

_CHALLENGE = 'Bearer realm="koi"'


class AuthConfigError(RuntimeError):
    """Auth is required but the settings cannot verify a token."""


class Unauthenticated(ApiError):
    status = 401
    code = "invalid_token"

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message, code=code, headers={"WWW-Authenticate": _CHALLENGE})


class PondForbidden(ApiError):
    status = 403
    code = "pond_forbidden"


class SigningKeys(Protocol):
    """What PyJWKClient offers; tests pass an offline key set."""

    def get_signing_key_from_jwt(self, token: str) -> Any: ...


@dataclass(frozen=True)
class Identity:
    auth_uid: str
    session_id: str


class TokenVerifier:
    def __init__(self, settings: Settings, signing_keys: Optional[SigningKeys] = None):
        self.algorithm = settings.jwt_algorithm
        self.issuer = settings.resolved_jwt_issuer
        self.audience = settings.jwt_audience
        if not self.issuer:
            raise AuthConfigError("KOI_AUTH=required needs SUPABASE_URL or KOI_JWT_ISSUER for the token issuer")
        if self.algorithm == "HS256":
            secret = settings.supabase_jwt_secret.get_secret_value()
            if not secret:
                raise AuthConfigError("KOI_JWT_ALGORITHM=HS256 needs SUPABASE_JWT_SECRET")
            self._secret: Optional[str] = secret
            self._keys: Optional[SigningKeys] = None
        else:
            if signing_keys is None:
                if not settings.supabase_jwks_url:
                    raise AuthConfigError(f"KOI_JWT_ALGORITHM={self.algorithm} needs SUPABASE_JWKS_URL")
                signing_keys = jwt.PyJWKClient(settings.supabase_jwks_url, cache_keys=True)
            self._secret = None
            self._keys = signing_keys

    def verify(self, token: str) -> Identity:
        """The verified identity in token; raises Unauthenticated."""
        try:
            key: Any = self._secret if self._keys is None else self._keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=[self.algorithm], audience=self.audience, issuer=self.issuer,
                leeway=LEEWAY_SEC, options={"require": ["exp", "sub", "aud", "iss", "session_id"]})
        except jwt.ExpiredSignatureError:
            raise Unauthenticated("Your session has expired. Sign in again.", code="token_expired") from None
        except jwt.PyJWKClientConnectionError as exc:
            raise ApiError("The sign-in keys could not be fetched. Try again shortly.", status=503,
                           code="auth_unavailable") from exc
        except jwt.PyJWTError as exc:
            raise Unauthenticated(f"The access token is not valid: {exc}") from None
        try:
            return Identity(str(uuid.UUID(str(claims["sub"]))), str(uuid.UUID(str(claims["session_id"]))))
        except ValueError:
            raise Unauthenticated("The access token's sub or session_id is not a UUID.") from None


def init_app(app: Flask, settings: Settings, signing_keys: Optional[SigningKeys] = None) -> None:
    """Stores the verifier (None when auth is disabled). Raises
    AuthConfigError at start-up when auth is required but cannot work."""
    if settings.auth == "disabled":
        log_event(log, "auth_disabled", level=logging.WARNING, env=settings.env)
        app.extensions["koi_auth"] = None
        return
    app.extensions["koi_auth"] = TokenVerifier(settings, signing_keys)


def _storage() -> Storage:
    return current_app.extensions["koi_storage"]


def _bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthenticated("Sign in to see this pond: the request has no Bearer access token.",
                              code="auth_required")
    return token.strip()


def authenticate() -> None:
    """before_request for the twin routes."""
    if request.endpoint in OPEN_ENDPOINTS or request.method == "OPTIONS":
        return
    verifier: Optional[TokenVerifier] = current_app.extensions["koi_auth"]
    if verifier is None:
        return
    identity = verifier.verify(_bearer_token())
    if not _storage().is_session_active(identity.session_id, identity.auth_uid):
        raise Unauthenticated("This sign-in session has ended. Sign in again.", code="session_revoked")
    g.koi_identity = identity
    g.koi_pond_id = _storage().fetch_pond_id_for_account(identity.auth_uid)
    user_id = (request.view_args or {}).get("user_id")
    if user_id is not None:
        require_pond(user_id)


def require_pond(user_id: int) -> None:
    """403 unless user_id is the authenticated account's pond. A no-op
    when auth is disabled."""
    if current_app.extensions["koi_auth"] is None:
        return
    if "koi_identity" not in g:
        raise Unauthenticated("The request was not authenticated.", code="auth_required")
    pond_id = g.koi_pond_id
    if pond_id is None:
        raise PondForbidden("This account is not linked to a pond yet. Ask the system owner to link your pond.",
                            code="pond_not_linked")
    if pond_id != user_id:
        raise PondForbidden(f"This account cannot access pond {user_id}.", code="pond_forbidden",
                            details={"user_id": user_id})


__all__ = ["AuthConfigError", "Identity", "LEEWAY_SEC", "OPEN_ENDPOINTS", "PondForbidden", "SigningKeys",
           "TokenVerifier", "Unauthenticated", "authenticate", "init_app", "require_pond"]

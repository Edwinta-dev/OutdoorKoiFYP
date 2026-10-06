"""Sign-in for the digital twin API (koi/api/auth.py, issue #11).

Every token here is minted offline: HS256 with conftest.TEST_JWT_SECRET, or
ES256 with a key generated in the test and served from an in-memory JWKS.
No test fetches a real JWKS or reaches Supabase.

Run with: python -m pytest -q -k auth
"""
import uuid
from datetime import timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from conftest import (
    TEST_ISSUER,
    TEST_JWT_SECRET,
    USER,
    USER_AUTH_UID,
    USER_SESSION_ID,
    api_client,
    link_account,
    make_settings,
    make_storage,
    mint_token,
)
from koi.api import create_app
from koi.api.auth import AuthConfigError, TokenVerifier
from koi.settings import Settings

OTHER = 456                      # a second pond, owned by OTHER_AUTH_UID
OTHER_AUTH_UID = "a3e2b7c1-9f40-4d1e-8c55-456000000456"
OTHER_SESSION_ID = "d4c1e8f0-2b6a-4f73-9e10-456000000001"
UNLINKED_AUTH_UID = "f0e1d2c3-b4a5-4697-8879-000000000000"   # an account with no pond
UNLINKED_SESSION_ID = "e9d8c7b6-a5f4-4e3d-8c2b-000000000001"
UNKNOWN = 999                    # no UserData row at all

READS = ["/v1/ponds/{}/ratings/algae", "/v1/ponds/{}/assessments/chemistry",
    "/v1/ponds/{}/assessments/evaporation", "/v1/ponds/{}/assessments/algae",
         "/v1/ponds/{}/assessments", "/v1/ponds/{}/forecasts/chemistry",
             "/v1/ponds/{}/forecasts/evaporation", "/v1/ponds/{}/forecasts/algae"]
WRITES = [
    (f"/v1/ponds/{USER}/events/feeding", {"food_grams": 100.0, "protein_percent": 40.0}),
    (f"/v1/ponds/{USER}/events/water-change", {"volume_percent": 20.0}),
    (f"/v1/ponds/{USER}/events/top-up", {"volume_percent": 20.0}),
    (f"/v1/ponds/{USER}/events/algal-scrub", {"scrub_type": "brush"}),
    (f"/v1/ponds/{USER}/events/algae-rating", {"severity": "minor"}),
    (f"/v1/ponds/{USER}/events/algae-rating/undo", {}),
]


@pytest.fixture
def storage():
    """The fixture pond (455, USER_AUTH_UID), a second pond 456 linked to
    OTHER_AUTH_UID, and a signed-in account with no pond."""
    s = make_storage()
    s.add_rows("UserData", [{"userID": OTHER, "volume": 3000.0, "biomass": 5.0}])
    link_account(s, OTHER, OTHER_AUTH_UID, OTHER_SESSION_ID)
    s.add_rows("auth.sessions", [{"id": UNLINKED_SESSION_ID, "user_id": UNLINKED_AUTH_UID, "not_after": None}])
    return s


@pytest.fixture
def app(storage):
    app = create_app(make_settings(), storage=storage)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def anon(app):
    """A client that sends no Authorization header."""
    return app.test_client()


def as_user(app, **kwargs):
    return api_client(app, mint_token(**kwargs))


def as_other(app):
    return api_client(app, mint_token(sub=OTHER_AUTH_UID, session_id=OTHER_SESSION_ID))


def error(resp, status):
    assert resp.status_code == status, resp.get_json()
    body = resp.get_json()["error"]
    assert set(body) == {"code", "message", "details"}
    return body


# --- valid tokens -----------------------------------------------------

@pytest.mark.parametrize("path", READS)
def test_auth_valid_token_reads_its_own_pond(app, path):
    # Reaches the view: some reads answer 404/422 for the fixture pond's
    # data, but none is refused for sign-in.
    assert api_client(app).get(path.format(USER)).status_code not in (401, 403)


@pytest.mark.parametrize("path, body", WRITES[:5])
def test_auth_valid_token_logs_events_for_its_own_pond(app, path, body):
    assert api_client(app).post(path, json={"user_id": USER, **body}).status_code == 200


def test_auth_valid_token_within_clock_leeway_is_accepted(app):
    client = as_user(app, expires_in=-timedelta(seconds=10))
    assert client.get(f"/v1/ponds/{USER}/forecasts/evaporation").status_code == 200


def test_auth_scheme_name_is_case_insensitive(app):
    resp = app.test_client().get(f"/v1/ponds/{USER}/forecasts/evaporation",
        headers={"Authorization": f"bearer {mint_token()}"})
    assert resp.status_code == 200


def test_auth_health_and_ready_need_no_token(anon):
    assert anon.get("/health").status_code == 200
    assert anon.get("/ready").status_code in (200, 503)
    assert anon.get("/ready").get_json().get("error", {}).get("code") != "auth_required"


# --- unauthenticated --------------------------------------------------

@pytest.mark.parametrize("path", READS)
def test_auth_unauthenticated_read_is_401(anon, path):
    body = error(anon.get(path.format(USER)), 401)
    assert body["code"] == "auth_required"


@pytest.mark.parametrize("path, body", WRITES)
def test_auth_unauthenticated_event_is_401_and_writes_nothing(anon, storage, path, body):
    before = storage.fetch_snapshot_version(USER)
    assert error(anon.post(path, json={"user_id": USER, **body}), 401)["code"] == "auth_required"
    assert storage.fetch_snapshot_version(USER) == before
    assert storage.rows("algae_severity_ratings") == []


@pytest.mark.parametrize("header", ["", "Bearer", "Bearer   ", "Basic dXNlcjpwYXNz", mint_token()])
def test_auth_missing_or_non_bearer_header_is_401(anon, header):
    resp = anon.get(f"/v1/ponds/{USER}/assessments/chemistry", headers={"Authorization": header})
    assert error(resp, 401)["code"] == "auth_required"
    assert resp.headers["WWW-Authenticate"].startswith("Bearer")


# --- invalid tokens ---------------------------------------------------

def test_auth_expired_token_is_401(app):
    resp = as_user(app, expires_in=-timedelta(minutes=5)).get(f"/v1/ponds/{USER}/assessments/chemistry")
    assert error(resp, 401)["code"] == "token_expired"
    assert "WWW-Authenticate" in resp.headers


def test_auth_wrong_signature_is_401(app):
    token = mint_token(secret="a-different-secret-that-is-at-least-32-bytes")
    assert error(api_client(app, token).get(f"/v1/ponds/{USER}/assessments/chemistry"), 401)["code"] == "invalid_token"


def test_auth_tampered_payload_is_401(app):
    header, _, signature = mint_token().split(".")
    forged_payload = jwt.utils.base64url_encode(
        jwt.api_jws.json.dumps({"sub": OTHER_AUTH_UID, "session_id": OTHER_SESSION_ID, "aud": "authenticated",
                                "iss": TEST_ISSUER, "exp": 4102444800}).encode()).decode()
    token = f"{header}.{forged_payload}.{signature}"
    assert error(api_client(app, token).get(f"/v1/ponds/{OTHER}/assessments/chemistry"), 401)["code"] == "invalid_token"


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c", "eyJhbGciOiJIUzI1NiJ9..", "Bearer x"])
def test_auth_malformed_token_is_401(app, token):
    assert error(api_client(app, token).get(f"/v1/ponds/{USER}/assessments/chemistry"), 401)["code"] == "invalid_token"


def test_auth_unsigned_alg_none_token_is_401(app):
    token = jwt.encode({"sub": USER_AUTH_UID, "session_id": USER_SESSION_ID, "aud": "authenticated",
                        "iss": TEST_ISSUER, "exp": 4102444800}, key=None, algorithm="none")
    assert error(api_client(app, token).get(f"/v1/ponds/{USER}/assessments/chemistry"), 401)["code"] == "invalid_token"


@pytest.mark.parametrize("claims", [
    {"iss": "https://someone-else.invalid/auth/v1"},
    {"aud": "anon"},
    {"aud": None},
    {"iss": None},
    {"exp": None},
    {"sub": None},
    {"session_id": None},
    {"sub": "455"},              # not a UUID
    {"session_id": "abc"},
])
def test_auth_token_with_wrong_or_missing_claim_is_401(app, claims):
    assert error(as_user(app, **claims).get(f"/v1/ponds/{USER}/assessments/chemistry"), 401)["code"] == "invalid_token"


def test_auth_service_role_style_token_without_session_is_401(app):
    """The anon and service-role API keys are JWTs too; neither names a
    user session, so neither opens a pond."""
    token = jwt.encode({"role": "service_role", "iss": TEST_ISSUER, "aud": "authenticated",
                        "exp": 4102444800}, TEST_JWT_SECRET, algorithm="HS256")
    assert error(api_client(app, token).get(f"/v1/ponds/{USER}/assessments/chemistry"), 401)["code"] == "invalid_token"


# --- revoked sessions -------------------------------------------------

def test_auth_signed_out_session_is_401_before_the_token_expires(app, storage):
    storage._tables["auth.sessions"] = [r for r in storage._tables["auth.sessions"] if r["id"] != USER_SESSION_ID]
    resp = api_client(app).get(f"/v1/ponds/{USER}/assessments/chemistry")
    assert error(resp, 401)["code"] == "session_revoked"
    # The other account's session is unaffected.
    assert as_other(app).get(f"/v1/ponds/{OTHER}/ratings/algae").status_code == 200


def test_auth_session_past_not_after_is_401(app, storage):
    for row in storage._tables["auth.sessions"]:
        if row["id"] == USER_SESSION_ID:
            row["not_after"] = "2026-08-01T00:00:00+00:00"   # before the storage clock
    assert error(api_client(app).get(f"/v1/ponds/{USER}/assessments/chemistry"), 401)["code"] == "session_revoked"


def test_auth_session_of_another_account_is_401(app):
    """A token whose session_id belongs to another account's session."""
    client = as_user(app, session_id=OTHER_SESSION_ID)
    assert error(client.get(f"/v1/ponds/{USER}/assessments/chemistry"), 401)["code"] == "session_revoked"


# --- ponds: two accounts, forged ids, unlinked accounts -----------------

def test_auth_two_accounts_each_reach_only_their_own_pond(app):
    mine, theirs = api_client(app), as_other(app)
    assert mine.get(f"/v1/ponds/{USER}/ratings/algae").status_code == 200
    assert theirs.get(f"/v1/ponds/{OTHER}/ratings/algae").status_code == 200
    assert error(mine.get(f"/v1/ponds/{OTHER}/ratings/algae"), 403)["code"] == "pond_forbidden"
    assert error(theirs.get(f"/v1/ponds/{USER}/ratings/algae"), 403)["code"] == "pond_forbidden"


@pytest.mark.parametrize("path", READS)
def test_auth_wrong_pond_in_path_is_403(app, path):
    body = error(api_client(app).get(path.format(OTHER)), 403)
    assert body["code"] == "pond_forbidden"
    assert body["details"] == {"user_id": OTHER}


@pytest.mark.parametrize("path, body", WRITES)
def test_auth_forged_pond_in_body_is_mismatch_and_writes_nothing(app, storage, path, body):
    resp = api_client(app).post(path, json={"user_id": OTHER, **body})
    assert error(resp, 400)["code"] == "pond_mismatch"
    assert storage.fetch_snapshot_version(OTHER) == 0
    assert storage.rows("algae_severity_ratings") == []


def test_auth_unknown_pond_id_is_403_not_404(app):
    """A pond id gives no access whether or not it exists, and the reply
    does not reveal which."""
    client = api_client(app)
    assert error(client.get(f"/v1/ponds/{UNKNOWN}/assessments/chemistry"), 403)["code"] == "pond_forbidden"
    assert error(client.post(f"/v1/ponds/{UNKNOWN}/events/top-up", json={"user_id": UNKNOWN,
        "volume_percent": 5.0}), 403)


def test_auth_account_with_no_linked_pond_is_403(app):
    client = as_user(app, sub=UNLINKED_AUTH_UID, session_id=UNLINKED_SESSION_ID)
    assert error(client.get(f"/v1/ponds/{USER}/assessments/chemistry"), 403)["code"] == "pond_not_linked"
    resp = client.post(f"/v1/ponds/{USER}/events/top-up", json={"user_id": USER, "volume_percent": 5.0})
    assert error(resp, 403)["code"] == "pond_not_linked"


def test_auth_identity_comes_from_the_token_not_the_request(app):
    """Headers or body fields naming another account change nothing."""
    resp = api_client(app).post(f"/v1/ponds/{OTHER}/events/top-up", json={"user_id": OTHER, "volume_percent": 5.0,
                                                         "auth_uid": OTHER_AUTH_UID, "sub": OTHER_AUTH_UID})
    assert error(resp, 403)["code"] == "pond_forbidden"


# --- JWKS (ES256) -------------------------------------------------------

class OfflineJwks:
    """The PyJWKClient interface over an in-memory key set."""

    def __init__(self, *public_keys_by_kid):
        self.keys = jwt.PyJWKSet([
            {**jwt.algorithms.ECAlgorithm.to_jwk(key, as_dict=True), "kid": kid, "alg": "ES256", "use": "sig"}
            for kid, key in public_keys_by_kid])

    def get_signing_key_from_jwt(self, token):
        kid = jwt.get_unverified_header(token).get("kid")
        for key in self.keys.keys:
            if key.key_id == kid:
                return key
        raise jwt.PyJWKClientError(f"no key {kid!r}")


@pytest.fixture
def es256():
    key = ec.generate_private_key(ec.SECP256R1())
    return key, OfflineJwks(("koi-test-kid", key.public_key()))


def es_app(storage, jwks):
    settings = make_settings(jwt_algorithm="ES256", supabase_jwt_secret="",
                             supabase_jwks_url="https://koi-test.invalid/auth/v1/.well-known/jwks.json")
    return create_app(settings, storage=storage, signing_keys=jwks)


def test_auth_jwks_es256_token_is_accepted(storage, es256):
    key, jwks = es256
    token = mint_token(secret=key, algorithm="ES256", headers={"kid": "koi-test-kid"})
    assert api_client(es_app(storage, jwks), token).get(f"/v1/ponds/{USER}/forecasts/evaporation").status_code == 200


def test_auth_jwks_token_signed_by_another_key_is_401(storage, es256):
    _, jwks = es256
    stranger = ec.generate_private_key(ec.SECP256R1())
    token = mint_token(secret=stranger, algorithm="ES256", headers={"kid": "koi-test-kid"})
    assert error(api_client(es_app(storage, jwks), token).get(f"/v1/ponds/{USER}/assessments/chemistry"),
        401)["code"] == "invalid_token"


def test_auth_jwks_unknown_kid_is_401(storage, es256):
    key, jwks = es256
    token = mint_token(secret=key, algorithm="ES256", headers={"kid": "rotated-away"})
    assert error(api_client(es_app(storage, jwks), token).get(f"/v1/ponds/{USER}/assessments/chemistry"),
        401)["code"] == "invalid_token"


def test_auth_jwks_mode_refuses_an_hs256_token(storage, es256):
    """Algorithm confusion: only the configured algorithm is accepted."""
    _, jwks = es256
    token = mint_token(headers={"kid": "koi-test-kid"})
    assert error(api_client(es_app(storage, jwks), token).get(f"/v1/ponds/{USER}/assessments/chemistry"),
        401)["code"] == "invalid_token"


def test_auth_hs256_mode_refuses_an_es256_token(app):
    key = ec.generate_private_key(ec.SECP256R1())
    token = mint_token(secret=key, algorithm="ES256")
    assert error(api_client(app, token).get(f"/v1/ponds/{USER}/assessments/chemistry"), 401)["code"] == "invalid_token"


def test_auth_jwks_unreachable_is_503(storage):
    class Down:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientConnectionError("connection refused")

    resp = api_client(es_app(storage, Down()), mint_token()).get(f"/v1/ponds/{USER}/assessments/chemistry")
    assert error(resp, 503)["code"] == "auth_unavailable"


# --- start-up configuration -----------------------------------------------

def test_auth_is_required_by_default(monkeypatch):
    monkeypatch.delenv("KOI_AUTH", raising=False)
    monkeypatch.delenv("KOI_ENV", raising=False)
    assert Settings(_env_file=None).auth == "required"


@pytest.mark.parametrize("overrides, message", [
    ({"supabase_jwt_secret": ""}, "SUPABASE_JWT_SECRET"),
    ({"jwt_algorithm": "ES256", "supabase_jwks_url": ""}, "SUPABASE_JWKS_URL"),
    ({"jwt_algorithm": "RS256", "supabase_jwks_url": ""}, "SUPABASE_JWKS_URL"),
    ({"jwt_issuer": "", "supabase_url": ""}, "KOI_JWT_ISSUER"),
])
def test_auth_required_without_keys_refuses_to_start(overrides, message):
    with pytest.raises(AuthConfigError, match=message):
        create_app(make_settings(**overrides), storage=make_storage())


def test_auth_issuer_defaults_to_the_project_auth_url():
    settings = make_settings(jwt_issuer="", supabase_url="https://ref.supabase.co/")
    assert TokenVerifier(settings).issuer == "https://ref.supabase.co/auth/v1"


def test_auth_jwks_client_is_built_from_the_url_without_fetching():
    settings = make_settings(jwt_algorithm="ES256", supabase_jwt_secret="",
                             supabase_jwks_url="https://koi-test.invalid/auth/v1/.well-known/jwks.json")
    verifier = TokenVerifier(settings)   # PyJWKClient fetches lazily, on the first token
    assert isinstance(verifier._keys, jwt.PyJWKClient)


# --- development bypass -------------------------------------------------

def test_auth_bypass_in_development_trusts_the_request_user_id(storage):
    app = create_app(make_settings(env="development", auth="disabled", supabase_jwt_secret=""), storage=storage)
    client = app.test_client()
    assert client.get(f"/v1/ponds/{USER}/forecasts/evaporation").status_code == 200
    assert client.get(f"/v1/ponds/{OTHER}/ratings/algae").status_code == 200
    assert client.post(f"/v1/ponds/{USER}/events/top-up", json={"user_id": USER,
        "volume_percent": 5.0}).status_code == 200


@pytest.mark.parametrize("env", ["production", "test"])
def test_auth_bypass_is_refused_outside_development(env):
    with pytest.raises(ValidationError, match="KOI_AUTH=disabled is only allowed with KOI_ENV=development"):
        make_settings(env=env, auth="disabled")


def test_auth_bypass_from_the_environment_is_refused_in_production(monkeypatch):
    monkeypatch.setenv("KOI_ENV", "production")
    monkeypatch.setenv("KOI_AUTH", "disabled")
    with pytest.raises(ValidationError, match="KOI_AUTH=disabled"):
        Settings(_env_file=None)


def test_auth_bypass_entry_point_refuses_production(monkeypatch):
    """python -m koi.api builds Settings from the environment, so a
    production process with the bypass set fails before serving."""
    import koi.api.__main__ as api_main

    monkeypatch.setenv("KOI_ENV", "production")
    monkeypatch.setenv("KOI_AUTH", "disabled")
    # As get_settings, minus the developer's real Backend/.env.
    monkeypatch.setattr(api_main, "get_settings", lambda: Settings(_env_file=None))
    with pytest.raises(ValidationError, match="KOI_AUTH=disabled"):
        api_main.main(run=False)


# --- storage: account links -------------------------------------------------

def test_auth_memory_storage_finds_the_linked_pond(storage):
    assert storage.fetch_pond_id_for_account(USER_AUTH_UID) == USER
    assert storage.fetch_pond_id_for_account(OTHER_AUTH_UID) == OTHER
    assert storage.fetch_pond_id_for_account(UNLINKED_AUTH_UID) is None
    assert storage.fetch_pond_id_for_account(str(uuid.uuid4())) is None


def test_auth_memory_storage_session_checks(storage):
    assert storage.is_session_active(USER_SESSION_ID, USER_AUTH_UID)
    assert not storage.is_session_active(USER_SESSION_ID, OTHER_AUTH_UID)
    assert not storage.is_session_active(str(uuid.uuid4()), USER_AUTH_UID)


def test_auth_storage_failure_is_not_a_sign_in(app, storage):
    storage.failing.add("is_session_active")
    resp = api_client(app).get(f"/v1/ponds/{USER}/assessments/chemistry")
    assert resp.status_code >= 500

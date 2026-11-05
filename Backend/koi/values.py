"""Small value coercions shared by the models, storage, API and weather
packages. Dependency-free so any of them can import it."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional


def parse_timestamp(value: object) -> datetime:
    """A timestamp column value (ISO string or datetime) as an aware
    datetime; one without a zone is taken as UTC."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def finite_number(value: Any) -> Optional[float]:
    """value as a float when it is a finite int or float (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None

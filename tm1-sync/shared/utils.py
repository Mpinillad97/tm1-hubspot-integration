import hashlib
from datetime import datetime, timezone
from typing import Any, Optional


def sha10(value: str) -> str:
    """PII-safe short hash for logs."""
    return hashlib.sha256(value.lower().encode("utf-8")).hexdigest()[:10]


def iso_to_ms(value: Optional[str]) -> Optional[int]:
    """Convert ISO datetime (e.g. ...Z) to epoch ms."""
    if not value or not isinstance(value, str):
        return None
    try:
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None
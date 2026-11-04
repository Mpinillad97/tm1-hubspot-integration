import json
import os
import random
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Context carried across a run
_run_id: ContextVar[str] = ContextVar("run_id", default="")
_client_id: ContextVar[str] = ContextVar("client_id", default="")
_component: str = "sync_job"

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_level() -> str:
    return (os.getenv("LOG_LEVEL") or "INFO").strip().upper()


def _level_num(level: str) -> int:
    return _LEVELS.get(level.upper(), 20)


def _sample_rate() -> float:
    v = os.getenv("LOG_ITEM_SAMPLE_RATE", "0.10").strip()
    try:
        f = float(v)
        return max(0.0, min(1.0, f))
    except Exception:
        return 0.10


def _http_body_chars() -> int:
    v = os.getenv("LOG_HTTP_BODY_CHARS", "700").strip()
    try:
        return max(0, int(v))
    except Exception:
        return 700


def _include_dedupe_keys() -> bool:
    v = os.getenv("LOG_INCLUDE_DEDUPE_KEYS", "false").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def new_run_id() -> str:
    return str(uuid.uuid4())


def set_run_context(*, run_id: str, client_id: str) -> None:
    _run_id.set(run_id)
    _client_id.set(client_id)


def get_run_id() -> str:
    return _run_id.get()


def get_client_id() -> str:
    return _client_id.get()


def log(event: str, level: str = "INFO", **fields: Any) -> None:
    """
    Structured JSON log. Always includes ts, event, run_id, client_id, component.
    """
    current_level = _get_level()
    if _level_num(level) < _level_num(current_level):
        return

    payload: Dict[str, Any] = {
        "ts": _now_iso(),
        "level": level.upper(),
        "event": event,
        "run_id": get_run_id(),
        "client_id": get_client_id(),
        "component": _component,
    }

    # Attach fields
    for k, v in fields.items():
        payload[k] = v

    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log_item(event: str, **fields: Any) -> None:
    """
    Per-item log with sampling
    """
    if random.random() > _sample_rate():
        return
    log(event, level="DEBUG", **fields)


def http_body_snippet(text: str) -> str:
    """
    Keep only a small snippet to avoid massive logs and accidental PII leaks.
    """
    if not text:
        return ""
    snip = text.replace("\n", " ").replace("\r", " ")
    return snip[: _http_body_chars()]


def dedupe_for_log(value: str) -> str:
    if _include_dedupe_keys():
        return value
    return f"sha:{abs(hash(value)) % 10_000_000}"
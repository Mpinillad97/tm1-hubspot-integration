import time
from typing import Any, Dict, Optional, Tuple

import requests

from sync.logging import log, http_body_snippet
from sync.rate_limit import TokenBucket


class DiscoveryError(Exception):
    pass


def _is_retryable_status(status: Optional[int]) -> bool:
    if status is None:
        return True
    return status in (429, 500, 502, 503, 504)


def fetch_event_details(
    *,
    event_id: str,
    discovery_base_url: str,
    discovery_api_key: str,
    include_inactive: bool = True,
    include_past: bool = True,
    view_internal: bool = False,
    timeout_seconds: int = 20,
    max_retries: int = 4,
    # Discovery rate limit default: 5 req/second
    rps_limit: int = 5,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Returns (status, payload)
      status in: "ok" | "not_found" | "error"
      payload is dict when ok, else None
    """
    event_id = (event_id or "").strip()
    if not event_id:
        return ("error", None)

    base = discovery_base_url.rstrip("/")
    url = f"{base}/events/{event_id}.json"

    params: Dict[str, str] = {"apikey": discovery_api_key}
    if include_inactive:
        params["includeInactive"] = "yes"
    if include_past:
        params["includePast"] = "yes"
    if view_internal:
        params["view"] = "internal"

    # 5 rps = 1 token per 200ms window, easiest: token bucket per 1s
    bucket = TokenBucket(rps_limit, 1000)

    attempt = 0
    last_status: Optional[int] = None

    while True:
        attempt += 1
        bucket.take(1)

        try:
            r = requests.get(url, params=params, timeout=timeout_seconds)
            last_status = r.status_code

            if r.status_code == 200:
                try:
                    return ("ok", r.json())
                except Exception:
                    log(
                        "discovery.event.bad_json",
                        level="ERROR",
                        event_id=event_id,
                        status=r.status_code,
                        body=http_body_snippet(r.text),
                    )
                    return ("error", None)

            if r.status_code == 404:
                # event deleted / not visible / permanently removed
                return ("not_found", None)

            # other non-200
            if _is_retryable_status(r.status_code) and attempt <= max_retries:
                wait_s = min(30, 2 ** (attempt - 1))
                log(
                    "discovery.event.retry",
                    level="WARN",
                    event_id=event_id,
                    status=r.status_code,
                    attempt=attempt,
                    wait_s=wait_s,
                    body=http_body_snippet(r.text),
                )
                time.sleep(wait_s)
                continue

            log(
                "discovery.event.http_error",
                level="ERROR",
                event_id=event_id,
                status=r.status_code,
                body=http_body_snippet(r.text),
            )
            return ("error", None)

        except Exception as e:
            if attempt <= max_retries:
                wait_s = min(30, 2 ** (attempt - 1))
                log(
                    "discovery.event.exc_retry",
                    level="WARN",
                    event_id=event_id,
                    err=type(e).__name__,
                    attempt=attempt,
                    wait_s=wait_s,
                )
                time.sleep(wait_s)
                continue

            log(
                "discovery.event.exc_error",
                level="ERROR",
                event_id=event_id,
                err=type(e).__name__,
            )
            return ("error", None)
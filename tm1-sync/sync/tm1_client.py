import time
from typing import Any, Dict, List, Tuple

import requests

from sync.logging import log


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def tm1_get_orders(
    *,
    tm1_base_url: str,
    api_key: str,
    since: str,
    page: int,
    size: int,
    timeout_seconds: int,
    max_retries: int,
) -> Dict[str, Any]:
    url = f"{tm1_base_url.rstrip('/')}/orders"
    params = {"apikey": api_key, "since": since, "page": page, "size": size}

    attempt = 0
    while True:
        attempt += 1
        try:
            r = requests.get(url, params=params, timeout=timeout_seconds)
            if r.status_code in RETRYABLE_STATUS and attempt <= max_retries:
                backoff = min(30, 2 ** (attempt - 1))  # 1s,2s,4s,8s.. cap 30
                log("tm1.retry", status=r.status_code, attempt=attempt, backoff_seconds=backoff, page=page)
                time.sleep(backoff)
                continue

            r.raise_for_status()
            return r.json()

        except requests.exceptions.Timeout:
            if attempt <= max_retries:
                backoff = min(30, 2 ** (attempt - 1))
                log("tm1.retry_timeout", attempt=attempt, backoff_seconds=backoff, page=page)
                time.sleep(backoff)
                continue
            raise

        except requests.exceptions.RequestException as e:
            # Network/DNS/etc. Treat as retryable up to max_retries
            if attempt <= max_retries:
                backoff = min(30, 2 ** (attempt - 1))
                log("tm1.retry_request_exception", attempt=attempt, backoff_seconds=backoff, page=page, err=type(e).__name__)
                time.sleep(backoff)
                continue
            raise


def extract_orders(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return payload.get("_embedded", {}).get("orders", []) or []


def parse_max_updated_since(orders: List[Dict[str, Any]], fallback: str) -> str:
    max_val = fallback
    for o in orders:
        upd = o.get("updatedSince")
        if isinstance(upd, str) and upd > max_val:
            max_val = upd
    return max_val


def reached_last_page(payload: Dict[str, Any], current_page: int) -> bool:
    """
    Uses TM1's "page" object when available; otherwise caller can stop on empty page.
    """
    page_info = payload.get("page", {}) or {}
    total_pages = page_info.get("totalPages")
    number = page_info.get("number")
    if isinstance(total_pages, int) and isinstance(number, int):
        return number >= total_pages - 1
    # fallback: caller may stop when no orders
    return False
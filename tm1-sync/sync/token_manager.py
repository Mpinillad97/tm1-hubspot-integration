from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests

from db.repo import get_oauth_token_row, upsert_oauth_token_row
from sync.logging import log


def _is_expired(saved_at: datetime, token_payload: Dict[str, Any], skew_seconds: int = 120) -> bool:
    """
    Treat token as expired if now >= saved_at + expires_in - skew.
    We use saved_at from DB as the "issued at" time.
    """
    expires_in = token_payload.get("expires_in")
    if not isinstance(expires_in, (int, float)):
        # If we don't know, assume expired to be safe.
        return True

    expires_at = saved_at.replace(tzinfo=timezone.utc) + timedelta(seconds=int(expires_in) - skew_seconds)
    return datetime.now(timezone.utc) >= expires_at


def refresh_access_token(
    *,
    hubspot_base_url: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> Dict[str, Any]:
    url = f"{hubspot_base_url.rstrip('/')}/oauth/v3/token"
    r = requests.post(
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        timeout=45,
    )
    r.raise_for_status()
    return r.json()


def get_valid_access_token(
    *,
    portal_id: str,
    hubspot_base_url: str,
    client_id: str,
    client_secret: str,
) -> str:
    row = get_oauth_token_row(portal_id)
    if not row:
        raise RuntimeError(f"No oauth_tokens row found for portal_id={portal_id}")

    saved_at, payload = row
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")

    if isinstance(access_token, str) and access_token.strip() and not _is_expired(saved_at, payload):
        return access_token.strip()

    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise RuntimeError(f"OAuth token expired/missing and no refresh_token for portal_id={portal_id}")

    # Refresh
    log("oauth.refresh.start", portal_id=str(portal_id))
    new_payload = refresh_access_token(
        hubspot_base_url=hubspot_base_url,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token.strip(),
    )

    # HubSpot may not always return a new refresh_token; keep old if missing
    if "refresh_token" not in new_payload:
        new_payload["refresh_token"] = refresh_token.strip()

    upsert_oauth_token_row(portal_id, new_payload)
    log("oauth.refresh.done", portal_id=str(portal_id))

    new_access = new_payload.get("access_token")
    if not isinstance(new_access, str) or not new_access.strip():
        raise RuntimeError("Refresh succeeded but access_token missing in response")

    return new_access.strip()
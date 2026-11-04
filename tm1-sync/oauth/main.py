import os
import json
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple
from urllib.parse import quote, urlencode

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
TOKENS_FILE = "tokens.json"

# -------------------------
# OAuth `state` (CSRF protection)
# -------------------------
HUBSPOT_AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"

# Must match `requiredScopes` in TM1_Hubspot_data_sync/src/app/app-hsmeta.json
# (enforced by tests/test_oauth_state.py).
HUBSPOT_SCOPES = (
    "oauth",
    "crm.objects.contacts.read",
    "crm.objects.contacts.write",
    "crm.schemas.orders.read",
    "crm.objects.orders.read",
    "crm.objects.orders.write",
    "crm.pipelines.orders.read",
    "crm.schemas.custom.read",
    "crm.schemas.custom.write",
    "crm.objects.custom.read",
    "crm.objects.custom.write",
)

STATE_COOKIE = "hs_oauth_state"
STATE_COOKIE_PATH = "/oauth"
STATE_TTL_SECONDS = 600


def _state_key(client_secret: str) -> bytes:
    # Derived sub-key: the client secret itself is never used directly as the HMAC key.
    return hmac.new(client_secret.encode("utf-8"), b"tm1-sync/oauth-state/v1", hashlib.sha256).digest()


def _state_signature(client_secret: str, nonce: str, issued_at: str) -> str:
    msg = f"{nonce}.{issued_at}".encode("utf-8")
    return hmac.new(_state_key(client_secret), msg, hashlib.sha256).hexdigest()


def issue_state(client_secret: str, now: Optional[float] = None) -> Tuple[str, str]:
    """
    Returns (state, nonce). `state` is sent to HubSpot; `nonce` is stored in a cookie
    so the callback can verify that it belongs to the browser that started the flow.
    """
    nonce = secrets.token_urlsafe(32)
    issued_at = str(int(now if now is not None else time.time()))
    return f"{nonce}.{issued_at}.{_state_signature(client_secret, nonce, issued_at)}", nonce


def validate_state(
    state: Optional[str],
    cookie_nonce: Optional[str],
    client_secret: str,
    now: Optional[float] = None,
) -> Optional[str]:
    """Returns None when valid, otherwise a short reason code."""
    if not state:
        return "missing_state"
    if not cookie_nonce:
        return "missing_state_cookie"

    parts = state.split(".")
    if len(parts) != 3 or not all(parts):
        return "malformed_state"
    nonce, issued_at, signature = parts

    expected = _state_signature(client_secret, nonce, issued_at)
    if not hmac.compare_digest(signature, expected):
        return "bad_signature"

    try:
        age = (now if now is not None else time.time()) - int(issued_at)
    except ValueError:
        return "malformed_state"
    if age < 0 or age > STATE_TTL_SECONDS:
        return "expired_state"

    if not hmac.compare_digest(nonce, cookie_nonce):
        return "state_mismatch"
    return None


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(HUBSPOT_SCOPES),
            "state": state,
        },
        quote_via=quote,
    )
    return f"{HUBSPOT_AUTHORIZE_URL}?{query}"

def _db_url() -> Optional[str]:
    url = os.getenv("DATABASE_URL")
    return url.strip() if url and url.strip() else None

def save_tokens_file(portal_id: str, data: Dict[str, Any]) -> None:
    tokens = {}
    if os.path.exists(TOKENS_FILE):
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            tokens = json.load(f) or {}

    tokens[str(portal_id)] = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        **data,
    }

    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)

def save_tokens_db(portal_id: str, data: Dict[str, Any]) -> None:
    import psycopg2
    from psycopg2.extras import Json

    db_url = _db_url()
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    portal_id TEXT PRIMARY KEY,
                    saved_at TIMESTAMPTZ NOT NULL,
                    token_payload JSONB NOT NULL
                );
            """)
            cur.execute("""
                INSERT INTO oauth_tokens (portal_id, saved_at, token_payload)
                VALUES (%s, %s, %s)
                ON CONFLICT (portal_id) DO UPDATE
                SET saved_at = EXCLUDED.saved_at,
                    token_payload = EXCLUDED.token_payload;
            """, (str(portal_id), datetime.now(timezone.utc), Json(data)))
        conn.commit()
    finally:
        conn.close()

def save_tokens(portal_id: str, data: Dict[str, Any]) -> str:
    if _db_url():
        try:
            save_tokens_db(portal_id, data)
            return "db"
        except Exception as e:
            raise RuntimeError(f"DB token save failed: {type(e).__name__}")
    save_tokens_file(portal_id, data)
    return "file"

@app.get("/healthz")
def health():
    return {"ok": True, "db": bool(_db_url())}

@app.get("/oauth/install")
def oauth_install():
    """Starts the install flow: issues a `state`, binds it to this browser, redirects to HubSpot."""
    client_id = os.getenv("HUBSPOT_CLIENT_ID")
    client_secret = os.getenv("HUBSPOT_CLIENT_SECRET")
    redirect_uri = os.getenv("HUBSPOT_REDIRECT_URI")

    if not client_id or not client_secret or not redirect_uri:
        return JSONResponse({"ok": False, "error": "missing_env"}, status_code=500)

    state, nonce = issue_state(client_secret)
    response = RedirectResponse(url=build_authorize_url(client_id, redirect_uri, state), status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        nonce,
        max_age=STATE_TTL_SECONDS,
        path=STATE_COOKIE_PATH,
        httponly=True,
        secure=redirect_uri.lower().startswith("https://"),
        samesite="lax",  # sent on HubSpot's top-level redirect back to the callback
    )
    return response


@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    error_desc = request.query_params.get("error_description")

    if error:
        return JSONResponse({"ok": False, "error": error, "error_description": error_desc}, status_code=400)

    if not code:
        return JSONResponse({"ok": False, "error": "missing_code"}, status_code=400)

    client_id = os.getenv("HUBSPOT_CLIENT_ID")
    client_secret = os.getenv("HUBSPOT_CLIENT_SECRET")
    redirect_uri = os.getenv("HUBSPOT_REDIRECT_URI")

    if not client_id or not client_secret or not redirect_uri:
        return JSONResponse({"ok": False, "error": "missing_env"}, status_code=500)

    # CSRF protection: only complete flows started by /oauth/install in this browser.
    # Validated before the authorization code is sent anywhere.
    state_error = validate_state(
        request.query_params.get("state"),
        request.cookies.get(STATE_COOKIE),
        client_secret,
    )
    if state_error:
        response = JSONResponse({"ok": False, "error": "invalid_state"}, status_code=400)
        response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
        return response

    r = requests.post(
        "https://api.hubapi.com/oauth/v3/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=45,
    )

    if r.status_code >= 400:
        return JSONResponse({"ok": False, "status": r.status_code, "body": r.text}, status_code=400)

    data = r.json()
    portal_id = str(data.get("hub_id") or data.get("portalId") or "unknown")

    saved_to = save_tokens(portal_id, data)

    # Redirect to home portal after successful auth
    hubspot_home = f"https://app.hubspot.com/home?portalId={portal_id}"
    response = RedirectResponse(url=hubspot_home, status_code=302)
    response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)  # single use
    return response

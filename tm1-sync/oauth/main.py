import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
TOKENS_FILE = "tokens.json" 

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
    return RedirectResponse(url=hubspot_home, status_code=302)

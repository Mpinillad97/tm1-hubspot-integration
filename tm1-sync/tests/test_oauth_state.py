"""
OAuth `state` validation (CSRF protection) for the OAuth service.

Run from tm1-sync/ with the project venv (needs fastapi, already a pinned dependency):
    sync/.venv/Scripts/python -m unittest discover -s tests -v

The app is driven through its raw ASGI interface (stdlib asyncio), so no extra HTTP
test client is needed. HubSpot and token storage are mocked: no network, no database.
"""
import asyncio
import importlib.util
import json
import time
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

TM1_SYNC_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TM1_SYNC_DIR.parent
APP_MANIFEST = REPO_ROOT / "TM1_Hubspot_data_sync" / "src" / "app" / "app-hsmeta.json"

_spec = importlib.util.spec_from_file_location("oauth_main", TM1_SYNC_DIR / "oauth" / "main.py")
oauth_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oauth_main)

# Synthetic test values only.
ENV = {
    "HUBSPOT_CLIENT_ID": "test-client-id",
    "HUBSPOT_CLIENT_SECRET": "test-client-secret",
    "HUBSPOT_REDIRECT_URI": "https://oauth.test.example/oauth/callback",
}


def _request(path: str, query: str = "", cookies: dict | None = None):
    """Minimal ASGI GET request. Returns (status, headers[list of (name, value)], body bytes)."""
    headers = [(b"host", b"oauth.test.example")]
    if cookies:
        headers.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("oauth.test.example", 443),
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(oauth_main.app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    hdrs = [(k.decode().lower(), v.decode()) for k, v in start["headers"]]
    return start["status"], hdrs, body


def _header(headers, name):
    return next((v for k, v in headers if k == name), None)


def _set_cookie(headers):
    for k, v in headers:
        if k == "set-cookie" and v.startswith(oauth_main.STATE_COOKIE + "="):
            c = SimpleCookie()
            c.load(v)
            return c[oauth_main.STATE_COOKIE], v
    return None, None


def _start_install():
    """Runs /oauth/install and returns (state, cookie_nonce)."""
    status, headers, _ = _request("/oauth/install")
    assert status == 302, status
    state = parse_qs(urlsplit(_header(headers, "location")).query)["state"][0]
    morsel, _ = _set_cookie(headers)
    return state, morsel.value


class ValidateStateTests(unittest.TestCase):
    SECRET = "unit-secret"

    def test_valid_state(self):
        state, nonce = oauth_main.issue_state(self.SECRET)
        self.assertIsNone(oauth_main.validate_state(state, nonce, self.SECRET))

    def test_missing_state_or_cookie(self):
        state, nonce = oauth_main.issue_state(self.SECRET)
        self.assertEqual(oauth_main.validate_state(None, nonce, self.SECRET), "missing_state")
        self.assertEqual(oauth_main.validate_state("", nonce, self.SECRET), "missing_state")
        self.assertEqual(oauth_main.validate_state(state, None, self.SECRET), "missing_state_cookie")

    def test_malformed(self):
        for bad in ["abc", "a.b", "a..c", "a.b.c.d", ".."]:
            self.assertEqual(oauth_main.validate_state(bad, "a", self.SECRET), "malformed_state", bad)

    def test_tampered_signature(self):
        state, nonce = oauth_main.issue_state(self.SECRET)
        n, ts, sig = state.split(".")
        forged = f"{n}.{ts}.{'0' * len(sig)}"
        self.assertEqual(oauth_main.validate_state(forged, nonce, self.SECRET), "bad_signature")

    def test_tampered_timestamp_breaks_signature(self):
        state, nonce = oauth_main.issue_state(self.SECRET)
        n, ts, sig = state.split(".")
        self.assertEqual(
            oauth_main.validate_state(f"{n}.{int(ts) + 1}.{sig}", nonce, self.SECRET), "bad_signature"
        )

    def test_signed_with_other_secret(self):
        state, nonce = oauth_main.issue_state("attacker-secret")
        self.assertEqual(oauth_main.validate_state(state, nonce, self.SECRET), "bad_signature")

    def test_expired_and_future(self):
        now = time.time()
        old, nonce = oauth_main.issue_state(self.SECRET, now=now - oauth_main.STATE_TTL_SECONDS - 1)
        self.assertEqual(oauth_main.validate_state(old, nonce, self.SECRET, now=now), "expired_state")
        future, nonce2 = oauth_main.issue_state(self.SECRET, now=now + 60)
        self.assertEqual(oauth_main.validate_state(future, nonce2, self.SECRET, now=now), "expired_state")

    def test_state_from_another_browser(self):
        state, _ = oauth_main.issue_state(self.SECRET)
        _, other_nonce = oauth_main.issue_state(self.SECRET)
        self.assertEqual(oauth_main.validate_state(state, other_nonce, self.SECRET), "state_mismatch")

    def test_states_are_unique(self):
        self.assertNotEqual(oauth_main.issue_state(self.SECRET)[0], oauth_main.issue_state(self.SECRET)[0])


class InstallEndpointTests(unittest.TestCase):
    def test_redirects_to_hubspot_with_state_and_sets_cookie(self):
        with mock.patch.dict("os.environ", ENV):
            status, headers, _ = _request("/oauth/install")

        self.assertEqual(status, 302)
        location = urlsplit(_header(headers, "location"))
        self.assertEqual(f"{location.scheme}://{location.netloc}{location.path}", oauth_main.HUBSPOT_AUTHORIZE_URL)
        q = parse_qs(location.query)
        self.assertEqual(q["client_id"], [ENV["HUBSPOT_CLIENT_ID"]])
        self.assertEqual(q["redirect_uri"], [ENV["HUBSPOT_REDIRECT_URI"]])
        self.assertEqual(q["scope"][0].split(" "), list(oauth_main.HUBSPOT_SCOPES))
        self.assertIn("%20", location.query)  # scopes space-separated, percent-encoded

        morsel, raw = _set_cookie(headers)
        self.assertIsNotNone(morsel, "state cookie not set")
        self.assertEqual(q["state"][0].split(".")[0], morsel.value)  # cookie binds the state
        self.assertIn("httponly", raw.lower())
        self.assertIn("secure", raw.lower())
        self.assertIn("samesite=lax", raw.lower())
        self.assertEqual(morsel["path"], oauth_main.STATE_COOKIE_PATH)
        self.assertEqual(str(morsel["max-age"]), str(oauth_main.STATE_TTL_SECONDS))

    def test_cookie_not_secure_for_local_http(self):
        env = dict(ENV, HUBSPOT_REDIRECT_URI="http://localhost:8080/oauth/callback")
        with mock.patch.dict("os.environ", env):
            _, headers, _ = _request("/oauth/install")
        _, raw = _set_cookie(headers)
        self.assertNotIn("secure", raw.lower())

    def test_missing_env(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            status, _, body = _request("/oauth/install")
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(body), {"ok": False, "error": "missing_env"})


class CallbackStateTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", ENV)
        self.env.start()
        token_response = mock.Mock(status_code=200)
        token_response.json.return_value = {"hub_id": 123456, "access_token": "x", "refresh_token": "y"}
        self.post = mock.patch.object(oauth_main.requests, "post", return_value=token_response).start()
        self.save = mock.patch.object(oauth_main, "save_tokens", return_value="db").start()

    def tearDown(self):
        mock.patch.stopall()
        self.env.stop()

    def _callback(self, query, cookies=None):
        return _request("/oauth/callback", query, cookies)

    def _assert_rejected(self, status, headers, body):
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"ok": False, "error": "invalid_state"})
        self.post.assert_not_called()  # the code is never sent to HubSpot
        self.save.assert_not_called()
        _, raw = _set_cookie(headers)
        self.assertIsNotNone(raw, "state cookie should be cleared")
        self.assertIn("max-age=0", raw.lower())

    def test_valid_flow_exchanges_code_and_clears_cookie(self):
        state, nonce = _start_install()
        status, headers, _ = self._callback(f"code=abc&state={state}", {oauth_main.STATE_COOKIE: nonce})

        self.assertEqual(status, 302)
        self.assertEqual(_header(headers, "location"), "https://app.hubspot.com/home?portalId=123456")
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.kwargs["data"]["code"], "abc")
        self.save.assert_called_once()
        _, raw = _set_cookie(headers)
        self.assertIn("max-age=0", raw.lower())

    def test_missing_state_rejected(self):
        _, nonce = _start_install()
        self._assert_rejected(*self._callback("code=abc", {oauth_main.STATE_COOKIE: nonce}))

    def test_missing_cookie_rejected(self):
        state, _ = _start_install()
        self._assert_rejected(*self._callback(f"code=abc&state={state}"))

    def test_cookie_from_other_flow_rejected(self):
        state, _ = _start_install()
        _, other_nonce = _start_install()
        self._assert_rejected(*self._callback(f"code=abc&state={state}", {oauth_main.STATE_COOKIE: other_nonce}))

    def test_attacker_crafted_state_rejected(self):
        forged, nonce = oauth_main.issue_state("attacker-secret")
        self._assert_rejected(*self._callback(f"code=abc&state={forged}", {oauth_main.STATE_COOKIE: nonce}))

    def test_expired_state_rejected(self):
        old, nonce = oauth_main.issue_state(
            ENV["HUBSPOT_CLIENT_SECRET"], now=time.time() - oauth_main.STATE_TTL_SECONDS - 5
        )
        self._assert_rejected(*self._callback(f"code=abc&state={old}", {oauth_main.STATE_COOKIE: nonce}))

    # Existing responses are unchanged.
    def test_error_param_response_unchanged(self):
        status, _, body = self._callback("error=access_denied&error_description=denied")
        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(body), {"ok": False, "error": "access_denied", "error_description": "denied"}
        )
        self.post.assert_not_called()

    def test_missing_code_response_unchanged(self):
        status, _, body = self._callback("state=x")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"ok": False, "error": "missing_code"})


class RedirectUriCompatibilityTests(unittest.TestCase):
    """
    The state cookie set by /oauth/install must come back on HubSpot's redirect to
    HUBSPOT_REDIRECT_URI. A stdlib RFC 6265 cookie jar stands in for the browser.
    """

    def _cookie_sent(self, install_url: str, redirect_uri: str, callback_url: str) -> bool:
        import http.cookiejar
        import urllib.request
        from email.message import Message

        env = dict(ENV, HUBSPOT_REDIRECT_URI=redirect_uri)
        with mock.patch.dict("os.environ", env):
            _, headers, _ = _request("/oauth/install")

        msg = Message()
        for k, v in headers:
            if k == "set-cookie":
                msg["Set-Cookie"] = v

        class _Resp:
            def info(self):
                return msg

        jar = http.cookiejar.CookieJar()
        jar.extract_cookies(_Resp(), urllib.request.Request(install_url))
        cb = urllib.request.Request(callback_url)
        jar.add_cookie_header(cb)
        return oauth_main.STATE_COOKIE in (cb.get_header("Cookie") or "")

    def test_authorize_and_token_exchange_use_the_same_redirect_uri(self):
        with mock.patch.dict("os.environ", ENV):
            state, nonce = _start_install()
            _, headers, _ = _request("/oauth/install")
            sent = parse_qs(urlsplit(_header(headers, "location")).query)["redirect_uri"][0]
            resp = mock.Mock(status_code=200)
            resp.json.return_value = {"hub_id": 1}
            with mock.patch.object(oauth_main.requests, "post", return_value=resp) as post, \
                    mock.patch.object(oauth_main, "save_tokens", return_value="db"):
                _request("/oauth/callback", f"code=c&state={state}", {oauth_main.STATE_COOKIE: nonce})
        self.assertEqual(sent, ENV["HUBSPOT_REDIRECT_URI"])
        self.assertEqual(post.call_args.kwargs["data"]["redirect_uri"], ENV["HUBSPOT_REDIRECT_URI"])

    def test_cookie_returned_when_install_and_callback_share_host(self):
        uri = "https://oauth.test.example/oauth/callback"
        self.assertTrue(self._cookie_sent("https://oauth.test.example/oauth/install", uri, uri + "?code=c&state=s"))

    def test_cookie_returned_for_local_http_development(self):
        uri = "http://localhost:8080/oauth/callback"
        self.assertTrue(self._cookie_sent("http://localhost:8080/oauth/install", uri, uri + "?code=c"))

    def test_cookie_not_returned_when_install_opened_on_another_host(self):
        # e.g. install opened on the platform's default hostname, callback on the custom domain
        uri = "https://oauth.test.example/oauth/callback"
        self.assertFalse(self._cookie_sent("https://default-host.test.example/oauth/install", uri, uri))

    def test_cookie_not_returned_when_callback_is_behind_a_path_prefix(self):
        # A reverse proxy that serves the app under /api/ changes the browser-visible path.
        uri = "https://oauth.test.example/api/oauth/callback"
        self.assertFalse(
            self._cookie_sent("https://oauth.test.example/api/oauth/install", uri, uri)
        )

    def test_documented_redirect_uris_are_compatible(self):
        # The example env file and the app manifest both use <host>/oauth/callback over https.
        example = (TM1_SYNC_DIR / "oauth" / ".env.example").read_text(encoding="utf-8")
        env_uri = next(
            line.split("=", 1)[1].strip() for line in example.splitlines()
            if line.startswith("HUBSPOT_REDIRECT_URI=")
        )
        manifest = json.loads(APP_MANIFEST.read_text(encoding="utf-8"))
        for uri in [env_uri, *manifest["config"]["auth"]["redirectUrls"]]:
            parts = urlsplit(uri)
            self.assertEqual(parts.scheme, "https", uri)
            self.assertEqual(parts.path, "/oauth/callback", uri)
            self.assertTrue(parts.path.startswith(oauth_main.STATE_COOKIE_PATH + "/"), uri)


class ScopesMatchAppManifestTests(unittest.TestCase):
    def test_scopes_match_required_scopes(self):
        manifest = json.loads(APP_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(list(oauth_main.HUBSPOT_SCOPES), manifest["config"]["auth"]["requiredScopes"])


if __name__ == "__main__":
    unittest.main()

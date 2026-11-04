"""
Customer email addresses must never reach application logs.

Run from tm1-sync/:  python -m unittest discover -s tests -v
Standard library only; no database or network access.
"""
import ast
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from sync.logging import email_hash, log, log_item, redact_pii

SYNC_DIR = Path(__file__).resolve().parent.parent / "sync"
EMAIL = "Jane.Doe+tickets@Example.co.uk"  # synthetic test address


def _capture(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue()


class RedactPiiTests(unittest.TestCase):
    def test_plain_email_is_replaced_with_hash(self):
        out = redact_pii(EMAIL)
        self.assertNotIn("@", out)
        self.assertEqual(out, f"<email:{email_hash(EMAIL)}>")

    def test_hash_is_case_and_whitespace_insensitive(self):
        self.assertEqual(email_hash(EMAIL), email_hash(f"  {EMAIL.lower()} "))

    def test_hash_matches_existing_sha10_convention(self):
        # sync.hubspot_client.sha10 is the hash already used for `email_hash` log fields.
        # Re-implemented here to avoid importing `requests` in a stdlib-only test.
        import hashlib

        expected = hashlib.sha256(EMAIL.lower().encode("utf-8")).hexdigest()[:10]
        self.assertEqual(email_hash(EMAIL), expected)

    def test_email_embedded_in_dedupe_key(self):
        out = redact_pii(f"12345->{EMAIL.lower()}")
        self.assertTrue(out.startswith("12345-><email:"))
        self.assertNotIn("@", out)

    def test_email_inside_http_body_snippet(self):
        body = json.dumps(
            {"status": "error", "message": f'Property values were not valid: "{EMAIL}" is invalid'}
        )
        out = redact_pii(body)
        self.assertNotIn(EMAIL, out)
        self.assertIn("Property values were not valid", out)

    def test_multiple_emails_and_nested_structures(self):
        value = {"a": [EMAIL, {"b": "x other@test.example y"}], "n": 5, "none": None}
        out = redact_pii(value)
        self.assertNotIn("@", json.dumps(out))
        self.assertEqual(out["n"], 5)
        self.assertIsNone(out["none"])

    def test_strings_without_email_are_unchanged(self):
        for s in ["", "order 123", "http_400:bad_request", "@mention", "user@localhost"]:
            self.assertEqual(redact_pii(s), s)


class LogOutputTests(unittest.TestCase):
    def test_log_line_never_contains_email(self):
        out = _capture(
            log,
            "test.event",
            level="WARN",
            email=EMAIL,
            dedupe_key=f"999->{EMAIL.lower()}",
            body=f"conflict for {EMAIL}",
            nested={"emails": [EMAIL]},
            count=3,
        )
        self.assertNotIn("@", out)
        payload = json.loads(out)
        self.assertEqual(payload["event"], "test.event")
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["dedupe_key"], f"999-><email:{email_hash(EMAIL)}>")

    def test_sampled_item_log_is_redacted(self):
        with mock.patch.dict("os.environ", {"LOG_LEVEL": "DEBUG", "LOG_ITEM_SAMPLE_RATE": "1.0"}):
            out = _capture(log_item, "test.item", dedupe_key=EMAIL)
        self.assertTrue(out, "log_item should have emitted a line")
        self.assertNotIn("@", out)


class NoRawEmailInLogCallsTests(unittest.TestCase):
    """Static guard: no log()/log_item() call in sync/ passes an email variable."""

    EMAIL_NAMES = {"email", "email_norm", "email_normalized", "email_clean", "seat_email_norm"}

    def test_log_calls_do_not_pass_email_values(self):
        offenders = []
        for path in sorted(SYNC_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if fname not in ("log", "log_item"):
                    continue
                for kw in node.keywords:
                    # Bare email variable or str(email) passed directly as a field value.
                    val = kw.value
                    if isinstance(val, ast.Call) and getattr(val.func, "id", None) == "str" and val.args:
                        val = val.args[0]
                    if kw.arg in self.EMAIL_NAMES or (
                        isinstance(val, ast.Name) and val.id in self.EMAIL_NAMES
                    ):
                        offenders.append(f"{path.name}:{node.lineno} {kw.arg}=")
        self.assertEqual(offenders, [], "raw email passed to logger")


if __name__ == "__main__":
    unittest.main()

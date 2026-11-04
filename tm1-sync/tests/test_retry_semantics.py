"""Unit tests for HTTP status classification and the retry backoff schedule."""
import datetime as dt
import unittest
from unittest import mock

from sync import backoff
from sync.errors import classify_http_status


class ClassifyHttpStatusTests(unittest.TestCase):
    def test_table(self):
        cases = [
            (None, True, "no_status_network_error"),
            (429, True, "rate_limited"),
            (500, True, "server_error"),
            (502, True, "server_error"),
            (503, True, "server_error"),
            (504, True, "server_error"),
            (401, False, "auth_error"),
            (403, False, "auth_error"),
            (404, False, "not_found_schema_or_object"),
            (400, False, "bad_request"),
            (409, False, "bad_request"),
            (499, False, "bad_request"),
            (501, True, "unknown_status"),
            (599, True, "unknown_status"),
            (200, True, "unknown_status"),
            (302, True, "unknown_status"),
        ]
        for status, retryable, message in cases:
            with self.subTest(status=status):
                info = classify_http_status(status)
                self.assertEqual((info.retryable, info.status_code, info.message), (retryable, status, message))


class BackoffTests(unittest.TestCase):
    NOW = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

    def _delay(self, attempts, jitter):
        fake_dt = mock.Mock(wraps=dt.datetime)
        fake_dt.now.return_value = self.NOW
        with mock.patch.object(backoff, "datetime", fake_dt), \
                mock.patch.object(backoff.random, "randint", return_value=jitter) as randint:
            result = backoff.compute_next_retry_at(attempts)
        return (result - self.NOW).total_seconds(), randint.call_args.args

    def test_schedule_without_jitter(self):
        expected = {0: 10, 1: 10, 2: 30, 3: 120, 4: 300, 5: 600, 6: 1200, 7: 1800, 8: 1800, 100: 1800}
        for attempts, seconds in expected.items():
            with self.subTest(attempts=attempts):
                delay, _ = self._delay(attempts, 0)
                self.assertEqual(delay, seconds)

    def test_jitter_is_up_to_twenty_percent(self):
        for attempts, base in [(1, 10), (3, 120), (7, 1800)]:
            with self.subTest(attempts=attempts):
                delay, randint_args = self._delay(attempts, 7)
                self.assertEqual(randint_args, (0, max(1, base // 5)))
                self.assertEqual(delay, base + 7)

    def test_result_is_timezone_aware_utc(self):
        self.assertEqual(backoff.compute_next_retry_at(1).tzinfo, dt.timezone.utc)


if __name__ == "__main__":
    unittest.main()

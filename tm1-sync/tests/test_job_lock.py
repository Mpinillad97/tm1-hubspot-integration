"""
Per-tenant job lock (Postgres session advisory lock) must be held for the whole run.

Run from tm1-sync/ with the project venv:
    sync/.venv/Scripts/python -m unittest discover -s tests -v

Unit tests use fake connections. The integration tests need a disposable PostgreSQL and
run only when TEST_DATABASE_URL is set, for example:
    TEST_DATABASE_URL=postgresql://postgres@localhost:55432/postgres
"""
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from db import locks
from db.locks import AdvisoryLock


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise RuntimeError("connection lost")
        self.conn.statements.append(sql.strip())

    def fetchone(self):
        last = self.conn.statements[-1]
        if "pg_try_advisory_lock" in last:
            return (self.conn.grant,)
        return (True,)


class FakeConn:
    def __init__(self, grant=True, fail_on=None):
        self.grant = grant
        self.fail_on = fail_on
        self.statements = []
        self.closed = 0
        self.autocommit = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = 1


class AdvisoryLockUnitTests(unittest.TestCase):
    def _patch(self, conn):
        return mock.patch.object(locks, "_connect", return_value=conn)

    def test_lock_key_unchanged(self):
        # Value produced by the original implementation: existing deployments keep the same key.
        self.assertEqual(locks._lock_key("tm1_sync:example-client"), 2825341568209711017)

    def test_acquired_lock_keeps_its_connection_open(self):
        conn = FakeConn(grant=True)
        with self._patch(conn):
            lock = AdvisoryLock("tm1_sync:c1")
            self.assertTrue(lock.acquire())
        self.assertTrue(lock.held)
        self.assertFalse(conn.closed, "connection must stay open while the lock is held")
        self.assertTrue(conn.autocommit)

    def test_release_unlocks_on_the_same_connection_and_closes_it(self):
        conn = FakeConn(grant=True)
        with self._patch(conn):
            lock = AdvisoryLock("tm1_sync:c1")
            lock.acquire()
            self.assertTrue(lock.release())
        self.assertTrue(any("pg_advisory_unlock" in s for s in conn.statements))
        self.assertTrue(conn.closed)
        self.assertFalse(lock.held)

    def test_not_acquired_closes_connection_and_release_is_noop(self):
        conn = FakeConn(grant=False)
        with self._patch(conn):
            lock = AdvisoryLock("tm1_sync:c1")
            self.assertFalse(lock.acquire())
        self.assertTrue(conn.closed)
        self.assertFalse(lock.held)
        self.assertTrue(lock.release())
        self.assertFalse(any("pg_advisory_unlock" in s for s in conn.statements))

    def test_acquire_error_closes_connection_and_propagates(self):
        conn = FakeConn(fail_on="pg_try_advisory_lock")
        with self._patch(conn):
            with self.assertRaises(RuntimeError):
                AdvisoryLock("tm1_sync:c1").acquire()
        self.assertTrue(conn.closed)

    def test_release_never_raises_and_still_closes(self):
        conn = FakeConn(grant=True, fail_on="pg_advisory_unlock")
        with self._patch(conn):
            lock = AdvisoryLock("tm1_sync:c1")
            lock.acquire()
            self.assertFalse(lock.release())
        self.assertTrue(conn.closed)
        self.assertFalse(lock.held)

    def test_release_is_idempotent(self):
        with self._patch(FakeConn(grant=True)):
            lock = AdvisoryLock("tm1_sync:c1")
            lock.acquire()
        self.assertTrue(lock.release())
        self.assertTrue(lock.release())

    def test_double_acquire_is_rejected(self):
        with mock.patch.object(locks, "_connect", side_effect=[FakeConn(), FakeConn()]):
            lock = AdvisoryLock("tm1_sync:c1")
            lock.acquire()
            with self.assertRaises(RuntimeError):
                lock.acquire()

    def test_keepalives_requested(self):
        with mock.patch.object(locks.psycopg2, "connect", return_value=FakeConn()) as connect, \
                mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://u@h/db"}):
            AdvisoryLock("tm1_sync:c1").acquire()
        self.assertEqual(connect.call_args.kwargs.get("keepalives"), 1)


class RunOnceLockTests(unittest.TestCase):
    """sync.main.run_once holds the lock across every stage and always releases it."""

    def setUp(self):
        import sync.main as main_mod

        self.main = main_mod
        cfg = SimpleNamespace(
            client_id="c1", sync_enabled=True, hubspot_portal_id="1", publish_batch_size=1,
            normalize_batch_size=1, default_since="2026-01-01T00:00:00Z", tm1_base_url="x",
            tm1_api_key="x", page_size=1, tm1_timeout_seconds=1, tm1_max_retries=1,
            discovery_base_url="x", discovery_api_key="x", event_cache_ttl_hours=1,
            discovery_max_per_run=1, discovery_view_internal=False, discovery_timeout_seconds=1,
            discovery_max_retries=1, discovery_rps_limit=1, hubspot_base_url="x",
            hubspot_client_id="x", hubspot_client_secret="x", hubspot_order_object_type="orders",
            orders_to_contacts_assoc_type_id=1, hubspot_rate_limit_max=1, hubspot_rate_limit_window_ms=1,
        )
        self.events = []
        self.lock = mock.Mock()
        self.lock.acquire.side_effect = lambda: self.events.append("acquire") or True
        self.lock.release.side_effect = lambda: self.events.append("release") or True

        def stage(name, result):
            return mock.Mock(side_effect=lambda *a, **k: self.events.append(name) or result)

        self.patches = {
            "load_config": mock.Mock(return_value=cfg),
            "AdvisoryLock": mock.Mock(return_value=self.lock),
            "migrate": stage("migrate", None),
            "reclaim_stale_processing_rows": stage("reclaim", 0),
            "count_outbox_by_status": mock.Mock(return_value=[]),
            "ingest_orders_once": stage("ingest", {}),
            "normalize_once": stage("normalize", ({}, [])),
            "backfill_order_events_from_outbox": stage("backfill", 0),
            "fetch_events_once": stage("events_fetch", {}),
            "enqueue_from_normalized_batch": stage("enqueue", {}),
            "publish_once": stage("publish", {}),
            "log": mock.Mock(side_effect=lambda event, **k: self.events.append(f"log:{event}")),
        }
        for name, value in self.patches.items():
            mock.patch.object(self.main, name, value).start()

    def tearDown(self):
        mock.patch.stopall()

    def _stages(self):
        return [e for e in self.events if not e.startswith("log:")]

    def test_lock_spans_all_stages(self):
        self.main.run_once()
        self.assertEqual(
            self._stages(),
            ["acquire", "migrate", "reclaim", "ingest", "normalize", "backfill",
             "events_fetch", "enqueue", "publish", "release"],
        )
        self.patches["AdvisoryLock"].assert_called_once_with("tm1_sync:c1")

    def test_lock_not_acquired_skips_run(self):
        self.lock.acquire.side_effect = lambda: self.events.append("acquire") or False
        self.main.run_once()
        self.assertEqual(self._stages(), ["acquire"])
        self.assertIn("log:run.skipped_lock_not_acquired", self.events)
        self.lock.release.assert_not_called()

    def test_lock_released_when_a_stage_fails(self):
        self.patches["publish_once"].side_effect = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self.main.run_once()
        self.assertEqual(self._stages()[-1], "release")
        self.assertIn("log:run.error", self.events)

    def test_release_failure_is_logged_not_raised(self):
        self.lock.release.side_effect = lambda: self.events.append("release") or False
        self.main.run_once()
        self.assertIn("log:run.lock.release_failed", self.events)

    def test_disabled_sync_takes_no_lock(self):
        self.patches["load_config"].return_value.__dict__["sync_enabled"] = False
        self.main.run_once()
        self.patches["AdvisoryLock"].assert_not_called()


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL not set: skipping real PostgreSQL lock tests")
class AdvisoryLockPostgresTests(unittest.TestCase):
    NAME = "tm1_sync:integration-test-client"

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"DATABASE_URL": TEST_DATABASE_URL})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _advisory_locks_held(self) -> int:
        import psycopg2

        with psycopg2.connect(TEST_DATABASE_URL) as c, c.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted "
                "AND ((classid::bigint << 32) | objid::bigint) = %s",
                (locks._lock_key(self.NAME),),
            )
            n = cur.fetchone()[0]
        c.close()
        return n

    def test_lock_is_held_until_release_and_blocks_overlapping_run(self):
        a = AdvisoryLock(self.NAME)
        b = AdvisoryLock(self.NAME)
        self.assertTrue(a.acquire())
        try:
            self.assertEqual(self._advisory_locks_held(), 1, "lock must still be held after acquire()")
            self.assertFalse(b.acquire(), "an overlapping run must be rejected")
        finally:
            self.assertTrue(a.release())
        self.assertEqual(self._advisory_locks_held(), 0)
        self.assertTrue(b.acquire(), "lock must be available again after release")
        b.release()

    def test_lock_freed_if_process_dies_without_release(self):
        a = AdvisoryLock(self.NAME)
        self.assertTrue(a.acquire())
        a._conn.close()  # simulate a crash: the session ends without an explicit unlock
        a._conn = None
        b = AdvisoryLock(self.NAME)
        self.assertTrue(b.acquire())
        b.release()

    def test_context_manager(self):
        with AdvisoryLock(self.NAME) as acquired:
            self.assertTrue(acquired)
            self.assertFalse(AdvisoryLock(self.NAME).acquire())
        self.assertEqual(self._advisory_locks_held(), 0)


if __name__ == "__main__":
    unittest.main()

import hashlib
from typing import Optional

import psycopg2
from psycopg2.extensions import connection as PGConnection

from db.connection import get_database_url

# TCP keepalives for the long-lived lock connection, so a dropped connection is detected
# instead of the run silently continuing without its lock.
_KEEPALIVE_KWARGS = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}


def _lock_key(name: str) -> int:
    """
    Derives a stable positive 63-bit int from a string.
    Postgres advisory locks accept BIGINT.
    """
    h = hashlib.sha256(name.encode("utf-8")).hexdigest()
    val = int(h[:16], 16)  # 64-bit
    return val & ((1 << 63) - 1)


def _connect() -> PGConnection:
    return psycopg2.connect(get_database_url(), **_KEEPALIVE_KWARGS)


class AdvisoryLock:
    """
    Session-level Postgres advisory lock held on a dedicated connection.

    Postgres releases session advisory locks when the session ends, so the connection
    that acquired the lock must stay open for the whole protected operation. This class
    keeps it open until release().

    Requires a direct connection or a session-mode pool: advisory session locks do not
    work through a transaction-mode connection pooler.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.key = _lock_key(name)
        self._conn: Optional[PGConnection] = None

    @property
    def held(self) -> bool:
        return self._conn is not None

    def acquire(self) -> bool:
        """Non-blocking. Returns True if this process now holds the lock."""
        if self._conn is not None:
            raise RuntimeError(f"lock already held: {self.name}")

        conn = _connect()
        try:
            conn.autocommit = True  # idle between statements, never "idle in transaction"
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s);", (self.key,))
                acquired = bool(cur.fetchone()[0])
        except Exception:
            conn.close()
            raise

        if not acquired:
            conn.close()
            return False

        self._conn = conn
        return True

    def release(self) -> bool:
        """
        Unlocks and closes the lock connection. Never raises, so it cannot mask an error
        from the protected operation. Closing the session releases the lock even if the
        explicit unlock fails. Returns False if the explicit unlock did not succeed.
        Calling it when the lock is not held is a no-op that returns True.
        """
        conn = self._conn
        if conn is None:
            return True
        self._conn = None

        unlocked = False
        try:
            if not conn.closed:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s);", (self.key,))
                    unlocked = bool(cur.fetchone()[0])
        except Exception:
            unlocked = False
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return unlocked

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

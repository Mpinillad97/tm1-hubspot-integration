import hashlib

from db.connection import get_conn


def _lock_key(name: str) -> int:
    """
    Derives a stable positive 63-bit int from a string.
    Postgres advisory locks accept BIGINT.
    """
    h = hashlib.sha256(name.encode("utf-8")).hexdigest()
    val = int(h[:16], 16)  # 64-bit
    return val & ((1 << 63) - 1)


def try_acquire_lock(lock_name: str) -> bool:
    key = _lock_key(lock_name)
    with get_conn(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s);", (key,))
            return bool(cur.fetchone()[0])


def release_lock(lock_name: str) -> None:
    key = _lock_key(lock_name)
    with get_conn(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s);", (key,))
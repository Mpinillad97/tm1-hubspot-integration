import os
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PGConnection


def _require_env(name: str) -> str:
    v = os.getenv(name)
    if not v or not v.strip():
        raise RuntimeError(f"Missing required env var: {name}")
    return v.strip()


def get_database_url() -> str:
    return _require_env("DATABASE_URL")


@contextmanager
def get_conn(*, autocommit: bool = False) -> Iterator[PGConnection]:
    """
    Creates a Postgres connection using DATABASE_URL.
    """
    conn = psycopg2.connect(get_database_url())
    conn.autocommit = autocommit
    try:
        yield conn
    finally:
        conn.close()


def ping_db() -> None:
    """
    Simple connectivity check; raises if cannot connect/query.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            _ = cur.fetchone()
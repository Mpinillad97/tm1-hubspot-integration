from typing import Optional

from db.connection import get_conn


def get_hubspot_meta(*, portal_id: str, key: str) -> Optional[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS hubspot_meta (
                  portal_id text NOT NULL,
                  key text NOT NULL,
                  value text NOT NULL,
                  updated_at timestamptz NOT NULL DEFAULT now(),
                  PRIMARY KEY (portal_id, key)
                );
                """
            )
            cur.execute(
                "SELECT value FROM hubspot_meta WHERE portal_id=%s AND key=%s",
                (str(portal_id), str(key)),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None


def set_hubspot_meta(*, portal_id: str, key: str, value: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS hubspot_meta (
                  portal_id text NOT NULL,
                  key text NOT NULL,
                  value text NOT NULL,
                  updated_at timestamptz NOT NULL DEFAULT now(),
                  PRIMARY KEY (portal_id, key)
                );
                """
            )
            cur.execute(
                """
                INSERT INTO hubspot_meta (portal_id, key, value, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (portal_id, key) DO UPDATE
                SET value=EXCLUDED.value, updated_at=now();
                """,
                (str(portal_id), str(key), str(value)),
            )
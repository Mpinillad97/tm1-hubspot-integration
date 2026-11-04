from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from psycopg2.extras import Json

from db.connection import get_conn


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_unique_order_id(order_id: str, creation_dt: Optional[str]) -> str:
    """
    Matches the original logic:
    tm1_order_id = "{order.id}|{creationDateTime}" (full precision).
    """
    base = (order_id or "").strip()
    dt = (creation_dt or "").strip() if creation_dt else ""
    return f"{base}|{dt}" if dt else base


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# -------------------------
# sync_state
# -------------------------
def get_last_since(client_id: str, entity: str) -> Optional[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT last_since
                FROM sync_state
                WHERE client_id = %s AND entity = %s
                """,
                (client_id, entity),
            )
            row = cur.fetchone()
            return row[0] if row else None


def set_last_since(client_id: str, entity: str, last_since: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sync_state (client_id, entity, last_since, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (client_id, entity)
                DO UPDATE SET last_since = EXCLUDED.last_since, updated_at = now()
                """,
                (client_id, entity, last_since),
            )
        conn.commit()


# -------------------------
# tm1_orders_raw (audit)
# -------------------------
def insert_order_raw(
    client_id: str,
    tm1_order_id: str,
    payload: Dict[str, Any],
    fetched_at: Optional[datetime] = None,
) -> None:
    fetched_at = fetched_at or utcnow()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tm1_orders_raw (client_id, tm1_order_id, payload, fetched_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (client_id, tm1_order_id, fetched_at) DO NOTHING
                """,
                (client_id, tm1_order_id, Json(payload), fetched_at),
            )
        conn.commit()


def fetch_raw_orders_pending_normalize(
    client_id: str, limit: int
) -> List[Tuple[int, str, Dict[str, Any]]]:
    """
    Returns list of (raw_id, tm1_order_id, payload) for rows not yet normalized.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, tm1_order_id, payload
                FROM tm1_orders_raw
                WHERE client_id = %s
                  AND normalized_at IS NULL
                ORDER BY id ASC
                LIMIT %s
                """,
                (client_id, limit),
            )
            rows = cur.fetchall()
            return [(int(r[0]), str(r[1]), dict(r[2])) for r in rows]


def mark_raw_orders_normalized(raw_ids: Sequence[int]) -> None:
    if not raw_ids:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tm1_orders_raw
                SET normalized_at = now()
                WHERE id = ANY(%s)
                """,
                (list(raw_ids),),
            )
        conn.commit()


# -------------------------
# tm1_orders (normalized)
# -------------------------
def upsert_tm1_order(client_id: str, tm1_order_id: str, payload: Dict[str, Any]) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tm1_orders (client_id, tm1_order_id, payload, created_at, updated_at)
                VALUES (%s, %s, %s, now(), now())
                ON CONFLICT (client_id, tm1_order_id)
                DO UPDATE SET payload = EXCLUDED.payload, updated_at = now()
                """,
                (client_id, tm1_order_id, Json(payload)),
            )
        conn.commit()


# -------------------------
# tm1_customers (normalized)
# -------------------------
def upsert_tm1_customer(
    client_id: str,
    email: str,
    payload: Dict[str, Any],
) -> None:
    """
    Dedupe by (client_id, email) where email is citext in DB (case-insensitive).
    Also stores email_normalized.
    """
    email_clean = (email or "").strip()
    email_norm = normalize_email(email_clean)

    if not email_norm:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tm1_customers (
                  client_id, email, email_normalized, payload, first_seen_at, last_seen_at
                )
                VALUES (%s, %s, %s, %s, now(), now())
                ON CONFLICT (client_id, email)
                DO UPDATE SET
                  payload = EXCLUDED.payload,
                  email_normalized = EXCLUDED.email_normalized,
                  last_seen_at = now()
                """,
                (client_id, email_clean, email_norm, Json(payload)),
            )
        conn.commit()


# -------------------------
# outbox dedupe helpers
# -------------------------
def _has_sent_outbox_row_with_hubspot_id(client_id: str, entity: str, dedupe_key: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM hubspot_outbox
                WHERE client_id = %s
                  AND entity = %s
                  AND dedupe_key = %s
                  AND status = 'sent'
                  AND hubspot_object_id IS NOT NULL
                  AND btrim(hubspot_object_id) <> ''
                LIMIT 1
                """,
                (client_id, entity, dedupe_key),
            )
            return cur.fetchone() is not None


def _has_inflight_outbox_row(client_id: str, entity: str, dedupe_key: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM hubspot_outbox
                WHERE client_id = %s
                  AND entity = %s
                  AND dedupe_key = %s
                  AND status IN ('pending', 'processing')
                LIMIT 1
                """,
                (client_id, entity, dedupe_key),
            )
            return cur.fetchone() is not None


def _has_sent_outbox_row(client_id: str, entity: str, dedupe_key: str) -> bool:
    """Check if a row was already sent, regardless of hubspot_object_id."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM hubspot_outbox
                WHERE client_id = %s
                  AND entity = %s
                  AND dedupe_key = %s
                  AND status = 'sent'
                LIMIT 1
                """,
                (client_id, entity, dedupe_key),
            )
            return cur.fetchone() is not None


# -------------------------
# outbox enqueue / processing
# -------------------------
def enqueue_outbox(
    client_id: str,
    entity: str,
    dedupe_key: str,
    payload: Dict[str, Any],
) -> bool:
    """
    Enqueue a message for HubSpot publish.

    Returns True if inserted, False if skipped due to dedupe.

    Special handling for entity='event':
      - skip if already SENT with hubspot_object_id
      - skip if currently inflight (pending / processing)
      - otherwise allow reinsertion

    For all other entities:
      - keep current ON CONFLICT DO NOTHING behavior
    """
    client_id = str(client_id or "").strip()
    entity = str(entity or "").strip()
    dedupe_key = str(dedupe_key or "").strip()

    if not client_id or not entity or not dedupe_key:
        return False

    _ASSOCIATION_ENTITIES = {
        "association",
        "order_event_association",
        "event_seat_association",
        "order_seat_association",
        "contact_seat_association",
    }

    if entity == "event":
        if _has_sent_outbox_row_with_hubspot_id(client_id, entity, dedupe_key):
            return False

        if _has_inflight_outbox_row(client_id, entity, dedupe_key):
            return False

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hubspot_outbox (
                      client_id, entity, dedupe_key, payload,
                      status, attempts, next_retry_at, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, 'pending', 0, now(), now(), now())
                    ON CONFLICT DO NOTHING
                    """,
                    (client_id, entity, dedupe_key, Json(payload)),
                )
                inserted = cur.rowcount == 1
            conn.commit()
            return inserted

    if entity in _ASSOCIATION_ENTITIES:
        if _has_sent_outbox_row(client_id, entity, dedupe_key):
            return False

        if _has_inflight_outbox_row(client_id, entity, dedupe_key):
            return False

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hubspot_outbox (
                      client_id, entity, dedupe_key, payload,
                      status, attempts, next_retry_at, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, 'pending', 0, now(), now(), now())
                    ON CONFLICT DO NOTHING
                    """,
                    (client_id, entity, dedupe_key, Json(payload)),
                )
                inserted = cur.rowcount == 1
            conn.commit()
            return inserted

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO hubspot_outbox (
                  client_id, entity, dedupe_key, payload,
                  status, attempts, next_retry_at, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, 'pending', 0, now(), now(), now())
                ON CONFLICT DO NOTHING
                """,
                (client_id, entity, dedupe_key, Json(payload)),
            )
            inserted = cur.rowcount == 1
        conn.commit()
        return inserted


def reclaim_stale_processing_rows(client_id: str, older_than_minutes: int = 30) -> int:
    """
    Move stale 'processing' rows back to 'pending' so they can be retried.
    Returns number of rows reclaimed.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE hubspot_outbox
                SET status = 'pending',
                    next_retry_at = now(),
                    last_error = LEFT(
                        COALESCE(last_error, '') || ' | reclaimed_stale_processing',
                        2000
                    ),
                    updated_at = now()
                WHERE client_id = %s
                  AND status = 'processing'
                  AND updated_at < (now() - (%s || ' minutes')::interval)
                """,
                (client_id, int(older_than_minutes)),
            )
            count = cur.rowcount
        conn.commit()
        return count


def count_outbox_by_status(client_id: str) -> List[Tuple[str, int]]:
    """
    Returns list of (status, count) for a given client_id.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status::text, COUNT(*)
                FROM hubspot_outbox
                WHERE client_id = %s
                GROUP BY status
                ORDER BY status
                """,
                (client_id,),
            )
            rows = cur.fetchall()
            return [(str(r[0]), int(r[1])) for r in rows]


def fetch_outbox_pending_batch(
    client_id: str,
    limit: int,
) -> List[Tuple[int, str, str, Dict[str, Any], int]]:
    """
    Fetch pending outbox rows ready to process and mark them 'processing'
    using SKIP LOCKED. Returns:
      (id, entity, dedupe_key, payload, attempts)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH cte AS (
                  SELECT id
                  FROM hubspot_outbox
                  WHERE client_id = %s
                    AND status = 'pending'
                    AND next_retry_at <= now()
                  ORDER BY created_at ASC
                  LIMIT %s
                  FOR UPDATE SKIP LOCKED
                )
                UPDATE hubspot_outbox o
                SET status = 'processing',
                    updated_at = now()
                FROM cte
                WHERE o.id = cte.id
                RETURNING o.id, o.entity, o.dedupe_key, o.payload, o.attempts
                """,
                (client_id, limit),
            )
            rows = cur.fetchall()
        conn.commit()
        return [(int(r[0]), str(r[1]), str(r[2]), dict(r[3]), int(r[4])) for r in rows]


def mark_outbox_sent(outbox_id: int, hubspot_object_id: Optional[str] = None) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE hubspot_outbox
                SET status = 'sent',
                    hubspot_object_id = COALESCE(%s, hubspot_object_id),
                    updated_at = now()
                WHERE id = %s
                """,
                (hubspot_object_id, outbox_id),
            )
        conn.commit()


def mark_outbox_failed(outbox_id: int, attempts: int, last_error: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE hubspot_outbox
                SET status = 'failed',
                    attempts = %s,
                    last_error = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                (attempts, last_error[:2000], outbox_id),
            )
        conn.commit()


def mark_outbox_retry(outbox_id: int, attempts: int, next_retry_at: datetime, last_error: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE hubspot_outbox
                SET status = 'pending',
                    attempts = %s,
                    next_retry_at = %s,
                    last_error = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                (attempts, next_retry_at, last_error[:2000], outbox_id),
            )
        conn.commit()


# -------------------------
# oauth_tokens
# -------------------------
def get_oauth_token_row(portal_id: str) -> Optional[Tuple[datetime, Dict[str, Any]]]:
    """
    Returns (saved_at, token_payload) for a portal_id, or None if not found.
    token_payload contains access_token/refresh_token/expires_in...
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT saved_at, token_payload
                FROM oauth_tokens
                WHERE portal_id = %s
                """,
                (str(portal_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            saved_at = row[0]
            payload = dict(row[1])
            return saved_at, payload


def upsert_oauth_token_row(portal_id: str, token_payload: Dict[str, Any]) -> None:
    """
    Updates oauth_tokens with new payload and bumps saved_at=now().
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oauth_tokens (portal_id, saved_at, token_payload)
                VALUES (%s, now(), %s)
                ON CONFLICT (portal_id) DO UPDATE
                SET saved_at = EXCLUDED.saved_at,
                    token_payload = EXCLUDED.token_payload
                """,
                (str(portal_id), Json(token_payload)),
            )
        conn.commit()
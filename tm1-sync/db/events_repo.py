from typing import Any, Dict, List, Optional

from psycopg2.extras import Json

from db.connection import get_conn
from sync.logging import log


def upsert_order_events(
    *,
    client_id: str,
    tm1_order_id: str,
    primary_event_id: Optional[str],
    event_ids: List[str],
) -> None:
    cleaned_event_ids = []
    seen = set()

    for event_id in event_ids or []:
        eid = str(event_id or "").strip()
        if not eid or eid in seen:
            continue
        seen.add(eid)
        cleaned_event_ids.append(eid)

    distinct_count = len(cleaned_event_ids)
    primary = (primary_event_id or "").strip() or None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tm_order_events (
                  client_id, tm1_order_id, primary_event_id, event_ids, distinct_count, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (client_id, tm1_order_id) DO UPDATE
                SET primary_event_id = EXCLUDED.primary_event_id,
                    event_ids        = EXCLUDED.event_ids,
                    distinct_count   = EXCLUDED.distinct_count,
                    updated_at       = now();
                """,
                (client_id, tm1_order_id, primary, Json(cleaned_event_ids), distinct_count),
            )
        conn.commit()


def backfill_order_events_from_outbox(*, client_id: str) -> int:
    """
    Ensures every pending/retry event in hubspot_outbox has a corresponding
    row in tm_order_events so that fetch_event_ids_to_refresh can find it.
    Returns the number of rows inserted.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tm_order_events (
                  client_id, tm1_order_id, primary_event_id, event_ids, distinct_count, updated_at
                )
                SELECT
                  %s,
                  'backfill:' || o.dedupe_key,
                  o.dedupe_key,
                  jsonb_build_array(o.dedupe_key),
                  1,
                  now()
                FROM hubspot_outbox o
                WHERE o.client_id = %s
                  AND o.entity = 'event'
                  AND o.status IN ('pending', 'processing')
                  AND NOT EXISTS (
                    SELECT 1 FROM tm_order_events toe
                    WHERE toe.client_id = %s
                      AND toe.event_ids::jsonb @> jsonb_build_array(o.dedupe_key)
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM tm_events te
                    WHERE te.client_id = %s
                      AND te.tm_event_id = o.dedupe_key
                      AND te.payload IS NOT NULL
                  )
                ON CONFLICT (client_id, tm1_order_id) DO NOTHING;
                """,
                (client_id, client_id, client_id, client_id),
            )
            inserted = cur.rowcount or 0
        conn.commit()

    if inserted > 0:
        log(
            "backfill.order_events_from_outbox",
            level="INFO",
            client_id=client_id,
            inserted=inserted,
        )

    return inserted


def fetch_event_ids_to_refresh(
    *,
    client_id: str,
    ttl_hours: int,
    limit: int,
) -> List[str]:
    """
    Returns distinct event ids referenced by tm_order_events that are:
      - missing from tm_events, or
      - stale by fetched_at TTL, or
      - present but incomplete (missing payload / key metadata)
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH referenced_event_ids AS (
                  SELECT
                    toe.client_id,
                    toe.tm1_order_id,
                    toe.updated_at AS order_event_updated_at,
                    jsonb_array_elements_text(toe.event_ids::jsonb) AS tm_event_id
                  FROM tm_order_events toe
                  WHERE toe.client_id = %s
                    AND toe.event_ids IS NOT NULL
                    AND jsonb_array_length(toe.event_ids::jsonb) > 0
                ),
                evaluated AS (
                  SELECT
                    rei.tm_event_id,
                    rei.tm1_order_id,
                    rei.order_event_updated_at,
                    e.tm_event_id IS NOT NULL AS has_tm_event_row,
                    e.fetched_at,
                    (e.payload IS NOT NULL) AS has_payload,
                    (e.name IS NOT NULL) AS has_name,
                    (e.start_datetime IS NOT NULL) AS has_start_datetime,
                    (e.venue_name IS NOT NULL) AS has_venue_name,
                    (e.subsystem_id IS NOT NULL) AS has_subsystem_id,
                    (
                      e.tm_event_id IS NULL
                      OR e.fetched_at IS NULL
                      OR e.fetched_at < (now() - (%s || ' hours')::interval)
                      OR e.payload IS NULL
                      OR e.name IS NULL
                      OR e.start_datetime IS NULL
                      OR (
                        (e.venue_name IS NULL OR e.subsystem_id IS NULL)
                        AND e.fetched_at < (now() - interval '2 hours')
                      )
                    ) AS needs_refresh,
                    CASE
                      WHEN e.tm_event_id IS NULL THEN 1
                      WHEN e.payload IS NULL OR e.name IS NULL OR e.start_datetime IS NULL THEN 2
                      WHEN e.venue_name IS NULL OR e.subsystem_id IS NULL THEN 3
                      WHEN e.fetched_at IS NULL THEN 4
                      WHEN e.fetched_at < (now() - (%s || ' hours')::interval) THEN 5
                      ELSE 9
                    END AS refresh_priority
                  FROM referenced_event_ids rei
                  LEFT JOIN tm_events e
                    ON e.client_id = %s
                   AND e.tm_event_id = rei.tm_event_id
                  WHERE rei.tm_event_id IS NOT NULL
                    AND btrim(rei.tm_event_id) <> ''
                ),
                deduped AS (
                  SELECT DISTINCT ON (tm_event_id)
                    tm_event_id,
                    tm1_order_id,
                    order_event_updated_at,
                    has_tm_event_row,
                    fetched_at,
                    has_payload,
                    has_name,
                    has_start_datetime,
                    has_venue_name,
                    has_subsystem_id,
                    needs_refresh,
                    refresh_priority
                  FROM evaluated
                  ORDER BY
                    tm_event_id,
                    refresh_priority ASC,
                    order_event_updated_at DESC
                )
                SELECT
                  tm_event_id,
                  has_tm_event_row,
                  fetched_at,
                  has_payload,
                  has_name,
                  has_start_datetime,
                  has_venue_name,
                  has_subsystem_id,
                  needs_refresh,
                  refresh_priority,
                  tm1_order_id,
                  order_event_updated_at
                FROM deduped
                ORDER BY
                  needs_refresh DESC,
                  refresh_priority ASC,
                  order_event_updated_at DESC,
                  tm_event_id ASC
                LIMIT %s;
                """,
                (client_id, int(ttl_hours), int(ttl_hours), client_id, int(limit)),
            )
            rows = cur.fetchall() or []

    preview = []
    out = []

    for r in rows:
        tm_event_id = r[0]
        has_tm_event_row = r[1]
        fetched_at = r[2]
        has_payload = r[3]
        has_name = r[4]
        has_start_datetime = r[5]
        has_venue_name = r[6]
        has_subsystem_id = r[7]
        needs_refresh = r[8]
        refresh_priority = r[9]
        sample_tm1_order_id = r[10]
        order_event_updated_at = r[11]

        preview.append(
            {
                "tm_event_id": tm_event_id,
                "has_tm_event_row": has_tm_event_row,
                "fetched_at": str(fetched_at) if fetched_at else None,
                "has_payload": has_payload,
                "has_name": has_name,
                "has_start_datetime": has_start_datetime,
                "has_venue_name": has_venue_name,
                "has_subsystem_id": has_subsystem_id,
                "needs_refresh": needs_refresh,
                "refresh_priority": refresh_priority,
                "sample_tm1_order_id": sample_tm1_order_id,
                "order_event_updated_at": str(order_event_updated_at) if order_event_updated_at else None,
            }
        )

        if needs_refresh and isinstance(tm_event_id, str) and tm_event_id.strip():
            out.append(tm_event_id)

    log(
        "events_refresh.debug",
        client_id=client_id,
        ttl_hours=ttl_hours,
        inspected=len(preview),
        candidates=len(out),
        preview=preview[:10],
    )

    return out


def upsert_tm_event(
    *,
    client_id: str,
    tm_event_id: str,
    payload: Optional[Dict[str, Any]],
    discovery_event_id: Optional[str],
    name: Optional[str],
    event_type: Optional[str],
    locale: Optional[str],
    event_url: Optional[str],
    start_datetime_iso: Optional[str],
    local_date: Optional[str],
    local_time: Optional[str],
    timezone: Optional[str],
    status_code: Optional[str],
    sales_public_start_datetime_iso: Optional[str],
    sales_public_end_datetime_iso: Optional[str],
    venue_id: Optional[str],
    venue_ids: Optional[List[str]],
    venue_name: Optional[str],
    city: Optional[str],
    state: Optional[str],
    country_code: Optional[str],
    attraction_ids: Optional[List[str]],
    attraction_names: Optional[List[str]],
    product_type: Optional[str],
    subsystem_id: Optional[str],
    subsystem_name: Optional[str],
    please_note: Optional[str],
    info: Optional[str],
) -> None:
    tm_event_id = (tm_event_id or "").strip()
    if not tm_event_id:
        return

    cleaned_venue_ids = []
    seen_venue_ids = set()
    for value in venue_ids or []:
        v = str(value or "").strip()
        if not v or v in seen_venue_ids:
            continue
        seen_venue_ids.add(v)
        cleaned_venue_ids.append(v)

    cleaned_attraction_ids = []
    seen_attraction_ids = set()
    for value in attraction_ids or []:
        v = str(value or "").strip()
        if not v or v in seen_attraction_ids:
            continue
        seen_attraction_ids.add(v)
        cleaned_attraction_ids.append(v)

    cleaned_attraction_names = []
    seen_attraction_names = set()
    for value in attraction_names or []:
        v = str(value or "").strip()
        if not v or v in seen_attraction_names:
            continue
        seen_attraction_names.add(v)
        cleaned_attraction_names.append(v)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tm_events (
                  client_id,
                  tm_event_id,
                  payload,
                  event_id,
                  name,
                  event_type,
                  locale,
                  event_url,
                  start_datetime,
                  local_date,
                  local_time,
                  timezone,
                  status_code,
                  sales_public_start_datetime,
                  sales_public_end_datetime,
                  venue_id,
                  venue_ids,
                  venue_name,
                  city,
                  state,
                  country_code,
                  attraction_ids,
                  attraction_names,
                  product_type,
                  subsystem_id,
                  subsystem_name,
                  please_note,
                  info,
                  fetched_at,
                  updated_at
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s,
                  CASE WHEN %s IS NULL OR %s = '' THEN NULL ELSE %s::timestamptz END,
                  %s, %s, %s, %s,
                  CASE WHEN %s IS NULL OR %s = '' THEN NULL ELSE %s::timestamptz END,
                  CASE WHEN %s IS NULL OR %s = '' THEN NULL ELSE %s::timestamptz END,
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  now(), now()
                )
                ON CONFLICT (client_id, tm_event_id) DO UPDATE
                SET payload                         = EXCLUDED.payload,
                    event_id                        = EXCLUDED.event_id,
                    name                            = EXCLUDED.name,
                    event_type                      = EXCLUDED.event_type,
                    locale                          = EXCLUDED.locale,
                    event_url                       = EXCLUDED.event_url,
                    start_datetime                  = EXCLUDED.start_datetime,
                    local_date                      = EXCLUDED.local_date,
                    local_time                      = EXCLUDED.local_time,
                    timezone                        = EXCLUDED.timezone,
                    status_code                     = EXCLUDED.status_code,
                    sales_public_start_datetime     = EXCLUDED.sales_public_start_datetime,
                    sales_public_end_datetime       = EXCLUDED.sales_public_end_datetime,
                    venue_id                        = EXCLUDED.venue_id,
                    venue_ids                       = EXCLUDED.venue_ids,
                    venue_name                      = EXCLUDED.venue_name,
                    city                            = EXCLUDED.city,
                    state                           = EXCLUDED.state,
                    country_code                    = EXCLUDED.country_code,
                    attraction_ids                  = EXCLUDED.attraction_ids,
                    attraction_names                = EXCLUDED.attraction_names,
                    product_type                    = EXCLUDED.product_type,
                    subsystem_id                    = EXCLUDED.subsystem_id,
                    subsystem_name                  = EXCLUDED.subsystem_name,
                    please_note                     = EXCLUDED.please_note,
                    info                            = EXCLUDED.info,
                    fetched_at                      = now(),
                    updated_at                      = now();
                """,
                (
                    client_id,
                    tm_event_id,
                    Json(payload) if payload is not None else None,
                    discovery_event_id,
                    name,
                    event_type,
                    locale,
                    event_url,
                    start_datetime_iso,
                    start_datetime_iso,
                    start_datetime_iso,
                    local_date,
                    local_time,
                    timezone,
                    status_code,
                    sales_public_start_datetime_iso,
                    sales_public_start_datetime_iso,
                    sales_public_start_datetime_iso,
                    sales_public_end_datetime_iso,
                    sales_public_end_datetime_iso,
                    sales_public_end_datetime_iso,
                    venue_id,
                    Json(cleaned_venue_ids),
                    venue_name,
                    city,
                    state,
                    country_code,
                    Json(cleaned_attraction_ids),
                    Json(cleaned_attraction_names),
                    product_type,
                    subsystem_id,
                    subsystem_name,
                    please_note,
                    info,
                ),
            )
        conn.commit()
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json

from db.connection import get_conn


def upsert_order_seats(
    *,
    client_id: str,
    seats: List[Dict[str, Any]],
) -> int:
    """
    Upsert extracted seat records into tm1_seats.
    Returns number of rows upserted.
    """
    if not seats:
        return 0

    count = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for seat in seats:
                seat_unique_id = seat.get("seat_unique_id")
                if not seat_unique_id:
                    continue

                cur.execute(
                    """
                    INSERT INTO tm1_seats (
                      client_id, seat_unique_id, tm1_order_id, tm_event_id,
                      item_group_index, item_index, payload,
                      created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())
                    ON CONFLICT (client_id, seat_unique_id) DO UPDATE
                    SET tm1_order_id     = EXCLUDED.tm1_order_id,
                        tm_event_id      = EXCLUDED.tm_event_id,
                        item_group_index = EXCLUDED.item_group_index,
                        item_index       = EXCLUDED.item_index,
                        payload          = EXCLUDED.payload,
                        updated_at       = now();
                    """,
                    (
                        client_id,
                        seat_unique_id,
                        seat.get("tm1_order_id"),
                        seat.get("tm_event_id"),
                        seat.get("item_group_index", 0),
                        seat.get("item_index", 0),
                        Json(seat),
                    ),
                )
                count += 1
        conn.commit()

    return count


def get_seat_cached(
    *,
    client_id: str,
    seat_unique_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Retrieve a seat from tm1_seats cache by its unique ID.
    Returns the payload dict or None.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload
                FROM tm1_seats
                WHERE client_id = %s AND seat_unique_id = %s
                """,
                (client_id, seat_unique_id),
            )
            row = cur.fetchone()
            if row and row[0]:
                return row[0] if isinstance(row[0], dict) else None
            return None

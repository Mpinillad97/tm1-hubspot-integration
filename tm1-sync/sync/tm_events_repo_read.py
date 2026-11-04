from typing import Any, Dict, Optional

from db.connection import get_conn


def get_tm_event_cached(*, client_id: str, tm_event_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  name, start_datetime, venue_name, city, state, product_type,
                  payload, event_id, event_type, locale, event_url,
                  local_date, local_time, timezone, status_code,
                  sales_public_start_datetime, sales_public_end_datetime,
                  venue_id, venue_ids, country_code,
                  attraction_ids, attraction_names,
                  subsystem_id, subsystem_name,
                  please_note, info
                FROM tm_events
                WHERE client_id=%s AND tm_event_id=%s
                """,
                (client_id, tm_event_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "name": row[0],
                "start_datetime": row[1],
                "venue_name": row[2],
                "city": row[3],
                "state": row[4],
                "product_type": row[5],
                "payload": row[6],
                "event_id": row[7],
                "event_type": row[8],
                "locale": row[9],
                "event_url": row[10],
                "local_date": row[11],
                "local_time": row[12],
                "timezone": row[13],
                "status_code": row[14],
                "sales_public_start_datetime": row[15],
                "sales_public_end_datetime": row[16],
                "venue_id": row[17],
                "venue_ids": row[18],
                "country_code": row[19],
                "attraction_ids": row[20],
                "attraction_names": row[21],
                "subsystem_id": row[22],
                "subsystem_name": row[23],
                "please_note": row[24],
                "info": row[25],
            }
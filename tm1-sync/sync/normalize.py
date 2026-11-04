from typing import Any, Dict, List, Tuple

from db.events_repo import upsert_order_events
from db.repo import (
    fetch_raw_orders_pending_normalize,
    mark_raw_orders_normalized,
    upsert_tm1_customer,
    upsert_tm1_order,
)
from db.seats_repo import upsert_order_seats
from sync.event_extract import choose_primary_event_id, extract_event_ids
from sync.hubspot_client import sha10
from sync.logging import log, log_item
from sync.seat_extract import extract_seats


def _extract_customer(order_payload: Dict[str, Any]) -> Dict[str, Any]:
    customer = order_payload.get("customer") or {}
    return customer if isinstance(customer, dict) else {}


def normalize_once(client_id: str, batch_size: int) -> Tuple[Dict[str, int], List[Tuple[str, Dict[str, Any]]]]:
    """
    Normalize pending RAW orders into tm1_orders and tm1_customers.
    Also extracts event IDs from itemGroups and stores them in tm_order_events.

    Returns:
      (counts, normalized_items) where normalized_items is a list of (tm1_order_id, order_payload)
    Marks RAW rows as normalized only if batch succeeds.
    """
    counts = {
        "raw_fetched": 0,
        "orders_upserted": 0,
        "customers_upserted": 0,
        "customers_skipped_piiCleared_true": 0,
        "customers_skipped_missing_email": 0,
        "errors": 0,

        # events
        "orders_with_event": 0,
        "orders_missing_event": 0,
        "multi_event_orders_detected": 0,

        # seats
        "seats_extracted": 0,
        "orders_with_seats": 0,
        "orders_without_seats": 0,
    }

    raws = fetch_raw_orders_pending_normalize(client_id, batch_size)
    counts["raw_fetched"] = len(raws)

    if not raws:
        log("normalize.noop", client_id=client_id, batch_size=batch_size)
        return counts, []

    raw_ids: List[int] = []
    normalized_items: List[Tuple[str, Dict[str, Any]]] = []

    log("normalize.start", client_id=client_id, batch_size=batch_size, raw_fetched=len(raws))

    try:
        for idx, (raw_id, tm1_order_id, order_payload) in enumerate(raws):
            raw_ids.append(raw_id)

            # 1) Always upsert order snapshot
            upsert_tm1_order(client_id, tm1_order_id, order_payload)
            counts["orders_upserted"] += 1
            normalized_items.append((tm1_order_id, order_payload))

            log_item(
                "normalize.item.order_upserted",
                idx=idx,
                raw_id=raw_id,
                tm1_order_id=tm1_order_id,
            )

            # 2) Extract event ids (new)
            event_ids = extract_event_ids(order_payload)
            if not event_ids:
                counts["orders_missing_event"] += 1
                log_item(
                    "event.missing",
                    idx=idx,
                    raw_id=raw_id,
                    tm1_order_id=tm1_order_id,
                )
            else:
                counts["orders_with_event"] += 1
                primary_event_id = choose_primary_event_id(event_ids)

                # Detector: multi-event (do NOT fail)
                if len(event_ids) > 1:
                    counts["multi_event_orders_detected"] += 1
                    log(
                        "event.multi_event_detected",
                        level="WARN",
                        client_id=client_id,
                        tm1_order_id=tm1_order_id,
                        distinct_event_ids=len(event_ids),
                        event_ids_preview=event_ids[:5],
                    )

                # Persist to DB (safe for future phases)
                upsert_order_events(
                    client_id=client_id,
                    tm1_order_id=tm1_order_id,
                    primary_event_id=primary_event_id,
                    event_ids=event_ids,
                )

                log_item(
                    "event.extracted",
                    idx=idx,
                    raw_id=raw_id,
                    tm1_order_id=tm1_order_id,
                    primary_event_id=primary_event_id,
                    distinct_event_ids=len(event_ids),
                )

            # 3) Seat extraction
            seats = extract_seats(tm1_order_id, order_payload)
            if seats:
                upsert_order_seats(client_id=client_id, seats=seats)
                counts["seats_extracted"] += len(seats)
                counts["orders_with_seats"] += 1
                log_item(
                    "normalize.item.seats_extracted",
                    idx=idx,
                    raw_id=raw_id,
                    tm1_order_id=tm1_order_id,
                    seat_count=len(seats),
                )
            else:
                counts["orders_without_seats"] += 1

            # 4) Customer handling (existing)
            customer = _extract_customer(order_payload)
            pii_cleared = customer.get("piiCleared", False)

            if pii_cleared is True:
                counts["customers_skipped_piiCleared_true"] += 1
                log_item(
                    "normalize.item.customer_skipped_piiCleared_true",
                    idx=idx,
                    raw_id=raw_id,
                    tm1_order_id=tm1_order_id,
                )
                continue

            email = customer.get("email")
            if not email or not str(email).strip():
                counts["customers_skipped_missing_email"] += 1
                log_item(
                    "normalize.item.customer_skipped_missing_email",
                    idx=idx,
                    raw_id=raw_id,
                    tm1_order_id=tm1_order_id,
                )
                continue

            upsert_tm1_customer(client_id, str(email), customer)
            counts["customers_upserted"] += 1

            log_item(
                "normalize.item.customer_upserted",
                idx=idx,
                raw_id=raw_id,
                tm1_order_id=tm1_order_id,
                email_hash=sha10(str(email)),
            )

        # Only mark as normalized after full success
        mark_raw_orders_normalized(raw_ids)

        log(
            "normalize.done",
            client_id=client_id,
            raw_fetched=counts["raw_fetched"],
            orders_upserted=counts["orders_upserted"],
            customers_upserted=counts["customers_upserted"],
            customers_skipped_piiCleared_true=counts["customers_skipped_piiCleared_true"],
            customers_skipped_missing_email=counts["customers_skipped_missing_email"],
            orders_with_event=counts["orders_with_event"],
            orders_missing_event=counts["orders_missing_event"],
            multi_event_orders_detected=counts["multi_event_orders_detected"],
            seats_extracted=counts["seats_extracted"],
            orders_with_seats=counts["orders_with_seats"],
            orders_without_seats=counts["orders_without_seats"],
        )
        return counts, normalized_items

    except Exception as e:
        counts["errors"] += 1
        log("normalize.error", level="ERROR", client_id=client_id, err=type(e).__name__)
        # Do NOT mark raws normalized on failure
        return counts, []
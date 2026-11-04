from typing import Any, Dict, List, Tuple

from db.repo import enqueue_outbox, normalize_email
from sync.event_extract import choose_primary_event_id, extract_event_ids
from sync.hubspot_client import sha10
from sync.logging import log, log_item
from sync.seat_extract import extract_seats


def _customer_from_order(order_payload: Dict[str, Any]) -> Dict[str, Any]:
    c = order_payload.get("customer") or {}
    return c if isinstance(c, dict) else {}


def _order_props_for_hubspot(order_payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"source": "tm1", "order": order_payload}


def _contact_props_for_hubspot(customer_payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"source": "tm1", "customer": customer_payload}


def _event_payload_for_hubspot(tm_event_id: str) -> Dict[str, Any]:
    return {"source": "tm_discovery", "tm_event_id": tm_event_id}


def enqueue_from_normalized_batch(
    *,
    client_id: str,
    normalized_items: List[Tuple[str, Dict[str, Any]]],
) -> Dict[str, int]:
    """
    normalized_items: list of (tm1_order_id, order_payload)
    Enqueues:
      - contact (if customer ok)
      - order
      - association (order->contact)
      - event (1 per order; primary event)
      - order_event_association (order->event)
    """
    counts = {
        "orders_enqueued": 0,
        "orders_skipped_dedupe": 0,
        "contacts_enqueued": 0,
        "contacts_skipped_dedupe": 0,
        "associations_enqueued": 0,
        "associations_skipped_dedupe": 0,
        "events_enqueued": 0,
        "events_skipped_dedupe": 0,
        "order_event_assoc_enqueued": 0,
        "order_event_assoc_skipped_dedupe": 0,
        "orders_missing_event": 0,
        "multi_event_orders_detected": 0,
        "seats_enqueued": 0,
        "seats_skipped_dedupe": 0,
        "order_seat_assoc_enqueued": 0,
        "order_seat_assoc_skipped_dedupe": 0,
        "event_seat_assoc_enqueued": 0,
        "event_seat_assoc_skipped_dedupe": 0,
        "contact_seat_assoc_enqueued": 0,
        "contact_seat_assoc_skipped_dedupe": 0,
        "contacts_skipped_piiCleared_true": 0,
        "contacts_skipped_missing_email": 0,
        "total_items": len(normalized_items),
    }

    if not normalized_items:
        log("enqueue.noop", client_id=client_id, total_items=0)
        return counts

    log("enqueue.start", client_id=client_id, total_items=len(normalized_items))

    for idx, (tm1_order_id, order_payload) in enumerate(normalized_items):
        # 1) Order event (always)
        order_dedupe = tm1_order_id
        order_payload_for_outbox = _order_props_for_hubspot(order_payload)
        order_inserted = enqueue_outbox(client_id, "order", order_dedupe, order_payload_for_outbox)
        if order_inserted:
            counts["orders_enqueued"] += 1
        else:
            counts["orders_skipped_dedupe"] += 1

        log_item(
            "enqueue.item",
            idx=idx,
            entity="order",
            inserted=order_inserted,
            tm1_order_id=tm1_order_id,
            dedupe_key=order_dedupe,
        )

        # events
        event_ids = extract_event_ids(order_payload)
        if not event_ids:
            counts["orders_missing_event"] += 1
            log_item(
                "enqueue.item.event_missing",
                idx=idx,
                tm1_order_id=tm1_order_id,
            )
        else:
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

            primary_event_id = choose_primary_event_id(event_ids)
            if primary_event_id:
                event_inserted = enqueue_outbox(
                    client_id,
                    "event",
                    primary_event_id,
                    _event_payload_for_hubspot(primary_event_id),
                )
                if event_inserted:
                    counts["events_enqueued"] += 1
                else:
                    counts["events_skipped_dedupe"] += 1

                log_item(
                    "enqueue.item.event",
                    idx=idx,
                    tm1_order_id=tm1_order_id,
                    tm_event_id=primary_event_id,
                    inserted=event_inserted,
                    dedupe_key=primary_event_id,
                )

                assoc_dedupe = f"{tm1_order_id}->{primary_event_id}"
                assoc_payload = {
                    "source": "tm",
                    "tm1_order_id": tm1_order_id,
                    "tm_event_id": primary_event_id,
                }
                order_event_assoc_inserted = enqueue_outbox(
                    client_id,
                    "order_event_association",
                    assoc_dedupe,
                    assoc_payload,
                )
                if order_event_assoc_inserted:
                    counts["order_event_assoc_enqueued"] += 1
                else:
                    counts["order_event_assoc_skipped_dedupe"] += 1

                log_item(
                    "enqueue.item.order_event_assoc",
                    idx=idx,
                    tm1_order_id=tm1_order_id,
                    tm_event_id=primary_event_id,
                    inserted=order_event_assoc_inserted,
                    dedupe_key=assoc_dedupe,
                )

        # 2) Contact + order->contact association if possible
        customer = _customer_from_order(order_payload)
        pii = customer.get("piiCleared", False)
        if pii is True:
            counts["contacts_skipped_piiCleared_true"] += 1
            log_item(
                "enqueue.item.contact_skipped_piiCleared_true",
                idx=idx,
                tm1_order_id=tm1_order_id,
            )
            continue

        email = customer.get("email")
        if not email or not str(email).strip():
            counts["contacts_skipped_missing_email"] += 1
            log_item(
                "enqueue.item.contact_skipped_missing_email",
                idx=idx,
                tm1_order_id=tm1_order_id,
            )
            continue

        email_norm = normalize_email(str(email))
        if not email_norm:
            counts["contacts_skipped_missing_email"] += 1
            log_item(
                "enqueue.item.contact_skipped_email_normalize_failed",
                idx=idx,
                tm1_order_id=tm1_order_id,
            )
            continue

        contact_dedupe = email_norm
        contact_payload_for_outbox = _contact_props_for_hubspot(customer)
        contact_inserted = enqueue_outbox(client_id, "contact", contact_dedupe, contact_payload_for_outbox)
        if contact_inserted:
            counts["contacts_enqueued"] += 1
        else:
            counts["contacts_skipped_dedupe"] += 1

        log_item(
            "enqueue.item.contact",
            idx=idx,
            tm1_order_id=tm1_order_id,
            email_hash=sha10(str(email)),
            inserted=contact_inserted,
            dedupe_key=contact_dedupe,
        )

        assoc_dedupe = f"{tm1_order_id}->{email_norm}"
        assoc_payload = {
            "source": "tm1",
            "tm1_order_id": tm1_order_id,
            "email": str(email).strip(),
            "email_normalized": email_norm,
        }
        association_inserted = enqueue_outbox(client_id, "association", assoc_dedupe, assoc_payload)
        if association_inserted:
            counts["associations_enqueued"] += 1
        else:
            counts["associations_skipped_dedupe"] += 1

        log_item(
            "enqueue.item.association",
            idx=idx,
            tm1_order_id=tm1_order_id,
            email_hash=sha10(str(email)),
            inserted=association_inserted,
            dedupe_key=assoc_dedupe,
        )

    # 3) Seats + seat associations
    for idx, (tm1_order_id, order_payload) in enumerate(normalized_items):
        seats = extract_seats(tm1_order_id, order_payload)
        if not seats:
            continue

        # Resolve customer email for contact-seat associations
        customer = _customer_from_order(order_payload)
        pii = customer.get("piiCleared", False)
        seat_email_norm = None
        if pii is not True:
            email = customer.get("email")
            if email and str(email).strip():
                seat_email_norm = normalize_email(str(email))

        for seat in seats:
            seat_unique_id = seat["seat_unique_id"]
            tm_event_id = seat.get("tm_event_id")

            # Seat entity
            seat_payload = {"source": "tm1", "seat": seat}
            seat_inserted = enqueue_outbox(client_id, "seat", seat_unique_id, seat_payload)
            if seat_inserted:
                counts["seats_enqueued"] += 1
            else:
                counts["seats_skipped_dedupe"] += 1

            # Order -> Seat association
            os_dedupe = f"{tm1_order_id}->{seat_unique_id}"
            os_payload = {
                "source": "tm1",
                "tm1_order_id": tm1_order_id,
                "seat_unique_id": seat_unique_id,
            }
            os_inserted = enqueue_outbox(client_id, "order_seat_association", os_dedupe, os_payload)
            if os_inserted:
                counts["order_seat_assoc_enqueued"] += 1
            else:
                counts["order_seat_assoc_skipped_dedupe"] += 1

            # Event -> Seat association (only if event exists)
            if tm_event_id:
                es_dedupe = f"{seat_unique_id}->{tm_event_id}"
                es_payload = {
                    "source": "tm1",
                    "seat_unique_id": seat_unique_id,
                    "tm_event_id": tm_event_id,
                }
                es_inserted = enqueue_outbox(client_id, "event_seat_association", es_dedupe, es_payload)
                if es_inserted:
                    counts["event_seat_assoc_enqueued"] += 1
                else:
                    counts["event_seat_assoc_skipped_dedupe"] += 1

            # Contact -> Seat association (only if valid contact)
            if seat_email_norm:
                cs_dedupe = f"{seat_unique_id}->{seat_email_norm}"
                cs_payload = {
                    "source": "tm1",
                    "seat_unique_id": seat_unique_id,
                    "email_normalized": seat_email_norm,
                }
                cs_inserted = enqueue_outbox(client_id, "contact_seat_association", cs_dedupe, cs_payload)
                if cs_inserted:
                    counts["contact_seat_assoc_enqueued"] += 1
                else:
                    counts["contact_seat_assoc_skipped_dedupe"] += 1

    log("enqueue.done", client_id=client_id, **counts)
    return counts
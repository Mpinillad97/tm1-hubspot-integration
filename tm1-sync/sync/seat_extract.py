from typing import Any, Dict, List


def extract_seats(tm1_order_id: str, order_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract seat/item records from TM1 order payload.
    Expected structure:
      order.itemGroups[].items[]

    Each seat gets a deterministic unique ID:
      "{tm1_order_id}|{itemGroupIndex}|{itemIndex}"

    Returns a list of seat dicts with all extracted fields.
    """
    item_groups = order_payload.get("itemGroups") or []
    if not isinstance(item_groups, list):
        return []

    seats: List[Dict[str, Any]] = []

    for g_idx, group in enumerate(item_groups):
        if not isinstance(group, dict):
            continue

        ev = group.get("event") or {}
        tm_event_id = None
        if isinstance(ev, dict):
            eid = ev.get("id")
            if isinstance(eid, str) and eid.strip():
                tm_event_id = eid.strip()

        is_upsell = bool(group.get("upsell", False))

        # TM1 API uses HAL format: items live under _embedded.items
        embedded = group.get("_embedded") or {}
        if not isinstance(embedded, dict):
            embedded = {}

        items = group.get("items") or embedded.get("items") or []
        if not isinstance(items, list):
            continue

        for i_idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue

            seat_unique_id = f"{tm1_order_id}|{g_idx}|{i_idx}"

            location = item.get("location") or {}
            if not isinstance(location, dict):
                location = {}

            price = item.get("price") or {}
            if not isinstance(price, dict):
                price = {}

            offers = item.get("offers") or []
            offer_types: List[str] = []
            if isinstance(offers, list):
                for o in offers:
                    if isinstance(o, dict):
                        ot = o.get("type")
                        if isinstance(ot, str) and ot.strip():
                            offer_types.append(ot.strip())

            seat = {
                "seat_unique_id": seat_unique_id,
                "tm1_order_id": tm1_order_id,
                "tm_event_id": tm_event_id,
                "item_group_index": g_idx,
                "item_index": i_idx,
                "type": _safe_str(item.get("type")),
                "sub_type": _safe_str(item.get("subType")),
                "status": _safe_str(item.get("status")),
                "ticket_holder": item.get("ticketHolder"),
                "transaction_datetime": _safe_str(item.get("transactionDatetime")),
                "accessible": item.get("accessible"),
                "gate": _safe_str(location.get("gate")),
                "level": _safe_str(location.get("level")),
                "place_group": _safe_str(location.get("placeGroup")),
                "section": _safe_str(location.get("section")),
                "row": _safe_str(location.get("row")),
                "seat": _safe_str(location.get("seat")),
                "face_value": _safe_float(price.get("faceValue")),
                "currency": _safe_str(price.get("currency")),
                "price_type": _safe_str(price.get("type")),
                "price_level": _safe_str(price.get("level")),
                "offers": offer_types,
                "is_upsell": is_upsell,
            }

            seats.append(seat)

    return seats


def build_seat_label(seat: Dict[str, Any]) -> str:
    """
    Build a human-readable label for a seat.
    E.g. "Section 101 Row A Seat 5" or "GA" or just the unique ID.
    """
    parts: List[str] = []

    section = seat.get("section")
    if section:
        parts.append(f"Sec {section}")

    row = seat.get("row")
    if row:
        parts.append(f"Row {row}")

    seat_num = seat.get("seat")
    if seat_num:
        parts.append(f"Seat {seat_num}")

    if parts:
        return " ".join(parts)

    gate = seat.get("gate")
    if gate:
        return f"Gate {gate}"

    level = seat.get("level")
    if level:
        return f"Level {level}"

    return seat.get("seat_unique_id", "Unknown Seat")


def _safe_str(value: Any) -> str | None:
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

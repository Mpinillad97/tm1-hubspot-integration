from typing import Any, Dict, List, Tuple


def extract_event_ids(order_payload: Dict[str, Any]) -> List[str]:
    """
    Extract event IDs from TM1 order payload.
    Expected structure:
      order.itemGroups[].event.id
    Returns a list of distinct event ids (stable order).
    """
    item_groups = order_payload.get("itemGroups") or []
    if not isinstance(item_groups, list):
        return []

    seen = set()
    out: List[str] = []

    for g in item_groups:
        if not isinstance(g, dict):
            continue
        ev = g.get("event") or {}
        if not isinstance(ev, dict):
            continue
        ev_id = ev.get("id")
        if isinstance(ev_id, str):
            ev_id = ev_id.strip()
            if ev_id and ev_id not in seen:
                seen.add(ev_id)
                out.append(ev_id)

    return out


def choose_primary_event_id(event_ids: List[str]) -> str:
    """
    Current assumption: 1 event per order.
    If multiple exist, take the first as primary (but we will log that case).
    """
    return event_ids[0] if event_ids else ""
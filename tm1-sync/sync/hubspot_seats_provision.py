from typing import Any, Dict, Optional, List, Set, Tuple

import requests

from db.hubspot_meta_repo import get_hubspot_meta, set_hubspot_meta
from sync.logging import log, http_body_snippet
from sync.token_manager import get_valid_access_token


def _hs_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _schema_label() -> str:
    return "Ticketmaster Seat"


def _schema_internal_name() -> str:
    return "ticketmaster_seat"


def _property_group_name() -> str:
    return "ticketmaster_seat_information"


def _property_group_label() -> str:
    return "Ticketmaster Seat Information"


def _schema_properties() -> List[Dict[str, Any]]:
    g = _property_group_name()
    return [
        {
            "name": "seat_unique_id",
            "label": "Seat Unique ID",
            "type": "string",
            "fieldType": "text",
            "hasUniqueValue": True,
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_name",
            "label": "Seat Name",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_location",
            "label": "Seat Location",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_tm1_order_id",
            "label": "TM1 Order ID",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_tm_event_id",
            "label": "TM Event ID",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_type",
            "label": "Item Type",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_status",
            "label": "Item Status",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_ticket_holder",
            "label": "Ticket Holder",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_transaction_datetime",
            "label": "Transaction Date/Time",
            "type": "datetime",
            "fieldType": "date",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_accessible",
            "label": "Accessible",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_gate",
            "label": "Gate",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_level",
            "label": "Level",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_section",
            "label": "Section",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_row",
            "label": "Row",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_number",
            "label": "Seat Number",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_face_value",
            "label": "Face Value",
            "type": "number",
            "fieldType": "number",
            "numberDisplayHint": "currency",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_currency",
            "label": "Currency",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_price_level",
            "label": "Price Level",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_sub_type",
            "label": "Sub Type",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_place_group",
            "label": "Place Group",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_price_type",
            "label": "Price Type",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_offers",
            "label": "Offers",
            "type": "string",
            "fieldType": "textarea",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
        {
            "name": "seat_is_upsell",
            "label": "Is Upsell",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": g,
        },
    ]


def _find_existing_schema(hubspot_base_url: str, token: str) -> Optional[Dict[str, Any]]:
    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/schemas"
    r = requests.get(url, headers=_hs_headers(token), timeout=45)
    r.raise_for_status()

    results = (r.json() or {}).get("results") or []
    for s in results:
        if s.get("name") == _schema_internal_name():
            return s
    return None


def _associated_objects(order_object_type: str, event_object_type: Optional[str]) -> List[str]:
    objs = ["CONTACT", order_object_type]
    if event_object_type:
        objs.append(event_object_type)
    return objs


def _create_schema(
    hubspot_base_url: str,
    token: str,
    order_object_type: str,
    event_object_type: Optional[str],
) -> Dict[str, Any]:
    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/schemas"

    payload = {
        "name": _schema_internal_name(),
        "labels": {
            "singular": _schema_label(),
            "plural": "Ticketmaster Seats",
        },
        "properties": _schema_properties(),
        "primaryDisplayProperty": "seat_name",
        "requiredProperties": ["seat_unique_id"],
        "searchableProperties": ["seat_unique_id", "seat_name", "seat_location"],
        "secondaryDisplayProperties": ["seat_section", "seat_row", "seat_number"],
        "associatedObjects": _associated_objects(order_object_type, event_object_type),
    }

    r = requests.post(url, headers=_hs_headers(token), json=payload, timeout=45)
    if r.status_code >= 400:
        log(
            "hubspot.schema.create_failed",
            level="ERROR",
            status=r.status_code,
            body=http_body_snippet(r.text),
            schema_internal_name=_schema_internal_name(),
            schema_label=_schema_label(),
        )
        r.raise_for_status()

    return r.json() or {}


def _ensure_schema_associations(
    hubspot_base_url: str,
    token: str,
    seat_object_type: str,
    order_object_type: str,
    event_object_type: Optional[str],
) -> None:
    """PATCH the schema to add associatedObjects if not yet defined."""
    desired = _associated_objects(order_object_type, event_object_type)
    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/schemas/{seat_object_type}"

    r = requests.patch(
        url,
        headers=_hs_headers(token),
        json={"associatedObjects": desired},
        timeout=45,
    )

    if r.status_code >= 400:
        log(
            "hubspot.schema.patch_associations_failed",
            level="WARN",
            status=r.status_code,
            body=http_body_snippet(r.text),
            seat_object_type=seat_object_type,
            desired=desired,
        )
    else:
        log(
            "hubspot.schema.associations_ensured",
            level="INFO",
            seat_object_type=seat_object_type,
            associated_objects=desired,
        )


def _list_properties(hubspot_base_url: str, token: str, object_type: str) -> List[Dict[str, Any]]:
    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/properties/{object_type}"
    r = requests.get(url, headers=_hs_headers(token), timeout=45)
    r.raise_for_status()
    return (r.json() or {}).get("results") or []


def _list_property_groups(hubspot_base_url: str, token: str, object_type: str) -> List[Dict[str, Any]]:
    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/properties/{object_type}/groups"
    r = requests.get(url, headers=_hs_headers(token), timeout=45)
    r.raise_for_status()
    return (r.json() or {}).get("results") or []


def _ensure_property_group(hubspot_base_url: str, token: str, object_type: str) -> None:
    groups = _list_property_groups(hubspot_base_url, token, object_type)
    existing_names = {str(g.get("name") or "") for g in groups}

    if _property_group_name() in existing_names:
        return

    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/properties/{object_type}/groups"
    payload = {
        "name": _property_group_name(),
        "label": _property_group_label(),
        "displayOrder": -1,
    }

    r = requests.post(url, headers=_hs_headers(token), json=payload, timeout=45)
    if r.status_code >= 400:
        log(
            "hubspot.property_group.create_failed",
            level="ERROR",
            status=r.status_code,
            body=http_body_snippet(r.text),
            object_type=object_type,
            group_name=_property_group_name(),
        )
        r.raise_for_status()

    log(
        "hubspot.property_group.created",
        level="INFO",
        object_type=object_type,
        group_name=_property_group_name(),
    )


def _create_property(
    hubspot_base_url: str,
    token: str,
    object_type: str,
    prop: Dict[str, Any],
) -> None:
    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/properties/{object_type}"
    r = requests.post(url, headers=_hs_headers(token), json=prop, timeout=45)
    if r.status_code >= 400:
        log(
            "hubspot.property.create_failed",
            level="ERROR",
            status=r.status_code,
            body=http_body_snippet(r.text),
            object_type=object_type,
            property_name=prop.get("name"),
        )
        r.raise_for_status()

    log(
        "hubspot.property.created",
        level="INFO",
        object_type=object_type,
        property_name=prop.get("name"),
    )


def _ensure_missing_properties(hubspot_base_url: str, token: str, object_type: str) -> None:
    existing = _list_properties(hubspot_base_url, token, object_type)
    existing_names: Set[str] = {str(p.get("name") or "") for p in existing}

    desired = _schema_properties()
    missing = [p for p in desired if str(p.get("name") or "") not in existing_names]

    if not missing:
        log(
            "hubspot.schema.properties_ok",
            level="INFO",
            object_type=object_type,
            missing_count=0,
        )
        return

    log(
        "hubspot.schema.properties_missing",
        level="INFO",
        object_type=object_type,
        missing_count=len(missing),
        missing_names=[p["name"] for p in missing],
    )

    _ensure_property_group(hubspot_base_url, token, object_type)

    for prop in missing:
        _create_property(hubspot_base_url, token, object_type, prop)


def _ensure_association_label(
    hubspot_base_url: str,
    token: str,
    portal_id: str,
    from_object_type: str,
    to_object_type: str,
    label: str,
    internal_name: str,
    meta_key: str,
) -> int:
    """
    Creates a labeled association type between two objects via HubSpot v4.
    Returns the forward typeId. Caches in hubspot_meta.
    """
    cached = get_hubspot_meta(portal_id=portal_id, key=meta_key)
    if cached and cached.strip():
        log(
            "hubspot.seats.assoc_label_cached",
            level="INFO",
            meta_key=meta_key,
            type_id=cached,
        )
        return int(cached)

    url = f"{hubspot_base_url.rstrip('/')}/crm/v4/associations/{from_object_type}/{to_object_type}/labels"
    payload = {"label": label, "name": internal_name}

    r = requests.post(url, headers=_hs_headers(token), json=payload, timeout=45)

    already_exists = (
        r.status_code == 409
        or (r.status_code == 400 and "already exists" in (r.text or ""))
    )

    if already_exists:
        log(
            "hubspot.seats.assoc_label_already_exists",
            level="INFO",
            meta_key=meta_key,
            from_object_type=from_object_type,
            to_object_type=to_object_type,
        )
    elif r.status_code >= 400:
        log(
            "hubspot.seats.assoc_label_create_failed",
            level="ERROR",
            status=r.status_code,
            body=http_body_snippet(r.text),
            meta_key=meta_key,
        )
        r.raise_for_status()

    # Always GET the labels for the specific from→to direction to get the
    # correct typeId. The POST response has both directions and we can't
    # reliably tell which is forward vs reverse.
    gr = requests.get(url, headers=_hs_headers(token), timeout=45)
    gr.raise_for_status()
    results = (gr.json() or {}).get("results") or []

    log(
        "hubspot.seats.assoc_label_get_results",
        level="INFO",
        meta_key=meta_key,
        from_object_type=from_object_type,
        to_object_type=to_object_type,
        results=results,
    )

    # Find the matching label by name or label text
    for item in results:
        if item.get("name") == internal_name or item.get("label") == label:
            type_id = int(item["typeId"])
            set_hubspot_meta(portal_id=portal_id, key=meta_key, value=str(type_id))
            return type_id
    # Fallback: use first USER_DEFINED result for this direction
    for item in results:
        if item.get("category") == "USER_DEFINED":
            type_id = int(item["typeId"])
            set_hubspot_meta(portal_id=portal_id, key=meta_key, value=str(type_id))
            return type_id

    raise RuntimeError(f"Could not find association label typeId for {meta_key}. Results: {results}")

    log(
        "hubspot.seats.assoc_label_created",
        level="INFO",
        meta_key=meta_key,
        type_id=type_id,
        from_object_type=from_object_type,
        to_object_type=to_object_type,
    )
    return type_id


def _ensure_all_seat_association_labels(
    hubspot_base_url: str,
    token: str,
    portal_id: str,
    seat_object_type: str,
    order_object_type: str,
    event_object_type: Optional[str],
    force_refresh: bool = False,
) -> Dict[str, int]:
    """
    Ensures labeled association types exist for Seat <-> Order, Seat <-> Event, Seat <-> Contact.
    Returns dict with type IDs: {
        "order_to_seat": <id>,
        "event_to_seat": <id>,  (only if event_object_type provided)
        "contact_to_seat": <id>,
    }
    """
    if force_refresh:
        # Clear stale cached type IDs so they get re-fetched from the API
        for k in [
            "seat_assoc_order_to_seat_type_id",
            "seat_assoc_event_to_seat_type_id",
            "seat_assoc_contact_to_seat_type_id",
        ]:
            set_hubspot_meta(portal_id=portal_id, key=k, value="")

    result: Dict[str, int] = {}

    result["order_to_seat"] = _ensure_association_label(
        hubspot_base_url, token, portal_id,
        from_object_type=order_object_type,
        to_object_type=seat_object_type,
        label="Order to Seat",
        internal_name="order_to_seat",
        meta_key="seat_assoc_order_to_seat_type_id",
    )

    if event_object_type:
        result["event_to_seat"] = _ensure_association_label(
            hubspot_base_url, token, portal_id,
            from_object_type=event_object_type,
            to_object_type=seat_object_type,
            label="Event to Seat",
            internal_name="event_to_seat",
            meta_key="seat_assoc_event_to_seat_type_id",
        )

    result["contact_to_seat"] = _ensure_association_label(
        hubspot_base_url, token, portal_id,
        from_object_type="contacts",
        to_object_type=seat_object_type,
        label="Contact to Seat",
        internal_name="contact_to_seat",
        meta_key="seat_assoc_contact_to_seat_type_id",
    )

    log(
        "hubspot.seats.assoc_labels_ready",
        level="INFO",
        portal_id=portal_id,
        assoc_type_ids=result,
    )
    return result


def ensure_seat_object(
    *,
    portal_id: str,
    hubspot_base_url: str,
    hubspot_client_id: str,
    hubspot_client_secret: str,
    order_object_type: str,
    event_object_type: Optional[str] = None,
) -> Tuple[str, Dict[str, int]]:
    """
    Ensures the Ticketmaster Seat custom object exists and required properties exist.
    Also ensures labeled association types exist for Order, Event, and Contact.
    Returns (seat_object_type, assoc_type_ids) where assoc_type_ids is a dict
    with keys like 'order_to_seat', 'event_to_seat', 'contact_to_seat'.
    """
    cached_obj = get_hubspot_meta(portal_id=portal_id, key="seat_object_type")

    token = get_valid_access_token(
        portal_id=portal_id,
        hubspot_base_url=hubspot_base_url,
        client_id=hubspot_client_id,
        client_secret=hubspot_client_secret,
    )

    seat_object_type: Optional[str] = None

    if cached_obj:
        seat_object_type = str(cached_obj)
        log(
            "hubspot.seats.object_cached",
            level="INFO",
            portal_id=str(portal_id),
            seat_object_type=seat_object_type,
        )
    else:
        existing = _find_existing_schema(hubspot_base_url, token)
        if not existing:
            log(
                "hubspot.schema.creating",
                level="INFO",
                portal_id=str(portal_id),
                schema_internal_name=_schema_internal_name(),
                schema_label=_schema_label(),
            )
            existing = _create_schema(hubspot_base_url, token, order_object_type, event_object_type)
        else:
            log(
                "hubspot.schema.exists",
                level="INFO",
                portal_id=str(portal_id),
                schema_internal_name=_schema_internal_name(),
                object_type_id=str(existing.get("objectTypeId") or ""),
            )

        seat_object_type = existing.get("objectTypeId")
        if not isinstance(seat_object_type, str) or not seat_object_type.strip():
            raise RuntimeError("Could not resolve seat objectTypeId from schema response")

        set_hubspot_meta(portal_id=portal_id, key="seat_object_type", value=seat_object_type)

    _ensure_missing_properties(hubspot_base_url, token, seat_object_type)

    # Ensure labeled association types exist
    assoc_type_ids = _ensure_all_seat_association_labels(
        hubspot_base_url, token, portal_id,
        seat_object_type, order_object_type, event_object_type,
    )

    log(
        "hubspot.seats.object_ready",
        level="INFO",
        portal_id=str(portal_id),
        seat_object_type=seat_object_type,
        assoc_type_ids=assoc_type_ids,
    )

    return seat_object_type, assoc_type_ids

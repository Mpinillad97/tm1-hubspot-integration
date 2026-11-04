from typing import Any, Dict, Optional, List, Set, Tuple

import requests

from db.hubspot_meta_repo import get_hubspot_meta, set_hubspot_meta
from sync.logging import log, http_body_snippet
from sync.token_manager import get_valid_access_token


def _hs_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _schema_label() -> str:
    return "Ticketmaster Event"


def _schema_internal_name() -> str:
    return "ticketmaster_event"


def _property_group_name() -> str:
    return "ticketmaster_event_information"


def _property_group_label() -> str:
    return "Ticketmaster Event Information"


def _schema_properties() -> List[Dict[str, Any]]:
    return [
        {
            "name": "tm_event_id",
            "label": "TM Event ID",
            "type": "string",
            "fieldType": "text",
            "hasUniqueValue": True,
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_name",
            "label": "Event Name",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_start_datetime",
            "label": "Event Start Date/Time",
            "type": "datetime",
            "fieldType": "date",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_venue_name",
            "label": "Venue Name",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_city",
            "label": "City",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_state",
            "label": "State",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_product_type",
            "label": "Product Type",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_subsystem_id",
            "label": "Subsystem ID",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_subsystem_name",
            "label": "Subsystem Name",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_attraction_ids",
            "label": "Attraction IDs",
            "type": "string",
            "fieldType": "textarea",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_venue_ids",
            "label": "Venue IDs",
            "type": "string",
            "fieldType": "textarea",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_attraction_names",
            "label": "Attraction Names",
            "type": "string",
            "fieldType": "textarea",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_country_code",
            "label": "Country Code",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_url",
            "label": "Event URL",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_timezone",
            "label": "Event Timezone",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_status_code",
            "label": "Event Status",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
        },
        {
            "name": "tm_event_show_type",
            "label": "Show Type",
            "type": "enumeration",
            "fieldType": "select",
            "hidden": False,
            "formField": False,
            "groupName": _property_group_name(),
            "options": [
                {"label": "Matinee", "value": "Matinee", "displayOrder": 0, "hidden": False},
                {"label": "Evening", "value": "Evening", "displayOrder": 1, "hidden": False},
            ],
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


def _create_schema(hubspot_base_url: str, token: str) -> Dict[str, Any]:
    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/schemas"

    payload = {
        "name": _schema_internal_name(),
        "labels": {
            "singular": _schema_label(),
            "plural": "Ticketmaster Events",
        },
        "properties": _schema_properties(),
        "primaryDisplayProperty": "tm_event_name",
        "requiredProperties": ["tm_event_id"],
        "searchableProperties": ["tm_event_id", "tm_event_name"],
        "secondaryDisplayProperties": ["tm_event_start_datetime", "tm_event_venue_name"],
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


def _ensure_order_event_association_label(
    hubspot_base_url: str,
    token: str,
    portal_id: str,
    order_object_type: str,
    event_object_type: str,
) -> int:
    """
    Ensures a labeled association type exists for Order→Event.
    Returns the forward typeId. Caches in hubspot_meta.
    """
    meta_key = "event_assoc_order_to_event_type_id"
    cached = get_hubspot_meta(portal_id=portal_id, key=meta_key)
    if cached and cached.strip():
        log(
            "hubspot.events.assoc_label_cached",
            level="INFO",
            meta_key=meta_key,
            type_id=cached,
        )
        return int(cached)

    url = f"{hubspot_base_url.rstrip('/')}/crm/v4/associations/{order_object_type}/{event_object_type}/labels"
    payload = {"label": "Order to Event", "name": "order_to_event"}

    r = requests.post(url, headers=_hs_headers(token), json=payload, timeout=45)

    already_exists = (
        r.status_code == 409
        or (r.status_code == 400 and "already exists" in (r.text or ""))
    )

    if already_exists:
        log(
            "hubspot.events.assoc_label_already_exists",
            level="INFO",
            meta_key=meta_key,
        )
    elif r.status_code >= 400:
        log(
            "hubspot.events.assoc_label_create_failed",
            level="ERROR",
            status=r.status_code,
            body=http_body_snippet(r.text),
            meta_key=meta_key,
        )
        r.raise_for_status()

    # GET the labels for the correct from→to direction
    gr = requests.get(url, headers=_hs_headers(token), timeout=45)
    gr.raise_for_status()
    results = (gr.json() or {}).get("results") or []

    log(
        "hubspot.events.assoc_label_get_results",
        level="INFO",
        meta_key=meta_key,
        results=results,
    )

    for item in results:
        if item.get("name") == "order_to_event" or item.get("label") == "Order to Event":
            type_id = int(item["typeId"])
            set_hubspot_meta(portal_id=portal_id, key=meta_key, value=str(type_id))
            return type_id
    for item in results:
        if item.get("category") == "USER_DEFINED":
            type_id = int(item["typeId"])
            set_hubspot_meta(portal_id=portal_id, key=meta_key, value=str(type_id))
            return type_id

    raise RuntimeError(f"Could not find association label typeId for {meta_key}. Results: {results}")


def ensure_event_object(
    *,
    portal_id: str,
    hubspot_base_url: str,
    hubspot_client_id: str,
    hubspot_client_secret: str,
    order_object_type: str,
) -> Tuple[str, int]:
    """
    Ensures the Ticketmaster Event custom object exists and required properties exist.
    Also ensures a labeled association type exists for Order→Event.
    Returns (event_object_type, order_to_event_assoc_type_id).
    """
    cached_obj = get_hubspot_meta(portal_id=portal_id, key="event_object_type")

    token = get_valid_access_token(
        portal_id=portal_id,
        hubspot_base_url=hubspot_base_url,
        client_id=hubspot_client_id,
        client_secret=hubspot_client_secret,
    )

    event_object_type: Optional[str] = None

    if cached_obj:
        event_object_type = str(cached_obj)
        log(
            "hubspot.events.object_cached",
            level="INFO",
            portal_id=str(portal_id),
            event_object_type=event_object_type,
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
            existing = _create_schema(hubspot_base_url, token)
        else:
            log(
                "hubspot.schema.exists",
                level="INFO",
                portal_id=str(portal_id),
                schema_internal_name=_schema_internal_name(),
                object_type_id=str(existing.get("objectTypeId") or ""),
            )

        event_object_type = existing.get("objectTypeId")
        if not isinstance(event_object_type, str) or not event_object_type.strip():
            raise RuntimeError("Could not resolve event objectTypeId from schema response")

        set_hubspot_meta(portal_id=portal_id, key="event_object_type", value=event_object_type)

    _ensure_missing_properties(hubspot_base_url, token, event_object_type)

    order_to_event_assoc_type_id = _ensure_order_event_association_label(
        hubspot_base_url,
        token,
        portal_id,
        order_object_type,
        event_object_type,
    )

    log(
        "hubspot.events.object_ready",
        level="INFO",
        portal_id=str(portal_id),
        event_object_type=event_object_type,
        order_to_event_assoc_type_id=order_to_event_assoc_type_id,
    )

    return event_object_type, order_to_event_assoc_type_id
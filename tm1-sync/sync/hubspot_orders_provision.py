from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from db.hubspot_meta_repo import get_hubspot_meta, set_hubspot_meta
from sync.logging import log, http_body_snippet
from sync.token_manager import get_valid_access_token


def _hs_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _property_group_name() -> str:
    return "tm1_order_information"


def _property_group_label() -> str:
    return "Ticketmaster Order Information"


def _custom_properties() -> List[Dict[str, Any]]:
    """
    Custom TM1 properties that need to be created on the Order object.
    Standard hs_* properties (hs_subtotal_price, hs_tax, etc.) already exist
    on the built-in orders object and are NOT included here.
    """
    grp = _property_group_name()
    return [
        {
            "name": "tm1_unique_id",
            "label": "TM1 Unique ID",
            "type": "string",
            "fieldType": "text",
            "hasUniqueValue": True,
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_order_id",
            "label": "TM1 Order ID",
            "type": "string",
            "fieldType": "text",
            "hasUniqueValue": False,
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_type",
            "label": "TM1 Order Type",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_version",
            "label": "Version Number",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_sales_channel_group",
            "label": "Sales Channel Group",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_sales_channel_detail",
            "label": "Sales Channel Detail",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_fee",
            "label": "TM1 Fee",
            "type": "number",
            "fieldType": "number",
            "numberDisplayHint": "currency",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_deliveries",
            "label": "Delivery Fee",
            "type": "number",
            "fieldType": "number",
            "numberDisplayHint": "currency",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_upsells",
            "label": "Upsell Amount",
            "type": "number",
            "fieldType": "number",
            "numberDisplayHint": "currency",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_delivery_type",
            "label": "TM1 Delivery Type",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_payment_brand",
            "label": "TM1 Payment Brand",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_operator_code",
            "label": "Operator Code",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_transaction_datetime",
            "label": "Ticket Transaction Date/Time",
            "type": "datetime",
            "fieldType": "date",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_currency",
            "label": "Currency",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
    ]


# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------

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
            "hubspot.orders.property_group.create_failed",
            level="ERROR",
            status=r.status_code,
            body=http_body_snippet(r.text),
            object_type=object_type,
            group_name=_property_group_name(),
        )
        r.raise_for_status()

    log(
        "hubspot.orders.property_group.created",
        level="INFO",
        object_type=object_type,
        group_name=_property_group_name(),
    )


def _create_property(
    hubspot_base_url: str, token: str, object_type: str, prop: Dict[str, Any],
) -> None:
    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/properties/{object_type}"
    r = requests.post(url, headers=_hs_headers(token), json=prop, timeout=45)
    if r.status_code >= 400:
        log(
            "hubspot.orders.property.create_failed",
            level="ERROR",
            status=r.status_code,
            body=http_body_snippet(r.text),
            object_type=object_type,
            property_name=prop.get("name"),
        )
        r.raise_for_status()

    log(
        "hubspot.orders.property.created",
        level="INFO",
        object_type=object_type,
        property_name=prop.get("name"),
    )


def _ensure_missing_properties(hubspot_base_url: str, token: str, object_type: str) -> None:
    existing = _list_properties(hubspot_base_url, token, object_type)
    existing_names: Set[str] = {str(p.get("name") or "") for p in existing}

    desired = _custom_properties()
    missing = [p for p in desired if str(p.get("name") or "") not in existing_names]

    if not missing:
        log(
            "hubspot.orders.properties_ok",
            level="INFO",
            object_type=object_type,
            missing_count=0,
        )
        return

    log(
        "hubspot.orders.properties_missing",
        level="INFO",
        object_type=object_type,
        missing_count=len(missing),
        missing_names=[p["name"] for p in missing],
    )

    _ensure_property_group(hubspot_base_url, token, object_type)

    for prop in missing:
        _create_property(hubspot_base_url, token, object_type, prop)


# ---------------------------------------------------------------------------
# Association type discovery (Order → Contact)
# ---------------------------------------------------------------------------

def _discover_order_to_contact_assoc_type(
    hubspot_base_url: str,
    token: str,
    order_object_type: str,
    portal_id: str,
) -> int:
    """
    Discovers the default Order→Contact association type ID from HubSpot.
    Caches the result in hubspot_meta.
    """
    meta_key = "orders_to_contacts_assoc_type_id"
    cached = get_hubspot_meta(portal_id=portal_id, key=meta_key)
    if cached and cached.strip():
        log(
            "hubspot.orders.assoc_type_cached",
            level="INFO",
            meta_key=meta_key,
            type_id=cached,
        )
        return int(cached)

    url = f"{hubspot_base_url.rstrip('/')}/crm/v4/associations/{order_object_type}/contacts/labels"
    r = requests.get(url, headers=_hs_headers(token), timeout=45)
    r.raise_for_status()
    results = (r.json() or {}).get("results") or []

    log(
        "hubspot.orders.assoc_type_labels",
        level="INFO",
        order_object_type=order_object_type,
        results=results,
    )

    # Prefer HUBSPOT_DEFINED with no label (the default generic association)
    for item in results:
        if item.get("category") == "HUBSPOT_DEFINED" and not item.get("label"):
            type_id = int(item["typeId"])
            set_hubspot_meta(portal_id=portal_id, key=meta_key, value=str(type_id))
            log(
                "hubspot.orders.assoc_type_discovered",
                level="INFO",
                type_id=type_id,
                category="HUBSPOT_DEFINED",
                label=None,
            )
            return type_id

    # Fall back to any HUBSPOT_DEFINED (labeled ones like "Billing Contact")
    for item in results:
        if item.get("category") == "HUBSPOT_DEFINED":
            type_id = int(item["typeId"])
            set_hubspot_meta(portal_id=portal_id, key=meta_key, value=str(type_id))
            log(
                "hubspot.orders.assoc_type_discovered",
                level="INFO",
                type_id=type_id,
                category="HUBSPOT_DEFINED",
                label=item.get("label"),
            )
            return type_id

    # Fallback: any available association type
    if results:
        type_id = int(results[0]["typeId"])
        set_hubspot_meta(portal_id=portal_id, key=meta_key, value=str(type_id))
        log(
            "hubspot.orders.assoc_type_discovered",
            level="INFO",
            type_id=type_id,
            category=results[0].get("category"),
        )
        return type_id

    raise RuntimeError(
        f"No association types found between {order_object_type} and contacts. "
        "Set ORDERS_TO_CONTACTS_ASSOC_TYPE_ID env var as fallback."
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def ensure_order_properties(
    *,
    portal_id: str,
    hubspot_base_url: str,
    hubspot_client_id: str,
    hubspot_client_secret: str,
    order_object_type: str,
) -> int:
    """
    Ensures all custom TM1 properties exist on the Order object and
    discovers the Order→Contact association type ID.
    Returns the association type ID.
    """
    token = get_valid_access_token(
        portal_id=portal_id,
        hubspot_base_url=hubspot_base_url,
        client_id=hubspot_client_id,
        client_secret=hubspot_client_secret,
    )

    _ensure_missing_properties(hubspot_base_url, token, order_object_type)

    assoc_type_id = _discover_order_to_contact_assoc_type(
        hubspot_base_url, token, order_object_type, portal_id,
    )

    log(
        "hubspot.orders.provisioned",
        level="INFO",
        portal_id=portal_id,
        order_object_type=order_object_type,
        assoc_type_id=assoc_type_id,
    )

    return assoc_type_id

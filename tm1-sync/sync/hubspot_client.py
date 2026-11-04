import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests


def sha10(value: str) -> str:
    return hashlib.sha256(value.lower().encode("utf-8")).hexdigest()[:10]


def hs_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def iso_to_ms(value: Optional[str]) -> Optional[int]:
    if not value or not isinstance(value, str):
        return None
    try:
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def datetime_to_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            s = value.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
        else:
            return None

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return int(dt.timestamp() * 1000)
    except Exception:
        return None


# -------------------------
# Generic CRUD / Search
# -------------------------
def search_object_id_by_property(
    base_url: str,
    token: str,
    object_type: str,
    property_name: str,
    value: str,
) -> Optional[str]:
    url = f"{base_url.rstrip('/')}/crm/v3/objects/{object_type}/search"
    payload = {
        "filterGroups": [{"filters": [{"propertyName": property_name, "operator": "EQ", "value": value}]}],
        "properties": [property_name],
        "limit": 1,
    }
    r = requests.post(url, headers=hs_headers(token), json=payload, timeout=45)
    r.raise_for_status()
    results = (r.json() or {}).get("results", []) or []
    return results[0]["id"] if results else None


def create_object(base_url: str, token: str, object_type: str, props: Dict[str, Any]) -> str:
    url = f"{base_url.rstrip('/')}/crm/v3/objects/{object_type}"
    r = requests.post(url, headers=hs_headers(token), json={"properties": props}, timeout=45)
    r.raise_for_status()
    return (r.json() or {}).get("id")


def update_object(base_url: str, token: str, object_type: str, object_id: str, props: Dict[str, Any]) -> None:
    url = f"{base_url.rstrip('/')}/crm/v3/objects/{object_type}/{object_id}"
    r = requests.patch(url, headers=hs_headers(token), json={"properties": props}, timeout=45)
    r.raise_for_status()


def hs_create_object(base_url: str, token: str, object_type: str, props: Dict[str, Any]) -> str:
    return create_object(base_url, token, object_type, props)


def hs_update_object(base_url: str, token: str, object_type: str, object_id: str, props: Dict[str, Any]) -> None:
    return update_object(base_url, token, object_type, object_id, props)


# -------------------------
# Associations
# -------------------------
def associate_order_to_contact(
    base_url: str,
    token: str,
    order_object_type: str,
    order_id: str,
    contact_id: str,
    assoc_type_id: int,
) -> None:
    url = f"{base_url.rstrip('/')}/crm/v3/associations/{order_object_type}/contacts/batch/create"
    payload = {
        "inputs": [
            {"from": {"id": str(order_id)}, "to": {"id": str(contact_id)}, "type": str(assoc_type_id)}
        ]
    }
    r = requests.post(url, headers=hs_headers(token), json=payload, timeout=45)
    r.raise_for_status()


def associate_objects_v3_batch(
    base_url: str,
    token: str,
    from_object_type: str,
    to_object_type: str,
    from_id: str,
    to_id: str,
    assoc_type_id: int,
) -> None:
    url = f"{base_url.rstrip('/')}/crm/v3/associations/{from_object_type}/{to_object_type}/batch/create"
    payload = {
        "inputs": [
            {
                "from": {"id": str(from_id)},
                "to": {"id": str(to_id)},
                "type": str(assoc_type_id),
            }
        ]
    }
    r = requests.post(url, headers=hs_headers(token), json=payload, timeout=45)
    r.raise_for_status()


def associate_objects_v4_default_batch(
    base_url: str,
    token: str,
    from_object_type: str,
    to_object_type: str,
    from_id: str,
    to_id: str,
) -> None:
    """
    Creates a default association between two objects using HubSpot v4.
    No association type id is required.
    """
    url = f"{base_url.rstrip('/')}/crm/v4/associations/{from_object_type}/{to_object_type}/batch/associate/default"
    payload = {
        "inputs": [
            {
                "from": {"id": str(from_id)},
                "to": {"id": str(to_id)},
            }
        ]
    }
    r = requests.post(url, headers=hs_headers(token), json=payload, timeout=45)
    r.raise_for_status()


def list_associations(
    base_url: str,
    token: str,
    from_object_type: str,
    from_id: str,
    to_object_type: str,
) -> List[str]:
    """
    Returns list of HubSpot object IDs that the given object is associated to.
    Uses CRM v4 associations read endpoint.
    """
    url = f"{base_url.rstrip('/')}/crm/v4/objects/{from_object_type}/{from_id}/associations/{to_object_type}"
    r = requests.get(url, headers=hs_headers(token), timeout=45)
    r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    return [str(item["toObjectId"]) for item in results if "toObjectId" in item]


def delete_associations_batch(
    base_url: str,
    token: str,
    from_object_type: str,
    to_object_type: str,
    from_id: str,
    to_ids: List[str],
) -> None:
    """
    Deletes all associations between from_id and the given to_ids.
    """
    if not to_ids:
        return
    url = f"{base_url.rstrip('/')}/crm/v4/associations/{from_object_type}/{to_object_type}/batch/archive"
    payload = {
        "inputs": [
            {"from": {"id": str(from_id)}, "to": [{"id": str(tid)}]}
            for tid in to_ids
        ]
    }
    r = requests.post(url, headers=hs_headers(token), json=payload, timeout=45)
    r.raise_for_status()


def associate_objects_v4_labeled_batch(
    base_url: str,
    token: str,
    from_object_type: str,
    to_object_type: str,
    from_id: str,
    to_id: str,
    assoc_type_id: int,
) -> None:
    """
    Creates a labeled association between two objects using HubSpot v4 batch/create.
    Requires a specific association type id (from the labels API).
    """
    url = f"{base_url.rstrip('/')}/crm/v4/associations/{from_object_type}/{to_object_type}/batch/create"
    payload = {
        "inputs": [
            {
                "from": {"id": str(from_id)},
                "to": {"id": str(to_id)},
                "types": [
                    {
                        "associationCategory": "USER_DEFINED",
                        "associationTypeId": int(assoc_type_id),
                    }
                ],
            }
        ]
    }
    r = requests.post(url, headers=hs_headers(token), json=payload, timeout=45)
    r.raise_for_status()


# -------------------------
# Mapping from TM1 snapshot -> HS properties
# -------------------------
def build_contact_props(customer: Dict[str, Any]) -> Dict[str, Any]:
    props: Dict[str, Any] = {}

    email = (customer.get("email") or "").strip()
    if email:
        props["email"] = email

    if customer.get("firstName"):
        props["firstname"] = str(customer["firstName"]).strip()
    if customer.get("lastName"):
        props["lastname"] = str(customer["lastName"]).strip()
    if customer.get("middleName"):
        props["middle_name"] = str(customer["middleName"]).strip()
    if customer.get("prefix"):
        props["salutation"] = str(customer["prefix"]).strip()
    if customer.get("gender"):
        props["gender"] = str(customer["gender"]).strip()
    if customer.get("preferredLanguage"):
        props["hs_language"] = str(customer["preferredLanguage"]).strip()

    dob = customer.get("dateofbirth") or customer.get("dateOfBirth")
    if dob and isinstance(dob, str):
        dob_ms = iso_to_ms(dob) or iso_to_ms(dob + "T00:00:00Z")
        if dob_ms is not None:
            props["date_of_birth"] = dob_ms

    phones = customer.get("phones") or []
    if isinstance(phones, list):
        for p in phones:
            if not isinstance(p, dict):
                continue
            number = (p.get("number") or "").strip()
            phone_type = (p.get("type") or "").strip().lower()
            if not number:
                continue
            if phone_type == "mobilephone":
                props["mobilephone"] = number
            elif phone_type == "eveningphone":
                props["eveningphone"] = number
            else:
                props.setdefault("phone", number)

    consent = customer.get("marketingConsent") or {}
    if isinstance(consent, dict) and "optin" in consent:
        props["tm1_marketingconsent"] = bool(consent["optin"])

    addr = (customer.get("address") or {}) if isinstance(customer, dict) else {}
    if isinstance(addr, dict):
        line1 = addr.get("line1")
        if isinstance(line1, str) and line1.strip():
            props["address"] = line1.strip()
        line2 = addr.get("line2")
        if isinstance(line2, str) and line2.strip():
            props["address_2"] = line2.strip()
        line3 = addr.get("line3")
        if isinstance(line3, str) and line3.strip():
            props["address_3"] = line3.strip()
        city = addr.get("city")
        if isinstance(city, str) and city.strip():
            props["city"] = city.strip()
        state = addr.get("state")
        if isinstance(state, str) and state.strip():
            props["state"] = state.strip()
        country = addr.get("country")
        if isinstance(country, str) and country.strip():
            props["country"] = country.strip()
        postal = addr.get("postalCode")
        if isinstance(postal, str) and postal.strip():
            props["zip"] = postal.strip()

    return props


def build_order_props(order: Dict[str, Any], tm1_order_id: str) -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    props["tm1_unique_id"] = tm1_order_id
    # Raw TM1 order id (without creationDateTime suffix)
    try:
        props["tm1_order_id"] = tm1_order_id.split("|")[0].rsplit(".", 1)[0]
    except Exception:
        props["tm1_order_id"] = tm1_order_id

    # Build hs_order_name: "{First} {Last} - Order - {MM/DD/YYYY}"
    try:
        customer = order.get("customer") or {}
        first = (customer.get("firstName") or "").strip()
        last = (customer.get("lastName") or "").strip()
        full_name = f"{first} {last}".strip() or "Unknown"
        parts = tm1_order_id.split("|")
        date_str = parts[1].split("T")[0]           # "2026-03-02"
        yyyy, mm, dd = date_str.split("-")
        props["hs_order_name"] = f"{full_name} - Order - {mm}/{dd}/{yyyy}"
    except Exception:
        pass

    tm1_type = order.get("type")
    if isinstance(tm1_type, str) and tm1_type.strip():
        props["tm1_type"] = tm1_type.strip()

    version = order.get("version")
    if version is not None:
        props["tm1_version"] = str(version)

    creation_iso = order.get("creationDateTime") if isinstance(order.get("creationDateTime"), str) else ""
    creation_ms = iso_to_ms(creation_iso)
    if creation_ms is not None:
        props["hs_external_created_date"] = creation_ms

    sales = order.get("salesChannel", {}) or {}
    group = sales.get("group")
    if isinstance(group, str) and group.strip():
        props["tm1_sales_channel_group"] = group.strip()
    detail = sales.get("detail")
    if isinstance(detail, str) and detail.strip():
        props["tm1_sales_channel_detail"] = detail.strip()

    totals = order.get("totals", {}) or {}
    subtotal = safe_float(totals.get("price"))
    if subtotal is not None:
        props["hs_subtotal_price"] = subtotal
    tax = safe_float(totals.get("taxes"))
    if tax is not None:
        props["hs_tax"] = tax
    total_price = safe_float(totals.get("total"))
    if total_price is not None:
        props["hs_total_price"] = total_price
    fee = safe_float(totals.get("fee"))
    if fee is not None:
        props["tm1_fee"] = fee
    deliveries_amt = safe_float(totals.get("deliveries"))
    if deliveries_amt is not None:
        props["tm1_deliveries"] = deliveries_amt
    upsells_amt = safe_float(totals.get("upsells"))
    if upsells_amt is not None:
        props["tm1_upsells"] = upsells_amt

    operator = order.get("operator")
    if isinstance(operator, str) and operator.strip():
        props["tm1_operator_code"] = operator.strip()

    deliveries = order.get("deliveries", []) or []
    if isinstance(deliveries, list) and deliveries:
        d0 = deliveries[0] if isinstance(deliveries[0], dict) else {}
        d_type = d0.get("type")
        if isinstance(d_type, str) and d_type.strip():
            props["tm1_delivery_type"] = d_type.strip()

    payments = order.get("payments", []) or []
    if isinstance(payments, list) and payments:
        p0 = payments[0] if isinstance(payments[0], dict) else {}
        p_type = p0.get("type")
        if isinstance(p_type, str) and p_type.strip():
            props["hs_payment_processing_method"] = p_type.strip()
        brand = p0.get("brand")
        if isinstance(brand, str) and brand.strip():
            props["tm1_payment_brand"] = brand.strip()

    currency = totals.get("currency")
    if isinstance(currency, str) and currency.strip():
        props["tm1_currency"] = currency.strip()

    item_groups = order.get("itemGroups") or []
    for group in item_groups:
        if not isinstance(group, dict):
            continue
        embedded = group.get("_embedded") or {}
        items = group.get("items") or (embedded.get("items") if isinstance(embedded, dict) else None) or []
        for item in items:
            if not isinstance(item, dict):
                continue
            txn_dt = item.get("transactionDatetime")
            if isinstance(txn_dt, str) and txn_dt.strip():
                txn_ms = iso_to_ms(txn_dt)
                if txn_ms is not None:
                    props["tm1_transaction_datetime"] = txn_ms
                break
        if "tm1_transaction_datetime" in props:
            break

    return props


# -------------------------
# Upserts (contacts & orders)
# -------------------------
def upsert_contact_by_email(
    base_url: str,
    token: str,
    email: str,
    customer_snapshot: Dict[str, Any],
) -> Tuple[str, str]:
    contact_id = search_object_id_by_property(base_url, token, "contacts", "email", email)
    props = build_contact_props(customer_snapshot)

    if contact_id:
        update_object(base_url, token, "contacts", contact_id, props)
        return "updated", contact_id

    new_id = create_object(base_url, token, "contacts", props)
    return "created", new_id


def upsert_order_by_tm1_order_id(
    base_url: str,
    token: str,
    order_object_type: str,
    tm1_order_id: str,
    order_snapshot: Dict[str, Any],
) -> Tuple[str, str]:
    existing_id = search_object_id_by_property(base_url, token, order_object_type, "tm1_unique_id", tm1_order_id)
    props = build_order_props(order_snapshot, tm1_order_id)

    if existing_id:
        update_object(base_url, token, order_object_type, existing_id, props)
        return "updated", existing_id

    new_id = create_object(base_url, token, order_object_type, props)
    return "created", new_id


# -------------------------
# Upsert for custom objects (Events)
# -------------------------
def upsert_custom_object_by_unique_property(
    base_url: str,
    token: str,
    object_type: str,
    unique_property: str,
    unique_value: str,
    props: Dict[str, Any],
) -> Tuple[str, str]:
    existing_id = search_object_id_by_property(base_url, token, object_type, unique_property, unique_value)
    if existing_id:
        update_object(base_url, token, object_type, str(existing_id), props)
        return "updated", str(existing_id)

    new_id = create_object(base_url, token, object_type, props)
    return "created", str(new_id)
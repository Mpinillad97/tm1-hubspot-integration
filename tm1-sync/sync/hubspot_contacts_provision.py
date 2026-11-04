from typing import Any, Dict, List, Set

import requests

from sync.logging import log, http_body_snippet
from sync.token_manager import get_valid_access_token


def _hs_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _property_group_name() -> str:
    return "tm1_contact_information"


def _property_group_label() -> str:
    return "Ticketmaster Contact Information"


def _custom_properties() -> List[Dict[str, Any]]:
    """
    Custom TM1 properties that need to be created on the Contact object.
    Standard HubSpot contact properties (firstname, lastname, email, phone,
    salutation, middle_name, date_of_birth, hs_language, hs_email_optout)
    already exist and are NOT included here.
    """
    grp = _property_group_name()
    return [
        {
            "name": "gender",
            "label": "Gender",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "date_of_birth",
            "label": "Date of Birth",
            "type": "date",
            "fieldType": "date",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "address_2",
            "label": "Street Address Line 2",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "address_3",
            "label": "Street Address Line 3",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "eveningphone",
            "label": "Evening Phone",
            "type": "string",
            "fieldType": "text",
            "hidden": False,
            "formField": False,
            "groupName": grp,
        },
        {
            "name": "tm1_marketingconsent",
            "label": "TM1 Marketing Consent",
            "type": "bool",
            "fieldType": "booleancheckbox",
            "hidden": False,
            "formField": False,
            "groupName": grp,
            "options": [
                {"label": "True", "value": "true", "displayOrder": 1, "hidden": False},
                {"label": "False", "value": "false", "displayOrder": 2, "hidden": False},
            ],
        },
    ]


def _list_properties(hubspot_base_url: str, token: str) -> List[Dict[str, Any]]:
    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/properties/contacts"
    r = requests.get(url, headers=_hs_headers(token), timeout=45)
    r.raise_for_status()
    return (r.json() or {}).get("results") or []


def _list_property_groups(hubspot_base_url: str, token: str) -> List[Dict[str, Any]]:
    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/properties/contacts/groups"
    r = requests.get(url, headers=_hs_headers(token), timeout=45)
    r.raise_for_status()
    return (r.json() or {}).get("results") or []


def _ensure_property_group(hubspot_base_url: str, token: str) -> None:
    groups = _list_property_groups(hubspot_base_url, token)
    existing_names = {str(g.get("name") or "") for g in groups}

    if _property_group_name() in existing_names:
        return

    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/properties/contacts/groups"
    payload = {
        "name": _property_group_name(),
        "label": _property_group_label(),
        "displayOrder": -1,
    }
    r = requests.post(url, headers=_hs_headers(token), json=payload, timeout=45)
    if r.status_code >= 400:
        log(
            "hubspot.contacts.property_group.create_failed",
            level="ERROR",
            status=r.status_code,
            body=http_body_snippet(r.text),
            group_name=_property_group_name(),
        )
        r.raise_for_status()

    log(
        "hubspot.contacts.property_group.created",
        level="INFO",
        group_name=_property_group_name(),
    )


def _create_property(hubspot_base_url: str, token: str, prop: Dict[str, Any]) -> None:
    url = f"{hubspot_base_url.rstrip('/')}/crm/v3/properties/contacts"
    r = requests.post(url, headers=_hs_headers(token), json=prop, timeout=45)
    if r.status_code >= 400:
        log(
            "hubspot.contacts.property.create_failed",
            level="ERROR",
            status=r.status_code,
            body=http_body_snippet(r.text),
            property_name=prop.get("name"),
        )
        r.raise_for_status()

    log(
        "hubspot.contacts.property.created",
        level="INFO",
        property_name=prop.get("name"),
    )


def ensure_contact_properties(
    *,
    portal_id: str,
    hubspot_base_url: str,
    hubspot_client_id: str,
    hubspot_client_secret: str,
) -> None:
    """
    Ensures custom TM1 contact properties exist on the HubSpot portal.
    Safe to call on every run — only creates what is missing.
    """
    token = get_valid_access_token(
        portal_id=portal_id,
        hubspot_base_url=hubspot_base_url,
        client_id=hubspot_client_id,
        client_secret=hubspot_client_secret,
    )

    existing = _list_properties(hubspot_base_url, token)
    existing_names: Set[str] = {str(p.get("name") or "") for p in existing}

    desired = _custom_properties()
    missing = [p for p in desired if str(p.get("name") or "") not in existing_names]

    if not missing:
        log(
            "hubspot.contacts.properties_ok",
            level="INFO",
            portal_id=str(portal_id),
            missing_count=0,
        )
        return

    log(
        "hubspot.contacts.properties_missing",
        level="INFO",
        portal_id=str(portal_id),
        missing_count=len(missing),
        missing_names=[p["name"] for p in missing],
    )

    _ensure_property_group(hubspot_base_url, token)

    for prop in missing:
        _create_property(hubspot_base_url, token, prop)

    log(
        "hubspot.contacts.provisioned",
        level="INFO",
        portal_id=str(portal_id),
    )

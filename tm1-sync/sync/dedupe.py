from typing import Any, Dict, Optional, Tuple


def normalize_email(email: Optional[str]) -> Optional[str]:
    if not email or not isinstance(email, str):
        return None
    e = email.strip().lower()
    return e if e else None


def build_unique_order_key(order: Dict[str, Any]) -> str:
    """
    Dedupe key: {order_id}|{creationDateTime}  (full datetime, no truncation)
    """
    order_id = str(order.get("id", "")).strip()
    creation = order.get("creationDateTime")
    creation_iso = creation.strip() if isinstance(creation, str) else ""
    return f"{order_id}|{creation_iso}" if creation_iso else order_id


def should_store_customer(customer: Dict[str, Any]) -> Tuple[bool, str]:
    # Mimic tu lógica: si piiCleared == True, no guardamos customer
    if customer.get("piiCleared", False) is True:
        return (False, "skipped_piiCleared_true")

    email_norm = normalize_email(customer.get("email"))
    if not email_norm:
        return (False, "skipped_missing_email")

    return (True, "ok")
from typing import Any, Dict, List, Tuple, Optional
import time

import requests

from db.repo import (
    fetch_outbox_pending_batch,
    mark_outbox_failed,
    mark_outbox_retry,
    mark_outbox_sent,
)
from sync.backoff import compute_next_retry_at
from sync.errors import classify_http_status
from sync.hubspot_client import (
    associate_order_to_contact,
    associate_objects_v4_labeled_batch,
    datetime_to_ms,
    delete_associations_batch,
    list_associations,
    search_object_id_by_property,
    upsert_contact_by_email,
    upsert_order_by_tm1_order_id,
    upsert_custom_object_by_unique_property,
)
from db.seats_repo import get_seat_cached
from sync.hubspot_contacts_provision import ensure_contact_properties
from sync.hubspot_events_provision import ensure_event_object
from sync.hubspot_orders_provision import ensure_order_properties
from sync.hubspot_seats_provision import ensure_seat_object
from sync.logging import log, http_body_snippet
from sync.rate_limit import TokenBucket
from sync.seat_extract import build_seat_label
from sync.tm_events_repo_read import get_tm_event_cached
from sync.token_manager import get_valid_access_token

Row = Tuple[int, str, str, Dict[str, Any], int]  # (outbox_id, entity, dedupe_key, payload, attempts)


def _group_rows(rows: List[Row]) -> Dict[str, List[Row]]:
    buckets: Dict[str, List[Row]] = {}
    for r in rows:
        buckets.setdefault(r[1], []).append(r)
    return buckets


def _get_retry_after_seconds(resp: Optional[requests.Response]) -> Optional[int]:
    if resp is None:
        return None
    try:
        raw = resp.headers.get("Retry-After")
        if not raw:
            return None
        return max(1, int(raw))
    except Exception:
        return None


def _safe_str(value: Any) -> Optional[str]:
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return None


def _csv_from_list(values: Any) -> Optional[str]:
    if not isinstance(values, list):
        return None

    cleaned: List[str] = []
    seen = set()

    for item in values:
        v = str(item or "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        cleaned.append(v)

    if not cleaned:
        return None

    return ",".join(cleaned)


def _normalized_event_payload(cached: Dict[str, Any]) -> Dict[str, Any]:
    payload = cached.get("payload")
    if not isinstance(payload, dict):
        return {}

    normalized = payload.get("_normalized")
    if not isinstance(normalized, dict):
        return {}

    return normalized


def publish_once(
    *,
    client_id: str,
    portal_id: str,
    hubspot_base_url: str,
    hubspot_client_id: str,
    hubspot_client_secret: str,
    order_object_type: str,
    assoc_type_id: int,
    batch_size: int,
    rate_limit_max: int = 90,
    rate_limit_window_ms: int = 10000,
) -> Dict[str, int]:
    counts = {
        "picked": 0,
        "sent": 0,
        "retry_scheduled": 0,
        "failed": 0,
        "contacts_created": 0,
        "contacts_updated": 0,
        "orders_created": 0,
        "orders_updated": 0,
        "associations_created": 0,
        "events_created": 0,
        "events_updated": 0,
        "order_event_associations_created": 0,
        "seats_created": 0,
        "seats_updated": 0,
        "order_seat_associations_created": 0,
        "event_seat_associations_created": 0,
        "contact_seat_associations_created": 0,
    }

    rows = fetch_outbox_pending_batch(client_id, batch_size)
    counts["picked"] = len(rows)

    if not rows:
        log("publish.noop", client_id=client_id, batch_size=batch_size)
        return counts

    log(
        "publish.start",
        client_id=client_id,
        batch_size=batch_size,
        picked=len(rows),
        portal_id=str(portal_id),
        order_object_type=order_object_type,
        assoc_type_id=assoc_type_id,
        rate_limit_max=rate_limit_max,
        rate_limit_window_ms=rate_limit_window_ms,
    )

    token = get_valid_access_token(
        portal_id=portal_id,
        hubspot_base_url=hubspot_base_url,
        client_id=hubspot_client_id,
        client_secret=hubspot_client_secret,
    )

    bucket = TokenBucket(rate_limit_max, rate_limit_window_ms)

    def _take():
        bucket.take(1)

    def _handle_http_error(outbox_id: int, attempts: int, e: requests.exceptions.HTTPError, label: str):
        status = getattr(e.response, "status_code", None)
        body = None
        try:
            body = e.response.text if e.response is not None else None
        except Exception:
            body = None

        retry_after_s = _get_retry_after_seconds(getattr(e, "response", None))

        log(
            "publish.http_error.debug",
            level="ERROR",
            client_id=client_id,
            outbox_id=outbox_id,
            label=label,
            attempts=attempts,
            status=status,
            retry_after_s=retry_after_s,
            body=http_body_snippet(body) if body else None,
        )

        info = classify_http_status(status)

        if info.retryable:
            if status == 429:
                wait_s = retry_after_s if retry_after_s is not None else min(30, max(2, attempts + 2))
                log(
                    "publish.rate_limit.backoff",
                    level="WARN",
                    client_id=client_id,
                    outbox_id=outbox_id,
                    label=label,
                    attempts=attempts,
                    status=status,
                    wait_s=wait_s,
                )
                time.sleep(wait_s)

            mark_outbox_retry(
                outbox_id,
                attempts + 1,
                compute_next_retry_at(attempts + 1),
                f"{label}:http_{status}:{info.message}",
            )
            counts["retry_scheduled"] += 1
        else:
            mark_outbox_failed(outbox_id, attempts + 1, f"{label}:http_{status}:{info.message}")
            counts["failed"] += 1

    buckets = _group_rows(rows)

    # Always provision all schemas/properties upfront so they exist from the first run,
    # regardless of which entity types are in the current batch.

    try:
        ensure_contact_properties(
            portal_id=portal_id,
            hubspot_base_url=hubspot_base_url,
            hubspot_client_id=hubspot_client_id,
            hubspot_client_secret=hubspot_client_secret,
        )
    except Exception as e:
        log(
            "publish.warn.contact_provision_failed",
            level="WARN",
            portal_id=str(portal_id),
            error=str(e),
        )

    try:
        discovered_assoc_type_id = ensure_order_properties(
            portal_id=portal_id,
            hubspot_base_url=hubspot_base_url,
            hubspot_client_id=hubspot_client_id,
            hubspot_client_secret=hubspot_client_secret,
            order_object_type=order_object_type,
        )
        assoc_type_id = discovered_assoc_type_id
    except Exception as e:
        log(
            "publish.warn.order_provision_failed",
            level="WARN",
            portal_id=str(portal_id),
            error=str(e),
        )

    event_object_type: Optional[str] = None
    order_to_event_assoc_type_id: Optional[int] = None
    try:
        event_object_type, order_to_event_assoc_type_id = ensure_event_object(
            portal_id=portal_id,
            hubspot_base_url=hubspot_base_url,
            hubspot_client_id=hubspot_client_id,
            hubspot_client_secret=hubspot_client_secret,
            order_object_type=order_object_type,
        )
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        log(
            "publish.warn.event_provision_failed",
            level="WARN",
            portal_id=str(portal_id),
            status=status,
        )
        event_object_type = None
    except Exception as e:
        log(
            "publish.warn.event_provision_failed",
            level="WARN",
            portal_id=str(portal_id),
            error=str(e),
        )
        event_object_type = None

    seat_object_type: Optional[str] = None
    seat_assoc_type_ids: Dict[str, int] = {}
    try:
        seat_object_type, seat_assoc_type_ids = ensure_seat_object(
            portal_id=portal_id,
            hubspot_base_url=hubspot_base_url,
            hubspot_client_id=hubspot_client_id,
            hubspot_client_secret=hubspot_client_secret,
            order_object_type=order_object_type,
            event_object_type=event_object_type,
        )
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        log(
            "publish.warn.seat_provision_failed",
            level="WARN",
            portal_id=str(portal_id),
            status=status,
        )
        seat_object_type = None
    except Exception as e:
        log(
            "publish.warn.seat_provision_failed",
            level="WARN",
            portal_id=str(portal_id),
            error=str(e),
        )
        seat_object_type = None

    contact_id_cache: Dict[str, str] = {}
    order_id_cache: Dict[str, str] = {}
    event_id_cache: Dict[str, str] = {}
    seat_id_cache: Dict[str, str] = {}

    # 1) CONTACTS
    for outbox_id, entity, dedupe_key, payload, attempts in buckets.get("contact", []):
        try:
            customer = (payload or {}).get("customer") or {}
            email = (customer.get("email") or "").strip()
            if not email:
                mark_outbox_failed(outbox_id, attempts + 1, "missing_email_in_contact_payload")
                counts["failed"] += 1
                continue

            _take()
            res, contact_id = upsert_contact_by_email(hubspot_base_url, token, email, customer)
            contact_id_cache[email.lower()] = str(contact_id)

            if res == "created":
                counts["contacts_created"] += 1
            else:
                counts["contacts_updated"] += 1

            mark_outbox_sent(outbox_id, hubspot_object_id=str(contact_id))
            counts["sent"] += 1

        except requests.exceptions.HTTPError as e:
            _handle_http_error(outbox_id, attempts, e, "contact")
        except Exception as e:
            log(
                "publish.contact.exc",
                level="ERROR",
                client_id=client_id,
                outbox_id=outbox_id,
                err=type(e).__name__,
                detail=str(e),
            )
            mark_outbox_retry(outbox_id, attempts + 1, compute_next_retry_at(attempts + 1), "exc:contact")
            counts["retry_scheduled"] += 1

    # 2) ORDERS
    for outbox_id, entity, dedupe_key, payload, attempts in buckets.get("order", []):
        try:
            order_snapshot = (payload or {}).get("order") or {}
            tm1_order_id = dedupe_key

            _take()
            res, order_id = upsert_order_by_tm1_order_id(
                hubspot_base_url, token, order_object_type, tm1_order_id, order_snapshot
            )
            order_id_cache[tm1_order_id] = str(order_id)

            if res == "created":
                counts["orders_created"] += 1
            else:
                counts["orders_updated"] += 1

            mark_outbox_sent(outbox_id, hubspot_object_id=str(order_id))
            counts["sent"] += 1

        except requests.exceptions.HTTPError as e:
            _handle_http_error(outbox_id, attempts, e, "order")
        except Exception as e:
            log(
                "publish.order.exc",
                level="ERROR",
                client_id=client_id,
                outbox_id=outbox_id,
                err=type(e).__name__,
                detail=str(e),
            )
            mark_outbox_retry(outbox_id, attempts + 1, compute_next_retry_at(attempts + 1), "exc:order")
            counts["retry_scheduled"] += 1

    # 3) ORDER -> CONTACT
    for outbox_id, entity, dedupe_key, payload, attempts in buckets.get("association", []):
        try:
            tm1_order_id = str((payload or {}).get("tm1_order_id") or "").strip()
            email = (
                str((payload or {}).get("email") or "").strip()
                or str((payload or {}).get("email_normalized") or "").strip()
            )
            if not tm1_order_id or not email:
                mark_outbox_failed(outbox_id, attempts + 1, "missing_assoc_payload_fields")
                counts["failed"] += 1
                continue

            order_id = order_id_cache.get(tm1_order_id)
            if not order_id:
                _take()
                order_id = search_object_id_by_property(
                    hubspot_base_url, token, order_object_type, "tm1_unique_id", tm1_order_id
                )
                if order_id:
                    order_id_cache[tm1_order_id] = str(order_id)

            contact_id = contact_id_cache.get(email.lower())
            if not contact_id:
                _take()
                contact_id = search_object_id_by_property(hubspot_base_url, token, "contacts", "email", email)
                if contact_id:
                    contact_id_cache[email.lower()] = str(contact_id)

            if not order_id or not contact_id:
                log(
                    "publish.association.missing_ids",
                    level="WARN",
                    client_id=client_id,
                    outbox_id=outbox_id,
                    tm1_order_id=tm1_order_id,
                    email=email,
                    order_id=order_id,
                    contact_id=contact_id,
                )
                mark_outbox_retry(
                    outbox_id,
                    attempts + 1,
                    compute_next_retry_at(attempts + 1),
                    "assoc_missing_hubspot_ids",
                )
                counts["retry_scheduled"] += 1
                continue

            _take()
            associate_order_to_contact(
                hubspot_base_url, token, order_object_type, str(order_id), str(contact_id), assoc_type_id
            )
            mark_outbox_sent(outbox_id)
            counts["sent"] += 1
            counts["associations_created"] += 1

        except requests.exceptions.HTTPError as e:
            _handle_http_error(outbox_id, attempts, e, "association")
        except Exception as e:
            log(
                "publish.association.exc",
                level="ERROR",
                client_id=client_id,
                outbox_id=outbox_id,
                err=type(e).__name__,
                detail=str(e),
            )
            mark_outbox_retry(outbox_id, attempts + 1, compute_next_retry_at(attempts + 1), "exc:association")
            counts["retry_scheduled"] += 1

    # 4) EVENTS
    for outbox_id, entity, dedupe_key, payload, attempts in buckets.get("event", []):
        try:
            tm_event_id = (payload or {}).get("tm_event_id") or dedupe_key
            tm_event_id = str(tm_event_id).strip()
            if not tm_event_id:
                mark_outbox_failed(outbox_id, attempts + 1, "missing_tm_event_id")
                counts["failed"] += 1
                continue

            if not event_object_type:
                log(
                    "publish.event.missing_object_type",
                    level="WARN",
                    client_id=client_id,
                    outbox_id=outbox_id,
                    tm_event_id=tm_event_id,
                )
                mark_outbox_retry(
                    outbox_id,
                    attempts + 1,
                    compute_next_retry_at(attempts + 1),
                    "event_object_not_provisioned",
                )
                counts["retry_scheduled"] += 1
                continue

            cached = get_tm_event_cached(client_id=client_id, tm_event_id=tm_event_id) or {}
            normalized = _normalized_event_payload(cached)

            # Retry if Discovery data is missing (up to 5 attempts)
            if not cached.get("name") and not normalized.get("name") and attempts < 5:
                log(
                    "publish.event.retry_no_cached_data",
                    level="WARN",
                    client_id=client_id,
                    outbox_id=outbox_id,
                    tm_event_id=tm_event_id,
                    attempts=attempts,
                )
                mark_outbox_retry(
                    outbox_id,
                    attempts + 1,
                    compute_next_retry_at(attempts + 1),
                    "event_no_cached_data",
                )
                counts["retry_scheduled"] += 1
                continue

            fallback_event_name = f"TM Event {tm_event_id}"

            raw_event_name = str(
                cached.get("name")
                or normalized.get("name")
                or fallback_event_name
            )
            raw_start_dt = cached.get("start_datetime") or normalized.get("start_datetime")
            try:
                import datetime as _dt
                if isinstance(raw_start_dt, _dt.datetime):
                    dt = raw_start_dt
                else:
                    dt_str = str(raw_start_dt).replace("Z", "+00:00")
                    dt = _dt.datetime.fromisoformat(dt_str)
                mm = dt.strftime("%m")
                dd = dt.strftime("%d")
                yyyy = dt.strftime("%Y")
                hour = dt.hour
                minute = dt.strftime("%M")
                period = "am" if hour < 12 else "pm"
                hour12 = hour % 12 or 12
                display_event_name = f"{raw_event_name}: {mm}/{dd}/{yyyy} {hour12}:{minute}{period}"
            except Exception:
                display_event_name = raw_event_name

            props: Dict[str, Any] = {
                "tm_event_id": tm_event_id,
                "tm_event_name": display_event_name,
            }

            venue_name = _safe_str(cached.get("venue_name")) or _safe_str(normalized.get("venue_name"))
            city = _safe_str(cached.get("city")) or _safe_str(normalized.get("city"))
            state = _safe_str(cached.get("state")) or _safe_str(normalized.get("state"))
            product_type = _safe_str(cached.get("product_type")) or _safe_str(normalized.get("product_type"))
            subsystem_id = _safe_str(cached.get("subsystem_id")) or _safe_str(normalized.get("subsystem_id"))
            subsystem_name = _safe_str(cached.get("subsystem_name")) or _safe_str(normalized.get("subsystem_name"))
            attraction_ids_csv = _csv_from_list(cached.get("attraction_ids")) or _csv_from_list(normalized.get("attraction_ids"))
            venue_ids_csv = _csv_from_list(cached.get("venue_ids")) or _csv_from_list(normalized.get("venue_ids"))

            if venue_name:
                props["tm_event_venue_name"] = venue_name
            if city:
                props["tm_event_city"] = city
            if state:
                props["tm_event_state"] = state
            if product_type:
                props["tm_event_product_type"] = product_type
            if subsystem_id:
                props["tm_event_subsystem_id"] = subsystem_id
            if subsystem_name:
                props["tm_event_subsystem_name"] = subsystem_name
            if attraction_ids_csv:
                props["tm_event_attraction_ids"] = attraction_ids_csv
            if venue_ids_csv:
                props["tm_event_venue_ids"] = venue_ids_csv

            attraction_names_csv = _csv_from_list(cached.get("attraction_names")) or _csv_from_list(normalized.get("attraction_names"))
            country_code = _safe_str(cached.get("country_code")) or _safe_str(normalized.get("country_code"))
            event_url = _safe_str(cached.get("event_url")) or _safe_str(normalized.get("event_url"))
            tz = _safe_str(cached.get("timezone")) or _safe_str(normalized.get("timezone"))
            status_code_val = _safe_str(cached.get("status_code")) or _safe_str(normalized.get("status_code"))

            if attraction_names_csv:
                props["tm_event_attraction_names"] = attraction_names_csv
            if country_code:
                props["tm_event_country_code"] = country_code
            if event_url:
                props["tm_event_url"] = event_url
            if tz:
                props["tm_event_timezone"] = tz
            if status_code_val:
                props["tm_event_status_code"] = status_code_val

            # Calculate Show Type: Matinee (before 17:00 local) vs Evening (17:00+)
            raw_start_dt = cached.get("start_datetime") or normalized.get("start_datetime")
            if raw_start_dt and tz:
                try:
                    import datetime as _dt
                    import zoneinfo as _zi
                    if isinstance(raw_start_dt, str):
                        _start_utc = _dt.datetime.fromisoformat(raw_start_dt.replace("Z", "+00:00"))
                    else:
                        _start_utc = raw_start_dt
                    if _start_utc.tzinfo is None:
                        _start_utc = _start_utc.replace(tzinfo=_dt.timezone.utc)
                    _local_dt = _start_utc.astimezone(_zi.ZoneInfo(tz))
                    props["tm_event_show_type"] = "Matinee" if _local_dt.hour < 17 else "Evening"
                except Exception:
                    pass

            start_ms = datetime_to_ms(cached.get("start_datetime") or normalized.get("start_datetime"))
            if start_ms is not None:
                props["tm_event_start_datetime"] = start_ms

            log(
                "publish.event.debug",
                client_id=client_id,
                outbox_id=outbox_id,
                tm_event_id=tm_event_id,
                event_object_type=event_object_type,
                cached_has_row=bool(cached),
                cached_name=bool(cached.get("name") or normalized.get("name")),
                cached_start_datetime=bool(cached.get("start_datetime") or normalized.get("start_datetime")),
                cached_venue_name=bool(venue_name),
                props=props,
            )

            _take()
            res, hs_event_id = upsert_custom_object_by_unique_property(
                hubspot_base_url,
                token,
                event_object_type,
                "tm_event_id",
                tm_event_id,
                props,
            )
            event_id_cache[tm_event_id] = str(hs_event_id)

            log(
                "publish.event.result",
                client_id=client_id,
                outbox_id=outbox_id,
                tm_event_id=tm_event_id,
                result=res,
                hs_event_id=str(hs_event_id),
            )

            if res == "created":
                counts["events_created"] += 1
            else:
                counts["events_updated"] += 1

            mark_outbox_sent(outbox_id, hubspot_object_id=str(hs_event_id))
            counts["sent"] += 1

        except requests.exceptions.HTTPError as e:
            _handle_http_error(outbox_id, attempts, e, "event")
        except Exception as e:
            log(
                "publish.event.exc",
                level="ERROR",
                client_id=client_id,
                outbox_id=outbox_id,
                tm_event_id=(payload or {}).get("tm_event_id") or dedupe_key,
                err=type(e).__name__,
                detail=str(e),
            )
            mark_outbox_retry(outbox_id, attempts + 1, compute_next_retry_at(attempts + 1), "exc:event")
            counts["retry_scheduled"] += 1

    # 5) ORDER -> EVENT
    for outbox_id, entity, dedupe_key, payload, attempts in buckets.get("order_event_association", []):
        try:
            tm1_order_id = str((payload or {}).get("tm1_order_id") or "").strip()
            tm_event_id = str((payload or {}).get("tm_event_id") or "").strip()
            if not tm1_order_id or not tm_event_id:
                mark_outbox_failed(outbox_id, attempts + 1, "missing_order_event_fields")
                counts["failed"] += 1
                continue

            if not event_object_type or order_to_event_assoc_type_id is None:
                log(
                    "publish.order_event_assoc.missing_object_type",
                    level="WARN",
                    client_id=client_id,
                    outbox_id=outbox_id,
                    tm1_order_id=tm1_order_id,
                    tm_event_id=tm_event_id,
                )
                mark_outbox_retry(
                    outbox_id,
                    attempts + 1,
                    compute_next_retry_at(attempts + 1),
                    "event_object_not_provisioned",
                )
                counts["retry_scheduled"] += 1
                continue

            order_id = order_id_cache.get(tm1_order_id)
            if not order_id:
                _take()
                order_id = search_object_id_by_property(
                    hubspot_base_url, token, order_object_type, "tm1_unique_id", tm1_order_id
                )
                if order_id:
                    order_id_cache[tm1_order_id] = str(order_id)

            hs_event_id = event_id_cache.get(tm_event_id)
            if not hs_event_id:
                _take()
                hs_event_id = search_object_id_by_property(
                    hubspot_base_url, token, event_object_type, "tm_event_id", tm_event_id
                )
                if hs_event_id:
                    event_id_cache[tm_event_id] = str(hs_event_id)

            log(
                "publish.order_event_assoc.debug",
                client_id=client_id,
                outbox_id=outbox_id,
                tm1_order_id=tm1_order_id,
                tm_event_id=tm_event_id,
                order_id=order_id,
                hs_event_id=hs_event_id,
                event_object_type=event_object_type,
            )

            if not order_id or not hs_event_id:
                log(
                    "publish.order_event_assoc.missing_ids",
                    level="WARN",
                    client_id=client_id,
                    outbox_id=outbox_id,
                    tm1_order_id=tm1_order_id,
                    tm_event_id=tm_event_id,
                    order_id=order_id,
                    hs_event_id=hs_event_id,
                )
                mark_outbox_retry(
                    outbox_id,
                    attempts + 1,
                    compute_next_retry_at(attempts + 1),
                    "missing_hubspot_order_or_event_id",
                )
                counts["retry_scheduled"] += 1
                continue

            _take()
            associate_objects_v4_labeled_batch(
                hubspot_base_url,
                token,
                order_object_type,
                event_object_type,
                str(order_id),
                str(hs_event_id),
                order_to_event_assoc_type_id,
            )

            log(
                "publish.order_event_assoc.result",
                client_id=client_id,
                outbox_id=outbox_id,
                tm1_order_id=tm1_order_id,
                tm_event_id=tm_event_id,
                order_id=str(order_id),
                hs_event_id=str(hs_event_id),
            )

            mark_outbox_sent(outbox_id)
            counts["sent"] += 1
            counts["order_event_associations_created"] += 1

        except requests.exceptions.HTTPError as e:
            _handle_http_error(outbox_id, attempts, e, "order_event_association")
        except Exception as e:
            log(
                "publish.order_event_assoc.exc",
                level="ERROR",
                client_id=client_id,
                outbox_id=outbox_id,
                tm1_order_id=(payload or {}).get("tm1_order_id"),
                tm_event_id=(payload or {}).get("tm_event_id"),
                err=type(e).__name__,
                detail=str(e),
            )
            mark_outbox_retry(
                outbox_id,
                attempts + 1,
                compute_next_retry_at(attempts + 1),
                "exc:order_event_association",
            )
            counts["retry_scheduled"] += 1

    # 6) SEATS
    for outbox_id, entity, dedupe_key, payload, attempts in buckets.get("seat", []):
        try:
            seat_unique_id = dedupe_key
            if not seat_unique_id:
                mark_outbox_failed(outbox_id, attempts + 1, "missing_seat_unique_id")
                counts["failed"] += 1
                continue

            if not seat_object_type:
                log(
                    "publish.seat.missing_object_type",
                    level="WARN",
                    client_id=client_id,
                    outbox_id=outbox_id,
                    seat_unique_id=seat_unique_id,
                )
                mark_outbox_retry(
                    outbox_id,
                    attempts + 1,
                    compute_next_retry_at(attempts + 1),
                    "seat_object_not_provisioned",
                )
                counts["retry_scheduled"] += 1
                continue

            cached = get_seat_cached(client_id=client_id, seat_unique_id=seat_unique_id)
            seat_data = cached or (payload or {}).get("seat") or {}

            location = build_seat_label(seat_data)

            seat_tm_event_id = _safe_str(seat_data.get("tm_event_id"))
            attraction_name = None
            if seat_tm_event_id:
                ev_cached = get_tm_event_cached(client_id=client_id, tm_event_id=seat_tm_event_id)
                if ev_cached:
                    names = ev_cached.get("attraction_names") or []
                    if isinstance(names, list) and names:
                        attraction_name = _safe_str(names[0])

            if attraction_name:
                seat_name = f"{attraction_name} {location}"
            else:
                seat_name = location

            props: Dict[str, Any] = {
                "seat_unique_id": seat_unique_id,
                "seat_name": seat_name,
                "seat_location": location,
            }

            tm1_oid = _safe_str(seat_data.get("tm1_order_id"))
            if tm1_oid:
                props["seat_tm1_order_id"] = tm1_oid

            tm_eid = _safe_str(seat_data.get("tm_event_id"))
            if tm_eid:
                props["seat_tm_event_id"] = tm_eid

            seat_type = _safe_str(seat_data.get("type"))
            if seat_type:
                props["seat_type"] = seat_type

            sub_type = _safe_str(seat_data.get("sub_type"))
            if sub_type:
                props["seat_sub_type"] = sub_type

            seat_status = _safe_str(seat_data.get("status"))
            if seat_status:
                props["seat_status"] = seat_status

            ticket_holder = seat_data.get("ticket_holder")
            if ticket_holder is not None:
                props["seat_ticket_holder"] = str(ticket_holder)

            accessible = seat_data.get("accessible")
            if accessible is not None:
                props["seat_accessible"] = str(accessible)

            tx_dt = datetime_to_ms(seat_data.get("transaction_datetime"))
            if tx_dt is not None:
                props["seat_transaction_datetime"] = tx_dt

            gate = _safe_str(seat_data.get("gate"))
            if gate:
                props["seat_gate"] = gate

            level = _safe_str(seat_data.get("level"))
            if level:
                props["seat_level"] = level

            place_group = _safe_str(seat_data.get("place_group"))
            if place_group:
                props["seat_place_group"] = place_group

            section = _safe_str(seat_data.get("section"))
            if section:
                props["seat_section"] = section

            row = _safe_str(seat_data.get("row"))
            if row:
                props["seat_row"] = row

            seat_num = _safe_str(seat_data.get("seat"))
            if seat_num:
                props["seat_number"] = seat_num

            face_value = seat_data.get("face_value")
            if face_value is not None:
                try:
                    props["seat_face_value"] = float(face_value)
                except (ValueError, TypeError):
                    pass

            currency = _safe_str(seat_data.get("currency"))
            if currency:
                props["seat_currency"] = currency

            price_type = _safe_str(seat_data.get("price_type"))
            if price_type:
                props["seat_price_type"] = price_type

            price_level = _safe_str(seat_data.get("price_level"))
            if price_level:
                props["seat_price_level"] = price_level

            offers = seat_data.get("offers")
            offers_csv = _csv_from_list(offers) if isinstance(offers, list) else None
            if offers_csv:
                props["seat_offers"] = offers_csv

            is_upsell = seat_data.get("is_upsell")
            if is_upsell is not None:
                props["seat_is_upsell"] = str(is_upsell)

            log(
                "publish.seat.debug",
                client_id=client_id,
                outbox_id=outbox_id,
                seat_unique_id=seat_unique_id,
                seat_object_type=seat_object_type,
                has_cached=bool(cached),
                props=props,
            )

            _take()
            res, hs_seat_id = upsert_custom_object_by_unique_property(
                hubspot_base_url,
                token,
                seat_object_type,
                "seat_unique_id",
                seat_unique_id,
                props,
            )
            seat_id_cache[seat_unique_id] = str(hs_seat_id)

            log(
                "publish.seat.result",
                client_id=client_id,
                outbox_id=outbox_id,
                seat_unique_id=seat_unique_id,
                result=res,
                hs_seat_id=str(hs_seat_id),
            )

            if res == "created":
                counts["seats_created"] += 1
            else:
                counts["seats_updated"] += 1

            mark_outbox_sent(outbox_id, hubspot_object_id=str(hs_seat_id))
            counts["sent"] += 1

        except requests.exceptions.HTTPError as e:
            _handle_http_error(outbox_id, attempts, e, "seat")
        except Exception as e:
            log(
                "publish.seat.exc",
                level="ERROR",
                client_id=client_id,
                outbox_id=outbox_id,
                seat_unique_id=dedupe_key,
                err=type(e).__name__,
                detail=str(e),
            )
            mark_outbox_retry(outbox_id, attempts + 1, compute_next_retry_at(attempts + 1), "exc:seat")
            counts["retry_scheduled"] += 1

    # 7) ORDER -> SEAT
    for outbox_id, entity, dedupe_key, payload, attempts in buckets.get("order_seat_association", []):
        try:
            tm1_order_id = str((payload or {}).get("tm1_order_id") or "").strip()
            seat_unique_id = str((payload or {}).get("seat_unique_id") or "").strip()
            if not tm1_order_id or not seat_unique_id:
                mark_outbox_failed(outbox_id, attempts + 1, "missing_order_seat_fields")
                counts["failed"] += 1
                continue

            if not seat_object_type:
                mark_outbox_retry(
                    outbox_id, attempts + 1, compute_next_retry_at(attempts + 1),
                    "seat_object_not_provisioned",
                )
                counts["retry_scheduled"] += 1
                continue

            order_id = order_id_cache.get(tm1_order_id)
            if not order_id:
                _take()
                order_id = search_object_id_by_property(
                    hubspot_base_url, token, order_object_type, "tm1_unique_id", tm1_order_id
                )
                if order_id:
                    order_id_cache[tm1_order_id] = str(order_id)

            hs_seat_id = seat_id_cache.get(seat_unique_id)
            if not hs_seat_id:
                _take()
                hs_seat_id = search_object_id_by_property(
                    hubspot_base_url, token, seat_object_type, "seat_unique_id", seat_unique_id
                )
                if hs_seat_id:
                    seat_id_cache[seat_unique_id] = str(hs_seat_id)

            if not order_id or not hs_seat_id:
                log(
                    "publish.order_seat_assoc.missing_ids",
                    level="WARN",
                    client_id=client_id,
                    outbox_id=outbox_id,
                    tm1_order_id=tm1_order_id,
                    seat_unique_id=seat_unique_id,
                    order_id=order_id,
                    hs_seat_id=hs_seat_id,
                )
                mark_outbox_retry(
                    outbox_id, attempts + 1, compute_next_retry_at(attempts + 1),
                    "missing_hubspot_order_or_seat_id",
                )
                counts["retry_scheduled"] += 1
                continue

            assoc_type_id = seat_assoc_type_ids.get("order_to_seat")
            if not assoc_type_id:
                mark_outbox_retry(
                    outbox_id, attempts + 1, compute_next_retry_at(attempts + 1),
                    "missing_order_to_seat_assoc_type_id",
                )
                counts["retry_scheduled"] += 1
                continue

            _take()
            associate_objects_v4_labeled_batch(
                hubspot_base_url, token,
                order_object_type, seat_object_type,
                str(order_id), str(hs_seat_id),
                assoc_type_id,
            )

            mark_outbox_sent(outbox_id)
            counts["sent"] += 1
            counts["order_seat_associations_created"] += 1

        except requests.exceptions.HTTPError as e:
            _handle_http_error(outbox_id, attempts, e, "order_seat_association")
        except Exception as e:
            log(
                "publish.order_seat_assoc.exc",
                level="ERROR",
                client_id=client_id,
                outbox_id=outbox_id,
                err=type(e).__name__,
                detail=str(e),
            )
            mark_outbox_retry(outbox_id, attempts + 1, compute_next_retry_at(attempts + 1), "exc:order_seat_assoc")
            counts["retry_scheduled"] += 1

    # 8) EVENT -> SEAT
    for outbox_id, entity, dedupe_key, payload, attempts in buckets.get("event_seat_association", []):
        try:
            seat_unique_id = str((payload or {}).get("seat_unique_id") or "").strip()
            tm_event_id = str((payload or {}).get("tm_event_id") or "").strip()
            if not seat_unique_id or not tm_event_id:
                mark_outbox_failed(outbox_id, attempts + 1, "missing_event_seat_fields")
                counts["failed"] += 1
                continue

            if not seat_object_type or not event_object_type:
                mark_outbox_retry(
                    outbox_id, attempts + 1, compute_next_retry_at(attempts + 1),
                    "seat_or_event_object_not_provisioned",
                )
                counts["retry_scheduled"] += 1
                continue

            hs_event_id = event_id_cache.get(tm_event_id)
            if not hs_event_id:
                _take()
                hs_event_id = search_object_id_by_property(
                    hubspot_base_url, token, event_object_type, "tm_event_id", tm_event_id
                )
                if hs_event_id:
                    event_id_cache[tm_event_id] = str(hs_event_id)

            hs_seat_id = seat_id_cache.get(seat_unique_id)
            if not hs_seat_id:
                _take()
                hs_seat_id = search_object_id_by_property(
                    hubspot_base_url, token, seat_object_type, "seat_unique_id", seat_unique_id
                )
                if hs_seat_id:
                    seat_id_cache[seat_unique_id] = str(hs_seat_id)

            if not hs_event_id or not hs_seat_id:
                log(
                    "publish.event_seat_assoc.missing_ids",
                    level="WARN",
                    client_id=client_id,
                    outbox_id=outbox_id,
                    tm_event_id=tm_event_id,
                    seat_unique_id=seat_unique_id,
                    hs_event_id=hs_event_id,
                    hs_seat_id=hs_seat_id,
                )
                mark_outbox_retry(
                    outbox_id, attempts + 1, compute_next_retry_at(attempts + 1),
                    "missing_hubspot_event_or_seat_id",
                )
                counts["retry_scheduled"] += 1
                continue

            assoc_type_id = seat_assoc_type_ids.get("event_to_seat")
            if not assoc_type_id:
                mark_outbox_retry(
                    outbox_id, attempts + 1, compute_next_retry_at(attempts + 1),
                    "missing_event_to_seat_assoc_type_id",
                )
                counts["retry_scheduled"] += 1
                continue

            # Remove stale event associations before creating the correct one
            _take()
            existing_event_ids = list_associations(
                hubspot_base_url, token,
                seat_object_type, str(hs_seat_id),
                event_object_type,
            )
            stale_event_ids = [eid for eid in existing_event_ids if eid != str(hs_event_id)]
            if stale_event_ids:
                log(
                    "publish.event_seat_assoc.removing_stale",
                    client_id=client_id,
                    outbox_id=outbox_id,
                    seat_unique_id=seat_unique_id,
                    stale_count=len(stale_event_ids),
                )
                _take()
                delete_associations_batch(
                    hubspot_base_url, token,
                    seat_object_type, event_object_type,
                    str(hs_seat_id), stale_event_ids,
                )

            _take()
            associate_objects_v4_labeled_batch(
                hubspot_base_url, token,
                event_object_type, seat_object_type,
                str(hs_event_id), str(hs_seat_id),
                assoc_type_id,
            )

            mark_outbox_sent(outbox_id)
            counts["sent"] += 1
            counts["event_seat_associations_created"] += 1

        except requests.exceptions.HTTPError as e:
            _handle_http_error(outbox_id, attempts, e, "event_seat_association")
        except Exception as e:
            log(
                "publish.event_seat_assoc.exc",
                level="ERROR",
                client_id=client_id,
                outbox_id=outbox_id,
                err=type(e).__name__,
                detail=str(e),
            )
            mark_outbox_retry(outbox_id, attempts + 1, compute_next_retry_at(attempts + 1), "exc:event_seat_assoc")
            counts["retry_scheduled"] += 1

    # 9) CONTACT -> SEAT
    for outbox_id, entity, dedupe_key, payload, attempts in buckets.get("contact_seat_association", []):
        try:
            seat_unique_id = str((payload or {}).get("seat_unique_id") or "").strip()
            email_norm = str((payload or {}).get("email_normalized") or "").strip()
            if not seat_unique_id or not email_norm:
                mark_outbox_failed(outbox_id, attempts + 1, "missing_contact_seat_fields")
                counts["failed"] += 1
                continue

            if not seat_object_type:
                mark_outbox_retry(
                    outbox_id, attempts + 1, compute_next_retry_at(attempts + 1),
                    "seat_object_not_provisioned",
                )
                counts["retry_scheduled"] += 1
                continue

            contact_id = contact_id_cache.get(email_norm.lower())
            if not contact_id:
                _take()
                contact_id = search_object_id_by_property(
                    hubspot_base_url, token, "contacts", "email", email_norm
                )
                if contact_id:
                    contact_id_cache[email_norm.lower()] = str(contact_id)

            hs_seat_id = seat_id_cache.get(seat_unique_id)
            if not hs_seat_id:
                _take()
                hs_seat_id = search_object_id_by_property(
                    hubspot_base_url, token, seat_object_type, "seat_unique_id", seat_unique_id
                )
                if hs_seat_id:
                    seat_id_cache[seat_unique_id] = str(hs_seat_id)

            if not contact_id or not hs_seat_id:
                log(
                    "publish.contact_seat_assoc.missing_ids",
                    level="WARN",
                    client_id=client_id,
                    outbox_id=outbox_id,
                    email_norm=email_norm,
                    seat_unique_id=seat_unique_id,
                    contact_id=contact_id,
                    hs_seat_id=hs_seat_id,
                )
                mark_outbox_retry(
                    outbox_id, attempts + 1, compute_next_retry_at(attempts + 1),
                    "missing_hubspot_contact_or_seat_id",
                )
                counts["retry_scheduled"] += 1
                continue

            assoc_type_id = seat_assoc_type_ids.get("contact_to_seat")
            if not assoc_type_id:
                mark_outbox_retry(
                    outbox_id, attempts + 1, compute_next_retry_at(attempts + 1),
                    "missing_contact_to_seat_assoc_type_id",
                )
                counts["retry_scheduled"] += 1
                continue

            _take()
            associate_objects_v4_labeled_batch(
                hubspot_base_url, token,
                "contacts", seat_object_type,
                str(contact_id), str(hs_seat_id),
                assoc_type_id,
            )

            mark_outbox_sent(outbox_id)
            counts["sent"] += 1
            counts["contact_seat_associations_created"] += 1

        except requests.exceptions.HTTPError as e:
            _handle_http_error(outbox_id, attempts, e, "contact_seat_association")
        except Exception as e:
            log(
                "publish.contact_seat_assoc.exc",
                level="ERROR",
                client_id=client_id,
                outbox_id=outbox_id,
                err=type(e).__name__,
                detail=str(e),
            )
            mark_outbox_retry(outbox_id, attempts + 1, compute_next_retry_at(attempts + 1), "exc:contact_seat_assoc")
            counts["retry_scheduled"] += 1

    log("publish.done", client_id=client_id, **counts)
    return counts
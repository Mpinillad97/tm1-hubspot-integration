from typing import Any, Dict, List, Optional

from db.events_repo import fetch_event_ids_to_refresh, upsert_tm_event
from sync.discovery_client import fetch_event_details
from sync.logging import log, log_item


def _get_nested(obj: Dict[str, Any], path: str) -> Optional[Any]:
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _extract_ids_from_links(discovery_payload: Dict[str, Any], key: str) -> List[str]:
    out: List[str] = []

    links = discovery_payload.get("_links")
    if not isinstance(links, dict):
        return out

    items = links.get(key)
    if not isinstance(items, list):
        return out

    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        href = item.get("href")
        if not isinstance(href, str) or not href.strip():
            continue

        raw = href.split("?")[0].rstrip("/")
        parts = raw.split("/")
        if not parts:
            continue

        maybe_id = parts[-1].strip()
        if maybe_id and maybe_id not in seen:
            seen.add(maybe_id)
            out.append(maybe_id)

    return out


def _extract_embedded_ids(discovery_payload: Dict[str, Any], key: str) -> List[str]:
    out: List[str] = []

    embedded = discovery_payload.get("_embedded")
    if not isinstance(embedded, dict):
        return out

    items = embedded.get(key)
    if not isinstance(items, list):
        return out

    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if isinstance(item_id, str):
            item_id = item_id.strip()
            if item_id and item_id not in seen:
                seen.add(item_id)
                out.append(item_id)

    return out


def _extract_embedded_names(discovery_payload: Dict[str, Any], key: str) -> List[str]:
    out: List[str] = []

    embedded = discovery_payload.get("_embedded")
    if not isinstance(embedded, dict):
        return out

    items = embedded.get(key)
    if not isinstance(items, list):
        return out

    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str):
            name = name.strip()
            if name and name not in seen:
                seen.add(name)
                out.append(name)

    return out


def _merge_unique(values_a: List[str], values_b: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()

    for value in (values_a or []) + (values_b or []):
        v = str(value or "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)

    return out


def _extract_fields(discovery_payload: Dict[str, Any]) -> Dict[str, Any]:
    discovery_event_id = discovery_payload.get("id") if isinstance(discovery_payload.get("id"), str) else None
    name = discovery_payload.get("name") if isinstance(discovery_payload.get("name"), str) else None
    event_type = discovery_payload.get("type") if isinstance(discovery_payload.get("type"), str) else None
    locale = discovery_payload.get("locale") if isinstance(discovery_payload.get("locale"), str) else None
    event_url = discovery_payload.get("url") if isinstance(discovery_payload.get("url"), str) else None
    product_type = discovery_payload.get("productType") if isinstance(discovery_payload.get("productType"), str) else None
    please_note = discovery_payload.get("pleaseNote") if isinstance(discovery_payload.get("pleaseNote"), str) else None
    info = discovery_payload.get("info") if isinstance(discovery_payload.get("info"), str) else None

    start_dt = _get_nested(discovery_payload, "dates.start.dateTime")
    start_dt = start_dt if isinstance(start_dt, str) else None

    local_date = _get_nested(discovery_payload, "dates.start.localDate")
    local_date = local_date if isinstance(local_date, str) else None

    local_time = _get_nested(discovery_payload, "dates.start.localTime")
    local_time = local_time if isinstance(local_time, str) else None

    timezone = _get_nested(discovery_payload, "dates.timezone")
    timezone = timezone if isinstance(timezone, str) else None

    status_code = _get_nested(discovery_payload, "dates.status.code")
    status_code = status_code if isinstance(status_code, str) else None

    sales_public_start_datetime = _get_nested(discovery_payload, "sales.public.startDateTime")
    sales_public_start_datetime = sales_public_start_datetime if isinstance(sales_public_start_datetime, str) else None

    sales_public_end_datetime = _get_nested(discovery_payload, "sales.public.endDateTime")
    sales_public_end_datetime = sales_public_end_datetime if isinstance(sales_public_end_datetime, str) else None

    venue_id = None
    venue_name = None
    city = None
    state = None
    country_code = None

    embedded = discovery_payload.get("_embedded")
    if isinstance(embedded, dict):
        venues = embedded.get("venues")
        if isinstance(venues, list) and venues:
            v0 = venues[0] if isinstance(venues[0], dict) else None
            if isinstance(v0, dict):
                venue_id = v0.get("id") if isinstance(v0.get("id"), str) else None
                venue_name = v0.get("name") if isinstance(v0.get("name"), str) else None

                vcity = v0.get("city")
                if isinstance(vcity, dict):
                    city = vcity.get("name") if isinstance(vcity.get("name"), str) else None

                vstate = v0.get("state")
                if isinstance(vstate, dict):
                    state = vstate.get("stateCode") if isinstance(vstate.get("stateCode"), str) else None

                vcountry = v0.get("country")
                if isinstance(vcountry, dict):
                    country_code = vcountry.get("countryCode") if isinstance(vcountry.get("countryCode"), str) else None

    attraction_ids = _merge_unique(
        _extract_embedded_ids(discovery_payload, "attractions"),
        _extract_ids_from_links(discovery_payload, "attractions"),
    )
    venue_ids = _merge_unique(
        _extract_embedded_ids(discovery_payload, "venues"),
        _extract_ids_from_links(discovery_payload, "venues"),
    )
    attraction_names = _extract_embedded_names(discovery_payload, "attractions")

    subsystem_obj = discovery_payload.get("subsystem")
    subsystem_id = None
    subsystem_name = None
    if isinstance(subsystem_obj, dict):
        subsystem_id = subsystem_obj.get("id") if isinstance(subsystem_obj.get("id"), str) else None
        subsystem_name = subsystem_obj.get("name") if isinstance(subsystem_obj.get("name"), str) else None

    return {
        "discovery_event_id": discovery_event_id,
        "name": name,
        "event_type": event_type,
        "locale": locale,
        "event_url": event_url,
        "start_datetime": start_dt,
        "local_date": local_date,
        "local_time": local_time,
        "timezone": timezone,
        "status_code": status_code,
        "sales_public_start_datetime": sales_public_start_datetime,
        "sales_public_end_datetime": sales_public_end_datetime,
        "venue_id": venue_id,
        "venue_name": venue_name,
        "city": city,
        "state": state,
        "country_code": country_code,
        "product_type": product_type,
        "attraction_ids": attraction_ids,
        "attraction_names": attraction_names,
        "venue_ids": venue_ids,
        "subsystem_id": subsystem_id,
        "subsystem_name": subsystem_name,
        "please_note": please_note,
        "info": info,
    }


def _build_enriched_payload(discovery_payload: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(discovery_payload)

    out["_normalized"] = {
        "discovery_event_id": fields.get("discovery_event_id"),
        "name": fields.get("name"),
        "event_type": fields.get("event_type"),
        "locale": fields.get("locale"),
        "event_url": fields.get("event_url"),
        "start_datetime": fields.get("start_datetime"),
        "local_date": fields.get("local_date"),
        "local_time": fields.get("local_time"),
        "timezone": fields.get("timezone"),
        "status_code": fields.get("status_code"),
        "sales_public_start_datetime": fields.get("sales_public_start_datetime"),
        "sales_public_end_datetime": fields.get("sales_public_end_datetime"),
        "venue_id": fields.get("venue_id"),
        "venue_name": fields.get("venue_name"),
        "city": fields.get("city"),
        "state": fields.get("state"),
        "country_code": fields.get("country_code"),
        "product_type": fields.get("product_type"),
        "attraction_ids": fields.get("attraction_ids") or [],
        "attraction_names": fields.get("attraction_names") or [],
        "venue_ids": fields.get("venue_ids") or [],
        "subsystem_id": fields.get("subsystem_id"),
        "subsystem_name": fields.get("subsystem_name"),
        "please_note": fields.get("please_note"),
        "info": fields.get("info"),
    }

    return out


def fetch_events_once(
    *,
    client_id: str,
    discovery_base_url: str,
    discovery_api_key: str,
    cache_ttl_hours: int,
    max_per_run: int,
    view_internal: bool,
    timeout_seconds: int,
    max_retries: int,
    rps_limit: int,
) -> Dict[str, int]:
    counts = {
        "candidates": 0,
        "fetched_ok": 0,
        "not_found": 0,
        "errors": 0,
    }

    event_ids = fetch_event_ids_to_refresh(
        client_id=client_id,
        ttl_hours=cache_ttl_hours,
        limit=max_per_run,
    )
    counts["candidates"] = len(event_ids)

    if not event_ids:
        log("events_fetch.noop", client_id=client_id, ttl_hours=cache_ttl_hours, max_per_run=max_per_run)
        return counts

    log(
        "events_fetch.start",
        client_id=client_id,
        ttl_hours=cache_ttl_hours,
        max_per_run=max_per_run,
        candidates=len(event_ids),
        event_ids_preview=event_ids[:10],
    )

    for idx, event_id in enumerate(event_ids):
        log(
            "events_fetch.item.request",
            client_id=client_id,
            idx=idx,
            tm_event_id=event_id,
            include_inactive=True,
            include_past=True,
            view_internal=view_internal,
        )

        status, payload = fetch_event_details(
            event_id=event_id,
            discovery_base_url=discovery_base_url,
            discovery_api_key=discovery_api_key,
            include_inactive=True,
            include_past=True,
            view_internal=view_internal,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            rps_limit=rps_limit,
        )

        if status == "ok" and payload:
            fields = _extract_fields(payload)
            enriched_payload = _build_enriched_payload(payload, fields)

            upsert_tm_event(
                client_id=client_id,
                tm_event_id=event_id,
                payload=enriched_payload,
                discovery_event_id=fields["discovery_event_id"],
                name=fields["name"],
                event_type=fields["event_type"],
                locale=fields["locale"],
                event_url=fields["event_url"],
                start_datetime_iso=fields["start_datetime"],
                local_date=fields["local_date"],
                local_time=fields["local_time"],
                timezone=fields["timezone"],
                status_code=fields["status_code"],
                sales_public_start_datetime_iso=fields["sales_public_start_datetime"],
                sales_public_end_datetime_iso=fields["sales_public_end_datetime"],
                venue_id=fields["venue_id"],
                venue_ids=fields["venue_ids"],
                venue_name=fields["venue_name"],
                city=fields["city"],
                state=fields["state"],
                country_code=fields["country_code"],
                attraction_ids=fields["attraction_ids"],
                attraction_names=fields["attraction_names"],
                product_type=fields["product_type"],
                subsystem_id=fields["subsystem_id"],
                subsystem_name=fields["subsystem_name"],
                please_note=fields["please_note"],
                info=fields["info"],
            )
            counts["fetched_ok"] += 1

            log_item(
                "events_fetch.item.ok",
                idx=idx,
                tm_event_id=event_id,
                discovery_event_id=fields["discovery_event_id"],
                name=fields["name"],
                start_datetime=fields["start_datetime"],
                venue=fields["venue_name"],
                city=fields["city"],
                state=fields["state"],
                product_type=fields["product_type"],
                attraction_ids=fields["attraction_ids"],
                attraction_names=fields["attraction_names"],
                venue_ids=fields["venue_ids"],
                subsystem_id=fields["subsystem_id"],
                subsystem_name=fields["subsystem_name"],
            )

        elif status == "not_found":
            upsert_tm_event(
                client_id=client_id,
                tm_event_id=event_id,
                payload=None,
                discovery_event_id=None,
                name=None,
                event_type=None,
                locale=None,
                event_url=None,
                start_datetime_iso=None,
                local_date=None,
                local_time=None,
                timezone=None,
                status_code=None,
                sales_public_start_datetime_iso=None,
                sales_public_end_datetime_iso=None,
                venue_id=None,
                venue_ids=[],
                venue_name=None,
                city=None,
                state=None,
                country_code=None,
                attraction_ids=[],
                attraction_names=[],
                product_type=None,
                subsystem_id=None,
                subsystem_name=None,
                please_note=None,
                info=None,
            )
            counts["not_found"] += 1
            log("events_fetch.not_found", level="WARN", client_id=client_id, tm_event_id=event_id)

        else:
            counts["errors"] += 1
            log("events_fetch.error", level="ERROR", client_id=client_id, tm_event_id=event_id)

    log("events_fetch.done", client_id=client_id, **counts)
    return counts
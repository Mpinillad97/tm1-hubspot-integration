"""
fetch_past_buyers.py
--------------------
Fetches ALL ticket buyers from TM1 for events that have ALREADY happened
(event date < today), enriches the data with event details from the
Ticketmaster Discovery API, and writes a CSV.

Usage:
    python fetch_past_buyers.py [--since 2020-01-01] [--output output.csv]

Environment variables (set in tools/.env or export manually):
    TM1_API_KEY       - Required. TM1 Reports API key.
    DISCOVERY_API_KEY - Required. Ticketmaster Discovery API key.
    TM1_BASE_URL      - Optional. Default: https://app.ticketmaster.com/tm1-reports-api/v1
    DISCOVERY_BASE_URL- Optional. Default: https://app.ticketmaster.com/discovery/v2

Requirements:
    pip install requests python-dotenv
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from the tools/ folder (same directory as this script)
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
load_dotenv(_HERE / ".env")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _require(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        print(f"ERROR: Missing required env var: {name}", file=sys.stderr)
        sys.exit(1)
    return v


def _optional(name: str, default: str) -> str:
    v = os.getenv(name, "").strip()
    return v if v else default


TM1_BASE_URL = _optional("TM1_BASE_URL", "https://app.ticketmaster.com/tm1-reports-api/v1")
DISCOVERY_BASE_URL = _optional("DISCOVERY_BASE_URL", "https://app.ticketmaster.com/discovery/v2")
RETRYABLE = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# TM1 API helpers
# ---------------------------------------------------------------------------
def tm1_fetch_page(api_key: str, since: str, page: int, size: int = 200) -> Dict[str, Any]:
    url = f"{TM1_BASE_URL.rstrip('/')}/orders"
    params = {"apikey": api_key, "since": since, "page": page, "size": size}

    for attempt in range(1, 6):
        try:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code in RETRYABLE and attempt < 5:
                wait = min(30, 2 ** (attempt - 1))
                print(f"  [TM1] HTTP {r.status_code} on page {page}, retry {attempt} in {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            if attempt < 5:
                wait = min(30, 2 ** (attempt - 1))
                print(f"  [TM1] Timeout on page {page}, retry {attempt} in {wait}s...")
                time.sleep(wait)
                continue
            raise
    return {}


def tm1_fetch_all_orders(api_key: str, since: str) -> List[Dict[str, Any]]:
    """Pages through all TM1 orders since `since` and returns them as a flat list."""
    all_orders: List[Dict[str, Any]] = []
    page = 0
    size = 200

    print(f"[TM1] Fetching orders since {since} ...")

    while True:
        payload = tm1_fetch_page(api_key, since, page, size)
        orders = (payload.get("_embedded") or {}).get("orders") or []

        if not orders:
            print(f"  [TM1] Page {page}: 0 orders — done.")
            break

        all_orders.extend(orders)
        print(f"  [TM1] Page {page}: {len(orders)} orders (total so far: {len(all_orders)})")

        # Check pagination info
        page_info = payload.get("page") or {}
        total_pages = page_info.get("totalPages")
        current = page_info.get("number", page)

        if isinstance(total_pages, int) and current >= total_pages - 1:
            break

        page += 1

    print(f"[TM1] Total orders fetched: {len(all_orders)}")
    return all_orders


# ---------------------------------------------------------------------------
# Extract event IDs from an order
# ---------------------------------------------------------------------------
def extract_event_ids(order: Dict[str, Any]) -> List[str]:
    item_groups = order.get("itemGroups") or []
    seen: Set[str] = set()
    out: List[str] = []
    for g in item_groups:
        if not isinstance(g, dict):
            continue
        ev = g.get("event") or {}
        if not isinstance(ev, dict):
            continue
        eid = ev.get("id")
        if isinstance(eid, str) and eid.strip() and eid.strip() not in seen:
            seen.add(eid.strip())
            out.append(eid.strip())
    return out


# ---------------------------------------------------------------------------
# Discovery API helpers
# ---------------------------------------------------------------------------
_event_cache: Dict[str, Optional[Dict[str, Any]]] = {}


def discovery_fetch_event(api_key: str, event_id: str) -> Optional[Dict[str, Any]]:
    """Fetches event details from Discovery API. Returns None if not found."""
    if event_id in _event_cache:
        return _event_cache[event_id]

    url = f"{DISCOVERY_BASE_URL.rstrip('/')}/events/{event_id}.json"
    params = {"apikey": api_key, "includeInactive": "yes", "includePast": "yes"}

    for attempt in range(1, 5):
        try:
            # Respect ~5 RPS Discovery rate limit
            time.sleep(0.21)
            r = requests.get(url, params=params, timeout=30)

            if r.status_code == 200:
                data = r.json()
                _event_cache[event_id] = data
                return data

            if r.status_code == 404:
                _event_cache[event_id] = None
                return None

            if r.status_code in RETRYABLE and attempt < 4:
                wait = min(30, 2 ** (attempt - 1))
                print(f"  [Discovery] HTTP {r.status_code} for event {event_id}, retry {attempt} in {wait}s...")
                time.sleep(wait)
                continue

            print(f"  [Discovery] HTTP {r.status_code} for event {event_id} — skipping.")
            _event_cache[event_id] = None
            return None

        except Exception as e:
            if attempt < 4:
                time.sleep(2 ** (attempt - 1))
                continue
            print(f"  [Discovery] Exception for event {event_id}: {e} — skipping.")
            _event_cache[event_id] = None
            return None

    _event_cache[event_id] = None
    return None


def parse_event_date(event_data: Dict[str, Any]) -> Optional[datetime]:
    """
    Parses the event start date from Discovery API response.
    Returns a timezone-aware datetime or None.
    """
    dates = event_data.get("dates") or {}
    start = dates.get("start") or {}

    # Try dateTime first (most precise), then localDate
    dt_str = start.get("dateTime")
    if dt_str:
        try:
            # ISO 8601 format like "2024-06-15T19:00:00Z"
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except ValueError:
            pass

    local_date = start.get("localDate")
    if local_date:
        try:
            # "2024-06-15" — treat as midnight UTC
            return datetime.fromisoformat(local_date).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return None


def extract_event_info(event_data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Extracts key fields from a Discovery API event payload."""
    if not event_data:
        return {
            "event_name": "",
            "event_date": "",
            "event_venue_name": "",
            "event_venue_city": "",
            "event_venue_state": "",
            "event_venue_country": "",
        }

    name = event_data.get("name") or ""

    dates = event_data.get("dates") or {}
    start = dates.get("start") or {}
    event_date = start.get("localDate") or start.get("dateTime") or ""

    venues = ((event_data.get("_embedded") or {}).get("venues") or [])
    venue = venues[0] if venues else {}
    venue_name = venue.get("name") or ""
    city = (venue.get("city") or {}).get("name") or ""
    state = (venue.get("state") or {}).get("name") or ""
    country = (venue.get("country") or {}).get("name") or ""

    return {
        "event_name": name,
        "event_date": event_date,
        "event_venue_name": venue_name,
        "event_venue_city": city,
        "event_venue_state": state,
        "event_venue_country": country,
    }


# ---------------------------------------------------------------------------
# Extract seat/ticket rows from a single order
# ---------------------------------------------------------------------------
def extract_seat_rows(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Returns one dict per seat/item in the order.
    Each dict contains all seat-level fields.
    """
    rows: List[Dict[str, Any]] = []
    item_groups = order.get("itemGroups") or []

    for g_idx, group in enumerate(item_groups):
        if not isinstance(group, dict):
            continue

        ev = group.get("event") or {}
        tm_event_id = ev.get("id", "").strip() if isinstance(ev, dict) else ""
        is_upsell = bool(group.get("upsell", False))

        # TM1 HAL format: items may be under _embedded.items or items directly
        embedded = group.get("_embedded") or {}
        items = group.get("items") or (embedded.get("items") if isinstance(embedded, dict) else None) or []

        for i_idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue

            location = item.get("location") or {}
            price = item.get("price") or {}
            offers = item.get("offers") or []
            offer_types = [o.get("type", "") for o in offers if isinstance(o, dict)]

            rows.append({
                "seat_unique_id": f"{order.get('id', '')}|{g_idx}|{i_idx}",
                "tm_event_id": tm_event_id,
                "seat_type": item.get("type", ""),
                "seat_sub_type": item.get("subType", ""),
                "seat_status": item.get("status", ""),
                "ticket_holder": item.get("ticketHolder", ""),
                "transaction_datetime": item.get("transactionDatetime", ""),
                "accessible": item.get("accessible", ""),
                "gate": location.get("gate", ""),
                "level": location.get("level", ""),
                "place_group": location.get("placeGroup", ""),
                "section": location.get("section", ""),
                "row": location.get("row", ""),
                "seat": location.get("seat", ""),
                "face_value": price.get("faceValue", ""),
                "currency": price.get("currency", ""),
                "price_type": price.get("type", ""),
                "price_level": price.get("level", ""),
                "offer_types": "; ".join(filter(None, offer_types)),
                "is_upsell": is_upsell,
            })

    return rows


# ---------------------------------------------------------------------------
# Build CSV rows
# ---------------------------------------------------------------------------
CSV_FIELDS = [
    # Order
    "order_id",
    "order_status",
    "order_type",
    "order_purchase_date",
    "order_total",
    "order_currency",
    "order_delivery_type",
    "order_payment_type",
    "order_sales_channel",
    # Customer / buyer
    "customer_email",
    "customer_first_name",
    "customer_last_name",
    "customer_phone",
    "customer_city",
    "customer_state",
    "customer_country",
    # Event (from Discovery API)
    "event_id",
    "event_name",
    "event_date",
    "event_venue_name",
    "event_venue_city",
    "event_venue_state",
    "event_venue_country",
    # Seat / ticket
    "seat_unique_id",
    "seat_type",
    "seat_sub_type",
    "seat_status",
    "ticket_holder",
    "transaction_datetime",
    "section",
    "row",
    "seat",
    "level",
    "gate",
    "face_value",
    "currency",
    "price_type",
    "offer_types",
    "is_upsell",
]


def order_to_rows(
    order: Dict[str, Any],
    event_info_map: Dict[str, Dict[str, str]],
) -> List[Dict[str, Any]]:
    """
    Converts a single TM1 order into one or more CSV rows (one per seat).
    Skips orders whose events are not in event_info_map (i.e. future events).
    """
    customer = order.get("customer") or {}
    address = customer.get("address") or {}

    order_base = {
        "order_id": order.get("id", ""),
        "order_status": order.get("status", ""),
        "order_type": order.get("type", ""),
        "order_purchase_date": order.get("purchasedAt") or order.get("transactionDatetime") or "",
        "order_total": (order.get("balance") or {}).get("total", ""),
        "order_currency": (order.get("balance") or {}).get("currency", ""),
        "order_delivery_type": order.get("deliveryType", ""),
        "order_payment_type": (order.get("payment") or {}).get("type", ""),
        "order_sales_channel": (order.get("salesChannel") or {}).get("group", ""),
        # Customer
        "customer_email": customer.get("email", ""),
        "customer_first_name": customer.get("firstName", ""),
        "customer_last_name": customer.get("lastName", ""),
        "customer_phone": customer.get("phone", ""),
        "customer_city": address.get("city", ""),
        "customer_state": address.get("state", ""),
        "customer_country": address.get("country", ""),
    }

    seat_rows = extract_seat_rows(order)
    if not seat_rows:
        # Order with no seat details: emit one row per event in the order
        event_ids = extract_event_ids(order)
        if not event_ids:
            return []

        rows = []
        for eid in event_ids:
            if eid not in event_info_map:
                continue
            row = {**order_base, "event_id": eid, **event_info_map[eid]}
            # Fill seat columns as empty
            for col in CSV_FIELDS:
                row.setdefault(col, "")
            rows.append({col: row.get(col, "") for col in CSV_FIELDS})
        return rows

    rows = []
    for seat in seat_rows:
        eid = seat.get("tm_event_id", "")
        if eid not in event_info_map:
            continue  # event is in the future or unknown → skip

        row = {
            **order_base,
            "event_id": eid,
            **event_info_map[eid],
            "seat_unique_id": seat.get("seat_unique_id", ""),
            "seat_type": seat.get("seat_type", ""),
            "seat_sub_type": seat.get("seat_sub_type", ""),
            "seat_status": seat.get("seat_status", ""),
            "ticket_holder": seat.get("ticket_holder", ""),
            "transaction_datetime": seat.get("transaction_datetime", ""),
            "section": seat.get("section", ""),
            "row": seat.get("row", ""),
            "seat": seat.get("seat", ""),
            "level": seat.get("level", ""),
            "gate": seat.get("gate", ""),
            "face_value": seat.get("face_value", ""),
            "currency": seat.get("currency", ""),
            "price_type": seat.get("price_type", ""),
            "offer_types": seat.get("offer_types", ""),
            "is_upsell": seat.get("is_upsell", ""),
        }
        rows.append({col: row.get(col, "") for col in CSV_FIELDS})

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Fetch past-event ticket buyers from TM1 → CSV")
    parser.add_argument(
        "--since",
        default="2020-01-01T00:00:00Z",
        help="Fetch orders updated since this ISO datetime (default: 2020-01-01T00:00:00Z)",
    )
    parser.add_argument(
        "--output",
        default="past_buyers.csv",
        help="Output CSV file path (default: past_buyers.csv)",
    )
    args = parser.parse_args()

    TM1_API_KEY = _require("TM1_API_KEY")
    DISCOVERY_API_KEY = _require("DISCOVERY_API_KEY")

    now = datetime.now(tz=timezone.utc)
    print(f"[INFO] Today (UTC): {now.date()}")
    print(f"[INFO] Fetching orders since: {args.since}")
    print(f"[INFO] Output file: {args.output}")
    print()

    # ------------------------------------------------------------------
    # 1. Fetch all orders from TM1
    # ------------------------------------------------------------------
    all_orders = tm1_fetch_all_orders(TM1_API_KEY, args.since)
    if not all_orders:
        print("[INFO] No orders found. Exiting.")
        return

    # ------------------------------------------------------------------
    # 2. Collect all unique event IDs across all orders
    # ------------------------------------------------------------------
    all_event_ids: Set[str] = set()
    for order in all_orders:
        for eid in extract_event_ids(order):
            all_event_ids.add(eid)
        # Also grab event IDs from seat rows
        for seat in extract_seat_rows(order):
            eid = seat.get("tm_event_id", "").strip()
            if eid:
                all_event_ids.add(eid)

    print(f"\n[Discovery] Found {len(all_event_ids)} unique event IDs. Fetching event details...")

    # ------------------------------------------------------------------
    # 3. Fetch event details from Discovery API & filter past events
    # ------------------------------------------------------------------
    past_event_info: Dict[str, Dict[str, str]] = {}
    skipped_future = 0
    skipped_unknown = 0

    for i, eid in enumerate(sorted(all_event_ids), 1):
        if i % 20 == 0 or i == len(all_event_ids):
            print(f"  [Discovery] {i}/{len(all_event_ids)} events processed...")

        event_data = discovery_fetch_event(DISCOVERY_API_KEY, eid)

        if event_data is None:
            skipped_unknown += 1
            continue

        event_dt = parse_event_date(event_data)

        if event_dt is None:
            # Can't determine date — include it to be safe (won't skip)
            past_event_info[eid] = extract_event_info(event_data)
            continue

        if event_dt >= now:
            skipped_future += 1
            continue

        past_event_info[eid] = extract_event_info(event_data)

    print(f"\n[Discovery] Past events: {len(past_event_info)}")
    print(f"[Discovery] Future events skipped: {skipped_future}")
    print(f"[Discovery] Unknown/not-found events skipped: {skipped_unknown}")

    if not past_event_info:
        print("\n[INFO] No past events found. Nothing to export.")
        return

    # ------------------------------------------------------------------
    # 4. Build CSV rows — only for past events
    # ------------------------------------------------------------------
    print(f"\n[CSV] Building rows for {len(all_orders)} orders...")
    csv_rows: List[Dict[str, Any]] = []
    orders_with_rows = 0

    for order in all_orders:
        rows = order_to_rows(order, past_event_info)
        if rows:
            csv_rows.extend(rows)
            orders_with_rows += 1

    print(f"[CSV] Orders with past-event rows: {orders_with_rows}")
    print(f"[CSV] Total rows to write: {len(csv_rows)}")

    if not csv_rows:
        print("[INFO] No rows to export. Exiting.")
        return

    # ------------------------------------------------------------------
    # 5. Write CSV
    # ------------------------------------------------------------------
    output_path = Path(args.output)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"\n[Done] CSV written to: {output_path.resolve()}")
    print(f"       {len(csv_rows)} rows, {len(CSV_FIELDS)} columns")


if __name__ == "__main__":
    main()

"""
Characterization harness for sync.publisher.publish_once.

Every database and HubSpot dependency of the publisher is replaced by a recording fake.
Each call is appended to one ordered `trace`, so tests can assert the exact sequence of
side effects: Outbox marks, HubSpot calls, cache lookups, waits.

All patch targets are listed in `_fakes()` against PUBLISHER_MODULE. When Phase 2 moves
publish_once or its dependencies, update this harness only; the expected behavior in
the tests must stay the same.

Synthetic data only.
"""
import datetime as dt
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

PUBLISHER_MODULE = "sync.publisher"

CLIENT_ID = "c1"
PORTAL_ID = "portal-1"
BASE_URL = "https://hubspot.test"
TOKEN = "test-access-token"
ORDER_TYPE = "orders"
CONFIG_ORDER_CONTACT_ASSOC = 507      # passed by config
DISCOVERED_ORDER_CONTACT_ASSOC = 508  # returned by ensure_order_properties
EVENT_TYPE = "2-1001"
ORDER_EVENT_ASSOC = 601
SEAT_TYPE = "2-2002"
SEAT_ASSOCS = {"order_to_seat": 701, "event_to_seat": 702, "contact_to_seat": 703}

EMAIL = "Jane.Doe@Example.test"
EMAIL_NORM = "jane.doe@example.test"
ORDER_ID = "1001|2026-03-02T18:30:00Z"
EVENT_ID = "EV1"
SEAT_ID = f"{ORDER_ID}|0|0"

# Deterministic HubSpot ids produced by the fakes
CONTACT_HS_ID = f"C:{EMAIL_NORM}"
ORDER_HS_ID = f"O:{ORDER_ID}"
EVENT_HS_ID = f"{EVENT_TYPE}:{EVENT_ID}"
SEAT_HS_ID = f"{SEAT_TYPE}:{SEAT_ID}"

EVENT_START = dt.datetime(2026, 4, 10, 0, 30, tzinfo=dt.timezone.utc)  # timestamptz from tm_events

CUSTOMER = {"email": EMAIL, "firstName": "Jane", "lastName": "Doe"}
ORDER = {"id": "1001", "creationDateTime": "2026-03-02T18:30:00Z", "customer": CUSTOMER}

# Shape produced by sync.seat_extract.extract_seats
SEAT = {
    "seat_unique_id": SEAT_ID,
    "tm1_order_id": ORDER_ID,
    "tm_event_id": EVENT_ID,
    "item_group_index": 0,
    "item_index": 0,
    "type": "TICKET",
    "sub_type": "ADULT",
    "status": "SOLD",
    "ticket_holder": None,
    "transaction_datetime": "2026-03-02T18:31:00Z",
    "accessible": False,
    "gate": "G2",
    "level": "Floor",
    "place_group": None,
    "section": "101",
    "row": "B",
    "seat": "7",
    "face_value": 45.5,
    "currency": "USD",
    "price_type": "STANDARD",
    "price_level": "PL1",
    "offers": ["PROMO", "PROMO2"],
    "is_upsell": False,
}

# Row returned by get_tm_event_cached
TM_EVENT = {
    "name": "Spring Gala",
    "start_datetime": EVENT_START,
    "venue_name": "Main Hall",
    "city": "Springfield",
    "state": "IL",
    "product_type": "Event",
    "payload": {"_normalized": {}},
    "subsystem_id": "SYS1",
    "subsystem_name": "Host",
    "attraction_ids": ["A1", "A1", "A2"],
    "attraction_names": ["The Band"],
    "venue_ids": ["V1"],
    "country_code": "US",
    "event_url": "https://events.test/ev1",
    "timezone": "America/Chicago",
    "status_code": "onsale",
}


# ---- Outbox rows: (outbox_id, entity, dedupe_key, payload, attempts) -------------------
def contact_row(oid=1, attempts=0, customer=None):
    return (oid, "contact", EMAIL_NORM, {"source": "tm1", "customer": dict(customer or CUSTOMER)}, attempts)


def order_row(oid=2, attempts=0):
    return (oid, "order", ORDER_ID, {"source": "tm1", "order": dict(ORDER)}, attempts)


def association_row(oid=3, attempts=0, **override):
    payload = {"source": "tm1", "tm1_order_id": ORDER_ID, "email": EMAIL, "email_normalized": EMAIL_NORM}
    payload.update(override)
    return (oid, "association", f"{ORDER_ID}->{EMAIL_NORM}", payload, attempts)


def event_row(oid=4, attempts=0, tm_event_id=EVENT_ID, dedupe_key=EVENT_ID):
    payload = {"source": "tm_discovery"}
    if tm_event_id is not None:
        payload["tm_event_id"] = tm_event_id
    return (oid, "event", dedupe_key, payload, attempts)


def order_event_row(oid=5, attempts=0, **override):
    payload = {"source": "tm", "tm1_order_id": ORDER_ID, "tm_event_id": EVENT_ID}
    payload.update(override)
    return (oid, "order_event_association", f"{ORDER_ID}->{EVENT_ID}", payload, attempts)


def seat_row(oid=6, attempts=0, dedupe_key=SEAT_ID, seat=None):
    return (oid, "seat", dedupe_key, {"source": "tm1", "seat": dict(seat or SEAT)}, attempts)


def order_seat_row(oid=7, attempts=0, **override):
    payload = {"source": "tm1", "tm1_order_id": ORDER_ID, "seat_unique_id": SEAT_ID}
    payload.update(override)
    return (oid, "order_seat_association", f"{ORDER_ID}->{SEAT_ID}", payload, attempts)


def event_seat_row(oid=8, attempts=0, **override):
    payload = {"source": "tm1", "seat_unique_id": SEAT_ID, "tm_event_id": EVENT_ID}
    payload.update(override)
    return (oid, "event_seat_association", f"{SEAT_ID}->{EVENT_ID}", payload, attempts)


def contact_seat_row(oid=9, attempts=0, **override):
    payload = {"source": "tm1", "seat_unique_id": SEAT_ID, "email_normalized": EMAIL_NORM}
    payload.update(override)
    return (oid, "contact_seat_association", f"{SEAT_ID}->{EMAIL_NORM}", payload, attempts)


def all_rows():
    return [
        contact_row(), order_row(), association_row(), event_row(), order_event_row(),
        seat_row(), order_seat_row(), event_seat_row(), contact_seat_row(),
    ]


# Search index that resolves every object when the in-run caches are empty
FULL_SEARCH_INDEX = {
    (ORDER_TYPE, "tm1_unique_id", ORDER_ID): ORDER_HS_ID,
    ("contacts", "email", EMAIL): CONTACT_HS_ID,
    ("contacts", "email", EMAIL_NORM): CONTACT_HS_ID,
    (EVENT_TYPE, "tm_event_id", EVENT_ID): EVENT_HS_ID,
    (SEAT_TYPE, "seat_unique_id", SEAT_ID): SEAT_HS_ID,
}


class Harness:
    def __init__(self) -> None:
        self.rows: List[Tuple] = []
        self.trace: List[Tuple] = []
        self.logs: List[Tuple[str, str, Dict[str, Any]]] = []
        self.takes = 0
        self.bucket_args: Optional[Tuple[int, int]] = None

        # Schema setup results
        self.discovered_order_assoc: Any = DISCOVERED_ORDER_CONTACT_ASSOC
        self.event_object_type: Optional[str] = EVENT_TYPE
        self.order_to_event_assoc: Optional[int] = ORDER_EVENT_ASSOC
        self.seat_object_type: Optional[str] = SEAT_TYPE
        self.seat_assoc_ids: Dict[str, int] = dict(SEAT_ASSOCS)
        self.provision_errors: Dict[str, Exception] = {}  # contact|order|event|seat -> exception

        # World state
        self.search_index: Dict[Tuple[str, str, str], str] = {}
        self.tm_events: Dict[str, Dict[str, Any]] = {EVENT_ID: dict(TM_EVENT)}
        self.seats_cache: Dict[str, Dict[str, Any]] = {}
        self.existing_associations: Dict[Tuple[str, str, str], List[str]] = {}
        self.upsert_result: Dict[str, str] = {}  # contact|order|<object_type> -> "created"/"updated"
        self.raise_on: Dict[str, Exception] = {}  # fake name -> exception raised on every call

        # Captured payloads
        self.custom_props: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.upserted_customers: List[Dict[str, Any]] = []
        self.upserted_orders: List[Dict[str, Any]] = []

    # ---- helpers ----------------------------------------------------------------------
    def _check(self, base_url, token):
        assert base_url == BASE_URL, base_url
        assert token == TOKEN, token

    def _raise(self, name):
        exc = self.raise_on.get(name)
        if exc is not None:
            raise exc

    def log_events(self) -> List[str]:
        return [e for e, _, _ in self.logs]

    def log_fields(self, event: str) -> Dict[str, Any]:
        return next(f for e, _, f in self.logs if e == event)

    def outbox_marks(self) -> List[Tuple]:
        return [t for t in self.trace if t[0] in ("db.sent", "db.retry", "db.failed")]

    def hubspot_calls(self) -> List[Tuple]:
        return [t for t in self.trace if t[0].startswith("hs.") and t[0] not in (
            "hs.token", "hs.ensure_contact", "hs.ensure_order", "hs.ensure_event", "hs.ensure_seat")]

    # ---- fakes: Outbox repository -----------------------------------------------------
    def fetch_outbox_pending_batch(self, client_id, batch_size):
        self.trace.append(("db.fetch", client_id, batch_size))
        return list(self.rows)

    def mark_outbox_sent(self, outbox_id, hubspot_object_id=None):
        self.trace.append(("db.sent", outbox_id, hubspot_object_id))

    def mark_outbox_failed(self, outbox_id, attempts, last_error):
        self.trace.append(("db.failed", outbox_id, attempts, last_error))

    def mark_outbox_retry(self, outbox_id, attempts, next_retry_at, last_error):
        self.trace.append(("db.retry", outbox_id, attempts, next_retry_at, last_error))

    # ---- fakes: caches in Postgres ----------------------------------------------------
    def get_tm_event_cached(self, *, client_id, tm_event_id):
        assert client_id == CLIENT_ID
        self.trace.append(("db.tm_event", tm_event_id))
        return self.tm_events.get(tm_event_id)

    def get_seat_cached(self, *, client_id, seat_unique_id):
        assert client_id == CLIENT_ID
        self.trace.append(("db.seat", seat_unique_id))
        return self.seats_cache.get(seat_unique_id)

    # ---- fakes: OAuth + schema setup --------------------------------------------------
    def get_valid_access_token(self, *, portal_id, hubspot_base_url, client_id, client_secret):
        self.trace.append(("hs.token", portal_id))
        return TOKEN

    def ensure_contact_properties(self, **kw):
        self.trace.append(("hs.ensure_contact",))
        if "contact" in self.provision_errors:
            raise self.provision_errors["contact"]

    def ensure_order_properties(self, **kw):
        self.trace.append(("hs.ensure_order", kw["order_object_type"]))
        if "order" in self.provision_errors:
            raise self.provision_errors["order"]
        return self.discovered_order_assoc

    def ensure_event_object(self, **kw):
        self.trace.append(("hs.ensure_event", kw["order_object_type"]))
        if "event" in self.provision_errors:
            raise self.provision_errors["event"]
        return self.event_object_type, self.order_to_event_assoc

    def ensure_seat_object(self, **kw):
        self.trace.append(("hs.ensure_seat", kw["order_object_type"], kw["event_object_type"]))
        if "seat" in self.provision_errors:
            raise self.provision_errors["seat"]
        return self.seat_object_type, dict(self.seat_assoc_ids)

    # ---- fakes: HubSpot client --------------------------------------------------------
    def search_object_id_by_property(self, base_url, token, object_type, property_name, value):
        self._check(base_url, token)
        self.trace.append(("hs.search", object_type, property_name, value))
        self._raise("search")
        return self.search_index.get((object_type, property_name, value))

    def upsert_contact_by_email(self, base_url, token, email, customer):
        self._check(base_url, token)
        self.trace.append(("hs.upsert_contact", email))
        self.upserted_customers.append(customer)
        self._raise("upsert_contact")
        return self.upsert_result.get("contact", "created"), f"C:{email.lower()}"

    def upsert_order_by_tm1_order_id(self, base_url, token, order_object_type, tm1_order_id, snapshot):
        self._check(base_url, token)
        self.trace.append(("hs.upsert_order", order_object_type, tm1_order_id))
        self.upserted_orders.append(snapshot)
        self._raise("upsert_order")
        return self.upsert_result.get("order", "created"), f"O:{tm1_order_id}"

    def upsert_custom_object_by_unique_property(self, base_url, token, object_type, prop, value, props):
        self._check(base_url, token)
        self.trace.append(("hs.upsert_custom", object_type, prop, value))
        self.custom_props[(object_type, value)] = props
        self._raise("upsert_custom")
        return self.upsert_result.get(object_type, "created"), f"{object_type}:{value}"

    def associate_order_to_contact(self, base_url, token, order_type, order_id, contact_id, type_id):
        self._check(base_url, token)
        self.trace.append(("hs.assoc_v3", order_type, order_id, contact_id, type_id))
        self._raise("assoc_v3")

    def associate_objects_v4_labeled_batch(self, base_url, token, from_type, to_type, from_id, to_id, type_id):
        self._check(base_url, token)
        self.trace.append(("hs.assoc_v4", from_type, to_type, from_id, to_id, type_id))
        self._raise("assoc_v4")

    def list_associations(self, base_url, token, from_type, from_id, to_type):
        self._check(base_url, token)
        self.trace.append(("hs.list_assoc", from_type, from_id, to_type))
        self._raise("list_assoc")
        return list(self.existing_associations.get((from_type, from_id, to_type), []))

    def delete_associations_batch(self, base_url, token, from_type, to_type, from_id, to_ids):
        self._check(base_url, token)
        self.trace.append(("hs.delete_assoc", from_type, to_type, from_id, list(to_ids)))
        self._raise("delete_assoc")

    # ---- fakes: retry schedule, waits, logging, rate limiter --------------------------
    @staticmethod
    def compute_next_retry_at(attempts):
        return f"next@{attempts}"

    def sleep(self, seconds):
        self.trace.append(("sleep", seconds))

    def log(self, event, level="INFO", **fields):
        self.logs.append((event, level, fields))

    def _bucket_cls(self):
        harness = self

        class FakeTokenBucket:
            def __init__(self, capacity, window_ms):
                harness.bucket_args = (capacity, window_ms)

            def take(self, n=1):
                harness.takes += n

        return FakeTokenBucket

    def _fakes(self) -> Dict[str, Any]:
        names = [
            "fetch_outbox_pending_batch", "mark_outbox_sent", "mark_outbox_failed", "mark_outbox_retry",
            "get_tm_event_cached", "get_seat_cached", "get_valid_access_token",
            "ensure_contact_properties", "ensure_order_properties", "ensure_event_object", "ensure_seat_object",
            "search_object_id_by_property", "upsert_contact_by_email", "upsert_order_by_tm1_order_id",
            "upsert_custom_object_by_unique_property", "associate_order_to_contact",
            "associate_objects_v4_labeled_batch", "list_associations", "delete_associations_batch",
            "compute_next_retry_at", "log",
        ]
        fakes = {n: getattr(self, n) for n in names}
        fakes["TokenBucket"] = self._bucket_cls()
        fakes["time"] = SimpleNamespace(sleep=self.sleep)
        return fakes

    def run(self, **overrides) -> Dict[str, int]:
        import sync.publisher as publisher

        kwargs = dict(
            client_id=CLIENT_ID,
            portal_id=PORTAL_ID,
            hubspot_base_url=BASE_URL,
            hubspot_client_id="test-client-id",
            hubspot_client_secret="test-client-secret",
            order_object_type=ORDER_TYPE,
            assoc_type_id=CONFIG_ORDER_CONTACT_ASSOC,
            batch_size=50,
        )
        kwargs.update(overrides)
        with mock.patch.multiple(PUBLISHER_MODULE, **self._fakes()):
            return publisher.publish_once(**kwargs)


def fixed_offset_zoneinfo(offset_hours: int):
    """Stand-in for zoneinfo.ZoneInfo so the Matinee/Evening logic is deterministic."""
    return lambda key: dt.timezone(dt.timedelta(hours=offset_hours), key)


def epoch_ms(value: dt.datetime) -> int:
    return int(value.timestamp() * 1000)

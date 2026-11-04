"""
Characterization tests for sync.publisher.publish_once: they pin CURRENT behavior so the
Phase 2 refactor can prove it changed nothing.

They cover:
- handler order and in-run ID caches;
- every Outbox mark (sent / retry / failed) and its exact `last_error` string;
- HTTP error classification, 429 waits, generic-exception paths;
- schema setup fallbacks;
- event and seat property building;
- counters, log event names and rate-limiter usage.

Tests marked CURRENT BEHAVIOR pin behavior that a planned Phase 1 fix will change on
purpose. Update them in that fix, never in a refactor.

Run from tm1-sync/ with the project venv:
    sync/.venv/Scripts/python -m unittest discover -s tests -v
"""
import datetime as dt
import unittest
from unittest import mock

import requests

from tests import publisher_harness as H
from tests.publisher_harness import Harness


def http_error(status, retry_after=None, body='{"status":"error","message":"synthetic"}'):
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode("utf-8")
    if retry_after is not None:
        resp.headers["Retry-After"] = retry_after
    return requests.exceptions.HTTPError(f"HTTP {status}", response=resp)


# (row factory, fake that performs the row's main HubSpot call, http label, exc log event, exc retry label)
ENTITY_ERROR_PATHS = [
    ("contact", H.contact_row, "upsert_contact", "contact", "publish.contact.exc", "exc:contact"),
    ("order", H.order_row, "upsert_order", "order", "publish.order.exc", "exc:order"),
    ("association", H.association_row, "assoc_v3", "association", "publish.association.exc", "exc:association"),
    ("event", H.event_row, "upsert_custom", "event", "publish.event.exc", "exc:event"),
    ("order_event_association", H.order_event_row, "assoc_v4", "order_event_association",
     "publish.order_event_assoc.exc", "exc:order_event_association"),
    ("seat", H.seat_row, "upsert_custom", "seat", "publish.seat.exc", "exc:seat"),
    ("order_seat_association", H.order_seat_row, "assoc_v4", "order_seat_association",
     "publish.order_seat_assoc.exc", "exc:order_seat_assoc"),
    ("event_seat_association", H.event_seat_row, "list_assoc", "event_seat_association",
     "publish.event_seat_assoc.exc", "exc:event_seat_assoc"),
    ("contact_seat_association", H.contact_seat_row, "assoc_v4", "contact_seat_association",
     "publish.contact_seat_assoc.exc", "exc:contact_seat_assoc"),
]

ZERO_COUNTS = {
    "picked": 0, "sent": 0, "retry_scheduled": 0, "failed": 0,
    "contacts_created": 0, "contacts_updated": 0, "orders_created": 0, "orders_updated": 0,
    "associations_created": 0, "events_created": 0, "events_updated": 0,
    "order_event_associations_created": 0, "seats_created": 0, "seats_updated": 0,
    "order_seat_associations_created": 0, "event_seat_associations_created": 0,
    "contact_seat_associations_created": 0,
}


def counts(**changes):
    out = dict(ZERO_COUNTS)
    out.update(changes)
    return out


class PublisherTestCase(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        # Deterministic Matinee/Evening regardless of the machine's tz database (UTC-5 = CDT).
        p = mock.patch("zoneinfo.ZoneInfo", H.fixed_offset_zoneinfo(-5))
        p.start()
        self.addCleanup(p.stop)

    def run_rows(self, *rows, **overrides):
        self.h.rows = list(rows)
        return self.h.run(**overrides)


class FullBatchTests(PublisherTestCase):
    def test_happy_path_exact_side_effects(self):
        result = self.run_rows(*H.all_rows())

        self.assertEqual(self.h.trace, [
            ("db.fetch", H.CLIENT_ID, 50),
            ("hs.token", H.PORTAL_ID),
            ("hs.ensure_contact",),
            ("hs.ensure_order", H.ORDER_TYPE),
            ("hs.ensure_event", H.ORDER_TYPE),
            ("hs.ensure_seat", H.ORDER_TYPE, H.EVENT_TYPE),
            # 1) contact
            ("hs.upsert_contact", H.EMAIL),
            ("db.sent", 1, H.CONTACT_HS_ID),
            # 2) order
            ("hs.upsert_order", H.ORDER_TYPE, H.ORDER_ID),
            ("db.sent", 2, H.ORDER_HS_ID),
            # 3) order -> contact (both ids from the in-run cache; discovered type id wins)
            ("hs.assoc_v3", H.ORDER_TYPE, H.ORDER_HS_ID, H.CONTACT_HS_ID, H.DISCOVERED_ORDER_CONTACT_ASSOC),
            ("db.sent", 3, None),
            # 4) event
            ("db.tm_event", H.EVENT_ID),
            ("hs.upsert_custom", H.EVENT_TYPE, "tm_event_id", H.EVENT_ID),
            ("db.sent", 4, H.EVENT_HS_ID),
            # 5) order -> event
            ("hs.assoc_v4", H.ORDER_TYPE, H.EVENT_TYPE, H.ORDER_HS_ID, H.EVENT_HS_ID, H.ORDER_EVENT_ASSOC),
            ("db.sent", 5, None),
            # 6) seat (seat cache miss -> payload seat; event lookup for attraction name)
            ("db.seat", H.SEAT_ID),
            ("db.tm_event", H.EVENT_ID),
            ("hs.upsert_custom", H.SEAT_TYPE, "seat_unique_id", H.SEAT_ID),
            ("db.sent", 6, H.SEAT_HS_ID),
            # 7) order -> seat
            ("hs.assoc_v4", H.ORDER_TYPE, H.SEAT_TYPE, H.ORDER_HS_ID, H.SEAT_HS_ID, 701),
            ("db.sent", 7, None),
            # 8) event -> seat (stale-association check first)
            ("hs.list_assoc", H.SEAT_TYPE, H.SEAT_HS_ID, H.EVENT_TYPE),
            ("hs.assoc_v4", H.EVENT_TYPE, H.SEAT_TYPE, H.EVENT_HS_ID, H.SEAT_HS_ID, 702),
            ("db.sent", 8, None),
            # 9) contact -> seat
            ("hs.assoc_v4", "contacts", H.SEAT_TYPE, H.CONTACT_HS_ID, H.SEAT_HS_ID, 703),
            ("db.sent", 9, None),
        ])
        self.assertEqual(result, counts(
            picked=9, sent=9, contacts_created=1, orders_created=1, associations_created=1,
            events_created=1, order_event_associations_created=1, seats_created=1,
            order_seat_associations_created=1, event_seat_associations_created=1,
            contact_seat_associations_created=1,
        ))

    def test_happy_path_log_events_and_fields(self):
        result = self.run_rows(*H.all_rows())
        self.assertEqual(self.h.log_events(), [
            "publish.start",
            "publish.event.debug", "publish.event.result",
            "publish.order_event_assoc.debug", "publish.order_event_assoc.result",
            "publish.seat.debug", "publish.seat.result",
            "publish.done",
        ])
        self.assertEqual(self.h.log_fields("publish.start"), {
            "client_id": H.CLIENT_ID, "batch_size": 50, "picked": 9, "portal_id": H.PORTAL_ID,
            "order_object_type": H.ORDER_TYPE, "assoc_type_id": H.CONFIG_ORDER_CONTACT_ASSOC,
            "rate_limit_max": 90, "rate_limit_window_ms": 10000,
        })
        self.assertEqual(self.h.log_fields("publish.done"), dict(client_id=H.CLIENT_ID, **result))
        self.assertEqual(self.h.log_fields("publish.event.result"), {
            "client_id": H.CLIENT_ID, "outbox_id": 4, "tm_event_id": H.EVENT_ID,
            "result": "created", "hs_event_id": H.EVENT_HS_ID,
        })
        self.assertEqual(self.h.log_fields("publish.seat.result"), {
            "client_id": H.CLIENT_ID, "outbox_id": 6, "seat_unique_id": H.SEAT_ID,
            "result": "created", "hs_seat_id": H.SEAT_HS_ID,
        })

    def test_rate_limiter_configuration_and_usage(self):
        self.run_rows(*H.all_rows(), rate_limit_max=40, rate_limit_window_ms=5000)
        self.assertEqual(self.h.bucket_args, (40, 5000))
        # One token per HubSpot call; event->seat makes two (list + associate).
        self.assertEqual(self.h.takes, 10)
        self.assertEqual(len(self.h.hubspot_calls()), 10)

    def test_handlers_run_in_fixed_order_regardless_of_row_order(self):
        rows = list(reversed(H.all_rows()))
        self.run_rows(*rows)
        sent_ids = [t[1] for t in self.h.trace if t[0] == "db.sent"]
        self.assertEqual(sent_ids, [1, 2, 3, 4, 5, 6, 7, 8, 9])

    def test_rows_of_the_same_entity_keep_their_batch_order(self):
        self.run_rows(H.contact_row(oid=11), H.contact_row(oid=10))
        self.assertEqual([t[1] for t in self.h.outbox_marks()], [11, 10])

    def test_updated_results_are_counted_separately(self):
        self.h.upsert_result = {"contact": "updated", "order": "updated", H.EVENT_TYPE: "updated", H.SEAT_TYPE: "updated"}
        result = self.run_rows(H.contact_row(), H.order_row(), H.event_row(), H.seat_row())
        self.assertEqual(result, counts(
            picked=4, sent=4, contacts_updated=1, orders_updated=1, events_updated=1, seats_updated=1,
        ))

    def test_empty_batch(self):
        result = self.run_rows()
        self.assertEqual(result, ZERO_COUNTS)
        self.assertEqual(self.h.trace, [("db.fetch", H.CLIENT_ID, 50)])
        self.assertEqual(self.h.log_events(), ["publish.noop"])
        self.assertEqual(self.h.log_fields("publish.noop"), {"client_id": H.CLIENT_ID, "batch_size": 50})


class CacheAndLookupTests(PublisherTestCase):
    def test_association_falls_back_to_search_when_not_cached(self):
        self.h.search_index = dict(H.FULL_SEARCH_INDEX)
        self.run_rows(H.association_row())
        self.assertEqual(self.h.hubspot_calls(), [
            ("hs.search", H.ORDER_TYPE, "tm1_unique_id", H.ORDER_ID),
            ("hs.search", "contacts", "email", H.EMAIL),
            ("hs.assoc_v3", H.ORDER_TYPE, H.ORDER_HS_ID, H.CONTACT_HS_ID, H.DISCOVERED_ORDER_CONTACT_ASSOC),
        ])
        self.assertEqual(self.h.takes, 3)

    def test_association_uses_email_normalized_when_email_missing(self):
        self.h.search_index = dict(H.FULL_SEARCH_INDEX)
        self.run_rows(H.association_row(email=""))
        self.assertIn(("hs.search", "contacts", "email", H.EMAIL_NORM), self.h.trace)
        self.assertEqual(self.h.outbox_marks(), [("db.sent", 3, None)])

    def test_search_results_are_cached_within_the_run(self):
        self.h.search_index = dict(H.FULL_SEARCH_INDEX)
        self.run_rows(H.association_row(oid=3), H.association_row(oid=30))
        searches = [t for t in self.h.trace if t[0] == "hs.search"]
        self.assertEqual(len(searches), 2)
        self.assertEqual(self.h.outbox_marks(), [("db.sent", 3, None), ("db.sent", 30, None)])

    def test_every_association_resolves_ids_by_search(self):
        self.h.search_index = dict(H.FULL_SEARCH_INDEX)
        self.run_rows(H.order_event_row(), H.order_seat_row(), H.event_seat_row(), H.contact_seat_row())
        self.assertEqual([t for t in self.h.trace if t[0] == "hs.search"], [
            ("hs.search", H.ORDER_TYPE, "tm1_unique_id", H.ORDER_ID),
            ("hs.search", H.EVENT_TYPE, "tm_event_id", H.EVENT_ID),
            ("hs.search", H.SEAT_TYPE, "seat_unique_id", H.SEAT_ID),
            ("hs.search", "contacts", "email", H.EMAIL_NORM),
        ])
        self.assertEqual([t[0] for t in self.h.outbox_marks()], ["db.sent"] * 4)

    def test_event_id_falls_back_to_dedupe_key(self):
        self.run_rows(H.event_row(tm_event_id=None, dedupe_key=H.EVENT_ID))
        self.assertIn(("hs.upsert_custom", H.EVENT_TYPE, "tm_event_id", H.EVENT_ID), self.h.trace)

    def test_seat_cache_row_takes_precedence_over_payload(self):
        cached = dict(H.SEAT, section="202", row="C", seat="9")
        self.h.seats_cache[H.SEAT_ID] = cached
        self.run_rows(H.seat_row())
        props = self.h.custom_props[(H.SEAT_TYPE, H.SEAT_ID)]
        self.assertEqual(props["seat_location"], "Sec 202 Row C Seat 9")


class EventSeatStaleAssociationTests(PublisherTestCase):
    def test_stale_event_associations_are_removed_before_associating(self):
        self.h.search_index = dict(H.FULL_SEARCH_INDEX)
        self.h.existing_associations[(H.SEAT_TYPE, H.SEAT_HS_ID, H.EVENT_TYPE)] = ["E-old", H.EVENT_HS_ID, "E-old2"]
        self.run_rows(H.event_seat_row())
        self.assertEqual(self.h.hubspot_calls()[-3:], [
            ("hs.list_assoc", H.SEAT_TYPE, H.SEAT_HS_ID, H.EVENT_TYPE),
            ("hs.delete_assoc", H.SEAT_TYPE, H.EVENT_TYPE, H.SEAT_HS_ID, ["E-old", "E-old2"]),
            ("hs.assoc_v4", H.EVENT_TYPE, H.SEAT_TYPE, H.EVENT_HS_ID, H.SEAT_HS_ID, 702),
        ])
        self.assertEqual(self.h.log_fields("publish.event_seat_assoc.removing_stale")["stale_count"], 2)
        self.assertEqual(self.h.outbox_marks(), [("db.sent", 8, None)])


class ValidationFailureTests(PublisherTestCase):
    """Malformed payloads are marked failed (attempts + 1) without calling HubSpot."""

    CASES = [
        (H.contact_row(customer={"firstName": "Jane"}), "missing_email_in_contact_payload"),
        (H.contact_row(customer={"email": "   "}), "missing_email_in_contact_payload"),
        (H.association_row(tm1_order_id=""), "missing_assoc_payload_fields"),
        (H.association_row(email="", email_normalized=""), "missing_assoc_payload_fields"),
        (H.event_row(tm_event_id=None, dedupe_key=""), "missing_tm_event_id"),
        (H.order_event_row(tm_event_id=""), "missing_order_event_fields"),
        (H.order_event_row(tm1_order_id=""), "missing_order_event_fields"),
        (H.seat_row(dedupe_key=""), "missing_seat_unique_id"),
        (H.order_seat_row(seat_unique_id=""), "missing_order_seat_fields"),
        (H.event_seat_row(tm_event_id=""), "missing_event_seat_fields"),
        (H.contact_seat_row(email_normalized=""), "missing_contact_seat_fields"),
    ]

    def test_validation_failures(self):
        for row, error in self.CASES:
            with self.subTest(entity=row[1], error=error):
                self.setUp()
                result = self.run_rows(row)
                self.assertEqual(self.h.outbox_marks(), [("db.failed", row[0], 1, error)])
                self.assertEqual(self.h.hubspot_calls(), [])
                self.assertEqual(result["failed"], 1)
                self.assertEqual(result["sent"], 0)


class NotProvisionedTests(PublisherTestCase):
    """Missing custom-object setup schedules a retry instead of failing."""

    def _expect_retry(self, row, error, log_event=None):
        result = self.run_rows(row)
        self.assertEqual(self.h.outbox_marks(), [("db.retry", row[0], 1, "next@1", error)])
        self.assertEqual(self.h.hubspot_calls(), [])
        self.assertEqual(result["retry_scheduled"], 1)
        if log_event:
            self.assertEqual(self.h.logs[1][0:2], (log_event, "WARN"))

    def test_event_without_event_object(self):
        self.h.event_object_type = None
        self._expect_retry(H.event_row(), "event_object_not_provisioned", "publish.event.missing_object_type")

    def test_order_event_without_event_object(self):
        self.h.event_object_type = None
        self._expect_retry(H.order_event_row(), "event_object_not_provisioned",
                           "publish.order_event_assoc.missing_object_type")

    def test_order_event_without_association_type(self):
        self.h.order_to_event_assoc = None
        self._expect_retry(H.order_event_row(), "event_object_not_provisioned",
                           "publish.order_event_assoc.missing_object_type")

    def test_seat_without_seat_object(self):
        self.h.seat_object_type = None
        self._expect_retry(H.seat_row(), "seat_object_not_provisioned", "publish.seat.missing_object_type")

    def test_order_seat_without_seat_object(self):
        self.h.seat_object_type = None
        self._expect_retry(H.order_seat_row(), "seat_object_not_provisioned")

    def test_event_seat_without_seat_or_event_object(self):
        for attr in ("seat_object_type", "event_object_type"):
            with self.subTest(missing=attr):
                self.setUp()
                setattr(self.h, attr, None)
                self._expect_retry(H.event_seat_row(), "seat_or_event_object_not_provisioned")

    def test_contact_seat_without_seat_object(self):
        self.h.seat_object_type = None
        self._expect_retry(H.contact_seat_row(), "seat_object_not_provisioned")


class MissingHubSpotIdTests(PublisherTestCase):
    """Associations whose endpoints are not in HubSpot yet are retried."""

    CASES = [
        (H.association_row(), "assoc_missing_hubspot_ids", "publish.association.missing_ids"),
        (H.order_event_row(), "missing_hubspot_order_or_event_id", "publish.order_event_assoc.missing_ids"),
        (H.order_seat_row(), "missing_hubspot_order_or_seat_id", "publish.order_seat_assoc.missing_ids"),
        (H.event_seat_row(), "missing_hubspot_event_or_seat_id", "publish.event_seat_assoc.missing_ids"),
        (H.contact_seat_row(), "missing_hubspot_contact_or_seat_id", "publish.contact_seat_assoc.missing_ids"),
    ]

    def test_missing_ids_schedule_retry(self):
        for row, error, log_event in self.CASES:
            with self.subTest(entity=row[1]):
                self.setUp()
                result = self.run_rows(row)  # empty search index
                self.assertEqual(self.h.outbox_marks(), [("db.retry", row[0], 1, "next@1", error)])
                self.assertIn(log_event, self.h.log_events())
                self.assertEqual(result["retry_scheduled"], 1)
                self.assertFalse(any(t[0] in ("hs.assoc_v3", "hs.assoc_v4") for t in self.h.trace))

    def test_missing_id_logs_hash_not_email(self):
        self.run_rows(H.association_row())
        fields = self.h.log_fields("publish.association.missing_ids")
        self.assertNotIn("email", fields)
        self.assertEqual(len(fields["email_hash"]), 10)

    def test_missing_seat_association_type_ids(self):
        cases = [
            (H.order_seat_row(), "missing_order_to_seat_assoc_type_id"),
            (H.event_seat_row(), "missing_event_to_seat_assoc_type_id"),
            (H.contact_seat_row(), "missing_contact_to_seat_assoc_type_id"),
        ]
        for row, error in cases:
            with self.subTest(entity=row[1]):
                self.setUp()
                self.h.search_index = dict(H.FULL_SEARCH_INDEX)
                self.h.seat_assoc_ids = {}
                self.run_rows(row)
                self.assertEqual(self.h.outbox_marks(), [("db.retry", row[0], 1, "next@1", error)])
                self.assertFalse(any(t[0] in ("hs.assoc_v4", "hs.list_assoc") for t in self.h.trace))


class ErrorHandlingTests(PublisherTestCase):
    def test_generic_exception_per_entity(self):
        for entity, row_fn, fake, _label, exc_event, exc_label in ENTITY_ERROR_PATHS:
            with self.subTest(entity=entity):
                self.setUp()
                self.h.search_index = dict(H.FULL_SEARCH_INDEX)
                self.h.raise_on[fake] = ValueError("synthetic failure")
                row = row_fn(attempts=2)
                result = self.run_rows(row)
                self.assertEqual(self.h.outbox_marks()[-1], ("db.retry", row[0], 3, "next@3", exc_label))
                event, level, fields = next(l for l in self.h.logs if l[0] == exc_event)
                self.assertEqual(level, "ERROR")
                self.assertEqual(fields["err"], "ValueError")
                self.assertEqual(fields["detail"], "synthetic failure")
                self.assertEqual(result["retry_scheduled"], 1)

    def test_retryable_http_error_per_entity(self):
        for entity, row_fn, fake, label, _exc_event, _exc_label in ENTITY_ERROR_PATHS:
            with self.subTest(entity=entity):
                self.setUp()
                self.h.search_index = dict(H.FULL_SEARCH_INDEX)
                self.h.raise_on[fake] = http_error(500)
                row = row_fn(attempts=2)
                self.run_rows(row)
                self.assertEqual(self.h.outbox_marks()[-1],
                                 ("db.retry", row[0], 3, "next@3", f"{label}:http_500:server_error"))
                self.assertIn("publish.http_error.debug", self.h.log_events())

    def test_non_retryable_http_error_per_entity(self):
        for entity, row_fn, fake, label, _exc_event, _exc_label in ENTITY_ERROR_PATHS:
            with self.subTest(entity=entity):
                self.setUp()
                self.h.search_index = dict(H.FULL_SEARCH_INDEX)
                self.h.raise_on[fake] = http_error(400)
                row = row_fn(attempts=2)
                result = self.run_rows(row)
                self.assertEqual(self.h.outbox_marks()[-1],
                                 ("db.failed", row[0], 3, f"{label}:http_400:bad_request"))
                self.assertEqual(result["failed"], 1)

    def test_http_status_classification(self):
        cases = [
            (401, "db.failed", "auth_error"),
            (403, "db.failed", "auth_error"),
            (404, "db.failed", "not_found_schema_or_object"),
            (409, "db.failed", "bad_request"),
            (422, "db.failed", "bad_request"),
            (500, "db.retry", "server_error"),
            (502, "db.retry", "server_error"),
            (503, "db.retry", "server_error"),
            (504, "db.retry", "server_error"),
            (501, "db.retry", "unknown_status"),
            (302, "db.retry", "unknown_status"),
        ]
        for status, mark, message in cases:
            with self.subTest(status=status):
                self.setUp()
                self.h.raise_on["upsert_contact"] = http_error(status)
                self.run_rows(H.contact_row())
                last = self.h.outbox_marks()[-1]
                self.assertEqual(last[0], mark)
                self.assertEqual(last[-1], f"contact:http_{status}:{message}")
                self.assertFalse(any(t[0] == "sleep" for t in self.h.trace))

    def test_http_error_without_response_is_retried(self):
        self.h.raise_on["upsert_contact"] = requests.exceptions.HTTPError("no response")
        self.run_rows(H.contact_row())
        self.assertEqual(self.h.outbox_marks(),
                         [("db.retry", 1, 1, "next@1", "contact:http_None:no_status_network_error")])
        self.assertIsNone(self.h.log_fields("publish.http_error.debug")["body"])

    def test_http_error_debug_log_fields(self):
        self.h.raise_on["upsert_contact"] = http_error(400, body='{"message":"bad"}')
        self.run_rows(H.contact_row(attempts=4))
        event, level, fields = next(l for l in self.h.logs if l[0] == "publish.http_error.debug")
        self.assertEqual(level, "ERROR")
        self.assertEqual(fields, {
            "client_id": H.CLIENT_ID, "outbox_id": 1, "label": "contact", "attempts": 4,
            "status": 400, "retry_after_s": None, "body": '{"message":"bad"}',
        })

    def test_429_waits_before_scheduling_retry(self):
        cases = [
            # (Retry-After header, attempts, expected wait seconds)
            ("7", 0, 7),
            ("0", 0, 1),        # clamped to >= 1
            ("abc", 0, 2),      # unparsable -> fallback min(30, max(2, attempts + 2))
            (None, 0, 2),
            (None, 3, 5),
            (None, 40, 30),     # fallback capped at 30s
        ]
        for retry_after, attempts, wait in cases:
            with self.subTest(retry_after=retry_after, attempts=attempts):
                self.setUp()
                self.h.raise_on["upsert_contact"] = http_error(429, retry_after=retry_after)
                self.run_rows(H.contact_row(attempts=attempts))
                self.assertEqual(self.h.trace[-2:], [
                    ("sleep", wait),
                    ("db.retry", 1, attempts + 1, f"next@{attempts + 1}", "contact:http_429:rate_limited"),
                ])
                backoff = self.h.log_fields("publish.rate_limit.backoff")
                self.assertEqual(backoff["wait_s"], wait)

    def test_error_in_one_row_does_not_stop_the_batch(self):
        self.h.raise_on["upsert_order"] = ValueError("boom")
        result = self.run_rows(H.contact_row(), H.order_row(), H.event_row())
        self.assertEqual([t[0] for t in self.h.outbox_marks()], ["db.sent", "db.retry", "db.sent"])
        self.assertEqual(result["sent"], 2)
        self.assertEqual(result["retry_scheduled"], 1)


class RetryCapTests(PublisherTestCase):
    """CURRENT BEHAVIOR (Phase 1 "maximum retry policy" will change this): no attempt limit."""

    def test_retryable_errors_retry_forever(self):
        self.h.raise_on["upsert_contact"] = http_error(500)
        self.run_rows(H.contact_row(attempts=99))
        self.assertEqual(self.h.outbox_marks(), [("db.retry", 1, 100, "next@100", "contact:http_500:server_error")])

    def test_missing_ids_retry_forever(self):
        self.run_rows(H.event_seat_row(attempts=99))
        self.assertEqual(self.h.outbox_marks(),
                         [("db.retry", 8, 100, "next@100", "missing_hubspot_event_or_seat_id")])


class SchemaSetupFallbackTests(PublisherTestCase):
    def test_contact_setup_failure_is_logged_and_processing_continues(self):
        self.h.provision_errors["contact"] = RuntimeError("setup down")
        self.run_rows(H.contact_row())
        event, level, fields = self.h.logs[1]
        self.assertEqual((event, level), ("publish.warn.contact_provision_failed", "WARN"))
        self.assertEqual(fields, {"portal_id": H.PORTAL_ID, "error": "setup down"})
        self.assertEqual(self.h.outbox_marks(), [("db.sent", 1, H.CONTACT_HS_ID)])

    def test_order_setup_failure_keeps_configured_association_type(self):
        self.h.provision_errors["order"] = RuntimeError("setup down")
        self.run_rows(H.contact_row(), H.order_row(), H.association_row())
        self.assertIn("publish.warn.order_provision_failed", self.h.log_events())
        self.assertIn(
            ("hs.assoc_v3", H.ORDER_TYPE, H.ORDER_HS_ID, H.CONTACT_HS_ID, H.CONFIG_ORDER_CONTACT_ASSOC),
            self.h.trace,
        )

    def test_discovered_association_type_replaces_config_even_when_none(self):
        # CURRENT BEHAVIOR: whatever ensure_order_properties returns is used, including None.
        self.h.discovered_order_assoc = None
        self.run_rows(H.contact_row(), H.order_row(), H.association_row())
        self.assertIn(("hs.assoc_v3", H.ORDER_TYPE, H.ORDER_HS_ID, H.CONTACT_HS_ID, None), self.h.trace)

    def test_event_setup_http_error(self):
        self.h.provision_errors["event"] = http_error(403)
        self.run_rows(H.event_row(), H.seat_row())
        event, level, fields = next(l for l in self.h.logs if l[0] == "publish.warn.event_provision_failed")
        self.assertEqual(fields, {"portal_id": H.PORTAL_ID, "status": 403})
        self.assertIn(("hs.ensure_seat", H.ORDER_TYPE, None), self.h.trace)
        self.assertEqual(self.h.outbox_marks()[0], ("db.retry", 4, 1, "next@1", "event_object_not_provisioned"))

    def test_event_setup_other_error(self):
        self.h.provision_errors["event"] = ValueError("bad schema")
        self.run_rows(H.event_row())
        fields = self.h.log_fields("publish.warn.event_provision_failed")
        self.assertEqual(fields, {"portal_id": H.PORTAL_ID, "error": "bad schema"})

    def test_seat_setup_failure(self):
        for exc, expected in [(http_error(500), {"portal_id": H.PORTAL_ID, "status": 500}),
                              (ValueError("x"), {"portal_id": H.PORTAL_ID, "error": "x"})]:
            with self.subTest(exc=type(exc).__name__):
                self.setUp()
                self.h.provision_errors["seat"] = exc
                self.run_rows(H.seat_row())
                self.assertEqual(self.h.log_fields("publish.warn.seat_provision_failed"), expected)
                self.assertEqual(self.h.outbox_marks(), [("db.retry", 6, 1, "next@1", "seat_object_not_provisioned")])


class EventPropertyTests(PublisherTestCase):
    def _event_props(self):
        return self.h.custom_props[(H.EVENT_TYPE, H.EVENT_ID)]

    def test_event_props_from_cached_row(self):
        self.run_rows(H.event_row())
        self.assertEqual(self._event_props(), {
            "tm_event_id": "EV1",
            "tm_event_name": "Spring Gala: 04/10/2026 12:30am",
            "tm_event_venue_name": "Main Hall",
            "tm_event_city": "Springfield",
            "tm_event_state": "IL",
            "tm_event_product_type": "Event",
            "tm_event_subsystem_id": "SYS1",
            "tm_event_subsystem_name": "Host",
            "tm_event_attraction_ids": "A1,A2",
            "tm_event_venue_ids": "V1",
            "tm_event_attraction_names": "The Band",
            "tm_event_country_code": "US",
            "tm_event_url": "https://events.test/ev1",
            "tm_event_timezone": "America/Chicago",
            "tm_event_status_code": "onsale",
            "tm_event_show_type": "Evening",   # 00:30 UTC = 19:30 local (UTC-5)
            "tm_event_start_datetime": H.epoch_ms(H.EVENT_START),
        })
        debug = self.h.log_fields("publish.event.debug")
        self.assertEqual(debug["props"], self._event_props())
        self.assertTrue(debug["cached_has_row"] and debug["cached_name"] and debug["cached_venue_name"])

    def test_normalized_payload_fallback_and_precedence(self):
        self.h.tm_events[H.EVENT_ID] = {
            "name": None,
            "start_datetime": None,
            "venue_name": "Cached Hall",
            "payload": {"_normalized": {
                "name": "Norm Name",
                "start_datetime": "2026-04-10T21:00:00Z",
                "venue_name": "Norm Hall",
                "timezone": "America/Chicago",
                "attraction_ids": ["N1", " ", "N1"],
            }},
        }
        self.run_rows(H.event_row())
        self.assertEqual(self._event_props(), {
            "tm_event_id": "EV1",
            "tm_event_name": "Norm Name: 04/10/2026 9:00pm",
            "tm_event_venue_name": "Cached Hall",           # cached column beats _normalized
            "tm_event_attraction_ids": "N1",
            "tm_event_timezone": "America/Chicago",
            "tm_event_show_type": "Matinee",                # 21:00 UTC = 16:00 local
            "tm_event_start_datetime": H.epoch_ms(dt.datetime(2026, 4, 10, 21, 0, tzinfo=dt.timezone.utc)),
        })

    def test_display_name_formatting(self):
        cases = [((0, 0), "12:00am"), ((9, 7), "9:07am"), ((12, 0), "12:00pm"), ((13, 5), "1:05pm"), ((23, 59), "11:59pm")]
        for (hour, minute), suffix in cases:
            with self.subTest(hour=hour, minute=minute):
                self.setUp()
                start = dt.datetime(2026, 1, 2, hour, minute, tzinfo=dt.timezone.utc)
                self.h.tm_events[H.EVENT_ID] = {"name": "Show", "start_datetime": start}
                self.run_rows(H.event_row())
                self.assertEqual(self._event_props()["tm_event_name"], f"Show: 01/02/2026 {suffix}")

    def test_show_type_boundary(self):
        for utc_hour, expected in [(21, "Matinee"), (22, "Evening")]:  # 16:59/17:00 local at UTC-5
            with self.subTest(utc_hour=utc_hour):
                self.setUp()
                start = dt.datetime(2026, 4, 10, utc_hour, 0 if utc_hour == 22 else 59, tzinfo=dt.timezone.utc)
                self.h.tm_events[H.EVENT_ID] = {"name": "Show", "start_datetime": start, "timezone": "America/Chicago"}
                self.run_rows(H.event_row())
                self.assertEqual(self._event_props()["tm_event_show_type"], expected)

    def test_show_type_omitted_when_time_zone_cannot_be_resolved(self):
        # CURRENT BEHAVIOR: without a tz database (e.g. no `tzdata`), Show Type is silently omitted.
        import zoneinfo

        def unavailable(key):
            raise zoneinfo.ZoneInfoNotFoundError(key)

        with mock.patch("zoneinfo.ZoneInfo", unavailable):
            self.run_rows(H.event_row())
        self.assertNotIn("tm_event_show_type", self._event_props())
        self.assertEqual(self._event_props()["tm_event_name"], "Spring Gala: 04/10/2026 12:30am")

    def test_no_cached_data_retries_until_five_attempts(self):
        self.h.tm_events = {}
        for attempts in range(5):
            with self.subTest(attempts=attempts):
                self.setUp()
                self.h.tm_events = {}
                self.run_rows(H.event_row(attempts=attempts))
                self.assertEqual(self.h.outbox_marks(),
                                 [("db.retry", 4, attempts + 1, f"next@{attempts + 1}", "event_no_cached_data")])
                self.assertIn("publish.event.retry_no_cached_data", self.h.log_events())

    def test_no_cached_data_after_five_attempts_publishes_fallback_name(self):
        self.h.tm_events = {}
        self.run_rows(H.event_row(attempts=5))
        self.assertEqual(self._event_props(), {"tm_event_id": "EV1", "tm_event_name": "TM Event EV1"})
        self.assertEqual(self.h.outbox_marks(), [("db.sent", 4, H.EVENT_HS_ID)])


class SeatPropertyTests(PublisherTestCase):
    def test_seat_props(self):
        self.run_rows(H.seat_row())
        self.assertEqual(self.h.custom_props[(H.SEAT_TYPE, H.SEAT_ID)], {
            "seat_unique_id": H.SEAT_ID,
            "seat_name": "The Band Sec 101 Row B Seat 7",
            "seat_location": "Sec 101 Row B Seat 7",
            "seat_tm1_order_id": H.ORDER_ID,
            "seat_tm_event_id": "EV1",
            "seat_type": "TICKET",
            "seat_sub_type": "ADULT",
            "seat_status": "SOLD",
            "seat_accessible": "False",
            "seat_transaction_datetime": H.epoch_ms(dt.datetime(2026, 3, 2, 18, 31, tzinfo=dt.timezone.utc)),
            "seat_gate": "G2",
            "seat_level": "Floor",
            "seat_section": "101",
            "seat_row": "B",
            "seat_number": "7",
            "seat_face_value": 45.5,
            "seat_currency": "USD",
            "seat_price_type": "STANDARD",
            "seat_price_level": "PL1",
            "seat_offers": "PROMO,PROMO2",
            "seat_is_upsell": "False",
        })

    def test_seat_without_event_or_attraction_uses_location_only(self):
        seat = dict(H.SEAT, tm_event_id=None, ticket_holder={"name": "x"}, face_value="not-a-number", offers=[])
        self.run_rows(H.seat_row(seat=seat))
        props = self.h.custom_props[(H.SEAT_TYPE, H.SEAT_ID)]
        self.assertEqual(props["seat_name"], "Sec 101 Row B Seat 7")
        self.assertNotIn("seat_tm_event_id", props)
        self.assertNotIn("seat_face_value", props)
        self.assertNotIn("seat_offers", props)
        self.assertEqual(props["seat_ticket_holder"], "{'name': 'x'}")  # str() of the raw value
        self.assertNotIn(("db.tm_event", H.EVENT_ID), self.h.trace)

    def test_seat_label_fallbacks(self):
        cases = [
            (dict(section=None, row=None, seat=None, gate="G9"), "Gate G9"),
            (dict(section=None, row=None, seat=None, gate=None, level="Upper"), "Level Upper"),
            (dict(section=None, row=None, seat=None, gate=None, level=None), H.SEAT_ID),
        ]
        for overrides, location in cases:
            with self.subTest(location=location):
                self.setUp()
                self.h.tm_events = {}
                self.run_rows(H.seat_row(seat=dict(H.SEAT, **overrides)))
                props = self.h.custom_props[(H.SEAT_TYPE, H.SEAT_ID)]
                self.assertEqual(props["seat_location"], location)
                self.assertEqual(props["seat_name"], location)


if __name__ == "__main__":
    unittest.main()

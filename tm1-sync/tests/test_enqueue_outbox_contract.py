"""
Outbox contract written by sync.enqueue.enqueue_from_normalized_batch: entity values,
dedupe keys, payload shapes and write order. Rows already queued in production depend on
these, so they must not change. Synthetic data only.
"""
import unittest
from unittest import mock

import sync.enqueue as enqueue
from sync.seat_extract import extract_seats

ORDER_ID = "1001|2026-03-02T18:30:00Z"
EMAIL = " Jane.Doe@Example.test "
EMAIL_STRIPPED = "Jane.Doe@Example.test"
EMAIL_NORM = "jane.doe@example.test"


def order_payload(customer=None):
    return {
        "id": "1001",
        "creationDateTime": "2026-03-02T18:30:00Z",
        "customer": customer if customer is not None else {"email": EMAIL, "firstName": "Jane", "piiCleared": False},
        "itemGroups": [
            {"event": {"id": "EV1"}, "items": [{"location": {"section": "101"}}, {"location": {"section": "102"}}]},
            {"event": {"id": "EV2"}, "_embedded": {"items": [{"location": {"section": "201"}}]}},
        ],
    }


class EnqueueContractTests(unittest.TestCase):
    def setUp(self):
        self.writes = []
        self.inserted = True

        def fake_enqueue(client_id, entity, dedupe_key, payload):
            self.writes.append((client_id, entity, dedupe_key, payload))
            return self.inserted

        self.logs = []
        for name, value in {
            "enqueue_outbox": fake_enqueue,
            "log": lambda event, **f: self.logs.append(event),
            "log_item": lambda event, **f: self.logs.append(event),
        }.items():
            p = mock.patch.object(enqueue, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_batch(self, *items):
        return enqueue.enqueue_from_normalized_batch(client_id="c1", normalized_items=list(items))

    def test_full_order_rows_in_write_order(self):
        order = order_payload()
        self.run_batch((ORDER_ID, order))
        seats = extract_seats(ORDER_ID, order)
        s0, s1, s2 = (s["seat_unique_id"] for s in seats)
        self.assertEqual([s0, s1, s2], [f"{ORDER_ID}|0|0", f"{ORDER_ID}|0|1", f"{ORDER_ID}|1|0"])

        expected = [
            ("c1", "order", ORDER_ID, {"source": "tm1", "order": order}),
            # Only the primary (first) event is enqueued as an entity.
            ("c1", "event", "EV1", {"source": "tm_discovery", "tm_event_id": "EV1"}),
            ("c1", "order_event_association", f"{ORDER_ID}->EV1",
             {"source": "tm", "tm1_order_id": ORDER_ID, "tm_event_id": "EV1"}),
            ("c1", "contact", EMAIL_NORM, {"source": "tm1", "customer": order["customer"]}),
            ("c1", "association", f"{ORDER_ID}->{EMAIL_NORM}",
             {"source": "tm1", "tm1_order_id": ORDER_ID, "email": EMAIL_STRIPPED, "email_normalized": EMAIL_NORM}),
        ]
        for seat in seats:
            sid, ev = seat["seat_unique_id"], seat["tm_event_id"]
            expected += [
                ("c1", "seat", sid, {"source": "tm1", "seat": seat}),
                ("c1", "order_seat_association", f"{ORDER_ID}->{sid}",
                 {"source": "tm1", "tm1_order_id": ORDER_ID, "seat_unique_id": sid}),
                ("c1", "event_seat_association", f"{sid}->{ev}",
                 {"source": "tm1", "seat_unique_id": sid, "tm_event_id": ev}),
                ("c1", "contact_seat_association", f"{sid}->{EMAIL_NORM}",
                 {"source": "tm1", "seat_unique_id": sid, "email_normalized": EMAIL_NORM}),
            ]
        self.assertEqual(self.writes, expected)
        self.assertIn("event.multi_event_detected", self.logs)

    def test_secondary_event_seats_reference_an_event_that_is_not_enqueued(self):
        # CURRENT BEHAVIOR: seats in the EV2 group get an EV2 event_seat_association,
        # but no "event" row is enqueued for EV2.
        self.run_batch((ORDER_ID, order_payload()))
        event_rows = [w[2] for w in self.writes if w[1] == "event"]
        ev_seat_targets = {w[3]["tm_event_id"] for w in self.writes if w[1] == "event_seat_association"}
        self.assertEqual(event_rows, ["EV1"])
        self.assertEqual(ev_seat_targets, {"EV1", "EV2"})

    def test_pii_cleared_customer_skips_contact_rows(self):
        counts = self.run_batch((ORDER_ID, order_payload({"email": EMAIL, "piiCleared": True})))
        entities = [w[1] for w in self.writes]
        self.assertNotIn("contact", entities)
        self.assertNotIn("association", entities)
        self.assertNotIn("contact_seat_association", entities)
        self.assertEqual(entities.count("seat"), 3)
        self.assertEqual(counts["contacts_skipped_piiCleared_true"], 1)

    def test_missing_email_skips_contact_rows(self):
        for customer in [{}, {"email": "  "}, {"email": None}]:
            with self.subTest(customer=customer):
                self.writes.clear()
                counts = self.run_batch((ORDER_ID, order_payload(customer)))
                entities = [w[1] for w in self.writes]
                self.assertNotIn("contact", entities)
                self.assertNotIn("contact_seat_association", entities)
                self.assertEqual(counts["contacts_skipped_missing_email"], 1)

    def test_order_without_events_or_seats(self):
        counts = self.run_batch((ORDER_ID, {"customer": {"email": EMAIL}}))
        self.assertEqual([w[1] for w in self.writes], ["order", "contact", "association"])
        self.assertEqual(counts["orders_missing_event"], 1)

    def test_counts_and_dedupe_skips(self):
        counts = self.run_batch((ORDER_ID, order_payload()))
        self.assertEqual(counts, {
            "orders_enqueued": 1, "orders_skipped_dedupe": 0,
            "contacts_enqueued": 1, "contacts_skipped_dedupe": 0,
            "associations_enqueued": 1, "associations_skipped_dedupe": 0,
            "events_enqueued": 1, "events_skipped_dedupe": 0,
            "order_event_assoc_enqueued": 1, "order_event_assoc_skipped_dedupe": 0,
            "orders_missing_event": 0, "multi_event_orders_detected": 1,
            "seats_enqueued": 3, "seats_skipped_dedupe": 0,
            "order_seat_assoc_enqueued": 3, "order_seat_assoc_skipped_dedupe": 0,
            "event_seat_assoc_enqueued": 3, "event_seat_assoc_skipped_dedupe": 0,
            "contact_seat_assoc_enqueued": 3, "contact_seat_assoc_skipped_dedupe": 0,
            "contacts_skipped_piiCleared_true": 0, "contacts_skipped_missing_email": 0,
            "total_items": 1,
        })
        self.inserted = False
        skipped = self.run_batch((ORDER_ID, order_payload()))
        self.assertEqual(skipped["orders_skipped_dedupe"], 1)
        self.assertEqual(skipped["seats_skipped_dedupe"], 3)
        self.assertEqual(skipped["contact_seat_assoc_skipped_dedupe"], 3)

    def test_empty_batch(self):
        counts = self.run_batch()
        self.assertEqual(self.writes, [])
        self.assertEqual(counts["total_items"], 0)
        self.assertEqual(self.logs, ["enqueue.noop"])


if __name__ == "__main__":
    unittest.main()

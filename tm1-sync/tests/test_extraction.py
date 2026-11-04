"""
Unit tests for TM1 order parsing: event ids, seats and seat labels.

Seat unique ids are Outbox dedupe keys and HubSpot unique properties, so their format is
a contract. Synthetic data only.
"""
import unittest

from sync.event_extract import choose_primary_event_id, extract_event_ids
from sync.seat_extract import build_seat_label, extract_seats

ORDER_ID = "1001|2026-03-02T18:30:00Z"


def item(**overrides):
    base = {
        "type": "TICKET",
        "subType": "ADULT",
        "status": "SOLD",
        "ticketHolder": None,
        "transactionDatetime": "2026-03-02T18:31:00Z",
        "accessible": False,
        "location": {"gate": "G2", "level": "Floor", "placeGroup": "PG", "section": "101", "row": "B", "seat": "7"},
        "price": {"faceValue": "45.50", "currency": "USD", "type": "STANDARD", "level": "PL1"},
        "offers": [{"type": "PROMO"}, {"type": " PROMO2 "}, {"type": ""}, {"name": "no-type"}, "bad"],
    }
    base.update(overrides)
    return base


class EventExtractTests(unittest.TestCase):
    def test_distinct_ids_in_stable_order(self):
        order = {"itemGroups": [
            {"event": {"id": " EV2 "}}, {"event": {"id": "EV1"}}, {"event": {"id": "EV2"}}, {"event": {"id": "EV3"}},
        ]}
        self.assertEqual(extract_event_ids(order), ["EV2", "EV1", "EV3"])

    def test_ignores_malformed_entries(self):
        order = {"itemGroups": [
            "not-a-dict", {"event": "not-a-dict"}, {"event": {"id": 123}}, {"event": {"id": "   "}},
            {"event": None}, {}, {"event": {"id": "EV1"}},
        ]}
        self.assertEqual(extract_event_ids(order), ["EV1"])

    def test_missing_or_invalid_item_groups(self):
        for order in [{}, {"itemGroups": None}, {"itemGroups": "x"}, {"itemGroups": {}}]:
            with self.subTest(order=order):
                self.assertEqual(extract_event_ids(order), [])

    def test_primary_event_is_the_first(self):
        self.assertEqual(choose_primary_event_id(["EV2", "EV1"]), "EV2")
        self.assertEqual(choose_primary_event_id([]), "")


class SeatExtractTests(unittest.TestCase):
    def test_full_seat_record(self):
        order = {"itemGroups": [{"event": {"id": " EV1 "}, "upsell": False, "items": [item()]}]}
        self.assertEqual(extract_seats(ORDER_ID, order), [{
            "seat_unique_id": f"{ORDER_ID}|0|0",
            "tm1_order_id": ORDER_ID,
            "tm_event_id": "EV1",
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
            "place_group": "PG",
            "section": "101",
            "row": "B",
            "seat": "7",
            "face_value": 45.5,
            "currency": "USD",
            "price_type": "STANDARD",
            "price_level": "PL1",
            "offers": ["PROMO", "PROMO2"],
            "is_upsell": False,
        }])

    def test_unique_id_uses_group_and_item_positions(self):
        order = {"itemGroups": [
            {"event": {"id": "EV1"}, "items": [item(), item()]},
            "skipped-but-counted",
            {"event": {"id": "EV2"}, "_embedded": {"items": ["bad", item()]}},
        ]}
        seats = extract_seats(ORDER_ID, order)
        self.assertEqual([s["seat_unique_id"] for s in seats],
                         [f"{ORDER_ID}|0|0", f"{ORDER_ID}|0|1", f"{ORDER_ID}|2|1"])
        self.assertEqual([s["tm_event_id"] for s in seats], ["EV1", "EV1", "EV2"])

    def test_items_take_precedence_over_embedded_items(self):
        order = {"itemGroups": [{"items": [item(status="A")], "_embedded": {"items": [item(status="B")]}}]}
        self.assertEqual([s["status"] for s in extract_seats(ORDER_ID, order)], ["A"])

    def test_upsell_flag_and_missing_event(self):
        order = {"itemGroups": [{"upsell": 1, "items": [item()]}]}
        seat = extract_seats(ORDER_ID, order)[0]
        self.assertIs(seat["is_upsell"], True)
        self.assertIsNone(seat["tm_event_id"])

    def test_coercions_of_malformed_values(self):
        order = {"itemGroups": [{"items": [item(
            type="  ", status=5, location="bad", price={"faceValue": "n/a"}, offers="bad",
        )]}]}
        seat = extract_seats(ORDER_ID, order)[0]
        self.assertIsNone(seat["type"])
        self.assertIsNone(seat["status"])
        self.assertIsNone(seat["section"])
        self.assertIsNone(seat["face_value"])
        self.assertEqual(seat["offers"], [])

    def test_invalid_structures(self):
        for order in [{}, {"itemGroups": "x"}, {"itemGroups": [{"items": "x"}]}, {"itemGroups": [{"_embedded": "x"}]}]:
            with self.subTest(order=order):
                self.assertEqual(extract_seats(ORDER_ID, order), [])


class SeatLabelTests(unittest.TestCase):
    def test_label_variants(self):
        cases = [
            ({"section": "101", "row": "B", "seat": "7"}, "Sec 101 Row B Seat 7"),
            ({"section": "101"}, "Sec 101"),
            ({"row": "B", "seat": "7", "gate": "G1"}, "Row B Seat 7"),
            ({"gate": "G1", "level": "Upper"}, "Gate G1"),
            ({"level": "Upper"}, "Level Upper"),
            ({"seat_unique_id": "X|0|0"}, "X|0|0"),
            ({}, "Unknown Seat"),
        ]
        for seat, label in cases:
            with self.subTest(label=label):
                self.assertEqual(build_seat_label(seat), label)


if __name__ == "__main__":
    unittest.main()

"""
Discovery API event payload -> normalized fields (stored in tm_events and under
payload["_normalized"], read back by the publisher). Synthetic data only.
"""
import unittest

from sync.events_fetch import _build_enriched_payload, _extract_fields

PAYLOAD = {
    "id": "EV1",
    "name": "Spring Gala",
    "type": "event",
    "locale": "en-us",
    "url": "https://events.test/ev1",
    "productType": "Event",
    "pleaseNote": "No bags",
    "info": "Doors at 7",
    "dates": {
        "start": {"dateTime": "2026-04-10T00:30:00Z", "localDate": "2026-04-09", "localTime": "19:30:00"},
        "timezone": "America/Chicago",
        "status": {"code": "onsale"},
    },
    "sales": {"public": {"startDateTime": "2026-01-01T15:00:00Z", "endDateTime": "2026-04-10T00:00:00Z"}},
    "subsystem": {"id": "SYS1", "name": "Host"},
    "_embedded": {
        "venues": [
            {"id": "V1", "name": "Main Hall", "city": {"name": "Springfield"},
             "state": {"stateCode": "IL"}, "country": {"countryCode": "US"}},
            {"id": "V2", "name": "Annex"},
        ],
        "attractions": [{"id": "A1", "name": "The Band"}, {"id": "A1", "name": "The Band"}, {"id": " ", "name": "Opener"}],
    },
    "_links": {
        "attractions": [{"href": "/discovery/v2/attractions/A2?locale=en-us"}, {"href": "/discovery/v2/attractions/A1/"}],
        "venues": [{"href": "/discovery/v2/venues/V3"}, {"bad": 1}],
    },
}

EXPECTED = {
    "discovery_event_id": "EV1",
    "name": "Spring Gala",
    "event_type": "event",
    "locale": "en-us",
    "event_url": "https://events.test/ev1",
    "start_datetime": "2026-04-10T00:30:00Z",
    "local_date": "2026-04-09",
    "local_time": "19:30:00",
    "timezone": "America/Chicago",
    "status_code": "onsale",
    "sales_public_start_datetime": "2026-01-01T15:00:00Z",
    "sales_public_end_datetime": "2026-04-10T00:00:00Z",
    "venue_id": "V1",
    "venue_name": "Main Hall",
    "city": "Springfield",
    "state": "IL",
    "country_code": "US",
    "product_type": "Event",
    "attraction_ids": ["A1", "A2"],        # embedded first, then links, de-duplicated
    "attraction_names": ["The Band", "Opener"],
    "venue_ids": ["V1", "V2", "V3"],
    "subsystem_id": "SYS1",
    "subsystem_name": "Host",
    "please_note": "No bags",
    "info": "Doors at 7",
}


class DiscoveryFieldTests(unittest.TestCase):
    def test_extract_fields(self):
        self.assertEqual(_extract_fields(PAYLOAD), EXPECTED)

    def test_extract_fields_from_empty_or_malformed_payload(self):
        empty = _extract_fields({"name": 5, "dates": "x", "_embedded": {"venues": ["bad"]}, "_links": "x"})
        self.assertEqual(empty["attraction_ids"], [])
        self.assertEqual(empty["venue_ids"], [])
        self.assertTrue(all(v is None for k, v in empty.items() if k not in ("attraction_ids", "attraction_names", "venue_ids")))

    def test_enriched_payload_keeps_original_and_adds_normalized(self):
        enriched = _build_enriched_payload(PAYLOAD, EXPECTED)
        self.assertEqual({k: v for k, v in enriched.items() if k != "_normalized"}, PAYLOAD)
        self.assertEqual(enriched["_normalized"], EXPECTED)
        self.assertNotIn("_normalized", PAYLOAD)  # input not mutated

    def test_enriched_payload_lists_default_to_empty(self):
        normalized = _build_enriched_payload({}, {})["_normalized"]
        self.assertEqual((normalized["attraction_ids"], normalized["attraction_names"], normalized["venue_ids"]), ([], [], []))
        self.assertIsNone(normalized["name"])


if __name__ == "__main__":
    unittest.main()

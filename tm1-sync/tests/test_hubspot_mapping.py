"""
Unit tests for TM1 snapshot -> HubSpot property mapping (contacts and orders) and the
date helpers they use. Event and seat props are built inside the publisher and are
covered by test_publisher_characterization.py. Synthetic data only.
"""
import datetime as dt
import unittest

from sync.hubspot_client import (
    build_contact_props,
    build_order_props,
    datetime_to_ms,
    iso_to_ms,
    safe_float,
)


def ms(*args):
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp() * 1000)


ORDER_ID = "1001|2026-03-02T18:30:00Z"


class ContactPropsTests(unittest.TestCase):
    def test_full_customer(self):
        customer = {
            "email": "  jane.doe@example.test ",
            "firstName": " Jane ",
            "lastName": "Doe",
            "middleName": "Q",
            "prefix": "Dr",
            "gender": "F",
            "preferredLanguage": "en",
            "dateOfBirth": "1990-05-17",
            "phones": [
                {"number": "555-0100", "type": "MobilePhone"},
                {"number": "555-0101", "type": "eveningPhone"},
                {"number": "555-0102", "type": "home"},
                {"number": "555-0103", "type": "work"},
                {"number": "  ", "type": "home"},
                "bad",
            ],
            "marketingConsent": {"optin": 1},
            "address": {
                "line1": " 1 Main St ", "line2": "Apt 2", "line3": "  ", "city": "Springfield",
                "state": "IL", "country": "US", "postalCode": "62701",
            },
        }
        self.assertEqual(build_contact_props(customer), {
            "email": "jane.doe@example.test",
            "firstname": "Jane",
            "lastname": "Doe",
            "middle_name": "Q",
            "salutation": "Dr",
            "gender": "F",
            "hs_language": "en",
            "date_of_birth": ms(1990, 5, 17),
            "mobilephone": "555-0100",
            "eveningphone": "555-0101",
            "phone": "555-0102",          # first "other" phone wins
            "tm1_marketingconsent": True,
            "address": "1 Main St",
            "address_2": "Apt 2",
            "city": "Springfield",
            "state": "IL",
            "country": "US",
            "zip": "62701",
        })

    def test_minimal_and_missing_values(self):
        self.assertEqual(build_contact_props({}), {})
        self.assertEqual(build_contact_props({"email": None, "phones": "bad", "address": "bad"}), {})

    def test_date_of_birth_variants(self):
        self.assertEqual(build_contact_props({"dateofbirth": "1990-05-17"})["date_of_birth"], ms(1990, 5, 17))
        self.assertNotIn("date_of_birth", build_contact_props({"dateOfBirth": "not a date"}))
        self.assertNotIn("date_of_birth", build_contact_props({"dateOfBirth": 19900517}))

    def test_marketing_consent_false_is_kept(self):
        self.assertIs(build_contact_props({"marketingConsent": {"optin": False}})["tm1_marketingconsent"], False)
        self.assertNotIn("tm1_marketingconsent", build_contact_props({"marketingConsent": {}}))


class OrderPropsTests(unittest.TestCase):
    def test_full_order(self):
        order = {
            "type": " SALE ",
            "version": 3,
            "creationDateTime": "2026-03-02T18:30:00Z",
            "customer": {"firstName": "Jane", "lastName": "Doe"},
            "salesChannel": {"group": "WEB", "detail": " Online "},
            "totals": {"price": "100.00", "taxes": 8, "total": "113.5", "fee": "5.5",
                       "deliveries": 0, "upsells": "bad", "currency": "USD"},
            "operator": "OP1",
            "deliveries": [{"type": "MOBILE"}, {"type": "MAIL"}],
            "payments": [{"type": "CARD", "brand": "VISA"}],
            "itemGroups": [
                {"items": [{"transactionDatetime": "  "}]},
                {"_embedded": {"items": [{"transactionDatetime": "2026-03-02T18:31:00Z"},
                                         {"transactionDatetime": "2026-03-02T18:40:00Z"}]}},
            ],
        }
        self.assertEqual(build_order_props(order, ORDER_ID), {
            "tm1_unique_id": ORDER_ID,
            "tm1_order_id": "1001",
            "hs_order_name": "Jane Doe - Order - 03/02/2026",
            "tm1_type": "SALE",
            "tm1_version": "3",
            "hs_external_created_date": ms(2026, 3, 2, 18, 30),
            "tm1_sales_channel_group": "WEB",
            "tm1_sales_channel_detail": "Online",
            "hs_subtotal_price": 100.0,
            "hs_tax": 8.0,
            "hs_total_price": 113.5,
            "tm1_fee": 5.5,
            "tm1_deliveries": 0.0,
            "tm1_operator_code": "OP1",
            "tm1_delivery_type": "MOBILE",
            "hs_payment_processing_method": "CARD",
            "tm1_payment_brand": "VISA",
            "tm1_currency": "USD",
            "tm1_transaction_datetime": ms(2026, 3, 2, 18, 31),  # first non-blank transaction
        })

    def test_order_id_variants(self):
        self.assertEqual(build_order_props({}, "1001.5|2026-03-02T18:30:00Z")["tm1_order_id"], "1001")
        props = build_order_props({}, "1001")
        self.assertEqual(props["tm1_order_id"], "1001")
        self.assertNotIn("hs_order_name", props)  # no date part

    def test_order_name_without_customer_name(self):
        self.assertEqual(build_order_props({}, ORDER_ID)["hs_order_name"], "Unknown - Order - 03/02/2026")

    def test_empty_order(self):
        self.assertEqual(build_order_props({}, ORDER_ID), {
            "tm1_unique_id": ORDER_ID,
            "tm1_order_id": "1001",
            "hs_order_name": "Unknown - Order - 03/02/2026",
        })


class DateHelperTests(unittest.TestCase):
    def test_iso_to_ms(self):
        self.assertEqual(iso_to_ms("2026-03-02T18:30:00Z"), ms(2026, 3, 2, 18, 30))
        self.assertEqual(iso_to_ms("2026-03-02T18:30:00"), ms(2026, 3, 2, 18, 30))  # naive = UTC
        self.assertEqual(iso_to_ms("2026-03-02T13:30:00-05:00"), ms(2026, 3, 2, 18, 30))
        for bad in [None, "", "nope", 123]:
            self.assertIsNone(iso_to_ms(bad))

    def test_datetime_to_ms(self):
        aware = dt.datetime(2026, 3, 2, 18, 30, tzinfo=dt.timezone.utc)
        self.assertEqual(datetime_to_ms(aware), ms(2026, 3, 2, 18, 30))
        self.assertEqual(datetime_to_ms(dt.datetime(2026, 3, 2, 18, 30)), ms(2026, 3, 2, 18, 30))
        self.assertEqual(datetime_to_ms("2026-03-02T18:30:00Z"), ms(2026, 3, 2, 18, 30))
        for bad in [None, "nope", 123]:
            self.assertIsNone(datetime_to_ms(bad))

    def test_safe_float(self):
        self.assertEqual(safe_float("1.5"), 1.5)
        self.assertEqual(safe_float(2), 2.0)
        self.assertIsNone(safe_float(None))
        self.assertIsNone(safe_float("x"))


if __name__ == "__main__":
    unittest.main()

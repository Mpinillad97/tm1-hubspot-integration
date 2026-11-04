"""
HubSpot HTTP contract of sync.hubspot_client: verbs, URLs, JSON bodies, headers, timeout,
response parsing and error propagation. `requests` is replaced by a recording fake; no
network access.
"""
import unittest
from unittest import mock

import requests

from sync import hubspot_client as hc

BASE = "https://hubspot.test/"  # trailing slash is stripped by every function
TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


class FakeResponse:
    def __init__(self, status=200, json_body=None):
        self.status_code = status
        self._json = json_body if json_body is not None else {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            resp = requests.Response()
            resp.status_code = self.status_code
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}", response=resp)


class FakeRequests:
    def __init__(self):
        self.calls = []
        self.responses = []  # queue; default 200 {}

    def _call(self, verb, url, **kwargs):
        self.calls.append((verb, url, kwargs))
        return self.responses.pop(0) if self.responses else FakeResponse()

    def post(self, url, **kw):
        return self._call("POST", url, **kw)

    def patch(self, url, **kw):
        return self._call("PATCH", url, **kw)

    def get(self, url, **kw):
        return self._call("GET", url, **kw)


class HubSpotClientContractTests(unittest.TestCase):
    def setUp(self):
        self.http = FakeRequests()
        for verb in ("post", "patch", "get"):
            p = mock.patch.object(hc.requests, verb, getattr(self.http, verb))
            p.start()
            self.addCleanup(p.stop)

    def assertCall(self, index, verb, path, json=None):
        v, url, kw = self.http.calls[index]
        self.assertEqual((v, url), (verb, f"https://hubspot.test{path}"))
        self.assertEqual(kw["headers"], HEADERS)
        self.assertEqual(kw["timeout"], 45)
        if json is not None or verb != "GET":
            self.assertEqual(kw.get("json"), json)

    def test_search_object_id_by_property(self):
        self.http.responses = [FakeResponse(json_body={"results": [{"id": "77"}, {"id": "78"}]})]
        self.assertEqual(hc.search_object_id_by_property(BASE, TOKEN, "contacts", "email", "a@example.test"), "77")
        self.assertCall(0, "POST", "/crm/v3/objects/contacts/search", {
            "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": "a@example.test"}]}],
            "properties": ["email"],
            "limit": 1,
        })

    def test_search_without_results(self):
        self.http.responses = [FakeResponse(json_body={"results": []})]
        self.assertIsNone(hc.search_object_id_by_property(BASE, TOKEN, "orders", "tm1_unique_id", "x"))

    def test_create_and_update(self):
        self.http.responses = [FakeResponse(json_body={"id": "9"})]
        self.assertEqual(hc.create_object(BASE, TOKEN, "2-1", {"a": 1}), "9")
        hc.update_object(BASE, TOKEN, "2-1", "9", {"a": 2})
        self.assertCall(0, "POST", "/crm/v3/objects/2-1", {"properties": {"a": 1}})
        self.assertCall(1, "PATCH", "/crm/v3/objects/2-1/9", {"properties": {"a": 2}})

    def test_upsert_contact_updates_existing(self):
        self.http.responses = [FakeResponse(json_body={"results": [{"id": "5"}]})]
        result = hc.upsert_contact_by_email(BASE, TOKEN, "a@example.test", {"email": "a@example.test", "firstName": "A"})
        self.assertEqual(result, ("updated", "5"))
        self.assertCall(1, "PATCH", "/crm/v3/objects/contacts/5",
                        {"properties": {"email": "a@example.test", "firstname": "A"}})

    def test_upsert_contact_creates_missing(self):
        self.http.responses = [FakeResponse(json_body={"results": []}), FakeResponse(json_body={"id": "6"})]
        self.assertEqual(hc.upsert_contact_by_email(BASE, TOKEN, "a@example.test", {"email": "a@example.test"}),
                         ("created", "6"))
        self.assertCall(1, "POST", "/crm/v3/objects/contacts", {"properties": {"email": "a@example.test"}})

    def test_upsert_order_searches_by_unique_id(self):
        self.http.responses = [FakeResponse(json_body={"results": []}), FakeResponse(json_body={"id": "8"})]
        result = hc.upsert_order_by_tm1_order_id(BASE, TOKEN, "orders", "1001", {})
        self.assertEqual(result, ("created", "8"))
        self.assertEqual(self.http.calls[0][2]["json"]["filterGroups"][0]["filters"][0],
                         {"propertyName": "tm1_unique_id", "operator": "EQ", "value": "1001"})
        self.assertCall(1, "POST", "/crm/v3/objects/orders",
                        {"properties": {"tm1_unique_id": "1001", "tm1_order_id": "1001"}})

    def test_upsert_custom_object_returns_string_ids(self):
        self.http.responses = [FakeResponse(json_body={"results": [{"id": 12}]})]
        self.assertEqual(hc.upsert_custom_object_by_unique_property(BASE, TOKEN, "2-1", "k", "v", {"x": 1}),
                         ("updated", "12"))
        self.http.responses = [FakeResponse(json_body={"results": []}), FakeResponse(json_body={"id": 13})]
        self.assertEqual(hc.upsert_custom_object_by_unique_property(BASE, TOKEN, "2-1", "k", "v", {"x": 1}),
                         ("created", "13"))

    def test_associate_order_to_contact_v3(self):
        hc.associate_order_to_contact(BASE, TOKEN, "orders", 1, 2, 508)
        self.assertCall(0, "POST", "/crm/v3/associations/orders/contacts/batch/create",
                        {"inputs": [{"from": {"id": "1"}, "to": {"id": "2"}, "type": "508"}]})

    def test_associate_v4_labeled(self):
        hc.associate_objects_v4_labeled_batch(BASE, TOKEN, "2-1", "2-2", 1, 2, "702")
        self.assertCall(0, "POST", "/crm/v4/associations/2-1/2-2/batch/create", {"inputs": [{
            "from": {"id": "1"}, "to": {"id": "2"},
            "types": [{"associationCategory": "USER_DEFINED", "associationTypeId": 702}],
        }]})

    def test_list_associations(self):
        self.http.responses = [FakeResponse(json_body={"results": [{"toObjectId": 10}, {"other": 1}, {"toObjectId": "11"}]})]
        self.assertEqual(hc.list_associations(BASE, TOKEN, "2-2", "5", "2-1"), ["10", "11"])
        self.assertCall(0, "GET", "/crm/v4/objects/2-2/5/associations/2-1")

    def test_delete_associations_batch(self):
        hc.delete_associations_batch(BASE, TOKEN, "2-2", "2-1", 5, ["10", 11])
        self.assertCall(0, "POST", "/crm/v4/associations/2-2/2-1/batch/archive", {"inputs": [
            {"from": {"id": "5"}, "to": [{"id": "10"}]},
            {"from": {"id": "5"}, "to": [{"id": "11"}]},
        ]})

    def test_delete_associations_with_no_ids_makes_no_call(self):
        hc.delete_associations_batch(BASE, TOKEN, "2-2", "2-1", 5, [])
        self.assertEqual(self.http.calls, [])

    def test_http_errors_propagate_with_status(self):
        cases = [
            lambda: hc.search_object_id_by_property(BASE, TOKEN, "contacts", "email", "x"),
            lambda: hc.create_object(BASE, TOKEN, "2-1", {}),
            lambda: hc.update_object(BASE, TOKEN, "2-1", "1", {}),
            lambda: hc.associate_order_to_contact(BASE, TOKEN, "orders", 1, 2, 3),
            lambda: hc.associate_objects_v4_labeled_batch(BASE, TOKEN, "a", "b", 1, 2, 3),
            lambda: hc.list_associations(BASE, TOKEN, "a", "1", "b"),
            lambda: hc.delete_associations_batch(BASE, TOKEN, "a", "b", 1, ["2"]),
        ]
        for i, call in enumerate(cases):
            with self.subTest(case=i):
                self.http.responses = [FakeResponse(status=429)]
                with self.assertRaises(requests.exceptions.HTTPError) as ctx:
                    call()
                self.assertEqual(ctx.exception.response.status_code, 429)


if __name__ == "__main__":
    unittest.main()

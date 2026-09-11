"""Offline tests for the Zefix client and the field-inventory logic.

These run without credentials and without network access: urlopen is patched
and the payloads are fixtures shaped like the documented Zefix responses.
They verify request construction, auth, retry behaviour and the ownership
field detection that FINDINGS.md relies on.

    python -m unittest discover -s zefix -v
"""

from __future__ import annotations

import base64
import io
import json
import unittest
import urllib.error
from unittest import mock

from zefix.explore import collect_field_paths, OWNERSHIP_HINTS
from zefix.zefix_client import PROD_BASE_URL, ZefixClient, ZefixError

# A company record shaped like the documented Zefix payload. Note what is
# absent: there is no shareholder, member or officer collection.
COMPANY_FIXTURE = {
    "name": "Muster Handels AG",
    "uid": "CHE-105.805.185",
    "chid": "CH-020.3.926.379-0",
    "ehraid": 1234567,
    "legalFormId": 4,
    "legalForm": {"id": 4, "name": {"de": "Aktiengesellschaft", "en": "Limited company"}},
    "status": "EXISTIEREND",
    "purpose": "Handel mit Waren aller Art.",
    "capitalNominal": 100000,
    "address": {
        "street": "Bahnhofstrasse",
        "houseNumber": "1",
        "swissZipCode": "8001",
        "city": "Zuerich",
        "careOf": None,
    },
    "registryOfficeId": 20,
    "cantonalExcerptWeb": "https://zh.chregister.ch/cr-portal/auszug/auszug.xhtml?uid=CHE-105.805.185",
    "sogcPub": [
        {
            "sogcId": 1234567,
            "sogcDate": "2026-03-14",
            "publicationId": 987654,
            "message": "Muster Handels AG, in Zuerich, CHE-105.805.185, Aktiengesellschaft ...",
            "registryOfficeId": 20,
            "mutationTypes": [{"id": 2, "key": "MUTATION"}],
        }
    ],
    "translation": [],
    "oldNames": [],
    "branchOffices": [],
    "hasTakenOver": [],
    "wasTakenOverBy": [],
}

SEARCH_FIXTURE = [
    {
        "name": "Muster Handels AG",
        "uid": "CHE-105.805.185",
        "chid": "CH-020.3.926.379-0",
        "ehraid": 1234567,
        "legalFormId": 4,
        "registryOfficeId": 20,
        "status": "EXISTIEREND",
    }
]


def fake_response(payload, status=200):
    body = json.dumps(payload).encode("utf-8")
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError(
        url="https://example.invalid", code=code, msg="err", hdrs=None, fp=io.BytesIO(body)
    )


class RequestBuildingTests(unittest.TestCase):
    def setUp(self):
        self.client = ZefixClient(username="user", password="secret", min_interval=0)

    def test_basic_auth_header_is_sent(self):
        with mock.patch("urllib.request.urlopen", return_value=fake_response(COMPANY_FIXTURE)) as opener:
            self.client.company_by_uid("CHE-105.805.185")
        request = opener.call_args[0][0]
        expected = "Basic " + base64.b64encode(b"user:secret").decode()
        self.assertEqual(request.get_header("Authorization"), expected)

    def test_company_by_uid_builds_expected_url(self):
        with mock.patch("urllib.request.urlopen", return_value=fake_response(COMPANY_FIXTURE)) as opener:
            self.client.company_by_uid("CHE-105.805.185")
        self.assertEqual(
            opener.call_args[0][0].full_url,
            f"{PROD_BASE_URL}/company/uid/CHE-105.805.185",
        )

    def test_search_posts_json_body_with_filters(self):
        with mock.patch("urllib.request.urlopen", return_value=fake_response(SEARCH_FIXTURE)) as opener:
            results = self.client.search_companies("Muster", canton="ZH", max_entries=10)
        request = opener.call_args[0][0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.full_url, f"{PROD_BASE_URL}/company/search")
        self.assertEqual(
            json.loads(request.data),
            {"name": "Muster", "activeOnly": True, "maxEntries": 10, "offset": 0, "canton": "ZH"},
        )
        self.assertEqual(results, SEARCH_FIXTURE)

    def test_sogc_by_date_url(self):
        with mock.patch("urllib.request.urlopen", return_value=fake_response([])) as opener:
            self.client.sogc_by_date("2026-09-10")
        self.assertEqual(
            opener.call_args[0][0].full_url, f"{PROD_BASE_URL}/sogc/bydate/2026-09-10"
        )


class ErrorHandlingTests(unittest.TestCase):
    def setUp(self):
        self.client = ZefixClient(username="u", password="p", min_interval=0, max_retries=2)

    def test_401_fails_fast_without_retry(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(401)) as opener:
            with self.assertRaises(ZefixError) as ctx:
                self.client.company_by_uid("CHE-105.805.185")
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(opener.call_count, 1, "auth failures must not be retried")

    def test_429_is_retried_then_succeeds(self):
        responses = [http_error(429), fake_response(COMPANY_FIXTURE)]

        def side_effect(*_args, **_kwargs):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with mock.patch("urllib.request.urlopen", side_effect=side_effect), \
             mock.patch("time.sleep"):
            record = self.client.company_by_uid("CHE-105.805.185")
        self.assertEqual(record["uid"], "CHE-105.805.185")

    def test_persistent_500_raises_after_retries(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(500)), \
             mock.patch("time.sleep"):
            with self.assertRaises(ZefixError):
                self.client.company_by_uid("CHE-105.805.185")


class FieldInventoryTests(unittest.TestCase):
    def test_collect_field_paths_flattens_nested_structures(self):
        paths = collect_field_paths(COMPANY_FIXTURE)
        self.assertIn("uid", paths)
        self.assertIn("address.city", paths)
        self.assertIn("sogcPub[].message", paths)
        self.assertIn("legalForm.name.de", paths)

    def test_company_record_exposes_no_ownership_fields(self):
        """The central finding: no shareholder/owner data in the payload."""
        paths = collect_field_paths(COMPANY_FIXTURE)
        hits = [p for p in paths if any(h in p.lower() for h in OWNERSHIP_HINTS)]
        self.assertEqual(hits, [], f"unexpected ownership-like fields: {hits}")

    def test_detector_would_flag_ownership_fields_if_present(self):
        """Guard against a false negative: the detector must actually work."""
        payload = {"shareholders": [{"name": "A. Muster", "stammanteil": 100}]}
        paths = collect_field_paths(payload)
        hits = [p for p in paths if any(h in p.lower() for h in OWNERSHIP_HINTS)]
        self.assertTrue(hits, "detector failed to flag an obvious ownership field")

    def test_cantonal_excerpt_link_is_the_documents_route(self):
        self.assertIn("cantonalExcerptWeb", COMPANY_FIXTURE)
        self.assertTrue(COMPANY_FIXTURE["cantonalExcerptWeb"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()

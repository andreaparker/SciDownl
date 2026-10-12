"""Deterministic HTTP-adapter checks; no request reaches a live service."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

from scidownl_reconcile.providers import discover
from scidownl_reconcile.transport import SafeRedirect, get_json


URL = "https://api.crossref.org/works?rows=1"
CR_RECORD = {"DOI": "10.9999/EXAMPLE", "title": ["Synthetic modern physics"],
             "author": [{"given": "Ada", "family": "Example"}], "published": {"date-parts": [[2020]]},
             "container-title": ["Synthetic Physics"], "short-container-title": ["Synth. Phys."],
             "ISBN": ["9780000000002"], "ISSN": ["1234-5678"], "type": "journal-article"}
OA_RECORD = {"id": "https://openalex.org/W9999999999", "title": "Synthetic modern physics",
             "doi": "https://doi.org/10.9999/EXAMPLE", "publication_year": 2020,
             "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/12345678"},
             "authorships": [{"author": {"display_name": "Ada Example"}, "institutions": ["unused"]}],
             "primary_location": {"source": {"id": "https://openalex.org/S99999", "type": "journal",
                                               "display_name": "Synthetic Physics", "issn": ["1234-5678"]}}}


def http_error(status, retry_after="0"):
    return HTTPError(URL, status, "Synthetic failure", {"Retry-After": retry_after}, None)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.opener = MagicMock()
        self.network = patch("scidownl_reconcile.transport.build_opener", return_value=self.opener)
        self.network.start()
        self.addCleanup(self.network.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def respond(self, *values):
        responses = [io.BytesIO(json.dumps(value).encode()) if isinstance(value, dict) else value for value in values]
        self.opener.open.side_effect = responses

    def crossref(self, target=None, record=None):
        self.respond({"message": {"items": [record or CR_RECORD]}})
        return discover(target or {"title": "Synthetic modern physics"}, {"providers": ["crossref"]})

    def openalex(self, record=None):
        self.respond({"results": [record or OA_RECORD]})
        return discover({"title": "Synthetic modern physics"}, {"providers": ["openalex"]})

    def cache_options(self):
        return {"cache_dir": str(self.root), "retries": 0}

    def test_crossref_normalizes_metadata_and_short_journal(self):
        candidate = self.crossref()["candidates"][0]
        self.assertEqual(candidate["identifiers"]["doi"], "10.9999/example")
        self.assertEqual(candidate["journal_variants"], ["Synth. Phys."])
        self.assertEqual(candidate["authors"], ["Ada Example"])
        self.assertEqual(candidate["year"], 2020)

    def test_crossref_bibliographic_query_retains_context(self):
        target = {"title": "Physics", "authors": ["A. Example"], "year": 1905, "journal": "Ann. Phys."}
        self.crossref(target)
        request = self.opener.open.call_args.args[0]
        query = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(query["query.bibliographic"], ["Physics A. Example 1905 Ann. Phys."])

    def test_crossref_doi_lookup_is_exact(self):
        self.respond({"message": CR_RECORD})
        result = discover({"doi": "https://doi.org/10.9999/EXAMPLE"}, {"providers": ["crossref"]})
        self.assertEqual(result["queries"][0]["type"], "identifier:doi")
        self.assertTrue(result["queries"][0]["request_url"].endswith("10.9999%2Fexample"))

    def test_crossref_isbn_filter_without_title(self):
        self.respond({"message": {"items": [CR_RECORD]}})
        result = discover({"isbn": "9780000000002"}, {"providers": ["crossref"]})
        query = parse_qs(urlparse(result["queries"][0]["request_url"]).query)
        self.assertEqual(query["filter"], ["isbn:9780000000002"])

    def test_explicit_variant_and_result_bounds(self):
        empty = {"message": {"items": []}}
        self.respond(*([empty] * 5))
        target = {"title": "original", "title_variants": ["variant" + str(i) for i in range(8)]}
        result = discover(target, {"providers": ["crossref"], "limit": 100})
        self.assertEqual(len(result["queries"]), 5)
        self.assertIn("rows=25", result["queries"][0]["request_url"])

    def test_crossref_oa_is_not_inferred_from_link_or_license(self):
        record = dict(CR_RECORD, link=[{"URL": "https://publisher.test/paper.pdf"}], license=[{"URL": "cc-by"}])
        candidate = self.crossref(record=record)["candidates"][0]
        self.assertEqual(candidate["oa_locations"], [])
        self.assertNotIn("link", candidate["sources"][0]["raw_metadata"])

    def test_openalex_identifiers_and_bibliographic_only_provenance(self):
        candidate = self.openalex()["candidates"][0]
        self.assertEqual(candidate["identifiers"]["pmid"], "12345678")
        self.assertEqual(candidate["identifiers"]["openalex"], "W9999999999")
        authorship = candidate["sources"][0]["raw_metadata"]["authorships"][0]
        self.assertNotIn("institutions", authorship)

    def test_openalex_exact_identifiers_and_header_authentication(self):
        self.respond(OA_RECORD, OA_RECORD, OA_RECORD)
        target = {"doi": "10.9999/example", "pmid": "12345678", "openalex": "W9999999999"}
        result = discover(target, {"providers": ["openalex"], "api_key": "SECRET"})
        self.assertEqual(len(result["queries"]), 3)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer SECRET")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_crossref_does_not_receive_openalex_key(self):
        self.respond({"message": {"items": []}})
        discover({"title": "Physics"}, {"providers": ["crossref"], "api_key": "SECRET"})
        self.assertIsNone(self.opener.open.call_args.args[0].get_header("Authorization"))

    def test_only_explicit_oa_locations_with_web_urls_are_exported(self):
        good = {"is_oa": True, "pdf_url": "https://repository.test/paper.pdf", "license": "cc-by", "version": "acceptedVersion"}
        bad = [{"is_oa": False, "pdf_url": "https://closed.test/paper.pdf"},
               {"is_oa": True, "pdf_url": "file:///tmp/paper.pdf"}, {"is_oa": True, "pdf_url": "https:invalid"}]
        candidate = self.openalex(dict(OA_RECORD, locations=[good] + bad))["candidates"][0]
        self.assertEqual(len(candidate["oa_locations"]), 1)
        self.assertEqual(candidate["oa_locations"][0]["license"], "cc-by")
        self.assertIn("retrieved_at", candidate["oa_locations"][0]["provenance"])

    def test_repository_source_is_not_a_journal(self):
        record = dict(OA_RECORD, primary_location={"source": {"type": "repository", "display_name": "arXiv"}})
        candidate = self.openalex(record)["candidates"][0]
        self.assertEqual(candidate["journal"], "")
        self.assertEqual(candidate["sources"][0]["raw_metadata"]["primary_location"]["source"]["display_name"], "arXiv")

    def test_identifier_404_is_a_completed_no_record_lookup(self):
        self.respond(http_error(404))
        result = discover({"doi": "10.9999/absent"}, {"providers": ["openalex"]})
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["queries"][0]["http_status"], 404)
        self.assertEqual(result["queries"][0]["status"], "complete")

    def test_search_404_is_a_failure(self):
        self.respond(http_error(404))
        result = discover({"title": "Physics"}, {"providers": ["crossref"]})
        self.assertEqual(result["errors"][0]["http_status"], 404)
        self.assertEqual(result["queries"][0]["status"], "failed")

    def test_unknown_provider_fails_without_http(self):
        result = discover({"title": "Physics"}, {"providers": ["unknown"]})
        self.assertEqual(result["errors"][0]["error"], "unsupported_provider")
        self.opener.open.assert_not_called()

    def test_malformed_metadata_is_explicit_and_not_cached(self):
        self.respond({"unrelated": "response"})
        result = get_json(URL, "crossref", self.cache_options())
        self.assertEqual(result["error"], "invalid_metadata")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_malformed_candidate_is_explicit_and_not_cached(self):
        self.respond({"message": {"items": [dict(CR_RECORD, author=["invalid-author"])]}})
        options = dict(self.cache_options(), providers=["crossref"])
        result = discover({"title": "Physics"}, options)
        self.assertEqual(result["errors"][0]["error"], "invalid_metadata")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_null_author_and_date_fields_remain_partial_metadata(self):
        candidate = self.crossref(record=dict(CR_RECORD, author=None, published=None))["candidates"][0]
        self.assertEqual(candidate["authors"], [])
        self.assertIsNone(candidate["year"])

    def test_crossref_malformed_year_is_audited_and_not_cached(self):
        record = dict(CR_RECORD, published={"date-parts": [["unknown"]]})
        self.respond({"message": {"items": [record]}})
        options = dict(self.cache_options(), providers=["crossref"])
        result = discover({"title": "Physics"}, options)
        self.assertEqual(result["errors"][0]["error"], "invalid_metadata")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_openalex_malformed_year_is_audited_and_not_cached(self):
        self.respond({"results": [dict(OA_RECORD, publication_year="unknown")]})
        options = dict(self.cache_options(), providers=["openalex"])
        result = discover({"title": "Physics"}, options)
        self.assertEqual(result["errors"][0]["error"], "invalid_metadata")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_boolean_year_is_not_treated_as_numeric_year(self):
        self.respond({"results": [dict(OA_RECORD, publication_year=True)]})
        result = discover({"title": "Physics"}, {"providers": ["openalex"]})
        self.assertEqual(result["errors"][0]["error"], "invalid_metadata")

    def test_numeric_year_string_is_normalized_safely(self):
        candidate = self.openalex(dict(OA_RECORD, publication_year="2020"))["candidates"][0]
        self.assertEqual(candidate["year"], 2020)

    def test_null_openalex_authors_and_year_remain_partial_metadata(self):
        candidate = self.openalex(dict(OA_RECORD, authorships=None, publication_year=None))["candidates"][0]
        self.assertEqual(candidate["authors"], [])
        self.assertIsNone(candidate["year"])

    def test_invalid_json_is_not_cached(self):
        self.respond(io.BytesIO(b"not JSON"))
        result = get_json(URL, "crossref", self.cache_options())
        self.assertEqual(result["error"], "invalid_response")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_successful_cache_preserves_identity_timestamp_and_digest(self):
        self.respond({"message": {"items": [CR_RECORD]}})
        first = get_json(URL, "crossref", self.cache_options())
        second = get_json(URL, "crossref", self.cache_options())
        self.assertEqual(second["cache"], "hit")
        self.assertEqual(first["retrieved_at"], second["retrieved_at"])
        self.assertEqual(self.opener.open.call_count, 1)
        cached = json.loads(next(self.root.iterdir()).read_text())
        self.assertEqual(cached["request"]["url"], URL)
        self.assertEqual(len(cached["response_sha256"]), 64)

    def corrupt_cache(self, field, value):
        path = next(self.root.iterdir())
        cached = json.loads(path.read_text())
        cached[field] = value
        path.write_text(json.dumps(cached))

    def test_corrupt_cache_digest_triggers_new_request(self):
        response = {"message": {"items": [CR_RECORD]}}
        self.respond(response, response)
        get_json(URL, "crossref", self.cache_options())
        self.corrupt_cache("response_sha256", "invalid")
        self.assertEqual(get_json(URL, "crossref", self.cache_options())["cache"], "stored")
        self.assertEqual(self.opener.open.call_count, 2)

    def test_corrupt_cache_request_identity_triggers_new_request(self):
        response = {"message": {"items": []}}
        self.respond(response, response)
        get_json(URL, "crossref", self.cache_options())
        self.corrupt_cache("request", {"provider": "openalex", "url": URL})
        get_json(URL, "crossref", self.cache_options())
        self.assertEqual(self.opener.open.call_count, 2)

    def test_corrupt_cache_timestamp_triggers_new_request(self):
        response = {"message": {"items": []}}
        self.respond(response, response)
        get_json(URL, "crossref", self.cache_options())
        self.corrupt_cache("retrieved_at", "not-a-timestamp")
        get_json(URL, "crossref", self.cache_options())
        self.assertEqual(self.opener.open.call_count, 2)

    def test_failed_request_is_never_cached(self):
        self.respond(http_error(503))
        result = get_json(URL, "crossref", self.cache_options())
        self.assertEqual(result["http_status"], 503)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_http_error_response_is_closed(self):
        body = io.BytesIO(b"service unavailable")
        self.respond(HTTPError(URL, 503, "Unavailable", {}, body))
        result = get_json(URL, "crossref", {"retries": 0})
        self.assertTrue(body.closed)
        self.assertEqual(result["http_status"], 503)

    def test_retry_after_is_bounded_and_then_succeeds(self):
        self.respond(http_error(429, "900"), {"message": {"items": []}})
        with patch("scidownl_reconcile.transport.time.sleep") as sleep:
            result = get_json(URL, "crossref", {"retries": 1})
        sleep.assert_called_once_with(30.0)
        self.assertEqual(result["data"]["message"]["items"], [])

    def test_server_retries_are_bounded_to_three(self):
        self.respond(*([http_error(500)] * 4))
        with patch("scidownl_reconcile.transport.time.sleep") as sleep:
            result = get_json(URL, "crossref", {"retries": 100})
        self.assertEqual(result["http_status"], 500)
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(self.opener.open.call_count, 4)

    def test_network_error_does_not_leak_exception_credentials(self):
        self.respond(URLError("SECRET"))
        result = get_json(URL, "crossref", {"api_key": "SECRET"})
        self.assertEqual(result["error"], "network_error")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_timeout_is_bounded(self):
        self.respond({"message": {"items": []}})
        get_json(URL, "crossref", {"timeout": 1000})
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 60.0)

    def test_credentials_are_redacted_from_cache_payload_and_identity(self):
        response = {"message": {"items": [dict(CR_RECORD, title=["secret token SECRET"]) ]}}
        self.respond(response)
        options = dict(self.cache_options(), api_key="SECRET")
        get_json(URL + "&title=SECRET", "crossref", options)
        self.assertNotIn("SECRET", next(self.root.iterdir()).read_text())

    def test_error_evidence_redacts_encoded_credentials(self):
        self.respond(http_error(403))
        result = discover({"title": "secret/+ token"}, {"providers": ["openalex"], "api_key": "secret/+ token"})
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["errors"][0]["http_status"], 403)

    def test_redirect_removes_bearer_on_another_host(self):
        request = Request("https://api.openalex.org/works/W1", headers={"Authorization": "Bearer SECRET"})
        redirected = SafeRedirect().redirect_request(request, None, 302, "Found", {}, "https://other.test/work")
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_redirect_preserves_bearer_on_same_https_host(self):
        request = Request("https://api.openalex.org/works/W1", headers={"Authorization": "Bearer SECRET"})
        redirected = SafeRedirect().redirect_request(request, None, 302, "Found", {}, "https://api.openalex.org/works/W2")
        self.assertEqual(redirected.get_header("Authorization"), "Bearer SECRET")

    def test_redirect_to_http_is_refused(self):
        request = Request("https://api.openalex.org/works/W1")
        with self.assertRaises(URLError):
            SafeRedirect().redirect_request(request, None, 302, "Found", {}, "http://api.openalex.org/works/W2")


if __name__ == "__main__":
    unittest.main()

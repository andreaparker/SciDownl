"""Input validation, audit preservation, and conservative worker export."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scidownl_reconcile.cli import main
from scidownl_reconcile.manifest import load_manifest, normalize_record, write_json
from scidownl_reconcile.pipeline import process_record, run_pipeline, worker_manifest


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, name, data):
        path = self.root / name
        write_json(path, data)
        return path

    def read(self, path):
        return json.loads(path.read_text())

    def synthetic_paper(self):
        return {"record_id": "m1", "title": "Precision measurement of optical frequencies",
                "authors": ["Ada Example"], "year": 2020, "journal": "Synthetic Physics"}

    def failed_discovery(self):
        return dict(candidates=[], queries=[], errors=[{"provider": "openalex", "error": "HTTP 429"}])

    def assert_worker_output(self, path):
        worker = self.read(path)["records"][0]
        self.assertEqual(worker["source_type"], "doi")
        self.assertEqual(worker["source_keyword"], "10.9999/fixture-test")

    def test_json_preserves_original_and_normalizes_identifiers(self):
        row = {"record_id": "old-17", "title": "Science", "authors": ["Einstein, A."],
               "doi": "https://doi.org/10.9999/FIXTURE", "worker_note": "keep me"}
        records = load_manifest(self.write("targets.json", {"records": [row]}))
        self.assertEqual(records[0]["original"], row)
        self.assertEqual(records[0]["target"]["identifiers"]["doi"], "10.9999/fixture")

    def test_csv_field_mapping_and_semicolon_names(self):
        path = self.root / "targets.csv"
        path.write_text('key,heading,byline,date\n19,Electrodynamics,"Einstein, A.;Planck, M.",1905\n')
        fields = {"record_id": "key", "title": "heading", "authors": "byline", "year": "date"}
        record = load_manifest(path, fields)[0]
        self.assertEqual(record["record_id"], "19")
        self.assertEqual(record["target"]["authors"], ["Einstein, A.", "Planck, M."])
        self.assertEqual(record["target"]["year"], 1905)

    def test_duplicate_ids_fail_but_duplicate_titles_remain_rows(self):
        rows = [{"record_id": "x", "title": "Science"}] * 2
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_manifest(self.write("duplicates.json", rows))
        records = load_manifest(self.write("titles.json", [{"title": "Science"}] * 2))
        self.assertEqual([item["record_id"] for item in records], ["row-1", "row-2"])

    def test_invalid_inputs_are_rejected(self):
        invalid = [{}, {"title": "x", "year": True}, {"title": "x", "authors": [2]},
                   {"title": "x", "doi": "not a doi"}, {"title": 1},
                   {"title": "x", "identifiers": []}, {"title": "x", "pmid": "abc"}]
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(ValueError):
                normalize_record(row)

    def test_identifier_only_input(self):
        target = normalize_record({"pmid": 123456})
        self.assertEqual(target["identifiers"]["pmid"], "123456")

    def test_conflicting_input_identifiers_fail(self):
        row = {"doi": "10.0000/one", "identifiers": {"doi": "10.0000/two"}}
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            normalize_record(row)
        row["doi"] = ""
        self.assertEqual(normalize_record(row)["identifiers"]["doi"], "10.0000/two")

    def test_verified_without_supported_worker_identifier_is_retained(self):
        row = {"record_id": "isbn-only", "isbn": "0000000000000"}
        records = load_manifest(self.write("isbn.json", [row]))
        result = run_pipeline(records, {}, {"isbn-only": [row]})
        self.assertEqual(result["records"][0]["status"], "verified")
        self.assertFalse(result["records"][0]["acquisition_ready"])
        self.assertEqual(worker_manifest(result)["records"], [])

    def test_missing_offline_record_is_incomplete(self):
        records = load_manifest(self.write("one.json", [{"title": "Electrodynamics"}]))
        result = run_pipeline(records, {}, {})
        self.assertTrue(result["records"][0]["discovery"]["errors"])
        self.assertEqual(worker_manifest(result)["records"], [])

    def test_provider_error_withholds_even_verified_match(self):
        record = dict(record_id="1", target={"title": "Electrodynamics"}, original={})
        decision = dict(status="verified", selected={"identifiers": {"doi": "10.9999/test"}}, ranked_candidates=[])
        discovery = self.failed_discovery()
        with patch("scidownl_reconcile.pipeline.discover", return_value=discovery):
            with patch("scidownl_reconcile.pipeline.reconcile", return_value=decision):
                result = process_record(record, {}, None)
        self.assertEqual(result["status"], "plausible")
        self.assertFalse(result["acquisition_ready"])
        self.assertIsNone(result["selected"])

    def test_cli_writes_audit_and_compatible_worker_input(self):
        target = self.synthetic_paper()
        candidate = dict(target, identifiers={"doi": "10.9999/fixture-test"})
        inputs = self.write("inputs.json", [target])
        pool = self.write("pool.json", {"m1": [candidate]})
        output, workers = self.root / "audit.json", self.root / "workers.json"
        code = main([str(inputs), "--offline-candidates", str(pool), "--output", str(output),
                     "--worker-output", str(workers)])
        self.assertEqual(code, 0)
        self.assert_worker_output(workers)
        self.assertEqual(self.read(output)["records"][0]["original"], target)

    def test_cli_never_overwrites_input(self):
        path = self.write("input.json", [{"title": "Electrodynamics"}])
        original = path.read_bytes()
        self.assertEqual(main([str(path), "--output", str(path)]), 1)
        self.assertEqual(path.read_bytes(), original)

    def test_cli_returns_nonzero_for_incomplete_discovery(self):
        path = self.write("input.json", [{"title": "Electrodynamics"}])
        pool = self.write("pool.json", {})
        code = main([str(path), "--offline-candidates", str(pool), "--output", str(self.root / "out.json")])
        self.assertEqual(code, 2)

    def test_cli_is_independent_of_download_package(self):
        script = 'import sys; from scidownl_reconcile.cli import parser; parser(); assert "scidownl" not in sys.modules'
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

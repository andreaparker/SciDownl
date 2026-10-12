"""Deterministic identity labels, conservative decisions and metric accounting."""

import json
import tempfile
import unittest
from pathlib import Path

from scidownl_reconcile.benchmark import evaluate, main, same_identity
from scidownl_reconcile.decisions import reconcile


FIXTURES = Path(__file__).parent / "fixtures" / "reconciliation.json"
CASES = json.loads(FIXTURES.read_text(encoding="utf-8"))


def _stub(case_id, status="verified"):
    candidate = {"identifiers": {"doi": case_id}}
    return {"status": status, "selected": candidate,
            "ranked_candidates": [candidate]}


def _metric_fixture(case_id, identifier, expected_status="verified"):
    return {
        "case_id": case_id, "category": "metric_accounting",
        "target": {"title": case_id}, "candidates": [],
        "expected_status": expected_status,
        "expected_identifiers": {"doi": identifier} if identifier else None,
    }


class FixtureBenchmarkTests(unittest.TestCase):
    def test_fixture_labels(self):
        for case in CASES:
            with self.subTest(case=case["case_id"]):
                result = reconcile(case["target"], case["candidates"])
                self.assertEqual(result["status"], case["expected_status"])
                if result["status"] == "verified":
                    self.assertTrue(same_identity(case["expected_identifiers"],
                                                  result["selected"]))

    def test_fixture_scope_and_provenance(self):
        self.assertGreaterEqual(len(CASES), 25)
        self.assertEqual(len(CASES), len({case["case_id"] for case in CASES}))
        categories = {case["category"] for case in CASES}
        self.assertTrue({"historical_physics", "translated_titles", "books",
                         "false_matches", "journal_abbreviations"} <= categories)
        for case in CASES:
            self.assertIn("Synthetic", case["fixture_provenance"])
            for candidate in case["candidates"]:
                self.assertEqual(candidate["source"], "synthetic_fixture")

    def test_benchmark_is_deterministic_and_conservative(self):
        report = evaluate(CASES)
        self.assertEqual(report, evaluate(CASES))
        self.assertEqual(report["false_positive_count"], 0)
        self.assertEqual(report["unsafe_auto_verification_count"], 0)
        self.assertEqual(report["status_accuracy"], 1.0)
        self.assertEqual(report["candidate_recall"], 1.0)
        self.assertGreater(report["automatic_verified_count"], 0)

    def test_precision_recall_denominators(self):
        fixtures = [_metric_fixture("correct", "correct"),
                    _metric_fixture("wrong", "truth"),
                    _metric_fixture("review", "review", "plausible"),
                    _metric_fixture("negative", None, "unresolved")]
        def resolver(target, candidates):
            status = "plausible" if target["title"] == "review" else "verified"
            return _stub(target["title"], status)
        report = evaluate(fixtures, resolver)
        self.assertEqual(report["precision"], 0.333333)
        self.assertEqual(report["recall"], 0.333333)
        self.assertEqual(report["candidate_recall"], 0.666667)
        self.assertEqual(report["false_positive_cases"], ["wrong", "negative"])
        self.assertEqual(report["false_negative_cases"], ["wrong", "review"])
        self.assertEqual(report["unsafe_auto_verification_cases"], ["negative"])

    def test_review_identity_does_not_excuse_unsafe_verification(self):
        fixture = _metric_fixture("review", "review", "plausible")
        report = evaluate([fixture], lambda target, candidates: _stub("review"))
        self.assertEqual(report["precision"], 1.0)
        self.assertEqual(report["unsafe_auto_verification_count"], 1)
        self.assertEqual(report["status_accuracy"], 0.0)

    def test_no_predictions_or_positive_labels_are_not_zero_precision(self):
        report = evaluate([], lambda target, candidates: _stub("unused"))
        self.assertIsNone(report["precision"])
        self.assertIsNone(report["recall"])
        self.assertIsNone(report["candidate_recall"])

    def test_identifier_equivalence(self):
        expected = {"doi": "https://doi.org/10.0000/SYNTHETIC.1"}
        candidate = {"identifiers": {"doi": "10.0000/synthetic.1"}}
        self.assertTrue(same_identity(expected, candidate))
        self.assertFalse(same_identity(None, candidate))
        self.assertFalse(same_identity({"doi": "10.0000/other"}, candidate))

    def test_benchmark_cli_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            main([str(FIXTURES), "--output", str(output)])
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["case_count"], len(CASES))
            self.assertEqual(len(report["fixture_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()

"""Offline, labeled engineering benchmark; never evidence of collection recovery."""

import argparse
import hashlib
import json
from pathlib import Path

from .normalize import identifier_values


DEFINITIONS = {
    "precision": "Correct selected identities / all automatic verified selections.",
    "recall": (
        "Correct automatic verified selections / all fixtures with a labeled "
        "single identity, including intentionally review-required cases."
    ),
    "candidate_recall": (
        "Labeled single identities present anywhere in ranked candidates / "
        "all fixtures with a labeled single identity."
    ),
    "top_3_candidate_recall": (
        "Labeled single identities in the first three ranked candidates / "
        "all fixtures with a labeled single identity."
    ),
    "false_positive": (
        "An automatic verified selection with the wrong identity, or where "
        "the label establishes no unique identity."
    ),
    "status_accuracy": "Predicted decision status equals the labeled status.",
    "unsafe_auto_verification": (
        "Automatic verified status on a fixture labeled plausible, unresolved "
        "or ambiguous_editions, even if its identity happens to match."
    ),
}


def same_identity(expected, candidate):
    """Compare labeled identifiers, allowing DOI URLs and ISBN separators."""
    if not expected or not candidate:
        return False
    expected_record = {"identifiers": expected}
    return all(identifier_values(expected_record, key)
               <= identifier_values(candidate, key) for key in expected)


def _decision_flags(case, result):
    expected = case.get("expected_identifiers")
    automatic = result["status"] == "verified"
    correct = automatic and same_identity(expected, result.get("selected"))
    return {
        "automatic": automatic,
        "correct_automatic": correct,
        "false_positive": automatic and not correct,
        "false_negative": bool(expected) and not correct,
        "status_correct": result["status"] == case["expected_status"],
        "unsafe_auto_verification": automatic and case["expected_status"] != "verified",
    }


def _retrieval_flags(case, result):
    expected = case.get("expected_identifiers")
    ranked = result["ranked_candidates"]
    return {
        "has_identity_label": bool(expected),
        "candidate_found": any(same_identity(expected, row) for row in ranked),
        "top_3_found": any(same_identity(expected, row) for row in ranked[:3]),
    }


def _case_result(case, reconciler):
    result = reconciler(case["target"], case["candidates"])
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "expected_status": case["expected_status"],
        "status": result["status"],
        "selected_identifiers": (result.get("selected") or {}).get("identifiers"),
        "expected_identifiers": case.get("expected_identifiers"),
        **_decision_flags(case, result),
        **_retrieval_flags(case, result),
    }


def _count(rows, field):
    return sum(bool(row[field]) for row in rows)


def _ratio(numerator, denominator):
    return round(numerator / denominator, 6) if denominator else None


def _identity_metrics(rows):
    automatic = _count(rows, "automatic")
    correct = _count(rows, "correct_automatic")
    labeled = _count(rows, "has_identity_label")
    found = _count(rows, "candidate_found")
    top_three = _count(rows, "top_3_found")
    return {
        "labeled_identity_count": labeled,
        "automatic_verified_count": automatic,
        "correct_automatic_count": correct,
        "precision": _ratio(correct, automatic),
        "recall": _ratio(correct, labeled),
        "candidate_recall": _ratio(found, labeled),
        "top_3_candidate_recall": _ratio(top_three, labeled),
        "candidate_found_count": found,
        "top_3_found_count": top_three,
    }


def _decision_metrics(rows):
    return {
        "case_count": len(rows),
        "false_positive_count": _count(rows, "false_positive"),
        "false_negative_count": _count(rows, "false_negative"),
        "unsafe_auto_verification_count": _count(rows, "unsafe_auto_verification"),
        "status_correct_count": _count(rows, "status_correct"),
        "status_accuracy": _ratio(_count(rows, "status_correct"), len(rows)),
    }


def _metrics(rows):
    return {**_identity_metrics(rows), **_decision_metrics(rows)}


def _case_ids(rows, field):
    return [row["case_id"] for row in rows if row[field]]


def evaluate(cases, reconciler=None):
    """Report identity precision/recall and conservative decision behavior."""
    if reconciler is None:
        from .decisions import reconcile
        reconciler = reconcile
    rows = [_case_result(case, reconciler) for case in cases]
    categories = sorted({row["category"] for row in rows})
    canonical = json.dumps(cases, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {
        "benchmark_kind": "deterministic_synthetic_engineering_fixtures",
        "limitations": (
            "Synthetic fixture results measure algorithm behavior, not live "
            "provider completeness, external validity or recovered documents."
        ),
        "fixture_sha256": hashlib.sha256(canonical).hexdigest(),
        "definitions": DEFINITIONS,
        **_metrics(rows),
        "false_positive_cases": _case_ids(rows, "false_positive"),
        "false_negative_cases": _case_ids(rows, "false_negative"),
        "unsafe_auto_verification_cases": _case_ids(rows, "unsafe_auto_verification"),
        "per_category": {
            category: _metrics([row for row in rows if row["category"] == category])
            for category in categories
        },
        "cases": rows,
    }


def _arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", type=Path, help="JSON array of labeled cases")
    parser.add_argument("--output", type=Path, help="Write the machine-readable report")
    return parser.parse_args(argv)


def main(argv=None):
    args = _arguments(argv)
    cases = json.loads(args.fixtures.read_text(encoding="utf-8"))
    rendered = json.dumps(evaluate(cases), indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()

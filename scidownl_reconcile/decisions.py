"""Deduplicate providers and choose conservative worker handoff decisions."""

import json

from .normalize import author_scores, best_journal_score, identifier_values, normalize_text, title_similarity
from .scoring import is_book, language_value, record_type, score_candidate, verification_ready


def stable_key(candidate, index):
    for key in ("doi", "pmid", "openalex", "isbn"):
        values = identifier_values(candidate, key)
        if values:
            return key, tuple(sorted(values))
    return "record", candidate.get("record_id") or index


def unique_records(records):
    seen, result = set(), []
    for record in records:
        identity = json.dumps(record, sort_keys=True, ensure_ascii=False)
        if identity not in seen:
            result.append(record)
            seen.add(identity)
    return result


def provider_disagreement(left, right):
    fields = provider_title_conflict(left, right)
    names = author_scores(left, right)
    if names and min(names) < 0.85:
        fields.append("authors")
    fields += provider_field_conflicts(left, right) + provider_publication_conflicts(left, right)
    return [{"field": field, "records": [left.get("record_id"), right.get("record_id")],
             "values": [left.get(field), right.get(field)]} for field in fields]


def provider_title_conflict(left, right):
    if left.get("title") and right.get("title") and title_similarity(left["title"], right["title"]) < 0.94:
        return ["title"]
    return []


def provider_field_conflicts(left, right):
    return [field for field in ("year", "edition", "volume", "issue", "pages")
            if left.get(field) and right.get(field)
            and normalize_text(left[field]) != normalize_text(right[field])]


def provider_publication_conflicts(left, right):
    fields = []
    for field, compare in (("language", language_value), ("type", record_type)):
        if compare(left) and compare(right) and compare(left) != compare(right):
            fields.append(field)
    if left.get("journal") and right.get("journal") and best_journal_score(left, right) < 0.85:
        fields.append("journal")
    return fields


def merge_identifiers(left, right):
    result = dict(left or {})
    for key, value in (right or {}).items():
        previous = result.get(key)
        if not previous:
            result[key] = value
            continue
        a = previous if isinstance(previous, list) else [previous]
        b = value if isinstance(value, list) else [value]
        values = list(dict.fromkeys(a + b))
        result[key] = values[0] if len(values) == 1 else values
    return result


def merge_candidate(left, right):
    merged = fill_missing_fields(left, right)
    conflicts = provider_disagreement(left, right)
    for field in ("sources", "oa_locations"):
        merged[field] = unique_records(list(left.get(field) or []) + list(right.get(field) or []))
    merged["identifiers"] = merge_identifiers(left.get("identifiers"), right.get("identifiers"))
    merged["provider_conflicts"] = list(left.get("provider_conflicts") or []) + conflicts
    return merged


def fill_missing_fields(left, right):
    merged = dict(left)
    for field, value in right.items():
        if not merged.get(field):
            merged[field] = value
    return merged


def deduplicate(candidates):
    groups = {}
    for index, candidate in enumerate(candidates):
        key = stable_key(candidate, index)
        groups[key] = merge_candidate(groups[key], candidate) if key in groups else dict(candidate)
    return list(groups.values())


def viable_candidates(ranked):
    return [item for item in ranked if item["eligible"] and item["confidence"] >= 0.7]


def edition_signature(candidate):
    return (tuple(sorted(identifier_values(candidate, "isbn"))), candidate.get("edition"),
            candidate.get("year"), candidate.get("language"))


def edition_ambiguity(target, viable):
    if not viable or not book_candidates(target, viable):
        return False
    if viable[0]["match_flags"]["edition_conflicts"]:
        return True
    if target.get("edition") or identifier_values(target, "isbn"):
        return False
    return differing_editions(viable)


def book_candidates(target, viable):
    return is_book(target) or any(is_book(item) for item in viable)


def differing_editions(viable):
    close = [item for item in viable if viable[0]["confidence"] - item["confidence"] <= 0.2]
    return len({edition_signature(item) for item in close}) > 1


def status_decision(target, ranked):
    viable = viable_candidates(ranked)
    if not viable:
        return "unresolved", None
    if edition_ambiguity(target, viable):
        return "ambiguous_editions", None
    top = viable[0]
    if len(viable) > 1 and top["confidence"] - viable[1]["confidence"] < 0.06:
        top["reasons"].append("Multiple distinct records have closely ranked evidence; review required.")
        return "plausible", None
    if verification_ready(target, top):
        return "verified", top
    return "plausible", None


def reconcile(target, candidates):
    """Return ranked evidence; only verified records are automatically selected."""
    ranked = [score_candidate(target, item) for item in deduplicate(candidates)]
    ranked.sort(key=lambda item: (not item["eligible"], -item["confidence"], str(item.get("record_id", ""))))
    for item in ranked:
        if item.get("provider_conflicts"):
            item["reasons"].append("Bibliographic providers disagree; merged identity requires review.")
    status, selected = status_decision(target, ranked)
    return {"status": status, "selected": selected, "ranked_candidates": ranked}

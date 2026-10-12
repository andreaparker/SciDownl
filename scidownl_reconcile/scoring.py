"""Interpretable metadata scores and explicit contradiction checks."""

from .normalize import (author_scores, best_comparison, distinct_authors, journal_similarity,
                        identifier_values, normalize_text, normalize_title,
                        title_numbers, title_similarity, variants)


_WEIGHTS = {"title": 0.58, "authors": 0.22, "year": 0.12, "journal": 0.08}
_IDENTIFIERS = ("doi", "pmid", "isbn", "openalex")
_BOOK_TYPES = {"book", "monograph", "edited-book", "reference-book"}
_OPPOSITES = (("heating", "cooling"), ("positive", "negative"),
              ("increase", "decrease"), ("attraction", "repulsion"))
_GREEK_PARTICLES = {"alpha", "beta", "gamma"}


def record_type(record):
    kind = str(record.get("type") or "").casefold()
    if kind in _BOOK_TYPES:
        return "book"
    return "article" if kind in {"article", "journal-article"} else kind


def is_book(record):
    return record_type(record) in _BOOK_TYPES


def component(score, field, target, candidate):
    return {"score": round(score, 4), "weight": _WEIGHTS.get(field, 0.0),
            "requested": target.get(field), "observed": candidate.get(field),
            "compared": bool(target.get(field)) and bool(candidate.get(field))}


def year_score(target, candidate):
    left, right = target.get("year"), candidate.get("year")
    if left is None or right is None:
        return 0.0
    distance = abs(int(left) - int(right))
    return {0: 1.0, 1: 0.75}.get(distance, 0.0)


def field_components(target, candidate):
    return {"title": comparison_component(target, candidate, "title"),
            "authors": authors_component(target, candidate),
            "year": component(year_score(target, candidate), "year", target, candidate),
            "journal": comparison_component(target, candidate, "journal")}


def authors_component(target, candidate):
    names = author_scores(target, candidate)
    score = sum(names) / len(names) if names else 0.0
    part = component(score, "authors", target, candidate)
    part["per_author_scores"] = [{"requested": name, "score": round(value, 4)}
                                 for name, value in zip(distinct_authors(target), names)]
    part["method"] = "One-to-one surname/given-name/initial assignment; explicit single-author aliases."
    return part


def comparison_component(target, candidate, field):
    compare = title_similarity if field == "title" else journal_similarity
    score, left, right = best_comparison(target, candidate, field, compare)
    part = component(score, field, target, candidate)
    normalize = normalize_title if field == "title" else normalize_text
    part.update(requested_variant=left, observed_variant=right,
                normalized_requested=normalize(left), normalized_observed=normalize(right))
    part["method"] = comparison_method(field)
    return part


def comparison_method(field):
    if field == "title":
        return "Best explicit alias pair: 0.6 character similarity + 0.4 token Dice overlap."
    return "Best explicit alias pair: exact, acronym or aligned word-prefix comparison."


def identifier_evidence(target, candidate):
    exact, conflicts, missing = [], [], []
    for key in _IDENTIFIERS:
        requested, observed = identifier_values(target, key), identifier_values(candidate, key)
        if not requested:
            continue
        if not observed:
            missing.append(key)
        elif requested & observed:
            exact.append(key)
        else:
            conflicts.append(key)
    return {"exact": exact, "conflicts": conflicts, "missing": missing}


def metadata_conflicts(target, candidate, parts):
    conflicts = low_score_conflicts(parts)
    names = author_scores(target, candidate)
    if names and min(names) == 0:
        conflicts.append("authors")
    return conflicts + year_conflicts(target, candidate, parts)


def low_score_conflicts(parts):
    limits = {"title": 0.72, "journal": 0.85}
    return [field for field, limit in limits.items()
            if parts[field]["compared"] and parts[field]["score"] < limit]


def year_conflicts(target, candidate, parts):
    if parts["year"]["compared"] and parts["year"]["score"] == 0:
        return ["edition_year" if is_book(target) or is_book(candidate) else "year"]
    return []


def structural_conflicts(target, candidate):
    conflicts = explicit_field_conflicts(target, candidate)
    left, right = record_type(target), record_type(candidate)
    if left and right and left != right:
        conflicts.append("type")
    return conflicts + number_conflicts(target, candidate) + title_term_conflicts(target, candidate)


def explicit_field_conflicts(target, candidate):
    fields = [field for field in ("edition", "volume", "issue", "pages")
            if target.get(field) and candidate.get(field)
            and normalize_text(target[field]) != normalize_text(candidate[field])]
    return fields + language_conflicts(target, candidate)


def language_value(record):
    value = normalize_text(record.get("language"))
    return {"english": "en", "german": "de", "french": "fr", "russian": "ru"}.get(value, value)


def language_conflicts(target, candidate):
    left, right = language_value(target), language_value(candidate)
    if left and right and left != right:
        return ["edition_language" if is_book(target) or is_book(candidate) else "language"]
    return []


def number_conflicts(target, candidate):
    if not target.get("title") or not candidate.get("title"):
        return []
    left_nums, right_nums = title_numbers(target.get("title")), title_numbers(candidate.get("title"))
    if left_nums != right_nums and not alias_numbers_agree(target, candidate):
        return ["title_numbers"]
    return []


def alias_numbers_agree(target, candidate):
    pairs = ((left, right) for left in variants(target, "title")
             for right in variants(candidate, "title"))
    return any(title_similarity(left, right) >= 0.94 and title_numbers(left) == title_numbers(right)
               for left, right in pairs)


def title_term_conflicts(target, candidate):
    left = set(normalize_title(target.get("title")).split())
    right = set(normalize_title(candidate.get("title")).split())
    if particle_conflict(left, right) or opposite_terms(left, right):
        return ["title_terms"]
    return []


def particle_conflict(left, right):
    a, b = left & _GREEK_PARTICLES, right & _GREEK_PARTICLES
    return bool(a and b and a != b)


def opposite_terms(left, right):
    return any((a in left and b in right) or (b in left and a in right) for a, b in _OPPOSITES)


def structural_missing(target, candidate):
    return [field for field in ("edition", "volume", "issue", "pages", "language")
            if target.get(field) and not candidate.get(field)]


def evidence_supports(parts):
    return [field for field, part in parts.items()
            if part["compared"] and part["score"] >= 0.85 and field != "title"]


def score_coverage(parts):
    weighted = sum(part["score"] * part["weight"] for part in parts.values())
    denominator = sum(part["weight"] for part in parts.values() if part["requested"])
    return weighted / denominator if denominator else 0.0


def confidence_cap(parts, supports):
    if not supports:
        return 0.74
    if "authors" not in supports:
        return 0.88
    return 0.96 if len(supports) == 1 else 0.98


def audit_reasons(parts, identifiers, conflicts):
    reasons = ["Conflict in requested %s metadata." % field for field in conflicts]
    reasons += ["Requested %s identifier differs." % key for key in identifiers["conflicts"]]
    reasons += ["Requested %s identifier is absent." % key for key in identifiers["missing"]]
    reasons += ["Exact requested %s identifier." % key for key in identifiers["exact"]]
    reasons += ["Missing candidate %s metadata." % field for field, part in parts.items()
                if part["requested"] and not part["observed"]]
    if parts["year"]["score"] == 0.75:
        reasons.append("Publication year differs by one; online/print timing needs review.")
    return reasons


def edition_conflicts(conflicts, identifiers):
    return [field for field in conflicts if field in {"edition", "edition_year", "edition_language"}] + [
        key for key in identifiers["conflicts"] if key == "isbn"]


def score_candidate(target, candidate):
    parts = field_components(target, candidate)
    identifiers = identifier_evidence(target, candidate)
    conflicts = metadata_conflicts(target, candidate, parts) + structural_conflicts(target, candidate)
    editions = edition_conflicts(conflicts, identifiers)
    supports = evidence_supports(parts)
    flags = {"identifier_evidence": identifiers, "conflicts": conflicts,
             "edition_conflicts": editions, "supports": supports,
             "structural_missing": structural_missing(target, candidate)}
    return scored_result(candidate, parts, flags)


def candidate_confidence(parts, flags):
    identifiers, conflicts = flags["identifier_evidence"], flags["conflicts"]
    supports = flags["supports"]
    confidence = min(score_coverage(parts), confidence_cap(parts, supports))
    if identifiers["exact"] and not conflicts and not identifiers["conflicts"]:
        confidence = 0.99
    if flags["edition_conflicts"]:
        confidence = min(confidence, 0.8)
    return round(confidence, 4)


def scored_result(candidate, parts, flags):
    identifiers, conflicts = flags["identifier_evidence"], flags["conflicts"]
    blocking = [item for item in conflicts if item not in {"edition_year", "edition_language"}]
    blocking += identifiers["conflicts"]
    result = dict(candidate)
    result.update(confidence=candidate_confidence(parts, flags), components=parts,
                  reasons=audit_reasons(parts, identifiers, conflicts), eligible=not blocking)
    result["match_flags"] = flags
    result["reasons"] += ["Missing candidate %s metadata." % field for field in flags["structural_missing"]]
    result["reasons"] += title_review_reasons(parts)
    return result


def title_review_reasons(parts):
    title = parts["title"]
    if title["compared"] and title["normalized_requested"] != title["normalized_observed"]:
        return ["Normalized titles differ; fuzzy ranking requires review or a confirmed explicit title alias."]
    return []


def short_title_requires_review(target, candidate):
    short = len(normalize_title(target.get("title")).split()) <= 3
    flags = candidate["match_flags"]
    return short and len(flags["supports"]) < 2 and not flags["identifier_evidence"]["exact"]


def verification_ready(target, candidate):
    flags = candidate["match_flags"]
    if verification_conflicts(candidate, flags):
        return False
    if not title_verification_ready(candidate):
        return False
    if flags["identifier_evidence"]["exact"]:
        return True
    if short_title_requires_review(target, candidate):
        return False
    if candidate["components"]["year"]["score"] != 1.0:
        return False
    return candidate["confidence"] >= 0.91 and candidate["components"]["title"]["score"] >= 0.94


def title_verification_ready(candidate):
    title = candidate["components"]["title"]
    if not title["compared"]:
        return True
    return title["normalized_requested"] == title["normalized_observed"]


def verification_conflicts(candidate, flags):
    return (not candidate["eligible"] or flags["conflicts"] or
            flags["identifier_evidence"]["conflicts"] or candidate.get("provider_conflicts") or
            flags["identifier_evidence"]["missing"] or flags["structural_missing"])

"""Conservative bibliographic comparisons without inferred translations."""

import html
import re
import unicodedata
from difflib import SequenceMatcher


_CYRILLIC = dict(zip(
    "абвгдеёзийклмнопрстуфхыэ",
    ("a", "b", "v", "g", "d", "e", "e", "z", "i", "i", "k", "l", "m",
     "n", "o", "p", "r", "s", "t", "u", "f", "kh", "y", "e"),
))
_CYRILLIC.update({"ж": "zh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
                  "ю": "yu", "я": "ya", "ь": "", "ъ": "", "і": "i",
                  "ї": "yi", "є": "ye", "ґ": "g"})
_LATIN = str.maketrans({"ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "ł": "l"})
_GREEK = dict(zip("αβγδεζηθικλμνξοπρστυφχψω",
                  ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
                   "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi", "rho",
                   "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega")))
_GREEK["ς"] = "sigma"
_SPELLINGS = {"colour": "color", "colours": "colors", "behaviour": "behavior",
              "centre": "center", "centres": "centers", "ionisation": "ionization",
              "connexion": "connection", "connexions": "connections",
              "aether": "ether", "electrodynamik": "elektrodynamik"}
_STOPWORDS = {"of", "the", "and", "der", "die", "des", "fur", "for", "de", "du"}
_NAME_PARTICLES = {"de", "del", "di", "da", "van", "von", "der", "den", "la", "le"}
_JOURNAL_INITIALS = {"j": {"journal"}, "z": {"zeitschrift"}}


def normalize_text(value):
    """Fold typography, accents and Cyrillic; do not translate words."""
    text = strip_formatting(value).casefold().translate(_LATIN)
    text = "".join(" " + _GREEK[char] + " " if char in _GREEK else char for char in text)
    text = "".join(_CYRILLIC.get(char, char) for char in text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def strip_formatting(value):
    text = html.unescape(str(value or ""))
    tags = r"</?(?:jats:)?(?:i|b|em|strong|sub|sup|span|italic)(?:\s+[^<>]*)?\s*/?>"
    return re.sub(tags, "", text, flags=re.IGNORECASE)


def normalize_title(value):
    words = normalize_text(value).split()
    return " ".join(_SPELLINGS.get(word, word) for word in words)


def title_similarity(left, right):
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    tokens_a, tokens_b = set(a.split()), set(b.split())
    overlap = 2 * len(tokens_a & tokens_b) / (len(tokens_a) + len(tokens_b))
    return 0.6 * SequenceMatcher(None, a, b).ratio() + 0.4 * overlap


def variants(record, field):
    """Return explicitly supplied aliases alongside the primary field."""
    return [record.get(field, "")] + list(record.get(field + "_variants") or [])


def best_title_score(target, candidate):
    return best_comparison(target, candidate, "title", title_similarity)[0]


def best_comparison(target, candidate, field, compare):
    pairs = ((compare(left, right), left, right) for left in variants(target, field)
             for right in variants(candidate, field))
    return max(pairs, default=(0.0, "", ""), key=lambda item: item[0])


def title_numbers(value):
    return set(re.findall(r"\b\d+\b", normalize_title(value)))


def author_parts(value):
    original = str(value or "")
    if "," in original:
        surname, given = original.split(",", 1)
        return normalize_text(surname), normalize_text(given).split()
    words = normalize_text(original).split()
    if not words:
        return "", []
    start = surname_start(words)
    return " ".join(words[start:]), words[:start]


def surname_start(words):
    start = len(words) - 1
    for word in reversed(words[:-1]):
        if word not in _NAME_PARTICLES:
            break
        start -= 1
    return start


def given_name_score(left, right):
    if not left or not right:
        return 0.75
    pairs = list(zip(left, right))
    if any(a[0] != b[0] for a, b in pairs):
        return 0.0
    if any(len(a) > 1 and len(b) > 1 and a != b for a, b in pairs):
        return 0.0
    return 1.0 if left == right else 0.92


def author_similarity(left, right):
    name_a, given_a = author_parts(left)
    name_b, given_b = author_parts(right)
    if not name_a or name_a != name_b:
        return 0.0
    return given_name_score(given_a, given_b)


def author_scores(target, candidate):
    requested = distinct_authors(target)
    available = distinct_authors(candidate)
    if not requested or not available:
        return []
    matrix = author_score_matrix(target, candidate, requested, available)
    return assigned_author_scores(matrix)


def distinct_authors(record):
    names = {normalize_text(name): name for name in record.get("authors") or []}
    return list(names.values())


def author_score_matrix(target, candidate, requested, available):
    aliases = single_author_aliases(target, requested)
    other_aliases = single_author_aliases(candidate, available)
    return [[best_author_score(author, aliases, [other] + other_aliases)
             for other in available] for author in requested]


def assigned_author_scores(matrix):
    edges = author_assignment_edges(matrix)
    assigned = {}
    for index in sorted(edges, key=lambda item: len(edges[item])):
        assign_author(index, edges, assigned)
    result = [0.0] * len(matrix)
    for candidate, requested in assigned.items():
        result[requested] = matrix[requested][candidate]
    return result


def author_assignment_edges(matrix):
    return {index: sorted((other for other, score in enumerate(row) if score > 0),
                          key=lambda other: -row[other]) for index, row in enumerate(matrix)}


def assign_author(index, edges, assigned):
    available, parents = assignment_path(index, edges, assigned)
    if available is None:
        return False
    augment_assignment(available, parents, assigned)
    return True


def assignment_path(index, edges, assigned):
    queue, parents = [index], {}
    for current in queue:
        for other in edges[current]:
            if other in parents:
                continue
            parents[other] = current
            if other not in assigned:
                return other, parents
            queue.append(assigned[other])
    return None, parents


def augment_assignment(available, parents, assigned):
    previous = {requested: candidate for candidate, requested in assigned.items()}
    while available is not None:
        requested = parents[available]
        assigned[available] = requested
        available = previous.get(requested)


def single_author_aliases(target, requested):
    return list(target.get("author_variants") or []) if len(requested) == 1 else []


def best_author_score(author, aliases, available):
    return max(author_similarity(name, other) for name in [author] + aliases
               for other in available)


def journal_tokens(value):
    return [word for word in normalize_text(value).split() if word not in _STOPWORDS]


def journal_word_match(left, right):
    shortest = min(len(left), len(right))
    return (left == right or journal_initial_match(left, right) or
            shortest >= 3 and (left.startswith(right) or right.startswith(left)))


def journal_initial_match(left, right):
    return right in _JOURNAL_INITIALS.get(left, set()) or left in _JOURNAL_INITIALS.get(right, set())


def journal_similarity(left, right):
    a, b = journal_tokens(left), journal_tokens(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if journal_acronym(a, b) or journal_acronym(b, a):
        return 0.95
    return journal_prefix_score(a, b)


def journal_acronym(left, right):
    return len(left) == 1 and len(left[0]) >= 3 and left[0] == "".join(w[0] for w in right)


def journal_prefix_score(a, b):
    matched = sum(journal_word_match(x, y) for x, y in zip(a, b))
    return 0.94 * matched / max(len(a), len(b))


def best_journal_score(target, candidate):
    return best_comparison(target, candidate, "journal", journal_similarity)[0]


def identifier_values(record, key):
    value = (record.get("identifiers") or {}).get(key)
    values = value if isinstance(value, list) else [value]
    return {normalize_identifier(item, key) for item in values if item}


def normalize_identifier(value, key):
    text = str(value or "").strip().casefold()
    if key == "doi":
        return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text)
    if key in {"isbn", "issn", "pmid"}:
        return re.sub(r"[^0-9x]", "", text)
    if key == "openalex":
        return text.rstrip("/").split("/")[-1]
    return text

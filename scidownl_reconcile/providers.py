"""Public metadata discovery, independent of any document downloader.

Each provider receives up to five title queries: the original and four explicit
title variants. Queries do not guess translations. Each request returns at most
25 records. Exact identifier requests are additional and bounded to four.
"""

from urllib.parse import quote, urlencode, urlparse

from .manifest import normalize_record
from .transport import get_json, redact


BASES = {"crossref": "https://api.crossref.org/works", "openalex": "https://api.openalex.org/works"}
CROSSREF_FIELDS = (
    "DOI", "title", "author", "published", "published-print", "published-online",
    "issued", "container-title", "short-container-title", "ISBN", "ISSN", "type",
    "edition-number", "language", "volume", "issue", "page", "publisher",
)
OPENALEX_FIELDS = (
    "id", "doi", "ids", "title", "display_name", "authorships", "publication_year",
    "type", "language", "biblio", "primary_location", "locations", "best_oa_location",
)


def _values(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if item]
    return [str(value).strip()] if value else []


def _first(value):
    values = _values(value)
    return values[0] if values else ""


def _pick(record, fields):
    return {key: record[key] for key in fields if key in record}


def _identifier(target, kind):
    value = target.get("identifiers", {}).get(kind) or target.get(kind)
    value = _first(value)
    if kind == "doi":
        return value.removeprefix("https://doi.org/").removeprefix("http://doi.org/").removeprefix("doi:").lower()
    if kind == "pmid":
        return value.rstrip("/").split("/")[-1].removeprefix("pmid:")
    if kind == "openalex":
        return value.rstrip("/").split("/")[-1]
    return value


def _titles(target):
    titles = _values(target.get("title")) + _values(target.get("title_variants"))[:4]
    return list(dict.fromkeys(titles))[:5]


def _bibliographic(target, title):
    authors = " ".join(_values(target.get("authors")))
    values = (title, authors, str(target.get("year") or ""), target.get("journal", ""))
    return " ".join(str(value) for value in values if value)


def _url(provider, params, options):
    params = dict(params)
    if options.get("mailto"):
        params["mailto"] = options["mailto"]
    return BASES[provider] + "?" + urlencode(params)


def _exact_urls(provider, target):
    kinds = ("doi",) if provider == "crossref" else ("doi", "pmid", "openalex")
    urls = []
    for kind in kinds:
        value = _identifier(target, kind)
        if value:
            prefix = "" if provider == "crossref" or kind == "openalex" else kind + ":"
            urls.append(("identifier:" + kind, BASES[provider] + "/" + quote(prefix + value, safe="")))
    return urls


def _crossref_queries(target, options):
    limit = min(25, max(1, int(options.get("limit", 5))))
    queries = _exact_urls("crossref", target)
    isbn = _identifier(target, "isbn")
    if isbn:
        params = {"filter": "isbn:" + isbn, "rows": limit}
        queries.append(("identifier:isbn", _url("crossref", params, options)))
    for title in _titles(target):
        params = {"query.bibliographic": _bibliographic(target, title), "rows": limit}
        queries.append(("bibliographic", _url("crossref", params, options)))
    return queries


def _openalex_queries(target, options):
    limit = min(25, max(1, int(options.get("limit", 5))))
    queries = _exact_urls("openalex", target)
    for title in _titles(target):
        params = {"search": title, "per_page": limit}
        queries.append(("title", _url("openalex", params, options)))
    return queries


def _crossref_year(record):
    for field in ("published", "published-print", "published-online", "issued"):
        parts = (record.get(field) or {}).get("date-parts", [])
        if parts and parts[0]:
            return parts[0][0]
    return None


def _crossref_authors(record):
    authors = record.get("author") or []
    return [author.get("name") or " ".join(filter(None, (author.get("given"), author.get("family")))) for author in authors]


def _crossref_ids(record):
    ids = {"doi": str(record.get("DOI", "")).lower()}
    ids.update(isbn=_values(record.get("ISBN")), issn=_values(record.get("ISSN")))
    return {key: value for key, value in ids.items() if value}


def _provenance(provider, record, context):
    fields = CROSSREF_FIELDS if provider == "crossref" else OPENALEX_FIELDS
    source_id = record.get("DOI") if provider == "crossref" else record.get("id")
    raw = _pick(record, fields) if provider == "crossref" else _openalex_raw(record)
    return {"provider": provider, "source_id": source_id, "request_url": context["url"],
            "retrieved_at": context["retrieved_at"], "raw_metadata": raw}


def _raw_location(location):
    raw = _pick(location, ("is_oa", "license", "version", "pdf_url", "landing_page_url"))
    raw["source"] = _pick(location.get("source") or {}, ("id", "display_name", "issn", "type"))
    return raw


def _openalex_raw(record):
    raw = _pick(record, OPENALEX_FIELDS)
    raw["authorships"] = [{"author": _pick(item.get("author") or {}, ("id", "display_name"))} for item in record.get("authorships") or []]
    raw["locations"] = [_raw_location(item) for item in record.get("locations") or []]
    for field in ("primary_location", "best_oa_location"):
        raw[field] = _raw_location(record.get(field) or {})
    return raw


def _crossref_details(record):
    return {"type": record.get("type", ""), "edition": record.get("edition-number", ""),
            "language": record.get("language", ""), "volume": record.get("volume", ""),
            "issue": record.get("issue", ""), "pages": record.get("page", "")}


def _crossref_candidate(record, context):
    journals = _values(record.get("container-title"))
    variants = journals[1:] + _values(record.get("short-container-title"))
    candidate = {"title": _first(record.get("title")), "authors": _crossref_authors(record),
                 "year": _crossref_year(record), "journal": _first(journals),
                 "journal_variants": variants, "identifiers": _crossref_ids(record)}
    candidate.update(_crossref_details(record))
    candidate.update(sources=[_provenance("crossref", record, context)], oa_locations=[])
    return candidate


def _openalex_ids(record):
    ids = record.get("ids") or {}
    target = {"identifiers": dict(ids, doi=record.get("doi") or ids.get("doi"), openalex=record.get("id"))}
    normalized = {kind: _identifier(target, kind) for kind in ("doi", "pmid", "openalex")}
    source = (record.get("primary_location") or {}).get("source") or {}
    normalized["issn"] = _values(source.get("issn"))
    return {key: value for key, value in normalized.items() if value}


def _openalex_details(record):
    biblio = record.get("biblio") or {}
    pages = "-".join(_values(biblio.get("first_page")) + _values(biblio.get("last_page")))
    return {"type": record.get("type", ""), "edition": "", "language": record.get("language", ""),
            "volume": biblio.get("volume", ""), "issue": biblio.get("issue", ""), "pages": pages}


def _location_urls(location):
    if location.get("is_oa") is not True:
        return []
    urls = _values(location.get("pdf_url")) + _values(location.get("landing_page_url"))
    return list(dict.fromkeys(url for url in urls if _http_url(url)))


def _http_url(url):
    parsed = urlparse(url)
    return parsed.scheme in ("https", "http") and bool(parsed.netloc)


def _oa_entry(url, location, context):
    source = location.get("source") or {}
    evidence = _pick(location, ("is_oa", "license", "version", "pdf_url", "landing_page_url"))
    provenance = dict(context, location_source=_pick(source, ("id", "display_name", "type")), raw_location=evidence)
    return {"url": url, "provider": "openalex", "source_id": source.get("id"),
            "license": location.get("license"), "version": location.get("version"),
            "is_oa": True, "provenance": provenance}


def _oa_locations(record, context):
    locations = list(record.get("locations") or [])
    locations.extend(filter(None, (record.get("primary_location"), record.get("best_oa_location"))))
    entries = {}
    for location in locations:
        for url in _location_urls(location):
            entries[url] = _oa_entry(url, location, context)
    return list(entries.values())


def _openalex_candidate(record, context):
    source = (record.get("primary_location") or {}).get("source") or {}
    candidate = {"title": record.get("title") or record.get("display_name", ""), "authors": _openalex_authors(record),
                 "year": record.get("publication_year"), "journal": _journal_name(source),
                 "journal_variants": [], "identifiers": _openalex_ids(record)}
    candidate.update(_openalex_details(record))
    candidate.update(sources=[_provenance("openalex", record, context)], oa_locations=_oa_locations(record, context))
    return candidate


def _openalex_authors(record):
    return [(item.get("author") or {}).get("display_name", "") for item in record.get("authorships") or []]


def _journal_name(source):
    if source.get("type") in ("journal", "conference"):
        return source.get("display_name", "")
    return ""


def _records(provider, response):
    if provider == "crossref":
        message = response.get("message")
        if not isinstance(message, dict):
            raise ValueError("Missing Crossref message")
        return message.get("items", [message])
    return response.get("results", [response])


def _convert(provider, response, context):
    converter = _crossref_candidate if provider == "crossref" else _openalex_candidate
    records = _records(provider, response)
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ValueError("Invalid records")
    if provider == "openalex" and "results" not in response and not response.get("id"):
        raise ValueError("Missing OpenAlex record")
    return [_validated_candidate(converter(record, context)) for record in records]


def _validated_candidate(candidate):
    candidate.update(normalize_record(candidate))
    return candidate


def _query_error(provider, url, result):
    error = {"provider": provider, "request_url": url, "error": result["error"]}
    if "http_status" in result:
        error["http_status"] = result["http_status"]
    return error


def _query_result(provider, query, options):
    kind, url = query
    result = get_json(url, provider, options, _metadata_valid)
    url = redact(url, options)
    evidence = {"provider": provider, "type": kind, "request_url": url}
    if result.get("error"):
        if _identifier_not_found(kind, result):
            return [], None, dict(evidence, status="complete", candidate_count=0, http_status=404)
        return [], _query_error(provider, url, result), dict(evidence, status="failed")
    return _converted_result(provider, result, evidence)


def _metadata_valid(response, provider):
    try:
        _convert(provider, response, {"url": "", "retrieved_at": ""})
    except (ValueError, TypeError, AttributeError, IndexError):
        return False
    return True


def _identifier_not_found(kind, result):
    return kind.startswith("identifier:") and kind != "identifier:isbn" and result.get("http_status") == 404


def _converted_result(provider, result, evidence):
    context = {"url": evidence["request_url"], "retrieved_at": result["retrieved_at"]}
    try:
        candidates = _convert(provider, result["data"], context)
    except (ValueError, TypeError, AttributeError, IndexError):
        error = _query_error(provider, evidence["request_url"], {"error": "invalid_metadata"})
        return [], error, dict(evidence, status="failed")
    evidence.update(status="complete", candidate_count=len(candidates), retrieved_at=result["retrieved_at"])
    evidence["cache"] = result.get("cache", "disabled")
    return candidates, None, evidence


def _run_provider(provider, target, options, output):
    builder = _crossref_queries if provider == "crossref" else _openalex_queries
    for query in builder(target, options):
        candidates, error, evidence = _query_result(provider, query, options)
        output["candidates"].extend(candidates)
        output["queries"].append(evidence)
        if error:
            output["errors"].append(error)


def discover(target, options):
    """Discover candidates with explicit query evidence and provider failures."""
    output = {"candidates": [], "errors": [], "queries": []}
    providers = list(dict.fromkeys(options.get("providers", ["crossref", "openalex"])))
    for provider in providers:
        if provider not in BASES:
            output["errors"].append({"provider": provider, "error": "unsupported_provider"})
            continue
        _run_provider(provider, target, options, output)
    return output

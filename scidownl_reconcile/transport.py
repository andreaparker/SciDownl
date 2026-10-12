"""Bounded JSON requests and integrity-checked caches; never cache credentials."""

import hashlib
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class SafeRedirect(HTTPRedirectHandler):
    """Keep bearer credentials on their original HTTPS host."""

    def redirect_request(self, *args):
        # Delegate urllib's positional callback without increasing API arity.
        req, newurl = args[0], args[-1]
        redirected = super().redirect_request(*args)
        if redirected is None:
            return None
        if urlparse(newurl).netloc != urlparse(req.full_url).netloc:
            redirected.remove_header("Authorization")
        if urlparse(newurl).scheme != "https":
            raise URLError("Non-HTTPS redirect refused")
        return redirected


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_serialize(value).encode("utf-8")).hexdigest()


def _cache_path(identity, options):
    if not options.get("cache_dir"):
        return None
    return Path(options["cache_dir"]) / (_digest(identity) + ".json")


def _valid_cache(cached, identity):
    if cached.get("schema_version") != 1 or cached.get("request") != identity:
        return False
    if cached.get("response_sha256") != _digest(cached.get("response")):
        return False
    datetime.fromisoformat(cached["retrieved_at"])
    return isinstance(cached.get("response"), dict)


def _load_cache(path, identity):
    if path is None:
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if _valid_cache(cached, identity):
            return cached
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def _store_cache(path, identity, result):
    if path is None:
        return "disabled"
    cached = {"schema_version": 1, "request": identity}
    cached.update(response=result["data"], retrieved_at=result["retrieved_at"])
    cached["response_sha256"] = _digest(result["data"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_serialize(cached), encoding="utf-8")
    except OSError:
        return "write_failed"
    return "stored"


def _request(url, provider, options):
    headers = {"Accept": "application/json", "User-Agent": "SciDownl-Reconcile/1.0"}
    if provider == "openalex" and options.get("api_key"):
        headers["Authorization"] = "Bearer " + options["api_key"]
    return Request(url, headers=headers)


def _read_response(request, options):
    timeout = min(60.0, max(0.1, float(options.get("timeout", 20))))
    with build_opener(SafeRedirect()).open(request, timeout=timeout) as response:
        content = response.read(MAX_RESPONSE_BYTES + 1)
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("Response exceeds 16 MiB")
    return _parse_response(content, options)


def redact(value, options):
    """Remove credentials from evidence, cache identities, and API payloads."""
    key = options.get("api_key")
    if key:
        for representation in (key, quote(key, safe=""), quote_plus(key)):
            value = value.replace(representation, "[REDACTED]")
    return value


def _parse_response(content, options):
    data = json.loads(redact(content.decode("utf-8"), options))
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def _retry_delay(headers, attempt):
    value = (headers or {}).get("Retry-After", "")
    try:
        return min(30.0, max(0.0, float(value)))
    except ValueError:
        return _retry_date(value, attempt)


def _retry_date(value, attempt):
    try:
        seconds = parsedate_to_datetime(value).timestamp() - time.time()
        return min(30.0, max(0.0, seconds))
    except (ValueError, TypeError, OverflowError):
        return min(30.0, 2.0 ** attempt)


def _http_failure(error, attempt, retries):
    if error.fp is not None:
        error.close()
    retryable = error.code == 429 or 500 <= error.code < 600
    if retryable and attempt < retries:
        time.sleep(_retry_delay(error.headers, attempt))
        return None
    return {"data": None, "error": "http_error", "http_status": error.code}


def _fetch(request, options):
    retries = min(3, max(0, int(options.get("retries", 2))))
    for attempt in range(retries + 1):
        try:
            return {"data": _read_response(request, options), "retrieved_at": _stamp()}
        except HTTPError as error:
            failure = _http_failure(error, attempt, retries)
            if failure:
                return failure
        except (URLError, OSError, TimeoutError):
            return {"data": None, "error": "network_error"}
        except (ValueError, UnicodeError):
            return {"data": None, "error": "invalid_response"}
    return {"data": None, "error": "retry_exhausted"}


def _provider_records(data, provider):
    if provider == "crossref":
        message = data.get("message")
        if not isinstance(message, dict):
            return None
        return message.get("items", [message])
    return data.get("results", [data])


def _valid_provider_data(data, provider, validator):
    records = _provider_records(data, provider)
    identifier = "DOI" if provider == "crossref" else "id"
    if not isinstance(records, list):
        return False
    valid = all(isinstance(item, dict) and isinstance(item.get(identifier), str) for item in records)
    return valid and (validator is None or validator(data, provider))


def get_json(url, provider, options, validator=None):
    """Return data/error, retrieval timestamp, and cache state for a safe URL."""
    identity = {"provider": provider, "url": redact(url, options)}
    path = _cache_path(identity, options)
    cached = _load_cache(path, identity)
    if cached and _valid_provider_data(cached["response"], provider, validator):
        return {"data": cached["response"], "retrieved_at": cached["retrieved_at"], "cache": "hit"}
    result = _fetch(_request(url, provider, options), options)
    if result["data"] is not None:
        if not _valid_provider_data(result["data"], provider, validator):
            return {"data": None, "error": "invalid_metadata"}
        result["cache"] = _store_cache(path, identity, result)
    return result

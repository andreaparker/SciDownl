"""Validated CSV/JSON input and lossless, atomic JSON output."""

import csv
import json
import os
import re
import tempfile
from pathlib import Path

from .normalize import identifier_values

LIST_FIELDS = ("authors", "title_variants", "author_variants", "journal_variants")
TEXT_FIELDS = ("title", "journal", "type", "edition", "language", "volume", "issue", "pages")
IDENTIFIERS = ("doi", "pmid", "isbn", "openalex", "issn")


def strings(value):
    """CSV lists use semicolons; a surname comma belongs to the name."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(";") if part.strip()]
    validate_string_list(value)
    return [item.strip() for item in value if item.strip()]


def validate_string_list(value):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("List fields must be string arrays or semicolon-separated text")


def identifier(value, kind):
    if kind in ("isbn", "issn"):
        return strings(value)
    if value is None:
        return ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("Identifier must be text or an integer")
    result = str(value).strip()
    if kind == "doi":
        return doi_identifier(result)
    if kind == "pmid" and result and not result.isdigit():
        raise ValueError("PMID must contain digits only")
    return result


def doi_identifier(value):
    result = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    if result and not re.fullmatch(r"10\.\d{4,9}/\S+", result, re.I):
        raise ValueError("Invalid DOI syntax: " + result)
    return result.lower()


def identifiers(row):
    nested = row.get("identifiers", {})
    if nested is None:
        nested = {}
    if not isinstance(nested, dict):
        raise ValueError("identifiers must be an object")
    result = dict(nested)
    for key in IDENTIFIERS:
        value = input_identifier(row, nested, key)
        if value:
            result[key] = value
        else:
            result.pop(key, None)
    return result


def input_identifier(row, nested, key):
    primary = identifier(row.get(key), key)
    stored = identifier(nested.get(key), key)
    if primary and stored:
        left = identifier_values({"identifiers": {key: primary}}, key)
        right = identifier_values({"identifiers": {key: stored}}, key)
        if left != right:
            raise ValueError("Conflicting top-level and nested " + key)
    return primary or stored


def publication_year(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not re.fullmatch(r"\d{4}", str(value)):
        raise ValueError("year must be a four-digit integer")
    return int(value)


def normalize_record(row):
    if not isinstance(row, dict):
        raise ValueError("Each manifest record must be an object")
    result = {key: strings(row.get(key)) for key in LIST_FIELDS}
    for key in TEXT_FIELDS:
        value = row.get(key) or ""
        if not isinstance(value, str):
            raise ValueError(key + " must be text")
        result[key] = value.strip()
    result.update(year=publication_year(row.get("year")), identifiers=identifiers(row))
    if not result["title"] and not result["identifiers"]:
        raise ValueError("Each record needs a title or publication identifier")
    return result


def read_json(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def read_rows(path, delimiter):
    if Path(path).suffix.lower() == ".csv":
        with Path(path).open(newline="", encoding="utf-8-sig") as stream:
            return list(csv.DictReader(stream, delimiter=delimiter))
    data = read_json(path)
    if isinstance(data, dict):
        data = data.get("records")
    if not isinstance(data, list):
        raise ValueError("JSON manifest must be an array or an object with records array")
    return data


def remap(row, mapping):
    result = dict(row)
    for canonical, source in mapping.items():
        if source in row:
            result[canonical] = row[source]
    return result


def load_manifest(path, mapping=None, delimiter=","):
    results, seen = [], set()
    for index, original in enumerate(read_rows(path, delimiter), start=1):
        record = input_record(original, mapping or {}, index)
        record_id = record["record_id"]
        if record_id in seen:
            raise ValueError("Duplicate record_id: " + record_id)
        seen.add(record_id)
        results.append(record)
    return results


def input_record(original, mapping, index):
    if not isinstance(original, dict):
        raise ValueError("Record {} must be an object".format(index))
    mapped = remap(original, mapping)
    raw_id = mapped.get("record_id")
    record_id = str(raw_id) if raw_id not in (None, "") else "row-{}".format(index)
    try:
        target = normalize_record(mapped)
    except ValueError as error:
        raise ValueError("Record {}: {}".format(record_id, error)) from error
    target["record_id"] = record_id
    return dict(record_id=record_id, original=original, target=target)


def write_json(path, value):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".reconcile-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

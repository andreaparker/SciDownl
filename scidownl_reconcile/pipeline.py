"""Discovery, decision, and worker handoff without queue or download access."""

from collections import Counter
from datetime import datetime, timezone

from . import __version__
from .decisions import reconcile
from .manifest import normalize_record
from .providers import discover


def offline_discovery(target, data):
    key = target["record_id"]
    if key not in data:
        return dict(candidates=[], queries=[], errors=[{"provider": "offline", "error": "missing record_id"}])
    candidates = []
    for raw in data[key]:
        normalized = normalize_record(raw)
        candidates.append(dict(raw, **normalized))
    return dict(candidates=candidates, queries=[], errors=[])


def worker_input(selected):
    ids = selected.get("identifiers", {})
    for kind in ("doi", "pmid"):
        if ids.get(kind):
            value = ids[kind]
            if isinstance(value, str):
                return dict(source_type=kind, source_keyword=value)
    return dict(source_type="title", source_keyword=selected.get("title", ""))


def process_record(record, options, offline):
    target = record["target"]
    discovery = discover(target, options) if offline is None else offline_discovery(target, offline)
    decision = reconcile(target, discovery["candidates"])
    complete = not discovery["errors"]
    # A failed provider may conceal a competing edition or match. Withhold export.
    if not complete and decision["status"] == "verified":
        decision.update(status="plausible", selected=None)
    selected = decision.get("selected")
    handoff = worker_input(selected) if selected else None
    ready = bool(handoff and handoff["source_keyword"] and complete)
    return dict(record, **decision, discovery=discovery, acquisition_ready=ready,
                worker_input=handoff if ready else None)


def run_pipeline(records, options, offline=None):
    results = [process_record(record, options, offline) for record in records]
    counts = Counter(record["status"] for record in results)
    return dict(schema_version="1.0", tool_version=__version__,
                generated_at=datetime.now(timezone.utc).isoformat(),
                summary=dict(input_rows=len(results), statuses=dict(counts),
                             ready_rows=sum(record["acquisition_ready"] for record in results)),
                records=results)


def worker_manifest(result):
    records = []
    for record in result["records"]:
        if record["acquisition_ready"]:
            records.append(dict(record_id=record["record_id"], **record["worker_input"],
                                identifiers=record["selected"].get("identifiers", {}),
                                bibliographic=worker_bibliographic(record["selected"]),
                                oa_locations=record["selected"].get("oa_locations", []),
                                sources=record["selected"].get("sources", [])))
    return dict(schema_version="1.0", records=records)


def worker_bibliographic(selected):
    fields = ("title", "authors", "year", "journal", "type", "edition", "language", "volume", "issue", "pages")
    return {field: selected.get(field) for field in fields}

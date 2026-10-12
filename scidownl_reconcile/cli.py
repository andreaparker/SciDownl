"""Standalone reconciliation command using only Python's standard library."""

import argparse
import os
import sys
from pathlib import Path

from .manifest import load_manifest, read_json, write_json
from .pipeline import run_pipeline, worker_manifest


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    input_arguments(command)
    discovery_arguments(command)
    runtime_arguments(command)
    return command


def input_arguments(command):
    command.add_argument("manifest", type=Path, help="CSV or JSON acquisition targets")
    command.add_argument("--output", required=True, type=Path, help="Full audit manifest (JSON)")
    command.add_argument("--worker-output", type=Path, help="Verified records only (JSON)")
    command.add_argument("--field-map", type=Path, help="JSON canonical-field to input-column mapping")
    command.add_argument("--delimiter", default=",")
    command.add_argument("--offline-candidates", type=Path, help="JSON record_id -> normalized candidate array")


def discovery_arguments(command):
    command.add_argument("--providers", nargs="+", choices=("crossref", "openalex"), default=["crossref", "openalex"])
    command.add_argument("--limit", type=int, default=5, help="Candidates per query, 1..25")
    command.add_argument("--mailto", default="", help="Contact email for Crossref polite requests")
    command.add_argument("--openalex-key-env", default="OPENALEX_API_KEY", help="Environment variable containing key")
    command.add_argument("--cache-dir", type=Path, help="Cache successful metadata responses")


def runtime_arguments(command):
    command.add_argument("--timeout", type=float, default=15, help="HTTP timeout in seconds, 0..60")
    command.add_argument("--retries", type=int, default=2, help="Retries per query, 0..3")


def validate_args(args):
    if len(args.delimiter) != 1:
        raise ValueError("delimiter must be one character")
    if not 1 <= args.limit <= 25 or not 0 < args.timeout <= 60 or not 0 <= args.retries <= 3:
        raise ValueError("limit, timeout, or retries outside allowed bounds")
    validate_paths(args)


def validate_paths(args):
    paths = [args.manifest, args.field_map, args.offline_candidates]
    outputs = [args.output, args.worker_output]
    inputs = {path.resolve() for path in paths if path}
    destinations = [path.resolve() for path in outputs if path]
    if inputs.intersection(destinations) or len(set(destinations)) != len(destinations):
        raise ValueError("Output paths must differ from input paths and each other")


def options(args):
    return dict(providers=args.providers, limit=args.limit, mailto=args.mailto,
                api_key=os.environ.get(args.openalex_key_env, ""),
                cache_dir=args.cache_dir, timeout=args.timeout, retries=args.retries)


def configuration(args):
    mapping = read_json(args.field_map) if args.field_map else {}
    if not isinstance(mapping, dict) or not all(isinstance(value, str) for value in mapping.values()):
        raise ValueError("field-map must be a JSON object mapping canonical fields to column names")
    offline = read_json(args.offline_candidates) if args.offline_candidates else None
    if offline is not None and not isinstance(offline, dict):
        raise ValueError("offline-candidates must be a JSON object keyed by record_id")
    return mapping, offline


def execute(args):
    validate_args(args)
    mapping, offline = configuration(args)
    records = load_manifest(args.manifest, mapping, args.delimiter)
    result = run_pipeline(records, options(args), offline)
    write_json(args.output, result)
    if args.worker_output:
        write_json(args.worker_output, worker_manifest(result))
    summary = result["summary"]
    print("{} rows; {} verified worker inputs. Audit: {}".format(
        summary["input_rows"], summary["ready_rows"], args.output))
    return 2 if any(record["discovery"]["errors"] for record in result["records"]) else 0


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    try:
        return execute(args)
    except (OSError, ValueError, TypeError) as error:
        print("reconciliation error: " + str(error), file=sys.stderr)
        return 1

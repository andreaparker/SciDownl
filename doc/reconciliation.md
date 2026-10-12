# Bibliographic reconciliation

`scidownl-reconcile` resolves bibliographic targets through public Crossref and
OpenAlex metadata. It ranks and audits candidates, then exports verified inputs
for a separate acquisition process. It does not download documents, import the
Sci-Hub task package, access acquisition queues, or update worker runtime state.
Python 3.9 or newer is required for this component. The new package uses only the
standard library.

From a checkout, no installation or download dependencies are needed:

```sh
python -m scidownl_reconcile unresolved.csv \
  --output reconciliation.json --worker-output verified-worker-inputs.json \
  --mailto you@example.org --cache-dir metadata-cache
```

Installing SciDownl also installs the `scidownl-reconcile` entry point. The
existing SciDownl installation still has its original download dependencies;
they are not required for running this module directly. Optional OpenAlex
credentials are read from `OPENALEX_API_KEY`, or a variable named with
`--openalex-key-env`. They are sent in an Authorization header and redacted from
output and caches. Choose one provider with `--providers crossref` or
`--providers openalex`. Provider limits and availability can change; review
[Crossref's REST API documentation](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
and [OpenAlex authentication](https://help.openalex.org/api/authentication/)
before a large run.

## Input contract

CSV uses UTF-8 (BOM accepted), comma-separated columns, and semicolons within
list fields. A surname comma stays part of an author name, so quote it in CSV.
`--delimiter` changes the column delimiter. JSON accepts a record array or
`{"records": [...]}`. Each record needs a title or publication identifier.

| Field | Format / meaning |
| --- | --- |
| `record_id` | Stable manifest row ID; generated as `row-N` if missing. Duplicate IDs fail validation. |
| `title`, `journal` | Bibliographic text. |
| `authors` | JSON string array, or semicolon-separated CSV names; initials and surname-first names supported. |
| `year` | Four-digit year; absent values remain unknown. |
| `doi`, `pmid`, `openalex` | Single identifier; JSON may instead nest them in `identifiers`. DOI URLs normalize to bare DOI. |
| `isbn`, `issn` | JSON array or semicolon-separated text; ISBN distinguishes editions. |
| `type` | E.g. `journal-article`, `article`, `book`, `monograph`, `book-chapter`. |
| `edition`, `language`, `volume`, `issue`, `pages` | Optional distinguishing metadata, retained in the handoff. Use compatible formats across records. |
| `title_variants`, `author_variants`, `journal_variants` | Explicit aliases, as arrays or semicolon-separated text. |

Unknown input fields are preserved in `original`, including any worker notes or
alias provenance. Keep aliases sourced and edition-specific: an English
translation of a title may identify an original paper, but a translated book
may be a distinct edition. The tool does not infer translations. Cyrillic
transliteration, Latin accents and ligatures, selected historical spellings,
author initials, and journal word-prefix abbreviations are normalized. Other
transliteration systems and journal series variants can use explicit aliases.
An author alias applies to a single-author target; multi-author lists should
supply compatible names directly.

A custom column map is JSON, for example:

```json
{"record_id": "worker_id", "title": "requested_title", "authors": "byline", "year": "publication_year"}
```

Pass it with `--field-map columns.json`. Mapping is canonical-field to existing
input-column name. It does not discard original columns or deduplicate titles:
multiple rows may represent the same work or different editions.

## Discovery and confidence

Crossref queries combine title, authors, year, and journal in
`query.bibliographic`; DOI and ISBN also have identifier queries. OpenAlex uses
DOI, PMID, or OpenAlex identifiers and text search. Each provider tries the
original title and up to four explicit title variants. Requests default to five
candidates each, adjustable from 1 to 25. Discovery is deliberately bounded;
not finding a work in this candidate window does not establish that it does not
exist. Metadata can be incomplete, mistaken, or absent from both indexes.

The confidence score is an interpretable heuristic, not a calibrated
probability or verification of scientific claims. The initial weighted score
uses title 0.58, authors 0.22, year 0.12, and journal 0.08, normalized over supplied
target fields. Missing candidate fields score zero. Metadata coverage caps,
contradiction guards, identifier evidence and competing matches then constrain
the decision. Missing metadata cannot turn a title-only match into certainty.
Component evidence includes requested and observed values; `match_flags` and
`reasons` explain identifier conflicts, missing fields, and review decisions.

Verification normally requires identical normalized text for the winning title
or explicit alias pair, confidence >=0.91, compatible distinct authors, and
year evidence. Fuzzy title similarity ranks review candidates; a changed
scientific material or process cannot be auto-verified just by a high score. An exact requested identifier can verify a record
with incomplete title metadata, provided no supplied metadata contradicts it. If both titles are present,
fuzzy differences still require review even with an exact identifier.
One-year publication drift is held for review. Title-only matches, unsupported
initials, competing identities within 0.06, and conflicting provider metadata
are held for review. Scientific symbols, opposite terms and part numbers are
checked to reduce near-title false positives. These guards do not guarantee
semantic equivalence for every title.

Same-identifier results are merged with provenance retained. Provider
bibliographic disagreements stay visible and prevent automatic verification.
Books with comparable competing edition signatures become
`ambiguous_editions`; an explicitly requested incompatible edition is rejected.

| Status | Meaning / handoff |
| --- | --- |
| `verified` | Bibliographic identity meets the conservative rules; automatically selected. |
| `plausible` | Candidate evidence warrants review; no automatic selection. |
| `unresolved` | No eligible candidate reached the review threshold; rejected candidates and reasons remain in the audit. |
| `ambiguous_editions` | Multiple book editions or edition evidence need resolution; no automatic selection. |

A verified decision is exported only when discovery completed and a usable
DOI, PMID or title exists. Failed providers withhold automatic export even when
another provider found a strong candidate. HTTP 404 identifier lookups mean no
record in that provider; authentication, network, malformed response and
rate-limit failures remain explicit errors. Exit codes: 0 completed (including
review/unresolved rows), 1 invalid input or local failure, 2 partial discovery
with a written audit. Repeated failures should be investigated before retrying.

Timeout defaults to 15 seconds. Retries default to two, capped at three, and
cover HTTP 429 and 5xx with bounded backoff/Retry-After (at most 30 seconds).
Successful HTTP JSON responses can be cached with request identity, original
retrieval timestamp and SHA256 integrity checks. Candidates are schema-validated before caching; malformed years and
invalid metadata are audited failures, not cached successes. Cached metadata
is not an endorsement of scientific validity.
Caches have no automatic expiration: use a fresh cache directory or remove
selected entries to refresh metadata. No negative HTTP results are cached.

## Output and acquisition handoff

The full audit JSON has `schema_version`, `tool_version`, `generated_at`, a
row-based `summary`, and `records`. Each record retains `record_id`, `original`,
normalized `target`, `status`, `selected`, `ranked_candidates`, `discovery`,
`acquisition_ready` and `worker_input`. Discovery preserves queries and failures.
Candidate sources preserve provider IDs, request URLs, retrieval timestamps and
bibliographic response fields. Scores, components, reasons and match flags are
retained on every ranked candidate, including rejected records.

The worker output is `{"schema_version": "1.0", "records": [...]}` with verified
rows only. Each row carries:

```json
{
  "record_id": "original-row-id",
  "source_type": "doi",
  "source_keyword": "10.0000/synthetic.example",
  "identifiers": {"doi": "10.0000/synthetic.example"},
  "bibliographic": {"title": "...", "authors": ["..."], "year": 2020},
  "oa_locations": [],
  "sources": []
}
```

This synthetic example is not a real publication. `source_type` and
`source_keyword` align with existing SciDownl task inputs. DOI is preferred,
then PMID, then the selected title. ISBN/OpenAlex IDs, edition metadata, and
provenance are retained for workers that support them. An adapter should map
these fields to the actual worker's supported contract once that runtime is
accessible. Do not execute an automatically constructed command line from
untrusted titles; pass identifiers as structured arguments.

OA locations come only from OpenAlex location records explicitly marked
`is_oa: true`. They include URL, source ID, license/version metadata and location
provenance. Both landing pages and PDFs may occur. Crossref full-text links and
license fields alone are not treated as OA. These are provider assertions;
accessibility and rights should be checked at acquisition time. No document
URL is fetched by this tool.

## Applying it to the unresolved collection

1. Export the current unresolved manifest and retain its stable worker IDs,
   original text, edition information and retry history. Do not interpret the
   October 9 count of 382 `no_match` classifications as 382 unique missing works
   or current October 12 totals.
2. Map its columns and validate a small sample. Keep duplicate bibliographic
   rows until their identities and editions have been inspected.
3. Run reconciliation into new output files and inspect `discovery.errors`
   before treating unresolved statuses as completed searches. Supply explicit
   aliases from catalogs or original records, then rerun with provenance saved.
4. Review plausible candidates and ambiguous editions against original catalog
   references. Add confirmed identifiers/metadata to a separate reviewed input
   manifest. Reconcile that manifest again; never bulk promote review rows just
   by lowering thresholds.
5. Apply the current research source-selection registry before citation,
   evidence promotion or training selection. For Andrea's installation it is
   `/Users/andrea/Documents/RESEARCH/policies/source-exclusions.json`; screen
   authors and source URLs with `research-policy check`. Preserve excluded or
   ambiguous originals and provenance in quarantine. Bibliographic identity
   matching does not itself qualify a source for evidence reliance.
6. Give the verified worker manifest to the existing acquisition adapter after
   checking its input contract. This tool neither changes queues nor claims
   recovered documents. Record actual acquisition outcomes separately.

## Offline validation

A complete deterministic CLI example is included:

```sh
python -m scidownl_reconcile doc/examples/reconciliation.csv \
  --offline-candidates doc/examples/candidates.json \
  --output audit-example.json --worker-output workers-example.json
python -m unittest discover -s tests -v
python -m scidownl_reconcile.benchmark tests/fixtures/reconciliation.json \
  --output benchmark.json
```

`--offline-candidates` accepts a JSON object keyed by `record_id`, with an array
of normalized candidates per key. Include an empty array for an intentional
completed no-candidate search; a missing key is an incomplete discovery error.
Offline fixtures are trusted test inputs and are not independently checked
against live providers.

The labeled set contains 48 synthetic cases covering modern and historical
physics metadata, initials, diacritics, transliteration, explicit translations,
journal abbreviations, books and editions, missing fields, and false matches.
Its identifiers and provenance are deliberately synthetic. The report gives
identity precision and recall, candidate/top-three recall, status accuracy,
unsafe automatic decisions, case IDs and per-category metrics. Automatic
recall counts labeled review-required identities too, making the cost of
conservative verification explicit. Fixture candidate recall measures ranking
of supplied records, not live API discovery recall. See `validation.md` for
actual run results and limitations.

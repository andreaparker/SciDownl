# Validation — 12 October 2026

The standalone reconciliation package passed **93 deterministic tests** on
Python **3.12.14** and **3.14.7**. The tests cover input validation and lossless
output, worker readiness, scoring/edition decisions, labeled benchmarks,
mocked public-service responses, bounded retries, cache integrity, credential
handling, malformed metadata, and resource cleanup. No document acquisition is
part of these tests.

The existing fork's **14 local regression tests** passed with Python 3.12.14 and
SQLAlchemy 1.4.54 in an isolated dependency environment. These covered sources,
HTML extraction from a fixed string, SQLite entities/services, URL choosers, and
configuration. The original network-dependent crawler/updater/downloader/task
tests were not run: they require acquisition infrastructure and Sci-Hub access,
which this component does not require. The first local attempt lacked the
legacy dependencies; after installing them in isolation, the 14 tests passed.

The installed `scidownl-reconcile` command also processed the synthetic CSV
example in a clean environment containing SciDownl with **no legacy download
dependencies installed**. It wrote one audited match and one compatible worker
input. A test confirms importing the reconciliation CLI does not load
`scidownl` or its acquisition modules.

The required `/Users/andrea/.local/bin/clanker-complexity-lint` passed for all
16 changed supported source files with the user's strict budgets and no added
suppressions. Its pinned lizard dependency was cached under `/private/tmp`, then
run offline. `git diff --check` passed. The CI workflow runs the offline suite
and uploads its benchmark on Python 3.9, 3.12, and 3.14; remote CI outcomes are
reported separately from these local results.

## Labeled fixture results

The fixtures are **synthetic engineering cases**, not fetched bibliographic
records. Titles resembling historical subjects, authors, identifiers and
provenance serve algorithm tests; they must not be cited as real publications.
The canonical fixture SHA256 is
`437671e7a5444985e8b2d461a46559fcec7ce30a418a11ede6d32ef64f502459`.

| Measure | Actual result |
| --- | --- |
| Cases | 48 |
| Labeled single identities | 32 |
| Correct automatic identities | 27 / 27 automatic selections |
| Automatic precision | 100% |
| Automatic recall | 27 / 32 = 84.375% |
| Ranked candidate recall | 32 / 32 = 100% |
| Top-three candidate recall | 32 / 32 = 100% |
| Automatic false positives | 0 |
| Unsafe automatic decisions on review/no-identity labels | 0 |
| Expected status agreement | 48 / 48 |

Recall includes five labeled identities intentionally held for review:
one-year publication drift, missing target authors, missing target year,
title-only input, and missing candidate authors. Candidate recall measures
ranking of supplied fixtures, not Crossref/OpenAlex retrieval completeness.
Null precision/recall for empty denominators in individual categories is
reported as null, not an invented zero or perfect result.

Independent review exposed false positives in an earlier fuzzy-verification
rule: long scientific titles could differ in hydrogen/deuterium,
absorption/desorption, or palladium/titanium while scoring above 0.94. The final
rule requires normalized equality of the winning explicit title/alias pair
before automatic selection when both titles are present. All three adversarial
cases now remain plausible review candidates. Other regression checks cover
many-to-one author matches, Physical Review A/B confusion, Greek particle
symbols, title part numbers, conflicting identifiers and editions, and malformed
provider years. Zero fixture false positives does not establish a population
false-positive rate; new real-world labels should expand this benchmark.

## Live public-metadata smoke checks

Two final CLI checks queried both public services for one historical paper.
They are metadata-integration checks, not collection recovery counts:

- DOI-only lookup for `10.1002/andp.19053221004`: both Crossref and OpenAlex
  returned a record. The final matcher held it as plausible because their
  language fields disagreed (`en` versus `de`), with the conflict visible in the
  audit and no worker export.
- Title-based lookup with `Zur Elektrodynamik bewegter Korper`, `A. Einstein`,
  1905, and abbreviated journal `Ann. Phys.`: both services returned candidate
  lists; the tool resolved the missing DOI to `10.1002/andp.19053221004` and
  emitted one verified worker input. No supplied DOI was used in this query.

The selected identity/source metadata was screened against Andrea's current
canonical research policy; policy SHA256
`be8c73a078438fd67203caba59198c7e117bf14e9c3d8b4718c486b568d9b05d`.
No source-policy match was found. This is a source-selection check, not a
scientific assessment. OA locations were recorded as provider assertions;
no URLs or documents were downloaded.

The current unresolved acquisition manifest and worker runtime were not
available to this task. No current collection totals, unique missing-document
counts, real collection recall, or recovered-document counts have been
measured. Follow [the application guide](reconciliation.md) once the current
manifest becomes accessible.

## Reproduce

```sh
python -m unittest discover -s tests -v
python -m scidownl_reconcile.benchmark tests/fixtures/reconciliation.json --output benchmark.json
python -m scidownl_reconcile doc/examples/reconciliation.csv \
  --offline-candidates doc/examples/candidates.json \
  --output audit-example.json --worker-output workers-example.json
```

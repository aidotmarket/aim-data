# S1716 S1294 Chunk 2a — canonical parser and tree build

**Status: Chunk 2a implemented and locally verified; committed and pushed,
not merged.** Full baseline/candidate suites completed with **zero branch-only
failures**, all **184 added tests passing**, unchanged original golden bytes,
two observed failing mutations, and successful 1M/10M bounded-worker runs.
Existing baseline failures remain documented; this is not a claim that the
repository-wide suites are green, or that later producer/release chunks exist.

Final implementation: `21ad23f78a530d33ab731029e0dc55a6a06000d8`.
[Per-test pytest parity](s1716-s1294-c2a-pytest-parity.md),
[frontend parity](s1716-s1294-c2a-frontend-parity.md),
[operation/runbook](s1716-s1294-c2a-operating.md),
[acceptance evidence](s1716-s1294-c2a-evidence/README.md).

| Generated records | Complete build seconds | Peak incremental RSS | Success / cancellation cleanup |
|---:|---:|---:|---|
| 1,000,000 | 36.71 | 107.41 MiB | Both passed |
| 10,000,000 | 331.44 | 110.59 MiB | Both passed |

Both benchmark receipts match the final parser/Merkle source SHA-256 values;
selected boundary proofs independently verify. Timings are measured local
results under concurrent verification load, not a throughput guarantee.
Metadata-only receipts include roots, proof digests and RSS; generated source
files and job spill files were cleaned. Scanning, hosting and public preview
limits remain later-chunk responsibilities. No marketplace, router, UI,
device-key, dependency or profile-version change occurred.


## Resumed build, controller ruling fba339b2

The 2026-09-16 controller ruling (subject to Max veto) supersedes the historical
stop below: retain aim-dataset-merkle-v1 and every original golden byte.
Canonical metadata admits JSON integers only in [-(2^53-1), 2^53-1]; reject
all other integers with `unsafe_integer` without echoing the value. Logical
row integers and decimals remain exact descriptor-typed base-10 strings.
No profile version, key, router, marketplace push or UI change is authorized.

Fetched backend origin/main remains 9b6f8c1d590116ffe2792696937a63821336e00c.
Its old permissive serializer is the known defect corrected by this ruling;
the hash domains, tree split, checkpoint bytes and original corpus are retained.

Milestone 1: declared parser, descriptors, metadata models, streaming reader,
reference primitives and initial disk worker implemented. Initial focused run:
127 passed, zero failures (existing environment DuckDB 1.5.3; pinned validation
and full parity still pending). No acceptance/completion claim yet.

Fixture SHA-256:

- `tests/fixtures/aim_dataset_merkle_v1.json`: `f3e358d1e7ce7c836ce8604810675e0952201a7799b92d30858cd489906499af`
- `tests/fixtures/aim_dataset_merkle_v1_extended.json`: `f7512f42d43d9b55b80eab93505acc4a2049bbb0575d3e971f1283cb095bbe8e`

## Milestone 2: parser and worker hardening

The complete reproducer signing object is now also a must-reject extended vector.
Real-file corpus `tests/fixtures/aim_dataset_parser_v1.json` includes actual file
bytes for CSV, TSV, JSON-array, NDJSON and Parquet, explicit declarations,
expected canonical rows/schema/root, and a distinct missing-property control.
Admitted JSON object ordering is independently compared with Node UTF-16 serialization;
descriptor arrays retain NFC UTF-8 order. Numeric strings preserve >53-bit values.
Parquet timestamps are decoded from Arrow integer ticks, including nanoseconds,
without a Python datetime or float intermediary that loses fractional precision.

Worker tests cover external merge passes, duplicates, private permissions,
startup recovery, concurrent-job refusal, mid-merge cancellation, low RSS/disk,
source mutation after reading, symlinks, wide rows and record-size rejection.
The adapter does not allocate a DuckDB connection: it uses bounded explicit
text parsing and Arrow batches, so the 128 MiB DuckDB allocation budget is unused.
Pinned DuckDB 0.9.2 resolved-type dispatch is separately executed in Python 3.11.

First complete comparison: baseline 2219 passed / 92 failed / 33 skipped / 38
live-backend setup errors; candidate 2360 passed / the identical 92 failed /
33 skipped / 38 setup errors. Every existing node ID has exactly the same outcome.
The 38 errors are `test_beta_readiness.py`, which requires a running localhost:80
backend; they are outside the requested backend-independent subset and are
retained transparently in the superset run. Later added boundary tests are
passing in focused runs; final full-suite rerun and per-test artifact pending.

The first 1M-record worker run completed in 45.99 seconds, peak incremental RSS
112885760 bytes (107.66 MiB), with successful cleanup and cancellation cleanup.
The leaf-domain-byte and ordinal-endianness mutations each made the golden
assertion fail (exit 1). Full logs and final measurements follow below.

Current fixture SHA-256 (supersedes the early extended-fixture hash above):

- `tests/fixtures/aim_dataset_merkle_v1.json`: `f3e358d1e7ce7c836ce8604810675e0952201a7799b92d30858cd489906499af`
- `tests/fixtures/aim_dataset_merkle_v1_extended.json`: `96893787c9757aaf9a18d4bb21da3d9b8e1acc1133c382ed33cdf98cfdee9a7e`
- `tests/fixtures/aim_dataset_parser_v1.json`: `8ee8ac6b6bdaccd943987afb0a17cbb548a88a7dc7598563a143e7d8ab415754`

## Milestone 3: final reference compatibility

Reference `tests/test_dataset_commitment.py` additionally pins the historical
code-point ordering of generic supplementary-plane object keys. Those keys do
not occur in the closed descriptor parameter vocabulary: field names are array
values. The producer now rejects a generic key set with differing Python/JCS
orders as `noncanonical_key_order`, rather than changing v1 bytes or introducing
a profile. The extended fixture includes this must-reject guard as well as the
complete original unsafe-integer signing-object reproducer. All safe admitted
metadata bytes are identical to reference bytes and independently match Node.

Full field names follow the backend 1..255 NFC character contract. Declared
logical decimal precision may reach the backend cap 1000; physical DuckDB
DECIMAL remains limited to the actual 0.9.2 range (38). Declared timestamps admit
precision 0..9; physical dispatch keeps exact source precision 0/3/6/9.

The final worker emits ready only after complete source identity revalidation
and successful child exit. No scanning, hosting, signatures or egress occur.
The runbook is `s1716-s1294-c2a-operating.md`. Existing UI/publication/keys were
not changed. Final test/evidence attachment follows after the frozen-source run.

## Final acceptance evidence

The final implementation receipt identifies the tested source SHA. A final
ancestor-symlink guard supplements a451608: every source path component is
opened without following symlinks, and temp-root symlink ancestors are refused.
The source-path regression passes alongside leaf-symlink and temp-mode tests. No merge, release,
marketplace request, router/UI change or key operation was performed.

### Reproducible commands and environments

Working directory: the continuation branch worktree
`/private/var/tmp/koskadeux/minimal-bridge-worktrees/ab45d16c9f0c-712a06`.
Baseline: detached `/var/tmp/s1716-c2a-baseline`, fetched origin/main
`ee7faa457a21d751bc581b6733ac9a40bb80a801`. Backend reference fetched and read at
`9b6f8c1d590116ffe2792696937a63821336e00c`.

All commands used the required `rtk` prefix. `PY` below denotes the existing
`/Users/max/Projects/ai-market/aim-data/.venv/bin/python` executable; `PINNED`
denotes `/var/tmp/s1716-c2a-py311/bin/python` in a separate temporary environment.
No repository dependency pin or shared environment was changed.

```sh
rtk proxy "$PY" -m pytest tests --ignore=tests/test_beta_readiness.py --continue-on-collection-errors -q --junitxml=<result.xml>
rtk proxy "$PY" -m pytest tests/test_dataset_canonicalization.py tests/test_dataset_merkle_service.py -q
rtk proxy env PYTHONPATH=. "$PINNED" -m pytest --noconftest tests/test_dataset_canonicalization.py tests/test_dataset_merkle_service.py -q
rtk proxy env PYTHONPATH=. "$PY" docs/reports/s1716-s1294-c2a-benchmark.py 1000000
rtk proxy env PYTHONPATH=. "$PY" docs/reports/s1716-s1294-c2a-benchmark.py 10000000
rtk proxy "$PY" docs/reports/s1716-s1294-c2a-mutations.py
rtk proxy ruff check app tests
rtk npm test
rtk npm run build
rtk npm run lint
rtk proxy node node_modules/typescript/bin/tsc --noEmit -p tsconfig.app.json
```

Full regression environment: macOS 27.0 arm64; Python 3.12.12; pytest 7.4.4;
pytest-asyncio 0.23.3; Pydantic 2.13.4; DuckDB 1.5.3; PyArrow 24.0.0;
NumPy 1.26.4; psutil 5.9.8; Node 25.6.0; Ruff 0.15.7.
Pinned focused environment: Python 3.11, DuckDB 0.9.2, NumPy 1.26.4,
PyArrow 25.0.1, Pydantic 2.13.5, pytest 9.1.1, psutil 7.2.2.
`--noconftest` avoids unrelated application/database bootstrap in the isolated
parser environment; it does not skip any selected test. The same selected tests
also run with the real repository conftest in the full environment.
The first attempted DuckDB 0.9.2 Python 3.12 source build failed; a compatible
Python 3.11 environment resolved that tooling issue without changing the pin.

### Coverage and interpretation

- Every dispatch-table row is executable: all listed scalar aliases, declared
  LIST/STRUCT children, timestamp precisions and offsets, and every unsupported
  category (including unsupported nested children and future types) fail closed.
- Real file bytes/declarations for all five formats share exact schema/rows/root
  only when their logical records match. Missing JSON properties remain distinct
  from null. Parquet exact binary, decimal(38,9), nanoseconds and nested arrays
  have dedicated real-file tests. A separate declared 1000-digit decimal checks
  the backend logical descriptor domain without claiming DuckDB supports it.
- Descriptor depth 15/16/17, nodes 9999/10000/10001, dictionaries 499/500/501,
  nested dictionaries, and field counts 24/25/26 are tested. **All three of
  24/25/26 are valid full-commitment schemas**; the 25-field selected-public-row
  limit belongs to 2b and is not incorrectly imposed on complete datasets.
- Tests cover safe metadata integer boundaries in both signs, the complete
  original unsafe signing object, exact large row integers/decimals, NFC
  collisions, invalid Unicode, supplementary descriptor names, independent
  ECMAScript bytes and generic differing-key-order refusal.
- All original schema/base/leaf/root/root-variant/proof/log/checkpoint bytes are
  asserted. Duplicate ordinals, odd trees, consistency proofs and valid 62/63
  sibling paths plus 64-level refusal are exercised. Both deliberate mutations
  were executed and made the golden assertion fail; neither is present in source.
- External sort, private directory/file modes, startup cleanup, symlink refusal,
  concurrent lock refusal, mid-merge cancellation, RSS/disk ceilings, large/wide
  rows and source changes after reading are tested. Benchmark outputs retain
  source SHA-256 hashes and only metadata, never generated source rows.

The worker reads every source member and revalidates the manifest before
returning. It exposes only digest/proof metadata; its local row-bearing tree
index is job-scoped and removed. It intentionally does not make a public
package, sign a commitment, implement a marketplace route or alter existing
readers. Those remain later chunks, not unreported omissions in 2a.

### Existing baseline failures

The full-suite per-test table records each outcome. None of the following
existing failures was deleted, skipped, weakened or repaired out of scope:

| Existing area | Explanation |
|---|---|
| upload/batch/notifications/PII/text/SQL/S145 file tests | Hard-coded `/data` writes fail on the macOS read-only root filesystem |
| deployment/channel/onboarding/config/source-string assertions | Existing source expectations differ from origin/main behavior (binding, channel use and alias choices) |
| copilot attachment validation | Existing unprovisioned-install gate precedes expected attachment errors |
| entitlement and raw download | Existing tests lack configured signing passphrase/install token |
| diagnostics | Existing zero-duration timing assertion and expected collector-count mismatch |
| metadata | Existing removed `calculate_searchability_score` method and missing searchability field |
| metering | Existing tests call `report_usage` without its required session ID |
| portal/raw-file detail/raw-file registration | Existing missing metadata attribute, draft/listed expectation, and unavailable Starlette status constant |
| request engine | Existing naive/aware datetime and detached SQLAlchemy instance failures |
| S145 async and nginx assertions | Existing non-awaitable processing return and stale nginx timeout/body-size expectations |
| security audit/classification | Existing missing crypto module and stale tool-classification set |

The 33 existing skips are 31 unmarked asynchronous **live allAI** integration
functions (`tests/integration/test_allai_e2e.py`, needs localhost:8080) and two
PostgreSQL self-connection tests. They remain visible in both tables and are
not claimed as passing backend-independent tests. The separately excluded
38 beta-readiness cases require a live localhost:80 backend; the initial
all-tests superset run recorded their setup errors on both refs.

Frontend baseline and candidate each have 44 passed / 23 failed of 67 tests,
with identical per-test statuses. Both builds passed. ESLint reports the same
10 errors / 25 warnings; TypeScript diagnostics are identical. Repository Ruff
reports the same 94 existing errors (one cached invalid-noqa warning appears
only in the initial baseline log). Changed Python files pass Ruff and compile.
There is no configured repository Python type-check command; the configured
frontend TypeScript check was executed explicitly.

### Completed regression results

| Check | origin/main | Candidate | Branch-only failures |
|---|---:|---:|---:|
| Complete backend-independent pytest | 2219 passed / 92 failed / 33 skipped | 2403 passed / 92 failed / 33 skipped | 0 |
| New focused parser/tree tests | Not present | 184 passed | 0 |
| Same focused tests, DuckDB 0.9.2 / Python 3.11.16 | Not present | 184 passed | 0 |
| Frontend Vitest | 44 passed / 23 failed | 44 passed / 23 failed | 0 |
| Frontend production build | Passed | Passed | 0 |
| Frontend ESLint | 10 errors / 25 warnings | Identical | 0 |
| Frontend TypeScript | Existing diagnostics | Identical | 0 |
| Repository Ruff | 94 errors | Same 94 errors | 0 |
| Changed Python Ruff / compilation / diff whitespace | — | Passed | 0 |

The final candidate pytest run completed in 148.59 seconds; baseline completed
in 148.83 seconds. These are completed runs, not collection-only comparisons.
Every pre-existing test has the same outcome and all 184 added tests pass.
Per-test tables: [Python](s1716-s1294-c2a-pytest-parity.md) and
[frontend](s1716-s1294-c2a-frontend-parity.md). Machine-readable counters and
outcome-only logs/XML are in [evidence](s1716-s1294-c2a-evidence/README.md).

An additional actual `DuckDBService.iter_commitment_records` adapter execution
read all 5001 generated Parquet records as a generator, preserving >53-bit
integers, 30-digit decimals and binary bytes. Reproduction:
`rtk proxy env PYTHONPATH=. "$PY" docs/reports/s1716-s1294-c2a-adapter-check.py`.
Its receipt contains only pass indicators/counts. Existing reader methods are
unchanged; their regression outcomes match origin/main in the complete suite.

### Final fixture hashes

- `tests/fixtures/aim_dataset_merkle_v1.json`: `f3e358d1e7ce7c836ce8604810675e0952201a7799b92d30858cd489906499af`
- `tests/fixtures/aim_dataset_merkle_v1_extended.json`: `6f33e31252c267ae6b3b4a920fab723a53ccbecae9742043599efd80e3b2a7fd`
- `tests/fixtures/aim_dataset_parser_v1.json`: `8ee8ac6b6bdaccd943987afb0a17cbb548a88a7dc7598563a143e7d8ab415754`

Earlier milestone hashes/statuses below are historical. The original golden corpus was never modified.

## Historical blocker report (superseded, retained as evidence)

**Status: INCOMPLETE. Producer acceptance is blocked by the approved plan's
serialization gate. No parser, descriptor models, Merkle service or bounded
worker has been implemented.** This is a reproducible pre-build finding, not
completion of any requested implementation milestone. No merge, release,
marketplace call, key operation or real seller-data access occurred.

Date: 2026-09-16. Repository: `aidotmarket/aim-data`.
Branch: `build/bq-listing-enrichment-seller-tools-s1294-c2a-canonical-tree-s1716`.
Fetched baseline and branch parent: `ee7faa457a21d751bc581b6733ac9a40bb80a801`.
Evidence commit: `f70c15ba9948d0a505bb41ab9f4cb025086619e9`.
Backend reference: `9b6f8c1d590116ffe2792696937a63821336e00c`.

## Authority and reason for returning the conflict

Read the complete Chunk 2 build-plan §B and §I row 2a, the pinned backend
`app/utils/dataset_commitment.py`, original fixture, parent §3.4, Chunk T §B
(including F1 descriptor shape), and the backend commitment/logical models.
Consulted `/Users/max/Projects/ai-market/runbooks/aim-data-seller-publish-journey.md`;
it governs the existing publish journey, not this new parser. The approved build
plan is the specific authority for this conformance gate. No lifecycle skill
was invoked to open, close or alter the controller's S1716 session.

The build plan §B “Serialization risk and release stop” says:

> If T's permitted input domain cannot be represented without disagreement,
> return the exact conflicting fixture for a coordinated contract correction
> before producer acceptance.

That condition is reproduced below. The plan stops **acceptance**; it does not
explicitly forbid implementing unrelated local parsing while the correction is
pending. This report returns the conflict before choosing the foundational
metadata representation or claiming an accepted producer. Remaining authorized
implementation work is listed explicitly below.

## Exact admitted conflict

The pinned `DatasetCommitmentContract` accepts `leaf_count = 9007199254740993`
(`2^53 + 1`), within its approved `1..2^63-1` bounds. Its native serializer and
the independent ECMAScript serializer disagree:

```text
backend:    {"leaf_count":9007199254740993}
ECMAScript: {"leaf_count":9007199254740992}
```

This is also reproduced for a complete commitment signature object, excluding
only `seller_signature`, as required by T. The fixture contains the complete
contract input, exact UTF-8 bytes as hex, and both SHA-256 digests. The pinned
Pydantic contract is actually executed and accepts the object; admission is not
inferred from a source-string search. Zero digest/signature placeholders are
synthetic structural inputs, **not** a real tree, valid signature, registered
identity or signed candidate. The mismatch occurs before signing.

RFC8785 §3.1 requires numbers representable as IEEE 754 doubles; §3.2.2.3 uses
ECMAScript number serialization. A strict losslessness check could reject this
input instead of rounding, but cannot produce the backend bytes while admitting
the complete approved integer domain. See [RFC8785](https://www.rfc-editor.org/rfc/rfc8785.html).

Logical row integers are different: the canonical row value is already an exact
string. `[["id","signed_integer","9007199254740993"]]` agrees in both runtimes.
The defect is metadata number admission, not a reason to coerce row integers.

### Supplementary-plane ordering

The generic helper also disagrees for `{"\uE000":"bmp","\U00010000":"supplementary"}`:
Python sorts U+E000 before U+10000; RFC8785 UTF-16 sorting places the U+10000
surrogate pair (`D800 DC00`) before `E000`. The independent Node implementation
sorts property names with JavaScript's UTF-16 `sort()` and serializes primitives
with `JSON.stringify`. It operates only on this no-float JSON subset and is not
claimed as a general-purpose JCS library.

This generic object case is **not** claimed as an admitted closed descriptor
object: field names are array values in the descriptor contract. Descriptor
arrays must remain NFC UTF-8 ordered (U+E000 then U+10000); the control demonstrates
that both serializers preserve that array order. Fixed ASCII parameter keys do
not independently establish a supplementary-key defect in closed descriptors.
The admitted numeric commitment above is the actual release-stop witness.

## Files and fixture integrity

| File | Purpose |
|---|---|
| `tests/fixtures/aim_dataset_merkle_v1.json` | Exact original 132-line backend fixture, copied from the pinned Git object |
| `tests/fixtures/aim_dataset_serialization_conflict_s1716.json` | Complete synthetic admitted commitment and three byte/digest comparison cases |
| `docs/reports/s1716-s1294-c2a-conformance.py` | Standalone failing gate, loading four exact pinned backend modules without application startup, database or networking |
| `docs/reports/s1716-s1294-c2a-conformance.log` | Captured actual failing execution, including per-test outcomes |
| `docs/reports/s1716-s1294-c2a-canonical-tree.md` | This report and remaining work |

Original fixture SHA-256:
`f3e358d1e7ce7c836ce8604810675e0952201a7799b92d30858cd489906499af`.
The copy was hash-checked before writing and again during the diagnostic.
Original corpus and backend code were not edited. Fixture JSON and log files
were explicitly staged because the repository ignores `*.json` and `*.log`.

## Reproduce and observed checks

From this branch, with the existing AIM Data environment and Node on PATH:

```sh
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/python docs/reports/s1716-s1294-c2a-conformance.py --backend /Users/max/Projects/ai-market/ai-market-backend
rtk proxy ruff check docs/reports/s1716-s1294-c2a-conformance.py
```

The first command must exit **1** until a coordinated correction is supplied.
Do not rewrite the fixture or turn the failures into expected-pass assertions
to make the gate green. `--write-fixture` was used only to initially record the
observed bytes; a normal run compares the committed evidence to live execution
of the same pinned objects. This diagnostic is outside normal pytest collection
and contains no skips or xfails. It does not substitute for the requested two
producer test modules or a completed full-suite run.

| Diagnostic | Actual outcome | Interpretation |
|---|---|---|
| `test_admitted_integer_metadata_must_agree` | FAIL, observed | Exact admitted metadata conflict |
| `test_complete_commitment_signature_object_must_agree` | FAIL, observed | Same conflict in the complete signing object |
| `test_generic_supplementary_object_key_order_must_agree` | FAIL, observed | Generic helper UTF-16 ordering mismatch; not a descriptor admission witness |
| `test_exact_integer_row_string_control` | OK | Diagnostic control only |
| `test_original_fixture_sha` | OK | Copy-integrity control only |
| `test_safe_integer_metadata_control` | OK | `2^53-1` and `2^53` controls only |
| `test_utf8_descriptor_array_order_control` | OK | UTF-8 array-order control only |

Observed execution: 7 diagnostics, 3 failures, 4 controls OK, zero skips, 0.291s
for the unittest portion. The four controls have not been observed failing and
are **not counted as completed acceptance tests** under the user's instruction.
The three failures are blocker evidence, not passing acceptance tests.

Ruff 0.15.7: script check passed. Attempting `python -m ruff` first failed because
ruff is absent from the existing AIM Data virtualenv; the installed standalone
ruff was then used. Runtime: Python 3.12.12, Pydantic 2.13.4, cryptography 48.0.0,
Node v25.6.0, macOS 27.0 arm64. The existing environment has DuckDB **1.5.3**, not
the required **0.9.2** pin. No DuckDB acceptance result is claimed; a dedicated
pinned environment remains necessary for the build. No dependency was changed.

## Dispatch coverage and remaining milestones

All dispatch acceptance rows remain unimplemented/unverified:

| Plan dispatch row | Coverage |
|---|---|
| BOOLEAN and aliases | NOT RUN |
| Signed integer types and aliases | NOT RUN |
| Unsigned integers / future UHUGEINT rejection | NOT RUN |
| DECIMAL / NUMERIC | NOT RUN |
| FLOAT / DOUBLE rejection | NOT RUN |
| VARCHAR and aliases | NOT RUN |
| DATE | NOT RUN |
| TIMESTAMP precisions and explicit UTC/offset | NOT RUN |
| TIMESTAMPTZ | NOT RUN |
| Binary types | NOT RUN |
| LIST | NOT RUN |
| STRUCT | NOT RUN |
| Fixed ARRAY rejection | NOT RUN |
| MAP / UNION rejection | NOT RUN |
| ENUM / UUID rejection | NOT RUN |
| Opaque JSON-cell rejection | NOT RUN |
| TIME / TIMETZ / INTERVAL rejection | NOT RUN |
| BIT / BIGNUM / VARINT / GEOMETRY / VARIANT / extensions rejection | NOT RUN |
| SQLNULL / NULL / UNKNOWN / ANY / unresolved rejection | NOT RUN |
| Unlisted types / unsupported nested children rejection | NOT RUN |

| Requested milestone | Status |
|---|---|
| Parser + descriptors + streaming DuckDB adapter | NOT BUILT |
| Merkle + proofs + consistency/checkpoint port | NOT BUILT |
| External sort + bounded worker + cleanup/cancellation/progress | NOT BUILT |
| Golden values, actual input formats, caps, error privacy and source mutation | NOT RUN |
| Leaf-domain one-byte mutation and ordinal-encoding mutation | NOT RUN; no mutation evidence claimed |
| 1M generated records time / peak incremental RSS / disk | NOT MEASURED / NOT MEASURED / NOT MEASURED |
| Plan's 10M/wide/large/low-resource acceptance | NOT RUN |
| Report | Blocker report only; final implementation report outstanding |

### Full-suite parity table

| Suite | origin/main `ee7faa45` | Branch | Branch-only failures |
|---|---|---|---|
| Complete pytest, per-test results | NOT RUN | NOT RUN | UNKNOWN |
| Frontend Vitest | NOT RUN | NOT RUN | UNKNOWN |
| Frontend build / TypeScript | NOT RUN | NOT RUN | UNKNOWN |
| Frontend lint | NOT RUN | NOT RUN | UNKNOWN |
| Repository-wide lint/type checks | NOT RUN | NOT RUN | UNKNOWN |

There is no completed full-suite per-test parity artifact and no claim of zero
branch-only failures. No tests were deleted, skipped or relabelled to mask this.

## Coordinated decision needed

Preserve exact 63-bit protocol values and decide their cross-repository signed
JSON representation, including tree size, leaf count, index, duplicate ordinal
and transparency sequence. Candidate corrections are an explicitly versioned
decimal-string metadata representation across producer/backend/viewer, or a
separately named lossless-integer canonical profile that does not claim RFC8785.
Both require contract authority and new shared vectors; neither is implemented.

Merely imposing a `2^53-1` dataset ceiling would narrow the approved domain and
conflict with the no-arbitrary-row-ceiling requirement. Merely adopting JavaScript
rounding would lose identity. A producer-only serializer fix would diverge from
the pinned backend. Keep the original corpus byte-for-byte through any correction.

After the contract decision, continue on this branch from the exact evidence
commit, implement and push every requested milestone, then complete the full
acceptance matrix. The source, worker and test milestones remain open.

## Evidence publication handling

The push secret scanner rejected token-like test traceback text in the initial
evidence attachment. Before publication, full tracebacks, inputs and captured
output were removed from committed pytest logs/XML. Every test name/outcome
and the complete summary remain. Original raw logs stay local, SHA-pinned in
`log-redactions.json`; no hook was disabled or bypassed.

# S1716 S1294 Chunk 2a — canonical parser and tree build

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
JSON object ordering is independently compared with Node UTF-16 serialization;
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

# S1717 chunk A builder report

Implementation branch: `build/bq-multi-file-datasets-s1717-a`.
Implementation commit: `528338d520768cedfaf4c868ebda10a3f428d3fd` (the report/evidence commit follows it).
Base: `97da3f096787b6010ce914803fdd30978a9d5bfd`.

**Acceptance limitation:** the requested approved, sanitized real-install AC7 database copy was not supplied. Its path was requested during the build. The migration tests use an explicitly synthetic copied SQLite install covering all named row shapes. They do not establish the real-install AC7 gate. No production flag was enabled, deployment performed, or PR opened. Gate 3 belongs to the caller on the pushed head; this report does not claim Gate 3 or complete BQ acceptance.

## Authority and preflight

Read from `/Users/max/Projects/ai-market/runbooks` with `git show origin/main:specs/<name>`: Gate 1 and Gate 2. The observed runbooks `origin/main` was `736889fe763d59a65a63b33a8e50e3452d8a7944`. Gate 2 records APPROVED 3/3 on `c0438019`.

At start, worktree HEAD and aim-data `origin/main` both equalled the requested base. Re-derived `git rev-list --count 1e21eb63..97da3f0`: **26**. Compared every chunk A source and test path, including abbreviated frontend paths expanded to their full names: **no file differs from the spec pin**. S1294 changed its own canonicalization/merkle/preview services, outside these seams. See [preflight.log](s1717-a-evidence/preflight.log).

Executed before editing:

```text
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/alembic heads
024_bq_data_verification_s1590 (head)
```

It was the single head. Revision 025 has `down_revision = "024_bq_data_verification_s1590"`. Final head:

```text
025_bq_multi_file_datasets_s1717 (head)
```

Only new revision 025 changed. Revision 026, every released revision, batch_service, publish wire code and the scanner are untouched.

## Files and behavior

- `alembic/versions/025_bq_multi_file_datasets_s1717.py`, `app/models/dataset.py`: nullable `root_path`, live `DatasetMember` rows with composite dataset/index key, unique dataset/path, role/status/sample-role/nonnegative constraints, timestamps, and missing-source representation. Schema upgrade changes no legacy row value.
- `app/config.py`: default-off flag and every §4.1 setting, using the existing AIM_DATA/VECTORAIZ alias helper and §6 defaults. R1 removes the three D-owned total/session/abandonment settings.
- `app/services/dataset_manifest.py`: seven-field D0 preimage, canonical path validation, derived counts/bytes, SHA-256; no bookkeeping/scalars in the hash.
- `app/services/directory_registration.py`: iterative descriptor-relative scandir traversal, hidden/junk/symlink exclusions, no depth/time cutoff, streamed stable reads, whole-registration refusal, NFC/case/control/UTF-8/path-size checks, member/byte bounds, stable member identity and removed-member handling. Registration completes before database writes; caller owns commit/rollback.
- `app/services/member_migration.py`: explicit flag-gated local backfill, exact legacy resolver-selected bytes, no sample convention, missing source without a fabricated digest, idempotent replay.
- `app/routers/datasets.py`: gated registration, paged/filterable members, authenticated role/sample PATCH with post-publish freeze; upload and batch call sites register one-member directories under the flag, reusing the batch record creator unchanged. Legacy branches remain intact.
- `app/routers/imports.py`, `app/services/import_service.py`: complete scan and one-directory registration under the flag; legacy scan/start/copy behavior preserved when off.
- `app/services/source_artifact_resolver.py`: original member path resolution with no Parquet fallback for directory records; legacy resolver priority unchanged. No directory-root verification amendment implemented.
- `frontend/src/pages/Datasets.tsx`, `DatasetDetail.tsx`, `frontend/src/lib/api.ts`: one folder card, file counts, registration status, paged role/status table, explicit sample/role editing and frozen/disabled states.
- All ten named backend test modules exist; existing resolver/local-service modules are extended and `tests/test_batch_upload.py` is unchanged. Both named frontend test files are covered. Golden fixture is tracked despite the repository's broad `*.json` ignore rule.

## Golden fixture: exact cross-repository construction

Path: `tests/fixtures/multi_file_datasets/manifest_golden.json` (474 bytes).

Two members, in this order; both source payloads are **zero bytes**:

| index | relative_path | size_bytes | detected_type | role | is_sample |
| --- | --- | --- | --- | --- | --- |
| 0 | data.csv | 0 | csv | data | false |
| 1 | README.md | 0 | md | documentation | false |

Each `sha256` is SHA-256 of `b""`. Define `C(x) = json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`.

1. `members` is exactly the ordered seven-field list above, with each SHA-256 added.
2. `manifest_hash = hashlib.sha256(C(members)).hexdigest()`.
3. Fixture bytes are `C({"members": members, "manifest_hash": manifest_hash}) + b"\n"`.
4. No root path, status, timestamps, scalars, random IDs or platform-dependent content is present.

Manifest preimage digest: `409ce050b2ad8846e4365224f09f6eb85b8f8a9d0274c817f6f28751841dddaf`.

Whole fixture SHA-256: `ca7c2cf2fb22d95b22cc5d9bf8fd78309ad784c2e104d76ee8e852b1ac40aba0`.

The test pins the whole-file digest and independently recomputes the recorded manifest digest. This construction is ready for C0 to copy/reconcile. The concurrently built backend copy was not inspected, so byte identity across repos is **not yet claimed**.

S1294 `dataset_canonicalization.py` imports a serializer from `dataset_merkle_service.py:94-132` that implements a restricted JCS safe-integer vocabulary and rejects generic key ordering/types. It is not the general shared `python-json-sort-compact-v1` algorithm. This chunk instead **reuses** `marketplace_action_signer.canonical_json_bytes` (`:15-22`), the existing signed-publish implementation of the exact D0 algorithm. No second serializer was introduced.

## Tests

Re-run on 2026-09-17 against candidate `35c19904d80513069a34165c1380956f29bd1ab9`, preserving the existing bridge commit. This follow-up changes this report only; no implementation or test code was changed.

### Focused backend results

The ten-module command below completed with **128 passed, 0 failed, 0 errors**, 23 warnings in 18.65 seconds (exit 0). Each module also has the same passing count in the full candidate run:

| Module | Focused passed | Full candidate passed | Failed / errors (both runs) |
| --- | ---: | ---: | ---: |
| `tests/test_directory_registration.py` | 12 | 12 | 0 / 0 |
| `tests/test_dataset_manifest.py` | 19 | 19 | 0 / 0 |
| `tests/test_dataset_members_api.py` | 9 | 9 | 0 / 0 |
| `tests/test_member_migration.py` | 2 | 2 | 0 / 0 |
| `tests/test_alembic_025_026.py` | 1 | 1 | 0 / 0 |
| `tests/test_directory_import.py` | 3 | 3 | 0 / 0 |
| `tests/test_single_file_uploads.py` | 4 | 4 | 0 / 0 |
| `tests/test_batch_upload.py` | 23 | 23 | 0 / 0 |
| `tests/test_data_verification_resolver.py` | 6 | 6 | 0 / 0 |
| `tests/test_data_verification_local_service.py` | 49 | 49 | 0 / 0 |
| **Total** | **128** | **128** | **0 / 0** |

Evidence: `/tmp/s1717-a-evidence/report-focused.log` and `report-focused.xml`. The focused command used `AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-a-evidence/report-focused-serial` and appended `--tb=short --junitxml=/tmp/s1717-a-evidence/report-focused.xml` to the listed module command.

### Full backend comparison

The base results are the requested existing `/tmp/s1717-a-evidence/baseline-portable.xml`, not a substituted baseline. The candidate was rerun with the reproduction command below, using fresh `AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-a-evidence/report-candidate-serial` and adding `--junitxml=/tmp/s1717-a-evidence/report-candidate.xml`.

| Revision / evidence | Passed | Failed | Errors | Skipped | Total | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Base `97da3f096787b6010ce914803fdd30978a9d5bfd` / `baseline-portable.xml` | 2842 | 55 | 38 | 34 | 2969 | 170.62 |
| Candidate `35c19904d80513069a34165c1380956f29bd1ab9` / `report-candidate.xml` | 2894 | 56 | 38 | 34 | 3022 | 185.23 |

Candidate exit status: **1**, with 786 warnings. Raw output: `/tmp/s1717-a-evidence/report-candidate.log`. The full suite is **not green**. Comparison uses exact JUnit `classname::name` identities and treats either failure or error as failing.

**New failing cases relative to `baseline-portable.xml` (explicit list):**

- `tests.test_sql::test_query_execution_blocked` — expected HTTP 400, received HTTP 403 from unprovisioned serial metering.

**Base failing cases that now pass:** none in this comparison. All 55 base failures and 38 base errors remain failing; the candidate adds 53 passing cases and the SQL case above changes from pass to fail.

**Correction to the previous summary:** the requested statement that `tests.test_sql::test_query_execution_blocked` is a base failure that now passes is not supported by the named baseline or this rerun. It passes in `baseline-portable.xml` and the earlier `candidate-r2.xml`, but fails in the saved `base-flag-off.xml`, `final.xml`, and this candidate rerun. The earlier candidate-r2 comparison therefore had no new failing cases, but that is not this rerun's result.

One-line explanation: this SQL case has suite-state-dependent metering behavior (403 while unprovisioned, before the expected 400 query rejection), not a demonstrated SQL fix; fresh isolated runs fail identically on both base and candidate.

The fresh isolated checks used the same Python virtualenv and `pytest -q tests/test_sql.py::test_query_execution_blocked --tb=short`, with separate new serial directories `report-base-sql-serial` and `report-candidate-sql-serial`; each returned **1 failed** (exit 1), with the same 403-versus-400 assertion. Logs: `/tmp/s1717-a-evidence/report-base-sql.log` and `report-candidate-sql.log`. SQL routing, serial metering, the SQL test and shared conftest are unchanged from the base. These results reproduce the failure on the unchanged base; no introduced implementation regression was established, so no code fix was made.

### Frontend results

All three listed frontend commands were rerun from `frontend/` with the existing dependency tree:

| Command | Result | Evidence under `/tmp/s1717-a-evidence/` |
| --- | --- | --- |
| `NODE_OPTIONS=--no-experimental-webstorage npm test` | **70 passed**, 10 test files passed, exit 0; 11.57 seconds | `report-frontend-test.log` |
| `npm run build` | **Passed**, exit 0; 14.23 seconds; Vite warns about chunks over 500 kB | `report-frontend-build.log` |
| `./node_modules/.bin/tsc --noEmit` | **Passed**, exit 0; no diagnostics | `report-frontend-types.log` |

**Acceptance limitation:** the requested approved, sanitized real-install AC7 database copy was not supplied. Its path was requested during the build. The migration tests use an explicitly synthetic copied SQLite install covering all named row shapes. They do not establish the real-install AC7 gate. No production flag was enabled, deployment performed, or PR opened. Gate 3 belongs to the caller on the pushed head; this report does not claim Gate 3 or complete BQ acceptance.

JUnit evidence SHA-256:

- `baseline-portable.xml`: `6ed712467982d7f3403174a3ae605b4fb21f6c522e7648605085ec52b0193086`.
- `report-focused.xml`: `36ef4063a2291bc3725651bdae26e63e858b59d879e2a07fc97c99da5a251c97`.
- `report-candidate.xml`: `d5fb3a247373149228e28b76410eb15ff30a04362a8d9b6f805ce0216e09612a`.

Local raw diagnostics live in `/tmp/s1717-a-evidence/`; committed evidence logs contain command context, outcomes/counts and test identities, omitting captured bodies and configuration values. The separate base worktree is `/tmp/s1717-a-baseline` at the exact requested base. Both runs use the same existing Python virtualenv and frontend dependency tree. No dependencies or application environment files were changed.

Reproduction (run from the appropriate base/candidate checkout, with a fresh temporary serial directory for each run):

```sh
rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-a-test-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q --tb=short
```

The focused command supplies these modules after `pytest -q`:

```text
tests/test_directory_registration.py tests/test_dataset_manifest.py
tests/test_dataset_members_api.py tests/test_member_migration.py
tests/test_alembic_025_026.py tests/test_directory_import.py
tests/test_single_file_uploads.py tests/test_batch_upload.py
tests/test_data_verification_resolver.py tests/test_data_verification_local_service.py
```

Frontend, from `frontend/`:

```sh
rtk proxy env NODE_OPTIONS=--no-experimental-webstorage npm test
rtk proxy npm run build
rtk proxy ./node_modules/.bin/tsc --noEmit
```

The 21,000-file fixture is generated in a temporary directory, with 20,996 CSV files eight levels deep plus LICENSE, extensionless, binary and zero-byte files. Collision tests emulate raw readdir names where APFS cannot represent both spellings. Long paths are created with directory descriptors so macOS's absolute-path limit does not prevent testing the application's 1,024-byte bound. Non-UTF-8 refusal likewise injects the POSIX surrogateescaped readdir result. Mutation testing writes same-size replacements while restoring mtime and verifies whole refusal.

## Spec interpretations and resolved ambiguities

Line references below refer to the authority files read with `git show`, not this checkout's implementation.

| Authority | Choice |
| --- | --- |
| Gate 2 §3 line 63; Gate 1 D0 line 50 | Preserve caller-supplied manifest order; hash only the exact seven fields. Reuse the signed-publish canonicalizer rather than the restricted S1294 JCS serializer, as explained above. |
| Gate 2 §3 line 63, golden fixture digest wording | The digest recorded *inside* the fixture is the member-list preimage digest. A separate constant in the test pins the whole fixture, avoiding an impossible self-referential file hash. |
| Gate 2 §4.1 line 76; Gate 1 D1/D1a lines 54/56 | Add `root_path` only; do not add a competing `authority_kind` dataset column from the older survey shorthand in Gate 2 §2. Directory mode is `file_type="directory"`. |
| Gate 1 D1a line 56; Gate 2 §7 line 172 | Backfill is an explicit transactional routine gated by the flag, not an automatic side effect of schema upgrade. Legacy file_type, filenames, processed_path, batch_id, listing_id and statuses remain unchanged; root_path/member rows bind a future republish. No ListingVersion is fabricated. |
| Gate 1 D1a line 56 | If no legacy source exists, preserve the recorded explicit path where available, otherwise the original-upload candidate; member status is missing, size is zero and sha256 is NULL. Manifest construction refuses NULL hashes. The old resolver remains authoritative for legacy delivery. |
| Gate 1 D8 line 70; Gate 2 §6 lines 137-139 | Maxima are inclusive: the next member/byte **above** the configured value refuses the entire registration. Byte accounting includes documentation/other files, so role choices cannot bypass an upload bound. |
| Gate 1 D8 line 70; Gate 2 §6 line 139 | `DIRECTORY_READ_MAX_ATTEMPTS=3` means at most three complete read attempts, requiring two consecutive matching stat/digest results. Configuration requires at least two. Track device, inode, size, nanosecond mtime and ctime; never hash mtime into the manifest. |
| Gate 1 D8 line 70; Gate 2 §4.1 line 76 | Iterative scandir recursion avoids Python recursion limits. All directory descriptors are closed. Registration uses `asyncio.to_thread` without the existing run_sync helper's 30-second timeout. |
| Gate 1 D0/D8 lines 50/70 | Store NFC paths; resolve decomposed filesystem spellings by NFC comparison without renaming seller files. Reject backslashes, empty/dot/dot-dot components, leading slash, Unicode Cc control characters and over-1,024-byte canonical paths. |
| Gate 1 D1 line 54; Gate 2 §4.1 lines 76-77 | Documentation-name convention takes precedence over unsupported extension (so extensionless LICENSE is documentation). Unsupported entries keep status unsupported/reason unsupported_type. Nothing selects samples automatically. New standalone uploads are the explicit one-data-member case. |
| Gate 1 D8 line 70; Gate 2 §4.1 line 76 | Initial indices follow sorted canonical paths; re-registration preserves indices and seller choices, appends new indices and marks absent paths removed. Path identity never depends on a changing hash. The API's path-only registration reuses a directory record with the same validated root. |
| Gate 2 §4.1 lines 76-77 | Page numbering starts at 1, page size is 100, role/status filters are exact validated literals. Existing listing_id is the available post-publish freeze signal. A role change away from data clears sample; an explicit simultaneous non-data/sample=true PATCH is refused. |
| Gate 2 §4.1 line 76, D-bytes | Provide explicit member-path resolution; the existing single-artifact resolver accepts a one-member directory and refuses ambiguous multi-member selection. It never silently selects the first member or falls back to Parquet. Chunk E still owns set-resolution, retained manifests and all directory-root preimages. |
| Gate 2 §4.1 line 76, upload/import branches | An uploaded file moves unchanged into its own dataset-ID storage directory; the directory-mode names follow that root's basename. `/imports/start` retains its legacy request schema; under the flag it registers the entire root, ignoring the legacy file-selection list. Off-path code and batch service remain unchanged. |
| Gate 2 §4.1/§4.2 lines 76/83 | Registration leaves datasets uploaded and the UI labels them Registered. It does not dispatch the legacy single-file profiler on a directory. B owns directory profiling and its terminal processing states. |
| Gate 2 §4.1 line 76; §6 | R1 correction: A owns exactly the §4.1-enumerated settings, ending at transfer_max_member_bytes. Chunk D adds transfer_max_total_bytes, transfer_session_ttl_s and abandoned_order_ttl_s with TTL-ordering validation; §6 assigns their enforcement to C/D and D. |
| Gate 2 §4.1 line 77; user dispatch | `test_alembic_025_026.py` covers 025 only. The real-install AC7 proof remains pending rather than being silently replaced by the synthetic fixture. |

The S1590 directory-root amendment, set profiling, publish wire, sample serving and scanner changes remain for their assigned chunks.


## R1 fold

Review base: `b7ddcc03b3b436e018d26014ef3e6d1548e17823`. Council verdicts supplied by the caller: GLM **APPROVE_WITH_MANDATES** (1 MEDIUM, 1 LOW), DeepSeek **APPROVE_WITH_MANDATES** (2 LOW, 3 NIT), CC **APPROVE_WITH_NITS** (1 LOW, 1 NIT). Folded on the existing `build/bq-multi-file-datasets-s1717-a` branch; the bridge WIP commit `35c1990` remains in its ancestry. No branch creation, rebase, history rewrite or PR.

| Finding | Disposition |
| --- | --- |
| DeepSeek F1 LOW | Adopted in `3180baa`: directory deletion removes dataset_members before the dataset row; rmtree is allowed only for a resolved root strictly beneath upload_directory. External registered roots are preserved. Upload DELETE and external registration DELETE regressions both return 200 and leave zero members; only the upload root disappears. |
| DeepSeek F2 LOW | Adopted in `537b697`: one helper maps file_type to itself only within PROCESSABLE_TYPES, otherwise unsupported. Both migration and upload registration use it. A legacy out-of-domain type migrates without changing the legacy row and is accepted by build_manifest. |
| GLM 2 LOW / DeepSeek F4 / CC LOW-1 | Adopted in `06888e4`: removed only transfer_max_total_bytes, transfer_session_ttl_s and abandoned_order_ttl_s. Kept all §4.1 fields; D owns the deferred settings and TTL-ordering validation. |
| CC NIT-1 | Adopted in `537b697`: a present but unreadable/unstable source still aborts the entire backfill; its error now names the dataset id and path. Parameterized tests verify both error reasons, caller rollback of all member/root changes and successful retry. See AC7 below. |
| GLM 1 MEDIUM | Not adopted as a code change: AC7 is real-install verification for the caller before Gate 4. The synthetic-copy limitation remains. The verbatim GLM criterion list is an outstanding documentation input, as noted below. |
| DeepSeek F3 | Not adopted: the one-member upload card's dataset UUID is the directory basename, literally conforming to §4.1 original_filename = directory name. Display-copy polish belongs to F. |
| DeepSeek F5 | Not adopted: flag-on /imports/start registers the whole root and ignores legacy files selection, as documented in the ambiguity table. Chunk F runbook/handoff note: present this whole-directory behavior in the import UI/copy; do not imply selected-file filtering. |

### AC7: real-install Gate 4 verification and retry

The approved sanitized real-install copy remains a caller-run prerequisite before Gate 4; synthetic tests do not satisfy it. Run backfill within a caller-owned transaction. If a present source is unreadable or cannot stabilize, expect an error identifying its dataset id and path and roll back the whole transaction. Restore readability or stop the source writer, then retry the complete backfill; do not commit partial work or skip the offending row. Missing-source rows retain their existing explicit missing representation.


### Gate 4 checklist: verbatim source outstanding

GLM's exact verification criterion list was not included in the caller's fold instructions and was not found in the available local verdict artifacts. Its location/text was requested during this run. **The verbatim checklist is not yet incorporated**; the caller must supply the original GLM 1 MEDIUM verification criteria before this documentation item can be closed. No reconstructed wording is presented as a GLM quotation. The real-install AC7 limitation and retry procedure above remain binding.

### R1 validation

Tested code: `06888e4` (report-only commit follows). Reused the report's ten-module command, Python virtualenv and frontend dependency tree, with fresh serial directories `/tmp/s1717-a-evidence/r1-focused-serial` and `r1-full-serial`. Logs and JUnit XML are under `/tmp/s1717-a-evidence/r1-*`.

| Check | Result |
| --- | --- |
| Focused ten-module backend command | **133 passed**, 0 failed/errors, 27 warnings, 12.54 seconds; exit 0 |
| Frontend npm test with NODE_OPTIONS=--no-experimental-webstorage | **70 passed**, 10 files passed; 2.20 seconds; exit 0 |
| Frontend npm run build | Passed, 3.52 seconds; exit 0; existing large-chunk and Browserslist-age warnings |
| Frontend tsc --noEmit | Passed without diagnostics; exit 0 |
| Full backend suite, run once | **2900 passed, 55 failed, 38 errors, 34 skipped**; 3027 total; 762 warnings, 162.33 seconds; exit 1 |
| Named baseline-portable.xml | **2842 passed, 55 failed, 38 errors, 34 skipped**; 2969 total |
| New failing cases against named baseline | **None** |
| Baseline failing cases no longer failing | **None** |

The full suite remains non-green: all 93 baseline failing/error case identities persist. Comparison uses exact JUnit `classname::name` identities, treating either failure or error as failing. This run adds 58 passing cases over baseline, including the five R1 regression cases. Unlike the prior report-candidate run, `tests.test_sql::test_query_execution_blocked` passes here, matching the named baseline; the prior report's suite-state limitation remains documented above. No unrelated fix was made.

Focused per-module counts: directory_registration 12; dataset_manifest 19; dataset_members_api 10; member_migration 5; alembic_025_026 1; directory_import 3; single_file_uploads 5; batch_upload 23; data_verification_resolver 6; data_verification_local_service 49. All 133 also pass in the full run.

Evidence digests (SHA-256):

- `baseline-portable.xml`: `6ed712467982d7f3403174a3ae605b4fb21f6c522e7648605085ec52b0193086`.
- `r1-focused.xml`: `e6b5b99730558c95c4216e1483c59e8ebfcc06cacc9712c99535cc8167f9e64b`.
- `r1-full.xml`: `8a39a71e2dbd20c69cfc8705d984fdd4123e9fe3abf53f873f6f19c9fe6e5b14`.

Machine-readable comparison: `/tmp/s1717-a-evidence/r1-comparison.json`.

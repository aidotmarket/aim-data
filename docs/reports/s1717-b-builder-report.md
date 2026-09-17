# S1717 chunk B builder report

Branch: `build/bq-multi-file-datasets-s1717-b`.
Base: `adb97f0497223525d4247fa4bb200076fa5fa969` (chunk A merged).
Implementation head: `d7fd820162b0bf1b712f73f3074bfab2e4c92fdd`; the report commit follows it.

Chunk B implements bounded local directory profiling and the carried legacy-deletion fix. The feature flag remains off by default. No PR, deployment, publish-wire change, scanner change, secret change or Alembic revision was made. Council review is for the caller on the final pushed head; this report does not claim Gate 3 or enabled-release acceptance.

## Authority and scope

Read the Gate 1 and Gate 2 specifications from `/Users/max/Projects/ai-market/runbooks` using `git show origin/main:specs/BQ-MULTI-FILE-DATASETS-S1717-GATE1.md` and the corresponding `GATE2.md`. Runbooks `origin/main` was `736889fe763d59a65a63b33a8e50e3452d8a7944`. Implementation follows Gate 2 §4.2, §6 and §7, Gate 1 D2 and AC1/AC2. Read chunk A's model, registration service, directory router branches and builder report before editing. The existing `profile_max_members=64`, `profile_max_member_bytes=268435456`, `profile_max_total_bytes=536870912` and `profile_timeout_s=900` fields are consumed without redefining them.

The checkout began clean at the exact requested base. A separate detached base worktree was created at `/tmp/s1717-b-base`; peer worktrees were not changed. No subagents were used. Shell commands used RTK.

## Files and behavior

- `app/services/directory_processing.py`: deterministic ascending `(sha256, relative_path)` selection of data members; inclusive limits; isolated, killable extraction and PII workers; one provider request for a completed set run; bounded quoted documentation context; explicit per-member outcomes; persisted sampled counts, bytes and distinct schema count. A private input copy and disposable processing output never replace original member bytes. Concurrent entry points share a task, and completed unchanged sets reuse persisted results.
- `app/services/processing_service.py`: reuses the existing per-record extractors through `profile_member`, without creating per-member dataset rows or running per-member PII/provider calls. Directory `process_file` dispatch is flag-gated. Every legacy deletion now removes member rows before the dataset row, closing the D1a backfill orphan. The directory root-removal rule is exactly as shipped: recursive removal only for a resolved root strictly beneath `upload_directory`; external directory roots remain intact.
- `app/services/pipeline_service.py`: both pipeline entry points and status reads dispatch directories to set processing. Legacy branches are retained.
- `app/routers/datasets.py`: directory branches at `/pipeline`, `/process-full`, `/pipeline-status`, `/statistics` and `/profile`; registered directories can start processing without an existing Parquet output. Per-member profiles remain separate rather than pretending different schemas are one table.
- `app/services/listing_metadata_service.py`: authors set metadata with explicit coverage, schema count and a deterministic fallback. Documentation is quoted untrusted JSON context to the text-only provider path, never executed. Metadata reads reuse the saved listing result. No successful profile is required for listing preparation.
- `frontend/src/pages/DatasetDetail.tsx`: member profiling state/reason, sampled count/bytes, terminal ready/skipped copy. Profiling states are read from metadata and displayed beside the existing registration member table.
- `tests/test_directory_processing.py`: 22 new tests, including the carried migrated-record DELETE regression, real worker termination and a real spawned CSV extractor.
- `frontend/src/pages/DatasetDetail.test.tsx`: five added assertions covering all four failure reasons, terminal rendering without a spinner, coverage and not-profiled copy.

No differences from base in `app/config.py`, `app/models/dataset.py`, `alembic/`, registration, existing backend test files (including `tests/test_batch_upload.py`), scanner or marketplace publish-wire files. Existing frontend assertions are unchanged; new assertions are appended.

## Tests

Commands use `/Users/max/Projects/ai-market/aim-data/.venv/bin/python`, the same environment for base and candidate, and a fresh `AIM_DATA_SERIAL_DATA_DIR` per run. Raw local logs and JUnit files are under `/tmp/s1717-b-evidence/`; they are not committed because full-suite captured output can include unrelated configuration/body diagnostics. Counts and named failures are recorded here.

Final focused command (exit 0):

```sh
rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-b-evidence/final-focused4-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q tests/test_directory_processing.py tests/test_directory_registration.py tests/test_dataset_members_api.py tests/test_single_file_uploads.py tests/test_member_migration.py tests/test_batch_upload.py tests/test_pipeline.py --tb=short --junitxml=/tmp/s1717-b-evidence/final-focused4.xml
```

| Module | Passed |
| --- | ---: |
| directory_processing | 22 |
| directory_registration | 12 |
| dataset_members_api | 10 |
| single_file_uploads | 5 |
| member_migration | 5 |
| batch_upload (unchanged) | 23 |
| pipeline (unchanged) | 15 |
| **Total** | **92** |

The final focused run had 0 failures/errors, 47 warnings, 15.18 seconds. It includes the existing batch/pipeline warning from a background test thread; no warning is relabeled as a passing assertion. A separate ordered run of `test_config_alias_choices.py` followed by these seven modules had **92 passed, 4 failed**; all four failures are unchanged baseline config-alias cases. This run reproduces the config-reload ordering that exposed the initial directory metadata test failure and proves the new directory tests pass after the fix.

Frontend commands from `frontend/`, using the existing dependency tree through a local ignored symlink:

| Command | Result |
| --- | --- |
| `rtk proxy env NODE_OPTIONS=--no-experimental-webstorage npm test` | **75 passed**, 10 files; 2.17 seconds; exit 0 |
| `rtk proxy ./node_modules/.bin/tsc --noEmit` | Passed, no diagnostics; exit 0 |
| `rtk proxy npm run build` | Passed, 3.20 seconds; existing chunk-size/Browserslist warnings; exit 0 |

### Full suite: base and first candidate

Each used `python -m pytest -q --tb=short --junitxml=<file>`, without changes to the flag-off suite. The base run was executed once. An additional final candidate run was required after the first candidate exposed a settings-import inconsistency; its results are recorded below.

| Revision / JUnit | Passed | Failed | Errors | Skipped | Total | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Base `adb97f0` / `base.xml` | 2987 | 55 | 38 | 34 | 3114 | 167.12 |
| First candidate / `candidate.xml` | 3002 | 57 | 38 | 34 | 3131 | 159.91 |

The first candidate run contained the original 17 new backend tests. Five additional tests (cache/concurrency, changed-member cache invalidation, provider timeout, PII failure, supported zero-row parsing) were added afterward and are included in the final focused run.

**Every newly failing identity in the first full comparison:**

1. `tests.test_directory_processing::test_all_over_cap_still_listing_allowed`: fixed in `d7fd820`. The existing config-alias tests reload `app.config`; a function-local settings import in the new listing branch saw a different instance from the processing modules. Using the same module-level settings import as the other services fixes the inconsistency. Both isolated and config-before-directory focused runs now pass this case.
2. `tests.test_sql::test_query_execution_blocked`: expected 400, received 403 from unprovisioned serial metering. Fresh isolated tests on **both unchanged base and candidate** return **1 failed** with the identical 403-versus-400 assertion (`base-sql.log`, `candidate-sql.log`, 4.05/4.00 seconds). No SQL/metering change was made. This is the suite-state-sensitive case already identified in chunk A's report, not a demonstrated introduced SQL regression.

All 93 original baseline failure/error identities remained failing in the first candidate. No baseline failure was fixed by unrelated changes.

### Final full-suite comparison on d7fd820

Final run (`final-full.xml`, exit 1): **3009 passed, 55 failed, 38 errors, 34 skipped**, **3136 total**, 788 warnings, **172.07 seconds**. All **22 new directory tests pass** in this full run. Exact JUnit `classname::name` comparison, treating errors and failures alike:

- **New failing cases relative to base: none.**
- Baseline failing/error cases no longer failing: none; the same 93 identities remain.
- Increase over base: **22 passing cases**.
- The initial all-over-cap failure passes after the settings-import fix. The SQL case passes in this final full run, consistent with its already documented suite-state variability; isolated base and candidate failures remain disclosed above.

The full suite remains non-green; this is baseline parity, not a claim that unrelated failures have been resolved.

Reproduction from the final checkout (base command is identical except the worktree, serial directory and JUnit filename):

```sh
rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-b-evidence/final-full-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/s1717-b-evidence/final-full.xml
```

Evidence SHA-256:

- `base.xml`: `013e61539f7f6ecc9ff55f0f3c6e08cf1c93958b4e49576528181906179ac01e`.
- `candidate.xml`: `a9a70be50b3068fe0636db3b5615126f1ed05a6b3621d08d4ff59e25d1a38ba5`.
- `final-full.xml`: `8a5cf03f979daef26591a53be46e29a76ec11bbad5a051f59ffbcf485e575e13`.
- `final-focused4.xml`: `22b71d4632e6953c998dfe6f58109c78c4ba9569bf580ec2e22ecb71323d3235`.
- `frontend.log`: `77eb4886939a57515569209748145bab700db4ba0e3ca366eed3381a8c8fdb24`.
- `types.log`: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- `build.log`: `ddf52442f3c8c88adf1b1d816a08bfedad41c3459bb7c5fcabd0cd7fb5c6f682`.

### Boundary evidence and limits

- Default 64 versus 65 data members: N and M asserted; 65th member names `PROFILE_MAX_MEMBERS=64`.
- Exact 512 MiB: two 256 MiB members admitted, next byte refused; count, admitted bytes and aggregate reason asserted. These size-policy fixtures stub extraction with declared sizes, rather than allocating 512 MiB of test payloads.
- A single 256 MiB + 1 byte member is skipped; a subsequent small member still profiles. All members above cap produce `profiling_skipped`, a reason and `not profiled`, with dataset lifecycle `preview_ready` and listing metadata generation allowed.
- The scale fixture creates **21,000 actual DatasetMember database rows**; provider `stream` invocation is counted at the provider stub and equals **one**, with 64 member extractions and one PII pass. Registration's physical 21,000-file traversal remains chunk A's separate test.
- A real spawned process sleeping for 60 seconds is killed at a test-tuned 1-second profile deadline; all unfinished eligible members become `timeout`, the dataset is terminal, total test elapsed is below 2 seconds, and no child remains. A hanging metadata author also returns a terminal deterministic fallback within the tuned bound.
- Real spawned CSV extraction returns one row and leaves the original bytes unchanged. Injected extraction failure records a fixed sanitized reason and reaches `preview_ready`. Unsupported type never enters the extractor. A supported successful zero-row parse is terminal, not a spinner.
- Migrated legacy DELETE backfills through the shipped D1a routine, preserves legacy `file_type`, invokes HTTP DELETE and verifies both record and member are gone while the parent directory survives. This is SQLite execution; no live Postgres installation is claimed.

## Spec interpretations and ambiguities

Line references are to the authority files read from runbooks `origin/main`.

| Authority | Choice and consequence |
| --- | --- |
| Gate 2 §4.1 line 76 versus §4.2 line 82; Gate 1 D2 line 58 | Chunk A's member CHECK admits only registration states `current/removed/missing/unsupported`. Profiling status/reason therefore lives in `metadata_json.directory_profile.members[index]`, and the member table overlays it. Registration status and manifest bytes are unchanged; no migration is needed. |
| Gate 1 D2 line 58; Gate 2 §4.2 lines 82–83 | `profiling_skipped` is the terminal **profile** state. Dataset lifecycle is `preview_ready`, preserving the existing ready-to-list contract without adding an enum/schema value. |
| Gate 1 D2 line 58; Gate 2 §6 lines 140–143 | N is successfully profiled data members; M is all non-removed data members, including unsupported/missing/oversized members. Documentation/other members do not inflate M. Failed extraction attempts consume their admitted member/byte budget; the loop may consider later smaller members that fit. Bounds are inclusive and refusals name value and field. |
| Gate 2 §4.2 line 83, count-limit reason not pinned | Count-limit skips use `too_large` plus `PROFILE_MAX_MEMBERS=<value>`, staying within D2's four reason codes rather than inventing a fifth state. Missing sources use `parse_failed` with a fixed reason. |
| Gate 1 D2 line 58; Gate 2 §6 lines 141–143 | Byte accounting is admitted original source bytes. Extractors can reread their disposable snapshot/Parquet output internally. Documentation shares the remaining aggregate input budget, is deterministically read after data, and is capped at 256 KiB of context across the set (the existing listing README context bound). It is omitted when no byte/time budget remains. |
| Gate 1 D2 line 58 | Documentation is decoded as bounded inert UTF-8 text (replacement for undecodable bytes), quoted in JSON; no document executable, tool or shell is invoked from its contents. This does not claim that arbitrary binary documentation is semantically extracted. |
| Gate 1 D2 line 58; Gate 2 §4.2 line 82 | One PII pass scans deterministic bounded previews (five rows/member, up to 10,000 characters/member), using the existing text scanner. Its scope is explicitly recorded as preview-only. Missing/timed-out PII is unknown, never a clean privacy score. This is not the chunk E verification scan. |
| Gate 1 D2 line 58; Gate 2 §6 line 143 | Deadline covers member extraction, documentation, PII and metadata work. A provider attempt is made at most once per registered set; if preceding work exhausts the deadline there is no late provider request. Worker cleanup and the final database commit can add scheduling/commit overhead; the timeout test allows one second of shutdown overhead. |
| Gate 1 D2 line 58 | Concurrent processing entry points share one job under the application's single-worker contract. An unchanged set reuses terminal results. A local cache key includes registered member identity/roles/status, root and policy values; it is not a published manifest commitment. Re-registration or role changes are processed on the next pipeline invocation. |
| Gate 1 D2 line 58; customer behavior 2 line 79 | Distinct schemas are exact ordered column definitions from successful member profiles. Set listing descriptions retain the explicit coverage and schema count even if the provider drafts other prose. No universal merged schema or extrapolated full-set row count is claimed. |
| Gate 1 trigger line 6; Gate 2 §4.2 line 83 | The authority does not identify the exact extension behind the screenshot's zero-row spinner. Clarification was requested; absent an answer, tests cover `unsupported` and a supported successful zero-row parse. They do not claim reproduction of the unidentified customer file. |
| Caller CC R2 LOW-1 fold; Gate 1 D1a line 56 | Delete member rows on the legacy path too, regardless of current flag state. This narrowly authorized bug fix is the intentional exception to “legacy unchanged”; the original directory root-removal condition is untouched. |

No provider-backed semantic-quality test, customer-file test, live Postgres test, production enablement, publish-wire acceptance or scanner acceptance is claimed. Those are not substituted with the passing stub tests.

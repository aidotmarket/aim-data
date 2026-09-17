# S1717 chunk B builder report

Branch: `build/bq-multi-file-datasets-s1717-b`.
Base: `adb97f0497223525d4247fa4bb200076fa5fa969` (chunk A merged).
Implementation head (R2): `f56c9ce7fde6a75b48687d990b733f297101f572`; the documentation-only report commit follows it. R2 review input: `9624a037d8345893cff5e9738a19cda38c6680e9`.

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


## R1 fold

Review base: `6706a4147502c880d982f42c8790badf9144edf0`. Caller-supplied Council verdicts: DeepSeek **REQUEST_CHANGES** (1 MEDIUM, 2 LOW, 4 NIT), GLM **APPROVE_WITH_NITS** (1 NIT), CC **APPROVE_WITH_NITS** (1 LOW, 1 NIT). Implementation commit: `0959c9a`; this report commit follows. Worked in the clean existing branch worktree `/private/var/tmp/koskadeux/minimal-bridge-worktrees/57806225a01e-a5e300`, because the supplied bridge checkout was detached at the same review head. No new branch, rebase, history rewrite, PR or deployment. Earlier sections describe the pre-R1 candidate; this section supersedes their affected behavior and counts.

### Findings and dispositions

| Finding | Disposition |
| --- | --- |
| DeepSeek F1 MEDIUM | Adopted. Empty member previews do not supply a synthetic `[]` text block to PII. When zero members were profiled or no nonempty preview text was sampled, set only `pii.privacy_score` to `None`, retaining the PII scope and scanner counts; listing metadata inherits `None`. The all-over-cap test checks the persisted unknown score and retains “not profiled” description copy. Four empty-preview cases cover empty rows, empty text, whitespace and null text; a sampled member retains numeric score 10. |
| DeepSeek F3 LOW = CC LOW-1 | Adopted. A registered directory with profile `not_started` returns overall pipeline status `pending` and “Profiling has not started.” HTTP regression verifies it never reports failure. |
| DeepSeek F5 NIT | Adopted. `_finish` rewrites any remaining `pending` member to `parse_failed` with the sanitized reason “Profiling interrupted before extraction completed.” Injected `CancelledError` verifies the terminal result is persisted with no pending member entries. |
| DeepSeek F7 NIT | Adopted. The spawn arguments explicitly carry the parent's actual `multi_file_datasets_enabled` value; the child no longer forces `True`. Real documentation workers exercise both values; the real CSV extraction test still exercises enabled member extraction. |
| DeepSeek F4 NIT | Adopted. New config field `profile_docs_context_bytes`, default **262144**, environment alias **AIM_DATA_PROFILE_DOCS_CONTEXT_BYTES**. This is a value **outside Gate 2 §6 for the caller to record**. Documentation has its own read-attempt cap of `PROFILE_MAX_MEMBERS` (default 64), separate from the data-member allowance; failed reads consume slots. Documentation still shares the aggregate byte/deadline budget. The new byte value participates in cache identity. Tests cover the alias, byte boundary, cache invalidation and read-attempt cap with failed reads. |
| CC NIT-1 | Adopted. The count-limit skip keeps member state `too_large` but uses the exact reason `member limit 64 reached` at the default cap. No `skipped_limit` state, §6/D2 vocabulary addition or frontend-label change. |
| DeepSeek F2 LOW | Not adopted, as instructed. Pipeline routes remain the intended profiling trigger. On a listing-metadata cache miss, `POST /datasets/{id}/listing-metadata` still awaits set profiling to prepare the listing; subsequent metadata reads reuse the persisted result. This preserves the current deliberate fallback behavior, including the synchronous request latency. No scheduling redesign was made. |
| DeepSeek F6 NIT | Not adopted as a code change. Packet SHA is caller-owned and the caller reports it corrected. This fold does not generate or validate a replacement Council packet. |
| GLM NIT-1 | Not adopted. The BQ-origin trigger screenshot is the only evidence for the production “0 rows stuck” case and does not identify its file type. The spec-requested `unsupported_type` path remains covered, together with a supported zero-row parse; neither is claimed as reproduction of an unidentified production file. |
| CC coverage discrepancy | No production change. `test_real_hung_worker_terminated` **passes in this environment**, including the focused run (1.028 seconds). The reviewer-sandbox failure is not reproduced here. |

### R1 validation

Used the same Python environment and frontend dependencies as the original report. Evidence is in `/tmp/s1717-b-evidence/r1-*`, using fresh focused/full serial directories. The focused seven-module command was:

```sh
rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-b-evidence/r1-focused-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q tests/test_directory_processing.py tests/test_directory_registration.py tests/test_dataset_members_api.py tests/test_single_file_uploads.py tests/test_member_migration.py tests/test_batch_upload.py tests/test_pipeline.py --tb=short --junitxml=/tmp/s1717-b-evidence/r1-focused.xml
```

Full suite uses the same environment with `AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-b-evidence/r1-full-serial` and `python -m pytest -q --tb=short --junitxml=/tmp/s1717-b-evidence/r1-full.xml`.

| Check | Result |
| --- | --- |
| Focused seven-module backend command | **104 passed**, 0 failures/errors, 57 warnings, 14.35 seconds |
| Frontend `NODE_OPTIONS=--no-experimental-webstorage npm test` | **75 passed**, 10 files, 1.90 seconds; exit 0 |
| Frontend `./node_modules/.bin/tsc --noEmit` | Passed without diagnostics; exit 0 |
| Frontend `npm run build` | Passed, 3.31 seconds; exit 0; existing chunk-size/Browserslist warnings |

Focused module counts: directory_processing **34**, directory_registration **12**, dataset_members_api **10**, single_file_uploads **5**, member_migration **5**, batch_upload **23**, pipeline **15**. The 12 additional passing directory cases cover the R1 fold. Existing background-thread and deprecation warnings remain visible in the logs.

| Full-suite comparison | Passed | Failed | Errors | Skipped | Total | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Named `/tmp/s1717-a-evidence/baseline-portable.xml` | 2842 | 55 | 38 | 34 | 2969 | 170.62 |
| R1 candidate `0959c9a`, `r1-full.xml` | 3021 | 55 | 38 | 34 | 3148 | 165.46 |

**New failing cases: none.** Exact JUnit `classname::name` comparison treats both failure and error as failing. All 93 baseline failure/error identities remain; none disappears. The full suite is therefore still non-green (exit 1), with **798 warnings**, but adds **179 passing cases** over the named baseline. All **34 directory-processing cases**, including the real hung-worker test, also pass in this full run. Machine-readable comparison: `/tmp/s1717-b-evidence/r1-comparison.json`.

Evidence SHA-256 digests:

- `baseline-portable.xml`: `6ed712467982d7f3403174a3ae605b4fb21f6c522e7648605085ec52b0193086`.
- `r1-focused.xml`: `6f3a91d1418a0169df27789c36214a9f7d8008d0d5ff6ef29adcac60c3e27745`.
- `r1-full.xml`: `e9078fa5f5c24ae1317b4300757ab1cefbaef8d723e89f7f2f159f7ab5916411`.
- `r1-comparison.json`: `849c5d5ead4c045d24fb35ea1ac037f07097a3c1fe1c1a2588b2d654f9b4f6c3`.


## R2 fold

Review input: `9624a037d8345893cff5e9738a19cda38c6680e9`. Caller-supplied Council: DeepSeek **REQUEST_CHANGES** (1 MEDIUM, 3 LOW, 3 NIT), GLM **APPROVE_WITH_NITS** (1 LOW, 1 NIT), CC **APPROVE_WITH_NITS**. Backend fold: `d443ba2`; frontend fold: `f56c9ce`. This section supersedes affected R1 behavior and counts. The existing clean branch worktree `/private/var/tmp/koskadeux/minimal-bridge-worktrees/57806225a01e-a5e300` was used; the supplied bridge checkout was detached at the same review input. Read the current Gate 2 specification and gate-procedure runbook in the runbooks repository. No new branch, rebase, history rewrite, PR or deployment.

### Findings and dispositions

| Finding | Disposition |
| --- | --- |
| DeepSeek F-1 MEDIUM | Adopted. `_save_record` never stats a directory to overwrite its registered `file_size_bytes`; the insert conversion also preserves a directory's authoritative zero size. HTTP regression registers a real two-file directory under the upload directory, profiles it, posts listing-metadata and checks `file_size_bytes == sum(member.size_bytes) == 10` before and after. Extraction/provider calls are stubbed; the registration, HTTP handler and record save are real. |
| DeepSeek F-2 LOW | Adopted. Persist UTC `started_at` at run start. Read-time projection converts `running` older than `PROFILE_TIMEOUT_S + 5` seconds to `timeout`, reason **profiling run did not complete (stale)**. Older pre-fold records lacking `started_at` use their persisted `updated_at`. `/pipeline-status`, `/status` and dataset metadata consumed by the card use this projection. Unfinished/missing data-member outcomes become `timeout`; completed outcomes survive. No read-time DB mutation or new worker is required. Tests exercise persisted running records at 901 and 906 seconds with a 900-second timeout, both endpoints, card metadata and rendered terminal copy. |
| DeepSeek F-3 LOW | Adopted using the shared-budget option. Data and documentation consume the same `profile_max_members` attempt allowance, default **64**; failed reads consume slots. Data remains first. Documentation retains `profile_docs_context_bytes`, default **262144**, plus the shared aggregate-byte/deadline bounds. Tests cover failed documentation attempts and no documentation read after data exhausts the shared slots. No separate `profile_max_doc_members` setting is introduced. |
| DeepSeek F-6 NIT | Adopted. Count-limit state remains D2 `too_large`; exact default reason is **too_large: PROFILE_MAX_MEMBERS=64**. The 64/65 boundary test asserts that string. This replaces R1's `member limit 64 reached` wording. |
| DeepSeek F-7 NIT | Adopted. Absent or `not_started` profile renders **Not profiled yet**, never Ready to list. Both fixtures have rendered-copy tests. |
| GLM NIT | Adopted. `detected_type` participates in each member's local cache identity. A type-only change invalidates a completed profile and takes the unsupported-type path in the regression test. |
| DeepSeek F-4 LOW | Not adopted, as instructed. Pipeline routes remain the trigger; listing-metadata cache misses may synchronously await profiling up to `PROFILE_TIMEOUT_S`. Caller's recorded acceptance is retained; no scheduling change. |
| DeepSeek F-5 NIT | Not adopted as a code change. Packet metadata remains caller-owned. No replacement Council packet is generated or claimed validated. |
| GLM LOW | Report identity refreshed to the current implementation head above, with the following documentation-only commit explicitly distinguished. No runtime change for this finding. |

### Policy values for caller's §6 record

| Setting | Default | Effective policy |
| --- | ---: | --- |
| `profile_max_members` / `PROFILE_MAX_MEMBERS` | **64** | One shared read-attempt budget across data and documentation; not 64 of each. |
| `profile_docs_context_bytes` / `AIM_DATA_PROFILE_DOCS_CONTEXT_BYTES` | **262144 bytes (256 KiB)** | Total documentation context, additionally limited by remaining aggregate bytes and time. |

Stale-run read grace is the named internal constant `PROFILE_STALE_GRACE_S = 5` seconds, in addition to the existing `profile_timeout_s = 900`; it is not another documentation budget.

### R2 validation

Same Python environment and frontend dependency tree as R1. Commands retain the seven focused modules from above, `python -m pytest -q --tb=short --junitxml=<path>` for the full suite, and all three frontend commands. Fresh serial directories: `/tmp/s1717-b-evidence/r2-focused-serial` and `r2-full-serial`. Evidence prefix: `/tmp/s1717-b-evidence/r2-`.

| Check | Result |
| --- | --- |
| Focused seven backend modules | **109 passed**, 0 failures/errors, 64 warnings, 14.22 seconds |
| Frontend `NODE_OPTIONS=--no-experimental-webstorage npm test` | **78 passed**, 10 files, 1.75 seconds |
| Frontend `tsc --noEmit` | Passed, no diagnostics |
| Frontend `npm run build` | Passed, 3.00 seconds; existing chunk-size/Browserslist warnings |

Focused counts: directory_processing **39**, directory_registration **12**, dataset_members_api **10**, single_file_uploads **5**, member_migration **5**, batch_upload **23**, pipeline **15**. R2 adds five backend cases and three frontend cases. Existing thread/deprecation warnings remain recorded in the logs.

The first R2 full run (`r2-full.xml`) returned **3025 passed, 56 failed, 38 errors, 34 skipped**, 3153 total, 804 warnings, 159.39 seconds. Exact comparison with `base.xml` has one additional failing identity: `tests.test_sql::test_query_execution_blocked` (403 versus expected 400). This is the same suite-state-sensitive SQL case disclosed in the original report, where isolated unchanged base and candidate both fail identically. All 39 directory cases pass, and all 93 base failing/error identities remain. No SQL code was changed. A fresh-serial full rerun follows to verify parity; the first result is retained rather than omitted.


The fresh-serial candidate rerun and an unchanged-base rerun used isolated temporary databases and serial directories. No code changed between the two R2 candidate runs. Fresh base remains `adb97f0497223525d4247fa4bb200076fa5fa969` in `/private/tmp/s1717-b-base`.

| Full-suite run | Passed | Failed | Errors | Skipped | Total | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Saved original base / `base.xml` | 2987 | 55 | 38 | 34 | 3114 | 167.12 |
| Fresh unchanged base / `r2-base-full.xml` | 2986 | 56 | 38 | 34 | 3114 | 158.06 |
| R2 implementation `f56c9ce` / `r2-final-full.xml` | 3025 | 56 | 38 | 34 | 3153 | 162.33 |

**New failing cases versus the fresh unchanged base: none.** The same **94** failure/error identities occur in both; no base failure disappears. Candidate adds **39 passing cases**. All **39 directory-processing tests pass** in both R2 full runs. The fresh base reproduces the SQL 403-versus-400 failure: both fresh runs have that one extra identity compared with the saved original base, so this report does not claim identical results to the saved 93-failure snapshot. Full-suite exit status remains 1; this is baseline parity, not a green full suite. Candidate warnings: 804; fresh-base warnings: 764. Exact comparison is saved in `r2-comparison.json`.

Evidence SHA-256 digests:

- `base.xml`: `013e61539f7f6ecc9ff55f0f3c6e08cf1c93958b4e49576528181906179ac01e`.
- `r2-focused.xml`: `214444663f7fa22c33ca0aabcdf3d4398afdca17a9e5d1d661f107ffee0e093a`.
- `r2-full.xml`: `05f181ccfce7fa0b411b5ec12c461d0f2ff3fb0dfd60c63a299aef2fe5398ae1`.
- `r2-base-full.xml`: `f4f8555651ef852d79b18f93e7a9afac6911ead9de3d5c524c876f271297945b`.
- `r2-final-full.xml`: `eb382e67d4c5e63ec29e8ada567d9ec1a7edbf65b4bf259446e402e8ece82c3c`.
- `r2-comparison.json`: `603db82696ce8f621ed1e3d9001b6401439099a325761d7213cd27990eae76ff`.
- `r2-frontend.log`: `0ac3caa0bbf4da3f65ca8a619061d4568f6b01ce581dd0907dff35305947f5fe`.
- `r2-types.log`: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- `r2-build.log`: `2d9199c8e858e74354d301834488f6b229720b420b8191fab8cdee3f7568ca92`.

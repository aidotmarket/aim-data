# S1717 chunk C — section 3a wire-contract follow-up

Branch: `build/bq-multi-file-datasets-s1717-c`; continuation start: `3662046601bad10c1d9e32d3cbe3c6c31f27b352`; implementation: `e1e4eb5`.
Base remains `adb97f0497223525d4247fa4bb200076fa5fa969`.

Authority: fetched runbooks `origin/main` at `bd703814803642f5a8e364767caecc89ef472083`, `specs/BQ-MULTI-FILE-DATASETS-S1717-GATE2.md` **§3a, Chunk C wire contract**. Receiver source: `ai-market-backend@305248064d836f9caaa4118f34aab4889aa21fae`. Read chunk B source at `9624a037d8345893cff5e9738a19cda38c6680e9` with `git show`; no merge of B. Continued in the existing branch-owning worktree; no new branch, rebase, history rewrite, PR or deployment.

## Changes against §3a

1. **Carrier addition / Publish payload (§3a):** local versions now emit signed `sample_members_total`, counted from the frozen manifest's `is_sample` members. The emitter requires it for local versions, refuses it on S3, and checks `members_total - sample_members_total >= 1`. The existing stricter data-only paid-set check still runs before registration or signing. Both S3 byte goldens pass unchanged, including explicitly null new defaults. A real signed HTTP mock verifies the count is in the hashed payload. The receiver's HTTP 400 `at least one non-sample data member is required` passes through verbatim and is rendered in the frontend alert (backend and UI tests).
2. **Profile carrier (§3a):** chunk B writes `metadata_json.directory_profile.members[str(index)] = {status: "profiled", profile: {columns: [{name, type, nullable, ...}], ...}, ...}` in `directory_processing.py`; `listing_metadata_service.py` persists aggregate listing metadata separately. The sender projects the former onto `schema_info.member_profiles`, retaining only index/name/type for profiled data members in the published manifest. Non-data/unknown members, absent profiles and malformed outcomes are ignored; no valid profiles means no key, allowing the receiver's “not profiled” fallback. A stored-metadata fixture with two different column sets verifies both signed profiles, plus absent/malformed cases. No chunk B runtime import or merge is needed.
3. **Members endpoint detected_type mirror (§3a):** added `tests/fixtures/multi_file_datasets/processable_types.json`, the exact sorted literal set serialized with `json.dumps(sorted(list), separators=(",", ":")) + "\n"`. The test checks both bytes and equality with `processing_service.PROCESSABLE_TYPES | {"unsupported"}`. SHA-256: **`5d6c91d85e77762277a79c03d31f442e21fc69de350f0d65588b77857faea089`**. The file is not present at the pinned backend SHA or its locally available chunk C branch at inspection; this digest is supplied for the in-progress backend fold to match. No claim of a cross-repository file comparison is made.

## Follow-up validation

Focused modules: **52 passed** in 4.21 s (`test_member_upload_client.py`, `test_dataset_publish_signed_proxy.py`, `test_alembic_025_026.py`). Frontend: **74 passed across 10 files**, 1.48 s; production build passed in 2.96 s (existing bundle-size warning); `tsc --noEmit` passed. `git diff --check` passed.

Fresh full-suite comparison on base and implementation `e1e4eb5`, with default-off flags, the same Python environment, separate serial directories and `PYTHONHASHSEED=0`:

| Run | Passed | Failed | Errors | Skipped | Total | Duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Base `adb97f0` | 2,986 | 56 | 38 | 34 | 3,114 | 151.13 s |
| Candidate `e1e4eb5` | 3,033 | 56 | 38 | 34 | 3,161 | 153.89 s |

**No new failing cases; no resolved cases.** All 94 failure/error identities match exactly by JUnit `classname::name`. Candidate has 47 additional passing tests over base. The full suite remains non-green.

The initial unseeded comparison had base 2,987 passed / 55 failed / 38 errors and candidate 3,033 passed / 56 failed / 38 errors. Its sole difference was `tests.test_sql::test_query_execution_blocked` (403 versus expected 400). The fixed-seed full repeat reproduces that failure on both revisions; SQL router/service/test files are unchanged from base. Both runs are retained; this is a pre-existing variable result, not silently discarded evidence.

Evidence: `/tmp/s1717-c-wire-{base,final,base2,final2,focused}.{log,xml}`, `/tmp/s1717-c-wire-comparison.json`, `/tmp/s1717-c-wire-{frontend,build,tsc}.log`. `base2`/`final2` are the fixed-seed full repeats. Commands (from the corresponding checkout; frontend commands from `frontend/`):

```sh
rtk proxy env PYTHONHASHSEED=0 AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-c-wire-base2-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/s1717-c-wire-base2.xml
rtk proxy env PYTHONHASHSEED=0 AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-c-wire-final2-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/s1717-c-wire-final2.xml
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q tests/test_member_upload_client.py tests/test_dataset_publish_signed_proxy.py tests/test_alembic_025_026.py --tb=short --junitxml=/tmp/s1717-c-wire-focused.xml
rtk proxy env NODE_OPTIONS=--no-experimental-webstorage npm test -- --run
rtk proxy npm run build
rtk proxy ./node_modules/.bin/tsc --noEmit
```

JUnit SHA-256:

- `base2`: `5694f05ba71ac30866ec0c9ffa2a67c3f8d380ef9e42452661479a72d936765a`.
- `final2`: `d7364b4c30b2b001a97cbaf1374c7855c29618615d0f72e30b0dfb88b080b8c5`.
- `focused`: `d323a3fbcaa25b4fe5d095d67199bd011dcdc22c84ffac4bb01f91ad54f614f5`.

## Operating scope

Re-read `runbooks/aim-data-seller-publish-journey.md`; the branch-local operating supplement below still applies. §3a adds no operator action. Local-route enablement remains gated; no provider, credential, production or external-message changes. Live receiver/database/store integration, independent review, copied real-install acceptance and enabled release remain outstanding and are not claimed by this sender follow-up.

---

## Prior reconciliation report (historical)

The following records the preceding reconciliation and its older receiver pin/results. The §3a follow-up above supersedes its carrier list and validation counts.

### S1717 chunk C — AIM Data sender reconciliation report

Branch: `build/bq-multi-file-datasets-s1717-c`.
Base: `adb97f0497223525d4247fa4bb200076fa5fa969` (chunk A, merged PR #66).
Continuation start: `84037ebca7865f516226f2d287224c7f695ad798`.
Reconciled code: `82564c1` (this report is committed afterward).
Receiver authority: ai-market-backend `7002ab34d5d6a75b6515b314a523df4418ce3c65`, on the same named branch.

**The sender now implements the pinned receiver's members, samples, signatures and activation contract.** All receiver references below are line numbers at that exact commit, read with `git -C /Users/max/Projects/ai-market/ai-market-backend show <sha>:<path>`. Its source and detached worktree were not modified. This is sender contract verification with real HTTP serialization and Ed25519 signatures against a mocked transport, not a live two-repository integration or enabled release.

## Scope and operating notes

Continued in the existing branch-owning worktree `/private/var/tmp/koskadeux/minimal-bridge-worktrees/498574af95a3-ac8271`, verified clean at the requested head. The supplied working directory was detached at that same head. No branch creation, rebase, history rewrite, PR, deployment, feature activation, secrets access or provider changes.

Consulted `/Users/max/Projects/ai-market/runbooks/aim-data-seller-publish-journey.md`. These branch-specific operating notes supplement its legacy metadata-only publish description: selected original sample bytes use the install-signed local sample route below. Local publishing remains gated; ordinary S3 publishing and its signed bytes remain unchanged. The prior implementation authority remains Gate 1/2 in the runbooks repo, as recorded in the pre-continuation report at `84037eb`.

On interruption or `sample_store_unavailable`, show the receiver error and leave local progress pending. Retry the existing publish operation after the underlying condition clears. It returns the same version's current status and resumes frozen member uploads from the last acknowledged offset. Each request gets a fresh action JWT/jti. A lost chunk ACK replays the exact frozen envelope; a lost final-sample ACK is recovered from the repeated publish response without resending samples to an already active version. Do not mark pending, quarantined or superseded versions published. No remote status polling route is introduced; the existing AIM Data `publish_status` only reports locally persisted progress.

## Reconciled receiver contract

| Concern | Exact contract and pinned backend source |
| --- | --- |
| Publish fields | Top-level `VZPublishPayload.agent_version`; version `source_kind="aim_data_local"`, `members_total`, `members_upload_id`. `app/schemas/vz_publish.py:133–172`; local creation/binding `app/services/vz_publish_service.py:1159–1209`. Initial local response carries `versions[].version_id` and `pending_members`; result schema `app/schemas/vz_publish.py:256–270`. |
| Member envelope | POST `/api/v1/vz/versions/{version_id}/members`, exactly `{version_id, members_upload_id, members}`. Body UUID must match path UUID. `app/routers/vz_publish.py:282–308`; `app/schemas/vz_publish.py:313–325`. |
| Member rows/bounds | Exactly index, relative_path, size_bytes, sha256, detected_type, role, is_sample; extra fields forbidden. `detected_type` is PROCESSABLE_TYPES plus unsupported; canonical path <=1,024 UTF-8 bytes. `app/schemas/vz_publish.py:281–310`. Sender uses <=configured PUBLISH_MEMBER_CHUNK and <=1,000 rows; existing worst-case path test includes both UUIDs and stays below the receiver's 8 MiB default body cap (`app/core/config.py:82–88`). Receiver enforces bytes at `app/routers/vz_publish.py:289–300` and configured chunk count at `app/schemas/vz_publish.py:319–325`. |
| Member authentication | Bearer EdDSA install-action JWT, action `publish_version_members`, `metadata_hash` of the complete JSON envelope. `app/routers/vz_publish.py:258–305`. Signature/install/identity/jti/action/hash validation: `app/services/vz_publish_service.py:339–490`. |
| Canonical hash | Backend `_jcs_serialize` / `compute_metadata_hash` at `app/services/vz_publish_service.py:68–86` exactly match sender `marketplace_action_signer.canonical_json_bytes` / `canonical_payload_hash`: `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`, then SHA-256 hex. Tests independently mirror these options and recompute member and sample JWT `metadata_hash`; non-ASCII member paths exercise UTF-8 behavior. Key insertion order does not affect the sorted-key hash. |
| Sample request | POST `/api/v1/vz/versions/{version_id}/samples/{index}?members_upload_id=<uuid>`, raw original bytes, `Content-Type: application/octet-stream`, exact `Content-Length`; no JSON/base64 sample body. Action `publish_sample_member`; signed metadata exactly `{version_id: str, members_upload_id: str, index: int, size_bytes: int, sha256: str}`. Receiver derives size/hash from the frozen member: `app/api/v1/endpoints/vz_samples.py:20–39`. |
| Sample streaming | Sender validates selected member limits, freezes one sample into a spooled temporary file, verifies byte count/SHA-256 before any egress, then streams that verified snapshot in <=64 KiB chunks. Memory spool threshold is 1 MiB; temporary storage closes after each request. Receiver streams, bounds and hashes raw bytes at `app/api/v1/endpoints/vz_samples.py:72–90`. Non-sample original files are not read. |
| Activation | `finalize_local_version` waits for all members, validates the manifest and transfer limits, then waits for every selected sample's active asset/size/hash: `app/services/vz_publish_service.py:1249–1286`. Without samples, the final member response can activate. With samples, the final sample response is `{version_id, status, index}`: `app/api/v1/endpoints/vz_samples.py:103–109`. Sender consumes these statuses and stops further uploads on active/quarantined/superseded, including on repeat publish. |
| Idempotency | Exact frozen chunks are acknowledged even after activation; conflicting members are refused. `app/services/vz_publish_service.py:1350–1377`. Samples are replayable while pending; terminal versions return 409, so sender uses repeat publish to recover a lost final ACK. `app/api/v1/endpoints/vz_samples.py:38–40,69–71`. |
| Worked receiver examples | Real signed member HTTP/bounds and sample HTTP/tampering examples: `tests/test_vz_publish_local_route.py:472–550`. Also read `docs/reports/s1717-c-backend-builder-report.md`; its inherited joint acceptance limits are not claimed closed by this sender change. |

### Named receiver refusals

Sender passes through the HTTP status and `detail` unchanged and does not advance unacknowledged work. The following are tested through real httpx request serialization with a mock receiver:

| HTTP | Detail | Pinned receiver source |
| --- | --- | --- |
| 400 | `<relative_path>: SAMPLE_MAX_FILE_BYTES: N` | `app/api/v1/endpoints/vz_samples.py:43–44` |
| 400 | `SAMPLE_MAX_FILES: N; SAMPLE_MAX_TOTAL_BYTES: N` | `app/api/v1/endpoints/vz_samples.py:45–46` |
| 409 | `SAMPLE_SELLER_QUOTA_BYTES: N` | `app/api/v1/endpoints/vz_samples.py:57–58` |
| 429 | `SAMPLE_UPLOAD_RATE: N` | `app/api/v1/endpoints/vz_samples.py:47–49` |
| 409 | `version is no longer pending_members` | `app/api/v1/endpoints/vz_samples.py:39–40,70–71` |
| 413 / 400 | `sample size mismatch` (overlong / short) | `app/api/v1/endpoints/vz_samples.py:78–84` |
| 409 | `sample hash mismatch` | `app/api/v1/endpoints/vz_samples.py:89–90,103–104` |
| 503 | `sample_store_unavailable` | `app/api/v1/endpoints/vz_samples.py:101–102` |

Preflight uses the same named file/count/total limits before signing/registration; sender quota and rate decisions remain the receiver's responsibility. Store failure retains pending progress and permits a later explicit retry. An external status change can explain a terminal-version 409; subsequent publish obtains the actual status rather than treating 409 as success.

## Wire examples

IDs/hashes below are placeholders; `0\n` represents two raw bytes (ASCII zero and LF). UUIDs are canonical lowercase strings. JWTs use EdDSA, existing seller `sub` and install `iss`, 300-second expiry and a fresh `jti`. All three actions use `metadata_hash`, not `payload_hash`.

`POST /api/v1/vz/publish`, `Authorization: Bearer <publish_listing JWT>`, `Content-Type: application/json` (ordinary fields abbreviated):

```json
{
  "title": "Example dataset",
  "description": "Two original data files",
  "tags": [],
  "pricing_type": "one_time",
  "price_cents": 2500,
  "vz_raw_listing_id": "<dataset-id>",
  "download_channel": "<CHANNEL.value>",
  "agent_version": "<settings.app_version>",
  "versions": [{
    "version_label": "<version-label>",
    "source_kind": "aim_data_local",
    "object_count": 2,
    "total_size_bytes": 4,
    "manifest_hash": "<canonical-member-list-sha256>",
    "members_total": 2,
    "members_upload_id": "<upload-uuid>"
  }]
}
```

Initial response includes `versions: [{version_id: "<version-uuid>", version_label: "<version-label>", status: "pending_members", quarantine_reason: null}]`.

`POST /api/v1/vz/versions/<version-uuid>/members`, `Authorization: Bearer <publish_version_members JWT>`, `Content-Type: application/json`. JWT `metadata_hash` covers this entire envelope:

```json
{
  "version_id": "<version-uuid>",
  "members_upload_id": "<upload-uuid>",
  "members": [
    {"index": 0, "relative_path": "sample.csv", "size_bytes": 2, "sha256": "<sha256-of-zero-and-LF>", "detected_type": "csv", "role": "data", "is_sample": true},
    {"index": 1, "relative_path": "paid.csv", "size_bytes": 2, "sha256": "<paid-sha256>", "detected_type": "csv", "role": "data", "is_sample": false}
  ]
}
```

Member response: `{version_id: "<version-uuid>", version_label: "<version-label>", status: "pending_members", quarantine_reason: null}` because the selected sample is not yet stored.

```http
POST /api/v1/vz/versions/<version-uuid>/samples/0?members_upload_id=<upload-uuid>
Authorization: Bearer <publish_sample_member JWT>
Content-Type: application/octet-stream
Content-Length: 2

0
```

The sample JWT `metadata_hash` covers the following metadata, which is **not** sent as the HTTP body:

```json
{"version_id":"<version-uuid>","members_upload_id":"<upload-uuid>","index":0,"size_bytes":2,"sha256":"<sha256-of-zero-and-LF>"}
```

Final sample response: `{"version_id":"<version-uuid>","status":"active","index":0}`. Only then does AIM Data report `published`. `versions[0]` and the sender's `version` summary reflect the final status. Sample completion can also return quarantined, which remains quarantined.

S3 omits local-only fields and agent_version, as before. Both S3 golden tests are unchanged. Disclosure remains the existing separately authenticated `member_files`/`none` decision; no duplicate sample bytes or members are added to disclosure.

## Validation

Final focused modules: **47 passed, 0 failed/errors**, 3.98 s: `test_member_upload_client.py` 11, `test_dataset_publish_signed_proxy.py` 34, `test_alembic_025_026.py` 2. Both existing S3 byte goldens are unchanged and pass. Added coverage includes independently mirrored backend hash canonicalization, exact envelopes/headers/actions, named refusals and later retry, no-sample final-chunk activation, terminal responses, frozen stream bytes, lost chunk ACK and lost final sample ACK.

Frontend: **73 passed in 10 files**, 2.22 s; production build passed, 3.65 s (existing large-bundle warning); TypeScript `tsc --noEmit` passed. No frontend source changed.

Fresh full flag-default-off comparison used the same existing Python environment, separate serial directories, and exact JUnit `classname::name` identities. The baseline is the existing clean detached `/tmp/s1717-c-base` at the exact base above. Candidate is `82564c1`; only documentation changes follow it.

| Run | Passed | Failed | Errors | Skipped | Total | Pytest duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Base `adb97f0` | 2,987 | 55 | 38 | 34 | 3,114 | 160.48 s |
| Reconciled candidate `82564c1` | 3,029 | 55 | 38 | 34 | 3,156 | 158.35 s |

**New failing cases: none. Resolved base failures: none.** The identical 93 failing/error identities occur in both runs. Candidate adds 42 passing tests over chunk A (22 added by this continuation). The full suite is not green; no unrelated fixes were made. These fresh results supersede the previous report's 56-failure baseline count; both current runs have 55 failures.

Evidence: `/tmp/s1717-c-reconcile-{base,final,focused}.{log,xml}`, `/tmp/s1717-c-reconcile-comparison.json` (counts and complete exact-case failure sets), `/tmp/s1717-c-reconcile-frontend.log`, `/tmp/s1717-c-reconcile-build.log`. `git diff --check` passed.

Commands, from the corresponding checkout (frontend commands from `frontend/`):

```sh
rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-c-reconcile-base-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/s1717-c-reconcile-base.xml
rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-c-reconcile-final-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/s1717-c-reconcile-final.xml
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q tests/test_member_upload_client.py tests/test_dataset_publish_signed_proxy.py tests/test_alembic_025_026.py --tb=short --junitxml=/tmp/s1717-c-reconcile-focused.xml
rtk proxy env NODE_OPTIONS=--no-experimental-webstorage npm test -- --run
rtk proxy npm run build
rtk proxy ./node_modules/.bin/tsc --noEmit
```

JUnit SHA-256:

- `base`: `9c754c8e02fbf90c8517435da9a67f4c499f69939fea3cac19ac1a04f3e655ad`.
- `final`: `368584871efeff43ba8c0e65dbe20dd932e1b75de43d60e4db4e0cfe4404d812`.
- `focused`: `23f5efd831b4b7fd8739e00db8eb079122189d3e652eacb4750e541b48f2d589`.

## Preserved implementation and remaining acceptance boundaries

Retained original chunk C behavior: frozen `(listing_version_id, manifest_hash)` snapshots/root authority independent of live edits; paid-set preflight; stable upload UUID and local version labels; local progress persisted before uploads; missing version-id retry; directory publish UI, named errors and optional verification heading; legacy S3 and flag-off behavior. No frontend source change was needed for this reconciliation.

Migration 026 remains chained to 025. The two focused migration tests still cover forward/backward behavior and legacy row preservation on a synthetic AC7-shaped install. No released migration was edited. A copied real-install AC7 fixture remains outstanding.

Not run here: a live AIM Data process against the backend process/database/object store, browser acceptance of merged chunks B/C, actual shared workspace-route quota concurrency, real cloud/provider proof, independent Gate 3 review or enabled release. Chunks D/E and whole-BQ completion remain separate. These are inherited acceptance boundaries, not unresolved sender wire choices. The requested sender-to-source contract reconciliation and checks are complete.

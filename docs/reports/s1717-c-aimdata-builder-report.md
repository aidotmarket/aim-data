# S1717 chunk C — AIM Data sender report

Branch: `build/bq-multi-file-datasets-s1717-c`.
Base: `adb97f0497223525d4247fa4bb200076fa5fa969` (chunk A, merged PR #66).
Code candidate: `7b1be6fbaede4dc1869d1b5ee7ae8c32f3a492ef`; this report is committed afterward.

**Sender implementation is pushed; cross-repository interoperability is not yet established.** Gate 2 specifies the publish fields, member endpoint and seven-field tuple, but not the complete member envelope or sample-upload HTTP contract. The concrete choices below require reconciliation with the separate receiver dispatch before this can be called an accepted chunk C. No receiver branch was inspected, no PR created, no production flag enabled, and no secrets or provider settings were accessed.

## Authority and scope

Read Gate 1 and Gate 2 using `git show origin/main:specs/BQ-MULTI-FILE-DATASETS-S1717-GATE{1,2}.md` in `/Users/max/Projects/ai-market/runbooks`; observed authority ref `2cafd9c020205e32b4ebae1d4ee853f644504189`. Applied Gate 2 §4.3 AIM-side scope, §3 wire, §6 policy, §7 evidence, and Gate 1 D0/D3/D4/D5/D7. Read chunk A models, manifest builder, directory router branches, config and builder report, plus both existing publish paths before implementation.

The initial worktree was clean and detached at the exact base. Created the requested branch there. No processing, scanner, fulfillment, backend, configuration, or released migration file was changed. DatasetDetail changes are confined to the publish control/import and adjacent optional verification heading, plus its test file. Chunk B's member table and processing areas remain untouched.

## Changes

| File | Change |
| --- | --- |
| `app/routers/marketplace_publish.py` | Load stored members in index order; derive manifest/scalars with chunk A; paid-set and sample preflight before crypto/registration/signing; emit local version and top-level app version; preserve S3 wire defaults; record retained manifest and listing id together; retry missing version id; resume acknowledged offsets; surface pending status and receiver errors; accept gated `member_files` disclosure. |
| `app/services/marketplace_push_service.py` | Signed member-upload orchestration using an injected publish signer/transport; at most configured PUBLISH_MEMBER_CHUNK and never above 1,000; advance checkpoint only after ACK. |
| `app/services/sample_upload_client.py` | Validate all selected samples before upload; enforce file/count/total limits; read only selected original files, reject missing/changed bytes by member name, hash-check before egress. |
| `app/models/published_manifest.py` | Composite `(listing_version_id, manifest_hash)` primary key; frozen root and exact seven-field member JSON; independent of live-member edits and dataset deletion. |
| `alembic/versions/026_bq_published_manifests_s1717.py` | Create retained snapshot table and dataset lookup index; chain from 025. No released revision edited. |
| `frontend/src/components/PublishModal.tsx` | Directory publish control with file-sample decision/opt-out, disclosure retry, pending status, named upgrade errors; standalone modal also refuses to call pending/quarantined versions published. |
| `frontend/src/pages/DatasetDetail.tsx` | Use directory publish control within existing metadata/disclosure approval area; optional verified-shape heading; no verification request added to publish. |
| `frontend/src/pages/DatasetDetail.test.tsx` | Directory `member_files`, pending/resume and upgrade-error assertions. |
| `tests/test_dataset_publish_signed_proxy.py` | S3 byte goldens, local wire, pre-sign refusals, missing version retries, signature/retention round trip and migrated-record republish. |
| `tests/test_member_upload_client.py` | Chunk boundaries and failure resume, frozen retention/root independence, pending status, selected bytes, sample bounds and changed bytes, worst-case path size, flag-off refusal. |
| `tests/test_alembic_025_026.py` | Update head expectation and exercise 026 on an AC7-shaped synthetic install without modifying legacy records. |

Commits are deliberately separated: storage/migration; sender/client; frontend; provenance and full sender tests; standalone modal status correction; report.

## Migration evidence

Before editing:

```text
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m alembic heads
025_bq_multi_file_datasets_s1717 (head)
```

After implementation, the same command returns exactly:

```text
026_bq_published_manifests_s1717 (head)
```

026 declares `down_revision = "025_bq_multi_file_datasets_s1717"`. The tests verify the composite key and that published/unpublished rows sharing a batch, with different original/storage names and an existing processed path, remain unchanged across upgrade/downgrade. This is a synthetic install fixture, **not a sanitized real-install AC7 copy**; real-install acceptance remains outstanding as it did for chunk A. Scan and delivery AC7 clauses belong to E and D.

## Validation

Final focused backend: **25 passed, 0 failed/errors**, 3.84 s; `test_dataset_publish_signed_proxy.py` 13, `test_member_upload_client.py` 10, `test_alembic_025_026.py` 2. Evidence: `/tmp/s1717-c-focused-final.log` and `.xml`.

Final frontend: **73 passed**, 10 files, including 15 DatasetDetail tests; 1.50 s. Production build passed (2.93 s; existing large bundle warning); TypeScript `tsc --noEmit` passed. Evidence: `/tmp/s1717-c-frontend-delivery.log`, `/tmp/s1717-c-build-delivery.log`.

Full flag-default-off comparison uses a new detached baseline at `/tmp/s1717-c-base`, the exact base SHA above, the same existing Python environment, separate per-run serial directories, and exact JUnit `classname::name` identities. New feature tests enable the flag explicitly and restore it.

| Run | Passed | Failed | Errors | Skipped | Total | Duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Exact base | 2,986 | 56 | 38 | 34 | 3,114 | 156.35 s |
| Final backend candidate `89fb6a5` (backend-identical to code head above) | 3,006 | 56 | 38 | 34 | 3,134 | 150.49 s |

**New failing cases: none. Resolved base failures: none.** All 94 base failing/error identities persist; the final candidate adds 20 passing tests. Logs/JUnit: `/tmp/s1717-c-base.{log,xml}`, `/tmp/s1717-c-final.{log,xml}`. Machine-readable exact-case comparison: `/tmp/s1717-c-comparison.json`.

An earlier full candidate run had 3,003 passed, 56 failed, 38 errors and 34 skipped: no new failing identities against the base. A final full rerun was started after the additional provenance/round-trip tests and status fixes; final counts above supersede that intermediate run. The suite is not green; no unrelated fixes were attempted.

Commands (from the appropriate checkout; all shell commands use RTK):

```sh
rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-c-base-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/s1717-c-base.xml
rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/tmp/s1717-c-final-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/s1717-c-final.xml
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q tests/test_dataset_publish_signed_proxy.py tests/test_member_upload_client.py tests/test_alembic_025_026.py --tb=short
# frontend/
rtk proxy env NODE_OPTIONS=--no-experimental-webstorage npm test -- --run
rtk proxy npm run build
rtk proxy ./node_modules/.bin/tsc --noEmit
```

## Emitted wire examples

Placeholder ids/hashes below are illustrative. Paths and member names are synthetic. No root path is transmitted. The signer remains `publish_listing` with canonical-body `metadata_hash`, EdDSA, existing issuer/seller identity and short expiry.

`POST /api/v1/vz/publish` (ordinary listing fields abbreviated):

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
    "manifest_hash": "<sha256-of-canonical-member-list>",
    "members_total": 2,
    "members_upload_id": "<stable-upload-uuid>"
  }]
}
```

`POST /api/v1/vz/versions/<version-id>/members`:

```json
{
  "members_upload_id": "<stable-upload-uuid>",
  "members": [
    {"index": 0, "relative_path": "sample.csv", "size_bytes": 2, "sha256": "<sample-sha256>", "detected_type": "csv", "role": "data", "is_sample": true},
    {"index": 1, "relative_path": "paid.csv", "size_bytes": 2, "sha256": "<paid-sha256>", "detected_type": "csv", "role": "data", "is_sample": false}
  ]
}
```

Provisional sample transport: `POST /api/v1/vz/versions/<version-id>/samples/0`:

```json
{"manifest_hash": "<manifest-sha256>", "content_base64": "MAo="}
```

Disclosure uses the existing disclosure endpoint and seller authentication with `sample_decision: "member_files"`, `approved_sample: null`, and no duplicate member list. The UI can opt out with `none`. S3 versions omit `source_kind`, `members_total`, `members_upload_id` and `agent_version`; canonical signed payload bytes remain the legacy golden.

## Ambiguities, choices and continuation requirements

References use line numbers from the authority read via `git show`, not shifted source-code line numbers.

| Authority | Choice / limitation |
| --- | --- |
| Gate 2 §3 line 64, §4.3 line 95 | The member envelope is not specified beyond chunks of seven-field objects. Chose `{members_upload_id, members}`; the path binds the receiver version and the upload UUID binds the publish operation. No chunk number/final flag invented: member count and indices determine completion. **Receiver agreement remains unverified.** |
| Gate 2 §4.3 lines 95–96; §3 lines 65–66 | The `vz_samples` module is named but no HTTP path, body schema or sample signing action is pinned. Chose the version/index path shown above and `{manifest_hash, content_base64}`, signed with the same publish JWT. Binary is bounded before base64 expansion. **This is a provisional implementation choice, not an approved wire fact.** A clarification was requested during the run; none was available at report time. |
| Gate 1 D3 line 60; Gate 2 §3 lines 64–65 | Sender uploads members then selected sample bytes and only reports published on receiver `active`. This requires the receiver to keep a sample-bearing version pending after member completion until every required asset is stored, and to return final version status from sample completion. **Activation order/status contract must be reconciled; the spec's final-member activation and fail-closed sample-store clauses do not specify this handshake.** |
| Gate 1 D0 line 50; Gate 2 §4.3 line 95 | Retain JSON member rows in one `published_manifests` row, not a second mutable child relation. Persist alongside listing id/progress before uploads; insert-only helper refuses conflicting root/member values for the same key. No FK cascade can erase a retained snapshot with live registration. |
| Same | Missing version id retries the identical publish twice (three total attempts), then fails with a named retry message and never writes an unkeyed local publish. Transport failures during member/sample upload remain pending and resume on another publish request. A receiver-assigned replacement version id resets the offset to zero. |
| Gate 1 D3 line 60; Gate 2 §3 line 64 | Generic local publish has no caller version label; choose `manifest-<first 32 hash characters>`. Explicit `/marketplace/versions/publish` accepts seller version labels. UUIDv5 derives the upload id from dataset/root/label/hash for stable retries; root stays local. |
| Gate 1 D1a line 56; Gate 2 §7 line 172 | A legacy record with migrated root/member rows uses the local version route upon republish without changing its file type/path/batch. S3 provenance remains excluded, both through source metadata and object records. Legacy no-root records keep the existing route. |
| Gate 1 D4 line 62 versus shipped chunk-A base | The spec's old inventory says `approved_rows` exists; this base has deliberately closed it (`Literal["none"]`, `legacy_sample_unavailable`). Added only gated `member_files` and preserved the existing row refusal rather than reopening S1294 row egress. |
| Gate 1 D4 line 62; Gate 2 §6 lines 149–152 | Aim-side file/count/total bounds run before signing or upload; global seller quota belongs to the receiver, which can count both routes. No local quota history is fabricated. |
| Gate 1 D5 line 64; Gate 2 §6 line 148 | Inject the actual `settings.app_version`, including `dev` if configured. No false `1.24.0` value or release bump: absent/unparseable/old-version refusal belongs to the receiver, and its named message is rendered unchanged. |
| Gate 2 §4.3 line 98 versus §4.6 line 118; user merge fence | Add the optional heading adjacent to the ListingPreparation publish area. Do not modify the verification component or other processing UI shared with B; broader copy/runbook work remains F. |
| Gate 2 §4.3 line 95, frontend dependency types outside the allowed file list | Keep directory-specific disclosure transport inside PublishModal's exported control, preserving legacy shared API/disclosure types and limiting DatasetDetail edits to the publish area. Full merged B→C journey and sample-count refresh after member edits need integration review. |

Required continuation: settle the three transport/activation rows first; add a shared receiver/sender contract fixture and run a true backend-interoperability test including interrupted samples, store failure and post-restart resume. Validate the merged B/C UI journey (including up-to-date seller-selected count), real-install AC7 evidence, and receiver quarantine/upgrade cases. Do not enable the local route from this sender-only report. D and E remain separate implementations; no Gate 3/4 or full BQ completion is claimed.

## Evidence digests

- `/tmp/s1717-c-base.xml`: `30dc1184bc7eeaf13ad1c41e1bfee440b526f017b73c07836ba4ae9b219048d6`.
- `/tmp/s1717-c-final.xml`: `b4eabd83a5ee0334f387cfc06c626aa8137619ccb1b4bcbe5bc05a3e20351317`.
- `/tmp/s1717-c-focused-final.xml`: `13944c36a1b7449f97002dc29ec21b4eea73426fda45dbb9b0b80b8ba34717a8`.

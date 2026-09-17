# S1717 chunk D — AIM Data sender builder report

Implemented and tested the sender half on `build/bq-multi-file-datasets-s1717-d`, based exactly on `7dc92619c2cecce4c7492d1eed823fcaf9ee4328`. No backend branch was read or used as a contract. No PR, release tag, image publication, deployment, provider mutation or secret access was performed.

## Authority and identity

Runbooks were fetched, then read with `git show origin/main:<path>`. `origin/main` resolved to `a64d6665418a8e90dd25b4501d3265240d635862`:

- `specs/BQ-MULTI-FILE-DATASETS-S1717-CHUNK-D-STORE-AMENDMENT.md`, full document; especially §2 line 46, §3.2 line 54, §3.3 line 57, §3.5 line 63, §3.8 lines 80–94, §4 AC-D2 line 98, and §7 lines 119–120.
- `specs/BQ-MULTI-FILE-DATASETS-S1717-GATE1.md`, D5 line 64.
- `aim-data-release-process.md`, full document: customer defaults change at stable promotion; tag-derived VERSION flows through the customer Dockerfile.

Implementation commits: `45d83bb` (application response correlation and base golden artifact), `a08d0a7` (sender, settings, version metadata), `4687cdb` (sender tests). The following report-only commit records the evidence.

## Result

The new branch is selected only when the feature flag is true and deliver parameters carry `manifest_hash`. The original fulfillment file is byte-identical to the base after removing the five-line gate. The original `_stream_chunks`, `_validate_ack`, complete path and S3 branch are untouched.

The sender loads `(purchased_version_id, manifest_hash)` from `published_manifests`, recomputes the hash, validates dense published rows and the retained registration-to-published mapping, and freezes the plan in published order. No live dataset/member registration is required. Every data member, including samples, is delivered; documentation/other members are excluded without renumbering published indices. Each member contributes at least one chunk; byte offsets are cumulative data-member byte offsets, not `chunk_index * 65536` across short/empty members. Memory is O(members + four chunks).

Source paths come from the frozen root and retained canonical relative paths via the inverse registration mapping. NFC filesystem names are resolved lazily only for unverified members. Directory entries are cached per streaming pass. Component-by-component descriptor opens refuse symlinks, FIFOs and other non-regular files. Length and streamed SHA-256 must match before complete is sent. A missing local file already verified by the listener is not read on resume.

Metadata is awaited, server-returned transfer_id adopted, and verified indices validated. Only unverified members are streamed from their first chunks. The window remains at most four chunks; ACK cadence uses `(chunk_index - origin) % 4 == 3` or the original plan's last chunk. A timed-out window is re-sent once with identical application request IDs/messages. Application refusals are correlated by request_id, consumed even away from an ACK boundary, and never mistaken for grant success.

| Response | Sender action |
| --- | --- |
| `listener_busy` | Wait configured retry interval, replay metadata, resume from its indices. |
| `member_reset` / `out_of_window` | Replay metadata immediately, resume from its indices. |
| `finalizing` on chunk | Wait retry interval, replay metadata; accept existing grant or resume indices. |
| `finalizing` on complete | Wait retry interval and replay complete until grant or budget expiry. |
| Other refusal / invalid ACK | Send `TRANSFER_ABORTED`; record failed session, order remains resumable. |
| Socket loss | Existing channel loop reconnects; sender waits/replays metadata before any more chunks. |

Metadata timeouts replay metadata; completion timeouts replay complete. Settings default to `TRANSFER_METADATA_WAIT_S=30`, `TRANSFER_COMPLETE_RETRY_BUDGET_S=1800`, `TRANSFER_RETRY_AFTER_S=20`, supporting AIM_DATA_, VECTORAIZ_, and these bare environment names. ACK/complete waits remain 30 seconds. The completion budget remains one deadline across member repair; a metadata/reconnect/no-progress episode is likewise bounded, and forward chunk progress ends that episode. Successful completion always requires a server grant.

`settings.app_version` defaults to `1.24.0`, which the existing publish builder emits as top-level `agent_version`. `Dockerfile.customer` defaults VERSION to `1.24.0`; explicit release build arguments/environment overrides remain authoritative. Compose/installer stable defaults are unchanged, as the release runbook reserves their coordinated update for promotion. Caller must release/tag 1.24.0 or newer separately.

## Verification

Final focused command (Python 3.12.12 environment already supplied by the repo's `.venv`):

```sh
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest tests/test_manifest_fulfillment.py tests/test_fulfillment.py tests/test_trust_channel_client.py tests/test_dataset_manifest.py tests/test_directory_registration.py tests/test_member_migration.py tests/test_dataset_publish_signed_proxy.py tests/test_s1097_sender_version_publish.py tests/test_vz_publish_proxy.py tests/test_s3_publish_source_resolver.py -q --disable-warnings --junitxml=/tmp/s1717-d-aimdata-sender-final.xml
```

**320 passed, 15 warnings, 11.46 seconds.** 61 tests are new sender cases. Cases include the three-member golden with an empty member, data samples and global offsets; sparse and unaligned resume; all verified; absent verified local file; server session adoption; all four refusals at metadata/chunk/complete; a delayed non-boundary refusal with ACK withheld; retry pacing; metadata/complete timeout and budget expiry; exact one-window resend; permanent missing manifest; same-size source mutation; symlink replacement; invalid resume authority/ACK; disconnect; documentation exclusion; settings and real signed-publish payload version. Sender shapes cover one 128-chunk member, one 384-chunk member, a 5-chunk member followed by 130 chunks, and twenty tiny members. These are wire-peer tests, not live store timing tests.

The single-file golden contains SHA-256 fingerprints of all seven complete serialized application messages from the shipped base (metadata, five chunks, complete), with deterministic IDs and bytes. It was generated by executing the base file from `git show`, not the candidate. Tests reproduce it with flag false, flag true/no manifest, and flag false/manifest fields. `send_action`, handshake, identity and legacy `wait_for_action` were also compared directly to the base and are byte-identical. Existing encrypted websocket interoperability tests pass. `git diff --check` passes.

Separate config audit: `tests/test_config_alias_choices.py` has **four failures on both candidate and untouched base**, all caused by pre-existing `oauth_enabled` using the string alias `AIM_DATA_OAUTH_ENABLED` instead of AliasChoices. Verified in detached base worktree `/tmp/s1717-d-aimdata-sender-base-51907d`; no unrelated config correction made. The three newly added settings' default/alias tests pass. Candidate config evidence: `/tmp/s1717-d-aimdata-sender-config.xml`.

Counts by suite:

| Suite | Passed |
| --- | --- |
| `tests.test_manifest_fulfillment` | 61 |
| `tests.test_fulfillment.TestValidDeliverFlow` | 3 |
| `tests.test_fulfillment.TestUnknownListingId` | 1 |
| `tests.test_fulfillment.TestFileNotFoundOnDisk` | 1 |
| `tests.test_fulfillment.TestFileSizeCap` | 1 |
| `tests.test_fulfillment.TestStreamingMemory` | 1 |
| `tests.test_fulfillment.TestQueuedFulfillments` | 2 |
| `tests.test_fulfillment.TestFulfillmentLog` | 5 |
| `tests.test_fulfillment.TestS3PresignedUrlFulfillment` | 12 |
| `tests.test_fulfillment.TestACKBackpressure` | 1 |
| `tests.test_fulfillment.TestTransferIdGeneration` | 1 |
| `tests.test_fulfillment.TestACKValidation` | 1 |
| `tests.test_fulfillment.TestDoubleACKTimeout` | 1 |
| `tests.test_fulfillment.TestACKWindowResend` | 2 |
| `tests.test_fulfillment.TestQueueRaceRegression` | 1 |
| `tests.test_fulfillment` | 13 |
| `tests.test_trust_channel_client` | 43 |
| `tests.test_dataset_manifest` | 19 |
| `tests.test_directory_registration` | 12 |
| `tests.test_member_migration` | 5 |
| `tests.test_dataset_publish_signed_proxy` | 52 |
| `tests.test_s1097_sender_version_publish` | 32 |
| `tests.test_vz_publish_proxy` | 26 |
| `tests.test_s3_publish_source_resolver` | 24 |

## Exact application wire examples

Placeholder UUIDs below are synthetic. Example frozen members are `first.csv` = `a`, `empty.csv` = empty, `last.csv` = `bc`; all role=data, is_sample=false, detected_type=csv. The manifest hash below is computed from those exact canonical rows. The existing Trust Channel encryption/framing surrounds these JSON application payloads unchanged. **The shipped send_action retains top-level fulfillment fields and also nests them in parameters; that duplication and the inner metadata/complete parameters are intentionally preserved here.**

Cloud deliver:

```json
{
  "action": "vai.fulfillment.deliver",
  "request_id": "00000000-0000-4000-8000-000000000010",
  "parameters": {
    "order_id": "00000000-0000-4000-8000-000000000001",
    "listing_id": "00000000-0000-4000-8000-000000000002",
    "purchased_version_id": "00000000-0000-4000-8000-000000000003",
    "manifest_hash": "1e5e7df7ee29e0dd10812ff860d9466488bbec70ee57b82b60dd781140d7b756"
  }
}
```

Device metadata (awaited):

```json
{
  "action": "vai.fulfillment.metadata",
  "request_id": "00000000-0000-4000-8000-000000000005",
  "transfer_id": "00000000-0000-4000-8000-000000000004",
  "order_id": "00000000-0000-4000-8000-000000000001",
  "listing_id": "00000000-0000-4000-8000-000000000002",
  "manifest_hash": "1e5e7df7ee29e0dd10812ff860d9466488bbec70ee57b82b60dd781140d7b756",
  "parameters": {
    "transfer_id": "00000000-0000-4000-8000-000000000004",
    "order_id": "00000000-0000-4000-8000-000000000001",
    "listing_id": "00000000-0000-4000-8000-000000000002",
    "manifest_hash": "1e5e7df7ee29e0dd10812ff860d9466488bbec70ee57b82b60dd781140d7b756",
    "parameters": {
      "filename": "dataset-directory",
      "content_type": "application/octet-stream",
      "total_bytes": 3,
      "total_chunks": 3,
      "chunk_size": 65536,
      "sha256_hash": "1e5e7df7ee29e0dd10812ff860d9466488bbec70ee57b82b60dd781140d7b756",
      "hash_algorithm": "sha256"
    }
  }
}
```

Existing metadata response, including the resume authority (indices `[0]` mean stream only chunks 1 and 2):

```json
{
  "request_id": "00000000-0000-4000-8000-000000000005",
  "success": true,
  "data": {
    "success": true,
    "transfer_id": "00000000-0000-4000-8000-000000000004",
    "verified_member_indices": [
      0
    ]
  },
  "error": null
}
```

Zero-byte member chunk, global byte_offset=1:

```json
{
  "action": "vai.fulfillment.chunk",
  "request_id": "00000000-0000-4000-8000-000000000006",
  "transfer_id": "00000000-0000-4000-8000-000000000004",
  "order_id": "00000000-0000-4000-8000-000000000001",
  "listing_id": "00000000-0000-4000-8000-000000000002",
  "member_index": 1,
  "chunk_index": 1,
  "byte_offset": 1,
  "payload_length": 0,
  "chunk_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "payload": "",
  "parameters": {
    "transfer_id": "00000000-0000-4000-8000-000000000004",
    "order_id": "00000000-0000-4000-8000-000000000001",
    "listing_id": "00000000-0000-4000-8000-000000000002",
    "member_index": 1,
    "chunk_index": 1,
    "byte_offset": 1,
    "payload_length": 0,
    "chunk_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "payload": ""
  }
}
```

ACK for the plan’s last chunk, even though its origin-relative window is short:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000011",
  "success": true,
  "data": {
    "action": "vai.fulfillment.ack",
    "transfer_id": "00000000-0000-4000-8000-000000000004",
    "acked_through_index": 2,
    "status": "continue"
  },
  "error": null
}
```

Complete always declares the full plan, including previously verified members:

```json
{
  "action": "vai.fulfillment.complete",
  "request_id": "00000000-0000-4000-8000-000000000007",
  "transfer_id": "00000000-0000-4000-8000-000000000004",
  "order_id": "00000000-0000-4000-8000-000000000001",
  "parameters": {
    "transfer_id": "00000000-0000-4000-8000-000000000004",
    "order_id": "00000000-0000-4000-8000-000000000001",
    "parameters": {
      "status": "fulfilled",
      "file_size_bytes": 3,
      "chunk_count": 3,
      "sha256_hash": "1e5e7df7ee29e0dd10812ff860d9466488bbec70ee57b82b60dd781140d7b756"
    }
  }
}
```

Refusal example (the same error field carries each of the four values):

```json
{
  "request_id": "00000000-0000-4000-8000-000000000007",
  "success": true,
  "data": {
    "success": false,
    "error": "finalizing"
  },
  "error": null
}
```

Completion/metadata replay success requires the existing grant:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000007",
  "success": true,
  "data": {
    "success": true,
    "token_id": "00000000-0000-4000-8000-000000000009",
    "download_token": "synthetic-grant-token"
  },
  "error": null
}
```

Permanent missing retained manifest:

```json
{
  "action": "vai.fulfillment.error",
  "request_id": "00000000-0000-4000-8000-000000000008",
  "transfer_id": "00000000-0000-4000-8000-000000000004",
  "order_id": "00000000-0000-4000-8000-000000000001",
  "parameters": {
    "transfer_id": "00000000-0000-4000-8000-000000000004",
    "order_id": "00000000-0000-4000-8000-000000000001",
    "parameters": {
      "status": "failed",
      "error_code": "MANIFEST_NOT_FOUND",
      "error_message": "No retained manifest for purchased_version_id=00000000-0000-4000-8000-000000000003"
    }
  }
}
```

## Ambiguities and integration notes (authority sections/lines)

1. **Permanent error literal is unspecified.** Gate 1 D5 line 64 requires the absent-manifest refusal to name the version and permanently fail the order, but does not assign its `error_code`. This sender uses **`MANIFEST_NOT_FOUND`**, with `purchased_version_id` in error_message; all other local failures use resumable `TRANSFER_ABORTED`. The listener must map that literal to permanent delivery failure. An optional clarification was asked; no alternate code was supplied before this report. No extra permanent/retryable wire field was invented.
2. **Sparse verified indices can remove ACK boundaries.** Amendment §3.3 line 57 gives global-index arithmetic relative to origin, while §3.2 line 54 can return a sparse verified set. D5 line 64 also requires streaming only unverified members. Counting every four *sent* chunks would violate that arithmetic. The implementation bounds each batch to four, ends it at an actual cadence index when present, and uses the already-shipped per-chunk results to drain a batch with no cadence index (including a verified suffix). It never synthesizes another chunk or renumbers the plan. Tested sparse sets `[1]` and `[2]`; no new wire fields/actions.
3. **Completion member-failure literal is unspecified beyond the four values.** Amendment §3.5 line 63 says refusal naming unverified members but supplies no fifth error value. The sender resumes on the four specified values; listener should return `member_reset` for the failed-member outcome. Any other literal keeps the mandated safe session-failure behavior (§2 line 46), rather than guessing a new protocol.
4. **Correlation placement and wrapper defaults.** Amendment §2 line 46 requires actionless refusal correlation but does not repeat the TrustActionResponse shape. D5 line 64 places metadata indices in `data`; this sender uses the existing request_id wrapper, accepts errors at either response level, and preserves outer/inner success validation. Requestless chunk ACKs are matched by transfer_id. An actionless refusal without a request_id cannot safely be assigned to a specific chunk; it is not treated as an acknowledged success and the bounded wait fails safely. The listener must echo existing request_id on refusals.
5. **Metadata retry budget start is not explicitly defined.** §2 line 46 / §3.8 lines 81 and 90 prescribe metadata retry inside the completion retry budget. Here its deadline begins with the first timeout/refusal/disconnect, forward chunk progress clears that recovery episode, and the first complete begins a fresh 1800-second completion deadline retained across repairs. Repeated resets without progress are bounded. The first unanswered metadata has its independent 30-second wait before that retry episode.
6. **Registration mapping is not a second path store.** D5 line 64 says retained member set; base C stores dense canonical rows plus registration-to-published indices. The inverse mapping binds published indices back to the retained row's registration identity, then the retained relative_path is resolved under retained root_path. No current DatasetMember path is substituted. This is necessary for deletion/re-registration and version supersession not to change purchased bytes.
7. **Release runbook describes promotion, not a source version file.** `aim-data-release-process.md`, “Release version source of truth” and “GitHub Actions workflow”, reserves coordinated installer/compose defaults for promotion and injects tag VERSION into the image. The authorized pre-release bump is implemented in Settings/Dockerfile defaults; it does not invoke the release script or pretend an image exists. An explicitly supplied older environment version still wins and remains below the backend gate, as it should.
8. **Sender-only scope versus live AC-D1 precedence.** Amendment §4 AC-D1 line 97 requires live bucket validation before integrated chunk D tests. This dispatch authorizes the AIM Data sender only, with no store credentials or provider mutations. All tests here are local sender/wire stubs; no claim is made that integrated AC-D1 or backend AC-D2 latency, store durability, 21K/50K finalizer/lock scenarios, 500K-chunk listener simulation, buyer downloads or sweeps passed. They remain with the concurrent listener/integration work.

## Unmodified sender fallback and remaining gates

The shipped sender has no manifest response inbox. An actionless failed response still raises ConnectionError through the original dispatcher; a nested completion refusal fails `_confirmed_result`; an uncorrelated refusal or missing ACK reaches the shipped bounded wait. The old sender therefore fails the session rather than claiming success. The amendment's listener must retain verified objects and keep the order resumable (§2 line 46, §3.2 line 54, AC-D2 line 98). The sender cannot prove that backend retention from a local stub; the report distinguishes it from the locally tested failure behavior.

The sender implementation and its local acceptance tests are complete. Cross-repo integration must confirm the permanent-error literal, sparse-resume per-chunk results and completion-member-reset value above, then exercise real reconnect/store timing against the listener. Release/tag/publish is left to the caller, as requested. The feature flag remains default false.

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

Source paths come from the frozen root and retained canonical relative paths (the registration mapping is a bijection check only). NFC filesystem names are resolved lazily only for unverified members. Directory entries are cached per streaming pass. Component-by-component descriptor opens refuse symlinks, FIFOs and other non-regular files. Length and streamed SHA-256 must match before complete is sent. A missing local file already verified by the listener is not read on resume.

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

`settings.app_version` defaults to `1.25.0` after R1, which the existing publish builder emits as top-level `agent_version`. `Dockerfile.customer` defaults VERSION to `1.25.0` after R1; explicit release build arguments/environment overrides remain authoritative. Compose/installer stable defaults are unchanged, as the release runbook reserves their coordinated update for promotion. Caller must release/tag 1.25.0 from a D-containing commit separately; the existing 1.24.0 release does not contain D.

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
4. **Correlation placement and wrapper defaults.** Amendment §2 line 46 requires actionless refusal correlation but does not repeat the TrustActionResponse shape. D5 line 64 places metadata indices in `data`; this sender uses the existing request_id wrapper, accepts errors at either response level, and preserves outer/inner success validation. Chunk ACKs, including separate `ack-<chunk request_id>` carriers, are matched by transfer_id after R1. An actionless refusal without a request_id cannot safely be assigned to a specific chunk; it is not treated as an acknowledged success and the bounded wait fails safely. The listener must echo existing request_id on refusals.
5. **Metadata retry budget start is not explicitly defined.** §2 line 46 / §3.8 lines 81 and 90 prescribe metadata retry inside the completion retry budget. Here its deadline begins with the first timeout/refusal/disconnect, forward chunk progress clears that recovery episode, and the first complete begins a fresh 1800-second completion deadline retained across repairs. Repeated resets without progress are bounded. The first unanswered metadata has its independent 30-second wait before that retry episode.
6. **Registration mapping is not a second path store.** D5 line 64 says retained member set; base C stores dense canonical rows plus registration-to-published indices. The mapping is checked for a bijection; the frozen published row supplies relative_path under retained root_path. The inverse-map round trip does not select a separate registration source path. No current DatasetMember path is substituted. This is necessary for deletion/re-registration and version supersession not to change purchased bytes.
7. **Release runbook describes promotion, not a source version file.** `aim-data-release-process.md`, “Release version source of truth” and “GitHub Actions workflow”, reserves coordinated installer/compose defaults for promotion and injects tag VERSION into the image. The authorized pre-release bump is implemented in Settings/Dockerfile defaults; it does not invoke the release script or pretend an image exists. An explicitly supplied older environment version still wins and remains below the backend gate, as it should.
8. **Sender-only scope versus live AC-D1 precedence.** Amendment §4 AC-D1 line 97 requires live bucket validation before integrated chunk D tests. This dispatch authorizes the AIM Data sender only, with no store credentials or provider mutations. All tests here are local sender/wire stubs; no claim is made that integrated AC-D1 or backend AC-D2 latency, store durability, 21K/50K finalizer/lock scenarios, 500K-chunk listener simulation, buyer downloads or sweeps passed. They remain with the concurrent listener/integration work.

## Unmodified sender fallback and remaining gates

The shipped sender has no manifest response inbox. An actionless failed response still raises ConnectionError through the original dispatcher; a nested completion refusal fails `_confirmed_result`; an uncorrelated refusal or missing ACK reaches the shipped bounded wait. The old sender therefore fails the session rather than claiming success. The amendment's listener must retain verified objects and keep the order resumable (§2 line 46, §3.2 line 54, AC-D2 line 98). The sender cannot prove that backend retention from a local stub; the report distinguishes it from the locally tested failure behavior.

The sender implementation and its local acceptance tests are complete. Cross-repo integration must confirm the permanent-error literal, sparse-resume per-chunk results and completion-member-reset value above, then exercise real reconnect/store timing against the listener. Release/tag/publish is left to the caller, as requested. The feature flag remains default false.


## R1 fold

Scope: Gate 3 R1, AIM DATA sender half only, folded onto reviewed
`7dd40816abc19bb9e810f15635b2b0ddfd3689f8` on the existing
`build/bq-multi-file-datasets-s1717-d` branch. No branch creation, rebase,
history rewrite, PR, tag, promotion or deployment. Review input: DeepSeek and
GLM REQUEST_CHANGES; CC pending. This section supersedes the original report's
ACK-carrier, source-mapping and release-version descriptions above.

### Adopted findings

- **DeepSeek F1 HIGH:** `FulfillmentInbox` claims `data.action ==
  "vai.fulfillment.ack"` for its active transfer regardless of request ID.
  The wire peer now returns the chunk's own response first and then a separate
  `TrustActionResponse` with `request_id = f"ack-{chunk_request_id}"`,
  `success: true`, `error: null`, and the ACK in `data`. The three-member golden
  reaches `complete` and `_update_log(..., "completed")` using that carrier.
  A direct inbox regression proves it still claims the ACK after the chunk's
  request ID has been removed from pending. Existing `_validate_ack` checks
  remain in use; transport framing and the legacy sender are unchanged.
- **GLM 1 HIGH / DeepSeek F2 MEDIUM:** Settings and customer Dockerfile default
  to **1.25.0**, and version-pinning publish/sender tests are updated. Commit
  `cc2af18` contains the version fold. `aim-data-v1.24.0` cannot satisfy the
  sender gate because that release does not include D.
- **GLM 2 MEDIUM:** Streaming explicitly assigns the resolved actual entry to
  `resolved_path` and opens it with the existing no-follow descriptor walk.
  Inspection of `7dd40816` found that `source_path` already returned the actual
  readdir entry and passed it directly to `open_member`; the reported NFC
  reopening was not reproduced. The added regression stores NFD directory and
  file names, emulates NFD readdir on normalizing filesystems, and rejects an
  attempted NFC component open. It proves the actual NFD components are opened,
  payload bytes match, and completion is logged. The explicit variable makes
  the resolved-entry contract visible without weakening symlink protection.
- **DeepSeek F3 LOW:** ACKs below the current window's expected index are logged
  and ignored. They do not acknowledge the current window or trigger
  `TRANSFER_ABORTED`. Unknown-transfer correlated ACKs and ACKs beyond the
  planned chunk range are refused; malformed indices are also refused.
  Tests cover duplicate prior-window ACKs and both invalid authority cases.
- **DeepSeek F4 LOW:** Added `load_plan` refusals for a member above
  `transfer_max_member_bytes`, a data set above `dataset_max_bytes`, a count
  above `dataset_max_members`, and non-dense retained published indices.
- **DeepSeek F6 NIT:** Published rows carry the frozen `relative_path`; source
  paths are that path under retained `root_path`. The registration-to-published
  mapping is a **bijection check only**, not an independent registration-index
  source-path lookup. The current inverse-map round trip returns the same
  published row. A test permutes registration identities and proves paths stay
  tied to frozen published rows, then rejects a non-bijective mapping. No live
  registration lookup is introduced. Commit `26e97dd` contains the sender/test
  fold.

### Recorded dispositions

- **DeepSeek F5:** `MANIFEST_NOT_FOUND` is a new device-to-cloud `error_code`
  **value**, not a field. Per the caller's disposition, the listener already
  treats it as permanent; the caller must record the value in §3a/the amendment.
  This sender fold does not edit the cross-repository amendment.
- **GLM 3:** The four inherited config-alias failures were reproduced on both
  the original chunk D base and this candidate. Each fails on the pre-existing
  string alias `AIM_DATA_OAUTH_ENABLED` where `AliasChoices` is required:
  - `tests/test_config_alias_choices.py::test_every_settings_field_has_primary_and_legacy_aliases`
  - `tests/test_config_alias_choices.py::test_every_settings_field_resolves_from_primary_and_legacy_prefixes[AIM_DATA]`
  - `tests/test_config_alias_choices.py::test_every_settings_field_resolves_from_primary_and_legacy_prefixes[VECTORAIZ]`
  - `tests/test_config_alias_choices.py::test_aim_data_prefix_wins_when_both_set`

### Release step still required

The current `aim-data-release-process.md` was read locally on 2026-09-17. It
prescribes no additional pre-promotion version file: Compose and both installer
defaults are changed together by `scripts/release-aim-data.sh` at stable
promotion, and CI derives image VERSION from the release tag. Those defaults
remain untouched in this fold.

The caller still needs to integrate a D-containing commit, cut and test the
**1.25.0 RC**, then promote **aim-data-v1.25.0** from a D-containing commit.
Before promotion, fetch and check changes since the RC as the runbook requires;
recut the RC or record the decision if additional commits ship. Promotion must
update the three customer defaults and push the release commit and tag
atomically, followed by successful multi-arch build, published version-label
proof, smoke test and GitHub release. The concurrent backend gate must be
`MIN_AGENT_VERSION_LOCAL = 1.25.0`. None of those release or backend actions was
performed here.

### R1 validation

Python: `/Users/max/Projects/ai-market/aim-data/.venv/bin/python` (3.12.12).
Focused command is the original ten-module command above, with output
`--junitxml=/tmp/s1717-d-r1-focused.xml`: **330 passed, 15 warnings**.
The sender module has **71 passing tests**, ten more than the reviewed head.
Separate config runs: **4 failed on each**, with matching node IDs above;
receipts `/tmp/s1717-d-r1-config-{base,candidate}.xml`.


Full-suite comparison used the original chunk D base
`7dc92619c2cecce4c7492d1eed823fcaf9ee4328`, matching the original report's
baseline, and functional candidate `26e97dd2396c8b9f989bd5029746754ffb7dcc4c`.
Both ran the same command (only receipt filename differed):

```sh
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q --disable-warnings --junitxml=/tmp/s1717-d-r1-candidate.xml
```

Base cwd: `/private/tmp/s1717-d-aimdata-sender-base-51907d`.
Candidate cwd: `/private/var/tmp/koskadeux/minimal-bridge-worktrees/63460bc8e9af-51907d`.

| Run | Passed | Failed | Errors | Skipped | Warnings |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original D base | 3078 | 97 | 38 | 34 | 820 |
| R1 candidate | 3149 | 97 | 38 | 34 | 820 |

The exact `(test ID, failure/error outcome)` sets are identical: **no new
failing cases**, and no base failures disappeared. All 71 test cases added since
the original D base pass (61 initial sender cases plus 10 R1 cases). This is
parity with an already-failing full suite, not a claim of a green full suite.
`git diff 7dd40816 --check` and `git diff --check` passed.

Raw logs and XML: `/tmp/s1717-d-r1-{base,candidate}.{log,xml}`. Parsed test-ID
comparison: `/tmp/s1717-d-r1-suite-parity.json`.
Receipt SHA-256 values:

- Base XML: `a0f3bf9a1ca9e4e02d4dd80281ff428ad708d7adb737994c484e255c793ea5bc`
- Candidate XML: `31881f638c456be94c733785543bf5220fd0a2df9091b1689c3614e52e5e9877`
- Parsed comparison: `cc0d76b356f971f5af5b16f0c7642dac3ab383334f9f815e12e4b8c5d31ddd5c`

Only the requested sender/version/test fold and this report changed. Local
wire-peer proof does not replace the concurrent listener/store integration or
release gates described above.

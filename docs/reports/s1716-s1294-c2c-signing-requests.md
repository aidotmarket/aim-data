# S1716 / S1294 Chunk 2c — signing, closed requests and local lifecycle

This branch implements producer-local signing/request construction and recovery. It does not merge, release, allocate live platform candidates, submit the new P1 arm, obtain real seller registration evidence, or claim T backend/browser acceptance. Source baseline: AIM Data `97da3f096787b6010ce914803fdd30978a9d5bfd`; backend contract/schema source: `origin/main` `9b6f8c1d590116ffe2792696937a63821336e00c`. User authority supplies v2, non-custodial, live-on-merge semantics and the I.b submission boundary.

## Delivered files

- `app/services/preview_signing_service.py`: one closed RFC8785/safe-integer serializer, existing encrypted Ed25519 identity, per-signature owner/key/status/observation checks, five F2 preimages, platform/checkpoint/log fixture verification, immutable local candidate, request construction/digest, whole-manifest budget and explicit I.b unavailable result.
- `app/models/dataset_commitment_schemas.py`: unchanged 2a local models plus closed commitment/proof/checkpoint/log-evidence wire fields. Chunk T admission adds its stricter identifiers, fingerprints and scan bounds to the backend field layout.
- `app/models/preview_disclosure_schemas.py`: exact B/request and withdrawal/refresh/supersession models, rights/scan/P1/schema/proof agreement and attestation checks. Seller-origin rows have no wire carrier. Errors at HTTP/signing boundaries are fixed codes.
- `app/services/registration_service.py`: bounded local owner-bound evidence readback, existing rotate/revoke endpoint wrappers, staged encrypted Ed25519 replacement preserving the X25519/platform fields, pending-state signing block, explicit confirmed-evidence activation. No new registration endpoint or credential store.
- `app/services/preview_lifecycle.py`: explicit rights digest, immutable candidate/request journal transactions, retry conflicts, fixture head/replay expectations, newly signed withdrawal inputs, refresh/supersession and stale/expiry rules. Retirement invokes the actual 2b `PublicationStore.retire`, binds the exact object URL, and requires GET + OPTIONS receipts before success.
- `app/routers/marketplace_publish.py`, `app/services/marketplace_push_service.py`, `app/models/listing_metadata_schemas.py`: remove `DisclosureApprovedSample`, refuse historical row replay, retain legacy none, separate local column values, route closed preview requests to `preview_integration_not_yet_available`. No feature flag or live preview POST; no 409-to-PATCH fallback.
- Tests: `test_preview_signing_service.py`, `test_preview_disclosure_schemas.py`, `test_preview_lifecycle.py`, `test_marketplace_commitment_push.py`, `preview_fixture_factory.py`; adapt `test_vz_publish_proxy.py` from row transmission to preserved none audit. New tests retain direct and HTTP refusal coverage for old row payloads, including non-reflecting errors.

## Cross-repository fixtures and exact bytes

Both JSON files are metadata-only. Test private keys are constructed from clearly synthetic sequences inside test code, never read from a customer keystore or committed as private-key files. Public keys, signatures and exact preimages are intentionally public fixture material. `aim_preview_signing_requests_v1.sha256` pins the files.

| File | SHA-256 |
|---|---|
| `tests/fixtures/aim_preview_signing_v1.json` | `19674653e7fd353d5141189310b7a274be1ff48b4704e3598eef53bf34cf72d2` |
| `tests/fixtures/aim_preview_requests_v1.json` | `164eced90fc11df44466b45df290c5f80e7fefc81044c9dfd45bb8a36e3cbe91` |

Each signature fixture contains `signed_bytes_hex`, `signed_bytes_sha256`, raw-public-key base64url and signature base64url. Hex decodes directly to the bytes signed, including each NUL/newline; it is not itself the signed string. Python verifies all ten entries. A separate Node `crypto` test independently reconstructs JCS and all five F2 preimages from the request/evidence fixtures and verifies Ed25519 signatures.

`J` is RFC8785 over the admitted 2a subset: no floats or unsafe integers, UTF-16 object-key ordering, canonical lowercase UUIDs, six-fraction UTC timestamps, included nulls/defaults retained. Ed25519 signatures are 64 bytes / 86 unpadded base64url characters; public keys are 32 bytes / 43 characters.

| Signature | Exact signed bytes |
|---|---|
| Proof | UTF-8 `aim-preview-proof-signature-v1` + one NUL + `J({commitment_id,listing_id,seller_dataset_version,schema_digest,dataset_merkle_root,proof})`; `proof` removes **only** `signature`. Every scan/signer/package/path field remains. |
| Commitment | UTF-8 `aim-dataset-commitment-signature-v1` + one NUL + `J(DatasetCommitmentContract minus seller_signature)`; ordered proofs retain their signatures. |
| Approve / none / withdraw / refresh / supersede | UTF-8 `aim-preview-disclosure-signature-v1` + one NUL + `J(B)`; every B field is included. No `signer_keys`, platform envelope/signature, or derived freshness expiry in B. |
| Platform envelope | UTF-8 `aim-preview-platform-envelope-v1` + one NUL + `J({profile,key_id,signature_algorithm,binding,seller_signature,signer_keys})`; only own signature is excluded. The T-authorized optional open-ended `valid_until` is omitted; all request/B nulls remain. Verification-only in application code. |
| Checkpoint | UTF-8 `aim-transparency-checkpoint-v1` + LF + log ID + LF + decimal tree size + LF + unpadded-base64url root + LF + canonical UTC timestamp + LF. No JCS. `key_id` and `public_key_algorithm` are independently checked, not falsely described as signed. |

The seller-attestation digest additionally uses plan E's `aim-dataset-seller-attestation-v1` + NUL + the closed listing/version/schema/root/count/sample/rights/permission/accuracy/time object. Rights hashing preserves whitespace after NFC. Ordered signed-proof scan hashing reuses 2b unchanged.

## Requests, retries and lifecycle

The complete examples are the `approve`, `none`, `withdraw`, `refresh` and `supersede` members of [aim_preview_requests_v1.json](../../tests/fixtures/aim_preview_requests_v1.json). Each is exactly `{profile,summary_id,binding,seller_signature,commitment,proofs}`. [Request metadata](s1716-c2c-evidence/request-metadata.json) records its full-request digest, exact byte count, conservative whole-manifest budget and intended endpoint. These are intended paths, not executed network calls.

The binding has exactly the plan G field set. The constructor checks the independent approved-P1 reference projection, all schema/sample/proof/scan/rights agreements, existing commitment/proof signatures and signer authority before signing B. It budgets duplicated commitment/proofs/descriptors and platform/log/checkpoint evidence; it never trims an approved selection.

A fixture allocation retains both IDs for identical facts; changed binding facts obtain new IDs. `freeze` atomically stores the exact candidate, request bytes and full-request SHA-256 under seller/listing/request ID. An identical retry returns stored bytes/digest; changing content under the same ID fails. Fixture replay tables separately model identical-result retry, 409 body reuse, stale-head 409, withdrawal and superseded-head rejection. They never label a fixture submitted or provide a platform verification result.

Withdrawal creates a new immutable `decision=withdraw` candidate, binds both expected-current and supersedes to the revoked head, clears sample grants, and receives a new disclosure signature. Refresh preserves the root/sample/rights/selection/commitment identities and signs a new time/cadence/event linked to the prior head. Supersession explicitly replaces the prior head. Historical package bytes are never edited by these request constructors; any package publication for a new disclosure identity must be prepared using the 2b builder before integration.

The journal represents building, selected, scanned, hosted, ready_to_sign, signed_candidate, submitted, invalidated, submission_unknown, withdrawal_pending, retirement_pending and retired. Fixture candidates cannot reach submitted or submission_unknown. Retirement transitions pending first, tombstones/removes the exact 2b object, and remains pending if receipts are missing. A saved copy cannot make a retired origin or superseded fixture head current again. Receipt shape is exactly C/T I.a.1, including six allowlisted headers with explicit nulls, no cookies/body, GET 404/410, successful OPTIONS and the 8,192-byte bound.

Staleness is labelled at `>= attested_at + min(90d,max(7d,2*cadence_days))`, or +90 days for null cadence. Staleness alone remains eligible. Independent policy/approval/summary expiry can remove time eligibility; no new hard-expiry interval is invented.

## Validation and mutation witnesses

Tested implementation commit: `f8d2229` (all application/test changes; report-only commit follows). Python 3.12.12, pytest 7.4.4, Pydantic 2.13.4, macOS 27 arm64; shared installed AIM Data test environment. Both complete suites ran with `--continue-on-collection-errors`, no deselection and a per-test outcome recorder.

| Run | Passed | Failed | Errors | Skipped |
|---|---:|---:|---:|---:|
| Full origin/main baseline | 2,801 | 96 | 38 | 34 |
| Full branch | 2,885 | 96 | 38 | 34 |
| Affected baseline | 539 | 0 | 0 | 1 |
| Affected branch | 623 | 0 | 0 | 1 |

**Zero branch-only failures, zero missing baseline test IDs, zero additional skips.** Full-run elapsed times: baseline 161.87s; final branch 157.24s. [Parity](s1716-c2c-evidence/parity.json), [baseline node IDs](s1716-c2c-evidence/baseline-nodeids.json), [branch node IDs](s1716-c2c-evidence/branch-nodeids.json) and both affected-suite node-ID files retain every observed outcome. The per-node recorder combines failures and setup errors as `failed`; the table above preserves pytest's separate counts.

The 134 baseline failure/error identities persist exactly. [Baseline reasons](s1716-c2c-evidence/baseline-failures.json) records each one: 40 read-only `/data` errors, 38 missing local app-server setup failures, 13 absent entitlement-signing configuration failures, seven metering call-signature mismatches, four missing `llm_key_crypto` module failures, plus existing frontend/source-contract, readiness, metadata, upload, request-engine and other regression assertions. No baseline failure was fixed, skipped or disguised to obtain parity. These are not green full suites.

Ruff passed over all affected application/test Python files. Mypy 1.20.2 passed the four new/extended signing, lifecycle and wire-model modules with `--follow-imports=silent --ignore-missing-imports --check-untyped-defs`; this is a scoped type check, not a repository-wide type-clean claim. `git diff --check` passed. Ten Python/Node F2 goldens passed; 758 protected preimage mutations were rejected; **53 distinct test IDs were actually observed failing** under the ten code mutations.

Commands and isolation:

- Cwd baseline `/tmp/s1716-c2c-baseline`, detached at the exact baseline SHA; branch cwd `/var/tmp/koskadeux/minimal-bridge-worktrees/b81bcd9475b9-38a004`.
- Full suites: `rtk proxy env PYTHONPATH=/tmp:. C2C_RESULTS=<per-run-nodeids.json> /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest tests -p s1716_c2c_results --continue-on-collection-errors -q --tb=short --junitxml=<per-run.xml>`; stdout/stderr captured locally. [Recorder source](s1716-c2c-evidence/pytest_results_plugin.py.txt) makes the outcome files and blocks non-loopback sockets.
- Affected suites: preview signing/disclosure/lifecycle/commitment push (branch only), 2b content policy/package/origin, VZ proxy, marketplace push, dataset signed proxy, OAuth call inventory, device/trust registration and S1681 serial registration. Same recorder and interpreter; both runs completed.
- Mutation replay: `rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/python /tmp/s1716_c2c_mutations.py`; [runner source](s1716-c2c-evidence/mutation_runner.py.txt) preserves/restores each changed source and records failing test IDs. Do not run it concurrently with a suite against the same checkout.

Milestones pushed without merge: signing `7a9b89b`; closed requests `b8497e9`; lifecycle/journal `6f93241`; tests/fixtures/hardening `f8d2229`.


The protected-field loop visits every included field, nested scalar, omission and nonempty ordering in each JCS preimage, plus all four protected checkpoint fields: **758 mutated preimages fail signature verification**. Wrong keys fail too. The two explicitly requested code mutations are observed failures: changing the proof domain fails the golden test; incorrectly excluding commitment `signed_at` fails golden/signature evidence tests. Checkpoint excluded fields are tested independently through trusted-ID/algorithm validation.

Ten semantic mutation runs also disable registration identity/fingerprint/status/time checks, closed request validation, fixture submission refusal, the stale equality boundary and origin retirement. [Mutation results](s1716-c2c-evidence/mutation-results.json) records the exact failing test IDs. Only tests actually observed failing in those runs are counted as mutation witnesses; ordinary green regressions are not described as mutation witnesses. This is a bounded mutation campaign, not a claim that every possible verification bypass was killed.

No production service was called. Tests use mocked registration/rotation/revocation; a test plugin blocks outbound non-loopback sockets. Existing local HTTP fixture tests remain local. No real owner-bound registration, seller machine, provider, customer dataset, release or T endpoint evidence is claimed.

## Plan / T differences and decisions

1. **F2 authority:** no change to T's signature domains/exclusions. Checkpoint remains its historical newline preimage, and `signer_keys` is confined to the platform envelope. The plan's table agrees with T on these points.
2. **Request layout/action seam:** T's conceptual union does not ship a concrete request schema. Implement the user's specified plan-G outer layout and signed `decision=approve|withdraw`, signed `summary_id`, `supersedes`, and `disclosure_version` without an `id` alias. These remain producer-local fixture contracts for T ratification; no live-acceptance claim.
3. **Integers/JCS:** backend storage permits signed-63-bit values; preserve 2a's mandatory safe-integer admission instead. No silent backend-byte change. T's stricter code/fingerprint/scan/public-host rules apply over the mirrored field layout. Old v1 is representable in the mirrored audit contracts but cannot authorize new requests.
4. **P1 types:** backend `ListingSummaryRecord/Decision` and `SummaryApprovalRequest` establish UUID content/approval/actor IDs and 64-hex source revision. Use those concrete types instead of interpreting the plan's generic legacy-ID prose as permission for arbitrary revision strings.
5. **Attestation seams:** use the plan's explicitly proposed local seller-attestation preimage; reuse 2b's sampled-leaf and scan digest definitions. No separate unsigned scan claim or allAI rights inference.
6. **Registration evidence:** existing register/rotate responses supply install IDs, not the complete required owner/key/status readback. A cached install ID or HTTP success is insufficient. Accept only separately supplied closed evidence; its maximum observation age is explicit caller policy, not an invented cross-repo freshness interval. Real evidence remains a producer acceptance prerequisite outside this mocked build.
7. **Open-ended signer validity:** T expressly permits omission of optional `valid_until`; that sole platform-key exception is retained. B/request nulls are never omitted.
8. **Post-T authority:** no newly submitted arm, feature flag, platform signing capability, provider mutation, merge or release. T's live-on-merge policy remains unchanged; installed producer delivery and I.b integration are separate gates.

## Local operating notes / runbook correction

Read `aim-data-seller-publish-journey.md` before implementation. Its existing install registration and metadata-publish path remain the authority. For this branch, its historical row-snapshot/retry description must not be used: `approved_rows` and row-bearing replay are unavailable, existing local audit material is retained, and a new preview request returns `preview_integration_not_yet_available`. These scoped notes describe branch behavior, not a deployed runbook refresh.

Load the existing `/data/keystore.json` through `DeviceCrypto` with its passphrase; never use `get_or_create_keypairs` from preview signing. Missing/corrupt identity, missing evidence, changed owner/fingerprint, inactive status, stale/future observation or pending key operation blocks signing. Keep keystore mode 0600 and its parent non-writable by others. Do not print keys, passphrases, bearer tokens or upstream error bodies.

On rotation, stage the encrypted Ed25519 replacement, call only the existing owner-authenticated rotate endpoint, and keep signing blocked until independent readback confirms the new key. On uncertainty preserve the staged identity and reconcile; never alternate keys. Revocation blocks further local signing even when its network outcome is uncertain. In this chunk these endpoints are exercised only through mocks.

For retirement, preserve the source dataset/entitlements; act on the exact preview object and retain pending status until both no-body receipts validate. Local fixture signing, hosted-object removal, platform withdrawal acknowledgement and installed-customer release are separate facts.

## R2 fold

Review source: DeepSeek `response-20260917-121500-938722`, CC `response-20260917-121529-807322`, GLM `response-20260917-121517-622711`; original reviewed candidate `0a409a7329d82e14d0281d3b169bf3af57c4eaef`.

DS-F4 corrected review extent: `app/models/dataset_commitment_schemas.py` contains 339 lines; 233 was the diff insertion count, not its full length.

DS-F2 release dependency: Chunk 2d must stop producing the legacy `approved_rows` request before this producer change ships, with an end-to-end seller click-through proving the replacement flow; no UI implementation or release is claimed by this fold.

CC-NIT-1 I.b precondition: enforce URL verb versus signed `binding.decision` agreement before live signing/submission and test approve/withdraw mismatches; both current routes remain unavailable without state change or egress.

### Findings and commits

| Finding | Result | Commit |
|---|---|---|
| G1 | Added independent Python/Node Unicode and numeric differential corpus; original fixtures unchanged | `1cea7bd3d899cd62fa6e288071699f67ab475e54` |
| G2 | Validate object identity/path, exact receipt URL and persisted browser origin before returning a retired receipt; migrate old journals without inventing origin | `7903abe8f833d007f5aa7c42c17991905ab64a2e` |
| G3 / DS-F3 | Real marketplace payload uses the closed four-field column projection; test captures actual mocked HTTP JSON and excludes local marker/rows | `de1fc46efef1f6eb1ac579b846349bd6de4b4c5f` |
| DS-F4 | Corrected review extent, 339 lines | `c79e5dac256c27b4afab510a993554197ca28a3f` |
| DS-F2 | Recorded mandatory UI release dependency; implementation remains Chunk 2d | `81f49b6a3ca022efbed4845dd80e481ddb364017` |
| CC-NIT-1 | Recorded I.b verb/decision validation precondition; live implementation carried | `e2e79a28e76d8b436b843680f875095476db12cc` |

Carried, not silently closed:

- **DS-F1:** finite `valid_until` and rotated/revoked envelope golden variants, plus explicit T ratification of null-to-omitted open-ended validity. Existing omission rule and fixtures are unchanged; this is larger than a one-line fold.
- **DS-F2 / CC-NIT-1 implementation:** UI replacement/click-through and live route/decision agreement remain the documented 2d/I.b dependencies above.
- **CC-NIT-2:** reconcile the conservative 2b manifest fixture's field names when the real manifest lands, and test its real B-shaped limit. No fixture shape is changed here.
- **CC-NIT-3:** expose a public duplicate-rejecting parser if the legacy `_pairs` usage spreads; no unrelated parser refactor in this fold.
- All T ratification and real owner-bound registration/backend/browser acceptance prerequisites remain open. These local tests do not admit release or live submission.

### Differential corpus

New file: [aim_preview_differential_v1.json](../../tests/fixtures/aim_preview_differential_v1.json), independently pinned by [its SHA manifest](../../tests/fixtures/aim_preview_differential_v1.sha256).

SHA-256: `a6684a718f57d841af5e591de20b1f58fa6716d0cfbeea2955b477569c729e57`.

Eight valid vectors each contain exact `signed_bytes_hex`, `signed_bytes_sha256`, public key and Ed25519 signature under the fixed synthetic `bytes(range(32))` test key. Python regenerates and compares the complete corpus, reconstructs each preimage through the production serializer/signing projection, and verifies every signature. Node independently reconstructs with `JSON.stringify`, UTF-16 key sorting and the same explicit 2a admission guards; it compares bytes and digests and verifies every signature. Any mismatch fails the test as a blocker. No mismatch was found.

Unicode disclosure/envelope vectors exercise admitted NFC field references and complete descriptors: accented BMP, Japanese, supplementary-plane names, quotes, backslash, controls (NUL, backspace, tab, newline, form feed, carriage return, U+001F) and U+2028/U+2029. The signed cadence also exercises `2^53-1`. Generic `jcs` entries isolate primitive admission and are **not** new legal wire fields or complete approved requests: they pin Unicode object-key/string handling and 0, -1, ±(2^53-1). Closed metadata object keys remain the existing vocabulary; user field names are descriptor/selection array values.

Six explicit `must_reject` vectors cover ±2^53 (`unsafe_integer`), positive/negative fractional floats (`invalid_metadata`), and both top-level and nested supplementary-plane/BMP key-order conflicts (`noncanonical_key_order`). Node compares code-point order against UTF-16 order and reproduces the 2a refusal; it does not silently sort a producer-refused object into acceptance. This refusal is narrower than general RFC8785 and preserves existing 2a bytes. All seven original preview fixture/pin files remain byte-identical, recorded in [unchanged-fixtures.json](s1716-c2c-r2-evidence/unchanged-fixtures.json). The original ten F2 goldens still independently verify all five signature shapes.

The independently computed preimage digests are retained in [differential.json](s1716-c2c-r2-evidence/differential.json):

| Vector | Python SHA-256 | Node SHA-256 |
|---|---|---|
| `unicode-disclosure` | `f883a11ccb2171e1888f5706411d5488a7993bd901cfd8ec9aa162063dc2cb7b` | `f883a11ccb2171e1888f5706411d5488a7993bd901cfd8ec9aa162063dc2cb7b` |
| `unicode-envelope` | `4781c9417585dbd3c2bb83b53a921ad7bafa726c2b2c96aa8d445e0bbd98f314` | `4781c9417585dbd3c2bb83b53a921ad7bafa726c2b2c96aa8d445e0bbd98f314` |
| `unicode-object-keys` | `5426657a2757aa128f8b8eed3996240ebce675e02efc76f8af195fcd7ee02904` | `5426657a2757aa128f8b8eed3996240ebce675e02efc76f8af195fcd7ee02904` |
| `supplementary-order-admitted` | `394fd237fffd944fa0427d6af9c4cdff149ab5e61fc0595fcf3c51ff1a12ee02` | `394fd237fffd944fa0427d6af9c4cdff149ab5e61fc0595fcf3c51ff1a12ee02` |
| `integer-0` | `23d7b286bd429460b92a2a1c21b6afc34110446c5034c17363fda363aa0a7c5d` | `23d7b286bd429460b92a2a1c21b6afc34110446c5034c17363fda363aa0a7c5d` |
| `integer--1` | `f32d4354160a457984a54414824b84c196a5ddf65d6ee6c992317a5fb7a53b0c` | `f32d4354160a457984a54414824b84c196a5ddf65d6ee6c992317a5fb7a53b0c` |
| `integer-9007199254740991` | `16c63bddb910905c4ee8abf36273baa20dba53020dbaf265da5936013ded185d` | `16c63bddb910905c4ee8abf36273baa20dba53020dbaf265da5936013ded185d` |
| `integer--9007199254740991` | `4fcd1f34a0ba00a639b9a6a805c6952838e7f403c126290440bc71e64d01693a` | `4fcd1f34a0ba00a639b9a6a805c6952838e7f403c126290440bc71e64d01693a` |

### R2 validation

Final tested code/docs tip before this evidence-only commit: `e2e79a28e76d8b436b843680f875095476db12cc`.

| Run | Passed | Failed / errors | Skipped |
|---|---:|---:|---:|
| Four 2c suites | 87 | 0 | 0 |
| Affected baseline at `97da3f096787b6010ce914803fdd30978a9d5bfd` | 758 | 0 | 1 |
| Affected R2 candidate | 845 | 0 | 1 |

Affected coverage repeats the prior ten baseline/four new suite set and adds `test_dataset_merkle_service.py` and `test_dataset_canonicalization.py` for G1's underlying admission/serialization boundary. **Zero missing baseline test IDs, zero branch-only failures, zero additional skips.** Per-node outcomes, exact commands/cwds, complete logs, parity comparison and the non-loopback-blocking recorder are retained under [s1716-c2c-r2-evidence](s1716-c2c-r2-evidence/parity.json), separately SHA-pinned. The prior full-suite evidence above is historical R1 evidence, not a new R2 full-suite run.

Ruff and mypy 1.20.2 pass all seven changed Python modules (application and tests); mypy uses the AIM Data interpreter for installed package discovery and `--follow-imports=silent --ignore-missing-imports --check-untyped-defs`. Exact argv/results are in [static-checks.json](s1716-c2c-r2-evidence/static-checks.json). No type ignores or test skips were added. `git diff --check` passes.

G2 tests retain correct retry idempotency and reject changed path, host, scheme, query, fragment, origin and sample hash without new retirement calls. Both exact-origin and wildcard-CORS receipts are tested. Existing SQLite journals add a nullable origin column without altering saved candidate/request bytes; an already-retired legacy entry with no origin fails closed and requires operator reconciliation. The local publication runbook records this behavior. G3 drives `push_to_marketplace` through `_build_payload` and the real retry method to a mocked HTTP POST, asserts the exact safe column projection and absence of the local marker in serialized outbound JSON, and confirms local values are preserved.

Executed commands (all shell invocations prefixed with `rtk`):

```sh
rtk proxy python /tmp/s1716-c2c-r2/run.py /private/tmp/s1716-c2c-baseline baseline
rtk proxy python /tmp/s1716-c2c-r2/run.py /private/var/tmp/koskadeux/minimal-bridge-worktrees/b81bcd9475b9-38a004 branch
rtk proxy python /tmp/s1716-c2c-r2/run.py /private/var/tmp/koskadeux/minimal-bridge-worktrees/b81bcd9475b9-38a004 four
rtk proxy node tests/preview_differential_check.cjs
```

The retained [runner source](s1716-c2c-r2-evidence/run.py.txt) expands the exact pytest arguments and offline recorder configuration; each command JSON supplies the fully expanded suite list. No merge, production request, provider write, release or new-arm submission was performed.

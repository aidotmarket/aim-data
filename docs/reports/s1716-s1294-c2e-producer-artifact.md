# S1716 / S1294 Chunk 2e — final producer artifact

Branch: `build/bq-listing-enrichment-seller-tools-s1294-c2e-producer-artifact-s1716`.
Base: `896b6157854a237e6497c0fbd6387244d845b2f4`, the requested `origin/main`
containing 2a–2d. Milestones are committed and pushed. No merge, Git tag, GitHub
release, installer default change or real seller-data processing is authorized by
this chunk. The controller releases after merge.

The producer tooling, fixture guard, CI and documentation are implemented. Local
full-suite comparison has **zero branch-only failures, missing baseline tests or
additional skips**. This is not a clean repository-wide pass. The customer ARM64
development image builds, starts healthy, and returns 401 for the unauthenticated
preview-job route. Real owner-bound registry provenance, HTTPS hosting and
Sergey's machine execution remain Gate-4 work. Option A's complete I.a acceptance
and both T dispatches are not declared ready by synthetic evidence.

## Authority and scope

Read the build plan §I row 2e, test matrix, release mechanics and Gate-4 timing,
and backend T §I.a 1–15. Fetched backend/runbook object identities and file hashes
are in [authority.json](s1716-c2e-evidence/authority.json). The named external
`aim-data-seller-publication*.md` pattern did not exist; the available authoritative
seller journey is `aim-data-seller-publish-journey.md`. The in-repository
[publication procedure](../runbooks/preview-publication.md) is updated, and the
[producer guide](../commitment-preview-producer.md) corrects historical row-snapshot
expectations for this producer. Non-custodial, joint v2 caps, live on merge and
Option A are retained. No feature flag or alternate publication endpoint is added.

## Milestones and release-blocking discovery

| Milestone | Commit / artifact |
|---|---|
| Fixture guard and 14-file manifest | `e2f48b5` |
| Correct customer preview route mount | `98d7d0d` |
| Evidence builder, synthetic checksum reference and CI | `8a486cb` |
| Hosted-CI portability correction | `e030a87` |
| Producer/release documentation | `30c51bb` |
| Bound Linux spawned-worker imports | `5d5d4c0` |
| Fixture purpose/profile clarification and publication regression CI | `448f087` |
| Customer CPU dependency index in CI | `36df6cc` |
| Final verification/report | This evidence milestone |

The actual customer-image probe found `/api/marketplace/preview-builds` returned
404: 2d included a `/preview-builds` subrouter under a parent with no marketplace
prefix, while its test parent supplied that missing prefix. Added the prefix to
the actual include, corrected the test mount, and added an auth-enabled
production-shaped `/api` + admin-wrapper regression. The unauthenticated route
now returns 401; the existing owner/dataset isolation cases remain passing.
No authentication policy was changed.

## §I.a evidence coverage

Artifact names below refer to files generated **on the seller machine** by
`scripts/build_preview_producer_evidence.py`. Real bundles are never committed.
The synthetic reference commits only per-artifact SHAs and the retired package
SHA in [preview-producer-synthetic-shas.json](../../tests/fixtures/preview-producer-synthetic-shas.json).
Every candidate and expected platform record is explicitly **NON-RUNTIME**.

| T item | Evidence artifact / implementation | Executable coverage and outstanding proof |
|---|---|---|
| 1 Hosting/transport | `publication.json`, `hosting-receipts.json`; real verifier's pinned public DNS/TLS + GET/OPTIONS; six-key receipts | `test_preview_origin_service.py`, synthetic real loopback origin. Real public HTTPS/domain/CORS remains open. |
| 2 Exact v2 envelope | `public/previews/<disclosure>/<sample>.json`, publication SHA; real `CommitmentPreviewBuilder` | `test_byte_exact_golden`, `test_envelope_mutations`; synthetic package bytes checked through HTTP before retirement. |
| 3 Joint caps | `build-receipt.json` records counts/canonical bytes and conservative complete signed manifest budget | Package cap−1/cap/cap+1 tests, `test_manifest_budget_counts_duplicated_signed_evidence`; no trimming or raised caps. |
| 4 Sample hash | Package and approval binding, ordered proof IDs/base digests/ordinals/indices | `test_sample_hash_vectors`, golden/order mutations; real builder computes the synthetic bundle hash. |
| 5 Local scan/attestation | Signed `proofs.json`, `seller-attestation.json`, binding scan digest | Strict real pinned PII engine in CI driver; content-policy/package suites. No notes or cells in metadata outputs. |
| 6 Rights/permission | Signed binding rights digest/code and explicit permission; private rights file not copied | `test_rights_are_local`, consent refusal, closed seller-attestation tests; real seller consent remains open. |
| 7 F2 signatures/candidate | `approval-candidate.json`, `commitment.json`, `proofs.json`, `signing-preimages.json`, expected platform fixtures | Existing install signer and independent verification; `test_golden_corpus`, protected-field mutation suite, independent Node F2 + differential checks. Fixture platform key is public test material and never runtime trust. |
| 8 Registration/rotation | `registration-readback.json`; fresh closed reader and signer key/owner/status matching before each signature | Registration failure/rotation/key-resolution tests. Synthetic owner readback is labelled; real owner-authorized provenance remains open. Tool does not fabricate a registry endpoint. |
| 9 P1/idempotency | Closed local approval/withdraw/refresh/supersede requests; `lifecycle-results.json` identical retry, body conflict and stale-head 409 | `construct_request`, `PreviewJournal.apply_fixture`, atomic-retry and head-replay tests. Live allocation/submission stays I.b. |
| 10 Complete metadata | Full signed commitment/ordered proofs/descriptors; `expected-platform-records.json` has matching fixture-signed envelope/checkpoint/log; total manifest budget | Full request validation, crypto verification, inclusion/checkpoint checks, duplicate-evidence budget tests. Expected platform objects use an explicitly synthetic test key. No platform verification claim. |
| 11 V2/invalidation | v2-only builder and closed arm; immutable source SHA and parser-options digest | Envelope profile mutations, source-mutation tests, legacy row refusal and UI invalidation tests. No v1 or none reinterpretation. |
| 12 Zero row ingress | Marker stays in local package/HTTP transport only; metadata, registration and manifest outputs exclude it, source path and rights prose | Synthetic driver's marker assertions, closed carrier tests and actual mocked producer HTTP egress tests. Platform storage/log/cache/SSR proof remains I.b/Chunk 5. |
| 13 Complete descriptors | `schema-descriptors.json` plus signed binding/digest; all fields, not display projection | Full canonicalization suite: real formats, decimal/null/missing/nesting, descriptor dictionary/depth/node limits and unsupported types. |
| 14 Withdrawal/retirement | Newly signed `withdraw-candidate.json`; real `PublicationStore.retire`; `retirement-receipts.json` | New signature, tombstone, 410 + OPTIONS, saved-package/current-head refusal and retirement URL/origin tests. Real external deletion/HTTPS receipts remain open. |
| 15 Refresh/supersession | Signed `refresh-candidate.json`, `supersede-candidate.json`, immutable head transitions; before/at/after stale and null/policy-expiry expectations | Real lifecycle constructors/journal and stale-boundary tests. Same sample/root retained on refresh; fixture head replay rejected. Actual post-T transitions remain open. |

The build action accepts one complete local dataset file plus explicit declarations,
reviewed indices, rights/consent, authorized root origin, existing encrypted key
and fresh registration readback. It invokes the real bounded worker, scanner,
package builder, signer and lifecycle services. It exports first with
`awaiting_host_verification`. `check-host` later verifies hosted bytes; `retire`
removes the local object and requires remote absence. A copied remote object needs
seller removal. No command writes to ai.market or changes a provider configuration.
The real CLI has no synthetic-key/registration/scan bypass switch.

The synthetic driver creates deterministic test keys only in temporary private
storage and uses the **real pinned policy engine**, no detector monkeypatch.
It exercises an actual loopback HTTP server and receipts, labelled with a fixture
HTTPS URL. This tests package/header/retirement behavior, not public DNS/TLS.
After retirement the public package is absent; its SHA remains in the reference.
The bundle manifest excludes private SQLite journals and row-bearing temporary
indexes. Keys are never copied into the output.

## Fixture manifest and CI

[preview-fixture-manifest.json](../../tests/fixtures/preview-fixture-manifest.json)
lists path, SHA-256, profile and purpose for all 14 inherited dataset/preview
corpus and pin files. The checker recomputes every hash, rejects missing/new
unpinned files, duplicate/unsafe paths and any changed backend reference pin.
`--backend-fixture` additionally compares the exact bytes with an exported
backend Git object. It neither rewrites nor blesses changed fixtures.

The fetched backend object comparison passed. Its unchanged reference SHA is
`f3e358d1e7ce7c836ce8604810675e0952201a7799b92d30858cd489906499af`.
The differential corpus and its `.sha256` are both covered. The new synthetic
bundle checksum reference is checked separately by regeneration, avoiding a
self-referential checksum cycle with the build receipt's fixture-manifest SHA.

[preview-contract-parity.yml](../../.github/workflows/preview-contract-parity.yml)
runs on push/PR: fixture guard, independent Node differential verifier, focused
2a–2e Python suites, strict synthetic evidence regeneration, 2d component/page
contracts and production frontend build. Only synthetic metadata manifests,
provenance and focused test receipts are uploaded; no package or key is uploaded.

Initial hosted run [35243726775](https://github.com/aidotmarket/aim-data/actions/runs/35243726775)
passed fixture/Node checks and 704 tests, but the older canonicalization test
invoked a locally installed `rtk` executable that GitHub runners lacked. Replaced
that no-shell subprocess with direct Node execution, retaining exact-byte
assertions. No verifier, assertion or test was removed. A subsequent hosted run exposed parent scanner imports being replayed by Python
spawn inside the parser worker, exceeding its existing memory budget. Deferred
those imports in both CLI entrypoints and added a spawn-import regression; the
512-MiB limit is unchanged. Strict checksum regeneration then passed inside the
actual Linux ARM64 customer image with networking disabled. The final CI receipt is
recorded in [ci-receipt.json](s1716-c2e-evidence/ci-receipt.json).

Final workflow/input SHA `36df6cce727bf46086ede31e93e44525a39cf3e7`: hosted
[run 35245647609](https://github.com/aidotmarket/aim-data/actions/runs/35245647609)
**passed**: 739 focused Python cases, strict synthetic checksum regeneration,
37 browser-contract cases and the production build. One inherited optional
isolated-import-target Python case is skipped; the standalone synthetic step
uses the real installed detector pins and passes. The following report commit
contains documentation/evidence only; tested inputs are bound by
[source-shas.json](s1716-c2e-evidence/source-shas.json).

## Full-suite and static verification

All complete suites ran on the exact baseline and candidate without deselection.
Per-node files and machine comparison: [parity.json](s1716-c2e-evidence/parity.json).
The recorder preserves setup/collection failures, permits test loopback sockets
and blocks external socket connections. Raw logs remain local and SHA-pinned;
committed outcome summaries omit captured output and test secret-shaped strings.
Commands, runtime identities and recorder source accompany the evidence.

| Check | Baseline | Candidate | Interpretation |
|---|---:|---:|---|
| Full pytest | 2,959 passed / 100 failed / 38 errors / 34 skipped | 2,975 passed / 100 failed / 38 errors / 34 skipped | Zero new failures/missing nodes/additional skips |
| Full Vitest | 61 passed / 23 failed | 61 passed / 23 failed | Identical per-test outcomes |
| Production Vite build | Passed | Passed | Existing chunk-size warning retained |
| Frontend lint | 10 errors / 25 warnings | Same | Byte-identical after path normalization |
| New Python scripts/tests Ruff | — | Passed | Existing marketplace module has inherited late-import E402 |
| Changed-module mypy | 9 errors in existing modules/tests | Same 9 errors | No new diagnostic; not repository-wide type-clean |
| New guard/evidence + preview routes | — | 33 passed | Includes production-shaped route mount/authentication |
| Synthetic bundle regeneration | — | Exact SHA match | Real pinned scanner, temporary synthetic registration/key and local HTTP |
| Docker customer ARM64 | — | Build + healthy + 200/401 | Development image, not RC/stable release |

The 138 baseline failing/error node IDs are individually classified in
[baseline-failure-causes.json](s1716-c2e-evidence/baseline-failure-causes.json).
They include read-only macOS `/data` writes, missing localhost:80 beta service,
absent test entitlement signing configuration, metering/API/schema drift, stale
configuration/source assertions and existing auth/diagnostic expectations. The
23 Vitest failures are the existing Node 25 localStorage-method problem. No
failure was hidden with an added skip. The hosted workflow uses Node 22 and the
customer dependency pins and the same CPU wheel index as Dockerfile.customer; the shared local full-suite environment is Python
3.12.12/DuckDB 1.5.3/Presidio 2.2.33. The separate synthetic run used the existing
isolated Presidio 2.2.362 target with spaCy/model pins. Exact runtime receipt is
[runtime.json](s1716-c2e-evidence/runtime.json).

## Customer-image receipts

Built the actual `Dockerfile.customer` for `linux/arm64`, with local development
label `dev-s1716-c2e`; no Git tag or published image was created. The final image
identity/labels and container health are in
[image receipt](s1716-c2e-evidence/image-final-receipt.json),
[container receipt](s1716-c2e-evidence/container-final-receipt.json) and
[HTTP receipts](s1716-c2e-evidence/image-http-receipts.json).
Final runtime proof uses a fresh container with `--network none` and internal
nginx HTTP probes. `/api/health` returned 200 and
`/api/marketplace/preview-builds` returned 401, `Not authenticated`.

The first development probe discovered the missing route prefix described above.
That initial network-connected startup also attempted the application's existing
auto-provision request and received HTTP 429; it was disconnected. The final
proof has no external network. An intermediate rebuild failed because a local
frontend dependency symlink entered the Docker context; removing that temporary
link restored the unchanged Docker build path. No Dockerfile/default change was
needed. Build logs are retained locally with checksums.

This does not prove amd64, multiarch publication, installer delivery, RC/stable
labels, existing-customer upgrade, real owner JWT flows or a real seller machine.
Those remain the controller's release/Gate-4 checks. Task-created proof containers
and their anonymous volumes were removed after saving receipts; the local image
remains available for inspection.

## Exact controller release commands

After merge, from the authorized release checkout and runbook execution environment:

```sh
rtk proxy scripts/release-aim-data.sh rc minor
rtk proxy scripts/release-aim-data.sh promote aim-data-v1.24.0-rc.1
```

The second command runs only after the RC has passed the controller's required
checks. `promote` alone chooses the latest RC; the explicit full namespaced tag
pins the intended RC. If v1.24.0 has been taken, use the next unused minor selected
by the script and record its tag. No manual compose/shell/PowerShell default edits.
Stable promotion updates all three defaults and rebuilds stable, rather than
retagging the RC. GHCR tags are `:v1.24.0-rc.1` and `:v1.24.0`, without the Git
`aim-data-` prefix. Full procedure: [release readiness](../release-aim-data-v1.24.0.md).

## Unverified and retained dependencies

- Real owner-bound signer registry readback/provenance, seller identity and key
  status on Sergey's machine; real source availability, explicit consent/rights,
  source snapshot, format/size and full parse/performance.
- Authorized public HTTPS origin, TLS/DNS/CORS/no-store/CDN configuration, exact
  hosted bytes and real GET/OPTIONS/retirement receipts. No S3/R2 permissions or
  automatic uploader were inferred or changed.
- RC/stable and amd64/multiarch release, fresh installation and upgrade, old retry
  state migration on the real install, key/source-volume preservation and digests.
- Independent review of the final combined producer artifact and full real I.a
  acceptance before both T dispatches; no review verdict is fabricated here.
- Post-T immutable candidate allocation, authenticated live signatures/submission,
  original-response idempotency/409, stored metadata and atomic log/checkpoint,
  trusted platform-envelope distribution, buyer/browser/anonymous-agent behavior,
  current-head revocation, real refresh/supersession, timing and zero-ingress
  platform logs/database/cache/index/SSR/backups. Chunk 5 custodial retirement
  remains an explicit dependency.
- Carried 2a–2d report findings remain carried unless explicitly addressed here.
  This chunk fixes the production route-prefix mismatch and adds its unauthenticated
  runtime probe; it does not claim to resolve broader owner-metadata exposure,
  UI schema-editor usability or all post-T authority checks.

No complete S1294 Gate-4, enabled marketplace preview, production release or
readiness to dispatch T is claimed by this report.

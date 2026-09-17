# S1716 / S1294 Chunk 2d — seller preview UI and local jobs

Implemented on `build/bq-listing-enrichment-seller-tools-s1294-c2d-seller-ui-s1716`,
from `origin/main` **9420621dbafcd64186d2c4044ece6b1840209975**. No merge,
release, provider mutation, real-data processing or live new-arm submission.
Authority: Chunk 2 build plan §H and §I/2d; merged 2a/2b/2c remain the parsing,
policy, packaging, origin, signing and lifecycle authorities.

Application/test candidate: **f10aa1cd857fb2900e7d88874800fc2e39e3ffe9**.
Milestones committed and pushed separately:

| Milestone | Commit |
|---|---|
| Local job API | `0e01431` |
| Builder screen and local metadata approval bridge | `65afc97` |
| Origin review and retirement | `0c33556` |
| Tests, recovery coverage and parity fixes | `f10aa1c` |
| This report and runbook update | Following documentation commit on the same branch |

## Routes

All routes below are relative to **`/api/marketplace/preview-builds`**, mounted
under the existing marketplace router. They require authenticated write/admin
scope, verified dataset ownership and, for existing jobs, matching job ownership.
Auth-disabled debug identities are refused. Missing resources return 404;
known resources owned by another account return 403. Closed bounded request
parsing produces non-reflecting errors. There is no client filesystem-path option.

| Method/path | Behavior and response |
|---|---|
| `POST /` | Owned dataset ID, explicit parsing/schema declarations when absent; asynchronous bounded 2a worker. Metadata status only. |
| `GET /?dataset_id=…` | Latest owned job, for reload recovery. No browser row storage. |
| `POST /metadata-approval` | Owned dataset ID plus approved metadata SHA-256. Persists explicitly local fixture references; not platform P1 allocation. |
| `GET /{job_id}` | State, 2a progress phase/records/canonical bytes/time, source/root digests, selection budgets, policy, publication/header receipts and candidate metadata. No cell values. |
| `GET /{job_id}/rows` | Separate private selection view, leaf-index pagination, complete cells, sizes and stable ineligible codes; `Cache-Control: no-store`. Pages are bounded by count and bytes. |
| `POST /{job_id}/cancel` | Stops the bounded worker/review, releases its installation lock and removes the private index. Published packages require withdrawal. |
| `PUT /{job_id}/selection` | Ascending unique leaf indices and display columns; whole-row 100/25/250,000-byte caps, no projection or trimming. |
| `POST /{job_id}/policy` | Actual sealed `CommitmentPreviewBuilder.prepare` scan; only policy/version/passed/digest and stable reason codes returned. Missing detector identity fails closed. |
| `POST /{job_id}/package` | `local` or `export` only; 2b `PublicationStore` writes beneath a fixed owner-specific managed root. Export storage is separate from controlled public storage. |
| `GET /{job_id}/package` | Explicit authenticated package-byte attachment download; the intentional row-bearing export surface. No-store; immutable hashed object. |
| `POST /{job_id}/origin-check` | Seller HTTPS URL, exact generated object path, actual-URL manifest budget, 2b pinned GET/OPTIONS and byte/hash checks. Only closed header receipts escape. |
| `POST /{job_id}/candidate` | Rights/permission/metadata confirmations; existing registered install key, signed proofs/commitment, immutable 2c local candidate and journal. No new key creation. |
| `POST /{job_id}/submit` | Records local prepared state and returns **Prepared locally; marketplace preview submission awaits backend support**. No transport or live submit call. |
| `POST /{job_id}/withdraw` | New signed withdrawal, 2c retirement journal and 2b tombstone/removal, GET 404/410 + OPTIONS receipts. Exported-object retirement remains pending until seller removal is verified. |
| `POST /{job_id}/refresh` | New bounded preparation with retained leaf/proof identities and sample hash, fresh package/evidence revision, and 2c predecessor linkage. Prior hosting retirement is separate. |

Stable failures include `dataset_owner_unverified`, `job_owner_mismatch`,
`parsing_declaration_required`, `source_not_allowed`, `source_changed`,
`rows_limit`, `fields_limit`, `canonical_bytes_limit`, `invalid_selection`,
`detector_unavailable`, `rescan_required`, `origin_path_mismatch`,
`metadata_approval_changed`, and the sealed origin/signing service codes.

Only the private row view and explicitly requested package attachment carry
records. Status, policy, candidate, submit, errors and metadata journals do not.
The publication URL is never sent to a marketplace proxy. Tests prohibit
outbound sockets during the Submit operation.

## Screens and data flow

`CommitmentPreviewBuilder.tsx` is mounted in the existing DatasetDetail step 3,
after metadata approval, beside the public-sample choice. No new outer wizard,
feature flag or mandatory long rights form was added. `ListingEditorForm` has a
small preview-status hook. The default remains **No sample**.

The builder shows source version and full-build progress/cancel; an optional
compact declaration editor appears only when complete parser/schema declarations
are missing. It currently accepts the explicit 2a declaration as JSON. Local rows
are selected by immutable leaf index using keyboard-operable checkboxes. The live
budget counts every committed field, including hidden display columns, and never
silently trims selection. Ineligible rows display stable codes.

Cells are React text children, including script tags, formulas, nested JSON and
URLs. No HTML/Markdown/formula evaluation or clickable cell URLs is introduced.
The all-fields warning and this disclaimer remain visible:

> Seller-selected preview. Membership does not establish quality,
> representativeness, legality, compliance, identity or external completeness.

The rights choice supplies a fixed local rights-basis statement for its digest;
it does not infer rights from the listing license. Exact complete-record public
permission and restricted-content confirmation precede the sealed local scan.
Source/root/sample short digests, selected display fields, exact origin and
registered key fingerprint appear before **Prepare signed preview**. The
candidate stays explicitly local. Step 6's live marketplace approval is not
present before T-backend.

`PreviewOriginReview.tsx` offers an app-managed publication directory or package
export plus seller HTTPS URL. It displays the exact directory/object path,
package bytes/hash, destination, hosting/retirement responsibilities, GET/OPTIONS
status and headers. It explicitly says a connected S3/R2 bucket is not
automatically writable or public. Errors use focus and alert announcements;
progress/budgets use status announcements. Cancellation, retries, latest-job
recovery, refresh and withdrawal are available without republishing the listing.

The old projected/truncated sample helper and old row-publication controls were
removed. The legacy disclosure helper refuses `approved_rows`; ordinary listing
publication retains the existing `none` payload. Runtime DatasetDetail tests
check that the old unredacted sample endpoint is not requested and that snapshot
retry does not call listing publication again.

The metadata bridge stores the browser's approved projection digest with locally
allocated fixture references. It does **not** relabel a legacy disclosure snapshot
as platform P1 or fabricate registration authority. Real authenticated platform
allocation and complete live P1 reference binding remain T integration work.

## Verification receipts

Evidence is in [s1716-c2d-evidence](s1716-c2d-evidence/SHA256SUMS), including exact
commands/cwds, runtime versions, source SHA-256s, complete compressed logs,
per-node outcomes, mutation logs and comparison JSON. Backend runs use isolated
SQLite/data/upload/processed directories and a recorder that blocks external
socket connections while permitting test loopback. No real source data or
customer credentials were used.

| Check | Baseline | Branch | Result |
|---|---:|---:|---|
| Full pytest | 2,888 passed; 96 failed; 38 errors; 34 skipped | 2,899 passed; 95 failed; 38 errors; 34 skipped | **Zero branch-only failures, zero missing baseline nodeids, zero additional skips** |
| Affected pytest (15 existing suites + new route suite) | 729 passed; 15 failed; 1 skipped | 739 passed; 15 failed; 1 skipped | **Zero branch-only failures, zero missing baseline nodeids, zero additional skips** |
| Full Vitest | 44 passed; 23 failed | 56 passed; 23 failed | **Zero branch-only failures**; legacy projection/egress expectations replaced with refusal and cap tests |
| TypeScript `tsc --noEmit` | 35 diagnostics | Same 35 diagnostics | No new diagnostics |
| ESLint | 10 errors, 25 warnings | Same 10 errors, 25 warnings | No new diagnostic signatures |
| Production Vite build | Not rerun for baseline | Passed | Receipt retained |
| Ruff F checks, new Python modules/tests | — | Passed | No undefined/unused imports |
| `git diff --check` | — | Passed | No whitespace errors |

[Full per-node parity](s1716-c2d-evidence/parity.json),
[affected parity](s1716-c2d-evidence/affected-parity.json),
[frontend parity](s1716-c2d-evidence/frontend-parity.json),
[typecheck parity](s1716-c2d-evidence/typecheck-parity.json),
[lint parity](s1716-c2d-evidence/lint-parity.json).

New route tests cover actual marketplace-router mounting, authentication,
owner 403/404 isolation, closed/path options, caps, no cell values in status,
sealed-scan refusal, real worker cancellation/rebuild, source invalidation,
local approval idempotency, package download, signed local preparation,
no-outbound Submit, refresh linkage and retirement. Successful synthetic policy
fixtures follow 2b's existing test convention: pin detector identity metadata
while using the real local scan engine. The production sealed scanner is not
replaced or made injectable.

Thirteen component tests cover the builder/origin steps, keyboard selection,
budgets, hostile text, required disclaimers and consent, signing confirmation,
awaiting-backend state, GET/OPTIONS, S3/R2 wording, focus/error announcements,
cancel and reload recovery. Existing disclosure and DatasetDetail regressions
are retained with the revised non-custodial behavior.

### Mutations

Both mutations were applied temporarily, executed, and restored before final
verification. Both produced a failing test (exit 1):

1. Replace the React text-child guard with `dangerouslySetInnerHTML`: the hostile
   cell rendering test fails.
2. Remove the dataset ownership comparison: the owner-validation test fails.

Exact edits/commands/results: [mutations.json](s1716-c2d-evidence/mutations.json),
[inert-render.log](s1716-c2d-evidence/inert-render.log),
[dataset-owner.log](s1716-c2d-evidence/dataset-owner.log).

### Existing failures and fixture correction

These are parity results, **not a clean full-suite pass**. Every baseline failing
node/error is retained in [baseline-failures.md](s1716-c2d-evidence/baseline-failures.md),
with extracted causes in [baseline-failure-causes.json](s1716-c2d-evidence/baseline-failure-causes.json)
and full tracebacks in `baseline.log.gz`. Principal causes are the missing
localhost:80 beta service (38 errors), macOS `/data` write failures, absent test
entitlement signing configuration, stale channel/config/source assertions,
API/schema drift in metering/metadata/portal tests, and existing auth/security
expectations. The unchanged `test_valid_api_key` failed in baseline and passed in
branch; no auth implementation was changed. The 23 frontend failures are existing
localStorage-method errors under Node 25.6.0. Existing TypeScript and ESLint
errors are outside the new builder/origin implementation.

An initial full branch run also exposed an unchanged 2b test's random hex cell
marker being classified as personal data. The fixture is now a distinctive,
deterministic synthetic marker; the real scanner and zero-egress assertion remain.
The initial failure is retained in `branch-initial.log.gz`. The final full and
affected runs have no branch-only failure. No test was skipped to obtain parity.

## Limits and unverified work

- Tested macOS arm64, Python 3.12.12, Node 25.6.0, npm 11.8.0. The existing local
  environment contains DuckDB 1.5.3 and Presidio 2.2.33, rather than the release
  pins in the plan. Exact versions are in [runtime.json](s1716-c2d-evidence/runtime.json).
  Pinned customer-image execution is **unverified**. The production policy
  correctly refuses mismatched detector identity.
- No real registered-seller key/evidence, HTTPS host, S3/R2 permissions, browser
  deployment, customer installation, RC/stable release, or post-T approval was
  exercised. No public marketplace preview is claimed active.
- Existing uploads without verifiable recorded ownership require authenticated
  re-upload. No implicit ownership migration was introduced. This adapter handles
  managed complete original local files; S3/database sources fail closed pending
  a complete manifest resolver. These restrictions are visible stable errors.
- Missing declarations use a compact JSON editor; a richer schema declaration UI
  is not included. There is no arbitrary source/export path picker.
- The normal entrypoint already enforces one uvicorn worker. Cross-process job
  dispatch is not implemented. R2 changes restart recovery to explicit review expiry; exported packages
  recover through the publication journal without rebuilding an index.
- Seller removal of externally copied packages cannot be automatic; retirement
  stays pending until GET/OPTIONS prove absence. Historical downloaded copies are
  outside this local retirement mechanism.
- P1 references are explicitly local fixtures; live allocated references,
  platform submission/verification, log/checkpoint integration and public viewer
  acceptance remain T-backend/T-frontend work. This is not overall S1294 Gate 4
  or customer release completion.

Operational instructions were added to the existing
[preview-publication runbook](../runbooks/preview-publication.md).


## R2 fold

D1 (DeepSeek F1, MEDIUM mandate) implements the supplied ruling: independent
(owner, dataset) review sessions, default 30-minute configurable owner-idle expiry,
terminal-action cleanup, failed-start durability and explicit expired recovery.
Heavy computation remains serialized; completed reviews no longer occupy that
installation build slot. Selected proof metadata is saved at packaging so signing
and origin review continue after the private row index is deleted. The frontend
keeps those stages available without a live index and offers a new build after
expiry. No merge or release is part of this fold.

The complete [session lifecycle table and configuration](../runbooks/preview-publication.md#seller-ui-and-local-job-api-chunk-2d)
are authoritative for states, HTTP codes, lease renewal and cleanup. The final
per-finding commits and verification receipts are recorded below after validation.

| State/action | HTTP / stable code | Session and private row index |
|---|---|---|
| `building` (queued or computing) | 200 / null | Live; bounded idle lease plus existing 2a worker budgets |
| `ready`, `selected`, `scanned` | 200 / null | Live; same idle lease |
| Same owner/dataset second create | 409 / `detail: {code: job_already_running, job_id: <existing>}` | Existing session unchanged; no new job row |
| Another owner requests this dataset/job | 403 / `dataset_owner_unverified` or `job_owner_mismatch` | No foreign job id/content or lease renewal |
| Another owned dataset create/list/status | 200 / normal result (list may be null) | Independent session; computation may queue |
| Synchronous start failure | 409 / `detail: {code: build_start_failed, job_id: <failed>}` | Durable `failed` row, no live session; never stranded `building` |
| Worker failure | 200 status / `build_failed` or stable 2a code | `failed`; released and index deleted |
| Idle expiry or reload without a live review | 200 status / `review_expired` | `expired`; no automatic rebuild; row operations return 409 / `review_expired` |
| Reload while live | 200 / existing state | Reattaches the same owner session/index |
| Package written | 200 / `packaged` | Released; index deleted before action returns |
| Candidate prepared / submit | 200 / `signed_candidate` (submit adds local outcome) | Released; metadata/proofs suffice, no index rebuild |
| Cancel | 200 / `cancelled`; published job: 409 / `withdraw_required` | Cancelled build/review released and index deleted before return |
| Withdraw | `withdrawn` / `external_retirement_pending`, then `retired` / null | Released and index deleted, including pending external retirement |

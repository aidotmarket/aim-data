# S1719 live verified-preview submission

## Outcome

AIM Data now completes the seller path from an already published dataset to an
ai.market verified preview. It uses backend-issued P1 and disclosure identifiers,
the install's existing Ed25519 key, the seller's stored ai.market session, and the
current backend contracts. No runtime fixture allocation, local metadata digest,
operator registration file, or content scan gate participates in submission.

No production write was made while implementing or validating this change.
Backend source was inspected at `origin/main` commit
`52fbbee98377ec8a6c155dd99db85fd5c4e992b1`.

## Implemented live sequence

All ai.market calls use the seller bearer token stored by AIM Data sign-in. The
preview disclosure, commitment, and proofs are independently Ed25519-signed by
the registered install key; ai.market re-resolves that install and key before it
accepts a decision.

1. `GET /api/v1/listings/{listing_id}/at-a-glance/preview`
   returns the current backend `summary_id`, source revision, summary/render
   hashes, At a glance projection, approval text, and state. The Public sample
   panel shows this exact response.
2. If the state is not `approved`, the seller's explicit **Approve At a glance
   and prepare signed preview** action sends
   `POST /api/v1/listings/{listing_id}/at-a-glance/approve` with the exact
   `summary_id`, `source_revision`, `summary_hash`, `render_hash`, a fresh
   `request_id`, and `sample_decision: "none"`. AIM Data then reads the preview
   again. This was chosen over a dead-end instruction because the seller is
   already reviewing the authoritative summary in the same flow.
3. AIM Data signs the commitment and selected-row proofs, builds a proposed
   closed disclosure, and sends
   `POST /api/v1/listings/{listing_id}/at-a-glance/preview` with
   `{ "binding": <DisclosureBinding> }`. The backend replaces all P1 references,
   seller identity, `disclosure_version`, and `approved_at` with authoritative
   values. AIM Data validates and journals those exact returned bytes.
4. AIM Data signs the returned disclosure binding and sends the frozen
   `PreviewDisclosureRequest` to
   `POST /api/v1/listings/{listing_id}/at-a-glance/approve`. Identical retries use
   the same journalled bytes and request ID.
5. AIM Data reads `GET /api/v1/listings/{listing_id}` for the listing slug and
   `GET /api/v1/public/listings/{slug}/preview-manifest`. The UI shows `pending`
   when no public manifest is returned, or `visible` with a live listing link
   when the manifest is available.
6. Withdrawal first allocates a new `decision: "withdraw"` candidate through the
   same candidate route, signs it, submits it to
   `POST /api/v1/listings/{listing_id}/at-a-glance/withdraw`, and only then
   tombstones/removes the seller-hosted package. External-host retirement remains
   pending until GET/OPTIONS prove removal.
7. Refresh reads live marketplace state, starts one new bounded preparation, and
   carries the prior backend disclosure version as both `supersedes` and
   `expected_current_disclosure_id`. The seller re-confirms and completes the same
   allocation/sign/submit sequence; the old package is retired separately.

The seller-facing transport maps every stable refusal emitted by the current
summary and preview services to an exact action: refresh At a glance, rebuild
from the current commitment, register/sign in again, use the listing owner,
repair a schema mismatch, or retry a timed-out/unreachable marketplace.

## Registration evidence decision

The partial HEAD change was directionally correct but incomplete as a release
flow. Runtime signing now constructs `PreviewSigningService` only from the local
registration store's `install_id` and `seller_id` plus the existing encrypted
Ed25519 key. No `registration-evidence.json`, one-hour observation, or operator
step is read. The backend's `registered_key()` check remains authoritative for
active status, seller ownership, rotation/revocation, and fingerprint equality.

The generic registration-evidence reader remains only for the repository's
non-runtime synthetic evidence/rotation tools; it is not on the seller preview
route. Fixture allocation and replay tables were also removed from application
runtime code.

## Upgrade safety

The chosen behavior is **complete an existing v1 job on v1**. On first load, a
job whose immutable scan record is `aim-preview-policy-v1` / `1.0.0` is persisted
with `policy_compatibility: "legacy_v1_completion"`. The attestation helper and
the request-model recomputation accept exactly the legacy v1/1.0.0 and current
v2/2.0.0 pairs. Candidate construction retains the persisted v1 identity; every
new job still emits only v2/2.0.0. The package envelope does not carry the scan
identity, so package-hash invalidation is not the reason for retaining v1. The
reason is to preserve the existing job's recorded evidence identity and complete
the compatibility path already accepted by the backend and viewer.

The upgrade test now runs both a persisted packaged v1 job and a persisted hosted
v1 job through the router. It performs origin verification for the packaged case,
prepares the candidate, validates the complete closed request, submits the frozen
request twice to the backend-shaped fake, and proves that both submissions are
byte-identical and idempotent. It also proves that the disclosure retains
v1/1.0.0 and that its binding digest is the digest recomputed from those proofs.

## R2 fold

The Gate 3 round 2 fold is limited to the four Council findings:

1. The scan-attestation predicate now admits exactly
   `{("aim-preview-policy-v1", "1.0.0"), ("aim-preview-policy-v2", "2.0.0")}`.
   All existing closed-field, URL, package-profile, byte-limit,
   signature-length, shared-field, proof-order, sampled-leaf and digest checks
   remain in place. Legacy jobs are not re-stamped; new scans remain v2-only.
2. Upstream ai.market 401 and 403 responses use the explicitly reserved local
   status 409 while retaining the structured `{code, message}` refusal. They can
   no longer enter the generic local-401 refresh/logout branch. Router tests cover
   `seller_session_required` on marketplace summary and
   `seller_session_expired` on candidate; the frontend test proves the local
   access and refresh tokens remain intact and the actionable message is shown.
3. `inertPreviewText` now applies the buyer renderer's bounded
   Cc/Cf/Cs-to-U+FFFD transform to scalar text and recursively to nested keys and
   values. The component test covers script/style-looking text, URLs, NUL, DEL,
   a bidirectional override, a zero-width format character and a lone surrogate.
   It proves no element, link or style carrier is created and that the source row,
   selected leaf, schema digest and Merkle root remain unchanged. This is
   render-time only; package bytes, canonical selection and signed hashes are not
   modified.
4. This report now states what the legacy test actually proves and removes the
   incorrect package-hash rationale. The two trailing spaces in the no-content
   decision header were stripped solely so the pinned base-to-candidate whitespace
   check is truthful; the decision text and status were not changed.

New and strengthened coverage:

- `test_upgrade_keeps_policy_v1_job_completable[packaged|hosted]` covers origin,
  candidate, unchanged v1 identity, attestation recomputation, closed request
  validation, frozen submission and identical retry. Reverting the helper to
  v2-only makes candidate construction fail this test.
- Router and transport tests cover the reserved local status for missing,
  expired and forbidden marketplace credentials.
- The frontend API test proves a 409 marketplace-auth refusal does not invoke
  local token removal. The component test proves recursive render neutralisation
  without selection/hash mutation.

R2 validation, run locally before the single push:

- `rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/private/var/tmp/s1719-r2-final-serial VECTORAIZ_DATA_DIRECTORY=/private/var/tmp/s1719-r2-final-data VECTORAIZ_UPLOAD_DIRECTORY=/private/var/tmp/s1719-r2-final-uploads VECTORAIZ_PROCESSED_DIRECTORY=/private/var/tmp/s1719-r2-final-processed DATABASE_URL=sqlite:////private/var/tmp/s1719-r2-final.db /var/tmp/aim-data-s1719-venv/bin/python -m pytest -q tests/test_dataset_canonicalization.py tests/test_dataset_merkle_service.py tests/test_preview_content_policy.py tests/test_preview_package_service.py tests/test_preview_origin_service.py tests/test_preview_signing_service.py tests/test_preview_marketplace_transport.py tests/test_preview_disclosure_schemas.py tests/test_marketplace_commitment_push.py tests/test_preview_lifecycle.py tests/test_preview_build_routes.py tests/test_vz_publish_proxy.py tests/test_dataset_publish_signed_proxy.py tests/test_s804_disclosure_dataset_detail.py tests/test_preview_fixture_parity.py tests/test_preview_producer_evidence.py --tb=short`
  — **passed: 569 tests**, 32 dependency deprecation warnings.
- From `frontend`, `rtk proxy env NODE_OPTIONS=--no-experimental-webstorage npm test -- --run src/components/CommitmentPreviewBuilder.test.tsx src/components/PreviewOriginReview.test.tsx src/lib/disclosure.test.ts src/pages/DatasetDetail.test.tsx`
  — **passed: 65 tests in 4 files**; existing React `act()` warnings only.
- From `frontend`, `rtk npx tsc --noEmit` — **passed**.
- From `frontend`, `rtk npm run build` — **passed**, 3,107 modules; existing
  Browserslist-age and large-chunk warnings only.
- `rtk ruff check app/services/preview_content_policy.py app/services/preview_marketplace_transport.py tests/test_preview_build_routes.py tests/test_preview_marketplace_transport.py`
  — **passed, no issues**.
- `rtk proxy /var/tmp/aim-data-s1719-venv/bin/python scripts/check_preview_fixture_parity.py`
  — **passed, 15 pinned files**; the local checker verified the backend SHA pin
  because no backend copy was supplied.
- `rtk node tests/preview_differential_check.cjs` — **passed, 8 accepted digests
  and 6 expected rejections**.
- `rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/private/var/tmp/s1719-r2-synthetic-serial VECTORAIZ_DATA_DIRECTORY=/private/var/tmp/s1719-r2-synthetic-data VECTORAIZ_UPLOAD_DIRECTORY=/private/var/tmp/s1719-r2-synthetic-uploads VECTORAIZ_PROCESSED_DIRECTORY=/private/var/tmp/s1719-r2-synthetic-processed DATABASE_URL=sqlite:////private/var/tmp/s1719-r2-synthetic.db /var/tmp/aim-data-s1719-venv/bin/python tests/run_preview_producer_synthetic.py --output /private/var/tmp/s1719-r2-synthetic-bundle --reference tests/fixtures/preview-producer-synthetic-shas.json`
  — **passed**, including row-egress checks; no live owner/HTTPS proof is claimed.
- `rtk git diff --check d0e7f4960733da2962daa47ab9c61e3ea8afde29..HEAD`
  — **passed**.

The first frontend test invocation could not start because this detached worktree
had no installed `node_modules` (`vitest: command not found`). `rtk npm ci`
installed the existing lockfile-defined dependencies, after which the full
frontend matrix above passed. It reported 25 existing audit findings; no
dependency or lockfile update and no `npm audit fix` was performed because that
would be outside this four-item fold.

No backend route, no-content-gate behavior, seller-session storage choice,
provider configuration, production state or removed metadata-approval endpoint
was changed. The three documented backend additions remain documentation only,
as directed.

## Backend changes required

The first-time signed-in seller flow works against the current backend. Three
backend additions are still required for complete install-native recovery and
key observability without relying on a stored web session:

### 1. Install-action authentication on seller preview routes

Extend these existing routes in
`app/api/v1/endpoints/listing_summary.py` to accept the EdDSA install-action JWT
already validated by `app/routers/vz_publish.py::authenticate_local_action` and
`app/services/vz_publish_service.py::validate_install_action_jwt`:

- `GET /api/v1/listings/{listing_id}/at-a-glance/preview`
- `POST /api/v1/listings/{listing_id}/at-a-glance/approve`
- `POST /api/v1/listings/{listing_id}/at-a-glance/preview`
- `POST /api/v1/listings/{listing_id}/at-a-glance/withdraw`
- `GET /api/v1/listings/{listing_id}` or a smaller slug readback

For POST, bind the JWT `metadata_hash` to JCS of the exact request body and use
actions `preview_summary_approve`, `preview_candidate_allocate`,
`preview_disclosure_approve`, and `preview_disclosure_withdraw`. For GET, bind to
JCS of `{ "listing_id": <uuid> }` and actions `preview_summary_read` and
`preview_listing_read`. Resolve the JWT principal to the existing seller owner
check. Responses and request schemas stay unchanged. Refusals are 401 for
missing/malformed/expired/replayed/wrong-action tokens, 403 for a non-owner or
inactive seller, 404 for an absent listing, and the existing 409/422 preview
codes. Until this exists AIM Data uses the stored seller OAuth bearer token.

### 2. Current disclosure-head readback

Extend the existing owner-only
`GET /api/v1/listings/{listing_id}/at-a-glance/preview` response with:

```json
{
  "current_preview": {
    "disclosure_version": "uuid",
    "decision": "approve",
    "eligible": true,
    "sample_hash": "64 lowercase hex or null"
  }
}
```

Return `current_preview: null` when no head exists. Populate it through a bounded
read helper in `app/services/listing_preview_disclosure.py` from
`ListingPreviewDisclosureHead` and its current event. Use the route's existing
seller-owner auth (plus install auth above). Refusals are 401/403/404 only; this
read does not allocate or mutate. AIM Data needs this after local journal loss to
recover `expected_current_disclosure_id`; today the backend can only signal
`stale_expected_head`, so recovery cannot safely guess the head.

### 3. Registered install-key readback

Add owner/install-authenticated
`GET /api/v1/vz/install/{install_id}` in `app/routers/vz_publish.py`, returning:

```json
{
  "install_id": "uuid",
  "seller_id": "uuid",
  "algorithm": "ed25519",
  "public_key": "base64url Ed25519 key",
  "fingerprint": "64 lowercase hex",
  "status": "active|rotated|revoked",
  "valid_from": "RFC3339 UTC",
  "valid_until": "RFC3339 UTC or null"
}
```

Use seller OAuth or an install-action JWT with action `install_key_read` bound to
the install ID. Refuse 401 for invalid auth, 403 for wrong seller, and 404 for an
unknown install. AIM Data should use this opportunistically to display current
registration state, never as a signing precondition; submission remains the
authority.

## Files changed

- Live transport and lifecycle: `app/services/preview_marketplace_transport.py`,
  `preview_build_service.py`, `preview_signing_service.py`,
  `preview_lifecycle.py`, `marketplace_push_service.py`.
- Local API/proxy/schema: `app/routers/preview_builds.py`,
  `app/routers/marketplace_publish.py`, `app/models/preview_build_schemas.py`.
- UI: Public sample builder, origin review, API DTOs, disclosure helper, and
  Dataset detail wiring under `frontend/src`.
- Contracts/tests: preview route, signing, lifecycle, live transport, marketplace
  proxy, frontend component tests, synthetic evidence helper, and preview CI.
- Documentation: producer guide, preview-publication runbook, v1.24.1 draft, and
  this report.

## Validation

Final clean pre-push results:

- `rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/private/var/tmp/s1719-final2-matrix-serial VECTORAIZ_DATA_DIRECTORY=/private/var/tmp/s1719-final2-matrix-data VECTORAIZ_UPLOAD_DIRECTORY=/private/var/tmp/s1719-final2-matrix-uploads VECTORAIZ_PROCESSED_DIRECTORY=/private/var/tmp/s1719-final2-matrix-processed DATABASE_URL=sqlite:////private/var/tmp/s1719-final2-matrix.db /var/tmp/aim-data-s1719-venv/bin/python -m pytest -q tests/test_dataset_canonicalization.py tests/test_dataset_merkle_service.py tests/test_preview_content_policy.py tests/test_preview_package_service.py tests/test_preview_origin_service.py tests/test_preview_signing_service.py tests/test_preview_marketplace_transport.py tests/test_preview_disclosure_schemas.py tests/test_marketplace_commitment_push.py tests/test_preview_lifecycle.py tests/test_preview_build_routes.py tests/test_vz_publish_proxy.py tests/test_dataset_publish_signed_proxy.py tests/test_s804_disclosure_dataset_detail.py tests/test_preview_fixture_parity.py tests/test_preview_producer_evidence.py --tb=short`
  — **565 passed**, 30 dependency deprecation warnings.
- From `frontend`, `rtk proxy env NODE_OPTIONS=--no-experimental-webstorage npm test -- --run src/components/CommitmentPreviewBuilder.test.tsx src/components/PreviewOriginReview.test.tsx src/lib/disclosure.test.ts src/pages/DatasetDetail.test.tsx`
  — **64 passed** in 4 files; existing React `act()` warnings only.
- From `frontend`, `rtk npx tsc --noEmit` — **passed**.
- From `frontend`, `rtk npm run build` — **passed**, 3,107 modules;
  existing Browserslist-age and large-chunk warnings only.
- `rtk ruff check` over all 15 changed Python application/script/test files —
  **passed, no issues**.
- `rtk proxy /var/tmp/aim-data-s1719-venv/bin/python scripts/check_preview_fixture_parity.py`
  — **passed, 15 pinned files**.
- `rtk node tests/preview_differential_check.cjs` — **passed, 8 accepted
  digests and 6 expected rejections**.
- `rtk proxy env ... /var/tmp/aim-data-s1719-venv/bin/python tests/run_preview_producer_synthetic.py --output /private/var/tmp/s1719-synthetic5.oVcTKt/bundle --reference tests/fixtures/preview-producer-synthetic-shas.json`
  — **passed**, including row-egress checks and the regenerated marketplace
  candidate checksum pins.
- `rtk git diff --check` — **passed**.

During implementation, the first combined matrix found one obsolete static test
that still required the removed local metadata gate (**545 passed, 1 failed**);
the assertion was corrected and the full matrix above was rerun cleanly. Early
synthetic invocations using the macOS `/var/tmp` symlink were rejected by the
existing canonical-path protection; the final command uses `/private/var/tmp`.

## Risks and known issues

- AIM Data cannot recover an unknown backend disclosure head after local journal
  loss until backend change 2 exists. It fails with an actionable
  `stale_expected_head` instead of guessing or replacing another head.
- Install-action JWT auth is not accepted by the current seller summary routes;
  the working flow requires the seller's stored ai.market session until backend
  change 1 lands.
- AIM Data's controlled origin is an isolated local publication store/server; the
  seller installation still needs its authorized public HTTPS reverse proxy.
  Export hosting remains seller-managed. No provider permissions are invented.
- No production write or real-seller submission was performed in this source
  change. Local HTTP fakes mirror the backend schemas and route sequence; release
  verification must still exercise a real authorized seller/install and origin.

## Scope

Scope was strictly limited to verified-preview seller publication, its current
backend transport, upgrade safety, directly affected UI/tests/docs, and removal
of obsolete runtime fixture/evidence gates. No production/provider mutation,
deployment, credential creation, listing write, or unrelated cleanup was done.

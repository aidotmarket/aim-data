# BQ-AIMCHANNEL-DISCLOSURE-UX-S804 Gate 1

## Purpose

Seller-facing disclosure UX for the AIM Data listing flow. The seller makes one clear decision at list time about exactly what becomes public:

- The existing guided Metadata Review remains the field-level approval for title, description, tags, category, schema, counts, and other buyer-facing metadata.
- Add one explicit sample-inclusion decision: publish the exact approved real rows shown in the UI, or publish no sample rows. Synthetic sample rows are not allowed.
- Add one final publish confirmation that plainly says the approved disclosure content will be published everywhere AI and search can find it, including search engines, AI assistants, HuggingFace, and AI-training crawler use.
- After the ai.market listing exists, AIM Data creates a backend disclosure snapshot. That snapshot is the activation trigger for the SEO push pipeline.

This spec is design only. Do not implement code under this Gate 1 change.

## Verified Anchors

### AIM Data repo

Repo: `aidotmarket/aim-data`

Origin/main SHA: `7a6c04010771dc41e338df815b250e49f300321d`

- Current listing wizard state and gates live in `frontend/src/pages/DatasetDetail.tsx` lines 278-289, including PII state, metadata approval, active step, and publishing state.
- Step 1 Privacy Review UI is `frontend/src/pages/DatasetDetail.tsx` lines 651-734. It reviews PII findings and records the privacy attestation at lines 716-725.
- Step 2 Metadata Review UI is `frontend/src/pages/DatasetDetail.tsx` lines 737-899. It edits title, description, category, tags, and approves the allAI draft at lines 886-893.
- Step 3 Listing Details and Publish UI is `frontend/src/pages/DatasetDetail.tsx` lines 902-925.
- Current publish handler posts only metadata through `marketplaceApi.publish` at `frontend/src/pages/DatasetDetail.tsx` lines 526-566.
- Dataset detail already loads local sample rows with `datasetsApi.getSample(id, 20)` and stores them in `sampleData` at `frontend/src/pages/DatasetDetail.tsx` lines 970-979. The sample tab renders those exact local rows at lines 1479-1505.
- Frontend API types include `DatasetListingMetadata`, `MarketplacePublishRequest`, and `MarketplacePublishResponse` with optional `listing_id` at `frontend/src/lib/api.ts` lines 402-460.
- The sample API client currently calls `/api/datasets/{id}/sample?limit={limit}` at `frontend/src/lib/api.ts` lines 645-648.
- The live publish proxy is `POST /api/marketplace/publish` in `app/routers/marketplace_publish.py` lines 323-335. It returns `listing_id` and `marketplace_url` from the ai.market response.
- The signed proxy posts to `{ai_market}/api/v1/vz/publish` at `app/routers/marketplace_publish.py` lines 424-520.
- The local sample endpoint returns local rows from the processed dataset at `app/routers/datasets.py` lines 902-940. It defaults to PII redaction, so the disclosure UX must explicitly request and display the exact values it will submit.
- Dead or out-of-scope paths still exist and must not be wired for this work: `/api/datasets/{dataset_id}/pipeline` at `app/routers/datasets.py` lines 809-840, legacy dataset listing metadata at lines 1072-1104, and raw-listing publish at `app/routers/raw_listings.py` lines 361-400.
- Local dataset records already persist `metadata` and `listing_id` at `app/services/processing_service.py` lines 40-75 and expose them through `to_dict` at lines 76-92. `_save_record` persists `metadata_json` and `listing_id` at lines 249-291.
- Legacy `frontend/src/components/PublishModal.tsx` still contains an older three-step modal and posts `/api/marketplace/publish` at lines 161-200, but the active guided flow in `DatasetDetail.tsx` owns this design. Do not add S804 to the modal unless the build first proves it is still reachable.

Runbook context:

- `/Users/max/Projects/ai-market/runbooks/aim-data.md` identifies AIM Data as local-first and non-custodial at lines 1-8.
- The runbook describes the current three-screen flow at lines 165-175 and says publish is `POST /api/marketplace/publish` to `{ai_market}/api/v1/vz/publish` at lines 173-175.
- The same runbook names the signed VZ path as the only live publish path at lines 179-197 and warns against dead paths at lines 187-190 and 209-212.
- `/Users/max/Projects/ai-market/runbooks/publish-paths.md` says the canonical endpoint is `POST /api/v1/vz/publish` at lines 11-15, that AIM Data and vectorAIz publish through it at lines 18-22, and that the one-route invariant is part of the operating model at lines 25-28 and 50-58.

### ai-market-backend repo

Repo: `aidotmarket/ai-market-backend`

Origin/main SHA: `e249e4c7debe810755adefb6254029467c5e9fb6`

- The VZ publish endpoint is `POST /api/v1/vz/publish` at `app/routers/vz_publish.py` lines 100-157. It creates or updates the listing and returns `listing_id`.
- `VZPublishResponse` includes optional `listing_id` at `app/schemas/vz_publish.py` lines 231-237.
- `create_or_update_listing` upserts by `(seller_id, source_dataset_id=vz_raw_listing_id)`, sets `status="published"`, generates JSON-LD, fires publish hooks, commits, refreshes, and returns the listing at `app/services/vz_publish_service.py` lines 803-986.
- Disclosure snapshot route is `POST /api/v1/listings/{listing_id}/disclosure-snapshots`, mounted through the v1 router. Endpoint code is `app/api/v1/endpoints/disclosure_snapshots.py` lines 28-41; router inclusion is `app/api/v1/router.py` lines 117 and 145.
- Snapshot auth uses `get_current_user_flexible` and tags auth method as JWT or API key at `app/api/v1/endpoints/disclosure_snapshots.py` lines 32-40.
- `DisclosureSnapshotCreateRequest` requires `approved_fields`, `sample_decision`, `approved_sample`, `ai_training_notification_ack`, `ai_training_notification_text`, `license`, `approval_source`, and optional `source_publish_operation_id` at `app/schemas/disclosure_snapshot.py` lines 101-112.
- Sample validation limits are defined at `app/schemas/disclosure_snapshot.py` lines 15-18. The sample validator enforces non-empty columns, max 25 columns, max 100 rows, matching row refs, exact column keys, no forbidden content, and max 250 KB at lines 64-92.
- `sample_decision` must be `"none"` with `approved_sample=null` or `"approved_rows"` with `approved_sample` present at `app/schemas/disclosure_snapshot.py` lines 119-125.
- Snapshot model constraints make the snapshot immutable-by-insert, enforce true AI-training ack, and store approval audit fields at `app/models/disclosure_snapshot.py` lines 13-62.
- `DisclosureSnapshotService.create_snapshot` verifies listing ownership, generates `disclosure_version`, stores approval hash and actor type, and commits at `app/services/disclosure_snapshot_service.py` lines 33-76.
- If the listing is already published, snapshot creation updates listing JSON-LD and enqueues search submission at `app/services/disclosure_snapshot_service.py` lines 69-86.
- Backend `disclosure_version` is generated server-side as `dsv_<UTC YYYYMMDDHHMMSS>_<8 hex chars>` at `app/services/disclosure_snapshot_service.py` lines 195-196. AIM Data must store the returned value; it must not generate it.
- Public snapshot response summarizes sample decision and counts without returning rows at `app/services/disclosure_snapshot_service.py` lines 151-171.
- HuggingFace publishing is disclosure-snapshot based: `app/services/huggingface_service.py` states non-custodial bounded seller-approved rows at line 9, fetches current snapshots at lines 325-330 and 403-412, uploads approved rows or metadata-only cards at lines 429-457 and 475-487, and persists HF URL into JSON-LD at lines 521-541.
- Search submission owns IndexNow and HuggingFace providers at `app/services/search_submission_service.py` lines 18-22 and initializes them at lines 86-94.

### vectorAIz repo

Repo: `aidotmarket/vectoraiz`

Origin/main SHA: `49fecb996bbc929ac0e0bca3744ec88dc6bf6b17`

- vectorAIz has its own `frontend/src/pages/DatasetDetail.tsx`, with independent publish modal state at lines 132-148 and independent sample loading at lines 165-174.
- vectorAIz opens its own `PublishModal` from `frontend/src/pages/DatasetDetail.tsx` lines 407-418.
- vectorAIz `frontend/src/components/PublishModal.tsx` owns its own modal state at lines 52-79, PII scan at lines 81-107, and publish request to `/api/marketplace/publish` at lines 161-200.
- vectorAIz backend publish proxy defines its own request/response and signed publish route in `app/routers/marketplace_publish.py` lines 40-59 and 106-172.

## Architecture Decision

Keep the AIM Data wizard at three screens. Do not add a fourth wizard step.

Add the sample decision and disclosure preview to Step 3, before the final publish button. Step 2 remains field-level approval, because the seller already reviews and approves the AI-generated public metadata there. Step 3 becomes "Listing Details and Disclosure", containing:

1. Listing details and price.
2. Sample disclosure decision: "Publish these real sample rows" or "Publish no sample rows".
3. A compact read-only disclosure summary: approved metadata fields plus sample decision.
4. A single final confirmation checkbox and button.

Reasoning:

- The BQ scope says review-as-approval plus explicit sample decision plus one confirmation.
- A new screen would dilute the single decision and make sellers treat the snapshot as a separate compliance chore.
- The final confirmation belongs at the moment the seller commits to public distribution and AI-training crawler notice.

## Screen Flow

### Step 1: Privacy Review

Keep existing behavior. The seller reviews local PII findings and column actions. This is not the public disclosure approval for sample rows.

Build requirement: if sample rows will be offered in Step 3, Step 3 must visually flag any approved-sample column that Step 1 flagged as personal data and require the seller to switch to "No sample rows" or confirm that exact row content. The final confirmation still remains one checkbox.

### Step 2: Metadata Review

Keep existing behavior as field-level approval. The seller reviews and edits:

- Title
- Description
- Category
- Tags
- Public schema/column summary
- Row count, column count, format, size, and applicable trust metadata

When the seller clicks Accept all and continue, AIM Data freezes an in-memory disclosure draft from the current approved form values. Any later edit in Step 3 invalidates the frozen approval and either routes back to Step 2 or re-runs the existing metadata approval gate. Do not allow silent drift between what was approved and what enters `approved_fields`.

### Step 3: Listing Details and Disclosure

Rename screen copy from "Listing Details" to "Listing Details and Disclosure".

Add a "Public sample" section above the publish action:

- Default decision: `none`.
- Option 1: "No sample rows" maps to `sample_decision="none"` and `approved_sample=null`.
- Option 2: "Publish these real sample rows" maps to `sample_decision="approved_rows"` and includes the exact row values shown in the table.
- The rows table is read-only. Sellers cannot approve hidden rows.
- The table must state row count and columns included. It must not imply synthetic, generated, or anonymized data.
- If candidate rows exceed backend limits, AIM Data truncates candidate rows and columns before display and labels the displayed set as the complete public sample.

Final confirmation copy contract:

> I understand that when I publish, the approved title, description, tags, category, schema, and my sample-row choice will become public on ai.market and may be shared with search engines, AI assistants, HuggingFace, and other AI discovery systems. If I approve sample rows, exactly the rows shown here will be public. I understand this public listing may be used by AI-training crawlers.

Build requirement: this copy requires a write-like-Max voice pass before implementation merge. The meaning must not soften:

- "Public" must remain explicit.
- Search engines, AI assistants, HuggingFace, and AI-training crawler use must remain explicit.
- "Exactly the rows shown here" must remain explicit for approved rows.
- "Synthetic" must not appear as an offered option.

## Sample Selection

Source of candidate rows:

- Use the local dataset preview/sample data already available in AIM Data.
- The current UI loads 20 rows with `datasetsApi.getSample(id, 20)`. S804 implementation should request enough rows to support the backend maximum, up to 100 rows, but only from local processed data.
- The current backend sample endpoint defaults `redact_pii=true`. Because the business intent is real rows or none, the S804 sample approval path must fetch the exact public sample values using an explicit unredacted disclosure-preview mode, display those exact values, and submit the same serialized values. If the product cannot safely expose real rows, default to and require `none`.

Client-side enforcement:

- Max 100 rows.
- Max 25 columns.
- Max 250 KB canonical JSON payload for `approved_sample`.
- Every approved row must contain exactly the approved columns.
- `row_refs` must be stable for the displayed candidate set. Use deterministic local display refs such as `preview:0`, `preview:1`, etc. Do not imply source table primary keys unless they are actually available and shown.
- If the local preview has more than 25 columns, choose the first 25 columns from the displayed preview unless the implementation adds an explicit column selector. The seller must see the final included column list before confirming.
- If serialized sample size exceeds 250 KB, reduce rows first, then columns if needed, and update the displayed table before the seller can approve.

No raw data beyond approved sample rows:

- No full dataset, hidden rows, raw files, local database credentials, S3 details, or unapproved preview rows may leave the install.
- The `approved_sample.rows` payload must be exactly the same JSON values rendered in the approval table.
- Do not call allAI or any LLM with approved sample rows. allAI may continue to generate metadata from profiles and summaries only.

## Disclosure Snapshot Payload

`approved_fields` maps from the seller-approved metadata draft:

- `title`: Step 2 approved title.
- `description`: Step 2 approved description.
- `category`: Step 2 approved category.
- `tags`: Step 2 approved tags.
- `schema`: public schema derived from approved column summary, not raw row data.
- `data_format`: published file format.
- `source_row_count`: published row count.
- `source_column_count`: published column count.
- `compliance_summary`: only already public compliance/trust summary fields; no raw PII findings samples.
- `source_delivery_public_metadata`: only public delivery facts safe for marketplace display; no S3 role ARN, bucket secret, connection IDs, serials, tokens, credentials, or local paths.

Required constants:

- `approval_source`: `"aim_channel"`.
- `ai_training_notification_ack`: `true`, only after the seller checks the final confirmation.
- `ai_training_notification_text`: the exact final confirmation copy version shown to the seller.
- `license`: use the listing/license default selected by product policy. If no license UI exists in the implementation scope, use the existing marketplace listing default and record that exact string; do not leave blank.
- `source_publish_operation_id`: a client-generated UUID for the publish attempt, reused across retry of the same disclosure snapshot attempt.

## API Sequencing

The snapshot endpoint requires an ai.market `listing_id`, so the sequence is publish first, snapshot second.

1. Seller confirms Step 3.
2. AIM Data creates a local `source_publish_operation_id`.
3. AIM Data posts existing listing payload to local `POST /api/marketplace/publish`.
4. The local proxy signs the publish JWT and posts to `{ai_market}/api/v1/vz/publish`.
5. ai.market returns `listing_id` in `VZPublishResponse`.
6. AIM Data must retain that `listing_id`. The current frontend type supports it, but `DatasetDetail.tsx` currently discards it.
7. AIM Data posts disclosure payload to ai.market `POST /api/v1/listings/{listing_id}/disclosure-snapshots`.
8. Prefer an AIM Data local proxy endpoint for snapshot creation so the backend can reuse the stored ai.market seller token or incoming bearer token, centralize retry/idempotency state, and avoid browser-origin/token drift. The proxy must not sign this as a VZ publish JWT; the backend endpoint expects seller JWT or API key auth.
9. On snapshot success, AIM Data stores the returned `disclosure_version` and marks disclosure status complete.
10. ai.market snapshot creation updates JSON-LD and enqueues search submission for the already-published listing. That is the SEO activation boundary.

Do not attempt to create the snapshot before publish unless ai.market changes `VZPublishResponse` to accept and return a reserved listing id before publication. Current backend does not support that shape.

## Local State and Audit

Store a disclosure decision audit on the AIM Data dataset record, under `metadata.disclosure_decision` or a typed successor field. The record must be persisted through the existing dataset record storage path.

Minimum local audit fields:

- `status`: `draft`, `publish_pending`, `snapshot_pending`, `complete`, `failed`.
- `source_publish_operation_id`.
- `listing_id`.
- `disclosure_version` returned by ai.market.
- `approved_fields_hash`.
- `sample_decision`.
- `approved_sample_hash` when rows are approved; no need to persist full rows locally beyond existing preview cache unless the retry path requires exact replay.
- `approved_sample_row_count`.
- `approved_sample_columns`.
- `ai_training_notification_text`.
- `license`.
- `created_at`, `updated_at`.
- `last_error` when snapshot creation fails.

Retry requirement:

- If a snapshot create fails after listing publish succeeds, AIM Data must keep the exact approved disclosure payload or enough locally persisted data to reconstruct exactly what the seller approved. If exact replay cannot be guaranteed, retry must force the seller back through Step 3 approval with the current exact rows.

Idempotency and re-publish/update:

- ai.market creates immutable snapshots and generates `disclosure_version` server-side.
- Re-publishing an existing source dataset upserts the listing through `vz_publish_service.create_or_update_listing`. If the approved disclosure set changes, AIM Data creates a new disclosure snapshot after the upsert and stores the new returned `disclosure_version`.
- If the seller retries the same failed snapshot attempt after publish, AIM Data reuses `source_publish_operation_id` and the same approval payload. The backend currently does not expose idempotent upsert by operation id, so duplicate successful retries may create multiple versions. AIM Data must prevent double-submit in the UI and, after an ambiguous network failure, query or reconcile before creating another snapshot if a read endpoint becomes available. If no read endpoint exists in the implementation scope, show a seller-visible "verify disclosure" recovery state rather than silently creating repeated versions.
- A listing must not be presented as fully published/discoverable in AIM Data until `disclosure_version` is stored locally.

## Failure Handling

### Publish fails before listing exists

- Keep current behavior: no snapshot attempt.
- Show publish failure with actionable message.
- Keep seller on Step 3 with decisions intact.

### Publish succeeds, snapshot create fails

This is the critical S804 case.

Seller-visible state:

- Show "Listing published, disclosure snapshot pending" rather than success.
- Explain that the listing exists on ai.market but the public discovery package is not complete.
- Provide a primary "Retry disclosure snapshot" action.
- Provide a secondary "Review disclosure decision" action that reopens Step 3 with the same approved fields and sample decision.
- Do not silently mark the dataset as simply published.

Backend/local state:

- Persist `listing_id`, `source_publish_operation_id`, disclosure payload hash, sample decision, and error.
- Keep `status="snapshot_pending"` or `status="failed"` with `last_error`.
- Retry calls only the snapshot endpoint. It must not re-publish unless the listing update payload changed.

SEO behavior:

- Treat snapshot success as the activation trigger for the S804 SEO push. If snapshot creation fails, JSON-LD, IndexNow, and HuggingFace activation must not be considered complete by AIM Data.

### Snapshot succeeds, local store update fails

- Show success only after local audit persistence succeeds.
- If ai.market succeeded but local persistence failed, show a recoverable warning and fetch/reconcile the snapshot if backend read support exists. If not, record a local repair task and display the listing as "disclosure status unknown" until resolved.

## VectorAIz Parity Strategy

Build AIM Data first.

Reasoning:

- AIM Data now owns a newer embedded guided metadata review in `DatasetDetail.tsx`; vectorAIz still uses its own older `PublishModal` flow.
- Both products publish through the same signed ai.market route, but their frontends and UX state are duplicated.
- The disclosure snapshot backend contract is shared and product-neutral. The UI implementation is not currently shareable without first extracting a product-neutral package.

Phase 1:

- Implement S804 in AIM Data only.
- Build payload assembly and client-side validators as pure TypeScript helpers inside AIM Data, with names and tests suitable for later extraction.

Phase 2:

- Port the disclosure decision and snapshot proxy to vectorAIz.
- Reuse the pure helper contract from AIM Data if practical; otherwise duplicate intentionally and keep tests contract-identical.
- vectorAIz must use the same backend snapshot endpoint, same copy contract, same sample limits, same failure state, and same "real rows or none" rule.

Do not block AIM Data on vectorAIz parity, because S804 activates the SEO pipeline for AIM Data listings waiting on first snapshot.

## Acceptance Criteria

- The AIM Data listing flow remains three screens.
- Step 2 approval is the source of truth for `approved_fields`.
- Step 3 includes an explicit sample choice with exactly two outcomes: approved real rows or no rows.
- Synthetic samples are not offered or submitted.
- Seller sees exactly the rows and columns that will be sent as `approved_sample`.
- Client enforces 100-row, 25-column, 250-KB, matching-column, and row-ref limits before submission.
- Final confirmation copy plainly states approved fields, sample decision, public distribution, search engines, AI assistants, HuggingFace, and AI-training crawler use.
- Snapshot call happens only after ai.market publish returns `listing_id`.
- Snapshot payload uses `approval_source="aim_channel"` and `ai_training_notification_ack=true`.
- On snapshot success, AIM Data stores returned `disclosure_version` locally.
- On publish success plus snapshot failure, seller sees a pending/failed disclosure state with retry; no silent success.
- No raw data beyond the approved sample rows leaves the install.
- No approved sample rows are sent to allAI or any LLM.
- Re-publish with changed disclosure creates a new backend-generated disclosure version and stores it locally.
- Existing dead paths remain untouched: `/pipeline`, `/process-full`, `/api/datasets/{id}/publish`, raw-listing publish, and website publish wizards.
- AIM Data implementation lands first; vectorAIz parity is tracked as phase 2.

## Test Plan Skeleton

Frontend unit tests:

- Disclosure payload builder maps approved metadata to `approved_fields`.
- Sample decision `none` produces `approved_sample=null`.
- Sample decision `approved_rows` includes only displayed columns and rows.
- Limit enforcement truncates or blocks over 100 rows, over 25 columns, and over 250 KB before submit.
- Editing approved metadata after Step 2 invalidates approval or routes back through approval.
- Final confirmation disabled until sample decision and confirmation checkbox are present.

Frontend integration tests:

- Happy path: privacy review, metadata approve, no sample rows, final confirm, publish, snapshot success, local complete state.
- Happy path: approve displayed sample rows, confirm, snapshot payload rows exactly match rendered rows.
- Publish failure: no snapshot request made.
- Snapshot failure after publish: UI shows "Listing published, disclosure snapshot pending" with retry and review actions.
- Retry snapshot: does not re-run publish when listing payload has not changed.

Backend/local AIM Data tests:

- Snapshot proxy forwards seller JWT/API key auth, not VZ publish JWT.
- Snapshot proxy rejects missing `listing_id`.
- Local dataset audit persists `listing_id`, `source_publish_operation_id`, payload hashes, sample decision, and returned `disclosure_version`.
- Ambiguous snapshot failure does not silently mark published complete.

ai-market-backend contract tests:

- Existing disclosure snapshot tests cover schema limits and public response shape; add cross-product contract fixture for AIM Data payload shape if missing.
- Verify creating a snapshot for an already-published listing regenerates JSON-LD and enqueues search submission.
- Verify unauthorized seller cannot snapshot another seller's listing.

E2E smoke:

- In a local AIM Data install pointed at staging ai.market, publish a listing with `sample_decision=none`; verify ai.market disclosure snapshot exists and JSON-LD includes the disclosure summary.
- Repeat with approved rows; verify HuggingFace path receives only approved rows.
- Force snapshot endpoint 500 after publish; verify AIM Data stays in pending retry state and can recover without creating a new listing.

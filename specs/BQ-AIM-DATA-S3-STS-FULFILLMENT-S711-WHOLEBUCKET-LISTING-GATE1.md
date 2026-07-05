# BQ-AIM-DATA-S3-STS-FULFILLMENT-S711 — Chunk: Whole-Bucket Listing (Seller Action) — GATE 1 (Design)

**Author:** mars-S1127
**Pillar(s):** AIM Data, ai.market, allAI
**Tracked as:** S711 chunk (whole-bucket seller front-door). NOT a new BQ (anti-duplication; extends the shipped S711 STS fulfillment path).
**Review intensity:** HEAVY — publish / money / customer-data + auth-scope surface. CORE §3 → unanimous Council (MP + AG + DeepSeek).

## Problem (from live customer, Sergey/eolymp, S1127)
A seller can only list **individual files** in AIM Data; there is no action to list an **entire bucket/prefix** as one marketplace listing that a buyer can purchase and download in full. Max directive S1127: "We need a list-the-whole-bucket feature. It needs to let a buyer download the entire bucket. I do not care about fingerprinting the files — I just need the buyer to have access to the bucket."

## Ground truth (traced against aim-data @12d15f1, ai-market-backend @c1d06dcb)
1. **Buyer whole-prefix download is BUILT + live + proven.** Backend `POST /orders/{id}/download` returns `delivery_type == "s3_scoped_credential"` (app/api/v1/endpoints/orders.py:254,292); refresh at :304. S968 capstone E2E: buyer JWT -> live download -> scoped STS cred -> `aws s3 sync` pulled the **whole sold prefix**; root ListBucket DENIED. Fulfillment/STS/presign all on main. **This is the "buyer accesses the whole bucket" engine and it works.**
2. **Reference (no-files[]) listings already deliver the whole prefix.** When a listing carries an `s3_connection` block and no explicit `files[]` manifest, delivery scopes the buyer to the whole prefix (agent-QA wiring S1011: ListObjectsV2 under sold prefix when files[] absent).
3. **Seller front-door is the gap.** Frontend `S3ConnectionReview.tsx` only calls `/objects/{id}/register` (listSingleObject) — per-file. `register_object` (app/routers/s3_connections.py:527) **downloads each file locally** into a dataset — wrong model for a 600k-object bucket. No UI action calls the whole-connection publish path.
4. **Whole-connection publish path exists but is object-coupled.** `/marketplace/versions/publish` (app/routers/marketplace_publish.py:338) emits the `s3_connection` block (bucket, region, role_arn, prefix, serial_id) via `resolve_s3_publish_source` (app/services/s3_publish_source_resolver.py). BUT that resolver requires a dataset with `source_type=s3` **and a single `source_object_key`** under the prefix — it assumes a registered file. There is no connection-level (no-object) resolution.
5. **Security constraint — bucket ROOT is currently disallowed.** `_validate_prefix` (s3_publish_source_resolver.py:149) raises `bucket_root_prefix` when prefix is empty or "/". Scoped delivery is bounded to a **non-root** prefix by design.

## Goal / Non-goals
**Goal:** A seller action "List the whole bucket" that creates ONE marketplace listing bound to the S3 connection (bucket + prefix + role_arn), with **no per-file manifest and no local file download**, so that on purchase the buyer receives scoped STS credentials and downloads the **entire prefix**. One listing, one seller-set price.
**Non-goals:** per-file fingerprint/manifest fidelity (Max: not needed); materializing bucket contents in AIM Data; changing the buyer download engine (already built); changing pricing model.

## Design (smallest reversible mechanism — reuse the proven path)
**D1. Connection-level publish source resolution.** Add a connection-level resolution in `s3_publish_source_resolver` that validates the **connection** (owner-scoped, verified, valid role_arn) WITHOUT requiring a `source_object_key`, taking a `scope` argument: `prefix` (validate a non-root prefix via the existing `_validate_prefix`) or `bucket_root` (explicit opt-in; allow empty/root prefix ONLY on this path). Returns the same `S3PublishSourceResolution` (bucket/region/role_arn/prefix/serial_id). Reuses `_validate_prefix` and owner-scoping unchanged.
**D2. Reference-listing publish (no files[], no download).** New seller endpoint `POST /s3-connections/{id}/publish-bucket` that: verifies the connection, builds the publish payload with the `s3_connection` block and **no files[] manifest**, and sends it through the existing signed proxy (`publish_via_signed_proxy`). No `register_object`, no local dataset bytes. Listing metadata (title, description, price, category) supplied by the seller; object_count/total_size come from the last completed scan's `scan_job.objects_enumerated` (the TRUE bucket count, not the 1000 sample) as informational only — not a manifest.
**D3. Seller UI action.** In `S3ConnectionReview.tsx`, add a primary "List the whole bucket" button (distinct from per-object selection) that opens the publish modal pre-scoped to the connection and calls D2. The modal lets the seller pick scope: the connection prefix (default) or the entire bucket root (explicit opt-in with a plain-language exposure warning). Copy makes clear the buyer will receive the whole bucket/prefix.
**D4. Manifest/fingerprint.** Per Max, no per-file manifest required. The listing's immutability/manifest field (if the receiver requires one) is a **prefix-level digest** (hash of {bucket, prefix, role_arn, objects_enumerated}) — cheap, no 600k materialization. Buyer download is by live S3 sync regardless, so the digest is metadata only.

## SCOPE DECISION — RESOLVED (Max S1127)
Max: make BOTH scopes available and let the **seller choose** at listing time.
- **Prefix scope (default):** list the connection's non-root prefix. No security change.
- **Whole-bucket-root scope (opt-in):** seller may list the entire bucket at its root. This relaxes the current `_validate_prefix` bucket-root block, so a purchased root listing grants the buyer scoped STS access to the ENTIRE bucket, including objects the seller adds later.

**Guardrails for the root option (required, since it widens auth-scope):**
- Root scope is an explicit, deliberate seller choice in the UI (never the default), with a clear plain-language warning that the buyer gets the whole bucket including future additions.
- The relaxation is confined to this listing path: `_validate_prefix` stays strict for all existing callers; add a distinct connection-level resolution that accepts an explicit `allow_bucket_root=true` flag rather than loosening the shared validator.
- STS delivery for a root listing scopes to the bucket root prefix only (still no cross-bucket / no other-bucket access); short-lived creds unchanged.
- Council must explicitly bless the root path (unanimous) as an auth-scope change; dissent surfaces to Max.

## Security / risk
Auth-scope + money + customer-data. Buyer credential is short-lived, prefix-scoped STS (proven). No long-lived creds leave seller AWS. Reversible (git revert + redeploy; no destructive migration expected — new endpoint + resolver path + UI). Gate flow: unanimous Council G1->G2->build->G3->deploy->G4 (G4 = real Sergey sale).

## Acceptance
- Seller clicks "List the whole bucket" -> one listing on ai.market bound to bucket+prefix, no files[], no local download.
- Test buyer purchase -> scoped STS cred -> `aws s3 sync` pulls the whole prefix; access outside prefix denied.
- No bucket-root listing unless decision (B) is taken.

---

## GATE-1 RESULT — UNANIMOUS APPROVE (S1127)
- **MP** (gpt-5.5): APPROVED_WITH_MANDATES
- **AG** (gemini-3.5): APPROVED_WITH_MANDATES
- **DeepSeek** (deepseek-v4-pro): APPROVED
No REJECT, no CRITICAL veto. Reuse of the proven signed-proxy + STS delivery with no-files[] reference listings is blessed. Root relaxation via a distinct resolver path is accepted. Gate 2 = verify these mandates are addressed in the build.

## MANDATES (M1–M5) — must be satisfied by the build
**M1 (MP-1) Root path isolation.** Shared `_validate_prefix` stays strict for ALL existing callers (still rejects empty/`/`). The new connection-level resolver enters root mode ONLY on an explicit `scope='bucket_root'` + `allow_bucket_root=True`; NO implicit root fallback from an empty/malformed prefix. Tests: (a) existing prefix publish + object register still reject root; (b) only the new publish-bucket path can enter root mode.

**M2 (MP-2 + AG-1 + DeepSeek-2) Count/scope integrity.** Source `object_count`/size from `scan_job.objects_enumerated` ONLY when the scan's target prefix exactly matches the chosen listing scope (prefix vs bucket root). On mismatch or no matching completed scan, set count `null` / "unscanned" — never show a prefix-scoped count for a root listing (or vice versa). Include a `last_scan_time`.

**M3 (MP-3) Server-side authz + validation.** `POST /s3-connections/{id}/publish-bucket` must enforce server-side (never rely on UI): authenticated seller owns the connection; connection status=verified; `role_arn` present+valid; requested scope authorized; listing metadata/price/category pass the SAME validation as existing marketplace publish.

**M4 (AG-2) STS policy per scope.** Buyer STS credential IAM policy is built dynamically from the sold listing's scope: prefix → `arn:aws:s3:::{bucket}/{prefix}/*` (+ scoped ListBucket); bucket_root → `arn:aws:s3:::{bucket}/*` (+ bucket-root ListBucket). A prefix-scoped listing must NEVER mint a bucket-root credential. (Verify against existing s3_presigner/sts_assumer scoping on ai-market-backend; extend only if needed.)

**M5 (AG-3 + DeepSeek-1) Delivery contract + disclosure.** Listing flagged `delivery_type=s3_scoped_credential`; buyer download bypasses single-file size checks (prefix sync). Frontend root opt-in warning must state explicitly that ALL current AND FUTURE files under the bucket root become accessible to the buyer. Optional: cheap prefix-level digest hash({bucket,prefix,role_arn,objects_enumerated}) + creation timestamp for audit/dispute (metadata only; not a per-file manifest).

## Build plan (Gate 2 → build)
Repos: aim-data (backend resolver + endpoint + tests; frontend button/modal). Verify M4 against ai-market-backend fulfillment scope logic (extend there only if the scope isn't already derived from the sold listing's prefix). Reversible; new endpoint + resolver path + UI; no destructive migration. Unanimous Council Gate-3 audit before deploy. Gate-4 code-level on staging; real cross-account Gate-4 = Sergey first sale (human-gated).

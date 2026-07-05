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
**D1. Connection-level publish source resolution.** Add a connection-level resolution in `s3_publish_source_resolver` that validates the **connection** (owner-scoped, verified, valid role_arn, valid non-root prefix) WITHOUT requiring a `source_object_key`. Returns the same `S3PublishSourceResolution` (bucket/region/role_arn/prefix/serial_id). Reuses `_validate_prefix` and owner-scoping unchanged.
**D2. Reference-listing publish (no files[], no download).** New seller endpoint `POST /s3-connections/{id}/publish-bucket` that: verifies the connection, builds the publish payload with the `s3_connection` block and **no files[] manifest**, and sends it through the existing signed proxy (`publish_via_signed_proxy`). No `register_object`, no local dataset bytes. Listing metadata (title, description, price, category) supplied by the seller; object_count/total_size come from the last completed scan's `scan_job.objects_enumerated` (the TRUE bucket count, not the 1000 sample) as informational only — not a manifest.
**D3. Seller UI action.** In `S3ConnectionReview.tsx`, add a primary "List the whole bucket" button (distinct from per-object selection) that opens the publish modal pre-scoped to the connection and calls D2. Copy makes clear the buyer will receive the whole bucket/prefix.
**D4. Manifest/fingerprint.** Per Max, no per-file manifest required. The listing's immutability/manifest field (if the receiver requires one) is a **prefix-level digest** (hash of {bucket, prefix, role_arn, objects_enumerated}) — cheap, no 600k materialization. Buyer download is by live S3 sync regardless, so the digest is metadata only.

## OPEN DECISION FOR MAX (blocks final scope semantics)
Current security forbids listing a **bucket root** (`bucket_root_prefix`). Sergey's data sits at top-level paths (checkers/..., README.md) — effectively the whole bucket root. Choose:
- **(A) Safe / no change:** "whole bucket" = the connection's **non-root prefix**. Sellers whose data is at root must set a prefix (or we guide them). Zero security relaxation.
- **(B) Allow bucket-root delivery:** relax `_validate_prefix` to permit root, so a buyer gets scoped creds to the **entire bucket** incl. anything added later. Wider blast radius; needs explicit Max sign-off + Council as a security change.
Recommendation: ship **(A)** now (covers the general feature safely); decide (B) separately with eyes open. For Sergey specifically, (A) works if he lists under a prefix; (B) if he wants the literal bucket root.

## Security / risk
Auth-scope + money + customer-data. Buyer credential is short-lived, prefix-scoped STS (proven). No long-lived creds leave seller AWS. Reversible (git revert + redeploy; no destructive migration expected — new endpoint + resolver path + UI). Gate flow: unanimous Council G1->G2->build->G3->deploy->G4 (G4 = real Sergey sale).

## Acceptance
- Seller clicks "List the whole bucket" -> one listing on ai.market bound to bucket+prefix, no files[], no local download.
- Test buyer purchase -> scoped STS cred -> `aws s3 sync` pulls the whole prefix; access outside prefix denied.
- No bucket-root listing unless decision (B) is taken.

# S1719 no-content-gate implementation report

Authority: `docs/decisions/2026-09-18-no-content-gate.md` (the supplied Max
decision is committed in item 7). Scope is AIM Data producer source only. No
merge, tag, release, provider mutation, production access, or marketplace
submission was performed.

## Seller-path obstacle inventory

The inventory below walks metadata approval, selection, policy confirmation,
package, origin check, candidate, local submit, withdrawal, refresh, and review
expiry. “Kept” means the condition protects a technical, cryptographic,
ownership, or explicit seller-confirmation invariant rather than judging row
content.

| Path | Condition | Disposition and reason |
|---|---|---|
| Metadata approval | Authenticated owner, write scope, owned dataset, exact approved metadata digest and unchanged source revision | **Kept**: ownership and signed metadata/source binding. |
| Build admission | Complete local source, supported declared format/type, explicit parsing/schema, immutable source, one bounded worker, safe private directories, finite/canonical values, memory/disk/record budgets | **Kept**: technical reproducibility and resource safety. |
| Selection | 1–100 distinct ordered leaves, 1–25 display/complete-row fields, 250,000 canonical bytes, 1 MiB envelope, 256 KiB manifest, depth 16, 10,000 nodes, proof/index validity; binary remains an unsupported preview value | **Kept**: published contract and safe-render/resource limits. |
| Row browsing | `secret`, `personal_data`, `executable`, `url`, `restricted_content`, `long_prose`, `control_character`, `formula`, `high_entropy` | **Removed**: rows are no longer disabled or hidden by content classification. |
| Policy endpoint | Presidio/spaCy availability, language/model/version identity, ML findings, deterministic content corpus | **Removed**: no detector is constructed or called. The endpoint records the seller confirmations and emits a passing v2 attestation after technical validation. |
| Policy endpoint | Rights basis, permission to show the exact rows publicly, restricted-content confirmation | **Kept**: explicit seller decisions required by the binding decision. |
| Package | Prior passing content scan / `rescan_required` | **Removed**: content cannot make package creation unavailable. A missing confirmation step reports `policy_confirmation_required`; a changed confirmation reports `confirmation_changed`. |
| Package | Exact envelope, complete rows, proof inclusion, sample hash, immutable publication path/journal, package/manifest caps | **Kept**: technical and cryptographic chain. |
| Origin check | HTTPS, public non-platform host, no userinfo/query/fragment/redirect, DNS/address pinning, GET 200, exact expected decoded bytes and SHA-256, JSON object body, GET CORS allow-origin `https://ai.market` or `*` | **Kept**: this is the minimum credential-free browser GET plus origin and exact-package binding. |
| Origin check | Exact custom media type, `Cache-Control: no-store`, OPTIONS success/method headers, no Set-Cookie, identity/no compression | **Non-blocking observations**: retained in receipts where present. OPTIONS transport failure is recorded with status 0. gzip/deflate bodies are checked after browser-equivalent decoding. |
| Candidate | Current seller confirmations, unchanged source/metadata approval, hosted exact package, owner-bound active registered Ed25519 key with fresh evidence, closed candidate fields and signatures | **Kept**: explicit consent, ownership, and cryptographic authority. |
| Submit | Frozen candidate/request identity, unchanged metadata digest; no network submit in this pre-T producer | **Kept**: idempotency and the existing no-live-integration boundary. |
| Withdraw | Newly signed withdrawal when a candidate exists, immutable head/request identity, package tombstone/removal and GET 404/410 evidence | **Kept**: cryptographic history and actual retirement. OPTIONS/header details are observations rather than retirement refusals. |
| Refresh | Same owner/source/sample/proof identity, fresh explicit confirmations, new timestamp/request/disclosure identity, prior-head link | **Kept**: immutable history and renewed seller consent. No content rescan occurs. |
| Review expiry | Positive configured 30-minute idle lease, source recheck, cleanup of private row index; explicit rebuild after expiry/reload without live state | **Kept**: local resource/privacy cleanup and stale-source protection, not content judgement. |

## Cross-repository requirements found

The requested paths in the detached `ai-market-backend` and
`ai-market-frontend` checkouts did not contain the S1717 listing-preview files.
The active implementation copies were inspected read-only in
`backend-wt-s1717-g` at `12eaf4b951aef152f64dc849332ee3a60f844436` and
`frontend-wt-s1717-g` at `c8dbcaa46acda5cc7f9700d79818d46cc3cba252`.
They must be relaxed in the same coordinated release; this AIM Data branch does
not modify either peer-owned repository.

| Consumer | Current requirement that affects this producer |
|---|---|
| Backend `app/schemas/dataset_commitment.py` | Both proof models accept only `scan_policy="aim-preview-policy-v1"`; they must accept v2/2.0.0 while retaining v1/1.0.0 as legacy input. |
| Backend listing preview disclosure/projection | Candidate binding, package media type/profile, proof order/identity, package URL/ceiling, seller signatures, manifest cap, current-head/idempotency, log/checkpoint, source/summary approval, and seller/key ownership remain required and AIM Data continues to emit them. |
| Frontend `wire.ts` / `policy.ts` | `validateProof` currently accepts only the single configured v1 producer/version pair. It must accept the exact pairs v2/2.0.0 and legacy v1/1.0.0, and must not run the old deterministic content corpus for either. |
| Frontend `transport.ts` | It currently refuses a package without exact `application/vnd.aim.preview+json`, `Cache-Control: no-store`, and identity/no content encoding. These must become observations if arbitrary seller origins are to match the S1719 decision. AIM Data's controlled origin and exports continue to emit the custom media type and no-store metadata, so the producer itself does not emit a shape the current viewer rejects. |
| Frontend browser transport | HTTPS public DNS name, no credentials, no redirects, CORS, bounded body, and JSON/package verification remain necessary. AIM Data keeps these requirements. |

## Risks and scope

- Seller confirmations, withdrawal, and immutable cryptographic verification are
  the controls after removing automated classification. A seller can now publish
  sensitive or unlawful content if their confirmations are false.
- Until backend and frontend policy acceptance change together, a v2 producer
  proof will be rejected by those S1717 consumer snapshots.
- Until frontend transport is relaxed, externally hosted packages that pass the
  new minimal origin check can still be rejected for media/cache/compression
  headers. The controlled AIM Data origin remains compatible.
- This change does not relax canonicalization, supported logical types, size and
  resource caps, source immutability, ownership, registration, signatures,
  transparency evidence, package integrity, or plain-text rendering.

Fixture hashes and final validation commands/counts are recorded after items 5
and 6 are complete.

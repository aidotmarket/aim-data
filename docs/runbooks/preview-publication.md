# Seller-local preview publication (Chunk 2b)

Authority: S1294 Chunk 2 build plan S1716 §§C/D/I. This procedure exports
synthetic or explicitly seller-approved complete rows to the seller's own
publication directory. It makes no marketplace request and grants no provider
write authority. Producer-local signing/journal recovery is described in the Chunk 2c report;
the customer UI is implemented in Chunk 2d. Neither establishes live T integration.
The final [producer guide](../commitment-preview-producer.md) documents the
Chunk 2e evidence commands, exact headers, registration prerequisites and remaining
Gate-4 requirements. [Release readiness](../release-aim-data-v1.24.0.md) records
the controller-only RC/promote commands; this chunk must not execute them.

1. Within the live Chunk 2a private job, construct
   `CommitmentPreviewBuilder(tree, complete_schema_descriptors)`. The index must
   belong to the complete immutable source snapshot. `page(start, count)` reads
   complete logical rows in sorted leaf order for local review. The caller must
   retain the existing worker resource/source-change protections; the public
   `run_commitment_job` currently cleans its index before returning, so that
   returned metadata alone is not a builder input. Never retain row-bearing
   indexes as an alternative permanent dataset copy.
2. Obtain explicit rights confirmation, public-preview permission and confirmation
   of no prohibited/third-party restricted content for the exact selection.
   Call `prepare` with ascending distinct indices, corresponding fixed proof UUIDs,
   commitment/disclosure UUIDs, scan time, all three consent booleans,
   intended credential-free seller package URL and `manifest_bytes` (the entire
   actual/expected metadata manifest, not a proofs-only size). The service also
   independently budgets a conservative complete manifest with duplicated proofs,
   descriptors, signatures, signer records and maximal log paths. Chunk 2c must
   check actual final signed manifest bytes again. Fewer rows may be needed even
   when the row cap passes. No projected, redacted or edited rows are admitted.
3. Policy `aim-preview-policy-v2` / `2.0.0` is seller-attested and has no
   automated content rules. Do not call Presidio/spaCy on this path. Dates,
   places, emails, URLs, long prose, identifiers, phone numbers, negative numeric
   text and control characters do not refuse or delay a preview. Emit verdict
   `passed` with empty rules/reasons after technical limits and the three seller
   confirmations pass. Accept v1/1.0.0 only as legacy input; producers emit v2.
4. Create `PublicationStore(public_root, journal_root)` using canonical local
   paths with no symlink components. The journal directory must be owner-only
   mode 0700 and outside the public root. `export(prepared)` atomically writes
   `previews/<disclosure_uuid>/<sample_hash>.json` and a private mode-0600 journal
   with identities, SHA-256, byte count and `exported` state. It never records rows,
   rights prose or row content. Only the builder can construct a prepared
   handle through the supported API. Interrupted unjournaled objects are never
   served by the controlled origin.
5. For controlled hosting, run from this checkout (substitute canonical paths):

   ```sh
   rtk proxy python scripts/serve-preview-origin.py --public-root /seller/public --journal-root /seller/private --origin https://ai.market
   ```

   The server defaults to 127.0.0.1:8795. Put only this isolated service behind
   the seller's HTTPS reverse proxy. Do not expose the AIM Data application.
   GET/OPTIONS/404/410 all carry the required media type and no-store; no cookies,
   directory listings, compression or query/body access logs are generated.
6. Alternatively copy the exact exported object using the seller's own tools.
   Emit `Content-Type: application/vnd.aim.preview+json` and `Cache-Control:
   no-store` for current viewer compatibility. The external host must implement
   credential-free GET CORS for `https://ai.market` or `*` and retirement. A verified S3
   source connection does not grant these permissions. Presigned URLs are never
   package URLs; automatic S3/R2 uploads are not implemented here.
7. Locally call `verify_hosted_package(url, origin=..., expected_sha256=...,
   expected_bytes=...)`. It resolves all addresses once, rejects nonpublic and
   platform-operated destinations, pins the validated IP and TLS hostname for
   GET and observational OPTIONS, checks JSON parsing and exact decoded package
   bytes, and returns two closed header receipts. It never follows redirects, uses credentials or invokes a
   platform proxy. Extend `operated_hosts` for additional platform domains; the
   default includes ai.market, its subdomains and configured marketplace hosts.
   Receipt bytes are capped at 8,192 each without truncation. Failed verification
   must not be shown as hosted. Persist each receipt with `receipt_bytes` so the
   saved UTF-8 representation is exactly the representation whose size was checked.
   A real browser check remains a release prerequisite.
8. Controlled retirement: call `store.retire(disclosure_uuid, sample_hash)`.
   The private tombstone is persisted before object removal. Replaying an old
   export cannot reactivate it; GET returns 410 with no-store and OPTIONS remains
   available. For a copied object, the seller must remove it themselves. Require
   `verify_hosted_package(..., retired=True)` GET 404/410 and OPTIONS receipts
   before claiming external retirement; local removal alone is insufficient.
   In the 2c journal, completed retries must supply the same exact URL and browser
   origin, even for wildcard CORS receipts. Older journals are migrated without
   inventing the missing origin; already-retired entries without it fail closed
   and require operator reconciliation rather than a claimed retry success.
9. Any changed source, schema, selection, policy identity or rights decision requires a new
   approval. Reconfirm, rebuild and use fresh immutable identities. After Chunk 2c
   signs complete closed proof records, `scan_attestation_digest` hashes those
   signed records in their approved order. It does not sign or verify signatures.

All fixtures and tests in this chunk use synthetic data. Real seller hosting,
external TLS/domain configuration, copied-object retirement and viewer policy
parity are not established by loopback or mocked-transport tests.

## Seller UI and live verified-preview API

The listing detail page retains its three outer steps. Its optional Public sample
panel defaults to **No sample**. **Prepare verified
preview** builds the complete original source, offers immutable leaf selection,
and reviews rights, local policy, publication location and the signing key.
Display-column selection never removes fields from published records. Cells are
plain React text children; HTML, Markdown, spreadsheet formulas and cell links
are not interpreted.

The installed application's existing `/api/marketplace` router mounts
`/preview-builds`. All job operations require authenticated write scope and both
job and dataset ownership. New single/bulk uploads record `preview_owner_id`;
processing preserves that ownership. Historic uploads with no recorded owner are
rejected (`dataset_owner_unverified`); re-upload while signed in. Confirmation or
batch membership is not ownership evidence. This adapter uses an original file
inside the managed upload root. S3/database records require a separately proven
complete source-manifest adapter and fail closed here; a processed sample is
never a substitute.

If parsing declarations are absent, the compact declaration editor accepts the
complete explicit schema and 2a parsing options. No field type or nullability is
silently inferred from sample rows. The current UI accepts these declarations as
JSON; CSV/TSV must include all required parser choices. Unsupported formats remain
ineligible. No client-supplied source or publication filesystem path is accepted.

Jobs are local to one application process, with independent review sessions per
(owner, dataset). Heavy 2a builds remain serialized under the installation worker
flock; waiting builds have a live cancellable session. When computation finishes,
the build flock is released while the review retains only its scoped private
index/flock. Different datasets and owners can therefore review independently.
The scope directory is SHA-256 of the closed owner/dataset tuple, not a supplied
path. Cross-process job dispatch remains unsupported.

A review expires after **1,800 seconds (30 minutes)** since its last authorized
owner API interaction. Set `PREVIEW_REVIEW_IDLE_SECONDS` to a positive finite
number of seconds to configure it; invalid settings fail startup. Background
progress does not renew the lease. An independent idle watcher covers queued,
building and ready reviews. The UI polls only builds, never completed/released
reviews. API status/row/selection interactions renew an unexpired lease.

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
| `packaged` (package written) | 200 / null | Released; index deleted before action returns |
| `signed_candidate` (candidate allocated) | 200 / null | Released; exact ai.market binding and signed request are durable |
| `submitted` (approve accepted) | 200 / live `pending` or `visible` state and listing link | Released; retry replays the identical request |
| `cancelled` (cancel) | 200 / null; published job: 409 / `withdraw_required` | Cancelled build/review released and index deleted before return |
| Withdraw: `withdrawn`, then `retired` | 200 / `external_retirement_pending`, then null; verifier failures retain pending state | Released and index deleted, including pending external retirement |

Job/candidate journals contain metadata, selected proof paths, indices and digests,
never records. A packaged job continues through origin review, candidate signing,
live submission and retirement without reopening an index. Publication bytes retain
the existing 2b journal/download/retirement rules. After process death, startup
cleans orphan private indexes under their locks; unfinished review recovery reports
`expired` / `review_expired`, and the seller explicitly starts a fresh preparation.
An expired review cannot resume stale seller confirmations. Cancellation, package completion,
prepared candidate, submit and withdrawal all release the live session; the heavy
worker's cancellation/termination cleanup completes before a terminal API returns.

Publication roots are beneath the dedicated `preview-builds` directory in the
configured data directory. The exact owner-specific directory and immutable
object path appear on screen. `publications/<owner-hex>` is for the controlled
origin; `exports/<owner-hex>` is separate and is not exposed by that origin. Their
private sibling journals are `publications-journals/<owner-hex>` and
`exports-journals/<owner-hex>`. Configure the isolated origin as above using the
selected controlled directory and its matching journal. Export downloads must be
hosted under the exact displayed `previews/<disclosure>/<sample-hash>.json` path.
No upload, provider configuration, HTTPS proxy or public-access grant is automatic.

After origin verification the UI reads the current backend At a glance preview.
The seller reviews it and, on the explicit prepare action, AIM Data approves that
exact backend summary if it is still pending. It then asks ai.market to allocate
the disclosure candidate, signs the returned binding with the install key, and
submits the closed request. The backend validates install ownership/key status and
returns the decision. AIM Data then reads the public manifest and shows `pending`
or `visible` plus the live listing link. It never invents summary, approval,
content, listing or disclosure identifiers.

Signing reads the existing encrypted install key plus `install_id` and `seller_id`
from the local registration store. There is no operator evidence file or freshness
gate. Revoked, rotated, inactive, wrong-owner or mismatched keys are refused by
ai.market during submission and produce a concrete sign-in/registration fix.

**Refresh attestation** starts another bounded local preparation, retaining source,
leaf/proof identities and sample hash while creating a new package/evidence
revision linked to the backend's predecessor disclosure. Review and
consent are required again. **Retire previous package** is separate. **Withdraw
preview** freezes a newly signed withdrawal when a signed candidate exists,
records retirement pending, invokes the 2c journal/2b store, and checks GET
404/410 plus OPTIONS. External exports require seller removal; failures remain
visibly pending and can be retried. Source files and listing entitlements are
never removed.

On upgrade, a packaged or hosted v1/1.0.0 job is marked
`legacy_v1_completion` and may finish unchanged because rewriting its package or
attestation would break immutable evidence. The backend accepts that legacy
input. Every newly created job emits v2/2.0.0.

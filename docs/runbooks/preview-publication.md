# Seller-local preview publication (Chunk 2b)

Authority: S1294 Chunk 2 build plan S1716 §§C/D/I. This procedure exports
synthetic or explicitly seller-approved complete rows to the seller's own
publication directory. It makes no marketplace request and grants no provider
write authority. Signing and the customer UI remain Chunks 2c/2d.

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
   commitment/disclosure UUIDs, a `PIIService`, scan time, all three consent booleans,
   intended credential-free seller package URL and `manifest_bytes` (the entire
   actual/expected metadata manifest, not a proofs-only size). The service also
   independently budgets a conservative complete manifest with duplicated proofs,
   descriptors, signatures, signer records and maximal log paths. Chunk 2c must
   check actual final signed manifest bytes again. Fewer rows may be needed even
   when the row cap passes. No projected, redacted or edited rows are admitted.
3. Policy 1.0.0 pins Presidio analyzer 2.2.362, spaCy 3.7.2 and model
   en_core_web_sm 3.7.1 (the repository dependency versions). Missing or different
   versions block publication. The supported detector language is English;
   unsupported declared languages fail. Passing local checks is not clearance.
4. Create `PublicationStore(public_root, journal_root)` using canonical local
   paths with no symlink components. The journal directory must be owner-only
   mode 0700 and outside the public root. `export(prepared)` atomically writes
   `previews/<disclosure_uuid>/<sample_hash>.json` and a private mode-0600 journal
   with identities, SHA-256, byte count and `exported` state. It never records rows,
   rights prose or detector matches. Only the builder can construct a prepared
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
   Required object metadata is `Content-Type: application/vnd.aim.preview+json`
   and `Cache-Control: no-store`. The external host must implement credential-free
   CORS, no-store on OPTIONS/errors, no cookies and retirement. A verified S3
   source connection does not grant these permissions. Presigned URLs are never
   package URLs; automatic S3/R2 uploads are not implemented here.
7. Locally call `verify_hosted_package(url, origin=..., expected_sha256=...,
   expected_bytes=...)`. It resolves all addresses once, rejects nonpublic and
   platform-operated destinations, pins the validated IP and TLS hostname for
   GET and OPTIONS, checks exact downloaded bytes, and returns only two closed
   header receipts. It never follows redirects, uses credentials or invokes a
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
9. Any changed source, schema, selection, policy or rights decision requires a new
   approval. Rescan, rebuild and use fresh immutable identities. After Chunk 2c
   signs complete closed proof records, `scan_attestation_digest` hashes those
   signed records in their approved order. It does not sign or verify signatures.

All fixtures and tests in this chunk use synthetic data. Real seller hosting,
external TLS/domain configuration, copied-object retirement and viewer policy
parity are not established by loopback or mocked-transport tests.

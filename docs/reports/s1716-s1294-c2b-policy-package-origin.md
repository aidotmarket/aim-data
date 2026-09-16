# S1716 / S1294 Chunk 2b — policy, package and seller origin

Built on `origin/main` **49985ed34d8a79dd69fec008c946e8feb7acfa12** on branch
`build/bq-listing-enrichment-seller-tools-s1294-c2b-policy-package-origin-s1716`.
No merge, release, marketplace push, signing, UI change, real seller data or
provider mutation was performed. No feature flag was introduced.

## Implementation and milestones

| Milestone | Files / behavior | Commit |
|---|---|---|
| Policy | `app/services/preview_content_policy.py`; strict `PIIService.scan_complete_selection` in `pii_service.py` | `413ef63` |
| Package and selection | `app/services/preview_package_service.py`; complete rows from the live 2a sorted index, proof checks, exact envelope/hash, conjunctive caps, atomic local export and private journal | `d739cec` |
| Origin and receipts | `app/services/preview_origin_service.py`, `scripts/serve-preview-origin.py`; source eligibility explicitly separated in `s3_publish_source_resolver.py` | `7ea2732` |
| Acceptance refinements/tests | Three `tests/test_preview_*.py` suites, frozen fixtures, metadata-only evidence and reproducible mutation tools; stricter consent, pinned detector identity, whole-manifest budget, prepared-handle admission and address admission | `665e281` |
| Report | This report and [local operating procedure](../runbooks/preview-publication.md) | Final documentation milestone |

Every milestone is committed and pushed separately. The final tested implementation
is commit `665e281`; the following report/runbook commit changes documentation only. The acceptance milestone
includes fixes exposed during testing; the earlier implementation commits are
not the final acceptance snapshot. The evidence source hashes identify the tested
implementation independently of subsequent documentation-only commits.

## Deterministic package and selection

The envelope contains exactly `package_profile`, `commitment_id`, `schema_digest`,
`disclosure_version`, `sample_hash`, `entries`; entries and siblings use the closed
contract shapes. Digests remain native Chunk 0 base64url. Only the sample-hash
preimage converts base-row digests to lowercase hex, after decoding. UUIDs must
already be canonical lowercase. The NUL-terminated `aim-approved-sample-v1`
domain is unchanged. The independently generated golden fixture comes directly
from all five 2a golden rows, including missing/null and two distinct duplicate
ordinals. Approved indices must be distinct and ascending; no projection,
redaction, implicit reordering or legacy profile upgrade is available.

Export writes `previews/<disclosure_uuid>/<sample_hash>.json` atomically, with
fsync, no symlink traversal and a private mode-0600 journal outside the public
root. Journal fields are identities, package SHA-256, byte count and state;
no rows or notes. Export reports `exported`, never `hosted`. A tombstone is
persisted before controlled-origin retirement; replay cannot reactivate it.
No connected S3/R2 source permission is treated as write/public-host authority.

### Fixture SHA-256

| File under `tests/fixtures/` | SHA-256 |
|---|---|
| `aim_dataset_merkle_v1.json` (unchanged 2a/backend golden) | `f3e358d1e7ce7c836ce8604810675e0952201a7799b92d30858cd489906499af` |
| `aim_preview_package_v2.json` (exact canonical envelope bytes) | `4397a036ccf4326edf686b68b9d0fe4beb83e73d1dbc81ea09afb3bdf95ca37a` |
| `aim_preview_package_vectors_v1.json` (preimage, sample/order and leaf-list vectors) | `4561ecc2cd9fae5845bcd78b9e5eeae8bb3af8d13f5a9532b99bdf5ffa33787c` |
| `aim_preview_policy_v1.json` (shared synthetic rule corpus) | `ff8c85862c6aa90da271a1bb6204a229266dec2b5218b7b061d187119c22008b` |
| `aim_preview_manifest_budget_v1.json` (conservative whole-manifest budget skeleton) | `fa10ad02a49c5c4d08967d9deb760fbb155676917baab0e08c7ec31a2d89d598` |

The budget skeleton includes duplicated signed-proof lists, descriptors repeated
in the binding, signature/key records, log entry, 63-element inclusion and
consistency paths, checkpoint and outer metadata. It deliberately uses maximal
bounded metadata widths. It is a **size fixture, not valid signed evidence**;
placeholder signatures and repeated maximal signer records grant nothing.
The caller's supplied whole-manifest byte count and the independently constructed
budget must both fit. Chunk 2c must additionally check final actual signed bytes.

### Caps matrix

All comparisons have cap−1 / cap / cap+1 executable cases. Lower and exact values
pass the individual limiter; greater values fail. Passing an individual limiter
does not imply a complete package passes all other limits.

| Limit | Boundary values | Additional exercised behavior |
|---|---|---|
| Rows | 99 / 100 / 101 | Real index selections; 101 rejected by row cap; 99/100 fixtures rejected by duplicated whole-manifest budget |
| Complete fields | 24 / 25 / 26 | Entire declared schema counted even when properties are missing; 26 rejected |
| Combined canonical row bytes | 249,999 / 250,000 / 250,001 | Actual 20-row × 25-field packages, strings below 500; first two pass, last rejected |
| Received envelope bytes | 1,048,575 / 1,048,576 / 1,048,577 | Exact received sizes with legal JSON whitespace; last rejected before parsing |
| Decoded canonical envelope bytes | Same three values | Shared byte limiter plus actual canonical serialization check; not a claim that a valid package can reach each value while satisfying every other cap |
| Whole manifest bytes | 262,143 / 262,144 / 262,145 | Independent conservative full-manifest budget; a legal row budget cannot replace it |
| Siblings per proof | 62 / 63 / 64 | Limiter boundaries; actual paths must also satisfy 2a shape/root/safe-integer constraints |
| Depth | 15 / 16 / 17 | Actual nested values, root depth 0; 17 rejected |
| Total value nodes | 9,999 / 10,000 / 10,001 | Actual containers/scalars/null counted across the envelope; object keys are not value nodes |
| Receipt UTF-8 bytes | 8,191 / 8,192 / 8,193 | Exact serialized closed receipts; 8,193 rejected without truncation |

No compression is admitted. Duplicate JSON keys, extra carriers, duplicate proof
IDs/indices, mismatched schema/rows/root/tree sizes and invalid proof directions
fail closed. Binary selected values are not exported.

## Policy and attestation

`aim-preview-policy-v1`, version **1.0.0**, scans every selected complete row,
including keys, hidden fields, nested objects and array elements. Presidio uses
the full unchanged `DEFAULT_ENTITIES`, threshold 0.5, no exclusions, sampling,
keep action or length skip. Tests place personal data in all 100 row positions
and nested positions. Missing detector/model, unsupported declared language or
detector exception blocks publication without reflecting its diagnostic.

Frozen additional predicates cover personal-data formats in text/numeric values;
secret/token/private-key/connection-string patterns and known token prefixes;
base64url/hex candidate runs ≥24 characters with Shannon entropy ≥4.0 bits/char;
HTML/script/event attributes, scheme URLs/links, formula prefixes, macro patterns,
control/bidi characters, binary, copied attribution/license-conflict indicators,
and strings over 500 Unicode characters or 80 whitespace-delimited words.
Descriptor-proven negative numeric strings remain numeric for the formula rule.
All three explicit confirmations are required: rights, public-preview permission,
and no prohibited/third-party restricted content. They do not override uncertainty.

Pinned local detector identity: Presidio analyzer **2.2.362**, spaCy **3.7.2**,
`en_core_web_sm` **3.7.1**, English. The shared regression environment has an older
Presidio 2.2.33; strict preview admission rejects that mismatch. A separate isolated
2.2.362 import target verified the real pinned engine plus full policy: a safe
synthetic row passed and a synthetic email was rejected. Presidio alone missed
the `.test` email; the deterministic recognizer blocked it. See
[real-policy.json](s1716-s1294-c2b-evidence/real-policy.json). No shared environment
or production dependency was changed. Automatic language identification and
legal clearance are not claimed.

Scan output is only policy/version/literal `passed`, canonical six-fraction UTC
time and ordered sampled-leaf digest. Leaf digest preimage is
`aim-preview-sampled-leaves-v1\0 || J([base64url(reconstructed_leaf), ...])`.
`scan_attestation_digest` separately hashes **already signed closed proof records**
under `aim-preview-scan-attestation-v1\0`. It neither signs nor verifies signatures;
that remains Chunk 2c. No fake unsigned digest is represented as a signed pass.

## Origin, receipt and zero-ingress evidence

The actual loopback HTTP origin was exercised with GET, OPTIONS, missing paths,
unsupported methods, traversal/query attempts and retirement. It serves only
journal-authorized, hash-matching package bytes, with exact media type, no-store,
CORS and no cookies; no directory listing, compression or HTTP access logging.

Seller-local HTTPS verification sends the intended Origin on GET and OPTIONS,
with `Access-Control-Request-Method: GET` and no requested headers on preflight.
All resolved addresses must be public; validated IP and TLS hostname are pinned
across both requests. Private/mixed DNS, multicast/transition addresses, rebinding
peer mismatch, HTTP, userinfo/query/fragment, redirects, missing CORS/no-store,
wrong media, credentials, cookies, compression, changed bytes and oversized
receipts have failure tests. Default platform denials cover ai.market/subdomains
and configured marketplace hosts, supplemented by `operated_hosts`; an unknown
additional operated-domain inventory cannot be inferred from DNS alone.

[Receipt examples](s1716-s1294-c2b-evidence/receipt-examples.json) are explicitly
synthetic metadata-only fixtures, **not live public HTTPS receipts**. Each has
exactly six outer keys and six allowlisted header keys; absent headers are null.
Cookie values, arbitrary headers and response bodies are absent. Real external
HTTPS transport is simulated in transport tests; IP dialing and TLS-hostname
binding are separately verified. Real seller TLS/CORS/browser proof remains open.

Unique synthetic markers remain present only in permitted local package bytes.
Tests verify absence from journals, returned publication metadata, receipt JSON,
captured logs and safe error strings. The new services have no marketplace client
or request path. This does not claim the later producer-wide legacy egress cutoff
or platform storage/cache/SSR proof; those remain 2c/T/Chunk 5 work.

## Validation and parity

| Completed check | Baseline | Candidate |
|---|---:|---:|
| Full aim-data pytest, no exclusions | 2,438 passed / 92 failed / 38 errors / 33 skipped | 2,787 passed / 92 failed / 38 errors / 33 skipped |
| Affected suites | 270 passed / 2 failed | 619 passed / 2 failed |
| Added test execution | — | 349 passed |
| Repository Ruff | 94 existing errors | Identical 94 errors |
| Frontend ESLint | Existing diagnostics | Byte-identical after path normalization |
| Frontend TypeScript | Existing diagnostics | Byte-identical after path normalization |
| Changed Python Ruff / compile / diff whitespace | — | Passed |

Both full suites completed: baseline 148.68s, candidate 153.01s. Every existing
nodeid has the same outcome, with zero branch-only failures, collection errors,
missing tests or added skips. The 38 existing setup errors require the separate
localhost:80 beta deployment. The affected suite's two existing PII scrub endpoint
failures remain present on both refs. Repository-wide checks are not green.
No configured Python type-checking command exists; Python compilation is not
represented as static type checking. No frontend source changed.

Exact committed tables: [full per-nodeid](s1716-s1294-c2b-evidence/full-per-test.json),
[affected per-nodeid](s1716-s1294-c2b-evidence/affected-per-test.json).
Raw outcome inputs and normalized Ruff/lint/type logs are alongside those tables.
Only metadata/outcomes are published, not captured row-bearing tracebacks.

The two specifically requested mutations were executed in isolated source copies:
the sample-hash domain NUL changed to byte 0x01 caused the fixed golden-vector
test to fail; `amount > cap` changed to `amount >= cap` caused all eight exact-cap
cases to fail. Unmodified controls passed. See
[mutation outcomes](s1716-s1294-c2b-evidence/mutations.json) and the reproducible
`docs/reports/s1716-c2b-mutations.py`. Passing execution counts above are separate
from observed-failure evidence; the failure-observation audit records the actual
failed nodeids rather than assuming a test would detect a mutation.

**All 349 added test nodeids were observed failing against at least one explicit
source mutation and passing on the final unmodified implementation.** The audit
used isolated copies and did not modify test assertions. It covered detector/rule/
consent bypasses, digest domains, cap directions, row order, package validation,
receipt/URL/DNS/pinning checks, journal isolation, retirement and source authority.
[Per-mutant failed nodeids and source hashes](s1716-s1294-c2b-evidence/failure-observation-audit.json)
record the evidence. Reproduce with `docs/reports/s1716-c2b-failure-audit.py` plus
the two-mutation script above. An initial audit replacement that matched twice
was not run or counted; its corrected unique replacement was run separately.

## Scope still unverified

- Real seller source/consent, machine, public HTTPS domain, browser CORS, copied
  S3/R2 hosting and external retirement have not been exercised.
- Cross-repository viewer execution of the shared policy/package corpus, Council
  ratification of the proposed policy/leaf-digest semantics, and final independent
  review are not claimed by this build.
- Registered-key signing, final actual signed manifest, marketplace submission,
  legacy egress retirement and UI/worker orchestration remain later chunks. The
  builder must run while the 2a index is alive; the normal 2a metadata-return API
  cleans that index before returning.
- No release, deployment, merge or full S1294/Chunk 2 completion is claimed.

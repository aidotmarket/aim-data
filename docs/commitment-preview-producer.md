# Verified previews from AIM Data

A verified preview is a small set of complete records that you choose from your
full dataset. AIM Data proves those records belong to that dataset and signs
your approval. It does not establish quality, representativeness, legality,
identity or completeness against an external source.

## What stays on your machine

AIM Data reads the complete original dataset, checks its declared schema, builds
a Merkle tree, lets you review complete rows, records your explicit publication
confirmations, and signs the resulting metadata. Source paths, source files,
private keys and rights prose stay local. Temporary row indexes are removed after package
creation, cancellation, failure or review expiry. A changed selection may require
another full build. Review sessions expire after 30 minutes without an authorized
owner interaction by default; an operator can set `PREVIEW_REVIEW_IDLE_SECONDS`.

The preview package contains the **entire selected records**, including fields
hidden by the display-column choice. You must explicitly confirm your rights,
public-preview permission, metadata accuracy and restricted-content review for
that exact selection. You are responsible for what you publish. Preview
publication does not call Presidio, spaCy or deterministic content rules;
missing model packages and version differences do not affect publication.

The preview can contain at most 100 rows, 25 complete-row fields and 250,000
canonical row bytes. All limits apply together. The package must also fit within
1 MiB; the complete metadata manifest, including repeated signed evidence, must
fit within 256 KiB. Nested structures have depth/node limits, and proofs have at
most 63 siblings. A selection may need fewer rows even if it meets the row cap.
AIM Data never silently trims, edits or masks an approved record.

## What leaves your machine

Only the approved row package goes to your chosen public seller origin. Visitors
read it directly, anonymously and without cookies. The marketplace receives the
closed metadata contract: hashes, complete schema descriptors, proofs, signatures,
rights/scan digests, your permission, and the package URL. It must not receive
records, snippets, local paths, scan notes or rights prose. There is no platform
proxy, server-rendered row fetch or cached-row fallback.

**Before marketplace support is deployed, the current state is “Prepared locally;
marketplace preview submission awaits backend support.”** Local candidate IDs,
approval references and expected platform records are non-runtime fixtures. They
are not platform allocations, approvals or proof of a live public listing sample.
Ordinary listing publication keeps the existing No sample decision. Preparing or
retrying a preview does not republish the listing. Legacy row samples are refused;
old No sample decisions are never silently converted into consent.

## Preparing a preview

In the dataset detail page, approve the listing metadata, then choose **Prepare
verified preview** in the optional Public sample panel. No sample remains the
default. Provide the full parser/schema declaration when requested, wait for the
complete build, and review the selected leaf-index rows and every field. Confirm
rights and permission, record the seller confirmations, select the publication directory or
export option, then verify the exact origin and signing fingerprint.

New uploads retain their authenticated owner. Historic uploads without a verified
owner require re-upload while signed in. Local preview jobs require ownership and
write scope; the production marketplace mount also retains its existing admin
authentication gate. A source bucket connection does not prove preview-host write,
public access or deletion authority. The UI's S3/database source adapter currently
requires a complete source-manifest resolver and fails closed without one.

CSV/TSV, JSON arrays, NDJSON/JSONL and Parquet are supported with explicit parsing
and complete schema declarations. Missing JSON properties remain different from
explicit nulls. CSV/TSV require UTF-8, delimiter, quote/escape, header, locale and
null-token declarations. Timestamps require an unambiguous UTC/offset declaration.
No source preview or inferred sample schema substitutes for the full dataset.

**FLOAT/DOUBLE/REAL are ineligible**, even when finite. There is no approved
binary-float type in this profile. Supply a separate, lossless source explicitly
declared as `DECIMAL(p,s)` with exact precision/scale, then review and rebuild it.
Changing only a label or silently casting an existing float column is not a
workaround. NaN/infinity, unsupported extensions, MAP/UNION, fixed-size ARRAY,
UUID/ENUM semantics, opaque JSON cells, TIME/INTERVAL and unresolved types are
also refused. Excel and document discovery do not make those formats eligible
for this commitment path. Binary can be committed but cannot appear in a selected
public row. More than 25 committed fields makes every complete row ineligible;
hiding columns does not change this.

## Hosting and receipts

Use your own authorized, public HTTPS origin, with no userinfo, query, fragment,
redirects or private-network destination. The URL must be at most 2,048 characters.
Do not use ai.market or its subdomains, a platform-operated host, a presigned URL,
or an R2 development URL. Automatic S3/R2 uploads or permission changes are not
provided. Exported bytes must be hosted unchanged at the displayed path:
`previews/<disclosure UUID>/<sample hash>.json`.

The controlled origin supplied by AIM Data can sit behind your HTTPS reverse
proxy. Expose only that isolated origin, never the main application. Configure
these compatibility headers on successful GET, OPTIONS and retirement responses:

| Header | Required value |
|---|---|
| `Content-Type` | `application/vnd.aim.preview+json` |
| `Cache-Control` | `no-store` |
| `Access-Control-Allow-Origin` | `https://ai.market` (or `*` for credential-free public access) |
| `Access-Control-Allow-Methods` | `GET, OPTIONS` (OPTIONS must permit GET) |
| `Access-Control-Allow-Headers` | May be absent; this GET requests no custom headers |
| `Access-Control-Allow-Credentials` | Absent or `false`, never `true` |

The controlled origin does not send `Set-Cookie` or compress the response and
disables response-body logging. External origin admission requires a
credential-free GET, CORS allow-origin `https://ai.market` or `*`, a JSON-parsable
body, and the exact expected decoded bytes/hash. Media type, no-store, cookies,
compression and OPTIONS behavior are recorded observations rather than refusals.
The verifier validates public DNS and pins the address and TLS hostname. A real anonymous browser check is
still required at Gate-4.

Each GET/OPTIONS receipt has exactly `url`, `method`, `status`, `captured_at`,
`headers`, `no_set_cookie`. The headers object has exactly the six lowercase
header names in the table, with `null` for missing values. `no_set_cookie` must be
true. Each canonical UTF-8 receipt is at most 8,192 bytes. No response body,
cookie value, arbitrary headers or truncated values are retained.

## Keys and changes over time

Use the installation's existing encrypted Ed25519 key. Never create a replacement
key to get past missing registration evidence. The operator supplies fresh,
owner-authorized registry readback with exactly `install_id`, `seller_id`,
`fingerprint`, `status`, `observed_at`. The signer rereads it before every signature,
requires active status, matches owner/install/public-key fingerprint, and requires
an observation less than one hour old. An install ID alone is insufficient.
Neither the UI nor the evidence tool invents a registry endpoint or proves the
provenance of a manually supplied readback. Real readback remains Gate-4 evidence.

Rotation needs a new registered reference and signatures. Unknown, wrong-owner,
rotated, revoked, stale or mismatched evidence blocks signing. Keys supplied by a
package are never authority. Platform-envelope/checkpoint signatures in the local
evidence bundle use a clearly named **public synthetic test key**, solely for
contract verification; never install that key in a runtime trust store.

Withdrawal creates a newly signed candidate identifying the current head; it
never reuses an approval signature. The controlled origin tombstones and removes
the package. For externally copied packages, remove them yourself and verify GET
404/410 plus OPTIONS with no-store before declaring retirement complete. Old
saved packages cannot restore a withdrawn or superseded current head.

Refresh creates a new immutable evidence revision linked to the same sample and
commitment, with renewed seller consent/time/cadence. Supersession explicitly
revokes the prior head. Changes to dataset values, schema, membership or duplicate
counts require a full rebuild, fresh proofs and new approval. Do not relabel an
old package. After T exists, every live action must use its newly allocated
candidate; the fixture candidates cannot be promoted by changing their labels.

A preview becomes stale at `last_attested_by_seller_at` plus
`min(90, max(7, 2 × update_cadence_days))` days. With no cadence, use 90 days.
It is stale exactly at that boundary. Stale is a label, not automatic hard expiry.
Without an authorized expiry policy, `freshness_expires_at` remains null. An
explicit approval, summary or policy expiry can make it ineligible. Platform
freshness is derived; it is not inserted into the seller-signed binding.

## Building the operator evidence bundle

Run from the exact approved producer checkout using its installed Python
dependencies. Use canonical absolute paths with no symlink components (on macOS,
`/private/var/...` rather than `/var/...`). The source is one complete local file;
the declaration JSON has exactly `parsing` and `schema_descriptors`. Review the
selected sorted leaf indices locally first. Supply the existing keystore
passphrase through the operator's protected environment as
`PREVIEW_KEYSTORE_PASSPHRASE`; never put it on the command line or in the bundle.

```sh
rtk proxy python scripts/build_preview_producer_evidence.py build \
  --dataset /seller/source.ndjson --declaration /seller/declaration.json \
  --origin https://preview.seller.example --output /seller/evidence-new \
  --registration-evidence /seller/private/registry-readback.json \
  --keystore /seller/private/keystore.json \
  --rights-file /seller/private/rights.txt --rights-code owner \
  --leaf-indices 0,1 --confirm-public-preview
```

The confirmation asserts exact-row rights, public permission, metadata accuracy
and restricted-content review. The output directory must be new. The tool performs
the bounded full worker build, seller-attested v2 policy record, package export, signing and
signature verification; it never sends a marketplace request. Source bytes and
parser options are hashed without exporting their paths. Rights prose is digested
and not copied. The `.private` directory contains local journals, not exported
keys; temporary full-row indexes are cleaned by the worker.

The bundle contains the local candidate, signed commitment/proofs/disclosure,
complete descriptors, seller attestation, registration readback, package,
manifest-size receipt, all five F2 preimages, and explicitly synthetic expected
platform envelope/log/checkpoint. It also constructs signed withdrawal, refresh
and supersession candidates and records local retry/409/head and stale-boundary
expectations. Each exported artifact has a SHA-256 in `manifest.json`.

After hosting the exact exported package using the authorized seller tools:

```sh
rtk proxy python scripts/build_preview_producer_evidence.py check-host --output /seller/evidence-new
```

For a controlled origin, serve `/seller/evidence-new/public` with the matching
journal `/seller/evidence-new/.private/publication` using
`scripts/serve-preview-origin.py`. The operator independently supplies HTTPS.
When the seller has authorized retirement:

```sh
rtk proxy python scripts/build_preview_producer_evidence.py retire --output /seller/evidence-new
```

This removes the local hosted package and checks remote absence. A copied remote
object must also be removed by the seller. Failed checks never create successful
receipts; external retirement remains pending. Save any authorized local package
inspection evidence before retirement; the manifest then describes the remaining
metadata and receipt files, while `publication.json` retains the package checksum.

Keep the bundle on the seller machine: it contains public row bytes until
retirement. Central evidence must contain only reviewed identities, hashes,
counts, timestamps, headers and outcomes. No secrets or real rows belong in Git.
The CI driver uses deterministic test identities, the seller-attested policy path and a real
loopback HTTP origin; its receipts explicitly do not prove public HTTPS or real
owner registration. Only its SHA reference is committed.

The release controller follows [release readiness](release-aim-data-v1.24.0.md).
Real owner-bound readback, Sergey's dataset/consent, live HTTPS receipts, actual
RC/stable installation and the later T integration remain separate gates.

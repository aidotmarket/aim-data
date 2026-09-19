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

AIM Data sends this metadata contract directly to ai.market. It first reads the
current At a glance draft, lets the seller review it, approves that exact backend
record when needed, asks ai.market to allocate the preview candidate, signs the
returned binding, and submits it. Candidate, summary, approval, content and
listing identifiers therefore come from ai.market rather than local placeholders.
Preparing or retrying a preview does not republish the listing.

## Preparing a preview

In the dataset detail page choose **Prepare verified preview** in the optional
Public sample panel. No sample remains the default. Provide the full parser/schema
declaration when requested, wait for the
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
null-token declarations. For ordinary RFC4180 CSV, declare an empty escape; doubled
quotes inside a quoted field remain the quote convention:

```json
{"format":"csv","encoding":"utf-8","delimiter":",","quote":"\"","escape":"","header":true,"locale":"C","null_token":""}
```

An equal quote/escape declaration selects the same RFC4180 doubled-quote parser
mode as an empty escape. This does not preserve the old interpretation of quote
characters inside unquoted fields: the old parser consumed one such character as
an escape, while the RFC4180 mode retains it. A genuinely distinct escape character
remains active. Timestamps require an unambiguous UTC/offset declaration.
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

AIM Data signs with the installation's existing encrypted Ed25519 key and the
`install_id` and `seller_id` already stored by sign-in and registration. No
operator file or freshness ceremony is part of preview publication. ai.market is
the authority: submission resolves the install registration and refuses a
revoked, rotated, inactive, wrong-owner or mismatched key. Sign in and register
the install again if that happens; never create an unregistered replacement key
to bypass it. Keys supplied by a package are never authority.

Withdrawal creates a newly signed candidate identifying the current head; it
never reuses an approval signature. The controlled origin tombstones and removes
the package. For externally copied packages, remove them yourself and verify GET
404/410 plus OPTIONS with no-store before declaring retirement complete. Old
saved packages cannot restore a withdrawn or superseded current head.

Refresh creates a new immutable evidence revision linked to the same sample and
commitment, with renewed seller consent/time/cadence. Supersession explicitly
revokes the prior head. Changes to dataset values, schema, membership or duplicate
counts require a full rebuild, fresh proofs and new approval. Do not relabel an
old package. Every live action uses its newly allocated ai.market candidate.

A preview becomes stale at `last_attested_by_seller_at` plus
`min(90, max(7, 2 × update_cadence_days))` days. With no cadence, use 90 days.
It is stale exactly at that boundary. Stale is a label, not automatic hard expiry.
Without an authorized expiry policy, `freshness_expires_at` remains null. An
explicit approval, summary or policy expiry can make it ineligible. Platform
freshness is derived; it is not inserted into the seller-signed binding.

## Synthetic contract evidence

The repository's producer-evidence script and fixtures are CI-only contract
oracles. They use deterministic test identities and a loopback origin and never
submit to ai.market. They are not part of the seller runtime, do not authorize a
listing, and must never be relabelled as live marketplace proof.

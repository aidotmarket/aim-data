# BQ-AIM-DATA-PUBLISH-SWEEP-SCOPE-S958 — Gate 1 (Design, Security-Critical) APPROVED

Branch: build/bq-aim-data-s711-c2c2-emit-and-hash
Target file: app/routers/marketplace_publish.py
Session: S958 (vulcan)
Gate 1 verdict: binding 3/3 APPROVE — MP gpt-5.5 APPROVE_WITH_MANDATES, DeepSeek v4-pro APPROVE, AG gemini-3.5-flash APPROVE; XAI grok-4.3 (comparison) APPROVE_WITH_NITS. No dissent, no veto.

## Problem
`_assert_no_sensitive_publish_values(payload)` recurses the ENTIRE publish payload before it is JCS-hashed, Ed25519-JWT-signed, and POSTed to ai.market. It raises HTTP 409 on:
- any dict key matching SENSITIVE_TOKEN_RE = (external[_-]?id|secret|token) unless in ALLOWED_SENSITIVE_KEY_NAMES={pricing_type};
- any string value matching VZ_SERIAL_RE, HMAC_HEX_RE, or SENSITIVE_TOKEN_RE.
Because it scans seller-controlled metadata, it false-positive-blocks legitimate publishes: a column literally named external_id/token/secret, or a title/description/tag containing those words or a 32+ hex run. Pre-merge regression on the C2c2 branch; not on main.

## Settled design — build exactly this
1. Stop recursing the whole payload.
2. Remove SENSITIVE_TOKEN_RE entirely (both the key-name check and the value word-match) and remove the now-dead ALLOWED_SENSITIVE_KEY_NAMES constant. The only legitimate carrier of such material is the s3_connection block, already strictly typed (S3ConnectionPublishEmit, extra="forbid"), so the word match protects nothing and is the dominant false-positive source.
3. Do NOT scan the s3_connection block. Its serial_id/role_arn are intentional publish material ai.market is meant to receive.
4. Keep an accidental-credential value guard matching ONLY VZ_SERIAL_RE and HMAC_HEX_RE, applied ONLY to these seller-controlled top-level payload fields: title, description, tags (list of str — check each element), category, vz_raw_listing_id. (vz_raw_listing_id is caller-derived from vz_dataset_id, hence user-controlled.) Skip keys absent from the payload (exclude_none drops them).

Keep VZ_SERIAL_RE and HMAC_HEX_RE unchanged. 409 detail string stays "Publish payload contains sensitive material".

## Implementation notes
- Apply the guard at the existing call site after `payload = _build_publish_payload(body, s3_source)`, operating on the built dict so vz_raw_listing_id is present and s3_connection is excluded by name.
- For `tags`, iterate and check each string element. For string fields, check the string. Non-string/absent values: skip.

## Test plan — MANDATE, all required
Extend the existing publish-router test module:
1. column_names=["external_id","token","secret"] + otherwise-clean fields -> no 409.
2. title="Trade Secrets Dataset", a tag "token", description mentioning "external id" -> no 409 (proves word-match removed).
3. description containing a VZ serial (e.g. "VZ-ABC1234XY") -> 409.
4. description containing a 32+ char hex run -> 409.
5. vz_dataset_id that renders vz_raw_listing_id containing a VZ serial -> 409 (guards caller-derived field).
6. Valid S3 publish with serial-shaped s3_connection.serial_id -> no 409 (s3_connection not scanned; intentional material).
7. S3ConnectionPublishEmit rejects an unexpected extra field (extra="forbid") -> assert (may already exist).

## Scope guard
Modify ONLY app/routers/marketplace_publish.py and its test module. Do NOT touch the S3 resolver, crypto, JWT build, JCS hashing, or the publish POST. Do NOT change s3_connection construction or the S3ConnectionPublishEmit model.

## Output contract
Report: files changed, tests run + results, risks/known issues, whether scope was strictly followed.

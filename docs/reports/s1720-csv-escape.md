# S1720 CSV escape and seller error report

## Outcome

An ordinary RFC4180 CSV now builds when its declaration uses either an empty
escape or an escape equal to the quote character. The two declarations produce
the same records and commitments for ordinary RFC4180 quoting, including doubled
quotes inside quoted fields. By Max's binding 2026-09-19 owner decision, this is
not universal compatibility with the old equal-escape parser: a quote character
inside an unquoted field is now retained instead of consumed, and a delimiter
escaped by a quote character is now refused with `csv_parse_error`. A genuinely
distinct escape character remains active. The Public sample panel defaults its
editable CSV declaration to an empty escape and shows actionable, non-reflecting
failure messages.

The branch started from `origin/main` commit
`316f9898437814d5a0e90eccc2bcf453df4aafa2`, tagged `aim-data-v1.24.1`.

## Confirmation and dialect rule

`ParsingDeclaration.validate` accepts CSV/TSV only with UTF-8, C locale, a
boolean header choice, an explicit null token, one-character delimiter and
quote, and an empty or one-character escape. It therefore accepts
`escape == quote`.

On the pinned Python 3.11 runtime, the ordinary quoted-field corpus succeeds
with an empty escape and a distinct backslash escape, but `escape == quote`
raises `csv.Error: unexpected end of data`. For that quoted-field corpus, this
is the only failure among the normal empty/equal/distinct escape choices. The
host Python 3.13 runtime rejects the equal pair earlier with
`ValueError: bad escapechar or quotechar value`.

The implemented rule is: **when CSV/TSV escape equals quote, pass no separate
escape character to `csv.reader`; otherwise pass the declared non-empty escape
unchanged**. Python's default doubled-quote handling then implements RFC4180.
An empty escape continues to mean no separate escape character.

The validator also permits delimiter equal to quote or delimiter equal to a
distinct escape. Those combinations do not have the same Python 3.11 failure;
they are ambiguous dialects and normally reach the existing `invalid_header` or
`invalid_record` checks. Python 3.13 rejects them during reader construction,
which is now reported as `csv_parse_error`. They were not newly refused because
doing so could change behavior for a file that already parsed, contrary to the
compatibility requirement.

## Seller-facing failures

Worker failures now carry an optional safe message alongside the stable code;
neither source values nor absolute paths cross the worker boundary or appear in
the UI.

| Code | Seller-facing message |
|---|---|
| `csv_parse_error` | `CSV parsing failed near line N. Check the delimiter, quote, and escape settings, then try again.` Reader-construction failures instead say the CSV dialect is invalid and explain the ordinary/distinct escape choices. |
| `source_encoding_error` | `The source is not valid UTF-8 near line N. Save or export it as UTF-8, then try again.` |
| `invalid_source` | `The source file could not be read. Re-upload it or restore access, then try again.` |
| `source_changed` | `The source changed while the preview was being built. Start a new preview build.` |

`invalid_source` retains its unreadable-source meaning. Existing
`record_resource_limit`, `invalid_record`, `invalid_header`, `duplicate_field`
and `source_changed` codes are unchanged. Empty headers now reach the existing
`invalid_header` code instead of falling through a generic read failure.

## Scoped byte and hash equivalence

`test_csv_equal_quote_escape_is_rfc4180_and_hash_equivalent` parses a CSV with a
comma inside a quoted field and an RFC4180 doubled quote under both declarations.
It compares the complete tuple of canonical row bytes, schema digest and Merkle
root and requires exact equality for that ordinary RFC4180 input. The test also
pins the decoded record values; it does not claim equivalence for every byte
sequence the old equal-escape parser happened to accept.

`test_csv_distinct_escape_character_is_honoured` proves a backslash escape still
decodes an escaped quote. The unchanged shared real-file corpus, the complete
preview/commitment matrix, fixture parity, differential verifier and synthetic
producer bundle provide regression proof outside the two owner-narrowed legacy
classes and for the downstream commitment artifacts. No signing, attestation, Merkle,
`sampled_leaf_list_digest`, marketplace transport or content-policy logic was
changed.

## R2 fold

The corrected equivalence claim is narrow: an empty escape and an escape equal
to the quote character select the same RFC4180 parser mode in this version, so
they produce the same records and commitment tuple for inputs interpreted the
same way by that mode. They are not universally byte- or hash-equivalent to the
released v1.24.1 equal-escape behavior.

Max made the binding owner decision on 2026-09-19 to ship that clean RFC4180
reading; it is recorded under Event Ledger
`062a82b6-3eb0-405c-a538-c26740a5859d` in
`docs/decisions/2026-09-19-equal-escape-rfc4180.md`. Compatibility with
v1.24.1 is deliberately narrowed for exactly two previously successful input
classes under an equal-escape declaration:

- A quote character inside an **unquoted** field is retained. For example,
  `value\nx""y\n` decoded as `x"y` before and now decodes as `x""y`, changing
  the canonical row, leaf hash and Merkle root.
- A delimiter escaped by a quote character is refused. For example,
  `value\na","a\n`, which v1.24.1 read as `a,a`, now fails with
  `csv_parse_error`.

The owner's reasoning is that treating the quote character as a separate escape
character is not a real CSV dialect and produced records no standard parser
agrees with. He rejected persisting a per-job dialect version because that adds
signing-path machinery for a case with no evidence, and rejected trying RFC4180
then falling back because it rescues the input that now errors but not the input
where both readings succeed and disagree.

`test_csv_equal_quote_escape_preserves_quotes_in_unquoted_field` pins the new
behavior with that counterexample. It requires the canonical value `x""y`, the
schema digest `825399d361e38fb1b2d104f84f32255426953977f5a7e20f3a5c8597310d0e9f`,
and Merkle root
`d4a04191a9ec02aafd107ca120deabb3f8966b3cb548eff69ba09c5ac7236649`.
The existing quoted-field equivalence test remains in place.

The production evidence weighed by the owner was two dataset commitments and 22
preview proofs, all created on 2026-09-19 with an empty escape. The
verified-preview path had produced no commitment before the preceding night,
and the seller run's equal-escape attempt failed before producing any records.
Therefore no known production artifact was created under the old equal-escape
interpretation.

A seller in another installation may nevertheless have a preview built by
v1.24.1 from one of these two input classes under an equal-escape declaration.
After upgrade, a rebuild either retains the unquoted quotes and produces a
different root, or refuses the quote-escaped delimiter with `csv_parse_error`.
The seller must follow this migration procedure:

1. Withdraw the old preview and complete retirement of its published package.
2. If the source used quote-escaped delimiters, re-export or correct it to valid
   RFC4180 first. Rebuild with an empty escape or the now-equivalent equal-escape
   declaration, producing fresh immutable commitment and disclosure identities.
3. Obtain fresh seller approval for the rebuilt preview.
4. Submit the replacement.

R2 validation before the single push:

- `rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/private/var/tmp/s1720-r2-serial.hpzV83 VECTORAIZ_DATA_DIRECTORY=/private/var/tmp/s1720-r2-data.Hs9jgL VECTORAIZ_UPLOAD_DIRECTORY=/private/var/tmp/s1720-r2-uploads.2tytHn VECTORAIZ_PROCESSED_DIRECTORY=/private/var/tmp/s1720-r2-processed.1dWwEz DATABASE_URL=sqlite:////private/var/tmp/s1720-r2.db /var/tmp/aim-data-s1719-venv/bin/python -m pytest -q tests/test_dataset_canonicalization.py tests/test_preview_build_routes.py --tb=short` — **passed: 180 tests**, 27 dependency deprecation warnings.
- `rtk ruff check tests/test_dataset_canonicalization.py` — **passed, no issues**.
- `rtk proxy /var/tmp/aim-data-s1719-venv/bin/python scripts/check_preview_fixture_parity.py` — **passed, 15 pinned files**; backend SHA pin checked because no backend copy was supplied.
- `rtk git diff --check` — **passed**.

## R3 owner-decision fold

The binding compatibility decision is in the new sibling decision record
`docs/decisions/2026-09-19-equal-escape-rfc4180.md`. A CSV-specific sibling is
more discoverable to future parser maintainers than appending this unrelated
dialect decision to the existing no-content-gate record. It names Max, the date
and Event Ledger id; records both narrowed input classes, the reasoning and both
rejected alternatives; and contains the complete four-step migration procedure.

`test_csv_equal_quote_escape_refuses_quote_escaped_delimiter` closes the R2
coverage gap. It pins `value\na","a\n` under an equal-escape declaration as a
`csv_parse_error` and requires the exact seller-facing line-2 message. Together
with the existing `x""y` regression, both deliberately changed legacy classes
are guarded. No parser, error code, error message or runtime path changed in R3.

R3 validation before the single push:

- `rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/private/var/tmp/s1720-r3-serial VECTORAIZ_DATA_DIRECTORY=/private/var/tmp/s1720-r3-data VECTORAIZ_UPLOAD_DIRECTORY=/private/var/tmp/s1720-r3-uploads VECTORAIZ_PROCESSED_DIRECTORY=/private/var/tmp/s1720-r3-processed DATABASE_URL=sqlite:////private/var/tmp/s1720-r3.db /var/tmp/aim-data-s1719-venv/bin/python -m pytest -q tests/test_dataset_canonicalization.py tests/test_preview_build_routes.py --tb=short` — **passed: 181 tests**, 27 dependency deprecation warnings.
- `rtk ruff check tests/test_dataset_canonicalization.py` — **passed, no issues**.
- `rtk git diff --check` — **passed**.

## UI and documentation

The Public sample declaration editor now opens with ordinary CSV settings using
`"escape": ""`, explains doubled quotes and distinct escapes, and renders the
safe build message instead of only the code. The producer guide and publication
runbook now give the correct empty-escape declaration and state the compatibility
rule. The binding decision records Max's 2026-09-19 extension verbatim.

## Files changed

- CSV parsing and safe failures:
  `app/services/dataset_canonicalization.py`,
  `app/services/dataset_merkle_service.py`,
  `app/services/preview_build_service.py`.
- Public sample UI and type:
  `frontend/src/components/CommitmentPreviewBuilder.tsx`,
  `frontend/src/lib/api.ts`.
- Tests: `tests/test_dataset_canonicalization.py`,
  `tests/test_preview_build_routes.py`,
  `frontend/src/components/CommitmentPreviewBuilder.test.tsx`.
- Documentation: `docs/commitment-preview-producer.md`,
  `docs/runbooks/preview-publication.md`,
  `docs/decisions/2026-09-18-no-content-gate.md`,
  `docs/decisions/2026-09-19-equal-escape-rfc4180.md`, and this report.

## Validation

- `rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/private/var/tmp/s1720-final-serial.tG0T6w VECTORAIZ_DATA_DIRECTORY=/private/var/tmp/s1720-final-data.PRSOfi VECTORAIZ_UPLOAD_DIRECTORY=/private/var/tmp/s1720-final-uploads.Z3UsF9 VECTORAIZ_PROCESSED_DIRECTORY=/private/var/tmp/s1720-final-processed.RKPu2L DATABASE_URL=sqlite:////private/var/tmp/s1720-final.db /var/tmp/aim-data-s1719-venv/bin/python -m pytest -q tests/test_dataset_canonicalization.py tests/test_dataset_merkle_service.py tests/test_preview_content_policy.py tests/test_preview_package_service.py tests/test_preview_origin_service.py tests/test_preview_signing_service.py tests/test_preview_marketplace_transport.py tests/test_preview_disclosure_schemas.py tests/test_marketplace_commitment_push.py tests/test_preview_lifecycle.py tests/test_preview_build_routes.py tests/test_vz_publish_proxy.py tests/test_dataset_publish_signed_proxy.py tests/test_s804_disclosure_dataset_detail.py tests/test_preview_fixture_parity.py tests/test_preview_producer_evidence.py --tb=short` — **passed: 575 tests**, 34 dependency deprecation warnings.
- From `frontend`, `rtk proxy env NODE_OPTIONS=--no-experimental-webstorage npm test -- --run src/components/CommitmentPreviewBuilder.test.tsx src/components/PreviewOriginReview.test.tsx src/lib/disclosure.test.ts src/pages/DatasetDetail.test.tsx` — **passed: 66 tests in 4 files**; existing React `act()` and React Router warnings only.
- From `frontend`, `rtk npx tsc --noEmit` — **passed**.
- From `frontend`, `rtk npm run build` — **passed**, 3,107 modules; existing Browserslist-age and large-chunk warnings only.
- `rtk ruff check app/services/dataset_canonicalization.py app/services/dataset_merkle_service.py app/services/preview_build_service.py tests/test_dataset_canonicalization.py tests/test_preview_build_routes.py` — **passed, no issues**.
- `rtk proxy /var/tmp/aim-data-s1719-venv/bin/python scripts/check_preview_fixture_parity.py` — **passed, 15 pinned files**; backend SHA pin checked because no backend copy was supplied.
- `rtk node tests/preview_differential_check.cjs` — **passed: 8 accepted digests and 6 required rejections**.
- `rtk proxy env AIM_DATA_SERIAL_DATA_DIR=/private/var/tmp/s1720-final-synthetic.SVneVS/serial VECTORAIZ_DATA_DIRECTORY=/private/var/tmp/s1720-final-synthetic.SVneVS/data VECTORAIZ_UPLOAD_DIRECTORY=/private/var/tmp/s1720-final-synthetic.SVneVS/uploads VECTORAIZ_PROCESSED_DIRECTORY=/private/var/tmp/s1720-final-synthetic.SVneVS/processed DATABASE_URL=sqlite:////private/var/tmp/s1720-final-synthetic.SVneVS/app.db /var/tmp/aim-data-s1719-venv/bin/python tests/run_preview_producer_synthetic.py --output /private/var/tmp/s1720-final-bundle.110Sd9/bundle --reference tests/fixtures/preview-producer-synthetic-shas.json` — **passed**, including row-egress checks; no live owner/HTTPS proof is claimed.
- `rtk git diff --check` — **passed**.

The first touched frontend-test attempt could not start because the new worktree
had no `node_modules` (`vitest: command not found`). `rtk npm ci` installed the
existing lockfile-defined dependencies; it reported 25 existing audit findings.
No dependency or lockfile change and no `npm audit fix` was made.

## Risks

- Reported CSV locations are physical line numbers and are approximate for
  multiline quoted records; they intentionally do not include row values.
- Delimiter collisions remain validator-compatible but are not recommended
  declarations. No universal byte/hash-stability claim is made for an
  equal-escape declaration containing quotes in unquoted fields or a delimiter
  escaped by a quote character.
- This is local automated proof. No production dataset, marketplace submission,
  provider configuration or external host was touched.

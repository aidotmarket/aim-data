# S1717 chunk E — AIM DATA builder report

2026-09-17. Branch: `build/bq-multi-file-datasets-s1717-e`. Base: `7dc92619c2cecce4c7492d1eed823fcaf9ee4328` (chunks A/B/C). Implementation commits: `48fa436`, `eb70827`; tests and this report are the following commit. No PR, migration, production enablement, secret access, sender edit or connector edit.

## Authority and result

Fetched both repositories. Read runbooks through `git show origin/main:<path>` at `a64d6665418a8e90dd25b4501d3265240d635862`: `specs/BQ-MULTI-FILE-DATASETS-S1717-GATE2.md`, especially §4.5 lines 120–126 and §6 lines 145–178; and `specs/BQ-DATA-VERIFICATION-S1590-DIRECTORY-ROOT-AMENDMENT.md`, including §1.3–1.4 lines 19–25 and §2–3 lines 27–49. Route (a) item-level approval on `30b734d7` is recorded at amendment line 3.

Implemented retained composite-key resolution, typed directory locator and path-bound content digest, presence-discriminated signed specs, and scanner-side per-member adaptation. Every data member is streamed in published index order before facts. Connector-fed members are reopened with no-follow descriptor traversal, size/SHA-256 verified, stat-bracketed, and checked against the current in-root path. The connector receives those exact bytes. Oversize and unsupported members remain visible skips; ZIP bytes never reach the connector. Objects, skips and previews are sorted by object id. Observed coverage count and aggregate inference budget fail closed. Existing terminal-failure orchestration is reused; no charging code changed.

## Files

- `app/services/data_verification_local_service.py`: directory artifact subtype, retained resolver, directory availability and quote probe using the locally recorded active publication.
- `app/services/data_verification/scanner.py`: two-pass reads, bound, member identity override, skip handling, canonical report assembly, and refusal of directory scans lacking version fields.
- `app/services/data_verification/contract.py`: paired non-null version/hash fields, strict extra-field rejection, serializer omitting both fields on legacy specs.
- `app/services/dataset_manifest.py`: locator and path-framed content hash helpers.
- `tests/test_data_verification_resolver.py`, `tests/test_data_verification_scanner.py`: AC5 vectors, retained/re-upload resolution, composite parity, privacy, mutation/race/escape/count failures, cap boundary, runtime skips, old-install refusal, flag-off refusal, registration-deletion retention, single-member scan, and base-byte legacy comparison.
- `tests/test_data_verification_local_service.py`: one superseded chunk-A probe test now requires refusal without a retained publication; its original-byte success case is covered through the published directory AC5 fixture.
- `tests/fixtures/multi_file_datasets/directory_verification_golden.json`: synthetic portable vectors for §1.3(a)–(c), two CSV data members, a ZIP disguised as CSV, documentation and other members.
- This report.

Frozen connector Git blob remains `248de97a568dc3f92e442bbf609a3047659ef1b5`; a test compares its actual bytes against the base Git object. `fulfillment_service.py` has zero diff against base. Existing report/schema fixtures were not modified.

## Validation and exact counts

Final regression command (default flag off; directory tests explicitly enable it):

```sh
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q tests/test_data_verification_resolver.py tests/test_data_verification_scanner.py tests/test_data_verification_local_service.py tests/test_data_verification_router.py tests/test_data_verification_enabled_default.py tests/test_data_verification_sanitizer.py tests/test_dataset_manifest.py tests/test_dataset_publish_signed_proxy.py tests/test_member_upload_client.py --tb=short --junitxml=/tmp/s1717-e-final.xml
```

**282 passed, 5 warnings, 9.19 seconds.** Log: `/tmp/s1717-e-final.log`; JUnit: `/tmp/s1717-e-final.xml`. Includes byte-for-byte comparison of complete signed local and S3 reports against the scanner loaded directly from the base, legacy signed-spec round trips, the existing backend-folded golden report, and connector immutability.

Additional read-only interop check: generated the directory AC5 report with the test factory and loaded the unchanged backend `SuccessfulVerificationReport` directly from `/Users/max/Projects/ai-market/ai-market-backend/app/schemas/data_verification.py` at `8ed234af49b3ba7d8fa84e363c5fe8263f40a23a`: **1 report accepted**. Log `/tmp/s1717-e-backend-validation.log`. No backend files changed.

Full suite is **not green / not completed**. An exploratory run was interrupted to preserve the dispatch time bound; it had failures/errors and produced no reliable final totals. It is not counted as passing evidence. A separate candidate run with `--maxfail=1` completed with **12 passed, 1 failed** in 7.24 seconds (`/tmp/s1717-e-first-failure.xml`). The first failure is `tests/test_aim_data_deployment.py::test_aim_data_compose_uses_aim_data_image_and_env`: expected `${AIM_DATA_PORT:-8080}:80`, actual `127.0.0.1:${AIM_DATA_PORT:-8080}:80`. Running that exact test in an isolated untouched base worktree `/tmp/s1717-e-base-7dc92619` also gave **1 failed** in 5.63 seconds (`/tmp/s1717-e-base-failure.xml`), with the same assertion. No claim is made about the other interrupted-suite failures. Relevant regression modules all passed as above.

`git diff --check` passes. No migrations or dependency changes were made.

## Ambiguities and dispositions

1. **Resolver location/type:** Gate 2 §4.5 line 122 names the local service, while amendment §2.2 line 30 describes extending the shared artifact. Used a local `ResolvedDirectoryArtifact` subtype and a lazy scanner import; left the shared resolver and concurrent sender untouched. Existing `listing_id` remains the leading argument; version/hash are additional arguments, retaining the legacy call shape.
2. **Missing live registration:** amendment §1.4 line 25 and §2.2 line 30 make retained snapshots authoritative, independent of re-upload. Resolution uses `retained.dataset_id` as source handle and supports a deleted live registration. A still-existing registration with a conflicting listing id refuses. The retained model has no immutable listing-id column; when registration is absent, the signed spec and exact version/hash/source-handle binding supply authority. No migration was invented.
3. **Absence versus null:** amendment §1.4 line 25 says presence-discriminated and legacy bytes unchanged. Both absent is legacy; partial presence or explicit null is invalid. A serializer omits absent fields through the existing local-service `model_dump` path, preserving signatures.
4. **Quote version selection and skips:** amendment §2.5 line 33 requires set-level probing but the quote request has no version fields and no `fixed_reason_skips` wire field. Probe uses chunk C's `metadata_json.local_publish` only when locally recorded `active`, resolves its retained composite and reports skips in the existing local `QuoteProbeView`. The issuer remains authoritative for the active version at signed-spec issue (§2.1 line 29); no cloud-active-state claim or request-schema widening is made.
5. **Parity wording:** amendment §2.6 line 34 parenthetically equates version/hash equality, but §1.4 line 25 explicitly permits identical manifests under distinct versions/roots. Tests enforce equality of the complete `(listing_version_id, manifest_hash)` pair, never hash alone, including a V1/V2 same-hash counterexample. Actual sender/order integration and badge display remain the D/backend dispatch responsibilities.
6. **Filesystem spelling and symlinks:** amendment §1.3(b) line 21 requires NFC; §2.3 line 31 requires in-root realpaths and fact-time stat checks. Normal NFC lookups use direct descriptor-relative opens; only a missing canonical spelling enumerates the directory to find its unique NFC equivalent. Symlinks are conservatively refused with `O_NOFOLLOW`, including in-root symlinks, consistent with registration policy; the connector never receives an unverified buffer.
7. **Runtime timeout:** amendment §2.4 line 32 specifies the `timeout` skip but no new duration setting. Existing raised `TimeoutError` is classified per member; no arbitrary scanner deadline or new policy value was introduced.
8. **One existing test outside the two named extension modules:** Gate 2 §4.5 line 124 names resolver/scanner extensions. The old local-service test asserted live unpublished directory probing with a fabricated hash. Its expectation conflicts directly with retained-only resolution (§1.4 line 25, §2.2 line 30), so this one assertion was updated rather than preserving a live-member fallback.
9. **Cross-repo vectors:** amendment §1.3 line 23 and §3 line 44 require reproduction in both repositories, while this dispatch is AIM DATA only. The fixture is included here and its expected results are independently framed; its exact hash is below for the backend builder. Backend validator acceptance is proven, but backend adoption/reproduction of these new vector bytes is still a separate-dispatch gap.

## Golden-vector handoff and remaining limits

Fixture SHA-256: `bec2796490db4734cba66aeee9b60ea7490e20fda61966910a54083b363429c9`.
Fixture Git blob: `b23fdbeca1490d9b2b8af23f14b7375038d9b07b`.
The fixture contains the root, commitment key, original synthetic payloads, manifest hash, locator preimage/commitment, content hash and every expected member id. The root is a preimage-only `/s1717/root`; no such filesystem directory is needed for vector checks. The executable scan fixture uses a temporary root and deliberately reverses object-id order in its manifest.

Outstanding beyond the passing AIM DATA regression: backend issuer and role-count disclosure; backend reuse of the golden fixture; actual D-sender/scan order integration; complete full-suite triage. No Gate 3, merge, deployment, enabled release, live payment/no-charge proof or overall S1717 completion is claimed.

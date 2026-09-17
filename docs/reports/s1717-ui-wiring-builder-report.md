# S1717 directory UI wiring builder report

Branch: `build/bq-multi-file-datasets-s1717-ui-wiring`
Base: `119f643b5fd25dc8fd61557649371c9832ca33c5` (`origin/main` at start)
Implementation commits: `caf8458` (directory preparation), `eeed9d5` (direct import completion).

## Fixes

1. The directory detail early return bypassed `ListingPreparation`. Directories now use the shared privacy, metadata, and disclosure flow. `DirectoryMembers` is passed into that flow above its listing preparation card, below the page heading; its pagination, role/sample controls, and profiling summary stay in the existing component. Published/error detail views retain the member table. The optional verified shape heading and border separation are unchanged.
2. `DirectoryPublishControl` now passes the published listing ID to preparation state, including while disclosure is pending. Both file types reuse the same completion handler for completion state, marketplace context, success toast, and dataset refresh. Disclosure retries remain in the directory control and resend only the retained snapshot payload. A refresh failure is caught and cannot undo successful publication.
3. `ImportStartResponse` is now a union distinguished by `job_id` versus `dataset_id`. A direct directory response produces one successful results entry, enters the complete phase, displays `Imported 1 dataset (N files)`, and invokes the same `onSuccess` refresh callback as legacy completion without starting a poll. The legacy polling branch is unchanged. Registration errors continue through the import-error handler.
4. The existing parametrized backend import test now checks the exact directory response shape and a nonempty dataset ID. Its existing exact legacy response assertion remains unchanged. Backend application code and feature flag defaults were not changed.

## Validation

Environment: Node `v25.6.0`, npm `11.8.0`, Vitest `3.2.4`; Python `3.12.12` in an isolated temporary environment. All shell commands used `rtk`. No specs were read.

| Check | Result |
| --- | --- |
| Frontend full suite: `rtk proxy npm test -- --reporter=json --outputFile=/tmp/s1717-ui-frontend.json` in `frontend` | **102 tests: 79 passed, 23 failed, 0 skipped**; 11 passed files, 2 failed files |
| Same full suite on untouched base in `/tmp/s1717-ui-wiring-base` | **97 tests: 74 passed, 23 failed, 0 skipped**; 10 passed files, 2 failed files |
| Relevant frontend files, included in the full run | `DatasetDetail.test.tsx`: **27 passed**; `LocalImportBrowser.test.tsx`: **3 passed**; all 25 pre-existing DatasetDetail tests remain unchanged and pass |
| `rtk proxy npm run lint` | **10 errors, 25 warnings**, identical diagnostics to the untouched base after normalizing paths/line numbers |
| `rtk proxy npx tsc --noEmit -p tsconfig.app.json` | **36 errors**, identical diagnostics to the untouched base after normalizing line numbers; package.json has no typecheck script |
| `rtk proxy npm run build` | Passed; existing Browserslist freshness and bundle-size warnings |
| Backend modules | `rtk proxy /tmp/s1717-ui-backend-venv/bin/python -m pytest tests/test_directory_import.py tests/test_directory_registration.py -q --tb=short`: **15 passed, 3 warnings** (9.51s) |
| `rtk git diff --check` and full base-to-head diff check | Passed |

The five added frontend tests cover directory page composition/order and the full privacy-to-publish path, directory publish/disclosure payloads, published listing ID and link, snapshot-only retry, direct import completion/card-refresh callback, legacy polling, and named registration refusal. Existing single-file tests were not edited.

The default Python 3.9 environment could not import a Python 3.10+ union annotation in existing backend code. Verification was moved to isolated Python 3.12; missing test dependencies were installed there without repository dependency changes.

### Inherited frontend failures by node ID

All 23 failing node IDs are identical on base and candidate. They fail because the runtime's localStorage object lacks `clear`, `getItem`, or `removeItem`; they are outside the changed flow.

- `src/lib/aimMarketAuth.test.ts > legacy refresh explicitly uses password mode`
- `src/lib/aimMarketAuth.test.ts > logout or account switch during refresh cannot restore the old session`
- `src/lib/aimMarketAuth.test.ts > mode_legacy_account_switch_and_logout`
- `src/lib/aimMarketAuth.test.ts > no_credentials_in_navigation`
- `src/lib/aimMarketAuth.test.ts > refresh_single_flight_across_tabs`
- `src/lib/aimMarketAuth.test.ts > serial fallback queues independent tabs and stores no lock values`
- `src/lib/aimMarketAuth.test.ts > startAuth preserves nonce with provider github`
- `src/lib/aimMarketAuth.test.ts > startAuth preserves nonce with provider google`
- `src/lib/aimMarketAuth.test.ts > startAuth preserves nonce with provider undefined`
- `src/lib/aimMarketAuth.test.ts > terminal refresh clears auth (401)`
- `src/lib/aimMarketAuth.test.ts > terminal refresh clears auth (403)`
- `src/lib/aimMarketAuth.test.ts > transient_refresh_does_not_clear_auth (429)`
- `src/lib/aimMarketAuth.test.ts > transient_refresh_does_not_clear_auth (502)`
- `src/lib/aimMarketAuth.test.ts > transient_refresh_does_not_clear_auth (503)`
- `src/lib/aimMarketAuth.test.ts > transient_refresh_does_not_clear_auth (504)`
- `src/lib/aimMarketAuth.test.ts > transient_refresh_does_not_clear_auth (network)`
- `src/lib/aimMarketAuth.test.ts > unavailable coordination fails closed without a refresh transport`
- `src/pages/LoginCompletePage.test.tsx > account switch and logout reset the published user`
- `src/pages/LoginCompletePage.test.tsx > complete_once_then_datasets`
- `src/pages/LoginCompletePage.test.tsx > failed completion never publishes authentication`
- `src/pages/LoginCompletePage.test.tsx > reload and API share refresh without clearing transient auth (200)`
- `src/pages/LoginCompletePage.test.tsx > reload and API share refresh without clearing transient auth (429)`
- `src/pages/LoginCompletePage.test.tsx > reload and API share refresh without clearing transient auth (503)`

## Known limitation and scope

`FileUploadModal.tsx` **Browse Folder** still submits files individually through `UploadContext.tsx:234-246`, creating N one-member datasets. This is an unchanged product decision pending with Max. Neither file was touched.

Flags remain default off. No backend contract change, deployment, or PR is included. The second backend module was taken to be `tests/test_directory_registration.py`; the request explicitly named only `tests/test_directory_import.py`.

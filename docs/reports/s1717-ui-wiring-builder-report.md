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


## R1 fold

Starting head: `72838720dbd98944f5acfb50ddf565ce5206e613`, on the existing
`build/bq-multi-file-datasets-s1717-ui-wiring` branch. No new branch, rebase,
spec reading, PR, deployment, or flag changes. This section supersedes the
initial report's claim that backend application code was unchanged. CC remains
pending for the next round; this fold does not claim Council approval.

Implementation commits: `4895bc3` (stored PII routes), `effa371` (folder import),
`1adbea4` (privacy and publication UI).

### Adopted findings

- **DeepSeek F1:** `app/routers/pii.py:84`, `:128`, `:195` adapt the stored
  `metadata.directory_profile.pii` for both GET and POST. Text-scan
  `total_columns` maps to `columns_scanned`, status maps to `scan_status`, and
  scope is returned even for timeout/failure. No profiled members means a null
  privacy score. Missing PII returns 409 `directory_pii_not_ready`; POST never
  rescans a directory. `frontend/src/pages/DatasetDetail.tsx:377` blocks failed
  directory scans, including persisted-state hydration; `:924` shows scope and
  the failure reason. Single-file gate semantics remain unchanged.
  `tests/test_pii_directory.py:34` covers the HTTP branches, failure states,
  missing profile, null score, and unchanged file GET/POST/readiness/not-found
  behavior. `frontend/src/pages/DatasetDetail.test.tsx:535` replaces the clean
  single-file scan mask with real `piiApi` transport and the directory response
  shape, and proves progression to Steps 2 and 3 with an enabled publish control.
  `:628` proves timeout/failed GET and POST stay blocked with visible reasons.
- **DeepSeek F2:** `frontend/src/components/LocalImportBrowser.tsx:184` sends
  `currentPath` with filenames relative to that folder, and `:196` names the
  registered folder in the completion toast. The legacy branch at
  `app/routers/imports.py:79` forwards path/files unchanged; `ImportService` at
  `app/services/import_service.py:189` joins the resolved base and each entry
  (`:195`), so source file selection is preserved. Exact subfolder arguments
  are tested for both response modes at
  `frontend/src/components/LocalImportBrowser.test.tsx:46`.
- **DeepSeek F5 / GLM 3:** `frontend/src/components/PublishModal.tsx:638` passes
  the receiver URL through completion and retains it across disclosure retry.
  `frontend/src/pages/DatasetDetail.tsx:635` stores it in completion state.
  Tests at `frontend/src/pages/DatasetDetail.test.tsx:640` exercise the receiver
  success link, and the following retry test verifies URL retention; the
  published-page test retains coverage of the URL fallback.
- **GLM 4:** `frontend/src/components/PublishModal.tsx:673` and `:689` notify the
  parent of busy state, connected at `frontend/src/pages/DatasetDetail.tsx:1223`.
  The shared listing fields and disclosure confirmation are disabled in flight.
  `frontend/src/pages/DatasetDetail.test.tsx:640` holds the publish response open
  to verify this and verifies unlocking after completion.
- **GLM 2:** `frontend/src/pages/DatasetDetail.tsx:1492` uses directory profile
  row/column aggregates. `:1727` adds profiled N of M and schema count. The
  directory sample tab is hidden and `:2066` replaces readiness loading with
  the member-table guidance. The published-directory fixture checks all of
  these, including absence of the endless readiness message.
- **DeepSeek F3:** `frontend/src/components/LocalImportBrowser.test.tsx:34`
  asserts no status request and no interval immediately after start resolves.
  The interval spy is cleared just before the click, excluding the testing
  library's earlier wait timer. A temporary mutation removing only the direct
  response's early return made this test fail on the unexpected 1000 ms
  interval. The mutation was restored before final verification.

### Final validation and reproducible read-only commands

This run used **Node v25.3.0**, Vitest 3.2.4 and Python 3.12.12 (the initial
report's Node v25.6.0 describes the earlier run, not this fold).

| Check | R1 result |
| --- | --- |
| `DatasetDetail.test.tsx` | 30 passed |
| `LocalImportBrowser.test.tsx` | 5 passed |
| `tests/test_directory_import.py` | 3 passed |
| `tests/test_directory_registration.py` | 12 passed |
| `tests/test_pii_directory.py` | 11 passed |
| Backend combined | 26 passed, 4 dependency/deprecation warnings |
| Frontend build | Passed; existing Browserslist/bundle-size warnings |
| Lint against untouched base `119f643b5fd25dc8fd61557649371c9832ca33c5` | Identical 10 errors and 25 warnings |
| Typecheck against that base | Identical 36 errors |
| Whitespace checks | `git diff --check` and starting-head-to-final diff pass |

The earlier **15 passed** backend result was **3 import + 12 registration**,
not 15 tests in one module. Lint diagnostics were compared by relative file,
severity, rule and message; TypeScript output was compared after removing line
and column positions. Both comparisons preserve diagnostic multiplicity and
have no candidate-only or base-only diagnostics.

Checkout used:
`/private/var/tmp/koskadeux/minimal-bridge-worktrees/bad84c16bc5b-3bc1a2`.
Untouched baseline used: `/tmp/s1717-ui-wiring-base` (clean, exact base above).

The following config-copy preparation ran from the checkout root. It leaves
config bundling and test caches outside the checkout and resolves dependencies
from the existing installation; it does not require editing project config:

```sh
rtk proxy python3 - <<'PYCONFIG'
from pathlib import Path
root = Path.cwd().resolve()
s = Path('frontend/vitest.config.ts').read_text()
s = s.replace('"vitest/config"', repr(str(root / 'frontend/node_modules/vitest/dist/config.js')))
s = s.replace('"@vitejs/plugin-react-swc"', repr(str(root / 'frontend/node_modules/@vitejs/plugin-react-swc/index.js')))
s = s.replace('  plugins:', '  root: process.cwd(),\n  cacheDir: "/tmp/s1717-r1-vitest-cache",\n  plugins:')
s = s.replace('path.resolve(__dirname, "./src")', 'path.resolve(process.cwd(), "./src")')
Path('/tmp/s1717-r1-vitest.config.mjs').write_text(s)
PYCONFIG
```

Exact successful frontend test invocation from `frontend`, with macOS sandbox
writes denied throughout the checkout (35 passed):

```sh
rtk proxy sandbox-exec -p '(version 1)(allow default)(deny file-write* (subpath "/private/var/tmp/koskadeux/minimal-bridge-worktrees/bad84c16bc5b-3bc1a2"))' npm test -- --config /tmp/s1717-r1-vitest.config.mjs src/pages/DatasetDetail.test.tsx src/components/LocalImportBrowser.test.tsx --reporter=json --outputFile=/tmp/s1717-r1-readonly-frontend.json > /tmp/s1717-r1-readonly-frontend.log 2>&1
```

Exact successful backend invocation from the checkout root with the same
write-denial proof (26 passed):

```sh
rtk proxy sandbox-exec -p '(version 1)(allow default)(deny file-write* (subpath "/private/var/tmp/koskadeux/minimal-bridge-worktrees/bad84c16bc5b-3bc1a2"))' env PYTHONDONTWRITEBYTECODE=1 /tmp/s1717-ui-backend-venv/bin/python -m pytest tests/test_directory_import.py tests/test_directory_registration.py tests/test_pii_directory.py -v --tb=short -o cache_dir=/tmp/s1717-r1-pytest-cache > /tmp/s1717-r1-readonly-backend.log 2>&1
```

Build and diagnostic commands from `frontend`:

```sh
rtk proxy npm run build > /tmp/s1717-r1-build.log 2>&1
rtk proxy npm run lint -- --format json --output-file /tmp/s1717-r1-lint.json > /tmp/s1717-r1-lint.log 2>&1
rtk proxy npx tsc --noEmit -p tsconfig.app.json > /tmp/s1717-r1-tsc.log 2>&1
```

Baseline diagnostic commands from `/tmp/s1717-ui-wiring-base/frontend`:

```sh
rtk proxy npm run lint -- --format json --output-file /tmp/s1717-r1-base-lint.json > /tmp/s1717-r1-base-lint.log 2>&1
rtk proxy npx tsc --noEmit -p tsconfig.app.json > /tmp/s1717-r1-base-tsc.log 2>&1
```

Lint and typecheck intentionally retain their inherited nonzero exits (1 and
2 respectively); parity is not a claim that the repository is lint/type clean.

## Install fixes

Starting head: `188248012afa26cdb9cd048ea9b6d59a70cc4ea0`; existing
`build/bq-multi-file-datasets-s1717-ui-wiring` branch.

- Customer compose now forwards `AIM_DATA_MULTI_FILE_DATASETS_ENABLED` immediately
  after the connectivity flag, defaulting to `false`. Runtime field inspection
  confirms aliases `AIM_DATA_MULTI_FILE_DATASETS_ENABLED` and
  `VECTORAIZ_MULTI_FILE_DATASETS_ENABLED`.
- Local Import now shows `./import (next to your compose file)` and
  `HOST_IMPORT_DIR`, matching the host mount into backend `/data/import`.
  Repository search found no remaining occurrences of the old variable or path.
- Validation: `rtk proxy npm test -- src/components/LocalImportBrowser.test.tsx`
  from `frontend`: **6 passed**, including real-brand empty-state guidance.
  `rtk proxy /tmp/s1717-ui-backend-venv/bin/python -m pytest tests/test_aim_data_deployment.py -k compose -q --tb=short --show-capture=no`:
  **2 passed, 1 pre-existing failure**. The new default-off flag assertion and
  YAML parsing pass; the older shape test expects an unbound port while compose
  binds `127.0.0.1`. Both mismatched lines were verified at the starting head and
  left unchanged to preserve scope. Missing `sqlglot` and Pillow dependencies
  were installed only in the temporary test environment.
- `rtk git diff --check` passed. No README/INSTALL edits, new branch, rebase, or PR.

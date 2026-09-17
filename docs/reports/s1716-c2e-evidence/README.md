# Reproducing Chunk 2e evidence

The two full pytest runs use baseline `896b6157` and code milestone `5d5d4c0`.
`source-shas.json` binds changed source/test/workflow bytes; fixture descriptions/CI coverage and documentation commits follow. Those later
changes do not alter the tested application or Python tool logic. Synthetic
checksums were regenerated and matched again in the customer image after the
fixture-description amendment. `baseline-command.json` and `candidate-command.json` preserve exact argv
and working directories. `run.py.txt` and `results_plugin.py.txt` reproduce the
isolated SQLite/storage and outbound-socket guard. Invoke their saved copies via
`rtk proxy python3`; update only the output directory/interpreter for your host.
No tests are deselected. The recorder writes a node outcome for every executed
case, including setup and collection errors. Pytest's separate failure/error
counts remain in the outcome-only summary files.

The full frontend commands in both worktrees were:

```sh
rtk npm test -- --reporter=json --outputFile=<local-result.json>
rtk npm run build
rtk npm run lint
```

`*-vitest-nodeids.json` retains every full assertion name and outcome. Raw failure
objects and captured output remain local, not in Git. `local-log-shas.json` pins
those original logs under `/var/tmp/s1716-c2e-evidence`; committed summaries are
not represented as the full raw logs. Dependencies were read from the existing
AIM Data virtualenv and frontend node_modules; no peer dependency installation
or production environment was modified.

New modules pass `rtk proxy ruff check` and mypy using:

```sh
rtk proxy /private/tmp/s1716-r2-typecheck/bin/mypy \
  --python-executable /Users/max/Projects/ai-market/aim-data/.venv/bin/python \
  --explicit-package-bases --check-untyped-defs --follow-imports=silent \
  --ignore-missing-imports <changed Python files>
```

The same existing modules on baseline have nine inherited mypy errors and the
same late-import Ruff E402. New scripts/tests have no added diagnostics. The
canonicalization test's two-line comment shifts inherited line numbers only.

For fixture/backend comparison, export the exact recorded backend Git object to
a local file, then run:

```sh
rtk proxy python3 scripts/check_preview_fixture_parity.py --backend-fixture <exported-object-file>
rtk proxy node tests/preview_differential_check.cjs
```

Strict synthetic regeneration (real scanner, no detector monkeypatch):

```sh
rtk proxy env PYTHONPATH=/var/tmp/s1716-c2b-pinned:. \
  /Users/max/Projects/ai-market/aim-data/.venv/bin/python \
  tests/run_preview_producer_synthetic.py \
  --output /private/var/tmp/<new-synthetic-directory> \
  --reference tests/fixtures/preview-producer-synthetic-shas.json
```

The isolated import target supplies Presidio 2.2.362 over the shared environment's
2.2.33; spaCy and model versions already match. CI installs the actual pinned
requirements and needs no import overlay. Synthetic output directories contain
no real customer data. Only checksums are committed; public row bytes are removed
by the retirement stage.

Customer image build/probe (a development Docker image name, never a Git tag):

```sh
rtk docker build --platform linux/arm64 -f Dockerfile.customer \
  --build-arg VERSION=dev-s1716-c2e --iidfile <local-iid-file> \
  -t aim-data-s1716-c2e:local .
rtk docker run -d --name <unique-proof-container> --platform linux/arm64 \
  --network none aim-data-s1716-c2e:local
rtk docker exec <unique-proof-container> curl -fsS http://localhost/api/health
rtk docker exec <unique-proof-container> curl -s -o /tmp/probe \
  -w '%{http_code}' http://localhost/api/marketplace/preview-builds
```

Wait for Docker's health status to be healthy and record image/container IDs,
architecture, labels and HTTP results. The second HTTP response is expected 401.
Remove local node_modules symlinks from the build context if present. Never use
an image health check as proof of real owner registration, a release, or public
HTTPS hosting. The release controller commands are in the parent chunk report.

# Reproduce Chunk 2b evidence

Baseline: `49985ed34d8a79dd69fec008c946e8feb7acfa12` (fetched origin/main).
`checks.json` pins every tested runtime/test source and fixture SHA-256, OS,
Python and dependency versions. Later documentation commits do not change those
tested files. No rows, real credentials, detector matches or captured test
tracebacks are included in this directory.

For full tests in each checkout, use the same existing repository Python test
environment (recorded run: `/Users/max/Projects/ai-market/aim-data/.venv/bin/python`):

```sh
rtk proxy env PYTHONPATH=.:docs/reports R2_OUTCOMES=/var/tmp/c2b-outcomes.json /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest tests --continue-on-collection-errors -q --tb=no -p s1716_r2_outcomes
```

No excluded paths or extra skips. The outcome-only plugin preserves exact pytest
nodeids and setup/call/teardown outcomes; it changes no test behavior. Full run
summaries and complete inputs are committed here. Baseline may use an absolute
candidate `docs/reports` PYTHONPATH entry for the same observer plugin.

Affected baseline suites: `test_pii.py`, `test_pii_scrub.py`,
`test_pii_text_content.py`, `test_s3_publish_source_resolver.py`,
`test_dataset_canonicalization.py`, `test_dataset_merkle_service.py` under tests.
Candidate adds all three `test_preview_*.py` suites. Use the same command/options,
replacing `tests` with those paths. Both affected runs completed.

Recompute the exact tables:

```sh
rtk proxy python3 docs/reports/s1716-r2-compare.py docs/reports/s1716-s1294-c2b-evidence/baseline-outcomes.json docs/reports/s1716-s1294-c2b-evidence/candidate-outcomes.json /var/tmp/c2b-full-per-test.json
rtk proxy python3 docs/reports/s1716-r2-compare.py docs/reports/s1716-s1294-c2b-evidence/baseline-affected.json docs/reports/s1716-s1294-c2b-evidence/candidate-affected.json /var/tmp/c2b-affected-per-test.json
```

Ruff was `rtk proxy ruff check app tests`. Frontend lint was `rtk proxy npm run
lint`, and types were `rtk proxy node node_modules/typescript/bin/tsc --noEmit -p
tsconfig.app.json`, in each checkout's frontend directory with the same installed
dependencies. All three checks exited nonzero on both refs with identical
diagnostics after checkout-path normalization. Checksums retain raw and normalized
log identities. All changed Python files/helpers pass Ruff and compilation.

Reproduce mutations without modifying the working checkout:

```sh
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/python docs/reports/s1716-c2b-mutations.py /var/tmp/c2b-mutations.json
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/python docs/reports/s1716-c2b-failure-audit.py /var/tmp/c2b-failure-audit.json
```

The first runs unmodified controls and the two requested mutations. The second
reproduces the additional explicit source mutations from the failure-observation
audit in isolated copies. Together their actually failed nodeids cover all 349
added tests; the final unmodified full run passes those same nodeids. Final
acceptance reran the combined helper after the synthetic fixture serialization
adjustment required by the repository secret scanner.
Timeouts, collection failures and replacements not executed are not counted as
test failures. `failure-observation-audit.json` stores only failed nodeids,
outcome counts, explicit replacements and source hashes, not diagnostic bodies.

`real-policy.json` records a separate pinned-detector smoke check using an
isolated Presidio 2.2.362 import target. It is not a claim that the regression
environment's older analyzer is admissible. `receipt-examples.json` contains
synthetic closed header examples, not live public HTTPS receipts. Actual loopback
GET/OPTIONS/retirement behavior and simulated pinned HTTPS behavior are separate
tests in the origin suite.

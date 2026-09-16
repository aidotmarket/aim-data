# R2 execution evidence

Baseline: origin/main `ee7faa457a21d751bc581b6733ac9a40bb80a801`.
Candidate runtime source: `034355de89a39903595d219ae05f0b5823dad355`.
Later commits package only evidence and documentation; source hashes are in the
implementation receipt. Original golden fixture remains byte-for-byte unchanged.

`full-per-test.json` contains baseline and candidate outcomes for every exact
pytest nodeid. Recompute the summary and table from `baseline-outcomes.json`
and `candidate-outcomes.json` using:

```sh
rtk proxy python3 docs/reports/s1716-r2-compare.py docs/reports/s1716-s1294-c2a-evidence/r2/baseline-outcomes.json docs/reports/s1716-s1294-c2a-evidence/r2/candidate-outcomes.json /var/tmp/s1716-r2-recomputed.json
rtk proxy cmp /var/tmp/s1716-r2-recomputed.json docs/reports/s1716-s1294-c2a-evidence/r2/full-per-test.json
```

To repeat execution in each checkout, set `PYTHONPATH=.:<candidate>/docs/reports`
and `R2_OUTCOMES=<output.json>` through `rtk proxy env`, then invoke
`/Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest tests
--continue-on-collection-errors -q -p s1716_r2_outcomes`.
The plugin only observes reports and writes outcome metadata; it does not alter
collection, tests, status, or skipping. All tests are included, including 38
beta-readiness cases needing localhost:80; their setup errors match exactly.

Affected-suite runs use `tests/test_duckdb.py` on baseline and add
`tests/test_dataset_canonicalization.py tests/test_dataset_merkle_service.py`
on candidate. `affected-per-test.json` uses the same comparison schema.
Focused and pinned logs contain final summaries. Full logs retain only summaries;
raw local log hashes are recorded separately. No tracebacks or captured data are
published. Nodeids are preserved exactly, including pytest parameter IDs.

Ruff (`ruff check app tests`), frontend ESLint (`npm run lint`) and TypeScript
(`node node_modules/typescript/bin/tsc --noEmit -p tsconfig.app.json`) were rerun
on both checkouts. Logs normalize checkout paths to `<WORKTREE>` and are otherwise
unaltered; each pair is byte-identical. Exit codes and raw log hashes are in
`checks.json`. The changed Python files and evidence helpers pass Ruff; Python
compilation and diff whitespace checks pass. No configured Python type checker
exists. Frontend tests/build and resource benchmarks were not rerun in R2;
the retained R1 results are historical and not new R2 acceptance claims.

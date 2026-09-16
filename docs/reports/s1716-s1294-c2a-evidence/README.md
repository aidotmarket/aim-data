# Acceptance receipts

The report and per-test tables summarize these completed executions. JSON
benchmark receipts contain only synthetic test metadata, source hashes and
resource measurements. No generated source row files are retained.

Committed pytest logs/XML retain outcomes only; all tracebacks, test inputs,
captured output and exception messages are omitted. The push secret scanner
rejected token-like text in the initial traceback attachment. The evidence was
minimized before publication; no scanner bypass was used.
`log-redactions.json` records the original file SHA-256 and omission policy.
Unmodified execution logs remain locally under `/var/tmp/s1716-c2a-*`.
This redaction does not change test identities, outcomes or parity.

`fixture-sha256.json` pins the unchanged backend corpus, extended must-reject
corpus and real-file parser corpus. `mutations.json` captures actual failing
golden assertions for the two deliberately broken primitives.

`baseline-failure-categories.json` lists every baseline failure without test
input values. The report explains their groups and all existing skips.

## R2 fold

`r2/` contains fresh full and affected-suite exact-nodeid parity, focused and
pinned summaries, and matching Ruff/lint/type diagnostics. The current
implementation receipt pins the final tested implementation snapshot; the old
receipt is retained as `implementation-receipt-r1.json`. R1 benchmark timings
remain historical.

A commit cannot contain its own SHA. After the evidence/report commits, generate
the exact final review-HEAD receipt outside the Git tree with:

```sh
rtk proxy python3 docs/reports/s1716-r2-receipt.py --output /var/tmp/s1716-r2-final-head-receipt.json
```

The generator verifies each runtime source against HEAD and binds all R2
evidence by SHA-256. This final receipt records the pushed candidate SHA.

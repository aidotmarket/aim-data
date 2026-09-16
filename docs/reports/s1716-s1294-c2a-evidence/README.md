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

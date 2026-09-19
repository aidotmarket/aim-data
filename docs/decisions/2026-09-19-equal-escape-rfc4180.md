# Decision: equal CSV quote and escape means RFC4180 doubled quotes

Date: 2026-09-19
Owner: Max
Event Ledger: `062a82b6-3eb0-405c-a538-c26740a5859d`
Status: binding

When a CSV/TSV parsing declaration sets `escape` equal to `quote`, AIM Data
uses the RFC4180 doubled-quote convention. It does not also treat the quote
character as a separate escape character.

This deliberately narrows compatibility with v1.24.1 for two previously
successful input classes under an equal-escape declaration:

- A quote character inside an unquoted field is retained. For example,
  `value\nx""y\n` now produces `x""y` rather than `x"y`, changing the
  canonical row, leaf hash and Merkle root.
- A delimiter escaped by a quote character is refused. For example,
  `value\na","a\n`, which v1.24.1 read as `a,a`, now fails with
  `csv_parse_error`.

Max made this owner decision on 2026-09-19 after reviewing the compatibility
impact. Treating the quote character as a separate escape character is not a
real CSV dialect and produced records that no standard parser agrees with. The
known production population contained two dataset commitments and 22 preview
proofs, all created that day with an empty escape. The verified-preview path had
produced no commitment before the preceding night, and the seller run's
equal-escape attempt failed before producing records. No known production
artifact was therefore built under the old interpretation.

Two alternatives were rejected:

- Persisting a per-job dialect version so old jobs retain the old reading would
  add machinery to the signing path for a case with no evidence of a production
  artifact.
- Trying RFC4180 first and falling back to the old reading would rescue inputs
  that now fail, but not inputs where both readings succeed and disagree. It
  would be a partial fix presented as complete compatibility.

For an old preview from another installation, the migration is explicit:

1. Withdraw the old preview and complete retirement of its published package.
2. If the source used quote-escaped delimiters, re-export or correct it to valid
   RFC4180 first. Rebuild with an empty escape or the now-equivalent equal-escape
   declaration, producing fresh immutable commitment and disclosure identities.
3. Obtain fresh seller approval for the rebuilt preview.
4. Submit the replacement.

# AIM Data v1.24.1 release notes (draft)

Draft only. Do not tag, promote, or release from this document.

## Verified preview publication

- Removes automated content refusal from the seller preview path. Dates, places,
  emails, URLs, long prose, UUIDs/hashes, phone numbers, negative numeric text,
  control characters, and the former deterministic rule corpus can be packaged.
- Stops requiring Presidio/spaCy/model availability or version parity for preview
  publication.
- Emits seller-attested `aim-preview-policy-v2` / `2.0.0` with a passing verdict
  and empty rules/reasons, while accepting v1/1.0.0 as legacy input.
- Preserves explicit seller confirmations, technical caps, package integrity,
  Merkle proofs, signatures, sampled-leaf digest, and attestation chain.
- Reduces seller-origin admission to credential-free browser GET essentials;
  media type, no-store, OPTIONS, cookies, and compression are observations.
- Updates UI copy to make seller responsibility for the exact public rows clear.

## Coordinated release requirement

The ai.market backend and browser viewer must accept v2/2.0.0 in the same release.
The viewer must also relax its current exact media type, no-store, and identity
content-encoding refusals if externally hosted packages are to use the reduced
origin contract. AIM Data's controlled origin continues emitting the old strict
headers for compatibility.

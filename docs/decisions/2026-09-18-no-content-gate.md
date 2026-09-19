# Decision: verified previews have no automated content gate

Date: 2026-09-18
Owner: Max
Status: binding

“I am not building a police state. I want to remove things that stand in the
way. If we have an issue we deal with it then. Gut anything that is going to
cause failures.”

The seller-side AIM Data producer must not use automated content judgement to
refuse, hide, fail, or delay a verified preview. This removes Presidio/spaCy and
the deterministic `secret`, `personal_data`, `executable`, `url`,
`restricted_content`, `long_prose`, `control_character`, `formula`, and
`high_entropy` rules from preview admission. Missing detector models and version
differences are irrelevant to this path.

The seller remains responsible for what they publish and must explicitly sign
the rights basis, permission to show the exact selected rows publicly,
restricted-content confirmation, and metadata accuracy.

Technical and verification controls remain: supported logical types, finite
numbers, row/field/byte/depth/node/resource caps, immutable source and selection,
seller ownership and registration, signatures, Merkle inclusion, sampled-leaf
digest, transparency/checkpoint evidence, exact package integrity, and inert
plain-text rendering.

Producers emit `aim-preview-policy-v2` / `2.0.0`, verdict `passed`, and empty
rules/reasons. The scan-attestation structure, signature, and
`sampled_leaf_list_digest` binding remain unchanged. Consumers accept v2 and
legacy v1/1.0.0 inputs without running a content corpus.

Origin admission requires what a credential-free browser GET needs: HTTPS public
seller origin, no credentials or redirects, CORS allow-origin for
`https://ai.market` or `*`, a JSON-parsable body, and the exact expected package.
Custom media type, no-store, OPTIONS behavior, cookies, and compression are
recorded observations rather than seller refusals. Controlled AIM Data hosting
continues to emit the stricter compatibility headers.

No cryptographic, non-custodial, size/resource, ownership, or explicit seller
confirmation control is relaxed by this decision.

# Trust Channel v1 client runbook

Contract: [backend specification at 58a04603f55c6d904b27d8e69bea15e5c3841d75](https://github.com/aidotmarket/ai-market-backend/blob/58a04603f55c6d904b27d8e69bea15e5c3841d75/docs/trust-channel-ed25519-handshake.md).
This client implements the Ed25519/X25519 stream path only.

## Prerequisites and operation

Use the existing registered device keystore and configured keystore passphrase,
internal API key, and HTTPS ai.market URL. Production requires authenticated WSS;
plain WS is for local testing. Registration, keystore format, ports and OAuth are
unchanged. The implementation loads existing keys through `DeviceCrypto` in
`app/core/crypto.py`; it never generates replacement keys during reconnect.

The registered string ID comes from the keystore's base64 JSON certificate,
including the unified registration certificate's nested base64 `payload` format.
Legacy certificates bind both public keys; unified certificates bind the signing
key fingerprint. Invalid/missing registration state fails before connecting. The
client does not recompute the machine fingerprint or use the VZ install UUID.

Connection setup retains `X-API-Key`, sends HELLO with a fresh 16-byte nonce, signs
the exact length-prefixed transcript, derives separate directional keys, and waits
for ESTABLISHED before allowing application sends. Data frames are AES-GCM encrypted;
event frames are plaintext JSON over authenticated WSS and acked by `event_id`.
Transport ACKs and ping/pong remain JSON control traffic. Both sequence directions start
at zero, permit gaps, and reject replay and out-of-range values. Session expiry or
counter exhaustion requires a fresh handshake. Disconnect clears traffic keys,
cancels session handlers and fails pending waiters. Mutable shared-secret zeroing
is best effort; Python/native copies cannot be guaranteed erased.

Application actions receive a `request_id`. Fulfillment wire envelopes retain flat
identity fields and place the caller's application message inside the outer
`parameters`: auth fields stay flat there and its nested `parameters` is preserved
untouched. This mirrors backend `trust_websocket.py:1241,1249–1253`; metadata,
complete and error validate their nested models at 1257, 1273 and 1277. Chunks
include `order_id` and `listing_id` for authorization at 1264–1266.
Encrypted backend ACK response `data` is unwrapped for action/transfer waiters;
request-ID waiters retain the entire envelope.

## Event acknowledgements and rate limits

After successfully decoding and dispatching an `event` frame with `event_id`, the
client immediately sends plaintext `{"type":"ack","event_id":<id>}` without
waiting for the application handler to finish. This is a transport acknowledgement,
separate from encrypted fulfillment ACKs. The server allows three unacknowledged
events; each control ACK releases capacity and triggers another pending-event drain.

A `{"type":"rate_limit","retry_after_ms":N}` control frame pauses subsequent
`send_action` calls for N milliseconds and logs the delay at info level. Further
rate limits can extend the pause. Incoming events, control ACKs and ping/pong
continue during the pause; reconnect clears it. Invalid, negative or non-finite
delays are ignored. Local chunk windows retain their existing one-resend ACK
timeout behavior. For a pending S3 response, a valid rate-limit control triggers
one resend of the same request after the pause (with a fresh encryption sequence).
A second rate limit fails the delivery. Each sent attempt has a 30-second reply
timeout; the server-directed send pause precedes that timeout. Controls carry no
request ID, so this recovery applies to pending S3 responses; fulfillment is
processed sequentially. Timeout or rejection alone does not trigger a resend.

## S3 delivery and fail-safe completion

S3 delivery sends `vai.fulfillment.response` with `order_id`, `listing_id` and
`request_id` (the incoming ID, or a generated UUID). Like local fulfillment,
the outer wire `parameters` contains the application message's auth fields and a
nested `parameters` object. After the server spread, that object contains
`success: true`, HTTPS `access_url`, ISO `expires_at`, and `file_size_bytes`.
A valid stored dataset SHA-256 (`sha256_hash`, `file_hash`, or `content_hash`)
becomes optional `file_hash`; an S3 ETag is never treated as SHA-256. No object
download is added. This path sends neither `vai.fulfillment.url` nor `complete`.

At backend `58a04603`, response is whitelisted (`trust_websocket.py:111`) but
absent from ActionRegistry's definitions. The executor rejects it at
`action_executor_service.py:102–105` before reaching the handler at 555–562.
**S3 delivery is therefore unsupported at that pin and fails safely.** A companion
server fast path is separate work; this client fold does not prove it exists,
executes, or is deployed. The contract suite asserts the known registry gap and
validates the requested response schema independently of that companion.
Read backend sources with `git show 58a04603:<path>`; the reference checkout's
moving main/HEAD is not the pin and must not be checked out or reset for this work.

The client registers its request-ID waiter before sending. It requires outer
`success` and `data.success` to be true with no errors, plus either the existing
handler's `data.status == "delivered"` or a complete-style `data.token_id`.
The pinned complete result is `{success: true, token_id, download_token}`
(`fulfillment_listener_service.py:451`; idempotent result at 318–322).
`trust_websocket.py:1287–1299` wraps it in `TrustActionResponse`, echoing the
request ID; default fields are `error: null`, `needs_confirmation: false`,
`confirmation_prompt: null`, and `audit_log_id: null`. Tests use this exact
complete envelope for both complete and response, with synthetic token values.
These are contract fixtures, not claims of successful server execution.

Missing/negative confirmation, disconnect or timeout marks S3 delivery failed.
Action-less errors fail the correlated waiter; errors without request IDs fail
all pending waiters. Local error text stays generic to avoid storing presigned URLs.

## Platform signature limitation

The current registration service stores platform Ed25519 and X25519 public keys,
not the trusted KMS RSA signing key required for `platform_signature`. Absence of
that RSA key does not fail the handshake: server authentication relies on WSS.
The client supports an explicitly supplied trusted `platform_signing_key` RSA
public key; when supplied, an invalid/missing signature fails the handshake.
Neither a challenge-provided key nor the stored Ed25519 key substitutes for RSA.
No new key discovery or storage format is introduced. The signature covers the
original nonce hex spelling, not the ephemeral key; WSS remains required.

## Validation and troubleshooting

Run `pytest -q tests/test_trust_channel*.py`, `pytest -q`, and `git diff --check`
(using the repository environment; prefix shell commands with `rtk`). The focused
suite pins the spec's keys, transcript, signature, first IVs and ciphertext; its
loopback server implements cryptography independently with no backend imports.

On AUTH_FAILED/1008 or INTERNAL_ERROR/1011, inspect the logged code and reason.
Retries back off from 2 seconds to a maximum of 60 seconds. Check registration ID
and key consistency for authentication failures; inspect backend logs for internal
errors. Never replace device keys merely to retry. A passing loopback is client
interoperability evidence, not proof of deployed delivery or an active production
session.

## Source cross-check

The spec's transcript, HKDF, no-AAD AES-GCM and IV rules agree with
[`TrustChannelService`](https://github.com/aidotmarket/ai-market-backend/blob/58a04603f55c6d904b27d8e69bea15e5c3841d75/app/services/trust_channel_service.py)
and the [fixed-vector tests](https://github.com/aidotmarket/ai-market-backend/blob/58a04603f55c6d904b27d8e69bea15e5c3841d75/tests/test_trust_channel_ed25519.py).
No Ed25519 wire disagreement was found. The service's legacy IV helper pads/truncates
non-12-byte nonces, but its Ed25519 HELLO always generates 12 bytes; the client
strictly rejects malformed challenges as required by the spec.

The task described connect authentication via `X-API-Key`. At the pinned SHA,
[`trust_channel_websocket`](https://github.com/aidotmarket/ai-market-backend/blob/58a04603f55c6d904b27d8e69bea15e5c3841d75/app/api/v1/endpoints/trust_websocket.py#L608)
accepts the socket without inspecting that header and authenticates the device
through the handshake. The client retains the existing header. This is a task
premise/source difference, not a change to the protocol or server authentication.

The shared backend Trust Channel runbook predates Ed25519 v1. For this client,
use the pinned normative contract above rather than its legacy RSA key-transport
instructions. Backend rollout and production delivery verification remain separate.

## T-2026-000779 validation (2026-09-08)

Using Python 3.12.12 / pytest 7.4.4 from the existing AIM Data environment:

```sh
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/pytest -q tests/test_trust_channel*.py
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/pytest -q tests/test_trust_channel*.py tests/test_aim_market_oauth_call_inventory.py
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/pytest -q
rtk proxy ruff check app/services/trust_channel_client.py app/services/trust_channel_protocol.py tests/test_trust_channel_client.py
rtk git diff --check
```

Results: 35 focused passes; 63 combined passes; scoped Ruff and diff checks pass.
Full suite: 2002 passed, 91 failed, 38 errors, 33 skipped. Untouched base
`ebe4b7b333cc9e9fdeb51cffc53ca79e1385b90f` in an isolated `git archive` directory:
1967 passed, 91 failed, 38 errors, 33 skipped. All 129 failure/error IDs match;
there are no added failures. Baseline issues include unavailable localhost:80
integration service, `/data` write failures, missing `llm_key_crypto`, entitlement
configuration, and existing assertion/API mismatches. No baseline issues were fixed.
The existing inventory file also has a pre-existing Ruff E731 at line 270, outside
the changed test. The initial default Python 3.13 pytest environment stopped at 37
collection errors, primarily missing `duckdb`; final validation used the project
environment above without installing dependencies or changing shared environments.

## Council fold validation (2026-09-08)

The DeepSeek review `deepseek/response-20260908-182644-386301.md` findings F1–F4
are addressed. The requested client/fulfillment suite passes 64 tests (11
deprecation warnings); scoped client/test Ruff and `git diff --check` pass.
The six-event loopback models the server's three-event capacity and release on
ACK, checks one ACK per event on one connection with zero remaining unacknowledged
events, and keeps handlers pending until transport ACKs finish. Its rate-limit
variant verifies the outbound delay and continued control ACK traffic. Fulfillment
log tests cover top-level request IDs, precedence and the parameters fallback;
send-failure tests cover cancellation and preservation of the original exception
when socket close also fails.

```sh
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/pytest -q tests/test_trust_channel_client.py tests/test_fulfillment*.py
rtk git diff --check
```

## Council fold 2 validation (2026-09-08)

Built on remote branch head `4447db6`, preserving the first Council fold.
`pytest -q tests/test_trust_channel_client.py tests/test_fulfillment*.py` passes
75 tests with 11 deprecation warnings in the existing AIM Data Python 3.12
environment. `git diff --check` passes. Coverage includes one-response S3 delivery,
stored SHA-256 and absent-hash paths, positive confirmation, nested delivery
failure, malformed confirmation, timeout, disconnect/rejection, encrypted error
frames, request-ID isolation, and an immediate encrypted reply during send.

## Council fold 3 validation (2026-09-08)

Built directly on `fe2cfd75a99f1d7ef930fe0edcc8fb0b7907f606`, with no rebase.
Read DeepSeek `response-20260908-185400-750963.md`, GLM
`response-20260908-185405-880410.md`, and CC
`response-20260908-185355-751587.md`. All three CC nits are applied; none skipped.

The requested suite passes **86 tests**, with 11 existing deprecation warnings;
`git diff --check` passes. Exact command:

```sh
rtk proxy /Users/max/Projects/ai-market/aim-data/.venv/bin/pytest -q tests/test_trust_channel_client.py tests/test_trust_channel_server_contract.py tests/test_fulfillment.py
rtk git diff --check
```

Contract tests decrypt actual local/S3 sends. Local schemas are vendored verbatim
from `58a04603:app/schemas/fulfillment.py`; the local backend object repository
provides an additional source-equality, whitelist and registry-gap cross-check
(skipped when that repository is absent). No companion server source is used.
Both completion and response tests use the pinned complete-response envelope; S3
covers zero/one/two rate limits, delayed identical request replay, fresh encrypted
sequences, and failure after the retry. Malformed rate-limit fields are ignored.
The schema and client-loopback results do not prove deployed S3 delivery.

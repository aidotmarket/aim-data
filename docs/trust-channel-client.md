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
for ESTABLISHED before allowing application sends. Only data frames are decrypted;
events and ping/pong remain JSON control traffic. Both sequence directions start
at zero, permit gaps, and reject replay and out-of-range values. Session expiry or
counter exhaustion requires a fresh handshake. Disconnect clears traffic keys,
cancels session handlers and fails pending waiters. Mutable shared-secret zeroing
is best effort; Python/native copies cannot be guaranteed erased.

Application actions receive a `request_id`; existing flat fulfillment fields are
copied into `parameters`, with explicit parameters taking precedence. Encrypted
backend ACK response `data` is unwrapped before dispatch to existing handlers.

## Event acknowledgements and rate limits

After successfully decoding and dispatching an `event` frame with `event_id`, the
client immediately sends plaintext `{"type":"ack","event_id":<id>}` without
waiting for the application handler to finish. This is a transport acknowledgement,
separate from encrypted fulfillment ACKs. The server allows three unacknowledged
events; each control ACK releases capacity and triggers another pending-event drain.

A `{"type":"rate_limit","retry_after_ms":N}` control frame pauses subsequent
`send_action` calls for N milliseconds and logs the delay at info level. Further
rate limits can extend the pause. Incoming events, control ACKs and ping/pong
continue during the pause; reconnect clears it. The pause does not automatically
replay the action rejected by the server; fulfillment's existing ACK timeout and
resend logic remains responsible for recovery.

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

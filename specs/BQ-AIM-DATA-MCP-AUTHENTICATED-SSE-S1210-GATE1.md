# BQ-AIM-DATA-MCP-AUTHENTICATED-SSE-S1210 — Gate 1 design

**Status:** AUTHOR GATE 1 ONLY; not approved, not implemented, not eligible for stable promotion
**Promoted from:** T-2026-000247; diagnosis c45575e2
**Source base:** `18aa9999eb2df7e65925ef05ab3eb44b509448db` (`origin/main`, verified 2026-07-14)
**Binding decisions:** preserve legacy SSE; Bearer header only; MCP off by default (Max, 2026-07-14)

## Charter and reality

Authoritative charter citation: `aidotmarket/koskadeux-mcp@977470b53ea8b9fafb8852822ff0898dbed4d212:DESIGN-CHARTER.md`; Charter commit `f2821d1cb20cc8a9e5c2ed06dd9e4c6b90ee934d`, file blob `9a01fc6b55c7c382e02062d822b836dc81b0b111`. This design applies its named-threat, smallest-reversible-mechanism, subtract-on-add, and blast-radius rules.

Reality: one seller install, at most 10 active connectivity tokens, a default cap of three concurrent SQL queries per token, and one nginx/uvicorn process. The customer-selected MCP client crosses the customer port into local AIM Data; ai.market, buyers, and sellers are not added to this path. Failure cost is unauthorized read access to locally exposed dataset results or a misleadingly enabled/healthy service. Current source pins `mcp>=1.8.0,<1.9` and ships 1.8.1, where `FastMCP.asgi` does not exist. The supported app is [`sse_app(mount_path)`](https://github.com/modelcontextprotocol/python-sdk/blob/v1.8.1/src/mcp/server/fastmcp/server.py#L651), which exposes `/sse`, mounts `/messages/`, and emits the POST endpoint with an opaque `session_id`. The official [1.8.1 SSE client](https://github.com/modelcontextprotocol/python-sdk/blob/v1.8.1/src/mcp/client/sse.py) reuses its supplied headers for GET and POST.

### Threat model

- A local/LAN process without a token, or with an invalid, expired, or revoked token, attempts MCP access.
- A client holding token B posts into token A's live SSE session; process-global/request-confused auth could expose A's access.
- Token material leaks through URL queries, endpoint events, logs, error text, metric labels, or disk.
- Token validation or persisted-enable state is indeterminate; permissive fallback would expose data.
- Version mismatch, proxy buffering, or runtime toggling produces false mounted/ready claims.

Not threats and therefore no machinery: multi-tenant scale, internet OAuth, buyer/seller communication, a new control plane, or more transports. Existing scoped connectivity tokens, HMAC verification, revocation/expiry, rate limits, and read-only `QueryOrchestrator` remain the authorization foundation.

## Smallest reversible mechanism

1. **Construct once; gate at request time.** At app creation, call exactly `mcp_server.sse_app(mount_path="/mcp")`, wrap that returned Starlette app with one AIM Data Bearer/session guard, and mount it once at `/mcp`. Effective routes are exactly `GET /mcp/sse` and `POST /mcp/messages/?session_id=<opaque-sdk-id>`. Always construct the routes, even while disabled, so `/enable` needs no dynamic mount. A construction failure installs a deterministic 503 fallback and records `transport_unavailable`; it never logs “mounted.”
2. **Authenticate and bind the session.** The guard accepts exactly one `Authorization: Bearer <vzmcp token>` header on both GET and POST and never reads a token from the query. It validates through the existing token service. After opportunistically sweeping expired or orphaned entries, an authenticated GET atomically reserves one of a hard process-wide cap of 32 concurrent HTTP SSE binding slots before SDK session creation; the cap is not per token, and a full cap rejects the GET with generic 503 `{"error":"mcp_unavailable"}` without entering the SDK. It buffers the SDK's first `endpoint` SSE frame, extracts only the SDK `session_id`, completes the reserved binding to the validated token ID, and only then forwards the frame unchanged. Each binding contains exactly token ID, creation time, last validated activity, and `deadline=min(token expiry, now + 1 hour)` at creation--never a secret/header; successful guarded activity updates the activity time but cannot extend the fixed deadline. POST independently revalidates its own Bearer, matches its token ID to a live binding, and only then enqueues into the GET stream. An evicted, expired, or unknown binding fails before enqueue. The guard removes reservations/bindings in GET `finally` on clean or unclean disconnect, on disable, on observed revocation/expiry, and on process teardown; a pre-GET/pre-POST sweep also removes expired entries and entries with no live owning GET task. Missing, malformed, invalid, expired, revoked, mismatched, unknown-session, indeterminate auth, or exhausted capacity never reaches MCP.
3. **Keep tool auth in the GET task.** This task ownership follows the pinned 1.8.1 source: [`FastMCP.sse_app.handle_sse`](https://github.com/modelcontextprotocol/python-sdk/blob/v1.8.1/src/mcp/server/fastmcp/server.py#L671-L684) awaits `self._mcp_server.run(...)` inside the GET `/mcp/sse` task, while [`SseServerTransport.handle_post_message`](https://github.com/modelcontextprotocol/python-sdk/blob/v1.8.1/src/mcp/server/sse.py#L149-L192) validates/parses a POST and sends its `SessionMessage` into that GET task's read stream; the POST task does not execute the tool. The GET guard therefore sets a `ContextVar` to that connection's raw Bearer before entering the SDK and resets it in `finally` only when the GET task ends; MCP server run and every HTTP tool execution consume and revalidate that GET context. The POST guard performs the independent validation/binding check in item 2 and never replaces the tool context. The existing process-global value is renamed/limited to CLI stdio; `_validate_token()` uses the HTTP GET context for HTTP execution and must never fall back to stdio for an HTTP call. Concurrent HTTP sessions cannot overwrite each other.
4. **Persist the opt-in locally.** A small `connectivity_state` service owns `/data/connectivity_state.json` on the already-persistent AIM Data volume, following existing local JSON-setting practice. Its only value is `{"enabled": <bool>}`; it contains no token material. Missing file bootstraps from the backwards-compatible `settings.connectivity_enabled` default (`false`); a valid file then wins across restarts. Malformed/unreadable state fails disabled with `reason=state_unavailable`. Toggle writes use temp-file + fsync + atomic replace. `/disable` first durably atomically writes false; only after that succeeds does it flip live state/block new sessions and close active sessions. A false-write failure truthfully returns 503, leaves the prior persisted and live enabled state unchanged, and never claims or transiently reports disabled; the operator must retry or repair storage. Once false is durable, a later session-close failure cannot reopen access: the request gate reads false, new sessions remain blocked, readiness is `not_ready`, and the close failure is logged without secrets. `/enable` likewise durably atomically writes true before live enable and only if transport is available; a true-write failure leaves the service disabled. Tokens are preserved. This adds no external/control-plane service or schema.
5. **Proxy one prefix safely.** Add one `location ^~ /mcp/` in nginx, preserving the URI and `Authorization` header, using HTTP/1.1, long read timeout, `Connection ""`, `proxy_buffering off`, `proxy_cache off`, and `proxy_request_buffering off`. Disable nginx access logging for this prefix and disable Uvicorn's duplicate raw request-target access log; the existing `CorrelationMiddleware` path-only structured request log remains. Thus even a rejected query-token attempt is not written. Do not alter CORS.

The endpoint-event parser is deliberately specific to the pinned 1.8.1 frame (`event: endpoint`, data path `/mcp/messages/?session_id=...`), buffers only that first bounded frame, and fails closed on any other shape. It is not a generic SSE abstraction.

## External contracts

- **Enabled + authenticated:** the two MCP routes above implement legacy SSE. The endpoint event contains only the relative messages URL and session ID. `initialize`, `tools/list`, and existing tools retain their schemas/names.
- **MCP auth:** 401 with `WWW-Authenticate: Bearer` and generic `{"error":"unauthorized"}` for credential/session failures. Authentication backend indeterminacy returns generic 503 `{"error":"mcp_unavailable"}`. No response distinguishes missing/invalid/expired/revoked tokens or echoes input.
- **Disabled:** both MCP routes return 503 `{"error":"mcp_disabled"}`. `GET /api/v1/ext/health`, mounted regardless of the flag, returns 503 with `status=not_ready`, `connectivity_enabled=false`, `mcp_sse_ready=false`, `reason=disabled`. Transport and state failures use `reason=transport_unavailable` and `reason=state_unavailable`. Ready returns 200 with `status=ready` and both booleans true. No token/dataset detail is exposed.
- **Liveness:** `GET /api/health` remains process/container liveness and stays 200 when MCP is intentionally disabled. Compose continues to health-check it. Logs may say routes were constructed, disabled, ready, or unavailable only from actual state.
- **Management:** existing authenticated `/api/connectivity/status`, `/enable`, and `/disable` remain; status additively reports `mcp_sse_ready` and safe `reason`. Enable remains disabled and returns 503 `{"error":"mcp_unavailable","enabled":false}` if transport or persistence is unavailable. Disable returns 200 only after false is durable and live access is blocked; a false-write failure returns 503 `{"error":"state_unavailable","enabled":true}` and leaves the prior enabled/live state unchanged. If closing sessions fails after the durable false write, the operation reports the truthful disabled/not-ready state and safely logs the cleanup error without reopening the request gate. Toggle results reflect persisted/live state, not a settings-object mutation.
- **Default/config:** `app/config.py` remains default false. Compose changes the forced true value to `AIM_DATA_CONNECTIVITY_ENABLED=${AIM_DATA_CONNECTIVITY_ENABLED:-false}`. Existing explicit true/false env values remain accepted; the env seeds state only when no persisted toggle exists.
- **Secrets:** raw Bearer/header values are request-local only. They never enter application/nginx logs, query strings generated by AIM Data, endpoint events, error bodies, metric keys/labels, the session map, the state file, or other persisted state. Metrics use only bounded method/result categories. Query strings accept only SDK `session_id` on POST; `token`, `access_token`, and other auth query parameters are rejected and never used.
- **Stdio:** `python -m app.mcp_server --token ...` and stdio framing/tool behavior remain compatible. No HTTP session state is shared with stdio.

## Exact build surface

| File | Contractual change |
|---|---|
| `app/routers/mcp.py` | deterministic 1.8.1 app construction; Bearer/session guard; active-session close; truthful transport readiness/fallback |
| `app/mcp_server.py` | request-scoped HTTP token context and stdio-only fallback; no tool/schema changes |
| `app/services/connectivity_state.py` (new) | one persisted boolean, fail-closed load, atomic toggle, live getter, and one MCP session-close hook |
| `app/main.py` | mount ext/MCP once regardless of enabled state; log actual result only |
| `app/routers/connectivity_mgmt.py` | use state service; truthful additive status/toggle responses |
| `app/routers/ext.py` | always-available readiness contract and request-time disabled gate |
| `app/models/connectivity.py` | additive typed readiness fields; no token, tool, or dataset schema change |
| `app/services/query_orchestrator.py` | read live state service instead of startup settings snapshot |
| `app/services/allai_tool_executor.py` | existing connectivity enable/disable/status tools use the same state service; no prompt/schema expansion |
| `app/services/diagnostic_collectors.py` | report safe live enabled/readiness state only |
| `docker-compose.aim-data.yml` | false-by-default env expansion; liveness check unchanged |
| `deploy/nginx.conf` | single SSE-safe `/mcp/` prefix with prefix access log disabled; no CORS change |
| `deploy/entrypoint.sh` | disable Uvicorn raw request-target access logging; retain safe path-only structured request logging |
| `tests/test_connectivity_state.py` (new), `tests/test_mcp_server.py`, `tests/test_connectivity_mgmt_api.py`, `tests/test_ext_api.py`, `tests/test_health.py`, `tests/test_connectivity_copilot.py`, `tests/test_query_orchestrator.py`, `tests/test_diagnostic.py` | unit/state/contract regressions |
| `tests/integration/test_mcp_sse_customer_port.py` (new) | official-client and nginx customer-port proof |

No migration, frontend, marketplace, seller, or release workflow file is in scope.

## Test matrix and Gate 4 evidence

1. **Route/version contract:** under `mcp==1.8.1`, assert exactly the GET and trailing-slash POST routes, endpoint event path, successful construction log, and unavailable fallback without a false mounted/ready log.
2. **Auth matrix on both legs:** missing, malformed/multiple header, query token, unknown, wrong secret, expired, revoked, DB/validator exception, unknown session, and token/session mismatch are rejected before SDK/tool work; bodies and headers contain no supplied value.
3. **Isolation/task ownership:** instrument the pinned 1.8.1 integration path; open token A and B concurrently, interleave initialize/tool traffic, and prove tool execution observes token A's GET `ContextVar` after an A-authenticated POST. Prove A-to-B and B-to-A POSTs fail before enqueue, each permitted call observes only its own scopes/token, HTTP execution never uses the stdio fallback, and after revoking A, B continues while A fails. Run repeatedly under AnyIO scheduling.
4. **Session bounds/lifecycle:** prove the process-wide cap admits exactly 32 concurrent HTTP SSE binding slots across all tokens and rejects the next GET before SDK session creation with the safe generic response. Assert every entry has only token ID, creation time, last validated activity, and the bounded deadline, with no secret. Cover clean and unclean disconnect/GET-`finally` cleanup, forced orphan sweep, expired-token and one-hour deadline eviction, evicted/expired/unknown POST rejection before enqueue, disable cleanup, and empty/reset state after process teardown/restart.
5. **Toggle failure ordering/restart:** fresh install is disabled; explicit env true seeds only an absent state file; API enable is immediate and survives container restart; disable survives restart, closes sessions, preserves token rows, and leaves `/api/health` healthy while readiness is 503. Inject a false-write failure and prove the response is 503, persisted/live state never reports disabled, existing access remains unchanged, and restart retains the prior enabled state. Inject a true-write failure and prove live and restarted state remain disabled. Inject session-close failure after durable false and prove new sessions stay blocked before and after restart, readiness remains `not_ready`, and the safe cleanup error cannot reopen access. Corrupt/unreadable state fails disabled with a safe error category.
6. **Stdio regression:** existing stdio tests pass with valid/invalid/revoked tokens and unchanged tools/list/tool-call schemas.
7. **Leak regression:** use an in-memory canary token and assert it is absent from captured app/nginx logs, URLs, endpoint/message events, error bodies, metrics snapshots/labels, session entries, `/data` state, and database fields. Evidence records only pass/fail and a one-way canary digest, never the token.
8. **Customer-port RC proof:** on native amd64 and arm64 hosts, record RC tag, manifest digest, pulled architecture, Compose config, and image digest. Through `http://localhost:${AIM_DATA_PORT}/mcp/sse` and nginx, use `mcp.client.sse.sse_client(..., headers={"Authorization":"Bearer ..."})` plus `ClientSession` to prove unauthenticated rejection, initialize, `tools/list`, and one harmless read-only `vectoraiz_list_datasets` fixture call. Prove the endpoint event arrives before the SSE stays open (no proxy buffering), disabled response, restart persistence, and concurrent-token isolation. Sanitize artifacts before Gate 4 review.

Gate 4 requires green unit/integration suites, both native-architecture customer-port transcripts, manifest/digest evidence, explicit scope diff, and a reviewer-confirmed secret scan. Stable promotion is forbidden until this RC evidence is accepted.

## Rejected alternatives

- **Stdio-only:** violates Max's preserved documented SSE decision.
- **Streamable HTTP or SDK upgrade:** protocol migration and dependency blast radius are separate BQs.
- **Query-string token:** leaks through URLs/proxies and violates the binding decision.
- **`FastMCP.asgi`, conditional startup mount, or dynamic `/enable` mount:** unsupported or nondeterministic and caused the present false claims.
- **SDK OAuth/auth server:** disproportionate for one local seller and existing HMAC tokens; it does not by itself prove same-token GET/POST binding.
- **New database schema/control service:** excluded and unnecessary for one persisted boolean.
- **CORS widening or a second MCP proxy location:** adds exposure/surfaces without serving the official client path.

## Rollback and residual risk

Rollback is one code/config revert and container restart. The state JSON is harmless to old images and may be removed to restore env/default behavior; no database/token migration or data rewrite exists. Roll back an RC only—do not promote it stable.

Residual risks accepted for this BQ: legacy SSE is superseded upstream; the small first-frame binder depends on the pinned 1.8.1 endpoint-event shape; some third-party SSE clients cannot set Authorization headers; a raw token necessarily lives in request-local memory for its connection; and an otherwise valid client idle past the fixed one-hour binding bound must reconnect. Version contract tests, the pin, generic failures, bounded session cleanup, and native RC proof contain these risks. Browser EventSource support, OAuth, arbitrary client compatibility, and SDK migration remain out of scope.

## Scope exclusions

Seller onboarding, metadata, listing, publish, checkout, fulfillment, Mars's e2e harness, schema, billing, raw-data movement, Streamable HTTP, query tokens, broad CORS, and stable promotion are excluded. AIM Data remains non-custodial, local-first, one-seller-per-install, read-only against customer S3, and mediated by existing product boundaries.

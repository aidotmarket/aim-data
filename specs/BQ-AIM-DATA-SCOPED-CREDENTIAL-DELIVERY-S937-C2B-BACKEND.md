_C2b-backend build spec under the already-approved Gate-1/Gate-2 design for BQ-AIM-DATA-SCOPED-CREDENTIAL-DELIVERY-S937. No new design gate (within approved intent). Build path: Codex (MP). Post-build audit: DeepSeek + AG binding pair (builder MP excluded; XAI excluded from code audit)._

# C2b-backend — README-fed allAI listing description for S3 reference listings

## 1. Goal (one paragraph)

When a seller connects a cloud (S3) dataset, the listing is a **prefix reference + a sampled description**, not a per-object `files[]` manifest. This chunk gives allAI the ability to **author that listing description from two inputs: the scan's sampled statistics (produced by C2a) and the seller's own README**, reusing the existing provider-call + JSON-parse machinery. It delivers a tested service capability only. The call site (seller approve surface / publish payload) is OUT of scope here and lands in the approve-surface / C2c chunk.

## 2. Scope

**IN**
1. Refactor the provider-call + parse seam out of `ListingMetadataService._author_listing_metadata` into a shared helper, used by both the existing tabular path and the new S3 path. The tabular path's behavior MUST NOT change.
2. A README reader that fetches the seller's README text through the existing ai.market S3 broker (list → presign → size-bounded fetch). No new broker endpoint; no AWS credentials in the container.
3. A new S3 authoring method on `ListingMetadataService` that builds an S3-specific prompt from sampled stats + README text and returns `{title, description, category, tags}` (or `{}` for the caller to fall back).
4. Unit tests for all of the above (mocked provider, mocked broker, mocked HTTP — no network, no AWS).

**OUT (do not build in this chunk)**
- Any new FastAPI endpoint or call site. Do not wire the new method into `app/routers/s3_connections.py` or any router.
- The seller approve/review surface (frontend) and the publish payload (C2c).
- Any change to the per-object `register_object` flow or the tabular `generate_listing_metadata` flow beyond the mechanical refactor in (1).

## 3. Exact anchors — read ONLY these files; do not explore the tree broadly

(The one time a build in this BQ explored broadly it timed out. Stay inside this list.)

- `app/services/listing_metadata_service.py` — the authoring service. Reuse `_build_authoring_prompt` style; reuse `_parse_authored_metadata` verbatim; refactor `_author_listing_metadata`.
- `app/services/s3_broker_client.py` — `S3BrokerClient` has `list_objects(role_arn, region, bucket, prefix, continuation_token, max_keys)` and `presign_object(role_arn, region, bucket, object_key) -> {url, ...}`. There is NO `get_object`; use presign + bounded HTTP GET.
- `app/services/allie_provider.py` — `get_allie_provider().stream(prompt, context=...)` async-iterates chunks with `.text` (already used by `_author_listing_metadata`).
- `app/models/s3_scan_job.py` — confirm the exact `sampled_stats` JSON shape on `S3ScanJob` (expected keys: `object_count`, `total_size_bytes`, `type_histogram`). Use the real key names you find here.
- `app/services/s3_scan_service.py` — confirm how `sampled_stats` is populated so the prompt consumes the real structure.

If a needed fact is not in these five files, stop and report rather than widening the search.

## 4. Implementation

### 4.1 Shared provider helper (refactor)
Extract the stream-accumulate-parse body of `_author_listing_metadata` into:

```python
async def _author_via_provider(self, prompt: str, context: str) -> Dict[str, Any]:
    """Stream the allAI provider, accumulate text, parse JSON metadata.
    Best-effort: returns {} on empty/invalid/malformed/exception."""
```

`_author_listing_metadata` then builds its prompt as today and calls `self._author_via_provider(prompt, context=<existing context string>)`. Net behavior of the tabular path is unchanged — prove it with a regression test.

### 4.2 README reader
Add a best-effort reader (function or small helper; keep it in `listing_metadata_service.py` or a new `app/services/s3_readme_reader.py` — your call, single home):

- Call `broker.list_objects(...)` for the connection's prefix.
- Select a README at the **prefix root** by basename, case-insensitive, trying: `README.md`, `README.txt`, `README`, `readme.md` (match basename directly under the prefix, not nested deep). First match wins.
- `broker.presign_object(object_key=<that key>)` → `url`.
- Size-bounded GET via `httpx` with a `Range: bytes=0-{MAX-1}` header AND a client-side read cap (`MAX_README_BYTES = 256 * 1024`). Decode UTF-8 with `errors="replace"`.
- Return the text, or `None` if no README is present or any step fails (best-effort, mirrors the tabular sample-rows fallback). Never raise to the caller.

Non-custodial: bytes are read in-memory, bounded, and NOT persisted to disk, logs, or events.

### 4.3 S3 authoring method
```python
async def author_s3_reference_metadata(
    self, *, sampled_stats: dict, readme_text: Optional[str],
    bucket: str, prefix: Optional[str],
    fallback_title: str, fallback_description: str,
) -> Dict[str, Any]:
```
- Build an S3-specific prompt (NOT DuckDB) from: `object_count`, `total_size_bytes`, `type_histogram` (the real keys from §3), the bucket/prefix, the README text (truncate to the same byte cap), and the fallbacks. Same output contract as the tabular prompt: JSON only, keys `title|description|category|tags`, title ≤256 chars, 3–8 lowercase tags, an honest data note when the stats warrant it.
- Return `self._author_via_provider(prompt, context="You write honest buyer-facing marketplace listing metadata for cloud datasets. Return only valid JSON.")`.
- Caller is responsible for applying fallbacks when `{}` is returned.

## 5. Tests (`tests/`)
- Tabular regression: existing `_author_listing_metadata` behavior unchanged after the refactor (mock provider.stream, assert same parsed output as before).
- `author_s3_reference_metadata`: returns parsed fields on good provider JSON; returns `{}` on empty / malformed / provider-exception.
- README reader: finds README among case variants at the prefix root; returns `None` when absent; respects `MAX_README_BYTES` (assert no more than the cap is read/sent). Mock `list_objects`, `presign_object`, and the HTTP GET; no real network/AWS.

## 6. Constraints & output contract
- Single commit on branch `build/bq-aim-data-scoped-credential-delivery-s937-c2b` cut from `main`.
- Do not modify router/endpoint code; do not change the tabular or register-object behavior beyond §4.1.
- Run the new + affected tests; report pass/fail.
- Report: files changed, how validated (commands run), risks/known issues, and confirmation scope was strictly followed.

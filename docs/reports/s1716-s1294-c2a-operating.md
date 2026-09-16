# Chunk 2a local commitment operations

Authority: build-plan §B / §I row 2a and controller ruling fba339b2.
This procedure performs local parsing and commitment construction only.

1. Obtain a complete immutable local source manifest and explicit schema from
   the dataset owner. Use canonical physical paths (no symlink ancestors). Use original declared artifacts unless preservation of
   every logical record and presence/type parameter in a derivative is proven.
   Pass all manifest members to one `run_commitment_job`; never pass previews,
   partial listings or a prefix and label the result the full dataset.
2. Construct `CanonicalSchema` descriptors `[NFC(name), tag, nullable, parameters]`.
   Nullability is required. Declare physical types using `dispatch_type` where
   needed; unsupported types are rejected, never converted behind the scenes.
3. Construct `ParsingDeclaration`. CSV/TSV require UTF-8, delimiter, quote,
   escape (empty means none), header boolean, locale `C`, and explicit null token.
   JSON-array/NDJSON require UTF-8. Parquet carries physical types; they must
   match the declaration, including precision. A nullable Arrow field may be
   declared non-nullable only when every streamed value is present and non-null;
   otherwise reading fails with `nullability_violation`. This also applies to
   nested fields. Naive timestamps
   additionally require an explicit `Z` or numeric offset declaration.
4. Call `run_commitment_job(paths, declaration, descriptors, temp_root, ...)`.
   Use the same dedicated, owner-only installation temp root for every call.
   The process lock permits one job. Startup removes interrupted `job-*` dirs
   under that root without following symlinks. Never share the root with another
   service. `DuckDBService.iter_commitment_records` is the separate streaming
   reader; existing preview/discovery readers retain their old behavior.
5. Supply an optional cancellation event and progress callback. Chunk 2a emits
   reading, sorting, building_tree, selecting and ready. Scanning and hosting
   are closed progress vocabulary for later chunks, not performed phases here.
   Events carry counts, byte counts and elapsed time, never row values or paths.
6. On success retain only returned commitment/proof metadata. Runs, indexes,
   node files and canonical rows are removed on success, failure or cancellation.
   Selection changes require rebuilding after cleanup. No function in this
   chunk signs or sends data. Later source selection/hosting must use the same
   pinned source version and invalidate stale approvals.

Defaults: separate worker; 512 MiB incremental RSS ceiling enforced by parent;
64 MiB sort-run accounting; 16 MiB serialized Arrow batch ceiling; 8 MiB
canonical/input-record ceiling; 20 GiB job disk quota; 1 GiB free-space reserve.
The reader allocates no DuckDB connection, so no DuckDB engine allocation
consumes its reserved 128 MiB budget. Arrow native allocations are included in
the measured worker RSS. The watchdog polls at 20 ms: this is sampled RSS, not
an OS hard memory reservation. Disk accounting includes canonical runs, merge
outputs, indexes, leaf hashes and all tree hashes. Seller-local disk quota
adjustment is explicit through `WorkerBudget`; protocol leaf bound stays 2^63-1,
while unsafe integer metadata is rejected during closed-model construction,
validation and assignment, as well as canonical export.

Failures expose stable codes (`unsafe_integer`, `noncanonical_key_order`,
`unsupported_logical_type`, `unsupported_format`, `duplicate_key`,
`duplicate_field`, `unknown_field`, `invalid_schema`, `field_limit`, `depth_limit`,
`node_limit`, `timestamp_timezone_required`, `timestamp_precision_loss`,
`decimal_out_of_range`, `record_resource_limit`, `resource_limit`,
`disk_resource_limit`, `source_changed`, `invalid_source`, `cancelled`,
`job_already_running`, `unsafe_temp_directory`, `worker_failed`) without cell
content. Do not attach private spill files or source paths to platform telemetry.

Metadata integers are JSON numbers only inside ±(2^53−1). Logical row integer
and decimal values are exact strings. Generic object key sets that would sort
differently under reference code-point order and JCS UTF-16 order fail closed;
closed metadata uses fixed ASCII keys, and supplementary-plane schema names
remain valid array values sorted by UTF-8. No new profile is introduced.

Acceptance commands and complete outcomes are in
`s1716-s1294-c2a-canonical-tree.md` and its per-test parity table. The full
backend-independent pytest run excludes only `tests/test_beta_readiness.py`,
which requires a separate live localhost:80 deployment. Its 38 setup errors
were also recorded in an initial all-tests superset run on both refs.

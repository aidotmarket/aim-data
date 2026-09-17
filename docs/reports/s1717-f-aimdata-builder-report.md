# S1717 F — AIM Data builder report

## Scope and changes

Base: `119f643b5fd25dc8fd61557649371c9832ca33c5` (`origin/main` at branch creation).
Branch: `build/bq-multi-file-datasets-s1717-f`.

- Extended `tests/test_channel_dataset_detail.py` with a directory publish that starts without verification metadata or a verification panel action. It exercises the dataset publish route, real JSON serialization and signing, mocked ai.market responses, member chunks, and the selected sample upload. It recursively rejects serialized keys matching verification, verified, scan, or shape label and asserts successful publication and the saved listing ID.
- Added single-file publish coverage with the feature flag on and off. Canonical payload bytes and HTTP JSON bytes must match the literal legacy payload expectation from `test_no_versions_legacy_publish_payload_is_unchanged`, including its exact keys and absence of new version/verification fields.
- The target module had no backend fixtures at the specified base; reused the existing signed-publish helpers and directory fixture from the backend publish tests. Its three original presentation assertions remain intact.
- Added a 13-line “Datasets with many files” section to each of `README.md` and `docs/INSTALL.md`, covering roles, seller-selected free samples, one listing, whole-set order-page delivery, optional verification, and the current release with an upgrade link. Other README sections are unchanged.
- No schema, API, frontend, or default-flag changes. Frontend tests were neither changed nor run. Gate 1/Gate 2 specs were not read.

## Validation environment

Used the existing `/Users/max/Projects/ai-market/aim-data/.venv/bin/python`: Python 3.12.12, pytest 7.4.4, httpx 0.27.2. The shell-default Python lacked `duckdb`; its initial collection attempt was superseded by the project environment run. No dependencies were installed or changed.

The full suite was also run on a separate detached worktree at the exact base, `/tmp/s1717-f-base`, to identify inherited failures. Each run uses the suite's per-run temporary SQLite database; the commands also set a distinct `AIM_DATA_SERIAL_STORE_PATH`.

## Commands and results

Focused module (6 passed, 5 warnings):

```sh
rtk proxy env AIM_DATA_SERIAL_STORE_PATH=/tmp/s1717-f-focused2-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q tests/test_channel_dataset_detail.py --junitxml=/tmp/s1717-f-focused.xml > /tmp/s1717-f-focused.log 2>&1
```

Full backend suite, candidate (run from the branch worktree):

```sh
rtk proxy env AIM_DATA_SERIAL_STORE_PATH=/tmp/s1717-f-final-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q --junitxml=/tmp/s1717-f-final.xml > /tmp/s1717-f-final.log 2>&1
```

Full backend suite, unchanged base (run from `/tmp/s1717-f-base`):

```sh
rtk proxy env AIM_DATA_SERIAL_STORE_PATH=/tmp/s1717-f-base-serial /Users/max/Projects/ai-market/aim-data/.venv/bin/python -m pytest -q --junitxml=/tmp/s1717-f-base.xml > /tmp/s1717-f-base.log 2>&1
```

`rtk git diff --check` passed with no whitespace errors.

| Run | Passed | Failed | Skipped | Errors | Warnings | Duration |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Focused module | 6 | 0 | 0 | 0 | 5 | 20.42s |
| Full backend, candidate | 3162 | 109 | 34 | 38 | 820 | 217.72s |
| Full backend, unchanged base | 3159 | 109 | 34 | 38 | 820 | 303.96s |

The full suite is **not green**. Both full runs exit 1 with exactly the same 147 failing/error node IDs and the same failure/error classifications. No new failing nodes; the candidate adds three passing cases. Dominant inherited causes include the unavailable `/data` path, no local HTTP service for beta-readiness setup, unconfigured entitlement signing, directory verification disabled in existing tests, and existing missing-module/metering mismatches. No inherited failures were fixed or suppressed.

The following node IDs failed identically on the exact base and candidate.

### Inherited failures (109)

```text
tests/test_aim_data_deployment.py::test_aim_data_compose_uses_aim_data_image_and_env
tests/test_batch_upload.py::test_batch_upload_single_file
tests/test_batch_upload.py::test_batch_upload_multiple_files
tests/test_batch_upload.py::test_batch_upload_custom_batch_id
tests/test_batch_upload.py::test_batch_upload_with_paths
tests/test_batch_upload.py::test_batch_reject_unsupported_extension
tests/test_batch_upload.py::test_batch_reject_paths_length_mismatch
tests/test_batch_upload.py::test_batch_reject_mime_mismatch
tests/test_batch_upload.py::test_dataset_status_endpoint
tests/test_batch_upload.py::test_batch_status_endpoint
tests/test_batch_upload.py::test_preview_before_extraction
tests/test_batch_upload.py::test_single_file_upload_backward_compat
tests/test_batch_upload.py::test_mode_process
tests/test_batch_upload.py::test_client_file_index_with_mixed_results
tests/test_channel_landing.py::test_channel_landing_redirects_aim_data_to_datasets
tests/test_channel_onboarding.py::test_aim_data_steps_have_three_steps
tests/test_channel_onboarding.py::test_get_steps_for_channel_aim_data_returns_aim_data_steps
tests/test_channel_presentation_only.py::test_channel_only_in_allowed_files
tests/test_config_alias_choices.py::test_every_settings_field_has_primary_and_legacy_aliases
tests/test_config_alias_choices.py::test_every_settings_field_resolves_from_primary_and_legacy_prefixes[AIM_DATA]
tests/test_config_alias_choices.py::test_every_settings_field_resolves_from_primary_and_legacy_prefixes[VECTORAIZ]
tests/test_config_alias_choices.py::test_aim_data_prefix_wins_when_both_set
tests/test_copilot_upload.py::TestBrainMessageAttachments::test_too_many_attachments
tests/test_copilot_upload.py::TestBrainMessageAttachments::test_attachment_not_found
tests/test_copilot_upload.py::TestBrainMessageAttachments::test_attachment_wrong_user
tests/test_data_verification_scanner.py::test_directory_ac5_report_order_zip_privacy_and_probe
tests/test_data_verification_scanner.py::test_directory_stale_manifest_never_reports[bytes]
tests/test_data_verification_scanner.py::test_directory_stale_manifest_never_reports[missing]
tests/test_data_verification_scanner.py::test_directory_stale_manifest_never_reports[escape]
tests/test_data_verification_scanner.py::test_directory_stale_manifest_never_reports[between_reads]
tests/test_data_verification_scanner.py::test_directory_stale_manifest_never_reports[during_read]
tests/test_data_verification_scanner.py::test_directory_bound_streams_every_member_and_count_fails_closed
tests/test_data_verification_scanner.py::test_directory_runtime_skips[PermissionError-permission_denied]
tests/test_data_verification_scanner.py::test_directory_runtime_skips[TimeoutError-timeout]
tests/test_data_verification_scanner.py::test_directory_runtime_skips[UnsupportedConnectorShape-unsupported_type]
tests/test_data_verification_scanner.py::test_directory_fact_time_exact_bound_and_deleted_registration
tests/test_data_verification_scanner.py::test_directory_detected_unsupported_never_buffers_or_calls_connector
tests/test_diagnostic.py::TestCollectorTimeout::test_successful_collector_has_no_error
tests/test_diagnostic.py::TestIndividualCollectors::test_error_collector_returns_registry
tests/test_entitlement.py::TestEntitlementValid::test_valid_token
tests/test_entitlement.py::TestEntitlementValid::test_valid_token_returns_all_fields
tests/test_entitlement.py::TestEntitlementExpired::test_expired_token
tests/test_entitlement.py::TestEntitlementReplay::test_replayed_token
tests/test_entitlement.py::TestEntitlementReplay::test_different_nonces_ok
tests/test_entitlement.py::TestEntitlementWrongHash::test_hash_mismatch_detected_downstream
tests/test_entitlement.py::TestEntitlementTampered::test_tampered_signature
tests/test_entitlement.py::TestEntitlementTampered::test_missing_required_field
tests/test_metadata.py::test_searchability_score
tests/test_metadata.py::test_enhanced_metadata
tests/test_metering.py::TestReportUsage::test_skips_when_auth_disabled
tests/test_metering.py::TestReportUsage::test_successful_deduction
tests/test_metering.py::TestReportUsage::test_insufficient_credits_402
tests/test_metering.py::TestReportUsage::test_network_error_fails_open
tests/test_metering.py::TestReportUsage::test_timeout_fails_open
tests/test_metering.py::TestReportUsage::test_server_error_fails_open
tests/test_metering.py::TestReportUsage::test_zero_tokens_skips_network_call
tests/test_notifications_p3.py::TestBatchUploadResilience::test_bad_file_continues_processing
tests/test_notifications_p3.py::TestBatchUploadResilience::test_notifications_created_for_successes_and_failures
tests/test_notifications_p3.py::TestBatchUploadResilience::test_summary_notification_correct_counts
tests/test_notifications_p3.py::TestBatchUploadResilience::test_summary_all_succeed
tests/test_notifications_p3.py::TestBatchUploadResilience::test_summary_all_fail
tests/test_notifications_p3.py::TestBatchUploadResilience::test_response_includes_per_file_status
tests/test_notifications_p3.py::TestSingleUploadNotifications::test_success_creates_notification
tests/test_notifications_p3.py::TestSingleUploadNotifications::test_failure_creates_error_notification
tests/test_notifications_p3.py::TestSingleUploadNotifications::test_batch_id_optional
tests/test_pii_scrub.py::test_scrub_endpoint_not_found
tests/test_pii_scrub.py::test_scrub_endpoint_exists
tests/test_portal_acl.py::test_session_invalidation_on_code_rotation
tests/test_raw_download.py::TestRawDownload::test_successful_download
tests/test_raw_download.py::TestRawDownload::test_hash_mismatch
tests/test_raw_download.py::TestRawDownload::test_missing_file_id
tests/test_raw_download.py::TestRawDownload::test_expired_token
tests/test_raw_download.py::TestRawDownload::test_replayed_nonce
tests/test_raw_download.py::TestRawDownload::test_file_missing_from_disk
tests/test_raw_file_detail.py::TestRawFileDetailEndpoint::test_detail_listing_status_listed
tests/test_raw_files.py::TestRawFileRegistration::test_upload_size_limit_exceeded
tests/test_request_engine.py::TestAPIEndpoints::test_get_matches
tests/test_request_engine.py::TestAPIEndpoints::test_create_draft_rejects_non_ready_dataset
tests/test_s145_bugfixes.py::TestAttestationLLMError::test_attestation_returns_404_on_value_error
tests/test_s145_bugfixes.py::TestAttestationLLMError::test_attestation_returns_500_on_other_error
tests/test_s145_bugfixes.py::TestListingMetadataLLMError::test_listing_metadata_returns_500_on_other_error
tests/test_s145_bugfixes.py::TestPIIScan503::test_pii_scan_returns_503_on_os_error
tests/test_s145_bugfixes.py::TestPIIScan503::test_pii_scan_returns_503_on_import_error
tests/test_s145_bugfixes.py::TestPIIScan503::test_pii_scan_returns_500_on_generic_error
tests/test_s145_bugfixes.py::TestHTMExtension::test_htm_upload_accepted
tests/test_s145_bugfixes.py::TestLargeCSVProcessing::test_extract_tabular_retries_large_files
tests/test_s145_bugfixes.py::TestNginxTimeout::test_nginx_conf_has_proxy_read_timeout
tests/test_s145_bugfixes.py::TestNginxTimeout::test_nginx_conf_has_proxy_send_timeout
tests/test_s145_bugfixes.py::TestNginxTimeout::test_nginx_conf_has_sufficient_client_max_body_size
tests/test_s145_bugfixes.py::TestDeleteEndpoint::test_delete_endpoint_returns_success
tests/test_s145_bugfixes.py::TestDuplicateDetection::test_different_size_same_name_allowed
tests/test_s145_bugfixes.py::TestDuplicateDetection::test_same_size_same_name_rejected
tests/test_s145_bugfixes.py::TestDuplicateDetection::test_allow_duplicate_flag_bypasses_check
tests/test_security_audit.py::TestAADEncryption::test_aad_roundtrip
tests/test_security_audit.py::TestAADEncryption::test_aad_mismatch_fails
tests/test_security_audit.py::TestAADEncryption::test_aad_scope_mismatch_fails
tests/test_security_audit.py::TestAADEncryption::test_backward_compat_no_aad
tests/test_security_foundation.py::TestToolClassification::test_auto_approve_tools
tests/test_single_file_uploads.py::test_single_file_original_bytes[upload-False]
tests/test_single_file_uploads.py::test_single_file_original_bytes[upload-True]
tests/test_single_file_uploads.py::test_single_file_original_bytes[batch-False]
tests/test_single_file_uploads.py::test_single_file_original_bytes[batch-True]
tests/test_single_file_uploads.py::test_delete_uploaded_directory
tests/test_sql.py::test_query_execution_blocked
tests/test_text_processor.py::test_upload_txt_accepted
tests/test_text_processor.py::test_upload_md_accepted
tests/test_text_processor.py::test_upload_html_accepted
tests/test_upload.py::test_upload_csv
tests/test_upload.py::test_upload_unsupported_type
```

### Inherited setup errors (38)

```text
tests/test_beta_readiness.py::TestBetaReadiness::test_auth_login
tests/test_beta_readiness.py::TestBetaReadiness::test_upload_small_csv
tests/test_beta_readiness.py::TestBetaReadiness::test_upload_small_json
tests/test_beta_readiness.py::TestBetaReadiness::test_upload_small_csv_barcelona
tests/test_beta_readiness.py::TestBetaReadiness::test_upload_small_tsv
tests/test_beta_readiness.py::TestBetaReadiness::test_upload_medium_parquet
tests/test_beta_readiness.py::TestBetaReadiness::test_upload_medium_htm
tests/test_beta_readiness.py::TestBetaReadiness::test_upload_medium_pdf
tests/test_beta_readiness.py::TestBetaReadiness::test_sql_query
tests/test_beta_readiness.py::TestBetaReadiness::test_search
tests/test_beta_readiness.py::TestBetaReadiness::test_pii_scan
tests/test_beta_readiness.py::TestBetaReadiness::test_compliance
tests/test_beta_readiness.py::TestBetaReadiness::test_searchability
tests/test_beta_readiness.py::TestBetaReadiness::test_attestation
tests/test_beta_readiness.py::TestBetaReadiness::test_listing_metadata
tests/test_beta_readiness.py::TestBetaReadiness::test_upload_unsupported_shp_zip
tests/test_beta_readiness.py::TestBetaReadiness::test_negative_wrong_extension
tests/test_beta_readiness.py::TestBetaReadiness::test_negative_empty_file
tests/test_beta_readiness.py::TestBetaReadiness::test_negative_sql_injection
tests/test_beta_readiness.py::TestBetaReadiness::test_negative_invalid_dataset_id
tests/test_beta_readiness.py::TestBetaReadiness::test_negative_delete_invalid_id
tests/test_beta_readiness.py::TestBetaReadiness::test_batch_upload
tests/test_beta_readiness.py::TestBetaReadiness::test_delete_dataset
tests/test_beta_readiness.py::TestBetaReadiness::test_large_generated_csv_50mb
tests/test_beta_readiness.py::TestBetaReadiness::test_large_generated_csv_10mb
tests/test_beta_readiness.py::TestBetaReadiness::test_row_count_after_processing
tests/test_beta_readiness.py::TestBetaReadiness::test_batch_upload_5_plus_files
tests/test_beta_readiness.py::TestBetaReadiness::test_data_preview_endpoint
tests/test_beta_readiness.py::TestBetaReadiness::test_preview_flow_batch_upload
tests/test_beta_readiness.py::TestBetaReadiness::test_batch_status_tracking
tests/test_beta_readiness.py::TestBetaReadiness::test_health_deep
tests/test_beta_readiness.py::TestBetaReadiness::test_sql_validate_valid_query
tests/test_beta_readiness.py::TestBetaReadiness::test_sql_validate_rejects_drop
tests/test_beta_readiness.py::TestBetaReadiness::test_concurrent_upload_stress_5x
tests/test_beta_readiness.py::TestBetaReadiness::test_server_no_500_on_malformed_large_csv
tests/test_beta_readiness.py::TestBetaReadiness::test_batch_path_traversal_blocked
tests/test_beta_readiness.py::TestBetaReadiness::test_batch_null_byte_blocked
tests/test_beta_readiness.py::TestBetaReadiness::test_upload_traversal_filename
```

## Delivery

Implementation commits: `db1e44a` (tests), `5fb41fe` (seller documentation). This report is committed separately. No PR was created. All marketplace HTTP responses in the new coverage are mocked; this is backend regression evidence, not live marketplace delivery proof.

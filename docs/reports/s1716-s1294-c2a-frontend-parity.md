# Frontend per-test parity

| Test | origin/main | Candidate |
|---|---|---|
| CoPilotProvider unread messages queues the CONNECTED welcome without opening and clears unread when opened | passed | passed |
| DataVerificationFlow asks for a card only after the seller explicitly starts the paid verification | passed | passed |
| DataVerificationFlow blocks publish when grounded interpretation text is unavailable and leaves decline enabled | passed | passed |
| DataVerificationFlow displays the successful probe and quote before acknowledgements can be checked | passed | passed |
| DataVerificationFlow keeps the active badge during rerun and resets both acknowledgements | passed | passed |
| DataVerificationFlow renders every low-occupancy sentinel as suppressed | passed | passed |
| DataVerificationFlow renders every public fact and method from the complete shared report fixture | passed | passed |
| DataVerificationFlow renders no artifact facts while capture is reconciling | passed | passed |
| DataVerificationFlow renders only the fixed fingerprint notice when grounding is withheld | passed | passed |
| DataVerificationFlow renders the exact server-dated withdrawal marker | passed | passed |
| DataVerificationFlow reveals only captured findings and presents publish and decline as equal explicit choices | passed | passed |
| account switch and logout reset the published user | failed | failed |
| button_disabled_preserves_password (backend_unavailable) | passed | passed |
| button_disabled_preserves_password (backend_unsupported) | passed | passed |
| button_disabled_preserves_password (client_disabled) | passed | passed |
| button_disabled_preserves_password (local_disabled) | passed | passed |
| clicking GitHub posts its provider hint and disables both buttons | passed | passed |
| clicking Google posts its provider hint and disables both buttons | passed | passed |
| complete_once_then_datasets | failed | failed |
| dataset detail publication state keeps publication available when listing_id is null | passed | passed |
| dataset detail publication state shows a server-published listing with an empty marketplace registry | passed | passed |
| disclosure payload builder approved_rows includes only displayed columns and rows with deterministic refs | passed | passed |
| disclosure payload builder classifies HTTP 502 as a pending snapshot | passed | passed |
| disclosure payload builder classifies HTTP 504 as a pending snapshot | passed | passed |
| disclosure payload builder classifies a 422 validation response as a non-retryable rejection | passed | passed |
| disclosure payload builder classifies an indeterminate audit result as disclosure unknown | passed | passed |
| disclosure payload builder maps approved metadata to approved_fields | passed | passed |
| disclosure payload builder requires final confirmation before building acked payload | passed | passed |
| disclosure payload builder serializes the exact approved-row request without changing the sample | passed | passed |
| disclosure payload builder serializes the exact no-sample request with schema.columns | passed | passed |
| disclosure payload builder surfaces a deterministic 422 as a plain-English rejection | passed | passed |
| disclosure payload builder truncates over 100 rows and over 25 columns before submit | passed | passed |
| disclosure snapshot failure panel offers Retry for disclosure_unknown | passed | passed |
| disclosure snapshot failure panel offers Retry for snapshot_pending | passed | passed |
| disclosure snapshot failure panel renders the detailed rejection beside review without offering Retry | passed | passed |
| example should pass | passed | passed |
| failed completion never publishes authentication | failed | failed |
| formatErrorDetail extracts msg from a single error object | passed | passed |
| formatErrorDetail passes through a plain string detail | passed | passed |
| formatErrorDetail returns a string message for a FastAPI 422 detail array (the React #31 crash case) | passed | passed |
| formatErrorDetail uses the fallback for null/empty | passed | passed |
| legacy refresh explicitly uses password mode | failed | failed |
| logout or account switch during refresh cannot restore the old session | failed | failed |
| mode_legacy_account_switch_and_logout | failed | failed |
| no_credentials_in_navigation | failed | failed |
| publish completion confirms completion, disables Publish and refetches the dataset for the published view | passed | passed |
| publish completion keeps Publish disabled after snapshot failure and retries only the snapshot | passed | passed |
| publish completion preserves successful publication if the dataset refresh fails | passed | passed |
| refresh_single_flight_across_tabs | failed | failed |
| reload and API share refresh without clearing transient auth (200) | failed | failed |
| reload and API share refresh without clearing transient auth (429) | failed | failed |
| reload and API share refresh without clearing transient auth (503) | failed | failed |
| seller listing preparation enables metadata acceptance without a draft listing id | passed | passed |
| seller listing preparation rehydrates persisted metadata and privacy decisions at step 2 without regenerating metadata | passed | passed |
| serial fallback queues independent tabs and stores no lock values | failed | failed |
| start disabled race keeps fallback and never navigates | passed | passed |
| startAuth preserves nonce with provider github | failed | failed |
| startAuth preserves nonce with provider google | failed | failed |
| startAuth preserves nonce with provider undefined | failed | failed |
| terminal refresh clears auth (401) | failed | failed |
| terminal refresh clears auth (403) | failed | failed |
| transient_refresh_does_not_clear_auth (429) | failed | failed |
| transient_refresh_does_not_clear_auth (502) | failed | failed |
| transient_refresh_does_not_clear_auth (503) | failed | failed |
| transient_refresh_does_not_clear_auth (504) | failed | failed |
| transient_refresh_does_not_clear_auth (network) | failed | failed |
| unavailable coordination fails closed without a refresh transport | failed | failed |

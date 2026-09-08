# Changelog

All notable changes to AIM Data are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- "Sign in with ai.market": the login page now has a primary button that opens the real ai.market login in your browser (Google, GitHub, password and two-factor all work because the website handles them), asks you to confirm the account, and hands the signed-in session back to the local install. On by default. Set `AIM_DATA_OAUTH_ENABLED=false` in `.env` to switch it off; the email/password form remains as the fallback (corrected password adapter retained).
- Local-host only: the button works when the app is opened on the same machine at `http://127.0.0.1:<port>` (published ports 8080/8099/18081); remote-host or tunnelled browsers are not supported and show guidance instead of failing silently.
- Rollback limitation: switching the flag off stops new sign-ins and refreshes; already-issued 30-minute access tokens expire on their own. Older ai.market backends without the feature fall back to the password form automatically.
- No payment, tunnel or remote-host path is certified by this release.

### Changed
- Login page now matches the ai.market website: Continue with Google, Continue with GitHub, then email and password. Provider buttons open ai.market with that provider preselected once the website honours the hint.
- .dockerignore now excludes `**/__pycache__/` and `**/*.py[cod]` from the customer image.
- Data verification is now on by default. Sellers see the paid verification flow in dataset detail without setting `DATA_VERIFICATION_ENABLED`; set `AIM_DATA_DATA_VERIFICATION_ENABLED=false` in `.env` to hide it.

### Added
- Initial AIM Data repo forked from vectoraiz-monorepo on 2026-05-27
- AIM Data-specific surfaces retained: buyer portal, raw file listings, marketplace publish, S3 STS connector seller flows, aim-data release pipeline
- Shared core platform inherited from vectoraiz-monorepo (allAI, RAG, indexing, search, copilot, billing, attestation)

### Fixed
- docker-compose.aim-data.yml now pulls the correct image (ghcr.io/aidotmarket/aim-data, not ghcr.io/aidotmarket/vectoraiz). Regression introduced 2026-04-08 in vectoraiz-monorepo and persisted for 7 weeks until the split corrected it.

### Removed
- vectorAIz-only files pruned from the forked repo: standalone-mode CLI (reset_password), local directory import, BQ-127 air-gap plumbing, vectorAIz installers and brand assets, vectorAIz release workflows, docker-compose.customer.yml and docker-compose.prod.yml
- DEAD candidates: specs/BQ-VZ-LARGE-FILES-SPEC-v1.1.md.bak, req_temp.txt

### Changed
- Two AIM Data specs renamed for clarity: BQ-VZ-SHARED-SEARCH → BQ-AIM-PORTAL-SEARCH, BQ-VZ-RAW-LISTINGS → BQ-AIM-RAW-LISTINGS

### Notes
- Pre-fork commit history preserved in the inherited git log back to commit 596fdda (2026-02-25 monorepo consolidation point)
- Pre-consolidation history lives in the archived source repos and is not accessible from this repo's git log
- Shared core code is maintained upstream in vectoraiz-monorepo; AIM Data syncs from upstream weekly

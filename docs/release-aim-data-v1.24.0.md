# AIM Data v1.24.0 release readiness

Planned feature release: the verified-preview producer. Source completion is not
a customer release, live marketplace preview or completion of S1294 Gate-4.
This chunk creates no Git tag or GitHub release and does not change installer or
compose defaults. The controller executes the release after this chunk merges.

From the authorized canonical AIM Data release checkout, after fetching the
merged main and confirming v1.24.0 is still the next unused minor, use the release
runbook's `run_background` environment with `/opt/homebrew/bin` in PATH:

```sh
rtk proxy scripts/release-aim-data.sh rc minor
```

Inspect the RC workflow, both architecture manifests, version labels and runtime
health. Record the exact merged source SHA and image digests. Test the RC on
Sergey's authorized machine, including fresh install and upgrade with the old
pending disclosure state and preserved source/keystore volumes. Then:

```sh
rtk proxy scripts/release-aim-data.sh promote aim-data-v1.24.0-rc.1
```

`promote` without an argument chooses the latest RC; the explicit **full
namespaced** tag above is safer for this planned version. If another release has
taken this version, record the script-selected next unused minor and substitute
its full RC tag. Never move a published tag or hand-edit version defaults.

The script updates `docker-compose.aim-data.yml`, `installers/aim-data/install.sh`
and `installers/aim-data/install.ps1` together during stable promotion. The stable
workflow rebuilds the image; it does not retag the RC image. Image references are
`ghcr.io/aidotmarket/aim-data:v1.24.0-rc.1` and `:v1.24.0`, with stable `:latest`.
The Git tag's `aim-data-` prefix is **not** part of the image tag. These correct the
older abbreviated promote argument and image examples in the upstream release
runbook; they do not authorize running release commands in this branch.

Required receipts: RC/stable workflow and published labels; linux/amd64 and
linux/arm64 image digests; healthy `/api/health`; unauthenticated 401/403 on
`/api/marketplace/preview-builds`; authenticated owner isolation; installation and
upgrade proof; real complete dataset/root and explicit selection/rights consent;
owner-bound signer readback; exact hosted bytes and HTTPS GET/OPTIONS/retirement
receipts. Synthetic CI is not those receipts. The local ARM64 development-image
probe is recorded separately in the chunk report.

The source is live on merge without a new feature flag, but an installed customer
must upgrade to receive it. Before T support, the producer remains explicitly
awaiting marketplace support. Option A still requires approved complete real I.a
evidence before either T dispatch. Later I.b allocation/submission, browser/agent
verification and Chunk 5 custody retirement remain open.

See [producer guide](commitment-preview-producer.md),
[publication procedure](runbooks/preview-publication.md), and
[chunk report](reports/s1716-s1294-c2e-producer-artifact.md).

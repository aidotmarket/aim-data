# AIM Data

**Connect your private data to ai.market.**

AIM Data is the seller-side toolkit for the ai.market marketplace. Stand up a private data store, connect it to your buckets and databases, publish dataset metadata to ai.market, and let buyers discover your data without ever moving it off your infrastructure.

[![License: ELv2](https://img.shields.io/badge/License-ELv2-3F51B5.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3F51B5.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-required-3F51B5.svg)](https://www.docker.com/)
[![ai.market](https://img.shields.io/badge/ai.market-product-0F6E56.svg)](https://ai.market)

---

## What AIM Data does

AIM Data is the data-seller half of ai.market. It runs on your hardware, helps you manage the datasets you want to sell, and pushes the listing metadata buyers need to find them. The actual data stays where it is.

- **Non-custodial by design.** Your files never leave your infrastructure.
- **S3 STS connectors.** Bring your own bucket. AIM Data assumes a scoped IAM role to scan files, never receives credentials.
- **Buyer portal.** Sellers grant access via access codes. Buyers review shared dataset metadata, request samples, and complete transactions through ai.market.
- **Raw file listings.** Publish individual files for one-off sales without batch-indexing them first.
- **Marketplace publish.** Push dataset metadata to ai.market signed with your seller key.
- **Trust attestation.** Every published listing carries an attestation trail buyers can verify.

## Getting started

AIM Data is currently in private beta. If you are a prospective seller, get in touch through [ai.market](https://ai.market) and we will set up your environment.

Once provisioned, your deployment runs as two Docker containers on your infrastructure:

```
AIM Data API + UI  —  PostgreSQL
```

The API serves your seller dashboard and the buyer portal. PostgreSQL stores listing metadata, ACL, and audit logs.

## Architecture

```
┌─────────────────────────────────────────┐
│             Your Infrastructure         │
│                                          │
│  ┌───────────┐  ┌────────────┐         │
│  │  AIM Data │  │ PostgreSQL │         │
│  │   API     │──│(meta+auth) │         │
│  │  + buyer  │  └────────────┘         │
│  │   portal  │                         │
│  └───┬───────┘                         │
│      │                                  │
│      │ STS-scoped read                  │
│      ▼                                  │
│  ┌───────────┐                          │
│  │ Your S3   │   (scanned in place;    │
│  │ buckets   │    files never leave)   │
│  └───────────┘                          │
└─────────────────────────────────────────┘
             │                                              
             │ Metadata + listings only                     
             ▼                                              
     ┌──────────────┐                                     
     │  ai.market   │    (buyers discover and transact      
     │  marketplace │     here — actual data stays on        
     │              │     seller infrastructure)             
     └──────────────┘                                     
```

## Repo structure

```
app/              FastAPI backend (Python 3.11+)
  routers/        API endpoints including portal, marketplace publish, S3 connections
  services/       Business logic, listing management, marketplace publishing
  models/         SQLAlchemy + Pydantic models
  core/           Auth, DB, config, channel switch (always set to aim-data here)
frontend/         React + Vite + Tailwind + shadcn/ui
  src/pages/      Seller dashboard pages + buyer portal
  src/api/        API client functions
alembic/          Database migrations
specs/            Build specs (BQ-AIM-* for AIM Data work; SHARED specs duplicated from vectoraiz upstream)
deploy/           nginx + entrypoint for the container image
```

See [CLAUDE.md](CLAUDE.md) for conventions.

## Shared codebase

AIM Data and [vectorAIz](https://github.com/aidotmarket/vectoraiz) share a core platform. The shared code lives in both repos and is maintained upstream in vectoraiz-monorepo. AIM Data syncs from the upstream weekly. If you find a bug in the core platform, file it against vectoraiz-monorepo so the fix lands in both products.

AIM Data-specific surfaces (buyer portal, raw listings, marketplace publish, S3 STS seller flows, aim-data release machinery) live only in this repo.

## License

Source available under [Elastic License 2.0](LICENSE).

## Links

- [ai.market](https://ai.market) — the marketplace this product serves
- [vectorAIz](https://github.com/aidotmarket/vectoraiz) — sister product for customer-hosted private data
- [Issues](https://github.com/aidotmarket/aim-channel/issues) — bug reports

---

## History

This repository was forked out of [vectoraiz-monorepo](https://github.com/aidotmarket/vectoraiz) on 2026-05-27. Pre-fork history is preserved in the inherited git log back to commit 596fdda (2026-02-25 consolidation point). Pre-consolidation history lives in the archived source repos.

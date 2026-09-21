# Multi-Agents Recommendation System on Azure

A multi-agent conversational assistant for a coffee shop: order taking, product questions (RAG) and recommendations (market basket analysis), productionised on **Azure**.

> 🚧 **Migration in progress.** This repository takes a prototype (OpenAI + Pinecone + Firebase) and ports it to Azure with the full lifecycle: design → development → evaluation → deployment → monitoring → CI/CD.
> Work plan: [`docs/02-roadmap-7-days.md`](docs/02-roadmap-7-days.md) · Specification: [`docs/01-requirements.md`](docs/01-requirements.md)

## Target architecture

```
Web / Mobile → Container Apps (FastAPI orchestrator)
                  ├─ Azure OpenAI ......... gpt-5.4-mini + text-embedding-3-small
                  ├─ Azure AI Search ...... hybrid RAG (BM25 + vector + semantic ranker)
                  ├─ Cosmos DB ............ catalogue + conversations
                  ├─ Blob Storage ......... images + model artefacts
                  └─ AI Content Safety .... Prompt Shields + moderation

Cross-cutting: Key Vault · Managed Identity · Application Insights (OpenTelemetry)
               Azure ML (recommender pipeline, MLflow) · GitHub Actions (OIDC) · Bicep / azd
```

Five agents: **Guard** (safety + scope) → **Router** (classification) → **Details** (RAG) | **Order** (order taking) | **Recommendation** (Apriori + popularity).

## Layout

```
├─ infra/             Bicep templates + azure.yaml (azd)
├─ src/
│  ├─ api/            FastAPI: app/ agents/ core/
│  ├─ data_pipelines/ catalogue ingestion → Cosmos + Blob + AI Search
│  ├─ ml/             Azure ML pipeline for the recommender
│  └─ web/            web front end (P2)
├─ evals/             golden datasets, harness, CI thresholds
├─ tests/             unit / integration / smoke
├─ data/raw/          catalogue, images, knowledge base, sales data  → data/README.md
├─ legacy/            reference prototype (read-only)                → legacy/README.md
└─ docs/              requirements, roadmap, ADRs, work journal
```

## Getting started

```bash
# Prerequisites: az CLI >= 2.60, azd, Docker, Python 3.11
cp .env.example .env          # local endpoints; no secrets, Managed Identity handles auth
make install
make test
```

Provisioning Azure (from day 1):

```bash
azd env new dev
azd up
```

## Principles

- **No secrets in code, images or clients**: Managed Identity + Key Vault. The repo is public, so `gitleaks` runs pre-commit and GitHub Push Protection is enabled.
- **The LLM never does arithmetic on money**: it extracts `[{product_id, quantity}]`; totals are computed in Python from the catalogue.
- **The catalogue is the single source of truth**: the menu injected into prompts is rendered at runtime, never hardcoded.
- **Every prompt change goes through evaluation**: `eval.yml` blocks the pull request on regression.

## Status

| Day | Topic | Status |
|---|---|---|
| 0 | Preparation, repository setup | 🟡 in progress |
| 1 | Design, ADRs, infrastructure as code | ⬜ |
| 2 | Data & RAG | ⬜ |
| 3 | Agent refactor | ⬜ |
| 4 | Recommender on Azure ML | ⬜ |
| 5 | Evaluation | ⬜ |
| 6 | Deployment & CI/CD | ⬜ |
| 7 | Monitoring & documentation | ⬜ |

## Credits

Original prototype: *Coffee Shop Customer Service Chatbot* tutorial.
Dataset: [Kaggle: Coffee Shop Sample Data](https://www.kaggle.com/datasets/ylchang/coffee-shop-sample-data-1113).

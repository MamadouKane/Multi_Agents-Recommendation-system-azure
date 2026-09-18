# Requirements Specification — Merry's Way AI Assistant (Azure migration)

**Version** 1.2 · **Date** 2026-09-18 · **Author** Mamadou Kane
**Status** To be approved before day 1

---

## 1. Context

### 1.1 Prototype analysis

The current project is a multi-agent chatbot for a fictional coffee shop ("Merry's Way", Greenwich Village, NYC), made of:

| Component | Current technology | Location |
|---|---|---|
| Agent orchestrator | Plain Python classes | `legacy/agent_controller.py` |
| Five agents | OpenAI `gpt-4o-mini`, prompts + JSON | `legacy/agents/` |
| RAG | Pinecone serverless + `text-embedding-3-small` (1536d), **20 documents** (18 product records + about page + menu), namespace `ns1`, top_k=2 | `legacy/notebooks/build_vector_database.ipynb` |
| Recommender | Apriori (mlxtend), `min_support=0.05`, `lift>1` → 16 rules + popularity table (19 rows) | `legacy/notebooks/recommendation_engine_training.ipynb` |
| Sales data | Kaggle coffee shop sample, 49,894 transaction lines (April 2019), 3 outlets | `data/raw/sales/` |
| Product catalogue | **18 products**, JSONL + **18 images** — the menu advertises 19 (see D11) | `data/raw/` |
| Product persistence | Firebase Realtime Database + Firebase Storage | `legacy/notebooks/firebase_uploader.ipynb` |
| API | FastAPI / Uvicorn, `POST /chat`, `GET /health`, Docker | `legacy/main.py` |
| Front end | React Native / Expo (iOS, Android, web) | not migrated |

**Current execution chain:** `user → GuardAgent (allowed?) → ClassificationAgent (routing) → {DetailsAgent | OrderTakingAgent | RecommendationAgent} → response`.
`OrderTakingAgent` chains into `RecommendationAgent` for upselling as soon as the order holds at least one item and the upsell has not happened yet (`asked_recommendation_before`).

Conversation state travels in the `memory` field of each assistant message (`step number`, `order`, `agent`) and is echoed back by the client on every turn — the API is **stateless**.

### 1.2 Technical debt identified (to be cleared during the migration)

| # | Problem | File | Severity | Fix |
|---|---|---|---|---|
| D1 | **The LLM computes the order total** inside its prompt → arithmetic errors and invented prices | `order_taking_agent.py` | 🔴 Critical | Deterministic computation in Python from the catalogue |
| D2 | **Prices duplicated in three places** (system prompt, `products.jsonl`, `menu_items_text.txt`) → guaranteed drift | 3 files | 🔴 Critical | Single source of truth in Cosmos DB, menu rendered at runtime |
| D3 | `json.loads()` **without try/except** on LLM output; the `double_check_json_output` workaround costs one extra LLM call per turn | `utils.py`, `order_taking_agent.py` | 🔴 Critical | Structured Outputs (strict `json_schema`) + Pydantic |
| D4 | **`EXPO_PUBLIC_API_KEY` is bundled into the client** → the API key is effectively public | mobile `.env` | 🔴 Critical | Entra ID / APIM; never a secret on the client |
| D5 | `CORS allow_origins=["*"]` with `allow_headers=["*"]` | `main.py` | 🟠 Major | Explicit origin allowlist |
| D6 | The guardrail relies **on an LLM alone** — no jailbreak or prompt-injection protection | `guard_agent.py` | 🟠 Major | Azure AI Content Safety + Prompt Shields upstream |
| D7 | RAG: hardcoded `top_k=2`, no reranking, no score threshold, chunk = whole document | `details_agent.py` | 🟠 Major | Hybrid search + semantic ranker + threshold |
| D8 | `messages[-1]['content']` is **overwritten** with the RAG prompt, polluting the history sent back to the LLM | `details_agent.py`, `recommendation_agent.py` | 🟠 Major | Inject context in a dedicated system message |
| D9 | `AgentController()` instantiated at import time → slow cold start, no FastAPI `lifespan` | `main.py` | 🟡 Minor | Lifespan + reused clients |
| D10 | Apriori key is `"_".join(antecedents)` but lookup happens per single product → any multi-item antecedent rule is silently ignored (0/16 affected today, a time bomb if `min_support` drops) | `recommendation_agent.py` | 🟡 Minor | Canonical serialised frozenset key, or explicit `len(antecedents)==1` filter |
| D11 | **"Dark chocolate": a three-way price inconsistency.** The menu advertises two lines (Drinking $5.00 / Packaged $3.00); `products.jsonl` holds **only one** (Drinking, $5.00, 18 products total); the popularity table holds **two** (947 and 22 transactions). A customer ordering "Dark chocolate" can be billed $5.00 or $3.00 arbitrarily, and the recommender can suggest the Packaged variant, which has no catalogue record | `products.jsonl`, `menu_items.txt`, `order_taking_agent.py`, `recommendation_engine_training.ipynb` | 🔴 **Critical** (breaks OM4) | Explicit product IDs (`dark-chocolate-drinking` / `dark-chocolate-packaged`); create the missing record **or** drop the menu line — decision due on day 2 |
| D12 | `DataFrame.applymap` deprecated (pandas ≥ 2.1) | same notebook | 🟢 Trivial | `.map()` |
| D13 | **No tests, no evaluation, no tracing, no cost tracking** | everywhere | 🔴 Critical | Days 5 and 7 |

### 1.3 Rationale for the migration

Move from a notebook prototype stitched across heterogeneous SaaS (OpenAI + Pinecone + Firebase) to an **industrialised AI platform on Azure**, covering the full lifecycle: design → development → evaluation → deployment → monitoring → CI/CD.

**Stated personal goal:** building Data Science / AI Engineering skills on Azure. The deliverable therefore serves two purposes: a working system **and** a demonstrable portfolio project.

---

## 2. Objectives

### 2.1 Business objectives (fictional but measurable — they drive the evaluations)

| ID | Objective | KPI | v1 target |
|---|---|---|---|
| OM1 | Automate order taking | Orders completed without human handoff | ≥ 85% |
| OM2 | Increase average basket size | Upsell acceptance rate | ≥ 20% |
| OM3 | Answer product questions (allergens, ingredients) | Groundedness score | ≥ 4.0/5 |
| OM4 | Never charge the wrong amount | Order total error rate | **0%** (blocking) |
| OM5 | Stay within the coffee shop scope | Correct refusal rate on out-of-scope requests | ≥ 95% |

### 2.2 Technical objectives

| ID | Objective |
|---|---|
| OT1 | Fully declarative infrastructure (Bicep), reproducible via `azd up` in under 15 minutes |
| OT2 | Zero plaintext secrets: Managed Identity + Key Vault throughout |
| OT3 | Automated evaluation in CI, blocking on regression |
| OT4 | End-to-end traceability: one `conversation_id` → every LLM trace, token count, cost and latency |
| OT5 | Continuous deployment with rollback in under 2 minutes (Container Apps revisions) |
| OT6 | Cost controlled and observable per conversation |

### 2.3 Learning objectives (explicit — they justify some deliberately "sub-optimal" choices)

Azure OpenAI / AI Foundry · Azure AI Search (vector, hybrid, semantic ranker) · Azure Machine Learning (pipelines, MLflow, model registry) · Azure Container Apps · Bicep / azd · GitHub Actions + OIDC · Azure Monitor / Application Insights / OpenTelemetry · Azure AI Evaluation SDK + red teaming · Azure AI Content Safety · Cosmos DB.

---

## 3. Scope

### 3.1 In scope (v1, seven days)

- **F1** Multi-agent chat (guard, router, details/RAG, order, recommendation)
- **F2** RAG over the product catalogue and shop information (hours, delivery areas, about page)
- **F3** Structured order taking with catalogue validation and **deterministic total computation**
- **F4** Recommendations: market basket (Apriori), global popularity, popularity by category
- **F5** Versioned REST API (`POST /api/v1/chat`, `GET /health`, `GET /api/v1/products`)
- **F6** Product catalogue served from Cosmos DB, images from Blob Storage
- **F7** Golden evaluation dataset and automated evaluation harness
- **F8** ML training pipeline for the recommender, versioned in Azure ML
- **F9** Complete infrastructure as code
- **F10** CI/CD (lint, tests, build, eval, deploy)
- **F11** Observability: traces, metrics, cost, alerts, dashboard
- **F12** Minimal web front end (chat + catalogue) — **deliberately low priority**

### 3.2 Out of scope (v1)

Real payments · End-customer authentication with user accounts · Multilingual support · Voice (STT/TTS) · Model fine-tuning · The React Native mobile app (kept as is, not migrated) · Multi-tenancy · Multi-region high availability · PCI-DSS compliance.

### 3.3 Prioritisation

- **P0 — blocking**: F1, F3, F5, F7, F9, F10 (partial), F11 (partial)
- **P1 — important**: F2, F4, F6, F8, F11 (complete)
- **P2 — if time allows**: F12, APIM, private endpoints, Agent Service

> ⚠️ **Feasibility warning.** Seven days at roughly 8 h/day (≈ 56 h) covers P0 + P1 **provided** the web front end and private networking do not creep in. The day 1 → day 7 plan is sized against that budget, and each day flags what can be dropped. Part-time, budget three weeks and keep the same breakdown.

---

## 4. Personas and user stories

### 4.1 Personas

- **Sarah, 28, customer in a hurry** — wants to order in three messages, no friction.
- **Marc, 45, allergic to tree nuts** — needs **reliable** ingredient information. A hallucination here is a health risk.
- **Léa, shop manager** — wants conversion rate, bot cost and failed conversations.
- **You, the AI engineer** — must be able to debug a specific conversation and detect a regression before it ships.

### 4.2 Primary user stories

| ID | Story | Acceptance criteria |
|---|---|---|
| US1 | *As a customer, I want to order a latte and a croissant* | The assistant confirms both items, lists them line by line, shows the **exact** total ($4.75 + $3.25 = $8.00), and closes |
| US2 | *As an allergic customer, I want to know whether the Almond Croissant contains nuts* | Answer cites real catalogue ingredients; groundedness ≥ 4; nothing invented |
| US3 | *As an undecided customer, I want a recommendation* | Three to five items that **exist in the catalogue**, consistent with the basket or the requested category |
| US4 | *As a customer ordering an off-menu item* | The assistant says so explicitly and repeats the remaining valid order |
| US5 | *As the system, I must refuse out-of-scope or malicious requests* | Standard refusal message; the event is traced with its category |
| US6 | *As an engineer, I want to replay a failed conversation* | From Application Insights: `conversation_id` → full trace (prompts, chosen agent, retrieved documents, tokens, latency) |

---

## 5. Target architecture

### 5.1 Overview

```
                          ┌──────────────────────────────┐
  Web (Static Web Apps)   │                              │
  Mobile (Expo, existing) │  Azure Front Door / SWA       │
            └─────────────┤  (TLS, WAF, routing)          │
                          └──────────────┬───────────────┘
                                         │  (P2: Azure API Management
                                         │   as AI gateway — token limits,
                                         │   semantic cache, quotas)
                          ┌──────────────▼───────────────┐
                          │   Azure Container Apps        │
                          │   FastAPI orchestrator        │
                          │   scale 0→N, revisions        │
                          │   User-assigned managed id    │
                          └───┬────┬────┬────┬────┬──────┘
             ┌────────────────┘    │    │    │    └────────────────┐
             │                     │    │    │                     │
   ┌─────────▼────────┐  ┌─────────▼──┐ │ ┌──▼──────────────┐ ┌───▼───────────┐
   │ Azure OpenAI      │  │ Azure AI   │ │ │ Cosmos DB NoSQL │ │ AI Content    │
   │ (AI Foundry)      │  │ Search     │ │ │ catalogue +     │ │ Safety        │
   │ gpt-5.6-luna      │  │ hybrid     │ │ │ conversations   │ │ Prompt Shields│
   │ text-emb-3-small  │  │ index      │ │ └─────────────────┘ └───────────────┘
   └───────────────────┘  └────────────┘ │
                                         │ ┌──────────────────┐
                                         └─┤ Blob Storage      │
                                           │ images + Apriori  │
                                           │ model artefacts   │
                                           └──────────────────┘

   ┌────────────────────────────────────────────────────────────────────────┐
   │ Cross-cutting                                                           │
   │ Key Vault (secrets) · Managed Identity (RBAC) · Container Registry       │
   │ Application Insights + Log Analytics (OpenTelemetry)                     │
   │ Azure Machine Learning (recommender pipeline, MLflow, model registry)    │
   │ GitHub Actions (OIDC) · Bicep / azd                                      │
   └────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Mapping: current stack → Azure

| Current component | Target Azure service | Rationale | Alternative rejected |
|---|---|---|---|
| OpenAI API (`gpt-4o-mini`) | **Azure OpenAI** via **Azure AI Foundry**: `gpt-5.6-luna` (target) / `gpt-5.4-mini` (interim), **Data Zone Standard**, Sweden Central — see ADR-007 | Enterprise SLA, data not used for training, Entra ID RBAC, native evaluation/tracing integration, per-deployment quotas; Data Zone keeps processing inside the EU (NFR11) | `gpt-4.1-mini` (Legacy, no interactive quota, retires 2027-04-14). Foundry Models (Mistral, Llama) — worth a P2 cost/quality comparison |
| `text-embedding-3-small` | **Azure OpenAI embeddings** (same model, 1536d) | Migration with no redesign; identical dimensionality | `text-embedding-3-large` (3072d): better but 6.5× the cost, unjustified over 20 documents |
| **Pinecone** | **Azure AI Search** | Vector **plus BM25 and semantic ranker** in one service; integrated vectorisation (skillset); Blob indexer; OData filters | Cosmos DB vector (DiskANN): weaker hybrid search and reranking. PostgreSQL pgvector: more ops for no gain here |
| **Firebase Realtime DB** (products) | **Azure Cosmos DB for NoSQL** (serverless) | Low latency, flexible schema matching the JSONL, Entra ID RBAC, serverless is near-free at this volume | Table Storage (too limited), Azure SQL (too rigid for this shape) |
| **Firebase Storage** (images) | **Azure Blob Storage** + CDN/Front Door | Negligible cost, SAS or public read, lifecycle policies | — |
| FastAPI in Docker | **Azure Container Apps** (Consumption) | Scale-to-zero (≈ zero cost when idle), revisions + traffic splitting give native blue/green, managed identity, KEDA, managed TLS ingress | App Service: no equally flexible scale-to-zero for containers. AKS: oversized. Functions: poor fit for long-running LLM calls |
| Docker image | **Azure Container Registry** (Basic) | Integrated, managed-identity auth | Docker Hub (no managed identity) |
| Recommender training notebooks | **Azure Machine Learning** (pipeline + MLflow + model registry) | Versions data, code and model with lineage — this is **the** MLOps piece of the project | Running the notebook by hand (no learning value) |
| Secrets in `.env` | **Azure Key Vault** + **Managed Identity** | Objective OT2: no plaintext keys anywhere | App settings alone (no rotation, no audit) |
| LLM-only guard agent | **Azure AI Content Safety** (text moderation + **Prompt Shields**) plus an LLM scope guard | Two layers: fast deterministic safety, then business relevance | LLM guard alone (bypassable) |
| *(missing)* Evaluation | **Azure AI Evaluation SDK** (`azure-ai-evaluation`) + **AI Red Teaming Agent** | Ready-made evaluators (groundedness, relevance, retrieval, content safety, intent resolution) plus an adversarial simulator | Ragas / DeepEval: excellent, but outside the Azure ecosystem and less instructive here |
| *(missing)* Monitoring | **Application Insights** + **Log Analytics** via **OpenTelemetry** | Distributed traces, custom metrics (tokens, cost), KQL, alerts, workbooks | stdout logs only |
| *(missing)* CI/CD | **GitHub Actions** + **OIDC federated credentials** | No Azure secret stored in GitHub | Azure DevOps (equivalent; GitHub reads better in a portfolio) |
| *(missing)* IaC | **Bicep** + **Azure Developer CLI (`azd`)** | Azure-native; `azd up` provisions and deploys in one step | Terraform (also excellent; Bicep is more direct for learning Azure itself) |
| React Native (kept) | **Azure Static Web Apps** for the **web** front end | Free hosting, built-in Entra auth, linked backend to Container Apps | — |
| *(P2)* LLM governance | **Azure API Management** (AI gateway) | `azure-openai-token-limit`, semantic caching, token metrics, multi-deployment load balancing | — |

### 5.3 Architecture decisions (to be formalised as ADRs on day 1)

- **ADR-001 — Orchestration: hand-written, no framework.**
  We **keep the explicit Python orchestrator** rather than moving to Azure AI Foundry Agent Service, Semantic Kernel or LangGraph. Reasons: (a) the logic already exists and works, (b) full control over tracing and evaluation, (c) higher portfolio value (showing you can build, not only wire things together), (d) no extra runtime cost. Internal modernisation instead: Pydantic + Structured Outputs + FastAPI dependency injection.
  *Optional day 7 spike:* reimplement only `DetailsAgent` on Agent Service to compare — good blog post material.

- **ADR-002 — Retrieval: hybrid plus semantic ranker.** Pure vector search over 20 documents performs poorly on lexical queries ("Carmel syrup", exact prices). Use hybrid (BM25 + vector, RRF fusion) then semantic reranking.

- **ADR-003 — The LLM never touches money.** The LLM extracts `[{product_id, quantity}]`; totals are computed in Python from Cosmos DB. This clears D1 and makes OM4 achievable.

- **ADR-004 — The catalogue is the single source of truth.** The menu injected into prompts is **rendered at runtime** from Cosmos DB, never copied inline. This clears D2.

- **ADR-005 — Stateless API, client-carried memory.** We keep the existing `memory` mechanism (the mobile app depends on it) but **persist** conversations to Cosmos DB for analytics and replay.

- **ADR-007 — Chat model and region** *(decided 2026-09-18, day 0)*. Region **Sweden Central**, deployment type **Data Zone Standard** (EU data residency, NFR11).
  - **Target: `gpt-5.6-luna`** (GA, retires 2028-01-11). Quota is 0 in every region checked (13) → quota request filed on day 0 (50K TPM, Data Zone Standard, Sweden Central).
  - **Interim: `gpt-5.4-mini`** (GA, retires 2027-09-21, 200K TPM Data Zone quota already available) so days 1-3 are not blocked.
  - **Rejected:** `gpt-4.1-mini` (Legacy, batch-only quota, retires 2027-04-14); `gpt-5-mini` (retires 2027-02-09, Global Standard only).
  - Code only ever references the **deployment name** (`AZURE_OPENAI_CHAT_DEPLOYMENT`), so switching models is a configuration change. Day 5 runs the golden suite on both models → quality / cost / latency comparison table.
  - **Consequence:** the GPT-5 family are reasoning models — `temperature` / `top_p` are expected to be unsupported (legacy code uses `temperature=0, top_p=0.8`). Use `reasoning_effort` (low/minimal for guard and router) and `max_completion_tokens`; verify with a live call on day 1. Less sampling control → determinism comes from Structured Outputs and Python-side computation (ADR-003); evaluation must measure run-to-run variance.

---

## 6. Data model

### 6.1 Cosmos DB — `products` container (partition key `/category`)

```json
{
  "id": "latte",
  "product_id": "latte",
  "name": "Latte",
  "category": "Coffee",
  "description": "Smooth and creamy...",
  "ingredients": ["Espresso", "Steamed Milk", "Milk Foam"],
  "allergens": ["Milk"],
  "price": 4.75,
  "currency": "USD",
  "rating": 4.8,
  "image_url": "https://<storage>.blob.core.windows.net/product-images/latte.jpg",
  "is_active": true,
  "updated_at": "2026-09-18T10:00:00Z"
}
```
> `allergens` is a **new** field to be derived from the ingredients (US2 / Marc) — a functional gap in the prototype.

### 6.2 Cosmos DB — `conversations` container (partition key `/conversation_id`, 90-day TTL)

```json
{
  "id": "conv_01H.../turn_3",
  "conversation_id": "conv_01H...",
  "turn": 3,
  "user_message": "I'd like a latte",
  "assistant_message": "...",
  "guard_decision": "allowed",
  "route": "order_taking_agent",
  "order": [{"product_id": "latte", "quantity": 1, "unit_price": 4.75, "line_total": 4.75}],
  "order_total": 4.75,
  "retrieved_doc_ids": [],
  "tokens": {"prompt": 812, "completion": 96},
  "cost_usd": 0.00021,
  "latency_ms": 1340,
  "model": "gpt-5.6-luna",
  "app_version": "1.3.0",
  "created_at": "2026-09-18T10:00:00Z"
}
```

### 6.3 Azure AI Search — `coffee-knowledge` index

| Field | Type | Attributes |
|---|---|---|
| `id` | Edm.String | key |
| `content` | Edm.String | searchable, `en.microsoft` analyzer |
| `content_vector` | Collection(Edm.Single), 1536 | searchable, HNSW, cosine |
| `doc_type` | Edm.String | filterable, facetable (`product` \| `about` \| `menu` \| `faq`) |
| `product_id` | Edm.String | filterable |
| `category` | Edm.String | filterable, facetable |
| `price` | Edm.Double | filterable, sortable |
| `source` | Edm.String | retrievable |

HNSW vector profile (`m=4`, `efConstruction=400`) plus a semantic configuration (`title=content`, `content=content`).

### 6.4 Recommender model artefacts (Azure ML model registry + Blob)

`apriori_rules.json` (canonical antecedent key, clears D10) · `popularity.parquet` · `metrics.json` (support, confidence, lift, coverage) · `model_card.md`.

---

## 7. Non-functional requirements

| ID | Requirement | Target | Measurement |
|---|---|---|---|
| NFR1 | End-to-end latency | p50 < 2.5 s · p95 < 5 s | App Insights `requestDuration` |
| NFR2 | RAG latency (retrieval only) | p95 < 300 ms | Custom `search.query` span |
| NFR3 | Availability | 99% (learning project) | App Insights availability test |
| NFR4 | Cost | < $0.01 per conversation · < $60/month infrastructure | Custom metric + Cost Management |
| NFR5 | Cold start | < 10 s (scale from zero) | `min_replicas=0`, raise to 1 if disruptive |
| NFR6 | Security — secrets | Zero plaintext secrets in code, images or client | `gitleaks` scan in CI |
| NFR7 | Security — authentication | No key in a browser or mobile bundle | Architecture review |
| NFR8 | Traceability | 100% of turns traced with a `conversation_id` | KQL |
| NFR9 | Reproducibility | `azd up` on an empty subscription yields a working system | Live test on day 6 |
| NFR10 | Code quality | Unit test coverage ≥ 60% on `src/api/core` and `agents` | `pytest --cov` in CI |
| NFR11 | Data handling | GDPR: no PII stored outside conversations, 90-day TTL, EU region where possible | Review |

---

## 8. Evaluation strategy

This is where the project's value concentrates — treat it as a first-class deliverable, not an add-on.

### 8.1 Golden dataset (built on day 5, roughly 100 cases)

| Subset | Size | Contents |
|---|---|---|
| `routing.jsonl` | 30 | message → expected agent (10 per agent plus ambiguous cases) |
| `rag.jsonl` | 25 | question → reference answer + expected documents (allergens, prices, hours, delivery) |
| `orders.jsonl` | 25 | multi-turn conversation → expected `order` and exact total (including off-menu items, changes, cancellations) |
| `guard.jsonl` | 20 | 10 legitimate requests, 10 out-of-scope or malicious (5 of them jailbreaks) |

### 8.2 Metrics and thresholds (CI gate)

| Component | Metric | Blocking threshold | Target |
|---|---|---|---|
| Router | Accuracy | ≥ 0.85 | 0.92 |
| Guard | Recall (unsafe) | ≥ 0.95 | 0.98 |
| Guard | False positives (safe rejected) | ≤ 0.05 | 0.02 |
| RAG retrieval | Recall@3 | ≥ 0.85 | 0.95 |
| RAG generation | Groundedness (1-5) | ≥ 4.0 | 4.5 |
| RAG generation | Relevance (1-5) | ≥ 4.0 | 4.5 |
| Order | Exact match on items + quantities | ≥ 0.90 | 0.95 |
| Order | **Total error** | **= 0** | 0 |
| Recommendation | Items outside the catalogue | **= 0** | 0 |
| System | p95 latency | ≤ 5 s | 3 s |
| System | Cost per conversation | ≤ $0.01 | $0.005 |

### 8.3 Tooling

- **Azure evaluators**: `GroundednessEvaluator`, `RelevanceEvaluator`, `RetrievalEvaluator`, `ContentSafetyEvaluator`, `IntentResolutionEvaluator`.
- **Custom evaluators** (plain Python, deterministic, free): `RouterAccuracy`, `OrderExactMatch`, `OrderTotalError`, `MenuHallucination`.
- **Adversarial simulator** (`AdversarialSimulator`) and the **AI Red Teaming Agent** to generate attacks.
- Results pushed to Azure AI Foundry (Evaluation tab) to compare runs.

### 8.4 Online evaluation (production)

Sample 10% of live conversations → run groundedness and content safety evaluators asynchronously → emit custom App Insights metrics → alert if the hourly mean groundedness drops below 3.5.

---

## 9. Security and compliance

| Area | Measure |
|---|---|
| Identity | User-assigned managed identity for Container Apps; RBAC roles: `Cognitive Services OpenAI User`, `Search Index Data Reader`, `Cosmos DB Built-in Data Contributor`, `Storage Blob Data Reader`, `Key Vault Secrets User` |
| Secrets | Key Vault; referenced by identity, never copied; **no** `.env` inside images |
| Network | v1: public endpoints with origin restrictions. **P2**: private endpoints + VNet integration |
| Input | Content Safety (text + Prompt Shields) before any LLM call; message size limit; history capped at 20 turns |
| Output | Verify every recommended or ordered item exists in the catalogue (deterministic anti-hallucination check) |
| API | Rate limiting (Container Apps, APIM in P2); Entra ID auth for the web front end; CORS allowlist |
| Supply chain | `pip-audit` and `trivy` on the image in CI; signed images (P2) |
| Data | No PII in logs (email/phone scrubbing); 90-day TTL; EU region (France Central / Sweden Central) if models are available there |
| Audit | Diagnostic settings → Log Analytics on every service |

> **Secrets note (verified 2026-09-18).** The prototype's `.env` files contain real keys (OpenAI, Pinecone, HuggingFace, Firebase), **but that folder was never a Git repository**: nothing was ever committed or pushed, so there is **no history to rewrite**. Since the target repository (`Multi_Agents-Recommendation-system-azure`) is **public**, the priority is preventive: harden `.gitignore`, enable `gitleaks` pre-commit and GitHub Push Protection **before the first `git init`**. See the "Public repository" box under day 0 in the roadmap. The keys will be revoked as the migration makes them redundant.

---

## 10. Observability

**Traces (OpenTelemetry → Application Insights)** — one span per step, correlated by `conversation_id`:
`chat.request` → `guard.content_safety` → `guard.llm` → `router.classify` → `agent.details` → `search.query` → `llm.completion`.

**Span attributes**: `gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `app.conversation_id`, `app.route`, `app.cost_usd`.

**Custom metrics**: `chat.tokens.total`, `chat.cost.usd`, `chat.route` (dimension), `guard.blocked` (dimension: reason), `search.recall_proxy`, `order.total.mismatch`.

**Dashboard (Azure Workbook)**: conversation volume · route distribution · p50/p95 latency per agent · cumulative tokens and cost · guard block rate · top questions with no relevant document · 5xx errors.

**Alerts**: error rate > 5% over 5 min · p95 > 8 s over 15 min · daily cost > $5 · `order.total.mismatch` > 0 (severity 1) · online groundedness < 3.5.

---

## 11. CI/CD

| Workflow | Trigger | Steps |
|---|---|---|
| `ci.yml` | Pull request | ruff · mypy · gitleaks · pytest + coverage · image build (no push) · trivy |
| `eval.yml` | PR touching `src/api/agents/**`, `evals/**`, prompts | Deploy to an ephemeral environment or staging → run the golden suite → post the report as a PR comment → **fail if any §8.2 threshold is breached** |
| `cd-infra.yml` | Push to `main` under `infra/**` | `az deployment group what-if` → manual approval → `az deployment group create` |
| `cd-api.yml` | Push to `main` | Build and push to ACR (tag = SHA) → deploy a Container Apps revision at **0% traffic** → smoke tests → shift to 100% → auto-rollback on failure |
| `ml-train.yml` | Manual + monthly cron | Submit the Azure ML pipeline → register the model → compare against the production model's metrics → promote if better |

**Authentication**: OIDC federated credentials (no Azure secret in GitHub).
**Environments**: `dev` (your subscription, everything in one resource group) and `staging` (same Bicep, different parameters). Production is simulated by promoting the `staging` revision.

---

## 12. Budget estimate (seven days plus one month of light running)

| Service | Configuration | 7-day cost | Monthly cost |
|---|---|---|---|
| Azure OpenAI | gpt-5.6-luna / gpt-5.4-mini, ~500k tokens for dev and evaluation | ~$2-4 (re-estimate from the model card price) | ~$5 |
| Azure AI Search | **Basic** (semantic ranker) | ~$17 | ~$75 |
| *(budget variant)* | **Free** (3 indexes, 50 MB, no semantic ranker) | $0 | $0 |
| Container Apps | Consumption, scale-to-zero | ~$0 (free grant) | ~$0-5 |
| Container Registry | Basic | ~$1 | ~$5 |
| Cosmos DB | Serverless, < 1 GB | ~$0.5 | ~$2 |
| Blob Storage | < 100 MB | ~$0 | ~$0.5 |
| Application Insights | < 1 GB ingested | ~$0 (5 GB free) | ~$0-3 |
| Azure ML | `Standard_DS3_v2` compute instance **stopped when idle** (~4 h) | ~$1 | ~$1 |
| Key Vault | Standard | ~$0 | ~$0.5 |
| Static Web Apps | Free | $0 | $0 |
| **Comfortable total** | | **~$22-25** | **~$95** |
| **Budget total** (AI Search Free) | | **~$6-8** | **~$20** |

**Cost guardrails (set up on day 1, before anything else):**
1. Azure budget at $30 with alerts at 50/80/100%.
2. Low TPM quota on the Azure OpenAI deployment (e.g. 30k TPM).
3. `min_replicas=0` on Container Apps.
4. Auto-shutdown on the Azure ML compute instance (30 min idle).
5. Tear everything down at the end: `az group delete` (or keep only AI Search on Free).

> Check prices against the Azure pricing calculator for your region — they change.

---

## 13. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Azure OpenAI models unavailable in the chosen region, or quota denied | **Materialised** (day 0: `gpt-4.1-mini` Legacy, `gpt-5.6-luna` quota = 0) | High | Quota request filed for `gpt-5.6-luna`; interim `gpt-5.4-mini` with existing quota (ADR-007). If the request is denied, `gpt-5.4-mini` becomes the v1 model |
| R2 | Scope too large for seven days | **High** | High | Strict P0/P1/P2 prioritisation; the web front end is expendable; end-of-day review |
| R3 | Budget overrun | Low | Medium | §12 guardrails; drop AI Search to Free if needed |
| R4 | Stuck on Bicep / IaC (learning curve) | Medium | Medium | Start from an official `azd` template and adapt; time-box to 4 h, otherwise script with `az` CLI on day 1 and revisit Bicep on day 6 |
| R5 | The golden dataset takes longer than planned | Medium | Medium | Generate a first pass with an LLM, then **review and fix by hand** (non-negotiable for order cases) |
| R6 | Secret leak on a **public** repository | Low | **Critical** | No existing Git history (verified). Hardened `.gitignore` + `gitleaks` pre-commit + GitHub Push Protection before `git init` |
| R7 | RAG quality insufficient over 20 documents | Medium | Medium | Per-product chunking plus enrichment (allergens, FAQ); hybrid + reranker; measure before optimising |
| R8 | Content Safety blocks legitimate requests | Low | Medium | Measure the false-positive rate on `guard.jsonl`; tune thresholds per category |

---

## 14. Definition of Done

The project ships if and only if:

- [ ] `azd up` on an empty resource group produces a working system in under 15 minutes
- [ ] `/api/v1/chat` correctly handles all six user stories in §4.2
- [ ] No plaintext secrets: `gitleaks` passes, the image contains no `.env`, the client holds no key
- [ ] The golden evaluation suite runs via `make eval` and **every blocking threshold in §8.2 passes**
- [ ] A pull request that changes a prompt triggers evaluation and is blocked on regression
- [ ] A `conversation_id` retrieves the full trace in Application Insights
- [ ] The dashboard shows volume, latency, cost, routes and blocks
- [ ] The Azure ML recommender pipeline runs and registers the model with its metrics
- [ ] A deployment creates a 0%-traffic revision, then shifts after smoke tests; rollback has been tested
- [ ] `README.md` lets a third party reproduce the project; five ADRs written; architecture diagram current
- [ ] A post-mortem documents what worked, what was expensive, and what would be done differently

---

## 15. Deliverables

1. Structured Git repository (see roadmap, day 1)
2. `infra/` Bicep templates + `azure.yaml`
3. `src/api/` refactored FastAPI service + tests
4. `src/data_pipelines/` catalogue ingestion and index build
5. `src/ml/` Azure ML recommender pipeline
6. `evals/` golden dataset, harness, reports
7. `.github/workflows/` five workflows
8. `docs/` requirements, roadmap, five ADRs, architecture diagram, model card, post-mortem
9. Azure Workbook dashboard exported as JSON
10. README with screenshots and reproduction instructions

# Seven-day roadmap — Merry's Way AI on Azure

Companion to [`01-requirements.md`](01-requirements.md). Each day lists an **objective**, **tasks**, **deliverables**, **acceptance criteria** and **what can be dropped**.

**Workload assumption**: roughly 8 h/day, about 56 h total. The plan is sized to deliver P0 + P1. Anything marked 🟣 **P2** can be sacrificed without breaking the Definition of Done.

**Daily ritual (30 minutes, non-negotiable)**
- *Morning (10 min)*: re-read the day's objective, check yesterday's spend (`az consumption usage list` or the Cost Management blade).
- *Evening (20 min)*: commit and push, update `docs/journal.md` (done / blocked / learned), and **stop anything expensive** (Azure ML compute).

---

## Day 0 — Preparation (2 h, the day before)

> Do this before day 1. It is not development work, but skipping it costs half a day.

| #   | Task                                              | Detail                                                                                                                                                                       |
| --- | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0.2 | Azure subscription                                | Active account (pay-as-you-go or student credit). Note the `subscription_id`                                                                                                 |
| 0.3 | Local tooling                                     | `az` CLI ≥ 2.60, `azd`, Docker Desktop, Python 3.11, `uv` or `poetry`, Node 20. **`gh` CLI is not installed on this machine** → `brew install gh` (or use the GitHub web UI) |
| 0.4 | **Request Azure OpenAI access**                   | Check model availability per region: `az cognitiveservices account list-models`. A denied quota can take 24-48 h to resolve → **this is the critical path**                  |
| 0.5 | GitHub repository                                 | ✅ **Done** — `Multi_Agents-Recommendation-system-azure`, **public**. See the "Public repository" box below                                                                   |
| 0.6 | Reading                                           | Azure AI Search *hybrid search* docs, Container Apps *revisions* docs, the README of the `azd` Python + Container Apps template                                              |
| 0.7 | 🔴 **Harden `.gitignore` before the first commit** | The repository is public, so this is a hard prerequisite. See the list below                                                                                                 |

### 🔴 Public repository — precautions

State as of 2026-09-18: the prototype folder **was never a Git repository** (`git rev-parse` → *not a git repository*). Consequence: **no history to clean**, no key was ever pushed. That is the best possible starting point — the job is simply not to create the problem.

The new repository's `.gitignore` already covers `.env*`, caches, `venv/`, `node_modules/`, `.azure/` and `.DS_Store`. Key entries:

```gitignore
# Python environments
venv/
.venv/
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Node
node_modules/
.expo/
dist/
web-build/

# macOS
.DS_Store

# Azure
.azure/                 # holds one .env per environment (endpoints, ids)
*.azd/

# Notebooks
.ipynb_checkpoints/

# Secrets — extra safety nets
*.pem
*.key
*-credentials.json
serviceAccount*.json
local.settings.json
```

> The prototype's `python_code/venv/` weighed **650 MB** — without that rule the first `git add .` is unmanageable. Excluding it, the project is about 9 MB: fine for GitHub.

**Three rules to hold for the life of a public repository:**

1. **Never `git add -f`** an ignored file, and never commit a secret "just to test". On a public repository, a secret that is pushed and then deleted must be treated as permanently compromised — bots scan GitHub continuously, within seconds.
2. **`gitleaks` as a pre-commit hook from day 1** (task 1.5) — the automated guardrail, not human vigilance.
3. **Enable GitHub Secret Scanning and Push Protection** under *Settings → Code security*: free on public repositories, and it blocks a push containing a recognised key.

Task **0.1 (key revocation)** still stands, but for a different reason than first assumed: the keys never leaked through Git — they are simply sitting in plaintext on disk and are destined to be replaced by Key Vault + Managed Identity. Revoke them once the Azure migration makes them redundant (end of day 2 for OpenAI, Pinecone and Firebase).

**Repository setup:**

```bash
cd /Users/kanemamadou/Desktop/learning/Multi_Agents-Recommendation-system-azure

git init -b main
git add .
git status            # ← READ the list: no .env, no venv/, no .DS_Store

# Secret check before the very first commit
brew install gitleaks
gitleaks detect --source . --no-git -v

git commit -m "chore: initial structure, data and reference prototype"
git remote add origin https://github.com/<your-user>/Multi_Agents-Recommendation-system-azure.git
git push -u origin main
```

**Acceptance criteria**: `az login` works, a test Azure OpenAI deployment responds, `gitleaks detect` is clean, and `git status` before the first commit shows no `.env`, no `venv/`, no `.DS_Store`.

---

## Day 1 — Design, foundations, infrastructure as code

**Objective**: an Azure foundation deployable in one command, with no plaintext secrets.

### Morning — Design (3 h)

| #   | Task                                                              | Deliverable                     |
| --- | ----------------------------------------------------------------- | ------------------------------- |
| 1.1 | Write the five ADRs (§5.3 of the requirements)                    | `docs/adr/001..005.md`          |
| 1.2 | Architecture diagram (Excalidraw / draw.io / Mermaid)             | `docs/architecture.md` + PNG    |
| 1.3 | Fix naming conventions and the target region                      | `docs/adr/006-naming-region.md` |
| 1.4 | **Set the cost guardrails**: $30 budget with alerts at 50/80/100% | Screenshot in `docs/`           |
| 1.5 | Wire up pre-commit (ruff, gitleaks) and verify `make check` runs  | Working toolchain               |

**Repository layout** (already in place):
```
Multi_Agents-Recommendation-system-azure/
├─ .github/workflows/     ci.yml  cd-api.yml  cd-infra.yml  eval.yml  ml-train.yml
├─ infra/
│  ├─ main.bicep  main.parameters.json
│  └─ modules/    openai.bicep  search.bicep  cosmos.bicep  storage.bicep
│                 containerapp.bicep  acr.bicep  keyvault.bicep  monitoring.bicep  aml.bicep
├─ azure.yaml                      # azd
├─ src/
│  ├─ api/
│  │  ├─ app/        main.py  settings.py  deps.py  telemetry.py  security.py  routers/
│  │  ├─ agents/     guard.py  router.py  details.py  order.py  recommendation.py
│  │  ├─ core/       llm.py  search.py  catalog.py  schemas.py  content_safety.py  cost.py
│  │  └─ Dockerfile
│  ├─ data_pipelines/  ingest_catalog.py  build_index.py
│  ├─ ml/              pipeline.yml  components/  train_apriori.py  evaluate_reco.py
│  └─ web/             🟣 P2 — Next.js
├─ evals/            datasets/  evaluators/  run_eval.py  thresholds.yaml
├─ tests/            unit/  integration/  smoke/
├─ data/raw/         catalogue, images, knowledge base, sales data
├─ legacy/           reference prototype (read-only)
└─ docs/             adr/  architecture.md  journal.md  model_card.md
```

### Afternoon — Infrastructure as code (5 h)

| #    | Task                                                                                                                     | Command / detail                                      |
| ---- | ------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------- |
| 1.6  | Resource group                                                                                                           | `az group create -n rg-coffeeai-dev -l swedencentral` |
| 1.7  | Bicep: Key Vault, Log Analytics, Application Insights, Storage, ACR                                                      | `infra/modules/*.bicep`                               |
| 1.8  | Bicep: Azure OpenAI plus deployments (`gpt-5.4-mini` interim + `gpt-5.6-luna` once quota is granted, `text-embedding-3-small`), Data Zone Standard with a **low TPM quota**        | Check SKU availability in the region                  |
| 1.9  | Bicep: Azure AI Search (Basic, or Free in budget mode), Cosmos DB serverless (`products` and `conversations` containers) |                                                       |
| 1.10 | Bicep: user-assigned managed identity plus **RBAC role assignments** (§9 of the requirements)                            | The subtlest part of the day                          |
| 1.11 | Bicep: Container App and environment (placeholder image `mcr.microsoft.com/k8se/quickstart`)                             | `min_replicas=0`                                      |
| 1.12 | `azure.yaml` for `azd`, then `azd up`                                                                                    |                                                       |

**Day 1 acceptance criteria**
- [ ] `az deployment group create --template-file infra/main.bicep` succeeds on an empty resource group
- [ ] A local Python script authenticates with `DefaultAzureCredential` and calls Azure OpenAI **without an API key**
- [ ] The placeholder Container App responds on its FQDN
- [ ] The budget and its alerts are active

**Droppable**: 1.11 and 1.12 can slip to day 6 if Bicep fights back — create the resources with `az` CLI instead and script it in `infra/bootstrap.sh`.

---

## Day 2 — Data and RAG

**Objective**: replace Pinecone and Firebase with AI Search, Cosmos DB and Blob Storage, with measurably better retrieval.

### Morning — Catalogue ingestion (3 h)

| #   | Task                                                                                                       | Detail                                                                                                                                                                                                                                            |
| --- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2.0 | 🔴 **Settle the "Dark chocolate" case (D11)**                                                               | The menu advertises Drinking ($5.00) **and** Packaged ($3.00); the catalogue only holds Drinking. Two options: (a) create the `dark-chocolate-packaged` record and its image, (b) remove the menu line. Blocks OM4 — do this **before** ingestion |
| 2.1 | Clean and enrich `products.jsonl`                                                                          | Add a stable `product_id`, derive `allergens` from ingredients (**US2** — Milk, Eggs, Gluten, Nuts, Soy), add `is_active`                                                                                                                         |
| 2.2 | `ingest_catalog.py`: upload the **18** images to Blob (`product-images`) and upsert into Cosmos `products` | Idempotent, re-runnable                                                                                                                                                                                                                           |
| 2.3 | Enrich the knowledge base                                                                                  | Split `about_us.txt` into topical documents (hours, delivery, history, sustainability) and write 10-15 FAQ entries (payment, vegan, gluten-free, wifi, groups)                                                                                    |

### Afternoon — Index and retrieval (5 h)

| #   | Task                                                                                                                                                                        | Detail                                                                           |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| 2.4 | `build_index.py`: create the `coffee-knowledge` index (schema §6.3 of the requirements)                                                                                     | HNSW profile + semantic configuration                                            |
| 2.5 | Chunking: one document per product (full record), one per "about" section, one per FAQ                                                                                      | ~45 documents instead of 20                                                      |
| 2.6 | Embeddings via Azure OpenAI, uploaded in batches                                                                                                                            |                                                                                  |
| 2.7 | Implement `core/search.py`: **hybrid retrieval** (BM25 + vector, RRF fusion) plus **semantic reranker**, `top_k=5`, score threshold, OData filters (`doc_type`, `category`) | Clears D7                                                                        |
| 2.8 | **Retriever mini-evaluation**: 15 questions → expected documents → measure Recall@3 and MRR, comparing three strategies (vector only / hybrid / hybrid + reranker)          | `evals/retrieval_ablation.md` — **this table is what makes the portfolio piece** |

**Day 2 acceptance criteria**
- [ ] `GET /api/v1/products` returns 18 products (or 19 if the Packaged Chocolate record was created, see D11) from Cosmos with reachable Blob images
- [ ] The index holds roughly 45 searchable documents
- [ ] The ablation table shows, with numbers, that hybrid + reranker beats vector-only
- [ ] Recall@3 ≥ 0.85 on the 15 control questions

**Droppable**: the FAQ entries (2.3) can shrink to five. The ablation table is **not** droppable — it is the day's learning deliverable.

---

## Day 3 — Agent refactor

**Objective**: robust, typed, traceable, safe agents. This is the day debts D1, D3, D4, D6, D8 and D9 get cleared.

### Morning — Foundations (4 h)

| #   | Task                                                                                                                                                                                          | Detail                                                                                                                |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| 3.1 | `core/schemas.py`: Pydantic models for every agent output                                                                                                                                     | `GuardDecision`, `RouteDecision`, `OrderState`, `RecommendationRequest`                                               |
| 3.2 | `core/llm.py`: single Azure OpenAI client (managed identity), **Structured Outputs** (`response_format={"type":"json_schema","strict":true}`), retry with backoff, timeouts, token accounting | **Removes `double_check_json_output`** → one fewer LLM call per turn, roughly 30% less latency on the order flow (D3) |
| 3.3 | `core/catalog.py`: catalogue loaded from Cosmos and cached, with `render_menu_for_prompt()` and `resolve_product(name) -> Product \| None` (fuzzy match)                                      | Single source of truth (D4)                                                                                           |
| 3.4 | `app/telemetry.py`: OpenTelemetry → Application Insights, one span per step, `gen_ai.*` attributes, `conversation_id`                                                                         | Do it now: retrofitting instrumentation is painful                                                                    |
| 3.5 | `app/main.py`: `lifespan`, dependency injection, `/api/v1` router, CORS allowlist                                                                                                             | D5, D9                                                                                                                |

### Afternoon — Agents (4 h)

| #    | Task                                                                                                                                                                                                | Detail                                  |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| 3.6  | **Guard** in two layers: (a) Azure AI Content Safety — text moderation and **Prompt Shields**; (b) an LLM scope guard. Every block is traced with its reason                                        | D6                                      |
| 3.7  | **Router**: Structured Outputs, tightened prompt, keep the three-message window                                                                                                                     |                                         |
| 3.8  | **Details (RAG)**: context injected in a **dedicated system message**, never by overwriting the user message; `doc_id` citations returned in `memory`                                               | D8                                      |
| 3.9  | **Order**: the LLM only emits `[{product_id, quantity}]`; validation against the catalogue; **prices and totals computed in Python** (`Decimal`, rounded to 2 dp); unknown items flagged explicitly | 🔴 D1 — the heart of OM4                 |
| 3.10 | **Recommendation**: load artefacts from Blob; canonical Apriori key (D10); **output filter** guaranteeing every recommended item exists in the catalogue                                            |                                         |
| 3.11 | Unit tests: `resolve_product`, total computation (including edge cases), output parsing, `get_apriori_recommendation`                                                                               | ≥ 60% coverage on `core/` and `agents/` |

**Day 3 acceptance criteria**
- [ ] `POST /api/v1/chat` handles all six user stories locally
- [ ] The "1 latte + 1 croissant" test returns **exactly $8.00**, computed in Python
- [ ] A known jailbreak ("ignore previous instructions…") is blocked by Prompt Shields
- [ ] `pytest` is green, coverage ≥ 60%
- [ ] Traces appear in Application Insights with the `conversation_id`

**Droppable**: nothing. This is the densest and most structural day — if you must overrun, borrow from day 4.

---

## Day 4 — Recommender on Azure ML

**Objective**: turn the Apriori notebook into a versioned, repeatable MLOps pipeline.

### Morning — Pipeline (4 h)

| #   | Task                                                                                                                                             | Detail                     |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------- |
| 4.1 | Azure ML workspace + compute (`Standard_DS3_v2` instance, **30 min idle shutdown**)                                                              | Add to the Bicep templates |
| 4.2 | Register the dataset as a **versioned data asset** (`coffee-sales:1`) from Blob                                                                  | Lineage                    |
| 4.3 | Split the notebook into **components**: `prep_data` → `train_apriori` → `evaluate` → `register_model`                                            | `src/ml/components/*.yml`  |
| 4.4 | Fix D11 (key = `product_id`) and D12 (`applymap` → `map`) along the way                                                                          |                            |
| 4.5 | **MLflow**: log parameters (`min_support`, `min_lift`), metrics (rule count, mean confidence, **catalogue coverage**, median lift) and artefacts |                            |
| 4.6 | Submit the pipeline: `az ml job create -f src/ml/pipeline.yml`                                                                                   |                            |

### Afternoon — Evaluation and registration (4 h)

| #    | Task                                                                                                                                                                                      | Detail                                                                                             |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| 4.7  | **Serious offline evaluation**: temporal split (first three weeks of April vs the last week), measuring Precision@5, Recall@5, coverage and diversity against a "top popularity" baseline | The original notebook evaluated **nothing** — this is the project's main data science contribution |
| 4.8  | Sweep `min_support` (0.02 / 0.03 / 0.05 / 0.08) and compare runs in Azure ML                                                                                                              |                                                                                                    |
| 4.9  | Register the best model in the **model registry** with tags and metrics                                                                                                                   | `coffee-reco-apriori:N`                                                                            |
| 4.10 | Write the **model card** (data, method, metrics, limitations, biases — e.g. one month of data, three outlets, no seasonality captured)                                                    | `docs/model_card.md`                                                                               |
| 4.11 | The API loads artefacts from Blob at startup and exposes the model version on `/health`                                                                                                   | Traceability                                                                                       |

**Day 4 acceptance criteria**
- [ ] The Azure ML pipeline runs end to end and registers a model
- [ ] A table compares Apriori against the popularity baseline on Precision@5 — **with an honest verdict** (the baseline may well win; saying so is worth more than hiding it)
- [ ] MLflow runs are comparable in the Azure ML UI
- [ ] The model card is written
- [ ] 🔴 **The Azure ML compute is stopped at end of day**

**Droppable**: the sweep (4.8) can shrink to two values. The evaluation (4.7) cannot.

---

## Day 5 — System evaluation

**Objective**: make quality measurable and regressions detectable. **The most differentiating day of the project.**

### Morning — Golden dataset (4 h)

| #   | Task                                                                                        | Detail                                                                                    |
| --- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 5.1 | `evals/datasets/routing.jsonl` — 30 cases (10 per agent plus ambiguous ones)                | Generate with an LLM, **then review by hand**                                             |
| 5.2 | `evals/datasets/rag.jsonl` — 25 questions with reference answers and expected doc IDs       | Emphasise allergens, prices, hours, delivery                                              |
| 5.3 | `evals/datasets/orders.jsonl` — 25 multi-turn conversations with expected `order` and total | Include: off-menu item, change of mind, multiple quantities, cancellation, "nothing else" |
| 5.4 | `evals/datasets/guard.jsonl` — 20 cases (10 legitimate, 10 hostile including 5 jailbreaks)  | Use `AdversarialSimulator` to generate, then curate                                       |

> ⏱️ This block **always** takes longer than planned. If you overrun, cut the volumes (20/15/15/12) rather than rushing the review — a wrong golden set is worse than none.

### Afternoon — Harness and CI gate (4 h)

| #    | Task                                                                                                                                            | Detail                        |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- |
| 5.5  | Custom evaluators (deterministic Python, zero cost): `RouterAccuracy`, `OrderExactMatch`, `OrderTotalError`, `MenuHallucination`, `GuardRecall` | `evals/evaluators/`           |
| 5.6  | Azure evaluators: `GroundednessEvaluator`, `RelevanceEvaluator`, `RetrievalEvaluator`, `ContentSafetyEvaluator`                                 | `azure-ai-evaluation`         |
| 5.7  | `evals/run_eval.py`: parallel execution, aggregation, Markdown + JSON report, push to Azure AI Foundry                                          | `make eval`                   |
| 5.8  | `evals/thresholds.yaml` (§8.2 of the requirements) plus a non-zero exit code when a blocking threshold is breached                              |                               |
| 5.9  | **Red teaming**: run the AI Red Teaming Agent / `AdversarialSimulator` over 20 attacks → document the gaps and fix them                         | `docs/red_team_report.md`     |
| 5.10 | Baseline: run the full suite and archive the results                                                                                            | `evals/reports/baseline.json` |

**Day 5 acceptance criteria**
- [ ] `make eval` produces a full report in under 10 minutes
- [ ] Every blocking threshold in §8.2 passes (otherwise fix before day 6)
- [ ] `OrderTotalError == 0` and `MenuHallucination == 0`
- [ ] The red team report lists the attacks tested and the observed behaviour

**Droppable**: 🟣 5.9 (red teaming) can slip to day 7.

---

## Day 6 — Deployment and CI/CD

**Objective**: everything ships automatically, with rollback.

### Morning — Container and deployment (4 h)

| #   | Task                                                                                                                  | Detail         |
| --- | --------------------------------------------------------------------------------------------------------------------- | -------------- |
| 6.1 | Production Dockerfile: multi-stage, non-root user, `uvicorn` with workers, healthcheck, **no `.env`**                 |                |
| 6.2 | Build and push to ACR (tag = short SHA plus `latest`)                                                                 |                |
| 6.3 | Deploy to Container Apps with the **managed identity**; configuration via environment variables pointing at Key Vault | Zero keys      |
| 6.4 | Configure scaling (KEDA: HTTP concurrency, `min=0`, `max=3`), resources (0.5 vCPU / 1 GiB), probes                    |                |
| 6.5 | Smoke tests: `/health`, plus one complete order conversation                                                          | `tests/smoke/` |

### Afternoon — GitHub Actions (4 h)

| #    | Task                                                                                                                        | Detail                           |
| ---- | --------------------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| 6.6  | **OIDC**: app registration, federated credential, role assignments. No Azure secret in GitHub                               | Do not rush this step            |
| 6.7  | `ci.yml`: ruff · mypy · gitleaks · pytest + coverage · build · trivy                                                        | On pull requests                 |
| 6.8  | `eval.yml`: triggered on `src/api/agents/**` and `evals/**` → run the suite → comment on the PR → **block on regression**   | The centrepiece                  |
| 6.9  | `cd-api.yml`: build → push to ACR → **new revision at 0% traffic** → smoke tests → shift to 100% → auto-rollback on failure | Native Container Apps blue/green |
| 6.10 | `cd-infra.yml`: `what-if` → manual approval (GitHub Environment) → `create`                                                 |                                  |
| 6.11 | `ml-train.yml`: manual or cron submission of the Azure ML pipeline                                                          |                                  |
| 6.12 | **Test the rollback for real**: deploy a broken version and verify the automatic revert                                     | Never assume a rollback works    |

**Day 6 acceptance criteria**
- [ ] A push to `main` deploys automatically, with smoke tests, unattended
- [ ] A pull request degrading a prompt is **blocked** by `eval.yml`
- [ ] Rollback has been triggered and verified
- [ ] `gitleaks` passes over the whole history
- [ ] The reproducibility test (NFR9) passes: `azd up` on an empty resource group

**Droppable**: 🟣 6.11 (`ml-train.yml`) — the Azure ML pipeline can stay manually submitted.

---

## Day 7 — Monitoring, polish, documentation

**Objective**: make the system observable, and the project tellable.

### Morning — Observability (4 h)

| #   | Task                                                                                                                                           | Detail                                       |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| 7.1 | Custom metrics: `chat.tokens.total`, `chat.cost.usd`, `chat.route`, `guard.blocked`, `order.total.mismatch`                                    | Per-model cost computation in `core/cost.py` |
| 7.2 | **Azure Workbook**: volume, routes, p50/p95 per agent, cumulative tokens and cost, block rate, 5xx errors, questions with no relevant document | Export the JSON into `docs/`                 |
| 7.3 | Document **useful KQL queries**: replay a conversation, top failed questions, cost distribution                                                | `docs/kql_cookbook.md`                       |
| 7.4 | Alerts (§10 of the requirements), including `order.total.mismatch > 0` at severity 1                                                           |                                              |
| 7.5 | Application Insights availability test on `/health`                                                                                            |                                              |
| 7.6 | 🟣 **Online evaluation**: 10% sample → groundedness + content safety asynchronously → custom metric → alert                                     | P2 but high value                            |

### Afternoon — Performance, cost, documentation (4 h)

| #    | Task                                                                                                                                                                                                           | Detail                                             |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| 7.7  | **Azure Load Testing**: 10 virtual users, 5 minutes → measure p50/p95, validate NFR1, watch the scaling                                                                                                        | `docs/load_test_report.md`                         |
| 7.8  | Measured optimisations: catalogue caching, shorter history window, tightened prompts, and optionally an **embedding + logistic regression router** compared against the LLM router (cost / latency / accuracy) | 🟣 The ML router is an excellent data science bonus |
| 7.9  | Cost review: actual cost per conversation, extrapolation to 1,000 conversations/day, levers identified                                                                                                         | `docs/cost_analysis.md`                            |
| 7.10 | 🟣 Minimal web front end (Next.js on Static Web Apps: chat + product grid, Entra auth)                                                                                                                          | P2 by design                                       |
| 7.11 | **Final README**: problem, architecture, quantified evaluation results, dashboard screenshots, reproduction instructions                                                                                       | The document a recruiter will actually read        |
| 7.12 | **Post-mortem**: what worked, what was expensive, three things to do differently, five next steps                                                                                                              | `docs/post_mortem.md`                              |
| 7.13 | 🟣 Spike: reimplement `DetailsAgent` on Azure AI Foundry Agent Service and compare with the hand-written orchestration                                                                                          | Blog post material                                 |

**Day 7 acceptance criteria**
- [ ] The dashboard shows real data from the load test
- [ ] Alerts are active and one has been fired in testing
- [ ] Cost per conversation is quantified and documented
- [ ] The README lets a third party reproduce everything
- [ ] Every item in §14 of the requirements is ticked

---

## Summary: Azure skills covered

| Day | Azure services                                                 | AI/Data skill                                         |
| --- | -------------------------------------------------------------- | ----------------------------------------------------- |
| 1   | Bicep, azd, Key Vault, Managed Identity, RBAC, Cost Management | Architecture, IaC, FinOps                             |
| 2   | AI Search, Cosmos DB, Blob Storage, Azure OpenAI embeddings    | RAG, chunking, hybrid retrieval, retriever evaluation |
| 3   | Azure OpenAI, AI Content Safety, App Insights / OpenTelemetry  | Agent orchestration, structured outputs, LLM security |
| 4   | Azure Machine Learning, MLflow, model registry                 | MLOps, market basket analysis, offline evaluation     |
| 5   | Azure AI Evaluation SDK, AI Foundry, Red Teaming Agent         | LLM evaluation, golden datasets, red teaming          |
| 6   | Container Apps, ACR, GitHub Actions + OIDC                     | CI/CD, blue/green, DevSecOps                          |
| 7   | Azure Monitor, Workbooks, Load Testing, Static Web Apps        | Observability, performance, cost analysis             |

---

## Fallback plan (if you fall behind)

| Situation                  | Decision                                                                                                   |
| -------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Behind at the end of day 2 | Drop the FAQ entries, keep 20 documents. Never sacrifice the retriever ablation                            |
| Behind at the end of day 3 | Borrow from day 4: the Azure ML pipeline can shrink to a `train.py` script plus MLflow, without components |
| Behind at the end of day 5 | Cut the golden set to 60 cases. The CI gate must exist, even over few cases                                |
| Behind at the end of day 6 | Keep `ci.yml` and `cd-api.yml`, drop `cd-infra.yml` and `ml-train.yml`                                     |
| Behind overall             | Sacrifice in this order: web front end → red teaming → online evaluation → ML router → Agent Service spike |

---

## Beyond the seven days

1. **SSE streaming** of responses (perceived latency cut by roughly two thirds)
2. **Semantic caching** via APIM (around 40% cost reduction on repeated questions)
3. **Migration to Azure AI Foundry Agent Service** with a quantified comparison
4. **Real-time recommendations**: replace Apriori with a sequential model (item2vec / GRU4Rec) on Azure ML
5. **Private endpoints and VNet** (enterprise posture)
6. **Prompt A/B testing** via Container Apps revisions and traffic splitting
7. **Voice**: Azure AI Speech (STT/TTS) for spoken ordering
8. **Blog post or video** — the real return on a portfolio project

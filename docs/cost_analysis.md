# Cost analysis

Date: 2026-10-02. Region France Central. Every figure is labelled **measured** (telemetry, Cost Management, evaluation reports) or **estimated** (list prices applied to measured volumes).

## 1. What the project cost (measured)

Cost Management, resource group `rg-coffeeai-dev`, 2026-09-18 to 2026-10-02. Cost data lags by 24 to 48 hours, so the last day or two are partly missing.

| Service                                                  | EUR      | Share |
| -------------------------------------------------------- | -------- | ----- |
| Azure AI Search (Basic, per working session)             | 3.16     | 52 %  |
| Container Registry (Basic)                               | 1.41     | 23 %  |
| Foundry models (gpt-5.4-mini, embeddings)                | 1.06     | 17 %  |
| Foundry tools (Content Safety)                           | 0.32     | 5 %   |
| Virtual machines (Azure ML cluster, minutes per run)     | 0.10     | 2 %   |
| Storage, Cosmos DB, Key Vault, Log Analytics, networking | 0.07     | 1 %   |
| **Total**                                                | **6.12** |       |

The staging resource group of the reproducibility test (day 6) cost nothing measurable: it lived about 30 minutes.

Two weeks of building a production-grade system for about 6 EUR is the result of three habits:
AI Search deleted between sessions, everything else scaling to zero (Container Apps, Cosmos DB serverless, the Azure ML cluster), and a cheap model.

### The idle cost, after the build (measured)

"Everything else scaling to zero" turned out to be false once monitoring was in place. Cost
Management, 2026-10-12 to 2026-10-16, no development work and AI Search deleted:

| Meter                                        | EUR per day | Why                                                                                                                                                                |
| -------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Container Apps, vCPU and memory active usage | 0.47        | The availability test pings `/health` every 15 minutes; each ping wakes a replica that stays up for the 5 minute cooldown, so the API is awake a third of the time |
| Container Registry (Basic)                   | 0.15        | Flat fee while the registry exists                                                                                                                                 |
| Five scheduled query alerts                  | 0.16        | Billed per evaluation, every 5 or 15 minutes                                                                                                                       |
| Standard availability test                   | 0.09        | Billed per execution                                                                                                                                               |
| **Total**                                    | **0.88**    | About 26 EUR a month                                                                                                                                               |

The Container Apps line was zero from October 1 to 11: the monthly free grant absorbed the pings, then ran out on October 12 and the daily cost doubled (0.41 to 0.88 EUR). The free grant hid the effect during the build.

`make pause` disables the availability test and the alerts (and deletes AI Search if it runs);
only the registry stays billed, about 0.15 EUR a day. `make resume` turns them back on. The
infrastructure deployment reads the state (`scripts/infra_state_params.sh`), so a `cd-infra` run
does not undo a pause. The trade-off: while paused, nobody is told the API is down.

## 2. Cost of one turn and one conversation

**Model (measured).** Load test, 10 users, 5 minutes: 231 turns cost 0.271 USD of model
calls, **0.00117 USD per turn**. Input tokens make **77 %** of it: about 1,200 input tokens and
60 output tokens per turn.

| Model call             | Mean input tokens | Mean output tokens | Calls per turn          |
| ---------------------- | ----------------- | ------------------ | ----------------------- |
| Guard (scope check)    | 350               | 18                 | 1                       |
| Router                 | 318               | 13                 | 1 (not on a refusal)    |
| Order extraction       | 788               | 43                 | on order turns          |
| Recommendation request | 558               | 23                 | on recommendation turns |
| Details answer         | 430               | 28                 | on details turns        |

**Content Safety (estimated).** Two text records per turn, Prompt Shields then moderation, at 0.375 USD per 1,000 records beyond the free 5,000 a month: **0.00075 USD per turn**. The safety layer costs **about two thirds of the model**.

**Semantic ranker (estimated).** One query per details turn; 1,000 free a month, then about 0.86 EUR per 1,000 (ADR-008).

**Per conversation.** The golden order conversations average 2.4 turns:

|                                                 | USD per conversation |
| ----------------------------------------------- | -------------------- |
| Model, measured on the evaluation suite         | 0.0020               |
| Content Safety, 2.4 turns x 2 records           | 0.0018               |
| Semantic ranker, when beyond the free allowance | 0.0005               |
| **Total**                                       | **about 0.004**      |

NFR4 asks for less than 0.01 USD per conversation: met with a margin of 2.5.

## 3. Extrapolation: 1,000 conversations a day (estimated)

30,000 conversations and about 72,000 turns a month.

| Item                                                              | Basis                                                             | USD per month |
| ----------------------------------------------------------------- | ----------------------------------------------------------------- | ------------- |
| Model calls                                                       | 72,000 turns x 0.00117                                            | 84            |
| Content Safety                                                    | 144,000 records - 5,000 free, x 0.375 per 1,000                   | 52            |
| Semantic ranker                                                   | about 29,000 details queries - 1,000 free                         | 26            |
| AI Search Basic, always on                                        | 0.0867 EUR per hour                                               | 70            |
| Container Apps                                                    | about 12 hours a day awake, 0.5 vCPU and 1 GiB, after free grants | 15            |
| Container Registry Basic                                          |                                                                   | 5             |
| Cosmos DB serverless, Blob, Key Vault, Log Analytics (under 5 GB) |                                                                   | under 5       |
| **Total**                                                         |                                                                   | **about 255** |

That is **0.0085 USD per conversation all included**, fixed costs spread. At this volume the
variable part (model, safety, ranker) is 64 %; at 100 conversations a day the fixed AI Search
dominates and the all-included cost per conversation rises to about 0.04 USD.

## 4. Levers, by expected saving

| Lever                                                                                                                                                                             | Saving                                                              | Cost of it                                                                                    |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **One call for Guard and Router.** The scope check would also return the route: the message still reaches no other model before the verdict, the principle chosen on day 3 holds. | About 27 % of model input tokens, one call and about 0.6 s per turn | Re-measure routing accuracy and guard recall on the golden suite; one prompt carries two jobs |
| **Shorter order prompt.** 788 input tokens, mostly the menu with identifiers.                                                                                                     | Up to 20 % of order turns' cost                                     | A smaller menu format, re-evaluated                                                           |
| **Moderation only on long or flagged messages.** Prompt Shields stays on every turn.                                                                                              | Up to half of Content Safety                                        | Weaker first layer on short harmful messages; the LLM scope check remains                     |
| **AI Search kept off outside opening hours** (7 AM to 8 PM)                                                                                                                       | About 45 % of AI Search                                             | Index rebuilt each morning (seconds) and a 15 to 20 minute creation                           |
| **Prompt caching**                                                                                                                                                                | Discounted cached input tokens                                      | Only prompts above 1,024 tokens qualify; none does today                                      |

## 5. One cost-latency trade-off kept open

**Cold start:** The availability test measured 30 s for the first request on a replica starting from zero; NFR5 targets 10 s. `minReplicas: 1` removes it for about 39 USD a month before free grants. Kept at zero for a portfolio project with sparse traffic; for a shop, one replica during opening hours (a Container Apps scale rule on a schedule) is the better deal.

## Sources

- Cost Management query, resource group scope, 2026-10-02.
- `evals/reports/load_test.json`, `evals/reports/baseline.md`, KQL in `docs/kql_cookbook.md`.
- Model prices: configuration defaults (OpenAI list price for gpt-5.4-mini, Data Zone price still to confirm). Content Safety: Azure pricing page, 0.375 USD per 1,000 text records.

## Final cost (measured)

Cost Management, resource group `rg-coffeeai-dev`, 2026-09-18 to 2026-10-28, the day the resources
were deleted: **20.24 EUR**, with a day or two of usage still to be reported (about 21 EUR in all).

| Service | EUR |
|---|---|
| Container Registry (Basic) | 5.17 |
| Azure Monitor (alerts, availability test) | 3.86 |
| Azure AI Search (Basic, per working session) | 3.86 |
| Foundry models (gpt-5.4-mini, embeddings) | 3.48 |
| Container Apps | 2.58 |
| Foundry tools (Content Safety) | 1.11 |
| Virtual machines, storage, load balancer, Cosmos DB, Key Vault, Log Analytics | 0.17 |

The model calls are about a sixth of the bill. The largest lines are the ones billed by the day
whatever the traffic: the registry, then the monitoring, which also kept the API awake (see the
idle cost above). For a portfolio project, the lesson is to pause what is billed per hour or per
run as soon as a session ends, and to delete everything once it is delivered.

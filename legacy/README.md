# Legacy: reference prototype

Source code of the original prototype (OpenAI + Pinecone + Firebase), kept **read-only** as a reference during the migration.

**Nothing under `src/` ever imports this folder.** It is not deployed, not tested, not maintained.

## Why keep it

1. **The prompts are the prototype's real asset**: several rounds of iteration went into them. `src/api/agents/` reuses them and hardens them (Structured Outputs, Pydantic) rather than rewriting from scratch.
2. **The recommendation artefacts** (`recommendation_objects/`) are the **baseline**: the day 4 Azure ML pipeline has to match or beat them.
3. **The notebooks** document the original method (index construction, Apriori training, Firebase upload).

## Contents

| Path | Role | Replaced on |
|---|---|---|
| `agents/*.py` | The five agents plus utils | Day 3 → `src/api/agents/` |
| `agent_controller.py` | Orchestrator | Day 3 → `src/api/app/` |
| `main.py` | Original FastAPI entry point | Day 3 → `src/api/app/main.py` |
| `recommendation_objects/` | Apriori rules (16) + popularity table (19 rows) | Day 4 → Azure ML model registry |
| `notebooks/build_vector_database.ipynb` | Pinecone index, 20 documents | Day 2 → `src/data_pipelines/build_index.py` |
| `notebooks/recommendation_engine_training.ipynb` | Apriori training | Day 4 → Azure ML pipeline |
| `notebooks/firebase_uploader.ipynb` | Firebase upload | Day 2 → `src/data_pipelines/ingest_catalog.py` |

## Debts not to carry over

The requirements document (§1.2) lists 13. The critical ones, visible directly in this code:

- **D1**: `order_taking_agent.py` has the LLM compute the order total inside its prompt.
- **D2**: prices are hardcoded in the system prompt, duplicated with `products.jsonl` and `menu_items.txt`.
- **D3**: `json.loads()` with no `try/except`; `double_check_json_output()` is a workaround costing one extra LLM call per turn.
- **D8**: `details_agent.py` and `recommendation_agent.py` **overwrite** `messages[-1]['content']` to inject RAG context.

**Planned removal**: once day 5 passes (all evaluation thresholds green), this folder is deleted. Its history stays in Git.

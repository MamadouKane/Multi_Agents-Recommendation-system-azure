.PHONY: venv install install-all lint format typecheck test cov check eval red-team ablation run docker env catalog knowledge recommendations ingest index train-local train publish-model secrets clean infra-preview infra-up search-up search-down

# Every target uses the project virtualenv directly: no need to `source .venv/bin/activate`.
VENV   := .venv
BIN    := $(VENV)/bin
PYTHON := $(BIN)/python

venv: $(PYTHON)   ## Create the Python 3.11 virtualenv with uv (only if missing)

$(PYTHON):
	uv venv --python 3.11 $(VENV)

install: venv     ## Install API + dev tooling, then the git hooks
	uv pip install --python $(PYTHON) -e ".[dev]"
	$(BIN)/pre-commit install

install-all: venv ## Install everything, including ML (day 4) and evaluation (day 5) extras
	uv pip install --python $(PYTHON) -e ".[dev,ml,evals]"
	$(BIN)/pre-commit install

lint:             ## Lint with ruff
	$(BIN)/ruff check src tests evals scripts

format:           ## Format and auto-fix
	$(BIN)/ruff format src tests evals scripts
	$(BIN)/ruff check --fix src tests evals scripts

typecheck:        ## Static type checking
	$(BIN)/mypy src

test:             ## Unit tests
	$(BIN)/pytest tests/unit

cov:              ## Unit tests with coverage (NFR10 threshold: 60%)
	$(BIN)/pytest tests/unit --cov --cov-report=term-missing --cov-fail-under=60

check: lint typecheck cov   ## Everything CI verifies

eval:             ## Golden evaluation suite: 100 cases, exits 1 when a blocking threshold fails
	$(PYTHON) -m evals.run_eval

red-team:         ## 20 hand-written attacks through the whole assistant (day 5)
	$(PYTHON) -m evals.red_team

ablation:         ## Retrieval ablation: vector vs hybrid vs hybrid + reranker (ADR-002)
	$(PYTHON) -m evals.retrieval_ablation --runs 3

run:              ## Run the API locally
	$(BIN)/uvicorn --factory src.api.app.main:create_app --reload --port 8000

docker:           ## Build the container image
	docker build -t coffee-ai-api:local -f src/api/Dockerfile .

env:              ## Write .env from the outputs of the last infrastructure deployment (endpoints only)
	az deployment group show -g $(RG) \
		-n $$(az deployment group list -g $(RG) --query "[?starts_with(name,'infra-')] | sort_by(@,&properties.timestamp)[-1].name" -o tsv) \
		--query properties.outputs -o json | $(PYTHON) scripts/outputs_to_env.py > .env
	@echo ".env written:" && cat .env

catalog:          ## Build the validated catalogue: data/raw/products.jsonl -> data/processed/catalog.jsonl
	$(PYTHON) -m src.data_pipelines.catalog

knowledge:        ## Build the search corpus: hand-written docs + docs generated from the catalogue
	$(PYTHON) -m src.data_pipelines.knowledge

recommendations:  ## Convert the prototype's recommendation artefacts, keyed by product_id (until day 4)
	$(PYTHON) -m src.data_pipelines.recommendations

ingest:           ## Load the catalogue into Cosmos DB and Blob Storage (day 2)
	$(PYTHON) -m src.data_pipelines.ingest_catalog

index:            ## Build the Azure AI Search index (day 2)
	$(PYTHON) -m src.data_pipelines.build_index

train-local:      ## Run the recommender pipeline on this machine (MLflow in ./mlruns)
	$(PYTHON) -m src.ml.run_local

train:            ## Submit the recommender pipeline to Azure ML and follow it (day 4)
	$(PYTHON) -m src.ml.submit --wait

publish-model:    ## Promote the latest registered recommender to the API (blob model-artefacts/.../current)
	$(PYTHON) -m src.ml.publish

RG      := rg-coffeeai-dev
SEARCH  := srch-coffeeai-dev-frc

infra-preview:    ## Preview infrastructure changes without applying them
	az deployment group what-if -g $(RG) -f infra/main.bicep -p infra/main.parameters.json \
		-p developerPrincipalId=$$(az ad signed-in-user show --query id -o tsv)

infra-up:         ## Deploy the infrastructure
	az deployment group create -g $(RG) -n infra-$$(date +%Y%m%d-%H%M) \
		-f infra/main.bicep -p infra/main.parameters.json \
		-p developerPrincipalId=$$(az ad signed-in-user show --query id -o tsv) \
		--query properties.outputs

search-up:        ## Recreate Azure AI Search for a working session (about 2 EUR per day, ADR-002)
	az deployment group create -g $(RG) -n search-up-$$(date +%Y%m%d-%H%M) \
		-f infra/main.bicep -p infra/main.parameters.json \
		-p developerPrincipalId=$$(az ad signed-in-user show --query id -o tsv) \
		-p deploySearch=true --query properties.outputs.searchEndpoint

search-down:      ## Delete Azure AI Search at the end of the session. The index is rebuilt by `make index`.
	az search service delete -g $(RG) -n $(SEARCH) --yes
	@echo "Search deleted. Run 'make search-up' then 'make index' at the next session."

secrets:          ## Scan the working tree for leaked secrets
	gitleaks detect --source . --no-git -v

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov

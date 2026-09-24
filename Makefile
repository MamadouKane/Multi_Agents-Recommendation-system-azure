.PHONY: venv install install-all lint format typecheck test cov check eval run docker ingest index train secrets clean infra-preview infra-up search-up search-down

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
	$(BIN)/ruff check src tests evals

format:           ## Format and auto-fix
	$(BIN)/ruff format src tests evals
	$(BIN)/ruff check --fix src tests evals

typecheck:        ## Static type checking
	$(BIN)/mypy src

test:             ## Unit tests
	$(BIN)/pytest tests/unit

cov:              ## Unit tests with coverage (NFR10 threshold: 60%)
	$(BIN)/pytest tests/unit --cov --cov-report=term-missing --cov-fail-under=60

check: lint typecheck cov   ## Everything CI verifies

eval:             ## Golden evaluation suite (day 5)
	$(PYTHON) evals/run_eval.py --thresholds evals/thresholds.yaml

run:              ## Run the API locally
	$(BIN)/uvicorn src.api.app.main:app --reload --port 8000

docker:           ## Build the container image
	docker build -t coffee-ai-api:local -f src/api/Dockerfile .

ingest:           ## Load the catalogue into Cosmos DB and Blob Storage (day 2)
	$(PYTHON) -m src.data_pipelines.ingest_catalog

index:            ## Build the Azure AI Search index (day 2)
	$(PYTHON) -m src.data_pipelines.build_index

train:            ## Submit the recommendation training pipeline to Azure ML (day 4)
	az ml job create -f src/ml/pipeline.yml

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

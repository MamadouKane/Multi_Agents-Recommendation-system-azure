#!/usr/bin/env bash
# AI Search for the length of an evaluation run (eval workflow).
#
#   scripts/ephemeral_search.sh up     # create it and build the index if it does not exist
#   scripts/ephemeral_search.sh down   # delete it, only if `up` created it
#
# AI Search is the one hourly meter of the project (ADR-008), deleted between working sessions.
# The evaluation needs it, so the workflow brings it up and takes it down again; it never deletes
# a service it did not create, which would cut a developer's session short.
set -euo pipefail
RG="${RG:-rg-coffeeai-dev}"
SEARCH="${SEARCH:-srch-coffeeai-dev-frc}"
IDENTITY="${IDENTITY:-id-coffeeai-dev-frc}"
MARKER="${RUNNER_TEMP:-/tmp}/search-created-by-this-run"

case "${1:-}" in
  up)
    if az search service show -g "$RG" -n "$SEARCH" -o none 2>/dev/null; then
      echo "AI Search already running: kept as is"
      exit 0
    fi
    app_principal=$(az identity show -g "$RG" -n "$IDENTITY" --query principalId -o tsv)
    # A name stays reserved for a few minutes after a deletion: retry rather than fail.
    for attempt in 1 2 3 4 5 6; do
      if az deployment group create -g "$RG" -n "search-eval-$(date +%H%M%S)" \
          -f infra/modules/search.bicep \
          -p name="$SEARCH" location="$(az group show -n "$RG" --query location -o tsv)" \
             tags='{"workload":"coffeeai","purpose":"evaluation"}' \
             appPrincipalId="$app_principal" developerPrincipalId="${DEVELOPER_PRINCIPAL_ID:-}" \
          -o none; then
        touch "$MARKER"
        break
      fi
      echo "attempt $attempt failed, retrying in 60 s"
      sleep 60
    done
    [[ -f "$MARKER" ]] || { echo "could not create $SEARCH"; exit 1; }
    python -m src.data_pipelines.build_index
    ;;
  down)
    if [[ -f "$MARKER" ]]; then
      az search service delete -g "$RG" -n "$SEARCH" --yes
      echo "AI Search deleted"
    else
      echo "AI Search was not created by this run: left running"
    fi
    ;;
  *)
    echo "usage: $0 up|down" >&2
    exit 2
    ;;
esac

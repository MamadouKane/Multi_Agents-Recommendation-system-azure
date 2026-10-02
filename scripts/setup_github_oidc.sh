#!/usr/bin/env bash
# GitHub Actions -> Azure with OIDC: no Azure secret stored in GitHub (task 6.6).
#
# Creates (or finds) an Entra ID application for the workflows, trusts the GitHub tokens of this
# repository only, grants the roles the workflows need on the resource group, and writes the
# non-secret identifiers to the repository variables. Idempotent: run it again at will.
#
#   scripts/setup_github_oidc.sh
set -euo pipefail

REPO="${REPO:-MamadouKane/Multi_Agents-Recommendation-system-azure}"
RG="${RG:-rg-coffeeai-dev}"
APP_NAME="${APP_NAME:-gh-coffeeai-dev}"

SUBSCRIPTION=$(az account show --query id -o tsv)
TENANT=$(az account show --query tenantId -o tsv)
SCOPE="/subscriptions/$SUBSCRIPTION/resourceGroups/$RG"

APP_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
if [[ -z "$APP_ID" ]]; then
  APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
  echo "created application $APP_NAME ($APP_ID)"
fi
SP_ID=$(az ad sp list --filter "appId eq '$APP_ID'" --query "[0].id" -o tsv)
if [[ -z "$SP_ID" ]]; then
  SP_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)
fi

# Which GitHub tokens are trusted: pushes to main, pull requests, and the production environment
# (the infra deployment job). A fork or another branch gets nothing.
federate() {  # name subject
  if ! az ad app federated-credential list --id "$APP_ID" --query "[?name=='$1']" -o tsv | grep -q .; then
    az ad app federated-credential create --id "$APP_ID" --parameters "{
      \"name\": \"$1\", \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"$2\", \"audiences\": [\"api://AzureADTokenExchange\"]}" -o none
    echo "trusted $2"
  fi
}
federate main "repo:$REPO:ref:refs/heads/main"
federate pull-request "repo:$REPO:pull_request"
federate production "repo:$REPO:environment:production"

grant() {  # role scope
  az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
    --role "$1" --scope "$2" -o none 2>/dev/null || true
  echo "role: $1 on ${2##*/}"
}
# Control plane: deploy the Bicep templates, push images, update the Container App, submit
# Azure ML jobs. RBAC Administrator because the templates create role assignments themselves.
grant "Contributor" "$SCOPE"
grant "Role Based Access Control Administrator" "$SCOPE"
# Data plane, used by the evaluation suite and the model promotion: Contributor grants none.
AIF=$(az cognitiveservices account list -g "$RG" --query "[0].id" -o tsv)
STORAGE=$(az storage account list -g "$RG" --query "[0].id" -o tsv)
grant "Cognitive Services OpenAI User" "$AIF"
grant "Cognitive Services User" "$AIF"            # Content Safety, Prompt Shields
grant "Storage Blob Data Contributor" "$STORAGE"  # recommender artefacts, model promotion
grant "Search Index Data Contributor" "$SCOPE"    # build the index of a per-run search service
COSMOS=$(az cosmosdb list -g "$RG" --query "[0].name" -o tsv)
if ! az cosmosdb sql role assignment list -g "$RG" -a "$COSMOS" --query "[?principalId=='$SP_ID']" -o tsv | grep -q .; then
  az cosmosdb sql role assignment create -g "$RG" -a "$COSMOS" --principal-id "$SP_ID" \
    --scope "/" --role-definition-id 00000000-0000-0000-0000-000000000002 -o none
fi
echo "role: Cosmos DB Built-in Data Contributor on $COSMOS"

# Identifiers, not secrets: they only name who to log in as. The token proves the rest.
gh variable set AZURE_CLIENT_ID --repo "$REPO" --body "$APP_ID"
gh variable set AZURE_TENANT_ID --repo "$REPO" --body "$TENANT"
gh variable set AZURE_SUBSCRIPTION_ID --repo "$REPO" --body "$SUBSCRIPTION"
gh variable set DEVELOPER_PRINCIPAL_ID --repo "$REPO" --body "$(az ad signed-in-user show --query id -o tsv)"
echo "repository variables set for $REPO"

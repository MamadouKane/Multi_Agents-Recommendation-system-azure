#!/usr/bin/env bash
# Prints the Bicep parameters that keep what runs today:
#   -p apiImage=<image> -p apiVersion=<commit> [-p deploySearch=<true|false>]
#
# Used by `make infra-preview`, `make infra-up` and the infra workflow, so that redeploying the
# infrastructure never swaps the API back to the placeholder image, and never creates AI Search
# (0.09 EUR per hour) when it was deleted, nor deletes it in the middle of a session.
# Pass --no-search to leave deploySearch out (make search-up sets it explicitly).
set -euo pipefail
RG="${1:-rg-coffeeai-dev}"
APP="${2:-ca-coffeeai-api-dev}"
SEARCH="${3:-srch-coffeeai-dev-frc}"

# The revision serving the traffic, not the app's template: after a rollback the template still
# describes the last revision created, the broken one (found on day 6).
serving=$(az containerapp revision list -g "$RG" -n "$APP" \
  --query "sort_by([?properties.trafficWeight > \`0\`], &properties.trafficWeight)[-1].name" -o tsv 2>/dev/null || true)
image=""
version=""
if [[ -n "$serving" ]]; then
  image=$(az containerapp revision show -g "$RG" -n "$APP" --revision "$serving" \
    --query "properties.template.containers[0].image" -o tsv)
  version=$(az containerapp revision show -g "$RG" -n "$APP" --revision "$serving" \
    --query "properties.template.containers[0].env[?name=='APP_VERSION'].value | [0]" -o tsv)
fi
[[ -z "$image" ]] && image="mcr.microsoft.com/k8se/quickstart:latest"  # first deployment
params="-p apiImage=$image -p apiVersion=${version:-placeholder}"

if [[ "${4:-}" != "--no-search" ]]; then
  if az search service show -g "$RG" -n "$SEARCH" -o none 2>/dev/null; then
    params="$params -p deploySearch=true"
  else
    params="$params -p deploySearch=false"
  fi
fi
echo "$params"

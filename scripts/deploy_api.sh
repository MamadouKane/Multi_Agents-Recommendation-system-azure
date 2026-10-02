#!/usr/bin/env bash
# Blue/green deployment of the API on Container Apps, with automatic rollback (tasks 6.9, 6.12).
#
#   scripts/deploy_api.sh <image> <version>
#
#   1. pin the traffic to the revision serving now (the stable one);
#   2. create a revision from <image> at 0 % of the traffic;
#   3. smoke test it on its own URL, which no customer uses;
#   4. switch 100 % of the traffic to it, then smoke test the public URL;
#   5. on any failure, the traffic stays on (or goes back to) the stable revision, and the new
#      one is deactivated. Exit code 1 tells the workflow the deployment was rolled back.
set -euo pipefail

IMAGE="$1"
VERSION="$2"
RG="${RG:-rg-coffeeai-dev}"
APP="${APP:-ca-coffeeai-api-dev}"
# Revision names allow lower case letters, digits and hyphens.
SUFFIX="v-$(echo "$VERSION" | tr -c 'a-z0-9\n' '-' | cut -c1-20)-$(date +%H%M%S)"
NEW="$APP--$SUFFIX"

log() { echo "[deploy] $*"; }
smoke() {  # $1: base URL
  SMOKE_BASE_URL="$1" SMOKE_EXPECTED_VERSION="$VERSION" python -m pytest tests/smoke -q -p no:cacheprovider
}

STABLE=$(az containerapp revision list -g "$RG" -n "$APP" \
  --query "sort_by([?properties.trafficWeight > \`0\`], &properties.trafficWeight)[-1].name" -o tsv)
log "stable revision: ${STABLE:-none}"
if [[ -n "$STABLE" ]]; then
  # Naming the revision removes "latest revision gets the traffic": the new one starts at 0 %.
  az containerapp ingress traffic set -g "$RG" -n "$APP" --revision-weight "$STABLE=100" -o none
fi

# First API deployment of a fresh environment: the ingress port is an app setting, still on the
# placeholder's 80 while the API listens on 8000 (found by the day 6 reproducibility test).
# Nothing to protect yet: the placeholder serves no customer.
PORT=$(az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.targetPort -o tsv)
if [[ "$PORT" != "8000" ]]; then
  log "first API deployment: ingress moved from port $PORT to 8000"
  az containerapp ingress update -g "$RG" -n "$APP" --target-port 8000 -o none
fi

log "creating $NEW from $IMAGE at 0 % of the traffic"
az containerapp update -g "$RG" -n "$APP" --image "$IMAGE" --revision-suffix "$SUFFIX" \
  --set-env-vars "APP_VERSION=$VERSION" -o none

rollback() {
  log "ROLLBACK: $1"
  if [[ -n "$STABLE" ]]; then
    az containerapp ingress traffic set -g "$RG" -n "$APP" --revision-weight "$STABLE=100" -o none
  fi
  az containerapp revision deactivate -g "$RG" -n "$APP" --revision "$NEW" -o none || true
  log "traffic on $STABLE, $NEW deactivated"
  exit 1
}

# Wait for the new revision to start (probes included), at most 5 minutes.
for _ in $(seq 1 30); do
  state=$(az containerapp revision show -g "$RG" -n "$APP" --revision "$NEW" \
    --query "properties.runningState" -o tsv)
  health=$(az containerapp revision show -g "$RG" -n "$APP" --revision "$NEW" \
    --query "properties.healthState" -o tsv)
  log "state=$state health=$health"
  [[ "$health" == "Healthy" ]] && break
  [[ "$state" == "Failed" || "$health" == "Unhealthy" ]] && rollback "$NEW failed to start"
  sleep 10
done
[[ "$health" == "Healthy" ]] || rollback "$NEW not healthy after 5 minutes"

REVISION_URL="https://$(az containerapp revision show -g "$RG" -n "$APP" --revision "$NEW" \
  --query properties.fqdn -o tsv)"
log "smoke tests on $REVISION_URL (0 % of the traffic)"
smoke "$REVISION_URL" || rollback "smoke tests failed on the new revision"

log "switching 100 % of the traffic to $NEW"
az containerapp ingress traffic set -g "$RG" -n "$APP" --revision-weight "$NEW=100" -o none
PUBLIC_URL="https://$(az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.fqdn -o tsv)"
smoke "$PUBLIC_URL" || rollback "smoke tests failed on the public URL after the switch"

# Keep the previous revision active for a manual rollback; deactivate anything older.
for old in $(az containerapp revision list -g "$RG" -n "$APP" \
    --query "[?properties.active && name != '$NEW' && name != '$STABLE'].name" -o tsv); do
  az containerapp revision deactivate -g "$RG" -n "$APP" --revision "$old" -o none && log "deactivated $old"
done
log "deployed $VERSION as $NEW"

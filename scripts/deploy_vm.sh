#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
source .deploy.env
set +a
if [ -f .source-secrets.env ]; then
  set -a
  source .source-secrets.env
  set +a
fi
command -v docker >/dev/null || { echo 'Install Docker Engine first; see docs/github-actions-deployment.md'; exit 1; }
docker compose version
command -v python3 >/dev/null || { echo 'Python 3 is required'; exit 1; }
command -v flock >/dev/null || { echo 'flock is required'; exit 1; }
exec 9>.deploy.lock
flock -n 9 || { echo 'Another deployment is active'; exit 1; }
# Refuse to compete with an already busy 4 GB VM. No swap or host settings are changed.
if ! docker compose -p search-services-poc ps --status running -q | grep -q .; then
  available_kb=$(awk '/MemAvailable/ { print $2 }' /proc/meminfo)
  test "$available_kb" -ge 2867200 || { echo 'Need at least 2800 MiB available for the initial small-corpus deployment'; exit 1; }
fi
free -h
df -h .
if [ -n "$DEMO_DOMAIN" ]; then
  overlay=compose.online.yaml
  url="https://$DEMO_DOMAIN"
else
  overlay=compose.http.yaml
  url="http://$DEMO_HOST"
fi
compose=(docker compose --profile live -p search-services-poc --env-file .deploy.env -f compose.yaml -f "$overlay" -f compose.small.yaml)
failed() {
  "${compose[@]}" ps
  "${compose[@]}" logs --no-color --tail=100
  echo 'Deployment failed. Existing data volumes are preserved; no automatic rollback was attempted.'
}
trap failed ERR
"${compose[@]}" config --quiet
# One build at a time keeps peak memory lower on the 4 GB host.
COMPOSE_PARALLEL_LIMIT=1 "${compose[@]}" build
# Re-run bootstrap on every release without deleting its data or other services.
"${compose[@]}" up -d vespa
"${compose[@]}" up -d --force-recreate bootstrap
"${compose[@]}" up -d web proxy analytics wikipedia structured
python3 scripts/check_analytics.py
python3 scripts/smoke.py
python3 scripts/check_wikipedia.py
python3 scripts/check_sources.py
"${compose[@]}" ps
docker stats --no-stream "$("${compose[@]}" ps -q vespa)" "$("${compose[@]}" ps -q web)"
printf '%s\n' "$DEPLOY_COMMIT" > .deployed-commit
printf 'Local smoke passed. Public endpoint to verify from runner: %s\n' "$url"

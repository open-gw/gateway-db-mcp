#!/usr/bin/env bash
# E4 — cross-database.
#
# Same config against MySQL and PostgreSQL. Substantiates the "any JDBC" claim
# that Reviewer 1 (minor comment 6) and Reviewer 2 both asked to be qualified.
#
# Brings up Compose profile `extra` (postgres, bridge-pg, …). Leave it running
# if useful; do NOT run E1/latency while it is up — contention confounds the
# gateway-cost measurement.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p results

COMPOSE=(docker compose -f docker-compose.bench.yml)
echo "== ensuring extra profile (postgres, bridge-pg) is up =="
"${COMPOSE[@]}" --profile extra up -d postgres bridge-pg
echo "Waiting for bridge-pg…"
for i in $(seq 1 60); do
  if curl -sf http://localhost:8083/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -sf http://localhost:8083/health >/dev/null

MY=http://localhost:8080
PG=http://localhost:8083

curl -sf "$MY/openapi" | jq -S . > results/openapi-mysql.json
curl -sf "$PG/openapi" | jq -S . > results/openapi-postgres.json

# OpenAPI paths object is `.paths` (the bare name `paths` is a jq builtin).
# x-mcp-tool lives on each HTTP method object under .paths.<path>.<method>.
MANIFEST='[.paths | to_entries[] | .value | to_entries[] | .value
           | select(.["x-mcp-tool"]) | .["x-mcp-tool"].name] | sort'
jq "$MANIFEST" results/openapi-mysql.json    > results/tools-mysql.json
jq "$MANIFEST" results/openapi-postgres.json > results/tools-postgres.json

echo "MySQL tools:      $(jq -c . results/tools-mysql.json)"
echo "PostgreSQL tools: $(jq -c . results/tools-postgres.json)"
diff -u results/tools-mysql.json results/tools-postgres.json > results/e4-tools.diff \
  && echo "PASS: identical tool manifests across engines" \
  || echo "DIFFER: see results/e4-tools.diff (report honestly, do not hide)"

echo
echo "== type mapping comparison =="
# GenerateOpenAPIOperation emits openapi/info/paths only — no components.schemas
# and no definitions. Type-mapping diff is therefore not applicable until the
# generator adds schema components; skip rather than comparing null to null.
if jq -e '(.components.schemas // .definitions) != null' results/openapi-mysql.json >/dev/null 2>&1 \
   && jq -e '(.components.schemas // .definitions) != null' results/openapi-postgres.json >/dev/null 2>&1; then
  jq -S '.components.schemas // .definitions' results/openapi-mysql.json    > results/types-mysql.json
  jq -S '.components.schemas // .definitions' results/openapi-postgres.json > results/types-postgres.json
  diff -u results/types-mysql.json results/types-postgres.json > results/e4-types.diff || true
  echo "Type differences written to results/e4-types.diff"
else
  echo "SKIP: generator does not emit .components.schemas or .definitions"
fi

echo
echo "NOTE: profile extra may still be running. Stop it before E1:"
echo "  docker compose -f docker-compose.bench.yml --profile extra stop"

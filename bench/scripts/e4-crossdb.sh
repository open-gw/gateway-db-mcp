#!/usr/bin/env bash
# E4 — cross-database.
#
# Same config against MySQL, PostgreSQL, and MariaDB. Substantiates the
# multi-engine claim; surfaces tool-manifest identity and type-mapping
# differences rather than suppressing them.
#
# Brings up Compose profile `extra` (postgres, bridge-pg, mariadb,
# bridge-mariadb, …). Leave it running if useful; do NOT run E1/latency while
# it is up — contention confounds the gateway-cost measurement.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p results

COMPOSE=(docker compose -f docker-compose.bench.yml)
echo "== ensuring extra profile (postgres, bridge-pg, mariadb, bridge-mariadb) is up =="
"${COMPOSE[@]}" --profile extra up -d --build postgres bridge-pg mariadb bridge-mariadb
# MySQL bridge on :8080 is part of the default stack; ensure it is healthy too.
"${COMPOSE[@]}" up -d bridge

wait_health() {
  local url=$1 name=$2
  echo "Waiting for ${name}…"
  for _ in $(seq 1 90); do
    if curl -sf "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "ERROR: ${name} did not become healthy at ${url}" >&2
  return 1
}

wait_health http://localhost:8080/health bridge
wait_health http://localhost:8083/health bridge-pg
wait_health http://localhost:8084/health bridge-mariadb

MY=http://localhost:8080
PG=http://localhost:8083
MDB=http://localhost:8084

curl -sf "$MY/openapi" | jq -S . > results/openapi-mysql.json
curl -sf "$PG/openapi" | jq -S . > results/openapi-postgres.json
curl -sf "$MDB/openapi" | jq -S . > results/openapi-mariadb.json

# OpenAPI paths object is `.paths` (the bare name `paths` is a jq builtin).
# x-mcp-tool lives on each HTTP method object under .paths.<path>.<method>.
MANIFEST='[.paths | to_entries[] | .value | to_entries[] | .value
           | select(.["x-mcp-tool"]) | .["x-mcp-tool"].name] | sort'
jq "$MANIFEST" results/openapi-mysql.json    > results/tools-mysql.json
jq "$MANIFEST" results/openapi-postgres.json > results/tools-postgres.json
jq "$MANIFEST" results/openapi-mariadb.json  > results/tools-mariadb.json

echo "MySQL tools:      $(jq -c . results/tools-mysql.json)"
echo "PostgreSQL tools: $(jq -c . results/tools-postgres.json)"
echo "MariaDB tools:    $(jq -c . results/tools-mariadb.json)"

diff -u results/tools-mysql.json results/tools-postgres.json > results/e4-tools-mysql-postgres.diff \
  && echo "PASS: identical tool manifests MySQL ↔ PostgreSQL" \
  || echo "DIFFER: see results/e4-tools-mysql-postgres.diff (report honestly, do not hide)"
diff -u results/tools-mysql.json results/tools-mariadb.json > results/e4-tools-mysql-mariadb.diff \
  && echo "PASS: identical tool manifests MySQL ↔ MariaDB" \
  || echo "DIFFER: see results/e4-tools-mysql-mariadb.diff (report honestly, do not hide)"
# Keep legacy path for older docs/scripts that expect e4-tools.diff
cp results/e4-tools-mysql-postgres.diff results/e4-tools.diff 2>/dev/null || true

echo
echo "== type mapping comparison =="
# GenerateOpenAPIOperation emits openapi/info/paths only — no components.schemas
# and no definitions. Type-mapping diff is therefore not applicable until the
# generator adds schema components; skip rather than comparing null to null.
if jq -e '(.components.schemas // .definitions) != null' results/openapi-mysql.json >/dev/null 2>&1 \
   && jq -e '(.components.schemas // .definitions) != null' results/openapi-postgres.json >/dev/null 2>&1; then
  jq -S '.components.schemas // .definitions' results/openapi-mysql.json    > results/types-mysql.json
  jq -S '.components.schemas // .definitions' results/openapi-postgres.json > results/types-postgres.json
  jq -S '.components.schemas // .definitions' results/openapi-mariadb.json  > results/types-mariadb.json
  diff -u results/types-mysql.json results/types-postgres.json > results/e4-types-mysql-postgres.diff || true
  diff -u results/types-mysql.json results/types-mariadb.json  > results/e4-types-mysql-mariadb.diff || true
  echo "Type differences written to results/e4-types-*.diff"
else
  echo "SKIP: generator does not emit .components.schemas or .definitions"
  echo "Surfacing engine identity via JDBC driver class instead (see below)."
fi

echo
echo "== resolved JDBC driver class (must not silently fall back) =="
"${COMPOSE[@]}" logs bridge --no-color 2>/dev/null | grep -i "driver=" | tail -n 1 \
  | tee results/e4-driver-mysql.log
"${COMPOSE[@]}" logs bridge-pg --no-color 2>/dev/null | grep -i "driver=" | tail -n 1 \
  | tee results/e4-driver-postgres.log
"${COMPOSE[@]}" logs bridge-mariadb --no-color 2>/dev/null | grep -i "driver=" | tail -n 1 \
  | tee results/e4-driver-mariadb.log

grep -q "com.mysql.cj.jdbc.Driver" results/e4-driver-mysql.log \
  && echo "PASS: MySQL bridge used com.mysql.cj.jdbc.Driver" \
  || { echo "FAIL: MySQL bridge driver assertion" >&2; exit 1; }
grep -q "org.postgresql.Driver" results/e4-driver-postgres.log \
  && echo "PASS: PostgreSQL bridge used org.postgresql.Driver" \
  || { echo "FAIL: PostgreSQL bridge driver assertion" >&2; exit 1; }
grep -q "org.mariadb.jdbc.Driver" results/e4-driver-mariadb.log \
  && echo "PASS: MariaDB bridge used org.mariadb.jdbc.Driver" \
  || { echo "FAIL: MariaDB bridge driver assertion" >&2; exit 1; }

echo
echo "NOTE: profile extra may still be running. Stop it before E1:"
echo "  docker compose -f docker-compose.bench.yml --profile extra stop"

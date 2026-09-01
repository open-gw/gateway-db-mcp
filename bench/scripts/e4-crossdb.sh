#!/usr/bin/env bash
# E4 — cross-database.
#
# Same config against MySQL and PostgreSQL. Substantiates the "any JDBC" claim
# that Reviewer 1 (minor comment 6) and Reviewer 2 both asked to be qualified.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p results

MY=http://localhost:8080
PG=http://localhost:8083

curl -sf "$MY/openapi" | jq -S . > results/openapi-mysql.json
curl -sf "$PG/openapi" | jq -S . > results/openapi-postgres.json

MANIFEST='[paths | to_entries[] | .value | to_entries[] | .value
           | select(.["x-mcp-tool"]) | .["x-mcp-tool"].name] | sort'
jq "$MANIFEST" results/openapi-mysql.json    > results/tools-mysql.json
jq "$MANIFEST" results/openapi-postgres.json > results/tools-postgres.json

echo "MySQL tools:      $(jq -c . results/tools-mysql.json)"
echo "PostgreSQL tools: $(jq -c . results/tools-postgres.json)"
diff -u results/tools-mysql.json results/tools-postgres.json > results/e4-tools.diff \
  && echo "PASS: identical tool manifests across engines" \
  || echo "DIFFER: see results/e4-tools.diff (report honestly, do not hide)"

echo
echo "== type mapping comparison (expect differences; document them) =="
jq -S '.components.schemas // .definitions' results/openapi-mysql.json    > results/types-mysql.json
jq -S '.components.schemas // .definitions' results/openapi-postgres.json > results/types-postgres.json
diff -u results/types-mysql.json results/types-postgres.json > results/e4-types.diff || true
echo "Type differences written to results/e4-types.diff"

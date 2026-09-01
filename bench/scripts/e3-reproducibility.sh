#!/usr/bin/env bash
# E3 — reproducibility.
#
# Two bridge instances, identical schema and identical six-parameter config,
# different underlying rows. If the reproducibility claim in the paper is true,
# the emitted tool manifests must be identical.
#
# This is the experiment that converts §4.1 from a hypothetical narrative into
# a measured result. It is the direct answer to SoftwareX Reviewer 3, point 4.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p results

A=http://localhost:8080
B=http://localhost:8082

echo "== fetching specs =="
curl -sf "$A/openapi" > results/openapi-a.raw.json
curl -sf "$B/openapi" > results/openapi-b.raw.json

# Normalise fields that are legitimately instance-specific (server URLs, any
# generated timestamps). Everything else must match. Adjust the jq deletions if
# your generator emits other volatile fields — but justify each one in the
# paper, because every deletion weakens the claim.
NORM='del(.servers) | del(.info.["x-generated-at"])'
jq -S "$NORM" results/openapi-a.raw.json > results/openapi-a.json
jq -S "$NORM" results/openapi-b.raw.json > results/openapi-b.json

echo "== full spec diff =="
if diff -u results/openapi-a.json results/openapi-b.json > results/e3-spec.diff; then
  echo "PASS: specs are byte-identical after normalisation"
else
  echo "FAIL: specs differ. See results/e3-spec.diff"
fi

echo
echo "== tool manifest diff (the claim that actually matters) =="
MANIFEST='[paths | to_entries[] | .value | to_entries[] | .value
           | select(.["x-mcp-tool"]) | .["x-mcp-tool"].name] | sort'
jq "$MANIFEST" results/openapi-a.json > results/tools-a.json
jq "$MANIFEST" results/openapi-b.json > results/tools-b.json
if diff -u results/tools-a.json results/tools-b.json > results/e3-tools.diff; then
  echo "PASS: tool manifests identical"
  echo "Tools: $(jq -c . results/tools-a.json)"
else
  echo "FAIL: manifests differ. See results/e3-tools.diff"
fi

echo
echo "== sanity: the data really is different =="
echo "A: $(curl -sf "$A/tables/orders/rows?limit=1")"
echo "B: $(curl -sf "$B/tables/orders/rows?limit=1")"
echo
echo "If those two lines are identical, E3 proves nothing. Check db/data-*.sql."

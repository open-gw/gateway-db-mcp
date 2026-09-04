#!/usr/bin/env bash
# mcp-conformance.sh — tools/list + tools/call transcript for E5.
#
# Runs conformance.py inside the mcp-server image (deps pinned).
# Writes bench/results/mcp-conformance.txt
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p results

OUT=results/mcp-conformance.txt
COMPOSE=(docker compose -f docker-compose.bench.yml)

echo "Ensuring mcp-server is healthy…" >&2
if ! curl -fsS "http://localhost:9090/health" >/dev/null 2>&1; then
  "${COMPOSE[@]}" up -d --build mcp-server
  deadline=$((SECONDS + 180))
  until curl -fsS "http://localhost:9090/health" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "REFUSE: mcp-server did not become healthy within 180s" >&2
      "${COMPOSE[@]}" logs --tail=80 mcp-server >&2 || true
      exit 1
    fi
    sleep 2
  done
fi

# From inside the compose network, use service DNS (host localhost:9090 is the
# published port, not reachable as localhost from another container).
INNER_URL="${MCP_INNER_URL:-http://mcp-server:8080/mcp}"

echo "MCP conformance against $INNER_URL (host health: :9090)" >&2
"${COMPOSE[@]}" run --rm --no-deps \
  --entrypoint python \
  -v "$(pwd)/results:/results" \
  mcp-server conformance.py --mcp-url "$INNER_URL" --out /results/mcp-conformance.txt

echo "Wrote $OUT" >&2
grep -A20 '## tools/list' "$OUT" || true
grep '## PASS\|## FAIL\|STOP:' "$OUT" || true

if grep -q '## FAIL\|STOP:' "$OUT"; then
  exit 2
fi
if ! grep -q '## PASS' "$OUT"; then
  echo "REFUSE: conformance transcript missing PASS" >&2
  exit 1
fi

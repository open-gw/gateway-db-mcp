#!/usr/bin/env bash
# verify-kong-generated.sh — prove deck openapi2kong output reaches the bridge,
# and that attaching the hand-written JWT (+ rate + otel) chain still 401/200s.
#
# Does NOT replace runtime kong/kong.yml. Spins a throwaway Kong on :18000.
set -euo pipefail
cd "$(dirname "$0")/.."

GEN=kong/kong-generated.yml
HAND=kong/kong.yml
GRAFT_PY=scripts/graft-kong-generated.py
OUT=results/kong-import-verify.txt
NETWORK="${COMPOSE_NETWORK:-gatewaydb-mcp-bench_default}"
CONTAINER=kong-generated-smoke
PORT=18000

if [[ ! -f "$GEN" ]]; then
  echo "REFUSE: $GEN missing — run ./scripts/generate-kong-from-openapi.sh first" >&2
  exit 1
fi

mkdir -p results
TMP=$(mktemp -d)
trap 'docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT

cp "$GEN" "$TMP/gen.yml"
cp "$HAND" "$TMP/hand.yml"
cp "$GRAFT_PY" "$TMP/graft.py"

docker run --rm \
  -v "$TMP:/work" \
  python:3.12-slim \
  bash -c 'pip install -q pyyaml && python /work/graft.py /work/gen.yml /work/hand.yml /work/kong.yml'

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" \
  --network "$NETWORK" \
  -v "$TMP/kong.yml:/kong/kong.yml:ro" \
  -e KONG_DATABASE=off \
  -e KONG_DECLARATIVE_CONFIG=/kong/kong.yml \
  -e KONG_PROXY_LISTEN=0.0.0.0:8000 \
  -e KONG_ADMIN_LISTEN=off \
  -e KONG_LOG_LEVEL=warn \
  -p "${PORT}:8000" \
  kong:3.9 >/dev/null

echo "Waiting for Kong smoke container on :$PORT …" >&2
code="000"
for _ in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/tables" || true)
  if [[ "$code" != "000" && -n "$code" ]]; then
    break
  fi
  sleep 1
done

{
  echo "# Kong generated-config smoke test"
  echo "# Container: $CONTAINER on localhost:$PORT (throwaway; runtime stays kong/kong.yml)"
  echo "# Graft: hand-written JWT consumer + /db plugin chain onto generated list_tables only."
  echo "#"

  echo "## Unauthenticated GET /tables (expect 401)"
  code401=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/tables")
  echo "http_code=$code401"

  echo "## Authenticated GET /tables (expect 200)"
  token=$(curl -fsS -X POST "http://localhost:8081/realms/mcp/protocol/openid-connect/token" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    -d 'grant_type=client_credentials&client_id=mcp-agent&client_secret=mcp-agent-secret' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
  code200=$(curl -s -o /tmp/kong-gen-tables.json -w '%{http_code}' \
    -H "Authorization: Bearer $token" \
    "http://127.0.0.1:${PORT}/tables")
  echo "http_code=$code200"
  python3 - <<'PY'
import json
try:
    d = json.load(open("/tmp/kong-gen-tables.json"))
    print("body_keys=", sorted(d.keys()) if isinstance(d, dict) else type(d).__name__)
    print("body_prefix=", str(d)[:240])
except Exception as e:
    print("body_parse_error=", e)
PY

  echo "## Unauthenticated GET /tables/orders/rows?limit=1 (expect 200 — ungrafted)"
  code_rows=$(curl -s -o /dev/null -w '%{http_code}' \
    "http://127.0.0.1:${PORT}/tables/orders/rows?limit=1")
  echo "http_code=$code_rows"

  ok=1
  [[ "$code401" == "401" ]] || { echo "FAIL: expected 401 without token, got $code401"; ok=0; }
  [[ "$code200" == "200" ]] || { echo "FAIL: expected 200 with token, got $code200"; ok=0; }
  [[ "$code_rows" == "200" ]] || { echo "FAIL: expected 200 on ungrafted rows route, got $code_rows"; ok=0; }
  if [[ "$ok" -eq 1 ]]; then
    echo "## PASS"
    echo "Generated routes reach bridge; grafted JWT chain behaves (401/200)."
  else
    echo "## FAIL"
    exit 1
  fi
} | tee "$OUT"

echo "Wrote $OUT" >&2

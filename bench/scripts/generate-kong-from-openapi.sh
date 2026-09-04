#!/usr/bin/env bash
# generate-kong-from-openapi.sh — deck openapi2kong from live bridge OpenAPI.
#
# Writes:
#   bench/kong/kong-generated.yml
#   bench/results/kong-import.diff  (vs hand-written kong/kong.yml)
#
# Uses docker image kong/deck when `deck` is not on PATH.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p results kong

BRIDGE_OPENAPI_URL="${BRIDGE_OPENAPI_URL:-http://localhost:8080/openapi}"
OUT_YML=kong/kong-generated.yml
DIFF_OUT=results/kong-import.diff
HAND=kong/kong.yml
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "Fetching OpenAPI from $BRIDGE_OPENAPI_URL …" >&2
curl -fsS "$BRIDGE_OPENAPI_URL" -o "$TMP/openapi.json"

# Require servers[] from the bridge (GenerateOpenAPI emits it). Do not inject.
python3 - "$TMP/openapi.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    spec = json.load(f)
servers = spec.get("servers")
if not servers:
    print("REFUSE: bridge OpenAPI has no servers[] — generator fix incomplete",
          file=sys.stderr)
    sys.exit(2)
print(f"OpenAPI servers={servers!r}", file=sys.stderr)
PY

deck_cmd() {
  if command -v deck >/dev/null 2>&1; then
    deck "$@"
  else
    echo "deck not installed locally — using docker image kong/deck" >&2
    docker run --rm -v "$TMP:/work" -v "$(pwd)/kong:/out" kong/deck:latest "$@"
  fi
}

# When using docker, paths must be under the mounted volumes.
if command -v deck >/dev/null 2>&1; then
  deck file openapi2kong -s "$TMP/openapi.json" -o "$OUT_YML"
else
  docker run --rm \
    -v "$TMP:/work" \
    -v "$(pwd)/kong:/out" \
    kong/deck:latest \
    file openapi2kong -s /work/openapi.json -o /out/kong-generated.yml
fi

echo "Wrote $OUT_YML" >&2

{
  echo "# Kong OpenAPI import diff"
  echo "# Hand-written: $HAND"
  echo "# Generated:    $OUT_YML"
  echo "# Source:       $BRIDGE_OPENAPI_URL"
  echo "#"
  echo "# Expected differences (document in README):"
  echo "# - Generated file is a fresh deck conversion of REST paths only;"
  echo "#   hand-written adds Keycloak JWT consumer, /raw passthrough,"
  echo "#   MCP /mcp route to mcp-server, and plugin chains."
  echo "# - Upstream URL / strip_path / plugin config will differ."
  echo "#"
  if [[ -f "$HAND" ]]; then
    diff -u "$HAND" "$OUT_YML" || true
  else
    echo "# (hand-written $HAND missing)"
  fi
} > "$DIFF_OUT"

echo "Wrote $DIFF_OUT" >&2
wc -l "$DIFF_OUT" >&2

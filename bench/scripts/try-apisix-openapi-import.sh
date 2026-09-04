#!/usr/bin/env bash
# try-apisix-openapi-import.sh — probe ADC OpenAPI→APISIX conversion.
#
# Writes bench/results/apisix-import.txt with the finding. Does NOT invent a
# working standalone APISIX import when ADC/annotations are unavailable.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p results

OUT=results/apisix-import.txt
BRIDGE_OPENAPI_URL="${BRIDGE_OPENAPI_URL:-http://localhost:8080/openapi}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

{
  echo "# APISIX OpenAPI import probe"
  echo "# Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# OpenAPI source: $BRIDGE_OPENAPI_URL"
  echo "#"
} > "$OUT"

if ! curl -fsS "$BRIDGE_OPENAPI_URL" -o "$TMP/openapi.json" 2>>"$OUT"; then
  {
    echo "FINDING: could not fetch OpenAPI from $BRIDGE_OPENAPI_URL"
    echo "Stack the latency bridge first, then re-run."
  } >> "$OUT"
  cat "$OUT"
  exit 0
fi

# Bridge OpenAPI must include servers[] (generator emits it). Do not inject.
python3 - "$TMP/openapi.json" <<'PY' >>"$OUT" 2>&1
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    spec = json.load(f)
if not spec.get("servers"):
    print("REFUSE: bridge OpenAPI has no servers[] — generator fix incomplete")
    sys.exit(2)
print(f"OpenAPI servers={spec.get('servers')!r}")
PY

ADC_IMAGE="${ADC_IMAGE:-api7/adc:latest}"
echo "Attempting: docker run $ADC_IMAGE convert openapi …" | tee -a "$OUT"

set +e
docker pull "$ADC_IMAGE" >>"$OUT" 2>&1
PULL_RC=$?
set -e

if [[ "$PULL_RC" -ne 0 ]]; then
  {
    echo ""
    echo "FINDING: ADC image '$ADC_IMAGE' could not be pulled (rc=$PULL_RC)."
    echo "Apache APISIX OSS standalone (file provider, no etcd) as used in this"
    echo "harness does not ship a first-class OpenAPI→routes importer comparable"
    echo "to Kong deck openapi2kong."
    echo ""
    echo "API7 ADC (adc convert openapi) targets APISIX with ADC-oriented"
    echo "annotations / declarative config and is not universally available for"
    echo "plain OSS standalone YAML without those annotations."
    echo ""
    echo "This harness therefore keeps hand-written apisix/apisix.yaml for REST"
    echo "arms and does not claim an automated APISIX OpenAPI import path."
    echo "MCP governed measurement uses Kong (/mcp) for a clean jwt+rate+otel"
    echo "comparison against REST /db on the same gateway."
  } >> "$OUT"
  cat "$OUT"
  exit 0
fi

set +e
docker run --rm -v "$TMP:/work" "$ADC_IMAGE" convert openapi \
  --file /work/openapi.json --output /work/apisix-out.yaml >>"$OUT" 2>&1
CONV_RC=$?
set -e

{
  echo ""
  if [[ "$CONV_RC" -ne 0 ]]; then
    echo "FINDING: adc convert openapi failed (rc=$CONV_RC) on the unmodified"
    echo "bridge OpenAPI. Import is not a drop-in for OSS APISIX standalone as used"
    echo "here (file provider, no etcd, hand-written apisix.yaml)."
    echo "ADC may require x-adc annotations / API7 sync — not assumed available."
    echo "Hand-written apisix.yaml remains the source of truth for this harness."
    echo "MCP governed measurement uses Kong (/mcp), not APISIX."
  else
    echo "FINDING: adc convert openapi exited 0 on unmodified bridge OpenAPI."
    echo "Review whether output is usable on standalone OSS without ADC sync."
    if [[ -f "$TMP/apisix-out.yaml" ]]; then
      cp "$TMP/apisix-out.yaml" results/apisix-generated.yaml 2>/dev/null || true
      echo "----- generated (first 80 lines) -----"
      head -n 80 "$TMP/apisix-out.yaml"
    fi
    echo ""
    echo "Even when conversion succeeds, this harness does not auto-load the"
    echo "result into OSS standalone APISIX (no ADC controller). Treat as probe."
  fi
} >> "$OUT"

cat "$OUT"
echo "Wrote $OUT" >&2

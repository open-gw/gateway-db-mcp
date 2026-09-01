#!/usr/bin/env bash
# run-benchmark.sh — immutable, self-describing k6 runs.
#
# Gathers provenance the k6 container cannot see, refuses dirty trees without
# --force, refuses unhealthy stacks, and writes results under results/runs/.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.bench.yml)
mkdir -p results/runs

TARGET=""
VUS=""
ITERATIONS=""
NOTE=""
FORCE=0
SWEEP=0
ALLOW_EXTRA=0

# Default E1 latency set. Anything in EXTRA contending on the Docker VM
# invalidates a citable latency measurement unless --allow-extra-containers.
LATENCY_SERVICES=(mysql-a bridge keycloak kong otel-collector jaeger)
EXTRA_SERVICES=(mysql-b bridge-b postgres bridge-pg)

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run-benchmark.sh --target direct|passthrough|gateway --vus N --iterations N [--note TEXT] [--force] [--allow-extra-containers]
  ./scripts/run-benchmark.sh --sweep --iterations N [--note TEXT] [--force] [--allow-extra-containers]
EOF
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="${2:-}"; shift 2 ;;
    --vus) VUS="${2:-}"; shift 2 ;;
    --iterations) ITERATIONS="${2:-}"; shift 2 ;;
    --note) NOTE="${2:-}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --sweep) SWEEP=1; shift ;;
    --allow-extra-containers) ALLOW_EXTRA=1; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1" >&2; usage ;;
  esac
done

if [[ "$SWEEP" -eq 1 ]]; then
  [[ -n "$ITERATIONS" ]] || { echo "--sweep requires --iterations" >&2; exit 2; }
elif [[ -z "$TARGET" || -z "$VUS" || -z "$ITERATIONS" ]]; then
  usage
fi

if [[ "$SWEEP" -ne 1 ]]; then
  case "$TARGET" in
    direct|passthrough|gateway) ;;
    *) echo "REFUSE: --target must be direct|passthrough|gateway (got '$TARGET')" >&2; exit 2 ;;
  esac
fi

require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "missing command: $1" >&2; exit 1; }; }
require_cmd docker
require_cmd jq
require_cmd git
require_cmd python3

# ── health gate ──────────────────────────────────────────────────────────────
require_healthy() {
  local name="$1" st
  if ! docker inspect "$name" >/dev/null 2>&1; then
    echo "REFUSE: container $name not found — is the stack up?" >&2
    return 1
  fi
  st=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name")
  if [[ "$st" != "healthy" && "$st" != "running" ]]; then
    echo "REFUSE: container $name is '$st' (need healthy)" >&2
    return 1
  fi
  if docker inspect -f '{{if .State.Health}}yes{{else}}no{{end}}' "$name" | grep -qx yes; then
    st=$(docker inspect -f '{{.State.Health.Status}}' "$name")
    if [[ "$st" != "healthy" ]]; then
      echo "REFUSE: container $name health=$st" >&2
      return 1
    fi
  fi
  return 0
}

bad=0
for c in gatewaydb-mcp-bench-bridge-1 gatewaydb-mcp-bench-kong-1 \
         gatewaydb-mcp-bench-keycloak-1 gatewaydb-mcp-bench-mysql-a-1; do
  require_healthy "$c" || bad=1
done
if [[ "$bad" -ne 0 ]]; then
  echo "Start the latency stack: docker compose -f docker-compose.bench.yml up -d" >&2
  echo "(Do not enable --profile extra for E1 — those services contend for CPU.)" >&2
  exit 1
fi

# ── refuse E3/E4 containers during latency measurement ───────────────────────
# mapfile / running service names from compose
running_services=$("${COMPOSE[@]}" ps --status running --format '{{.Service}}' 2>/dev/null | sort -u || true)
CONTAINERS_RUNNING_JSON=$(printf '%s\n' "$running_services" | python3 -c '
import json, sys
names = sorted({ln.strip() for ln in sys.stdin if ln.strip()})
print(json.dumps(names))
')

extra_running=()
for svc in "${EXTRA_SERVICES[@]}"; do
  if printf '%s\n' "$running_services" | grep -qx "$svc"; then
    extra_running+=("$svc")
  fi
done

if [[ ${#extra_running[@]} -gt 0 && "$ALLOW_EXTRA" -ne 1 ]]; then
  echo "REFUSE: containers outside the latency set are running:" >&2
  printf '  %s\n' "${extra_running[@]}" >&2
  echo "These belong to Compose profile 'extra' (E3/E4). They contend for the" >&2
  echo "same Docker VM CPUs as bridge/Kong/k6 and invalidate the gateway-cost" >&2
  echo "delta. Stop them with:" >&2
  echo "  docker compose -f docker-compose.bench.yml --profile extra stop mysql-b bridge-b postgres bridge-pg" >&2
  echo "Or pass --allow-extra-containers to override (not for paper figures)." >&2
  exit 1
fi
if [[ ${#extra_running[@]} -gt 0 && "$ALLOW_EXTRA" -eq 1 ]]; then
  echo "WARNING: extra containers running (${extra_running[*]}); continuing because --allow-extra-containers was set." >&2
  echo "WARNING: paper figures must not cite runs taken under E3/E4 contention." >&2
fi

# ── route behaviour gate (governed vs passthrough) ───────────────────────────
# /db without a token must be 401; /raw without a token must be 200.
# If either fails the routes are misconfigured and every subsequent number is
# meaningless.
assert_kong_routes() {
  local code_db code_raw
  code_db=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/db/tables || true)
  code_raw=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/raw/tables || true)
  if [[ "$code_db" != "401" ]]; then
    echo "REFUSE: /db/tables without token returned HTTP $code_db (expected 401)." >&2
    echo "Governed route misconfigured — fix kong/kong.yml before measuring." >&2
    exit 1
  fi
  if [[ "$code_raw" != "200" ]]; then
    echo "REFUSE: /raw/tables without token returned HTTP $code_raw (expected 200)." >&2
    echo "Passthrough route misconfigured — fix kong/kong.yml before measuring." >&2
    exit 1
  fi
  echo "Kong routes OK: /db → 401 (no token), /raw → 200 (no token)" >&2
}
assert_kong_routes

# ── git provenance ───────────────────────────────────────────────────────────
REPO_ROOT=$(cd .. && pwd)
GIT_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)
GIT_BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
# Dirty means source / config changes — not newly written run artifacts waiting
# to be committed. Otherwise every successive local run would require --force.
if git -C "$REPO_ROOT" status --porcelain -- . \
    ':(exclude)bench/results/runs/' \
  | grep -q .; then
  GIT_DIRTY=true
else
  GIT_DIRTY=false
fi

if [[ "$GIT_DIRTY" == "true" ]]; then
  echo "WARNING: git working tree is DIRTY. A dirty run is not reproducible." >&2
  echo "WARNING: paper figures must not cite dirty runs." >&2
  if [[ "$FORCE" -ne 1 ]]; then
    echo "Refusing to run. Pass --force to override." >&2
    exit 1
  fi
  echo "WARNING: continuing because --force was set." >&2
fi

# ── collect host / images / bridge config (once) ─────────────────────────────
HOST_JSON=$(python3 - <<'PY'
import json, platform, subprocess

def sh(*args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""

os_name = f"{platform.system()} {platform.release()} {platform.machine()}"
cpu_model, cpu_cores, memory_gb = "", 0, 0.0

if platform.system() == "Darwin":
    cpu_model = sh("sysctl", "-n", "machdep.cpu.brand_string")
    cpu_cores = int(sh("sysctl", "-n", "hw.ncpu") or "0")
    mem = int(sh("sysctl", "-n", "hw.memsize") or "0")
    memory_gb = round(mem / (1024 ** 3), 2) if mem else 0.0
else:
    cpu_model = sh("bash", "-lc", "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2").strip() or sh("uname", "-m")
    cpu_cores = int(sh("bash", "-lc", "nproc 2>/dev/null || getconf _NPROCESSORS_ONLN") or "0")
    mem_kb = sh("bash", "-lc", "awk '/MemTotal/ {print $2}' /proc/meminfo")
    memory_gb = round(int(mem_kb or 0) / (1024 ** 2), 2)

docker_cpus = sh("docker", "info", "--format", "{{.NCPU}}")
docker_mem = sh("docker", "info", "--format", "{{.MemTotal}}")
print(json.dumps({
    "os": os_name,
    "cpu_model": cpu_model,
    "cpu_cores": cpu_cores,
    "memory_gb": memory_gb,
    "docker_vm_cpus": int(docker_cpus) if docker_cpus.isdigit() else None,
    "docker_vm_memory_gb": round(int(docker_mem) / (1024 ** 3), 2) if docker_mem.isdigit() else None,
}))
PY
)

IMAGES_JSON=$(python3 - <<'PY'
import json, subprocess

def digest(cname):
    try:
        img_id = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.Image}}", cname],
            text=True, stderr=subprocess.DEVNULL).strip()
        tag = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.Config.Image}}", cname],
            text=True, stderr=subprocess.DEVNULL).strip()
        # Digests live on the image, not the container.
        repo = subprocess.check_output(
            ["docker", "inspect", "-f",
             "{{if index .RepoDigests 0}}{{index .RepoDigests 0}}{{end}}",
             img_id],
            text=True, stderr=subprocess.DEVNULL).strip()
        if repo:
            return repo
        return f"{tag} {img_id}"
    except Exception as e:
        return f"unknown ({e})"

print(json.dumps({
    "bridge": digest("gatewaydb-mcp-bench-bridge-1"),
    "kong": digest("gatewaydb-mcp-bench-kong-1"),
    "mysql": digest("gatewaydb-mcp-bench-mysql-a-1"),
    "keycloak": digest("gatewaydb-mcp-bench-keycloak-1"),
}))
PY
)

BRIDGE_ENV_RAW=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' gatewaydb-mcp-bench-bridge-1)
BRIDGE_CFG_JSON=$(BRIDGE_ENV_RAW="$BRIDGE_ENV_RAW" python3 - <<'PY'
import json, os
wanted = {
  "SECURITY_READ_ONLY": "security_read_only",
  "SECURITY_ALLOWED_TABLES": "security_allowed_tables",
  "SECURITY_MAX_ROWS": "security_max_rows",
  "SECURITY_QUERY_TIMEOUT": "security_query_timeout",
  "DB_TYPE": "db_type",
}
out = {v: "" for v in wanted.values()}
for line in os.environ.get("BRIDGE_ENV_RAW", "").splitlines():
    line = line.strip()
    if "=" not in line:
        continue
    k, _, v = line.partition("=")
    if k in wanted:
        out[wanted[k]] = v
print(json.dumps(out))
PY
)

K6_VERSION=$(docker image inspect grafana/k6:0.54.0 --format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null || true)
[[ -n "$K6_VERSION" && "$K6_VERSION" != "<no value>" ]] || K6_VERSION="0.54.0"

run_one() {
  local target="$1" vus="$2" iterations="$3"
  local ts_compact ts_iso run_id out_path meta

  ts_compact=$(date -u +%Y%m%dT%H%M%SZ)
  ts_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  run_id="${ts_compact}-${target}-vus${vus}-iter${iterations}"
  out_path="results/runs/${run_id}.json"

  if [[ -e "$out_path" ]]; then
    echo "REFUSE: $out_path already exists — refusing to overwrite" >&2
    exit 1
  fi

  meta=$(
    export RUN_ID="$run_id" TS="$ts_iso" TARGET="$target" VUS="$vus" ITER="$iterations"
    export GIT_COMMIT GIT_DIRTY GIT_BRANCH K6_VERSION NOTE
    export IMAGES_JSON HOST_JSON BRIDGE_CFG_JSON CONTAINERS_RUNNING_JSON
    python3 - <<'PY'
import json, os
print(json.dumps({
  "run_id": os.environ["RUN_ID"],
  "timestamp_utc": os.environ["TS"],
  "target": os.environ["TARGET"],
  "vus": int(os.environ["VUS"]),
  "iterations": int(os.environ["ITER"]),
  "git_commit": os.environ["GIT_COMMIT"],
  "git_dirty": os.environ["GIT_DIRTY"].lower() == "true",
  "git_branch": os.environ["GIT_BRANCH"],
  "k6_version": os.environ["K6_VERSION"],
  "images": json.loads(os.environ["IMAGES_JSON"]),
  "host": json.loads(os.environ["HOST_JSON"]),
  "bridge_config": json.loads(os.environ["BRIDGE_CFG_JSON"]),
  "containers_running": json.loads(os.environ["CONTAINERS_RUNNING_JSON"]),
  "notes": os.environ.get("NOTE", ""),
}, separators=(",", ":")))
PY
  )

  echo "== run $run_id ==" >&2
  "${COMPOSE[@]}" --profile bench run --rm \
      -e "TARGET=$target" \
      -e "VUS=$vus" \
      -e "ITERATIONS=$iterations" \
      -e "RUN_ID=$run_id" \
      -e "RUN_METADATA_JSON=$meta" \
      k6 run /scripts/latency.js

  if [[ ! -f "$out_path" ]]; then
    echo "ERROR: k6 finished but $out_path was not written" >&2
    exit 1
  fi

  export INDEX_META="$meta" INDEX_PATH="$out_path"
  python3 - <<'PY'
import json, os
meta = json.loads(os.environ["INDEX_META"])
row = {
  "run_id": meta["run_id"],
  "timestamp_utc": meta["timestamp_utc"],
  "target": meta["target"],
  "vus": meta["vus"],
  "iterations": meta["iterations"],
  "git_commit": meta["git_commit"],
  "git_dirty": meta["git_dirty"],
  "notes": meta.get("notes", ""),
  "filename": os.environ["INDEX_PATH"],
}
with open("results/runs/index.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(row, separators=(",", ":")) + "\n")
PY

  echo "Wrote $out_path" >&2
  printf '%s\n' "$out_path"
}

if [[ "$SWEEP" -eq 1 ]]; then
  for target in direct passthrough gateway; do
    for vus in 1 10 50; do
      run_one "$target" "$vus" "$ITERATIONS"
      echo "settle 5s…" >&2
      sleep 5
    done
  done
  echo >&2
  echo "== sweep complete — see results/runs/index.jsonl ==" >&2
else
  run_one "$TARGET" "$VUS" "$ITERATIONS"
fi

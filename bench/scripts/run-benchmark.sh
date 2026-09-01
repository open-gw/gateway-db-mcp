#!/usr/bin/env bash
# run-benchmark.sh — immutable, self-describing k6 runs.
#
# Refuses to measure when preconditions fail (broken telemetry, wrong routes,
# extra containers, dirty tree). Archives every attempt; marks aborted runs.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.bench.yml)
mkdir -p results/runs
: > results/spans.jsonl 2>/dev/null || true

TARGET=""
VUS=""
ITERATIONS=""
NOTE=""
FORCE=0
SWEEP=0
ALLOW_EXTRA=0
REPEATS=3
NO_SPAN_FILE=0

LATENCY_SERVICES=(mysql-a bridge keycloak kong otel-collector jaeger)
EXTRA_SERVICES=(mysql-b bridge-b postgres bridge-pg)

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run-benchmark.sh --target direct|passthrough|gateway --vus N --iterations N
      [--note TEXT] [--force] [--allow-extra-containers] [--repeats N] [--no-span-file]
  ./scripts/run-benchmark.sh --sweep --iterations N
      [--note TEXT] [--force] [--allow-extra-containers] [--repeats N] [--no-span-file]
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
    --repeats) REPEATS="${2:-}"; shift 2 ;;
    --no-span-file) NO_SPAN_FILE=1; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1" >&2; usage ;;
  esac
done

if [[ "$SWEEP" -eq 1 ]]; then
  [[ -n "$ITERATIONS" ]] || { echo "--sweep requires --iterations" >&2; exit 2; }
elif [[ -z "$TARGET" || -z "$VUS" || -z "$ITERATIONS" ]]; then
  usage
fi

if ! [[ "$REPEATS" =~ ^[1-9][0-9]*$ ]]; then
  echo "REFUSE: --repeats must be a positive integer (got '$REPEATS')" >&2
  exit 2
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
require_cmd curl

# ── health helpers ───────────────────────────────────────────────────────────
require_healthy() {
  local name="$1" st
  if ! docker inspect "$name" >/dev/null 2>&1; then
    echo "REFUSE: container $name not found — is the stack up?" >&2
    return 1
  fi
  st=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name")
  if [[ "$st" != "healthy" && "$st" != "running" ]]; then
    echo "REFUSE: container $name is '$st' (need healthy/running)" >&2
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

# ── span-file mode ───────────────────────────────────────────────────────────
# Never pull Jaeger back up as a side effect of refreshing the collector
# (depends_on would restart it and defeat the telemetry preflight).
if [[ "$NO_SPAN_FILE" -eq 1 ]]; then
  export OTEL_COLLECTOR_CONFIG=./collector-jaeger-only.yaml
  echo "Using collector-jaeger-only.yaml (--no-span-file)" >&2
  "${COMPOSE[@]}" up -d --no-deps --force-recreate otel-collector >/dev/null
else
  export OTEL_COLLECTOR_CONFIG=./collector.yaml
fi

# ── base container health ────────────────────────────────────────────────────
bad=0
for c in gatewaydb-mcp-bench-bridge-1 gatewaydb-mcp-bench-kong-1 \
         gatewaydb-mcp-bench-keycloak-1 gatewaydb-mcp-bench-mysql-a-1; do
  require_healthy "$c" || bad=1
done
if [[ "$bad" -ne 0 ]]; then
  echo "Start the latency stack: docker compose -f docker-compose.bench.yml up -d" >&2
  exit 1
fi

running_services=$("${COMPOSE[@]}" ps --status running --format '{{.Service}}' 2>/dev/null | sort -u || true)
CONTAINERS_RUNNING_JSON=$(printf '%s\n' "$running_services" | python3 -c '
import json, sys
print(json.dumps(sorted({ln.strip() for ln in sys.stdin if ln.strip()})))
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
  echo "Stop with: docker compose -f docker-compose.bench.yml --profile extra stop" >&2
  echo "Or pass --allow-extra-containers (not for paper figures)." >&2
  exit 1
fi

# ── git provenance ───────────────────────────────────────────────────────────
REPO_ROOT=$(cd .. && pwd)
GIT_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)
GIT_BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
if git -C "$REPO_ROOT" status --porcelain -- . \
    ':(exclude)bench/results/runs/' \
  | grep -q .; then
  GIT_DIRTY=true
else
  GIT_DIRTY=false
fi
if [[ "$GIT_DIRTY" == "true" ]]; then
  echo "WARNING: git working tree is DIRTY. A dirty run is not reproducible." >&2
  if [[ "$FORCE" -ne 1 ]]; then
    echo "Refusing to run. Pass --force to override." >&2
    exit 1
  fi
  echo "WARNING: continuing because --force was set." >&2
fi

# ── host / images / bridge config ────────────────────────────────────────────
HOST_JSON=$(python3 - <<'PY'
import json, platform, subprocess

def sh(*args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""

os_name = f"{platform.system()} {platform.release()} {platform.machine()}"
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
        repo = subprocess.check_output(
            ["docker", "inspect", "-f",
             "{{if index .RepoDigests 0}}{{index .RepoDigests 0}}{{end}}",
             img_id],
            text=True, stderr=subprocess.DEVNULL).strip()
        return repo or f"{tag} {img_id}"
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
    if "=" not in line: continue
    k, _, v = line.partition("=")
    if k in wanted: out[wanted[k]] = v
print(json.dumps(out))
PY
)

K6_VERSION=$(docker image inspect grafana/k6:0.54.0 --format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null || true)
[[ -n "$K6_VERSION" && "$K6_VERSION" != "<no value>" ]] || K6_VERSION="0.54.0"

# ── Jaeger store reset (gateway only) ────────────────────────────────────────
# Empty in-memory store before every governed run so throughput is not a
# function of prior run ordering. See README "Telemetry confounds".
JAEGER_CONTAINER=gatewaydb-mcp-bench-jaeger-1
JAEGER_MEM_LIMIT_MB="${JAEGER_MEM_LIMIT_MB:-1024}"
JAEGER_RESET_SETTLE_S="${JAEGER_RESET_SETTLE_S:-5}"

jaeger_memory_mb() {
  python3 - <<'PY'
import re, subprocess, sys
out = subprocess.check_output(
    ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", "gatewaydb-mcp-bench-jaeger-1"],
    text=True, stderr=subprocess.DEVNULL,
).strip()
# e.g. "123.4MiB / 7.748GiB"
used = out.split("/", 1)[0].strip()
m = re.match(r"([0-9.]+)\s*([KMGT]?i?B)", used, re.I)
if not m:
    print(f"cannot parse MemUsage={out!r}", file=sys.stderr)
    sys.exit(1)
val, unit = float(m.group(1)), m.group(2).lower()
mult = {
    "b": 1 / (1024 * 1024),
    "kib": 1 / 1024, "kb": 1 / 1000,
    "mib": 1, "mb": 1,
    "gib": 1024, "gb": 1000,
    "tib": 1024 * 1024, "tb": 1000 * 1000,
}.get(unit)
if mult is None:
    print(f"unknown unit in MemUsage={out!r}", file=sys.stderr)
    sys.exit(1)
print(f"{val * mult:.3f}")
PY
}

wait_jaeger_ui() {
  local deadline=$((SECONDS + 90))
  until curl -sf -o /dev/null "http://localhost:16686/" \
        && curl -sf -o /dev/null "http://localhost:16686/api/services"; do
    if (( SECONDS >= deadline )); then
      echo "REFUSE: jaeger UI (http://localhost:16686) did not become ready within 90s after restart" >&2
      exit 1
    fi
    sleep 1
  done
}

# True when /api/services has no kong-bench (store empty of governed traces).
jaeger_store_empty_of_kong() {
  python3 - <<'PY'
import json, sys, urllib.request
with urllib.request.urlopen("http://localhost:16686/api/services", timeout=15) as resp:
    payload = json.loads(resp.read().decode())
names = payload if isinstance(payload, list) else payload.get("data", payload)
flat = []
for n in (names or []):
    if isinstance(n, str):
        flat.append(n)
    elif isinstance(n, dict) and "name" in n:
        flat.append(n["name"])
sys.exit(0 if "kong-bench" not in flat else 1)
PY
}

start_otel_collector() {
  echo "Starting otel-collector (--no-deps, recreate)…" >&2
  "${COMPOSE[@]}" up -d --no-deps --force-recreate otel-collector >/dev/null
  local deadline=$((SECONDS + 60))
  until docker inspect -f '{{.State.Status}}' gatewaydb-mcp-bench-otel-collector-1 2>/dev/null | grep -qx running; do
    if (( SECONDS >= deadline )); then
      echo "REFUSE: otel-collector did not become running within 60s after start" >&2
      exit 1
    fi
    sleep 1
  done
}

# Stop collector, wipe Jaeger, confirm no kong-bench. Leaves collector stopped.
wipe_jaeger_store() {
  echo "Stopping otel-collector…" >&2
  "${COMPOSE[@]}" stop otel-collector >/dev/null || true
  sleep 2
  echo "Restarting jaeger for empty in-memory store…" >&2
  "${COMPOSE[@]}" restart jaeger
  wait_jaeger_ui
  jaeger_store_empty_of_kong
}

reset_jaeger_store() {
  # Kong buffers OTLP while the collector is down. A single wipe then immediate
  # collector start lets that buffer land in the fresh store and flakes the
  # empty-store preflight. Drain the buffer into a disposable store, wipe
  # again, then bring the collector up for measurement.
  local JAEGER_DRAIN_S="${JAEGER_DRAIN_S:-3}"
  if ! wipe_jaeger_store; then
    echo "WARNING: kong-bench present after first wipe; retrying…" >&2
    if ! wipe_jaeger_store; then
      echo "REFUSE: jaeger store still contains kong-bench after reset" >&2
      exit 1
    fi
  fi
  echo "Draining Kong OTLP buffer into disposable Jaeger store (${JAEGER_DRAIN_S}s)…" >&2
  start_otel_collector
  sleep "$JAEGER_DRAIN_S"
  if ! wipe_jaeger_store; then
    echo "REFUSE: jaeger store still contains kong-bench after drain wipe" >&2
    exit 1
  fi
  start_otel_collector
  echo "settle ${JAEGER_RESET_SETTLE_S}s after jaeger reset…" >&2
  sleep "$JAEGER_RESET_SETTLE_S"
  if ! jaeger_store_empty_of_kong; then
    echo "WARNING: residual kong-bench after settle; final wipe…" >&2
    if ! wipe_jaeger_store; then
      echo "REFUSE: jaeger store still contains kong-bench after final wipe" >&2
      exit 1
    fi
    start_otel_collector
    sleep 2
  fi
  if ! jaeger_store_empty_of_kong; then
    echo "REFUSE: jaeger store not empty after reset (Kong still flushing)" >&2
    exit 1
  fi
}

# ── preflight ────────────────────────────────────────────────────────────────
# Returns JSON map of check -> pass|fail|skipped via stdout. Exits 1 on any fail.
run_preflight() {
  local target="$1"
  FORCE="$FORCE" GIT_DIRTY="$GIT_DIRTY" JAEGER_MEM_LIMIT_MB="$JAEGER_MEM_LIMIT_MB" \
    python3 - "$target" <<'PY'
import json, os, re, subprocess, sys, time, urllib.parse, urllib.request

target = sys.argv[1]
checks = {}
failed = []
mem_limit = float(os.environ.get("JAEGER_MEM_LIMIT_MB", "1024"))

def fail(name, why):
    checks[name] = "fail"
    failed.append(f"{name}: {why}")
    print(f"PREFLIGHT FAIL: {name} — {why}", file=sys.stderr)

def pass_(name):
    checks[name] = "pass"

def skip(name):
    checks[name] = "skipped"

def sh(*args, timeout=30):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()

def parse_mem_mb(usage: str) -> float:
    used = usage.split("/", 1)[0].strip()
    m = re.match(r"([0-9.]+)\s*([KMGT]?i?B)", used, re.I)
    if not m:
        raise ValueError(f"cannot parse MemUsage={usage!r}")
    val, unit = float(m.group(1)), m.group(2).lower()
    mult = {
        "b": 1 / (1024 * 1024),
        "kib": 1 / 1024, "kb": 1 / 1000,
        "mib": 1, "mb": 1,
        "gib": 1024, "gb": 1000,
        "tib": 1024 * 1024, "tb": 1000 * 1000,
    }.get(unit)
    if mult is None:
        raise ValueError(f"unknown unit in MemUsage={usage!r}")
    return val * mult

def service_names(payload):
    names = payload if isinstance(payload, list) else payload.get("data", payload)
    flat = []
    for n in (names or []):
        if isinstance(n, str):
            flat.append(n)
        elif isinstance(n, dict) and "name" in n:
            flat.append(n["name"])
    return flat

# For governed runs, empty-store gates must run before any other work that
# gives Kong time to flush buffered OTLP into the fresh Jaeger store.
if target == "gateway":
    pass_("jaeger_reset")
    try:
        st = sh("docker", "inspect", "-f", "{{.State.Status}}", "gatewaydb-mcp-bench-jaeger-1")
        if st != "running":
            fail("jaeger_running", f"gatewaydb-mcp-bench-jaeger-1 status={st} — governed runs without telemetry measure a broken exporter")
        else:
            pass_("jaeger_running")
    except Exception as e:
        fail("jaeger_running", f"gatewaydb-mcp-bench-jaeger-1 missing: {e}")

    try:
        usage = sh(
            "docker", "stats", "--no-stream", "--format", "{{.MemUsage}}",
            "gatewaydb-mcp-bench-jaeger-1",
        )
        mb = parse_mem_mb(usage)
        checks["jaeger_memory_mb"] = round(mb, 3)
        if mb > mem_limit:
            fail(
                "jaeger_memory",
                f"{mb:.1f} MB exceeds JAEGER_MEM_LIMIT_MB={mem_limit:g} "
                f"(usage={usage!r}) — governed run against a loaded store is invalid",
            )
        else:
            pass_("jaeger_memory")
    except Exception as e:
        fail("jaeger_memory", str(e))

    try:
        with urllib.request.urlopen("http://localhost:16686/api/services", timeout=15) as resp:
            services = json.loads(resp.read().decode())
        flat = service_names(services)
        n_traces = 0
        if "kong-bench" in flat:
            q = urllib.parse.urlencode({"service": "kong-bench", "limit": "20"})
            with urllib.request.urlopen(f"http://localhost:16686/api/traces?{q}", timeout=15) as resp:
                body = json.loads(resp.read().decode())
            data = body.get("data", body) if isinstance(body, dict) else body
            n_traces = len(data or [])
        checks["jaeger_trace_count_value"] = n_traces
        if n_traces > 0 or "kong-bench" in flat:
            fail(
                "jaeger_trace_count",
                f"store not empty after reset (services={flat!r}, kong-bench traces≈{n_traces})",
            )
        else:
            pass_("jaeger_trace_count")
    except Exception as e:
        fail("jaeger_trace_count", str(e))

try:
    code_db = sh("curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8000/db/tables")
    code_raw = sh("curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8000/raw/tables")
    if code_db != "401":
        fail("routes_db_401", f"/db/tables without token returned {code_db}")
    else:
        pass_("routes_db_401")
    if code_raw != "200":
        fail("routes_raw_200", f"/raw/tables without token returned {code_raw}")
    else:
        pass_("routes_raw_200")
except Exception as e:
    fail("routes", str(e))

pass_("no_extra_containers")

if os.environ.get("GIT_DIRTY", "false").lower() == "true" and os.environ.get("FORCE", "0") != "1":
    fail("git_clean", "working tree dirty")
else:
    pass_("git_clean")

if target != "gateway":
    for n in (
        "jaeger_reset", "jaeger_running", "otel_collector_running",
        "otel_collector_logs_clean", "jaeger_memory", "jaeger_trace_count",
        "traces_arriving",
    ):
        skip(n)
else:
    try:
        st = sh("docker", "inspect", "-f", "{{.State.Status}}", "gatewaydb-mcp-bench-otel-collector-1")
        if st != "running":
            fail("otel_collector_running", f"gatewaydb-mcp-bench-otel-collector-1 status={st} — governed runs without telemetry measure a broken exporter")
        else:
            pass_("otel_collector_running")
    except Exception as e:
        fail("otel_collector_running", f"gatewaydb-mcp-bench-otel-collector-1 missing: {e}")

    try:
        logs = subprocess.check_output(
            ["docker", "logs", "--since", "2m", "gatewaydb-mcp-bench-otel-collector-1"],
            text=True, stderr=subprocess.STDOUT, timeout=30,
        )
        real = [ln for ln in logs.splitlines() if any(
            tok in ln.lower() for tok in (
                "dropping data", "no more retries left", "failed to",
                "lookup jaeger", "connection refused", "no such host",
            )
        )]
        if real:
            fail("otel_collector_logs_clean", f"{len(real)} export error line(s); e.g. {real[-1][:160]}")
        else:
            pass_("otel_collector_logs_clean")
    except Exception as e:
        fail("otel_collector_logs_clean", str(e))

    try:
        req = urllib.request.Request(
            "http://localhost:8081/realms/mcp/protocol/openid-connect/token",
            data=urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": "mcp-agent",
                "client_secret": "mcp-agent-secret",
            }).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            token = json.loads(resp.read().decode())["access_token"]
        for _ in range(5):
            r = urllib.request.Request(
                "http://localhost:8000/db/tables",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(r, timeout=15) as resp:
                resp.read()
        time.sleep(3)
        with urllib.request.urlopen("http://localhost:16686/api/services", timeout=15) as resp:
            services = json.loads(resp.read().decode())
        flat = service_names(services)
        if "kong-bench" not in flat:
            fail("traces_arriving", f"jaeger services={flat!r} — missing kong-bench")
        else:
            pass_("traces_arriving")
    except Exception as e:
        fail("traces_arriving", str(e))

print(json.dumps(checks, separators=(",", ":")))
if failed:
    print("REFUSE: preflight failed — measurement would be invalid:", file=sys.stderr)
    for f in failed:
        print(f"  {f}", file=sys.stderr)
    sys.exit(1)
PY
}

run_one() {
  local target="$1" vus="$2" iterations="$3" repeat_index="$4" repeat_group_id="$5"
  local ts_compact ts_iso run_id out_path meta preflight spans_start spans_end k6_rc status
  local jaeger_mem_start="" jaeger_mem_end=""

  # Truncate spans before each run (and each repeat).
  : > results/spans.jsonl
  spans_start=$(wc -c < results/spans.jsonl | tr -d ' ')

  if [[ "$target" == "gateway" ]]; then
    reset_jaeger_store
  else
    echo "Skipping jaeger reset (target=$target — no telemetry in path)" >&2
  fi

  preflight=$(run_preflight "$target") || exit 1

  if [[ "$target" == "gateway" ]]; then
    jaeger_mem_start=$(jaeger_memory_mb)
  fi

  ts_compact=$(date -u +%Y%m%dT%H%M%SZ)
  ts_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  run_id="${ts_compact}-${target}-vus${vus}-iter${iterations}"
  if [[ "$REPEATS" -gt 1 ]]; then
    run_id="${run_id}-r${repeat_index}"
  fi
  out_path="results/runs/${run_id}.json"

  if [[ -e "$out_path" ]]; then
    echo "REFUSE: $out_path already exists — refusing to overwrite" >&2
    exit 1
  fi

  meta=$(
    export RUN_ID="$run_id" TS="$ts_iso" TARGET="$target" VUS="$vus" ITER="$iterations"
    export GIT_COMMIT GIT_DIRTY GIT_BRANCH K6_VERSION NOTE
    export IMAGES_JSON HOST_JSON BRIDGE_CFG_JSON CONTAINERS_RUNNING_JSON
    export PREFLIGHT_JSON="$preflight" REPEAT_INDEX="$repeat_index" REPEAT_GROUP="$repeat_group_id"
    export SPANS_START="$spans_start" NO_SPAN_FILE
    export JAEGER_MEM_START="${jaeger_mem_start}"
    python3 - <<'PY'
import json, os
meta = {
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
  "preflight": json.loads(os.environ["PREFLIGHT_JSON"]),
  "repeat_index": int(os.environ["REPEAT_INDEX"]),
  "repeat_group_id": os.environ["REPEAT_GROUP"],
  "spans_bytes_start": int(os.environ["SPANS_START"]),
  "no_span_file": os.environ.get("NO_SPAN_FILE", "0") == "1",
  "notes": os.environ.get("NOTE", ""),
}
start = os.environ.get("JAEGER_MEM_START", "").strip()
if start:
    meta["jaeger_memory_mb_start"] = float(start)
print(json.dumps(meta, separators=(",", ":")))
PY
  )

  echo "== run $run_id (repeat $repeat_index/$REPEATS) ==" >&2
  set +e
  "${COMPOSE[@]}" --profile bench run --rm \
      -e "TARGET=$target" \
      -e "VUS=$vus" \
      -e "ITERATIONS=$iterations" \
      -e "RUN_ID=$run_id" \
      -e "RUN_METADATA_JSON=$meta" \
      k6 run /scripts/latency.js
  k6_rc=$?
  set -e

  spans_end=$(wc -c < results/spans.jsonl | tr -d ' ')
  if [[ "$target" == "gateway" ]]; then
    jaeger_mem_end=$(jaeger_memory_mb) || jaeger_mem_end=""
  fi

  if [[ ! -f "$out_path" ]]; then
    echo "ERROR: k6 finished (rc=$k6_rc) but $out_path was not written" >&2
    exit 1
  fi

  # Patch spans_bytes_end / jaeger memory; enforce abort on non-zero k6 or incomplete metrics.
  export OUT_PATH="$out_path" K6_RC="$k6_rc" SPANS_END="$spans_end" ITERATIONS="$iterations"
  export JAEGER_MEM_END="${jaeger_mem_end}"
  python3 - <<'PY'
import json, os
path = os.environ["OUT_PATH"]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
meta = data.setdefault("run_metadata", {})
meta["spans_bytes_end"] = int(os.environ["SPANS_END"])
end = os.environ.get("JAEGER_MEM_END", "").strip()
if end:
    meta["jaeger_memory_mb_end"] = float(end)
status = data.get("status") or meta.get("status") or "complete"
reason = meta.get("abort_reason")
k6_rc = int(os.environ["K6_RC"])
iterations = int(os.environ["ITERATIONS"])
if k6_rc != 0:
    status = "aborted"
    reason = f"k6 exit status {k6_rc}" + (f"; {reason}" if reason else "")
main = ((data.get("metrics") or {}).get("ep_list_tables{phase:main}") or {}).get("values") or {}
count = main.get("count")
if status != "aborted" and count != iterations:
    status = "aborted"
    reason = f"ep_list_tables{{phase:main}}.count={count} != ITERATIONS={iterations}"
if status != "aborted" and meta.get("main_scenario_duration_s") is None:
    status = "aborted"
    reason = "main_scenario_duration_s is null"
data["status"] = status
meta["status"] = status
if reason:
    meta["abort_reason"] = reason
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print(status)
if reason:
    print(reason)
PY

  status=$(jq -r '.status' "$out_path")
  export INDEX_META="$meta" INDEX_PATH="$out_path" INDEX_STATUS="$status"
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
  "status": os.environ["INDEX_STATUS"],
  "repeat_index": meta.get("repeat_index"),
  "repeat_group_id": meta.get("repeat_group_id"),
}
with open("results/runs/index.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(row, separators=(",", ":")) + "\n")
PY

  echo "Wrote $out_path status=$status" >&2
  if [[ "$status" == "aborted" ]]; then
    echo "REFUSE: run aborted — see $out_path" >&2
    exit 1
  fi
  printf '%s\n' "$out_path"
}

run_config() {
  local target="$1" vus="$2" iterations="$3"
  local group_id i
  group_id="$(date -u +%Y%m%dT%H%M%SZ)-${target}-vus${vus}-iter${iterations}"
  for i in $(seq 1 "$REPEATS"); do
    run_one "$target" "$vus" "$iterations" "$i" "$group_id"
    if [[ "$i" -lt "$REPEATS" ]]; then
      echo "settle 5s between repeats…" >&2
      sleep 5
    fi
  done
}

if [[ "$SWEEP" -eq 1 ]]; then
  for target in direct passthrough gateway; do
    for vus in 1 10 50; do
      run_config "$target" "$vus" "$ITERATIONS"
      echo "settle 5s…" >&2
      sleep 5
    done
  done
  echo >&2
  echo "== sweep complete — see results/runs/index.jsonl ==" >&2
else
  run_config "$TARGET" "$VUS" "$ITERATIONS"
fi

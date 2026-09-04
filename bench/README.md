# Evaluation harness

Reproducible local evaluation of GatewayDB-MCP. No cloud account, no vendor
subscription, no enterprise licence, no employer infrastructure. Every component
is Apache 2.0 or equivalent, so results from this harness carry no
benchmark-publication restriction.

## What it stands up

| Service | Image | Role |
|---|---|---|
| `mysql-a` | `mysql:8.0` | Primary test database |
| `mysql-b` | `mysql:8.0` | Identical schema, different rows. E3 only |
| `postgres` | `postgres:16` | Cross-engine target. E4 only |
| `mariadb` | `mariadb:11.4` | Cross-engine target. E4 only; schema/data reused from MySQL |
| `bridge` | built from `sidecar/Dockerfile` | Direct path, `:8080` |
| `bridge-b` | same | Second instance, `:8082` |
| `bridge-pg` | same | PostgreSQL instance, `:8083` |
| `bridge-mariadb` | same Dockerfile with `-Pmariadb` | MariaDB instance, `:8084` |
| `keycloak` | `quay.io/keycloak/keycloak:26.0` | OAuth 2.1 authorization server, RS256 |
| `kong` | `kong:3.9` (OSS) | Gateway path, `:8000`. `jwt` + `rate-limiting` + `opentelemetry` |
| `apisix` | `apache/apisix:3.13.0-debian` | Second gateway path, `:9080`. `jwt-auth` + `limit-count` + `opentelemetry` (standalone, no etcd) |
| `otel-collector` | `otel/opentelemetry-collector-contrib` | Spans to Jaeger and to `results/spans.jsonl` |
| `jaeger` | `jaegertracing/all-in-one` | Trace UI, `:16686` |
| `mcp-server` | built from `mcp-server/` (FastMCP 2.x) | OpenAPI→MCP streamable HTTP, `:9090` → `/mcp` |
| `k6` | `grafana/k6` | Load generator (REST arms), profile-gated |
| `mcp-loadgen` | same image as `mcp-server` | Python loadgen (FastMCP Client + httpx rest-python), profile-gated |

Kong's `ai-mcp-proxy` plugin is AI Gateway Enterprise only and is deliberately
not used. MCP protocol translation is the gateway vendor's commodity feature,
not this project's contribution. What this harness validates is the bridge:
that it emits a consumable MCP tool specification, that it behaves correctly
behind a real policy chain, and what that chain costs.

## Reporting rule

**Report deltas, not absolutes.**

Absolute latency measured on a laptop under Docker Desktop is not a meaningful
number and a reviewer will say so. The difference between the direct path and
the gateway-mediated path is meaningful, because both carry the same
virtualisation overhead and it cancels.

### Citation rule (immutable runs)

Any performance figure appearing in a paper, the repository README, or any
external document **must cite a `run_id` present under `results/runs/`**. A
figure with no corresponding run record does not go in.

Each file under `results/runs/` is immutable (never overwritten) and carries a
`run_metadata` block: git commit, dirty flag, image digests, host spec, bridge
config, VUs, and iteration count. The append-only `results/runs/index.jsonl`
is the scannable ledger of every measurement.

Pre-provenance files left in `results/*.json` from earlier experiments are
historical only and must not be cited.

## Running

```bash
cd bench
# Latency stack only (6 services). Do NOT pass --profile extra for E1.
docker compose -f docker-compose.bench.yml up -d --build
docker compose -f docker-compose.bench.yml ps        # wait for healthy
```

`mysql-b`, `bridge-b`, `postgres`, `bridge-pg`, `mariadb`, and `bridge-mariadb`
live under Compose profile `extra`. They start only when E3/E4 bring them up.
Running E1 while they are up
is refused by `run-benchmark.sh` unless `--allow-extra-containers` is passed —
those JVMs and databases contend for the same Docker VM CPUs as the bridge,
Kong, and k6, and that contention is a plausible alternative explanation for
any measured gateway cost.

### Host resources (citable runs)

Docker Desktop's VM allocation materially affects absolute numbers and can
distort deltas when the VM is undersized. For citable runs, allocate **at least
8 CPUs and 8 GB** to Docker. The allocation actually used is recorded per run in
`run_metadata.host` (`docker_vm_cpus`, `docker_vm_memory_gb`).

### E1 — governance overhead

Five targets isolate proxy hop vs governance on two gateways (shared `direct`):

| TARGET | Path | Measures |
|---|---|---|
| `direct` | `:8080` | Bridge alone |
| `kong-passthrough` | Kong `:8000/raw` (no plugins) | Kong proxy hop |
| `kong-governed` | Kong `:8000/db` (jwt + rate-limit + otel) | Kong proxy + governance |
| `apisix-passthrough` | APISIX `:9080/raw` (proxy-rewrite only) | APISIX proxy hop |
| `apisix-governed` | APISIX `:9080/db` (jwt-auth + limit-count + otel) | APISIX proxy + governance |

Legacy aliases when **reading** archived runs (summariser normalizes in memory;
files are never rewritten): `passthrough` → `kong-passthrough`,
`gateway` → `kong-governed`.

Per gateway: `Δ proxy = {gw}-passthrough − direct`,
`Δ policy = {gw}-governed − {gw}-passthrough`,
`Δ total = {gw}-governed − direct`. **Lead with Δ policy.** The summariser also
emits a **cross-gateway Δ policy** table (Kong vs APISIX at each percentile and
VU). Absolute throughput on this harness is a property of the harness; only the
deltas transfer.

```bash
# Single run (immutable path under results/runs/); 3 repeats by default
./scripts/run-benchmark.sh --target direct            --vus 10 --iterations 5000 --no-span-file
./scripts/run-benchmark.sh --target kong-passthrough  --vus 10 --iterations 5000 --no-span-file
./scripts/run-benchmark.sh --target kong-governed     --vus 10 --iterations 5000 --no-span-file \
  --note "post-hardening"
./scripts/run-benchmark.sh --target apisix-passthrough --vus 10 --iterations 5000 --no-span-file
./scripts/run-benchmark.sh --target apisix-governed    --vus 10 --iterations 5000 --no-span-file

# Full VU sweep: --gateway kong|apisix|both (default both → five targets × 1,10,50 × repeats)
./scripts/run-benchmark.sh --sweep --gateway both --iterations 5000 --no-span-file --repeats 3

# Paste-ready Kong + APISIX decomposition + cross-gateway Δ policy
./scripts/summarise-runs.py --latest --format markdown --repeats 3
# Or select one sweep by run_id timestamp prefix
./scripts/summarise-runs.py --since 20260901T2137 --format markdown
# Regenerable RESULTS.md (hardware + tables + provenance from metadata)
./scripts/generate-results-doc.py --since 20260901T2137 > RESULTS.md
```

#### APISIX vs Kong policy equivalence

Intent-equivalent governance on both gateways; not bit-identical plugins.

- **Auth.** Both validate a static RS256 public key against the same Keycloak
  realm (no live JWKS on the hot path). Kong uses the `jwt` plugin; APISIX uses
  `jwt-auth` with `key_claim_name: iss` (requires APISIX ≥3.12; compose pins
  `3.13.0-debian`). The APISIX consumer username is `mcp_agent` (APISIX
  disallows hyphens in consumer names). Same work class as Kong's jwt plugin.
- **Rate limit.** Kong `rate-limiting` (local, per-minute) vs APISIX
  `limit-count` (local fixed window, `time_window: 60`). Both ceilings are set
  far above offered load. Algorithms differ — not bit-identical; intent is
  equivalent (plugin executes, does not throttle).
- **Telemetry.** Both export OTLP to the same collector; `service.name` is
  `kong-bench` vs `apisix-bench`. APISIX requires `plugin_metadata` for
  `opentelemetry` in `apisix.yaml` (route plugin alone is not enough).
- **Passthrough.** Kong uses route `strip_path`; APISIX uses `proxy-rewrite`
  only to strip the path prefix — not a governance plugin. Neither passthrough
  path has jwt / rate-limit / otel.
- **Deployment.** APISIX runs standalone (no etcd). File-provider YAML must end
  with `#END` or APISIX will not load it.

**Citable latency runs should pass `--no-span-file`.** That starts the collector
with only the Jaeger exporter (see `collector-jaeger-only.yaml`). The file
exporter in `collector.yaml` exists for E3 span comparison only — it appends to
`results/spans.jsonl` without rotation and on Docker Desktop every flush crosses
VirtioFS, which contaminates latency measurements.

The runner refuses to measure when preconditions fail: Jaeger/collector down on
a governed target, collector export errors, traces not arriving at Jaeger,
Jaeger RSS above `JAEGER_MEM_LIMIT_MB`, misconfigured `/db`/`/raw` routes,
unexpected containers, or a dirty git tree. Each check is recorded in
`run_metadata.preflight`. Aborted k6 runs are archived with `status=aborted`
and skipped by the summariser; `status=suspect` runs are included and flagged
(latency usable; throughput is always re-derived from
`iteration_duration{phase:main}`). Default governed runs do **not** restart
Jaeger/Kong per repeat; use `--reset-telemetry` only as a diagnostic (see
Telemetry confounds). Sweeps restart Jaeger once up front.

Writes `results/runs/<UTC>-<target>-vus<N>-iter<M>[-rK].json` and appends a line to
`results/runs/index.jsonl`. Dirty git trees are refused unless `--force` is
set (dirty runs must not be cited). First 30 seconds are a discarded warmup
scenario covering JIT, HikariCP pool fill, and Kong plugin compilation —
warmup samples are tagged `{phase:warmup}` and are **not** included in the
`{phase:main}` series the summariser reads. Throughput is reported as
`throughput_measured` (main phase only) and `throughput_wall` (k6's full-window
rate, reference only). Figures across repeats are `median [min–max]`; wide
spread (`(max−min)/median > 0.25`) is flagged.

#### Interpreting latency and throughput

- **VU=1** is serial: throughput is latency inverted. It is **not** a capacity
  measurement and must not be presented as one.
- **VU=10 and VU=50** saturate the direct path on this harness (~8,200 req/s
  historically), so those figures are capacity measurements — of a host running
  the load generator, Kong, the bridge, MySQL and Keycloak on shared CPUs.
- Both Kong arms share the same process overhead, so **Δ policy** isolates
  governance cost even when the host is contended. That is the column the paper
  should lead with.
- Absolute throughput is harness-bound. Cite deltas, not absolutes.

#### Telemetry confounds

In a governance benchmark the telemetry path is part of the system under test.
Its own failure modes are indistinguishable from the effect being measured
unless the harness verifies them independently. This harness has found four
such artifacts so far; they belong in the paper's threats-to-validity discussion.

1. **Response-write stall** — a ~40 ms delayed-ACK floor was attributed to the
   bridge until Content-Length + a single write (and `TCP_NODELAY`) removed the
   header/body split that triggered Nagle interaction on the return path.
2. **Unrotated spans file** — the collector's file exporter appended JSONL to
   `results/spans.jsonl` without rotation. Under load the file grew past 2 GB
   and every flush crossed Docker Desktop's VirtioFS, contaminating latency.
   Citable runs use `--no-span-file` / `collector-jaeger-only.yaml`.
3. **Jaeger absent from the container set** — with Jaeger down, the collector
   DNS-retried and dropped spans in large batches (~8,200 at a time),
   back-pressuring the Kong OpenTelemetry plugin. Governed throughput collapsed
   while the failure looked like "policy cost." Preflight now requires Jaeger
   running, clean collector logs, and the governed gateway's service name
   (`kong-bench` or `apisix-bench`) appearing in `/api/services` before k6 starts.
4. **Unbounded Jaeger store** — `jaeger-all-in-one` defaults to an unbounded
   in-memory span store. Three identical gateway runs (VU=1, 20k iterations)
   measured 678 → 669 → 405 req/s as the store grew from fresh to ~40k traces
   (41% of median). A prior run hit 5.7 GiB and aborted on k6's 10-minute
   ceiling. The harness keeps `MEMORY_MAX_TRACES=10000` (and
   `--memory.max-traces=10000`), the `jaeger_memory` preflight ceiling
   (`JAEGER_MEM_LIMIT_MB`, default 1024), and raises `maxDuration` to 30m.
   Sweeps restart Jaeger **once** before the first run for a clean start.
5. **Per-run telemetry reset** — a routine that stopped the collector, drained
   Kong's OTLP buffer, wiped Jaeger, and bounced Kong before every governed
   run made variance *worse*: 410 / **160** / 550 req/s (97% of median) on the
   same VU=1 / 20k config. The 160 req/s run was internally healthy
   (`ep_list_tables{phase:main}` med 0.8 ms) — `main_scenario_duration_s` had
   absorbed reset activity overlapping the measured window. Direct k6 with one
   manual Jaeger restart beforehand agreed within ~9%. The full reset is kept
   as `--reset-telemetry` / `--reset-telemetry-once` for diagnostics and must
   **not** be used for citable runs; default is no per-run reset. Duration is
   measured inside k6 from `Date.now()` marks, and a run is marked
   `status=suspect` if `throughput_measured` differs >2× from the value implied
   by `iteration_duration{phase:main}`.

`run_metadata.telemetry_reset` records `none`, `per_run`, or `once`. When the
opt-in routine runs, `telemetry_reset_seconds` records its own wall cost.

### E3 — reproducibility

```bash
./scripts/e3-reproducibility.sh
```

Brings up profile `extra` itself. Identical config, identical schema, different
rows. Passes when the two tool manifests are identical. This is what turns the
paper's §4.1 scenario from a hypothetical into a result. Stop `extra` before
returning to E1.

### E4 — cross-database

```bash
./scripts/e4-crossdb.sh
```

Brings up profile `extra` itself. Same config against MySQL, PostgreSQL, and
MariaDB. Asserts the resolved JDBC driver class for each engine so a MySQL
fallback cannot silently pass as MariaDB support. Type-mapping differences are
expected; report them rather than suppressing them. Stop `extra` before
returning to E1.

### Security-layer verification

```bash
./scripts/e-security.sh
```

Confirms L1 through L5 behave as the README claims, **including** the documented
`POST /query` confidentiality gap. That test is expected to return rows. Report
it. Reviewer 1 called the honest disclosure of this gap the strongest part of
the original submission.

### E2 — validator overhead

Not in this stack. `QueryValidator` is a Java class and belongs in a JMH
benchmark under `src/test/`, not behind HTTP. It replaces the deleted
"under 1.4 ms at p99" claim with a real measurement.

### E5 — MCP conformance and protocol overhead

Feed `bridge:8080/openapi` to an OSS OpenAPI-to-MCP server (FastMCP), connect an
MCP client, capture `tools/list` and one successful `tools/call` returning rows.
Measure MCP vs REST on the same four operations.

#### FastMCP choice (Apache-2.0)

Pinned **FastMCP 2.14.7** + **httpx 0.28.1** under `bench/mcp-server/`. Licence:
Apache-2.0 ([PrefectHQ/fastmcp](https://github.com/PrefectHQ/fastmcp)). FastMCP
2.x keeps `FastMCP.from_openapi(..., client=httpx.AsyncClient(...))`; 4.x moved
to `httpx2` and was not required here.

**Rejected / not used for this harness:**

| Alternative | Why not |
|---|---|
| `mcp-openapi-proxy` / similar thin proxies | Less maintained; weaker typed tool schema from OpenAPI than FastMCP |
| `awslabs/openapi-mcp-server` | AWS Labs packaging; extra vendor surface for a paper that already avoids cloud accounts |
| Speakeasy Gram | Requires an account / SaaS path — out of scope for this offline harness |

**Finding — `operationId` vs `x-mcp-tool`:** FastMCP derives MCP tool names from
OpenAPI `operationId`, **not** from `x-mcp-tool`. The bridge sets both to the
same eight names (`list_tables`, `run_query`, `get_{t}_rows`,
`describe_{t}_schema` for customers/orders/products), so names should match.
Conformance (`./scripts/mcp-conformance.sh`) **stops with a non-zero exit** if
`tools/list` does not equal that set.

**OpenAPI fidelity (fixed in `src/`):** `GenerateOpenAPI` emits a typed
`/query` requestBody (`sql` + `params`) and a `servers` URL from bridge config
(`API_SERVER_URL` / defaults). The mcp-server consumes `/openapi` as emitted —
no harness-side schema enrichment. **Prior MCP latency runs taken against
harness-enriched specs are discarded** and must not be cited; re-measure after
the `src/` fix.

Streamable HTTP: `mcp.run(transport="http", host="0.0.0.0", port=8080)` →
`http://host:8080/mcp` (published as host `:9090`).

#### Measurement arms

| TARGET | Path | Gateway / protocol / loadgen | Measures |
|---|---|---|---|
| `direct` | `:8080` | `gateway=none`, `protocol=rest`, `loadgen=k6` | Bridge alone (k6 REST baseline) |
| `rest-python` | `:8080` | `gateway=none`, `protocol=rest`, `loadgen=python` | Same four HTTP ops via **httpx.AsyncClient** (control) |
| `mcp-direct` | `:9090/mcp` → bridge | `gateway=none`, `protocol=mcp`, `loadgen=python` | MCP stack alone |
| `mcp-governed` | Kong `:8000/mcp` → mcp-server → bridge | `gateway=kong`, `protocol=mcp`, `loadgen=python` | MCP + Kong policy |

**Decomposition (protocol section of the summariser):**

| Quantity | Computed as | Role |
|---|---|---|
| Load generator cost | `rest-python − direct` | Tooling artifact (python/httpx vs k6) — not protocol |
| **Protocol overhead** | `mcp-direct − rest-python` | **Paper figure** (same python loadgen) |
| MCP policy cost | `mcp-governed − mcp-direct` | Kong jwt + rate + otel on MCP |
| REST policy cost | `kong-governed − kong-passthrough` | Same plugins on REST |

**Why Kong for mcp-governed (not APISIX):** Kong already carries the
jwt + rate-limiting + opentelemetry chain used for REST `/db`. Putting MCP on
the same gateway isolates **protocol** cost (mcp-direct − rest-python) and
**policy** cost per protocol without inventing a second MCP policy chain on
APISIX. Cross-gateway tables stay REST-focused.

Load generator: **Python** (`mcp-loadgen` / `loadgen.py`) for
`rest-python` / `mcp-direct` / `mcp-governed`. MCP arms use the FastMCP Client;
`rest-python` uses `httpx.AsyncClient` (same HTTP library FastMCP sits on).
REST gateway arms still use k6 `latency.js`. Metadata fields:
`run_metadata.protocol` is `rest` or `mcp`; `run_metadata.loadgen` is `k6` or
`python` (legacy files without `loadgen` are inferred). The summariser refuses
deltas across different loadgens except the explicit pairs in the table above.

```bash
./scripts/mcp-conformance.sh
# → results/mcp-conformance.txt

./scripts/run-benchmark.sh --target direct       --vus 1 --iterations 5000 --no-span-file --force
./scripts/run-benchmark.sh --target rest-python  --vus 1 --iterations 5000 --no-span-file --force
./scripts/run-benchmark.sh --target mcp-direct   --vus 1 --iterations 5000 --no-span-file --force
./scripts/run-benchmark.sh --target mcp-governed --vus 1 --iterations 5000 --no-span-file --force
./scripts/summarise-runs.py --latest --format markdown   # Protocol overhead table when rest-python + mcp-direct present
```

#### Part 2 — OpenAPI import scaffolding

```bash
# Requires bridge :8080 up
./scripts/generate-kong-from-openapi.sh
# → kong/kong-generated.yml + results/kong-import.diff (*.diff is gitignored; regenerate anytime)

./scripts/verify-kong-generated.sh
# → results/kong-import-verify.txt (throwaway Kong :18000; 401/200 after grafting plugins)

./scripts/try-apisix-openapi-import.sh
# → results/apisix-import.txt (documents ADC availability; does not invent a working OSS import)
```

**Kong import:** `deck file openapi2kong` (via `kong/deck` Docker image if `deck`
is not installed). The bridge OpenAPI includes `servers` from config; deck
conversion uses that upstream. Diff against hand-written `kong/kong.yml` is large by
design: deck emits one service with regex paths for each OpenAPI operation
(`~/tables$`, `~/query$`, …) and **no** JWT consumer, **no** `/db` strip prefix,
**no** `/raw` passthrough, **no** MCP `/mcp` → mcp-server route, and **no**
plugin chain. The hand-written file remains the runtime config.

Smoke test (`./scripts/verify-kong-generated.sh` → `results/kong-import-verify.txt`):
a throwaway Kong on `:18000` loads the generated file, grafts the hand-written
JWT consumer + `/db` plugin chain onto the generated `list_tables` route only,
and checks 401 without a token / 200 with one (bridge body returned). Ungrafted
generated routes (e.g. `/tables/orders/rows`) return 200 without auth — confirming
deck routes reach the bridge and that plugins must still be attached by hand.

**APISIX import:** `adc convert openapi` (Docker `api7/adc`) can produce routes
from the bridge OpenAPI, but the output is ADC-oriented declarative
config — this harness runs OSS APISIX **standalone** (file provider, no etcd,
no ADC controller) and does **not** auto-load that output. See
`results/apisix-import.txt` (and optional `results/apisix-generated.yaml`).
Hand-written `apisix/apisix.yaml` stays the source of truth. MCP governed
measurement uses Kong `/mcp`, not APISIX.

## Platform coverage this produces

| Platform | Status |
|---|---|
| Kong Gateway 3.9 (OSS) | Validated end-to-end, latency decomposition measured |
| Apache APISIX | Validated end-to-end, latency decomposition measured |
| Apigee X (embedded) | Deployment documented, not validated in this evaluation |
| Azure API Management | Deployment documented, not validated in this evaluation |

MySQL 8.0, PostgreSQL 16, and MariaDB 11.4 are validated as bridge backends
(E4). That gateway table goes in the paper as written. It is what Reviewer 2
(point 3) and Reviewer 3 (point 2) both asked for, and claiming more than it
says is what drew the objection in the first place.

## Notes and gotchas

- `kong/kong.yml` keys the JWT credential on `http://keycloak:8080/realms/mcp`.
  That string must equal the `iss` claim, which is fixed by `KC_HOSTNAME`.
  Change one, change both, or every gateway request returns 401.
- The Keycloak realm embeds a fixed RSA keypair so Kong can be configured
  statically and runs are reproducible. It is a test key. Never reuse it.
- `rate-limiting` is set to 1,000,000/min deliberately. The goal is to measure
  the cost of the plugin executing, not the cost of being throttled.
- `db/schema.sql` must be reconciled with the existing `sidecar/init-mysql.sql`
  before E3 means anything. Identical schema is the premise of the experiment.
- Run E1 with nothing else competing for CPU. Close the browser. Prefer ≥8 CPUs
  / ≥8 GB for Docker Desktop; see Host resources above. Stop profile `extra`
  before latency runs.

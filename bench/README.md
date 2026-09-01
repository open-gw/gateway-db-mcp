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
| `bridge` | built from `sidecar/Dockerfile` | Direct path, `:8080` |
| `bridge-b` | same | Second instance, `:8082` |
| `bridge-pg` | same | PostgreSQL instance, `:8083` |
| `keycloak` | `quay.io/keycloak/keycloak:26.0` | OAuth 2.1 authorization server, RS256 |
| `kong` | `kong:3.12` (OSS) | Gateway path, `:8000`. `jwt` + `rate-limiting` + `opentelemetry` |
| `otel-collector` | `otel/opentelemetry-collector-contrib` | Spans to Jaeger and to `results/spans.jsonl` |
| `jaeger` | `jaegertracing/all-in-one` | Trace UI, `:16686` |
| `k6` | `grafana/k6` | Load generator, profile-gated |

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

`mysql-b`, `bridge-b`, `postgres`, and `bridge-pg` live under Compose profile
`extra`. They start only when E3/E4 bring them up. Running E1 while they are up
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

Three arms isolate what the paper is about:

| TARGET | Path | Measures |
|---|---|---|
| `direct` | `:8080` | Bridge alone |
| `passthrough` | Kong `:8000/raw` (no plugins) | Proxy hop |
| `gateway` | Kong `:8000/db` (jwt + rate-limit + otel) | Proxy + governance |

`Δ proxy = passthrough − direct`, `Δ policy = gateway − passthrough`,
`Δ total = gateway − direct`. **Lead with Δ policy.** Absolute throughput on
this harness is a property of the harness; only the deltas transfer.

```bash
# Single run (immutable path under results/runs/); 3 repeats by default
./scripts/run-benchmark.sh --target direct      --vus 10 --iterations 5000 --no-span-file
./scripts/run-benchmark.sh --target passthrough --vus 10 --iterations 5000 --no-span-file
./scripts/run-benchmark.sh --target gateway     --vus 10 --iterations 5000 --no-span-file \
  --note "post-hardening"

# Full VU sweep (three targets × 1,10,50 × repeats)
./scripts/run-benchmark.sh --sweep --iterations 5000 --no-span-file --repeats 3

# Paste-ready three-arm decomposition with provenance footer
./scripts/summarise-runs.py --latest --format markdown --repeats 3
```

**Citable latency runs should pass `--no-span-file`.** That starts the collector
with only the Jaeger exporter (see `collector-jaeger-only.yaml`). The file
exporter in `collector.yaml` exists for E3 span comparison only — it appends to
`results/spans.jsonl` without rotation and on Docker Desktop every flush crosses
VirtioFS, which contaminates latency measurements.

The runner refuses to measure when preconditions fail: Jaeger/collector down on
a governed target, collector export errors, traces not arriving at Jaeger,
Jaeger RSS above `JAEGER_MEM_LIMIT_MB`, misconfigured `/db`/`/raw` routes,
unexpected containers, or a dirty git tree. Each check is recorded in
`run_metadata.preflight`. Aborted or suspect k6 runs are archived with the
corresponding `status` and skipped by the summariser. Default governed runs do
**not** restart Jaeger/Kong per repeat; use `--reset-telemetry` only as a
diagnostic (see Telemetry confounds). Sweeps restart Jaeger once up front.

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
   running, clean collector logs, and `kong-bench` appearing in
   `/api/services` before k6 starts.
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

Brings up profile `extra` itself. Same config against MySQL and PostgreSQL.
Type-mapping differences are expected; report them rather than suppressing them.
Stop `extra` before returning to E1.

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

### E5 — MCP conformance

Feed `bridge:8080/openapi` to an OSS OpenAPI-to-MCP server, connect MCP
Inspector, capture `tools/list` and one successful `tools/call` returning rows.
Verify the current API of whichever library you pick before wiring it in.
Reviewer-reproducible, unlike a vendor console screenshot.

## Platform coverage this produces

| Platform | Status after this harness |
|---|---|
| Kong Gateway 3.12 (OSS) | Validated end-to-end locally |
| MySQL 8.0 | Validated |
| PostgreSQL 16 | Validated |
| Apigee X embedded | Documented, not validated |
| Azure API Management | Documented, not validated |

That table goes in the paper as written. It is what Reviewer 2 (point 3) and
Reviewer 3 (point 2) both asked for, and claiming more than it says is what
drew the objection in the first place.

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

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
misconfigured `/db`/`/raw` routes, unexpected containers, or a dirty git tree.
Each check is recorded in `run_metadata.preflight`. Aborted k6 runs are archived
with `"status": "aborted"` and skipped by the summariser.

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

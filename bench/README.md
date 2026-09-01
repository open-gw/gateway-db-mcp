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

Every figure that leaves this harness states the harness. No figure is
described as measured unless it came out of an actual run recorded in
`results/`.

## Running

```bash
cd bench
docker compose -f docker-compose.bench.yml up -d --build
docker compose -f docker-compose.bench.yml ps        # wait for healthy
```

### E1 — governance overhead

```bash
for VUS in 1 10 50; do
  TARGET=direct  VUS=$VUS docker compose -f docker-compose.bench.yml \
    --profile bench run --rm k6 run /scripts/latency.js
  TARGET=gateway VUS=$VUS docker compose -f docker-compose.bench.yml \
    --profile bench run --rm k6 run /scripts/latency.js
done
```

Writes `results/latency-{direct,gateway}-vus{N}.json`. The reported result is
gateway minus direct, per endpoint, at p50/p95/p99. First 30 seconds are a
discarded warmup scenario covering JIT, HikariCP pool fill, and Kong plugin
compilation.

### E3 — reproducibility

```bash
./scripts/e3-reproducibility.sh
```

Identical config, identical schema, different rows. Passes when the two tool
manifests are identical. This is what turns the paper's §4.1 scenario from a
hypothetical into a result.

### E4 — cross-database

```bash
./scripts/e4-crossdb.sh
```

Same config against MySQL and PostgreSQL. Type-mapping differences are expected;
report them rather than suppressing them.

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
- Run E1 with nothing else competing for CPU. Close the browser.

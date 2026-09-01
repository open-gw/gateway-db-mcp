// E1 — governance overhead.
//
// Runs the same workload twice: once straight at the bridge, once through Kong
// with JWT validation, rate limiting and OTel export active. The reported
// result is the DELTA between the two, not the absolute latency.
//
// That distinction matters. Absolute numbers from a laptop under Docker are
// meaningless to a reviewer. The delta is not, because both paths carry the
// same virtualisation overhead and it cancels.
//
//   TARGET=direct  docker compose -f docker-compose.bench.yml --profile bench \
//                    run --rm k6 run /scripts/latency.js
//   TARGET=gateway docker compose -f docker-compose.bench.yml --profile bench \
//                    run --rm k6 run /scripts/latency.js

import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';

const TARGET = __ENV.TARGET || 'direct';
const VUS = parseInt(__ENV.VUS || '10', 10);
const ITERATIONS = parseInt(__ENV.ITERATIONS || '1000', 10);

const BASE = TARGET === 'gateway'
  ? (__ENV.GATEWAY_URL || 'http://kong:8000/db')
  : (__ENV.DIRECT_URL || 'http://bridge:8080');

const KEYCLOAK = __ENV.KEYCLOAK_URL || 'http://keycloak:8080';

// One Trend per endpoint so each gets its own p50/p95/p99 rather than a single
// blended figure across five very different operations.
const T = {
  list_tables: new Trend('ep_list_tables', true),
  describe:    new Trend('ep_describe_schema', true),
  get_rows:    new Trend('ep_get_rows', true),
  run_query:   new Trend('ep_run_query', true),
};

export const options = {
  scenarios: {
    // Discarded. JIT warmup, HikariCP pool fill, Kong plugin compile.
    warmup: {
      executor: 'constant-vus',
      vus: 3,
      duration: '30s',
      tags: { phase: 'warmup' },
      exec: 'workload',
      gracefulStop: '5s',
    },
    // Measured.
    main: {
      executor: 'shared-iterations',
      vus: VUS,
      iterations: ITERATIONS,
      maxDuration: '10m',
      startTime: '35s',
      tags: { phase: 'main' },
      exec: 'workload',
    },
  },
  thresholds: {
    // Correctness gate, not a performance gate. A run with failures is not a
    // measurement.
    'checks{phase:main}': ['rate>0.99'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max', 'count'],
};

export function setup() {
  if (TARGET !== 'gateway') return { token: null };

  const res = http.post(
    `${KEYCLOAK}/realms/mcp/protocol/openid-connect/token`,
    { grant_type: 'client_credentials', client_id: 'mcp-agent', client_secret: 'mcp-agent-secret' },
    { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }
  );
  if (res.status !== 200) {
    throw new Error(`Keycloak token request failed: ${res.status} ${res.body}`);
  }
  return { token: res.json('access_token') };
}

function hdrs(data) {
  const h = { 'Content-Type': 'application/json' };
  if (data.token) h['Authorization'] = `Bearer ${data.token}`;
  return h;
}

export function workload(data) {
  const h = hdrs(data);
  const phase = __ITER >= 0 ? undefined : undefined; // tags come from scenario

  let r = http.get(`${BASE}/tables`, { headers: h, tags: { ep: 'list_tables' } });
  check(r, { 'list_tables 200': (x) => x.status === 200 });
  T.list_tables.add(r.timings.duration);

  r = http.get(`${BASE}/tables/orders/schema`, { headers: h, tags: { ep: 'describe_schema' } });
  check(r, { 'describe_schema 200': (x) => x.status === 200 });
  T.describe.add(r.timings.duration);

  r = http.get(`${BASE}/tables/orders/rows?limit=100`, { headers: h, tags: { ep: 'get_rows' } });
  check(r, { 'get_rows 200': (x) => x.status === 200 });
  T.get_rows.add(r.timings.duration);

  r = http.post(
    `${BASE}/query`,
    JSON.stringify({ sql: 'SELECT id,status,total FROM orders WHERE status=?', params: ['completed'] }),
    { headers: h, tags: { ep: 'run_query' } }
  );
  check(r, { 'run_query 200': (x) => x.status === 200 });
  T.run_query.add(r.timings.duration);
}

// k6 requires a default export even when every scenario names an exec function.
export default function () { }

export function handleSummary(summary) {
  const out = `/results/latency-${TARGET}-vus${VUS}.json`;
  return {
    [out]: JSON.stringify(summary, null, 2),
    stdout: `\nWrote ${out}\n`,
  };
}

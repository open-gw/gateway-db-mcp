// E1 — governance overhead.
//
// Three arms on the same workload:
//   direct       — client → bridge :8080
//   passthrough  — client → Kong /raw (no plugins) → bridge
//   gateway      — client → Kong /db (jwt + rate-limit + otel) → bridge
//
//   passthrough − direct  = proxy hop / proxying cost
//   gateway − passthrough = governance policy cost  ← paper claim
//   gateway − direct      = total mediation cost
//
// Absolute numbers from a laptop under Docker are meaningless to a reviewer.
// The deltas are not, because shared virtualisation overhead cancels.
//
// Prefer the wrapper so every run is immutable and self-describing:
//
//   ./scripts/run-benchmark.sh --target direct      --vus 10 --iterations 5000
//   ./scripts/run-benchmark.sh --target passthrough --vus 10 --iterations 5000
//   ./scripts/run-benchmark.sh --target gateway     --vus 10 --iterations 5000
//
// Direct k6 invocation still works, but writes under results/runs/ only when
// RUN_ID and RUN_METADATA_JSON are supplied by the wrapper.

import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Trend } from 'k6/metrics';

const TARGET = __ENV.TARGET || 'direct';
const VUS = parseInt(__ENV.VUS || '10', 10);
const ITERATIONS = parseInt(__ENV.ITERATIONS || '1000', 10);

function resolveBase(target) {
  if (target === 'gateway') {
    return __ENV.GATEWAY_URL || 'http://kong:8000/db';
  }
  if (target === 'passthrough') {
    return __ENV.PASSTHROUGH_URL || 'http://kong:8000/raw';
  }
  if (target === 'direct') {
    return __ENV.DIRECT_URL || 'http://bridge:8080';
  }
  throw new Error(
    `Unknown TARGET=${target}; expected direct|passthrough|gateway`,
  );
}

const BASE = resolveBase(TARGET);

const KEYCLOAK = __ENV.KEYCLOAK_URL || 'http://keycloak:8080';

// One Trend per endpoint so each gets its own p50/p95/p99 rather than a single
// blended figure across five very different operations.
const T = {
  list_tables: new Trend('ep_list_tables', true),
  describe:    new Trend('ep_describe_schema', true),
  get_rows:    new Trend('ep_get_rows', true),
  run_query:   new Trend('ep_run_query', true),
};

// Wall-clock marks for the main scenario (diagnostic). Every main iteration
// records Date.now() at entry and exit; handleSummary keeps
// (max − min) / 1000 as main_scenario_duration_s_walltrend. Authoritative
// duration comes from iteration_duration{phase:main}.avg — see handleSummary.
const mainWallMark = new Trend('main_wall_mark_ms', false);

// Endpoint steps exercised once per iteration. Length is requests_per_iteration
// — keep this array as the single source of truth for that count.
const ENDPOINT_STEPS = [
  {
    trend: T.list_tables,
    checkName: 'list_tables 200',
    run: (h) => http.get(`${BASE}/tables`, { headers: h, tags: { ep: 'list_tables' } }),
  },
  {
    trend: T.describe,
    checkName: 'describe_schema 200',
    run: (h) => http.get(`${BASE}/tables/orders/schema`, {
      headers: h, tags: { ep: 'describe_schema' },
    }),
  },
  {
    trend: T.get_rows,
    checkName: 'get_rows 200',
    run: (h) => http.get(`${BASE}/tables/orders/rows?limit=100`, {
      headers: h, tags: { ep: 'get_rows' },
    }),
  },
  {
    trend: T.run_query,
    checkName: 'run_query 200',
    run: (h) => http.post(
      `${BASE}/query`,
      JSON.stringify({
        sql: 'SELECT id,status,total FROM orders WHERE status=?',
        params: ['completed'],
      }),
      { headers: h, tags: { ep: 'run_query' } },
    ),
  },
];

const REQUESTS_PER_ITERATION = ENDPOINT_STEPS.length;

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
      maxDuration: '30m',
      startTime: '35s',
      tags: { phase: 'main' },
      exec: 'workload',
    },
  },
  thresholds: {
    // Correctness gate, not a performance gate. A run with failures is not a
    // measurement. Thresholds on {phase:main} also force k6 to materialise the
    // filtered series into the summary JSON (required for Trend sub-metrics).
    'checks{phase:main}': ['rate>0.99'],
    'iteration_duration{phase:main}': ['max<600000'],
    'ep_list_tables{phase:main}':     ['p(99)<60000'],
    'ep_describe_schema{phase:main}': ['p(99)<60000'],
    'ep_get_rows{phase:main}':        ['p(99)<60000'],
    'ep_run_query{phase:main}':       ['p(99)<60000'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max', 'count'],
};

export function setup() {
  // Token only for the governed arm. Passthrough must NOT send Authorization —
  // that would add header-parsing work Kong would otherwise skip.
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
  // Scenario tags do not flow onto custom Trend.add() samples — tag explicitly
  // so warmup and main never share a measured series.
  const phase = exec.scenario.name === 'main' ? 'main' : 'warmup';
  const phaseTag = { phase };

  if (phase === 'main') {
    mainWallMark.add(Date.now());
  }

  for (const step of ENDPOINT_STEPS) {
    const r = step.run(h);
    check(r, { [step.checkName]: (x) => x.status === 200 });
    step.trend.add(r.timings.duration, phaseTag);
  }

  if (phase === 'main') {
    mainWallMark.add(Date.now());
  }
}

// k6 requires a default export even when every scenario names an exec function.
export default function () { }

export function handleSummary(summary) {
  const runId = __ENV.RUN_ID;
  if (!runId) {
    // Wrapper did not run — refuse to write a colliding legacy path.
    const msg = '\nERROR: RUN_ID unset. Use ./scripts/run-benchmark.sh so results are immutable.\n';
    return { stdout: msg };
  }

  const out = `/results/runs/${runId}.json`;
  let metadata = {};
  if (__ENV.RUN_METADATA_JSON) {
    try {
      metadata = JSON.parse(__ENV.RUN_METADATA_JSON);
    } catch (e) {
      metadata = { parse_error: String(e), raw: __ENV.RUN_METADATA_JSON };
    }
  }

  // Authoritative measured-phase duration from iteration_duration avg:
  //   duration_s = avg_ms × iterations / vus / 1000
  // The Date.now Trend (max−min) is retained as walltrend for comparison; at
  // high VU it can span VU create/teardown and inflate far beyond the scenario.
  const metrics = summary.metrics || {};
  const mark = metrics.main_wall_mark_ms;
  const markVals = mark && mark.values ? mark.values : null;
  let walltrendS = null;
  if (markVals && markVals.max != null && markVals.min != null && markVals.max > markVals.min) {
    walltrendS = (markVals.max - markVals.min) / 1000.0;
  }

  const iterVals = (metrics['iteration_duration{phase:main}'] || {}).values || {};
  let mainDurationS = null;
  let throughputMeasured = null;
  if (iterVals.avg != null && VUS > 0) {
    mainDurationS = (iterVals.avg * ITERATIONS) / VUS / 1000.0;
    if (mainDurationS > 0) {
      throughputMeasured = (ITERATIONS * REQUESTS_PER_ITERATION) / mainDurationS;
    }
  }

  const httpReqs = (metrics.http_reqs || {}).values || {};
  metadata.requests_per_iteration = REQUESTS_PER_ITERATION;
  metadata.main_scenario_duration_s = mainDurationS;
  metadata.main_scenario_duration_s_walltrend = walltrendS;
  metadata.throughput_measured = throughputMeasured;
  metadata.throughput_wall = httpReqs.rate != null ? httpReqs.rate : null;
  metadata.main_duration_method =
    'iteration_duration{phase:main}.avg_ms × iterations / vus / 1000';

  if (
    mainDurationS != null && walltrendS != null && mainDurationS > 0
    && Math.abs(walltrendS - mainDurationS) / mainDurationS > 0.20
  ) {
    metadata.duration_metric_divergence = true;
  } else if (mainDurationS != null && walltrendS != null) {
    metadata.duration_metric_divergence = false;
  }

  // Completeness: measured-phase count must equal ITERATIONS and duration must exist.
  const mainList = (metrics['ep_list_tables{phase:main}'] || {}).values || {};
  const mainCount = mainList.count != null ? mainList.count : null;
  let status = 'complete';
  let abortReason = null;
  if (mainDurationS == null) {
    status = 'aborted';
    abortReason = 'main_scenario_duration_s is null (missing iteration_duration{phase:main}.avg)';
  } else if (mainCount !== ITERATIONS) {
    status = 'aborted';
    abortReason = `ep_list_tables{phase:main}.count=${mainCount} != ITERATIONS=${ITERATIONS}`;
  }

  // Suspect: iteration time far exceeds sum of endpoint averages — time spent
  // outside the measured requests (not a throughput/duration circular check).
  if (status === 'complete' && iterVals.avg != null) {
    let epSum = 0;
    let epN = 0;
    for (const name of [
      'ep_list_tables{phase:main}',
      'ep_describe_schema{phase:main}',
      'ep_get_rows{phase:main}',
      'ep_run_query{phase:main}',
    ]) {
      const avg = ((metrics[name] || {}).values || {}).avg;
      if (avg != null) {
        epSum += avg;
        epN += 1;
      }
    }
    if (epN === REQUESTS_PER_ITERATION && epSum > 0) {
      metadata.iteration_avg_ms = iterVals.avg;
      metadata.endpoint_avg_sum_ms = epSum;
      const ratio = iterVals.avg / epSum;
      if (ratio > 3) {
        status = 'suspect';
        abortReason =
          `iteration_duration.avg=${iterVals.avg} ms exceeds sum of ep_*.avg=` +
          `${epSum} ms by >3x (ratio=${ratio.toFixed(2)})`;
      }
    }
  }

  metadata.status = status;
  if (abortReason) {
    if (status === 'suspect') metadata.suspect_reason = abortReason;
    else metadata.abort_reason = abortReason;
  }

  const payload = Object.assign({}, summary, {
    run_metadata: metadata,
    status: status,
  });
  return {
    [out]: JSON.stringify(payload, null, 2),
    stdout: `\nWrote ${out} status=${status}\n`
      + `main_scenario_duration_s=${mainDurationS}`
      + (walltrendS != null ? `  walltrend=${walltrendS}` : '')
      + `\n`
      + `throughput_measured=${throughputMeasured}  throughput_wall=${metadata.throughput_wall}\n`,
  };
}

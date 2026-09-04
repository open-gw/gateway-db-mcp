// E1 MCP arm — protocol overhead vs REST (companion to latency.js).
//
// Prefer this script when streamable-HTTP session handling in k6 works against
// the FastMCP server. The harness default for mcp-direct / mcp-governed is the
// Python loadgen (bench/mcp-server/loadgen.py) because initialize + session id
// headers are awkward in k6; keep this file for experiments and REST-parity
// documentation of the four tools/call ops.
//
// TARGET: mcp-direct | mcp-governed
//
//   ./scripts/run-benchmark.sh --target mcp-direct --vus 10 --iterations 1000
//
// This script is NOT invoked by run-benchmark.sh today (Python loadgen is).

import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Trend } from 'k6/metrics';

const TARGET = __ENV.TARGET || 'mcp-direct';
const VUS = parseInt(__ENV.VUS || '10', 10);
const ITERATIONS = parseInt(__ENV.ITERATIONS || '1000', 10);

function resolveUrl(target) {
  if (target === 'mcp-governed') {
    return __ENV.MCP_GOVERNED_URL || 'http://kong:8000/mcp';
  }
  if (target === 'mcp-direct') {
    return __ENV.MCP_DIRECT_URL || 'http://mcp-server:8080/mcp';
  }
  throw new Error(`Unknown TARGET=${target}; expected mcp-direct|mcp-governed`);
}

const MCP_URL = resolveUrl(TARGET);
const IS_GOVERNED = TARGET === 'mcp-governed';
const KEYCLOAK = __ENV.KEYCLOAK_URL || 'http://keycloak:8080';
const MCP_PROTOCOL_VERSION = __ENV.MCP_PROTOCOL_VERSION || '2024-11-05';

const T = {
  list_tables: new Trend('ep_list_tables', true),
  describe: new Trend('ep_describe_schema', true),
  get_rows: new Trend('ep_get_rows', true),
  run_query: new Trend('ep_run_query', true),
};
const mainWallMark = new Trend('main_wall_mark_ms', false);

let rpcId = 1;
function nextId() {
  return rpcId++;
}

function mcpHeaders(token, sessionId) {
  const h = {
    'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream',
    'MCP-Protocol-Version': MCP_PROTOCOL_VERSION,
  };
  if (token) h.Authorization = `Bearer ${token}`;
  if (sessionId) h['Mcp-Session-Id'] = sessionId;
  return h;
}

function parseRpc(res) {
  // Streamable HTTP may return JSON or SSE. Prefer JSON body.
  const ct = (res.headers['Content-Type'] || res.headers['content-type'] || '');
  if (ct.indexOf('text/event-stream') >= 0) {
    const lines = String(res.body || '').split('\n');
    for (const line of lines) {
      if (line.startsWith('data:')) {
        return JSON.parse(line.slice(5).trim());
      }
    }
    throw new Error(`SSE body without data: line: ${String(res.body).slice(0, 200)}`);
  }
  return res.json();
}

function rpc(url, headers, method, params) {
  const body = JSON.stringify({
    jsonrpc: '2.0',
    id: nextId(),
    method,
    params: params || {},
  });
  const res = http.post(url, body, { headers, tags: { mcp_method: method } });
  return { res, msg: parseRpc(res) };
}

export const options = {
  scenarios: {
    warmup: {
      executor: 'constant-vus',
      vus: 3,
      duration: '30s',
      tags: { phase: 'warmup' },
      exec: 'workload',
      gracefulStop: '5s',
    },
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
    'checks{phase:main}': ['rate>0.99'],
    'iteration_duration{phase:main}': ['max<600000'],
    'ep_list_tables{phase:main}': ['p(99)<60000'],
    'ep_describe_schema{phase:main}': ['p(99)<60000'],
    'ep_get_rows{phase:main}': ['p(99)<60000'],
    'ep_run_query{phase:main}': ['p(99)<60000'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max', 'count'],
};

export function setup() {
  let token = null;
  if (IS_GOVERNED) {
    const res = http.post(
      `${KEYCLOAK}/realms/mcp/protocol/openid-connect/token`,
      {
        grant_type: 'client_credentials',
        client_id: 'mcp-agent',
        client_secret: 'mcp-agent-secret',
      },
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
    );
    if (res.status !== 200) {
      throw new Error(`Keycloak token request failed: ${res.status} ${res.body}`);
    }
    token = res.json('access_token');
  }

  // Session initialize once in setup; many streamable-HTTP servers bind the
  // session to a single connection — per-VU re-init may be required. The
  // Python loadgen is the supported path when this fails under load.
  const h = mcpHeaders(token, null);
  const init = rpc(MCP_URL, h, 'initialize', {
    protocolVersion: MCP_PROTOCOL_VERSION,
    capabilities: {},
    clientInfo: { name: 'k6-mcp-latency', version: '0.1.0' },
  });
  const sessionId =
    init.res.headers['Mcp-Session-Id'] ||
    init.res.headers['mcp-session-id'] ||
    null;
  // notifications/initialized (no id)
  http.post(
    MCP_URL,
    JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }),
    { headers: mcpHeaders(token, sessionId) },
  );

  // tools/list once
  const listed = rpc(MCP_URL, mcpHeaders(token, sessionId), 'tools/list', {});
  return { token, sessionId, tools: listed.msg };
}

const TOOL_STEPS = [
  { trend: T.list_tables, checkName: 'list_tables ok', name: 'list_tables', args: {} },
  {
    trend: T.describe,
    checkName: 'describe_orders_schema ok',
    name: 'describe_orders_schema',
    args: {},
  },
  {
    trend: T.get_rows,
    checkName: 'get_orders_rows ok',
    name: 'get_orders_rows',
    args: { limit: 100 },
  },
  {
    trend: T.run_query,
    checkName: 'run_query ok',
    name: 'run_query',
    args: {
      sql: 'SELECT id,status,total FROM orders WHERE status=?',
      params: ['completed'],
    },
  },
];

const REQUESTS_PER_ITERATION = TOOL_STEPS.length;

export function workload(data) {
  const phase = exec.scenario.name === 'main' ? 'main' : 'warmup';
  const phaseTag = { phase };
  if (phase === 'main') mainWallMark.add(Date.now());

  const h = mcpHeaders(data.token, data.sessionId);
  for (const step of TOOL_STEPS) {
    const t0 = Date.now();
    const { res, msg } = rpc(MCP_URL, h, 'tools/call', {
      name: step.name,
      arguments: step.args,
    });
    const ok = res.status === 200 && msg && !msg.error;
    check(res, { [step.checkName]: () => ok });
    step.trend.add(Date.now() - t0, phaseTag);
  }

  if (phase === 'main') mainWallMark.add(Date.now());
}

export default function () {}

export function handleSummary(summary) {
  const runId = __ENV.RUN_ID;
  if (!runId) {
    return { stdout: '\nERROR: RUN_ID unset. Use ./scripts/run-benchmark.sh.\n' };
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
  metadata.protocol = 'mcp';
  metadata.requests_per_iteration = REQUESTS_PER_ITERATION;
  metadata.loadgen = 'k6-streamable-http';

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
  metadata.main_scenario_duration_s = mainDurationS;
  metadata.main_scenario_duration_s_walltrend = walltrendS;
  metadata.throughput_measured = throughputMeasured;
  metadata.throughput_wall = httpReqs.rate != null ? httpReqs.rate : null;
  metadata.main_duration_method =
    'iteration_duration{phase:main}.avg_ms × iterations / vus / 1000';

  const mainList = (metrics['ep_list_tables{phase:main}'] || {}).values || {};
  const mainCount = mainList.count != null ? mainList.count : null;
  let status = 'complete';
  let abortReason = null;
  if (mainDurationS == null) {
    status = 'aborted';
    abortReason = 'main_scenario_duration_s is null';
  } else if (mainCount !== ITERATIONS) {
    status = 'aborted';
    abortReason = `ep_list_tables{phase:main}.count=${mainCount} != ITERATIONS=${ITERATIONS}`;
  }
  metadata.status = status;
  if (abortReason) metadata.abort_reason = abortReason;

  const payload = Object.assign({}, summary, { run_metadata: metadata, status });
  return {
    [out]: JSON.stringify(payload, null, 2),
    stdout: `\nWrote ${out} status=${status}\n`,
  };
}

#!/usr/bin/env python3
"""Latency load generator for MCP and rest-python control arms.

Emits the same summary JSON shape as k6/latency.js handleSummary so
run-benchmark.sh and summarise-runs.py work unchanged.

Targets:
  mcp-direct, mcp-governed — FastMCP Client tools/call path
  rest-python             — httpx.AsyncClient plain HTTP to the bridge
                            (same four ops as REST latency.js / MCP tools)

Streamable-HTTP session handling in k6 is fragile (initialize + session id);
MCP arms use FastMCP's Client which negotiates the wire format correctly.
rest-python uses httpx so loadgen cost can be isolated from MCP protocol
cost (same process / library stack as FastMCP's HTTP layer).

Four ops per iteration (same as REST latency.js):
  list_tables, describe_orders_schema, get_orders_rows(limit=100), run_query
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastmcp import Client

SQL = "SELECT id,status,total FROM orders WHERE status=?"
SQL_PARAMS = ["completed"]

REQUESTS_PER_ITERATION = 4

PYTHON_TARGETS = ("mcp-direct", "mcp-governed", "rest-python")


@dataclass
class Sample:
    duration_ms: float
    phase: str
    ok: bool


@dataclass
class Collector:
    samples: dict[str, list[Sample]] = field(default_factory=lambda: defaultdict(list))
    checks_ok: int = 0
    checks_total: int = 0
    iter_durations_main: list[float] = field(default_factory=list)
    wall_marks_main: list[float] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def add(
        self,
        ep: str,
        duration_ms: float,
        phase: str,
        ok: bool,
    ) -> None:
        async with self.lock:
            self.samples[ep].append(Sample(duration_ms, phase, ok))
            self.checks_total += 1
            if ok:
                self.checks_ok += 1


def resolve_url(target: str) -> str:
    if target == "mcp-governed":
        return os.environ.get("MCP_GOVERNED_URL", "http://kong:8000/mcp")
    if target == "mcp-direct":
        return os.environ.get("MCP_DIRECT_URL", "http://mcp-server:8080/mcp")
    if target == "rest-python":
        return (
            os.environ.get("REST_PYTHON_URL")
            or os.environ.get("DIRECT_URL")
            or "http://bridge:8080"
        )
    raise SystemExit(
        f"Unknown TARGET={target}; expected mcp-direct|mcp-governed|rest-python"
    )


def fetch_token(keycloak_url: str) -> str:
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": "mcp-agent",
        "client_secret": "mcp-agent-secret",
    }).encode()
    req = urllib.request.Request(
        f"{keycloak_url.rstrip('/')}/realms/mcp/protocol/openid-connect/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    token = body.get("access_token")
    if not token:
        raise RuntimeError(f"Keycloak token response missing access_token: {body}")
    return token


def mcp_client(url: str, headers: dict[str, str] | None) -> Client:
    if headers and "Authorization" in headers:
        from fastmcp.client.transports import StreamableHttpTransport
        return Client(transport=StreamableHttpTransport(url, headers=headers))
    return Client(url)


async def call_tool(client: Client, name: str, args: dict[str, Any]) -> None:
    await client.call_tool(name, args)


async def one_iteration_mcp(
    client: Client,
    collector: Collector,
    phase: str,
) -> None:
    t_iter0 = time.perf_counter()
    if phase == "main":
        async with collector.lock:
            collector.wall_marks_main.append(time.time() * 1000.0)

    steps = [
        ("list_tables", "list_tables", {}),
        ("describe_schema", "describe_orders_schema", {}),
        ("get_rows", "get_orders_rows", {"limit": 100}),
        ("run_query", "run_query", {
            "sql": SQL,
            "params": SQL_PARAMS,
        }),
    ]

    for ep, tool, args in steps:
        t0 = time.perf_counter()
        ok = False
        try:
            await call_tool(client, tool, args)
            ok = True
        except Exception as e:
            print(f"tools/call {tool} failed ({phase}): {e}", file=sys.stderr)
            ok = False
        dt = (time.perf_counter() - t0) * 1000.0
        await collector.add(ep, dt, phase, ok)

    if phase == "main":
        async with collector.lock:
            collector.wall_marks_main.append(time.time() * 1000.0)
            collector.iter_durations_main.append(
                (time.perf_counter() - t_iter0) * 1000.0
            )


async def one_iteration_rest(
    client: httpx.AsyncClient,
    base: str,
    collector: Collector,
    phase: str,
) -> None:
    t_iter0 = time.perf_counter()
    if phase == "main":
        async with collector.lock:
            collector.wall_marks_main.append(time.time() * 1000.0)

    steps = [
        ("list_tables", "GET", "/tables", None),
        ("describe_schema", "GET", "/tables/orders/schema", None),
        ("get_rows", "GET", "/tables/orders/rows?limit=100", None),
        ("run_query", "POST", "/query", {"sql": SQL, "params": SQL_PARAMS}),
    ]

    for ep, method, path, body in steps:
        t0 = time.perf_counter()
        ok = False
        try:
            if method == "GET":
                resp = await client.get(f"{base}{path}")
            else:
                resp = await client.post(f"{base}{path}", json=body)
            ok = resp.status_code == 200
            if not ok:
                print(
                    f"REST {method} {path} failed ({phase}): "
                    f"status={resp.status_code}",
                    file=sys.stderr,
                )
        except Exception as e:
            print(f"REST {method} {path} failed ({phase}): {e}", file=sys.stderr)
            ok = False
        dt = (time.perf_counter() - t0) * 1000.0
        await collector.add(ep, dt, phase, ok)

    if phase == "main":
        async with collector.lock:
            collector.wall_marks_main.append(time.time() * 1000.0)
            collector.iter_durations_main.append(
                (time.perf_counter() - t_iter0) * 1000.0
            )


async def worker_warmup_mcp(
    url: str,
    headers: dict[str, str] | None,
    collector: Collector,
    stop_at: float,
) -> None:
    client = mcp_client(url, headers)
    async with client:
        while time.time() < stop_at:
            await one_iteration_mcp(client, collector, "warmup")


async def worker_main_mcp(
    url: str,
    headers: dict[str, str] | None,
    collector: Collector,
    queue: asyncio.Queue,
) -> None:
    client = mcp_client(url, headers)
    async with client:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await one_iteration_mcp(client, collector, "main")
            queue.task_done()


async def worker_warmup_rest(
    base: str,
    headers: dict[str, str] | None,
    collector: Collector,
    stop_at: float,
) -> None:
    async with httpx.AsyncClient(headers=headers or {}, timeout=60.0) as client:
        while time.time() < stop_at:
            await one_iteration_rest(client, base, collector, "warmup")


async def worker_main_rest(
    base: str,
    headers: dict[str, str] | None,
    collector: Collector,
    queue: asyncio.Queue,
) -> None:
    async with httpx.AsyncClient(headers=headers or {}, timeout=60.0) as client:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await one_iteration_rest(client, base, collector, "main")
            queue.task_done()


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def trend_values(ms: list[float]) -> dict[str, float]:
    if not ms:
        return {"count": 0}
    return {
        "avg": statistics.mean(ms),
        "min": min(ms),
        "med": statistics.median(ms),
        "p(95)": percentile(ms, 95) or 0.0,
        "p(99)": percentile(ms, 99) or 0.0,
        "max": max(ms),
        "count": len(ms),
    }


def build_summary(
    collector: Collector,
    *,
    vus: int,
    iterations: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    ep_map = {
        "list_tables": "ep_list_tables",
        "describe_schema": "ep_describe_schema",
        "get_rows": "ep_get_rows",
        "run_query": "ep_run_query",
    }
    for ep, metric_base in ep_map.items():
        all_s = collector.samples.get(ep, [])
        for phase in ("warmup", "main"):
            vals = [s.duration_ms for s in all_s if s.phase == phase]
            key = f"{metric_base}{{phase:{phase}}}"
            metrics[key] = {"values": trend_values(vals), "type": "trend"}
        # Unfiltered series (warmup+main) for parity with k6 raw metrics.
        metrics[metric_base] = {
            "values": trend_values([s.duration_ms for s in all_s]),
            "type": "trend",
        }

    metrics["iteration_duration{phase:main}"] = {
        "values": trend_values(collector.iter_durations_main),
        "type": "trend",
    }
    if collector.wall_marks_main:
        metrics["main_wall_mark_ms"] = {
            "values": trend_values(collector.wall_marks_main),
            "type": "trend",
        }

    check_rate = (
        collector.checks_ok / collector.checks_total
        if collector.checks_total
        else 0.0
    )
    metrics["checks{phase:main}"] = {
        "values": {
            "rate": check_rate,
            "passes": collector.checks_ok,
            "fails": collector.checks_total - collector.checks_ok,
        },
        "type": "rate",
    }

    # Throughput bookkeeping (same formulas as latency.js handleSummary).
    iter_vals = metrics["iteration_duration{phase:main}"]["values"]
    main_duration_s = None
    throughput_measured = None
    if iter_vals.get("avg") is not None and vus > 0 and iterations > 0:
        main_duration_s = (iter_vals["avg"] * iterations) / vus / 1000.0
        if main_duration_s > 0:
            throughput_measured = (iterations * REQUESTS_PER_ITERATION) / main_duration_s

    walltrend_s = None
    mark = metrics.get("main_wall_mark_ms", {}).get("values") or {}
    if mark.get("max") is not None and mark.get("min") is not None and mark["max"] > mark["min"]:
        walltrend_s = (mark["max"] - mark["min"]) / 1000.0

    # Approximate http_reqs.rate over wall window (warmup+main).
    total_reqs = collector.checks_total
    wall_s = walltrend_s
    if wall_s is None and main_duration_s:
        wall_s = main_duration_s + 30.0  # warmup was 30s
    http_rate = (total_reqs / wall_s) if wall_s and wall_s > 0 else None
    metrics["http_reqs"] = {
        "values": {"count": total_reqs, "rate": http_rate},
        "type": "counter",
    }

    metadata = dict(metadata)
    metadata["requests_per_iteration"] = REQUESTS_PER_ITERATION
    metadata["main_scenario_duration_s"] = main_duration_s
    metadata["main_scenario_duration_s_walltrend"] = walltrend_s
    metadata["throughput_measured"] = throughput_measured
    metadata["throughput_wall"] = http_rate
    metadata["main_duration_method"] = (
        "iteration_duration{phase:main}.avg_ms × iterations / vus / 1000"
    )
    # Runner sets loadgen=python in RUN_METADATA_JSON; do not overwrite.
    if "loadgen" not in metadata:
        metadata["loadgen"] = "python"

    main_list = metrics.get("ep_list_tables{phase:main}", {}).get("values") or {}
    main_count = main_list.get("count")
    status = "complete"
    abort_reason = None
    if main_duration_s is None:
        status = "aborted"
        abort_reason = "main_scenario_duration_s is null"
    elif main_count != iterations:
        status = "aborted"
        abort_reason = (
            f"ep_list_tables{{phase:main}}.count={main_count} != ITERATIONS={iterations}"
        )
    elif check_rate <= 0.99:
        status = "aborted"
        abort_reason = f"checks rate {check_rate:.4f} <= 0.99"

    if status == "complete" and iter_vals.get("avg") is not None:
        ep_sum = 0.0
        ep_n = 0
        for name in (
            "ep_list_tables{phase:main}",
            "ep_describe_schema{phase:main}",
            "ep_get_rows{phase:main}",
            "ep_run_query{phase:main}",
        ):
            avg = (metrics.get(name) or {}).get("values", {}).get("avg")
            if avg is not None:
                ep_sum += avg
                ep_n += 1
        if ep_n == REQUESTS_PER_ITERATION and ep_sum > 0:
            metadata["iteration_avg_ms"] = iter_vals["avg"]
            metadata["endpoint_avg_sum_ms"] = ep_sum
            ratio = iter_vals["avg"] / ep_sum
            if ratio > 3:
                status = "suspect"
                abort_reason = (
                    f"iteration_duration.avg={iter_vals['avg']} ms exceeds sum of "
                    f"ep_*.avg={ep_sum} ms by >3x (ratio={ratio:.2f})"
                )

    metadata["status"] = status
    if abort_reason:
        if status == "suspect":
            metadata["suspect_reason"] = abort_reason
        else:
            metadata["abort_reason"] = abort_reason

    return {
        "root_group": {"name": "", "path": "", "id": "d41d8cd98f00b204e9800998ecf8427e", "groups": [], "checks": []},
        "options": {},
        "metrics": metrics,
        "run_metadata": metadata,
        "status": status,
    }


async def run_load(
    *,
    target: str,
    url: str,
    token: str | None,
    vus: int,
    iterations: int,
    warmup_s: float = 30.0,
) -> Collector:
    headers = None
    if token:
        headers = {"Authorization": f"Bearer {token}"}

    collector = Collector()
    is_rest = target == "rest-python"

    # Warmup: constant ~3 VUs for warmup_s.
    warmup_vus = min(3, max(1, vus))
    stop_at = time.time() + warmup_s
    mode = "REST/httpx" if is_rest else "MCP/FastMCP"
    print(
        f"Warmup {warmup_s}s with {warmup_vus} VU(s) ({mode}) against {url}",
        file=sys.stderr,
    )
    if is_rest:
        await asyncio.gather(*[
            worker_warmup_rest(url.rstrip("/"), headers, collector, stop_at)
            for _ in range(warmup_vus)
        ])
    else:
        await asyncio.gather(*[
            worker_warmup_mcp(url, headers, collector, stop_at)
            for _ in range(warmup_vus)
        ])

    queue: asyncio.Queue = asyncio.Queue()
    for i in range(iterations):
        queue.put_nowait(i)

    print(f"Main: {iterations} shared iterations, {vus} VU(s)", file=sys.stderr)
    if is_rest:
        await asyncio.gather(*[
            worker_main_rest(url.rstrip("/"), headers, collector, queue)
            for _ in range(vus)
        ])
    else:
        await asyncio.gather(*[
            worker_main_mcp(url, headers, collector, queue)
            for _ in range(vus)
        ])
    return collector


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", default=os.environ.get("TARGET", "mcp-direct"))
    ap.add_argument("--vus", type=int, default=int(os.environ.get("VUS", "10")))
    ap.add_argument("--iterations", type=int, default=int(os.environ.get("ITERATIONS", "1000")))
    ap.add_argument("--warmup-s", type=float, default=float(os.environ.get("WARMUP_S", "30")))
    args = ap.parse_args()

    run_id = os.environ.get("RUN_ID")
    if not run_id:
        print("ERROR: RUN_ID unset. Use ./scripts/run-benchmark.sh", file=sys.stderr)
        raise SystemExit(2)

    metadata: dict[str, Any] = {}
    raw_meta = os.environ.get("RUN_METADATA_JSON")
    if raw_meta:
        try:
            metadata = json.loads(raw_meta)
        except json.JSONDecodeError as e:
            metadata = {"parse_error": str(e), "raw": raw_meta}

    target = args.target
    if target not in PYTHON_TARGETS:
        print(
            f"Unknown TARGET={target}; expected {'|'.join(PYTHON_TARGETS)}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    url = resolve_url(target)
    token = None
    if target == "mcp-governed":
        keycloak = os.environ.get("KEYCLOAK_URL", "http://keycloak:8080")
        token = fetch_token(keycloak)

    collector = asyncio.run(
        run_load(
            target=target,
            url=url,
            token=token,
            vus=args.vus,
            iterations=args.iterations,
            warmup_s=args.warmup_s,
        )
    )

    summary = build_summary(
        collector,
        vus=args.vus,
        iterations=args.iterations,
        metadata=metadata,
    )

    out = f"/results/runs/{run_id}.json"
    # Allow local runs without docker volume.
    out_env = os.environ.get("RESULTS_PATH")
    if out_env:
        out = out_env
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    status = summary["status"]
    meta = summary["run_metadata"]
    print(
        f"\nWrote {out} status={status}\n"
        f"main_scenario_duration_s={meta.get('main_scenario_duration_s')}\n"
        f"throughput_measured={meta.get('throughput_measured')}  "
        f"throughput_wall={meta.get('throughput_wall')}\n",
        end="",
    )
    if status == "aborted":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

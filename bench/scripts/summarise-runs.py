#!/usr/bin/env python3
"""summarise-runs.py — emit comparison tables from archived immutable runs.

Standard library only. Never invents numbers: every figure comes from a file
under results/runs/ with a run_metadata provenance block.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parent.parent / "results" / "runs"

ENDPOINTS = [
    ("ep_list_tables", "list_tables"),
    ("ep_describe_schema", "describe_schema"),
    ("ep_get_rows", "get_rows"),
    ("ep_run_query", "run_query"),
]


def load_run(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "run_metadata" not in data:
        raise SystemExit(f"REFUSE: {path} has no run_metadata (pre-provenance file)")
    return data


def percentile(metric: dict, key: str) -> float | None:
    vals = metric.get("values") or {}
    if key not in vals:
        return None
    return float(vals[key])


def rps(run: dict) -> float | None:
    metrics = run.get("metrics") or {}
    http = metrics.get("http_reqs") or {}
    # Prefer rate if present; else count / test duration from state.
    vals = http.get("values") or {}
    if "rate" in vals:
        return float(vals["rate"])
    count = vals.get("count")
    state = run.get("state") or {}
    # testRunDurationMs appears in newer k6; fall back to iteration_duration count.
    dur_ms = state.get("testRunDurationMs")
    if count is not None and dur_ms:
        return float(count) / (float(dur_ms) / 1000.0)
    return None


def index_runs() -> list[dict]:
    rows = []
    for path in sorted(RUNS_DIR.glob("*.json")):
        if path.name == "index.jsonl":
            continue
        try:
            data = load_run(path)
        except SystemExit:
            continue
        meta = data["run_metadata"]
        rows.append({"path": path, "data": data, "meta": meta})
    return rows


def latest_pairs(rows: list[dict]) -> list[tuple[dict, dict]]:
    """Newest direct/gateway pair per VU level (same iterations preferred)."""
    by_key: dict[tuple, dict] = {}
    for row in rows:
        m = row["meta"]
        key = (m["target"], m["vus"], m["iterations"])
        prev = by_key.get(key)
        if prev is None or m["timestamp_utc"] > prev["meta"]["timestamp_utc"]:
            by_key[key] = row

    pairs = []
    vu_levels = sorted({r["meta"]["vus"] for r in by_key.values()})
    for vu in vu_levels:
        directs = [r for (t, v, _), r in by_key.items() if t == "direct" and v == vu]
        gateways = [r for (t, v, _), r in by_key.items() if t == "gateway" and v == vu]
        if not directs or not gateways:
            continue
        d = max(directs, key=lambda r: r["meta"]["timestamp_utc"])
        g = max(gateways, key=lambda r: r["meta"]["timestamp_utc"])
        pairs.append((d, g))
    return pairs


def fmt(n: float | None, digits: int = 2) -> str:
    if n is None:
        return "—"
    return f"{n:.{digits}f}"


def delta(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "—"
    return fmt(b - a)


def pct_cost(direct: float | None, gateway: float | None) -> str:
    if direct is None or gateway is None or direct == 0:
        return "—"
    return f"{((gateway - direct) / direct) * 100:.1f}%"


def render_pair(d: dict, g: dict, fmt_kind: str, allow_mixed: bool) -> str:
    md = d["meta"]
    mg = g["meta"]
    if md["git_commit"] != mg["git_commit"] and not allow_mixed:
        raise SystemExit(
            f"REFUSE: comparing different git commits\n"
            f"  direct={md['run_id']} commit={md['git_commit']}\n"
            f"  gateway={mg['run_id']} commit={mg['git_commit']}\n"
            f"Pass --allow-mixed-commit to override (not for paper figures)."
        )
    warn = ""
    if md["git_commit"] != mg["git_commit"] and allow_mixed:
        warn = (
            "WARNING: MIXED GIT COMMITS — delta is not attributable to the gateway alone.\n"
            f"  direct commit={md['git_commit']}\n"
            f"  gateway commit={mg['git_commit']}\n\n"
        )

    lines = []
    if warn:
        lines.append(warn.rstrip())

    title = f"VU={md['vus']} iterations={md['iterations']}"
    headers = ["endpoint", "direct p50", "direct p95", "direct p99",
               "gateway p50", "gateway p95", "gateway p99",
               "Δp50", "Δp95", "Δp99"]
    rows = []
    for metric_key, label in ENDPOINTS:
        dm = (d["data"].get("metrics") or {}).get(metric_key) or {}
        gm = (g["data"].get("metrics") or {}).get(metric_key) or {}
        dp50, dp95, dp99 = percentile(dm, "med"), percentile(dm, "p(95)"), percentile(dm, "p(99)")
        gp50, gp95, gp99 = percentile(gm, "med"), percentile(gm, "p(95)"), percentile(gm, "p(99)")
        rows.append([
            label,
            fmt(dp50), fmt(dp95), fmt(dp99),
            fmt(gp50), fmt(gp95), fmt(gp99),
            delta(dp50, gp50), delta(dp95, gp95), delta(dp99, gp99),
        ])

    drps, grps = rps(d["data"]), rps(g["data"])
    thr = [
        "throughput rps",
        fmt(drps, 1), "—", "—",
        fmt(grps, 1), "—", "—",
        delta(drps, grps) if drps and grps else "—",
        pct_cost(drps, grps),
        "—",
    ]

    if fmt_kind == "markdown":
        lines.append(f"### {title}")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for r in rows:
            lines.append("| " + " | ".join(r) + " |")
        lines.append("")
        lines.append(
            f"Throughput: direct {fmt(drps, 1)} req/s, gateway {fmt(grps, 1)} req/s, "
            f"gateway cost {pct_cost(drps, grps)}."
        )
    else:
        lines.append(title)
        lines.append("\t".join(headers))
        for r in rows:
            lines.append("\t".join(r))
        lines.append(
            f"throughput\tdirect={fmt(drps, 1)} rps\tgateway={fmt(grps, 1)} rps\t"
            f"cost={pct_cost(drps, grps)}"
        )

    lines.append("")
    lines.append(
        f"Provenance: direct={md['run_id']} gateway={mg['run_id']} "
        f"git={md['git_commit'][:12]} host={md['host'].get('cpu_model', '?')} "
        f"({md['host'].get('os', '?')})"
    )
    if md.get("notes") or mg.get("notes"):
        lines.append(f"Notes: direct={md.get('notes')!r} gateway={mg.get('notes')!r}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--latest", action="store_true", help="newest direct/gateway pair per VU")
    ap.add_argument("--run-ids", nargs=2, metavar=("A", "B"), help="compare two run ids")
    ap.add_argument("--format", choices=["text", "markdown"], default="text")
    ap.add_argument("--allow-mixed-commit", action="store_true")
    args = ap.parse_args()

    if not RUNS_DIR.is_dir():
        raise SystemExit(f"no runs directory at {RUNS_DIR}")

    rows = index_runs()
    if not rows:
        raise SystemExit(f"no provenance-bearing runs in {RUNS_DIR}")

    outputs = []
    if args.run_ids:
        a_id, b_id = args.run_ids
        by_id = {r["meta"]["run_id"]: r for r in rows}
        if a_id not in by_id or b_id not in by_id:
            raise SystemExit(f"run id not found: {a_id!r} / {b_id!r}")
        ra, rb = by_id[a_id], by_id[b_id]
        # Order as direct, gateway when possible
        if ra["meta"]["target"] == "gateway" and rb["meta"]["target"] == "direct":
            ra, rb = rb, ra
        outputs.append(render_pair(ra, rb, args.format, args.allow_mixed_commit))
    elif args.latest:
        pairs = latest_pairs(rows)
        if not pairs:
            raise SystemExit("no direct/gateway pairs found for --latest")
        for d, g in pairs:
            outputs.append(render_pair(d, g, args.format, args.allow_mixed_commit))
    else:
        ap.error("specify --latest or --run-ids")

    print("\n\n".join(outputs))


if __name__ == "__main__":
    main()

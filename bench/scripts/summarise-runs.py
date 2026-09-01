#!/usr/bin/env python3
"""summarise-runs.py — emit comparison tables from archived immutable runs.

Standard library only. Never invents numbers: every figure comes from a file
under results/runs/ with a run_metadata provenance block.

Reads ep_*{phase:main} series only — unfiltered ep_* include warmup samples and
must not be cited. Runs that predate phase tagging are refused, not silently
re-parsed.

Three-arm decomposition (when available):
  Δ proxy  = passthrough − direct   (Kong hop, no plugins)
  Δ policy = gateway − passthrough  (governance plugins)  ← paper claim
  Δ total  = gateway − direct
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parent.parent / "results" / "runs"

# (filtered metric key in summary JSON, display label)
ENDPOINTS = [
    ("ep_list_tables{phase:main}", "list_tables"),
    ("ep_describe_schema{phase:main}", "describe_schema"),
    ("ep_get_rows{phase:main}", "get_rows"),
    ("ep_run_query{phase:main}", "run_query"),
]

ARMS = ("direct", "passthrough", "gateway")
ARM_LABEL = {
    "direct": "direct",
    "passthrough": "passthrough",
    "gateway": "governed",
}


def load_run(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "run_metadata" not in data:
        raise SystemExit(f"REFUSE: {path} has no run_metadata (pre-provenance file)")
    return data


def require_phase_main(run: dict, path_hint: str) -> None:
    """Refuse runs that predate phase-tagged Trends (warmup was mixed in)."""
    metrics = run.get("metrics") or {}
    missing = [key for key, _ in ENDPOINTS if key not in metrics]
    if missing:
        run_id = (run.get("run_metadata") or {}).get("run_id", path_hint)
        raise SystemExit(
            f"REFUSE: run {run_id} predates the phase-tagging fix "
            f"(missing {', '.join(missing)}). "
            f"Do not cite unfiltered ep_* series from this file; re-run after "
            f"the warmup-exclusion change."
        )


def percentile(metric: dict, key: str) -> float | None:
    vals = metric.get("values") or {}
    if key not in vals:
        return None
    return float(vals[key])


def throughput_wall(run: dict) -> float | None:
    """k6 http_reqs.rate over the full test window — reference only."""
    meta = run.get("run_metadata") or {}
    if meta.get("throughput_wall") is not None:
        return float(meta["throughput_wall"])
    metrics = run.get("metrics") or {}
    http = metrics.get("http_reqs") or {}
    vals = http.get("values") or {}
    if "rate" in vals:
        return float(vals["rate"])
    return None


def throughput_measured(run: dict) -> float | None:
    """Measured-phase throughput: (iterations × reqs/iter) / main duration."""
    meta = run.get("run_metadata") or {}
    if meta.get("throughput_measured") is not None:
        return float(meta["throughput_measured"])
    duration = meta.get("main_scenario_duration_s")
    iterations = meta.get("iterations")
    rpi = meta.get("requests_per_iteration")
    if duration and iterations and rpi and float(duration) > 0:
        return (float(iterations) * float(rpi)) / float(duration)
    return None


def index_runs(*, require_phase: bool = False) -> list[dict]:
    rows = []
    for path in sorted(RUNS_DIR.glob("*.json")):
        if path.name == "index.jsonl":
            continue
        try:
            data = load_run(path)
        except SystemExit:
            continue
        if require_phase:
            metrics = data.get("metrics") or {}
            if any(key not in metrics for key, _ in ENDPOINTS):
                continue
        meta = data["run_metadata"]
        rows.append({"path": path, "data": data, "meta": meta})
    return rows


def latest_by_arm(rows: list[dict]) -> list[dict[str, dict]]:
    """Newest run per (target, vu) for the latest shared iteration count per VU.

    Returns a list of arm maps keyed by target name, one entry per VU level.
    Missing arms are omitted from the map (caller reports which are absent).
    """
    by_key: dict[tuple, dict] = {}
    for row in rows:
        m = row["meta"]
        key = (m["target"], m["vus"], m["iterations"])
        prev = by_key.get(key)
        if prev is None or m["timestamp_utc"] > prev["meta"]["timestamp_utc"]:
            by_key[key] = row

    vu_levels = sorted({r["meta"]["vus"] for r in by_key.values()})
    groups: list[dict[str, dict]] = []
    for vu in vu_levels:
        # Prefer the iteration count that has the most arms present.
        iter_counts = sorted({
            it for (t, v, it) in by_key if v == vu
        }, reverse=True)
        best: dict[str, dict] = {}
        best_n = -1
        for it in iter_counts:
            arms = {
                t: by_key[(t, vu, it)]
                for t in ARMS
                if (t, vu, it) in by_key
            }
            if len(arms) > best_n:
                best = arms
                best_n = len(arms)
        if best:
            groups.append(best)
    return groups


def fmt(n: float | None, digits: int = 2) -> str:
    if n is None:
        return "—"
    return f"{n:.{digits}f}"


def delta(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "—"
    return fmt(b - a)


def pct_delta(baseline: float | None, other: float | None) -> str:
    """Percent change of other relative to baseline (throughput cost style)."""
    if baseline is None or other is None or baseline == 0:
        return "—"
    return f"{((other - baseline) / baseline) * 100:.1f}%"


def check_commits(arms: dict[str, dict], allow_mixed: bool) -> str:
    commits = {a["meta"]["git_commit"] for a in arms.values()}
    if len(commits) <= 1:
        return ""
    detail = "\n".join(
        f"  {t}={arms[t]['meta']['run_id']} commit={arms[t]['meta']['git_commit']}"
        for t in ARMS if t in arms
    )
    if not allow_mixed:
        raise SystemExit(
            f"REFUSE: comparing different git commits\n{detail}\n"
            f"Pass --allow-mixed-commit to override (not for paper figures)."
        )
    return (
        "WARNING: MIXED GIT COMMITS — deltas are not attributable to mediation alone.\n"
        f"{detail}\n\n"
    )


def ep_percentiles(run: dict | None, metric_key: str) -> tuple[float | None, float | None, float | None]:
    if run is None:
        return None, None, None
    m = (run["data"].get("metrics") or {}).get(metric_key) or {}
    return percentile(m, "med"), percentile(m, "p(95)"), percentile(m, "p(99)")


def render_group(arms: dict[str, dict], fmt_kind: str, allow_mixed: bool) -> str:
    for row in arms.values():
        require_phase_main(row["data"], str(row["path"]))

    warn = check_commits(arms, allow_mixed)
    lines: list[str] = []
    if warn:
        lines.append(warn.rstrip())

    sample = next(iter(arms.values()))
    vu = sample["meta"]["vus"]
    iterations = sample["meta"]["iterations"]
    title = f"VU={vu} iterations={iterations}"

    missing = [ARM_LABEL[t] for t in ARMS if t not in arms]
    if missing:
        lines.append(f"NOTE: missing arm(s): {', '.join(missing)}. Showing available columns only.")

    # One table per percentile so the three-arm + three-delta layout stays readable.
    for pct_key, pct_label in (("med", "p50"), ("p(95)", "p95"), ("p(99)", "p99")):
        headers = [
            "endpoint",
            "direct", "passthrough", "governed",
            "Δ proxy", "Δ policy", "Δ total",
        ]
        rows = []
        for metric_key, label in ENDPOINTS:
            vals = {}
            for t in ARMS:
                if t not in arms:
                    vals[t] = None
                    continue
                m = (arms[t]["data"].get("metrics") or {}).get(metric_key) or {}
                vals[t] = percentile(m, pct_key)
            d, p, g = vals.get("direct"), vals.get("passthrough"), vals.get("gateway")
            rows.append([
                label,
                fmt(d), fmt(p), fmt(g),
                delta(d, p),   # proxy
                delta(p, g),   # policy
                delta(d, g),   # total
            ])

        if fmt_kind == "markdown":
            lines.append(f"### {title} — latency {pct_label} (ms)")
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for r in rows:
                lines.append("| " + " | ".join(r) + " |")
            lines.append("")
        else:
            lines.append(f"{title} latency {pct_label}")
            lines.append("\t".join(headers))
            for r in rows:
                lines.append("\t".join(r))
            lines.append("")

    # Throughput decomposition
    thr = {t: throughput_measured(arms[t]["data"]) if t in arms else None for t in ARMS}
    thr_wall = {t: throughput_wall(arms[t]["data"]) if t in arms else None for t in ARMS}
    d_t, p_t, g_t = thr["direct"], thr["passthrough"], thr["gateway"]

    if fmt_kind == "markdown":
        lines.append(
            f"**throughput_measured** (main phase, req/s): "
            f"direct {fmt(d_t, 1)}, passthrough {fmt(p_t, 1)}, governed {fmt(g_t, 1)}.  "
            f"Δ proxy cost {pct_delta(d_t, p_t)}, "
            f"Δ policy cost {pct_delta(p_t, g_t)}, "
            f"Δ total cost {pct_delta(d_t, g_t)}."
        )
        lines.append(
            f"throughput_wall (reference only): "
            f"direct {fmt(thr_wall['direct'], 1)}, "
            f"passthrough {fmt(thr_wall['passthrough'], 1)}, "
            f"governed {fmt(thr_wall['gateway'], 1)}."
        )
        lines.append("")
        lines.append(
            "Δ proxy = passthrough − direct (Kong hop). "
            "Δ policy = governed − passthrough (jwt + rate-limit + otel). "
            "Δ total = governed − direct. "
            "Lead with Δ policy."
        )
    else:
        lines.append(
            f"throughput_measured\tdirect={fmt(d_t, 1)}\tpassthrough={fmt(p_t, 1)}\t"
            f"governed={fmt(g_t, 1)}\t"
            f"proxy_cost={pct_delta(d_t, p_t)}\tpolicy_cost={pct_delta(p_t, g_t)}\t"
            f"total_cost={pct_delta(d_t, g_t)}"
        )
        lines.append(
            f"throughput_wall\tdirect={fmt(thr_wall['direct'], 1)}\t"
            f"passthrough={fmt(thr_wall['passthrough'], 1)}\t"
            f"governed={fmt(thr_wall['gateway'], 1)}\t(reference only)"
        )

    # Provenance
    ids = " ".join(
        f"{ARM_LABEL[t]}={arms[t]['meta']['run_id']}" for t in ARMS if t in arms
    )
    commit = sample["meta"]["git_commit"][:12]
    host = sample["meta"]["host"].get("cpu_model", "?")
    os_name = sample["meta"]["host"].get("os", "?")
    lines.append("")
    lines.append(f"Provenance: {ids} git={commit} host={host} ({os_name})")
    notes = [
        f"{ARM_LABEL[t]}={arms[t]['meta'].get('notes')!r}"
        for t in ARMS if t in arms and arms[t]["meta"].get("notes")
    ]
    if notes:
        lines.append("Notes: " + " ".join(notes))
    return "\n".join(lines)


def render_pair_legacy(d: dict, g: dict, fmt_kind: str, allow_mixed: bool) -> str:
    """Two-arm --run-ids path retained for ad-hoc comparisons."""
    return render_group(
        {"direct": d, "gateway": g} if d["meta"]["target"] == "direct"
        else {d["meta"]["target"]: d, g["meta"]["target"]: g},
        fmt_kind,
        allow_mixed,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--latest",
        action="store_true",
        help="newest arms per VU (direct / passthrough / gateway when present)",
    )
    ap.add_argument(
        "--run-ids",
        nargs="+",
        metavar="ID",
        help="compare 2 or 3 run ids (any of direct/passthrough/gateway)",
    )
    ap.add_argument("--format", choices=["text", "markdown"], default="text")
    ap.add_argument("--allow-mixed-commit", action="store_true")
    args = ap.parse_args()

    if not RUNS_DIR.is_dir():
        raise SystemExit(f"no runs directory at {RUNS_DIR}")

    # --latest skips pre-phase-tag archives; --run-ids still hard-refuses them
    # so an explicit citation attempt cannot silently use contaminated series.
    rows = index_runs(require_phase=bool(args.latest))
    if not rows:
        raise SystemExit(f"no provenance-bearing runs in {RUNS_DIR}")

    outputs = []
    if args.run_ids:
        if len(args.run_ids) < 2 or len(args.run_ids) > 3:
            raise SystemExit("--run-ids expects 2 or 3 run ids")
        all_rows = index_runs(require_phase=False)
        by_id = {r["meta"]["run_id"]: r for r in all_rows}
        arms: dict[str, dict] = {}
        for rid in args.run_ids:
            if rid not in by_id:
                raise SystemExit(f"run id not found: {rid!r}")
            row = by_id[rid]
            t = row["meta"]["target"]
            if t not in ARMS:
                raise SystemExit(f"run {rid} has unknown target {t!r}")
            if t in arms:
                raise SystemExit(f"duplicate target {t} in --run-ids")
            arms[t] = row
        outputs.append(render_group(arms, args.format, args.allow_mixed_commit))
    elif args.latest:
        groups = latest_by_arm(rows)
        if not groups:
            raise SystemExit(
                "no phase-tagged runs found for --latest "
                "(re-run after the warmup-exclusion fix)"
            )
        for arms in groups:
            outputs.append(render_group(arms, args.format, args.allow_mixed_commit))
    else:
        ap.error("specify --latest or --run-ids")

    print("\n\n".join(outputs))


if __name__ == "__main__":
    main()

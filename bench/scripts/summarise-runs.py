#!/usr/bin/env python3
"""summarise-runs.py — emit comparison tables from archived immutable runs.

Standard library only. Never invents numbers: every figure comes from a file
under results/runs/ with a run_metadata provenance block.

Reads ep_*{phase:main} series only — unfiltered ep_* include warmup samples and
must not be cited. Runs that predate phase tagging are refused/skipped, not
silently re-parsed. Aborted runs (status=aborted) are skipped and counted.

Three-arm decomposition (when available):
  Δ proxy  = passthrough − direct   (Kong hop, no plugins)
  Δ policy = gateway − passthrough  (governance plugins)  ← paper claim
  Δ total  = gateway − direct

When repeats share a repeat_group_id, every latency and throughput figure is
reported as median [min–max] across those repeats.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
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

# Relative spread threshold: (max − min) / median > this → mark cell + WARNING.
SPREAD_WARN = 0.25

# Unicode en-dash for median [min–max] ranges.
EN_DASH = "\u2013"


def load_run(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "run_metadata" not in data:
        raise SystemExit(f"REFUSE: {path} has no run_metadata (pre-provenance file)")
    return data


def has_phase_main(run: dict) -> bool:
    metrics = run.get("metrics") or {}
    return all(key in metrics for key, _ in ENDPOINTS)


def missing_phase_main(run: dict) -> list[str]:
    metrics = run.get("metrics") or {}
    return [key for key, _ in ENDPOINTS if key not in metrics]


def require_phase_main(run: dict, path_hint: str) -> None:
    """Refuse runs that predate phase-tagged Trends (warmup was mixed in)."""
    missing = missing_phase_main(run)
    if missing:
        run_id = (run.get("run_metadata") or {}).get("run_id", path_hint)
        raise SystemExit(
            f"REFUSE: run {run_id} predates the phase-tagging fix "
            f"(missing {', '.join(missing)}). "
            f"Do not cite unfiltered ep_* series from this file; re-run after "
            f"the warmup-exclusion change."
        )


def resolve_status(data: dict) -> str | None:
    """Prefer top-level status; fall back to run_metadata.status.

    Missing status is treated as None (historical complete when phase:main
    is present). Explicit aborted is always aborted.
    """
    if "status" in data and data["status"] is not None:
        return str(data["status"])
    meta = data.get("run_metadata") or {}
    if "status" in meta and meta["status"] is not None:
        return str(meta["status"])
    return None


def is_aborted(data: dict) -> bool:
    return resolve_status(data) == "aborted"


def is_suspect(data: dict) -> bool:
    return resolve_status(data) == "suspect"


def is_usable(data: dict) -> bool:
    """Complete enough to cite: not aborted/suspect, and has phase:main series.

    Historical runs without a status field count as complete when they carry
    phase:main metrics. Aborted/suspect runs are never cited.
    """
    if is_aborted(data) or is_suspect(data):
        return False
    return has_phase_main(data)


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


def index_runs() -> tuple[list[dict], int, int]:
    """Load provenance-bearing runs.

    Returns (usable_rows, skipped_aborted, skipped_pre_phase).
    """
    rows: list[dict] = []
    skipped_aborted = 0
    skipped_pre_phase = 0
    for path in sorted(RUNS_DIR.glob("*.json")):
        if path.name == "index.jsonl":
            continue
        try:
            data = load_run(path)
        except SystemExit:
            continue
        if is_aborted(data) or is_suspect(data):
            skipped_aborted += 1
            continue
        if not has_phase_main(data):
            skipped_pre_phase += 1
            continue
        meta = data["run_metadata"]
        rows.append({"path": path, "data": data, "meta": meta})
    return rows, skipped_aborted, skipped_pre_phase


def group_key_for(meta: dict) -> str:
    """Repeat-group id when present; else each run is its own group of 1."""
    rgid = meta.get("repeat_group_id")
    if rgid is not None and str(rgid) != "":
        return str(rgid)
    return f"solo:{meta['run_id']}"


def build_repeat_groups(rows: list[dict]) -> dict[tuple, list[dict]]:
    """Map (target, vus, iterations, group_key) → list of run rows (repeats)."""
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        m = row["meta"]
        key = (m["target"], m["vus"], m["iterations"], group_key_for(m))
        groups.setdefault(key, []).append(row)
    for key in groups:
        groups[key].sort(key=lambda r: r["meta"]["timestamp_utc"])
    return groups


def group_timestamp(runs: list[dict]) -> str:
    return max(r["meta"]["timestamp_utc"] for r in runs)


def latest_arm_maps(
    rows: list[dict],
) -> list[dict[str, list[dict]]]:
    """Newest usable arm groups per VU.

    For each VU, prefer the iteration count whose newest-per-arm groups cover
    the most arms; break ties by newest group timestamp.
    Each arm maps to its list of repeat runs.
    """
    groups = build_repeat_groups(rows)

    # Newest group per (target, vus, iterations).
    newest: dict[tuple, list[dict]] = {}
    for (target, vus, iterations, _gk), runs in groups.items():
        arm_key = (target, vus, iterations)
        prev = newest.get(arm_key)
        if prev is None or group_timestamp(runs) > group_timestamp(prev):
            newest[arm_key] = runs

    vu_levels = sorted({vus for (_t, vus, _it) in newest})
    result: list[dict[str, list[dict]]] = []
    for vu in vu_levels:
        iter_counts = sorted(
            {it for (_t, v, it) in newest if v == vu},
            reverse=True,
        )
        best: dict[str, list[dict]] = {}
        best_n = -1
        best_ts = ""
        for it in iter_counts:
            arms = {
                t: newest[(t, vu, it)]
                for t in ARMS
                if (t, vu, it) in newest
            }
            n = len(arms)
            ts = max((group_timestamp(r) for r in arms.values()), default="")
            if n > best_n or (n == best_n and ts > best_ts):
                best = arms
                best_n = n
                best_ts = ts
        if best:
            result.append(best)
    return result


def median_min_max(values: list[float]) -> tuple[float, float, float] | None:
    if not values:
        return None
    return statistics.median(values), min(values), max(values)


def fmt_range(
    stats: tuple[float, float, float] | None,
    *,
    digits: int = 2,
    warn: bool = False,
) -> str:
    """Format as ``median [min–max]``; append `` !`` when spread is wide."""
    if stats is None:
        return "—"
    med, lo, hi = stats
    cell = f"{med:.{digits}f} [{lo:.{digits}f}{EN_DASH}{hi:.{digits}f}]"
    if warn:
        cell += " !"
    return cell


def spread_wide(stats: tuple[float, float, float] | None) -> bool:
    if stats is None:
        return False
    med, lo, hi = stats
    if med == 0:
        return (hi - lo) > 0
    return ((hi - lo) / abs(med)) > SPREAD_WARN


def fmt_delta(a: float | None, b: float | None, digits: int = 2) -> str:
    if a is None or b is None:
        return "—"
    return f"{(b - a):.{digits}f}"


def pct_delta(baseline: float | None, other: float | None) -> str:
    """Percent change of other relative to baseline (throughput cost style)."""
    if baseline is None or other is None or baseline == 0:
        return "—"
    return f"{((other - baseline) / baseline) * 100:.1f}%"


def arm_metric_values(
    runs: list[dict],
    metric_key: str,
    pct_key: str,
) -> list[float]:
    out: list[float] = []
    for row in runs:
        m = (row["data"].get("metrics") or {}).get(metric_key) or {}
        v = percentile(m, pct_key)
        if v is not None:
            out.append(v)
    return out


def arm_throughput_values(
    runs: list[dict],
    kind: str,
) -> list[float]:
    out: list[float] = []
    for row in runs:
        if kind == "measured":
            v = throughput_measured(row["data"])
        else:
            v = throughput_wall(row["data"])
        if v is not None:
            out.append(v)
    return out


def collect_commits(arms: dict[str, list[dict]]) -> set[str]:
    commits: set[str] = set()
    for runs in arms.values():
        for row in runs:
            commits.add(row["meta"]["git_commit"])
    return commits


def check_commits(arms: dict[str, list[dict]], allow_mixed: bool) -> str:
    commits = collect_commits(arms)
    if len(commits) <= 1:
        return ""
    detail_lines = []
    for t in ARMS:
        if t not in arms:
            continue
        for row in arms[t]:
            detail_lines.append(
                f"  {t}={row['meta']['run_id']} commit={row['meta']['git_commit']}"
            )
    detail = "\n".join(detail_lines)
    if not allow_mixed:
        raise SystemExit(
            f"REFUSE: comparing different git commits\n{detail}\n"
            f"Pass --allow-mixed-commit to override (not for paper figures)."
        )
    return (
        "WARNING: MIXED GIT COMMITS — deltas are not attributable to mediation alone.\n"
        f"{detail}\n\n"
    )


def sample_meta(arms: dict[str, list[dict]]) -> dict:
    first_arm = next(iter(arms.values()))
    return first_arm[0]["meta"]


def repeats_note(
    arms: dict[str, list[dict]],
    expected: int | None,
) -> list[str]:
    """Announce how many repeats were used, especially when below expected."""
    notes: list[str] = []
    counts = {t: len(arms[t]) for t in ARMS if t in arms}
    if not counts:
        return notes
    max_n = max(counts.values())
    # Infer expectation: explicit --repeats, else max observed if any group
    # carries repeat_group_id / repeat_index, else 1.
    has_repeat_meta = any(
        row["meta"].get("repeat_group_id") or row["meta"].get("repeat_index") is not None
        for runs in arms.values()
        for row in runs
    )
    exp = expected
    if exp is None and has_repeat_meta:
        exp = max_n
        # Prefer the highest repeat_index + 1 when available.
        idxs = [
            int(row["meta"]["repeat_index"])
            for runs in arms.values()
            for row in runs
            if row["meta"].get("repeat_index") is not None
        ]
        if idxs:
            exp = max(exp, max(idxs) + 1)
    if exp is None:
        exp = 1

    short = {t: n for t, n in counts.items() if n < exp}
    if max_n > 1 or has_repeat_meta or short:
        parts = [f"{ARM_LABEL[t]}={n}" for t, n in counts.items()]
        notes.append("repeats used: " + ", ".join(parts))
        if short and exp > 1:
            missing = ", ".join(
                f"{ARM_LABEL[t]} has {n} of {exp}" for t, n in short.items()
            )
            notes.append(f"NOTE: fewer than expected repeats ({missing}).")
    return notes


def render_group(
    arms: dict[str, list[dict]],
    fmt_kind: str,
    allow_mixed: bool,
    expected_repeats: int | None,
) -> str:
    for runs in arms.values():
        for row in runs:
            require_phase_main(row["data"], str(row["path"]))

    warn = check_commits(arms, allow_mixed)
    lines: list[str] = []
    spread_warnings: list[str] = []
    if warn:
        lines.append(warn.rstrip())

    meta0 = sample_meta(arms)
    vu = meta0["vus"]
    iterations = meta0["iterations"]
    title = f"VU={vu} iterations={iterations}"

    for note in repeats_note(arms, expected_repeats):
        lines.append(note)

    missing = [ARM_LABEL[t] for t in ARMS if t not in arms]
    if missing:
        lines.append(
            f"NOTE: missing arm(s): {', '.join(missing)}. "
            f"Showing available columns only."
        )

    for pct_key, pct_label in (("med", "p50"), ("p(95)", "p95"), ("p(99)", "p99")):
        headers = [
            "endpoint",
            "direct",
            "passthrough",
            "governed",
            "Δ proxy",
            "Δ policy",
            "Δ total",
        ]
        rows_out: list[list[str]] = []
        for metric_key, label in ENDPOINTS:
            stats: dict[str, tuple[float, float, float] | None] = {}
            for t in ARMS:
                if t not in arms:
                    stats[t] = None
                    continue
                vals = arm_metric_values(arms[t], metric_key, pct_key)
                stats[t] = median_min_max(vals)
                if spread_wide(stats[t]):
                    spread_warnings.append(
                        f"WARNING: wide spread VU={vu} {label} {pct_label} "
                        f"{ARM_LABEL[t]}: "
                        f"{fmt_range(stats[t], digits=2)}"
                    )
            d, p, g = stats.get("direct"), stats.get("passthrough"), stats.get("gateway")
            d_med = d[0] if d else None
            p_med = p[0] if p else None
            g_med = g[0] if g else None
            rows_out.append([
                label,
                fmt_range(d, digits=2, warn=spread_wide(d)),
                fmt_range(p, digits=2, warn=spread_wide(p)),
                fmt_range(g, digits=2, warn=spread_wide(g)),
                fmt_delta(d_med, p_med),  # proxy
                fmt_delta(p_med, g_med),  # policy
                fmt_delta(d_med, g_med),  # total
            ])

        if fmt_kind == "markdown":
            lines.append(f"### {title} — latency {pct_label} (ms)")
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for r in rows_out:
                lines.append("| " + " | ".join(r) + " |")
            lines.append("")
        else:
            lines.append(f"{title} latency {pct_label}")
            lines.append("\t".join(headers))
            for r in rows_out:
                lines.append("\t".join(r))
            lines.append("")

    # Throughput decomposition from medians across repeats.
    thr_stats: dict[str, tuple[float, float, float] | None] = {}
    wall_stats: dict[str, tuple[float, float, float] | None] = {}
    for t in ARMS:
        if t not in arms:
            thr_stats[t] = None
            wall_stats[t] = None
            continue
        thr_stats[t] = median_min_max(arm_throughput_values(arms[t], "measured"))
        wall_stats[t] = median_min_max(arm_throughput_values(arms[t], "wall"))
        if spread_wide(thr_stats[t]):
            spread_warnings.append(
                f"WARNING: wide spread VU={vu} throughput_measured "
                f"{ARM_LABEL[t]}: {fmt_range(thr_stats[t], digits=1)}"
            )

    d_t = thr_stats["direct"][0] if thr_stats["direct"] else None
    p_t = thr_stats["passthrough"][0] if thr_stats["passthrough"] else None
    g_t = thr_stats["gateway"][0] if thr_stats["gateway"] else None

    d_cell = fmt_range(thr_stats["direct"], digits=1, warn=spread_wide(thr_stats["direct"]))
    p_cell = fmt_range(
        thr_stats["passthrough"], digits=1, warn=spread_wide(thr_stats["passthrough"])
    )
    g_cell = fmt_range(
        thr_stats["gateway"], digits=1, warn=spread_wide(thr_stats["gateway"])
    )
    wall_d = fmt_range(wall_stats["direct"], digits=1)
    wall_p = fmt_range(wall_stats["passthrough"], digits=1)
    wall_g = fmt_range(wall_stats["gateway"], digits=1)

    if fmt_kind == "markdown":
        lines.append(
            f"**throughput_measured** (main phase, req/s): "
            f"direct {d_cell}, passthrough {p_cell}, governed {g_cell}.  "
            f"Δ proxy cost {pct_delta(d_t, p_t)}, "
            f"Δ policy cost {pct_delta(p_t, g_t)}, "
            f"Δ total cost {pct_delta(d_t, g_t)}."
        )
        lines.append(
            f"throughput_wall (reference only): "
            f"direct {wall_d}, passthrough {wall_p}, governed {wall_g}."
        )
        lines.append("")
        lines.append(
            "Δ proxy = passthrough − direct (Kong hop). "
            "Δ policy = governed − passthrough (jwt + rate-limit + otel). "
            "Δ total = governed − direct. "
            "Lead with Δ policy. "
            "Latency/throughput cells are median [min–max] across repeats."
        )
    else:
        lines.append(
            f"throughput_measured\tdirect={d_cell}\tpassthrough={p_cell}\t"
            f"governed={g_cell}\t"
            f"proxy_cost={pct_delta(d_t, p_t)}\tpolicy_cost={pct_delta(p_t, g_t)}\t"
            f"total_cost={pct_delta(d_t, g_t)}"
        )
        lines.append(
            f"throughput_wall\tdirect={wall_d}\t"
            f"passthrough={wall_p}\t"
            f"governed={wall_g}\t(reference only)"
        )

    # Provenance footer — every run_id that contributed.
    id_parts: list[str] = []
    for t in ARMS:
        if t not in arms:
            continue
        ids = ",".join(r["meta"]["run_id"] for r in arms[t])
        id_parts.append(f"{ARM_LABEL[t]}={ids}")
    commit = meta0["git_commit"][:12]
    host = meta0["host"].get("cpu_model", "?")
    os_name = meta0["host"].get("os", "?")
    lines.append("")
    lines.append(f"Provenance: {' '.join(id_parts)} git={commit} host={host} ({os_name})")
    notes = []
    for t in ARMS:
        if t not in arms:
            continue
        for row in arms[t]:
            n = row["meta"].get("notes")
            if n:
                notes.append(f"{ARM_LABEL[t]}/{row['meta']['run_id']}={n!r}")
    if notes:
        lines.append("Notes: " + " ".join(notes))

    # Spread warnings after the tables so cells marked " !" are visible first.
    if spread_warnings:
        # Dedupe while preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for w in spread_warnings:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        lines.append("")
        lines.extend(unique)

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--latest",
        action="store_true",
        help="newest arm groups per VU (direct / passthrough / gateway when present)",
    )
    ap.add_argument(
        "--run-ids",
        nargs="+",
        metavar="ID",
        help="compare 2 or 3 run ids (any of direct/passthrough/gateway)",
    )
    ap.add_argument(
        "--repeats",
        type=int,
        default=None,
        metavar="N",
        help="expected repeats per configuration; report when fewer were available",
    )
    ap.add_argument("--format", choices=["text", "markdown"], default="text")
    ap.add_argument("--allow-mixed-commit", action="store_true")
    args = ap.parse_args()

    if not RUNS_DIR.is_dir():
        raise SystemExit(f"no runs directory at {RUNS_DIR}")

    rows, skipped_aborted, skipped_pre_phase = index_runs()

    # Always report aborted skips (Part 5).
    print(f"Skipped {skipped_aborted} aborted/suspect run(s).", file=sys.stderr)
    if skipped_pre_phase:
        print(
            f"Skipped {skipped_pre_phase} pre-phase-tag run(s) "
            f"(missing ep_*{{phase:main}}).",
            file=sys.stderr,
        )

    if not rows and not args.run_ids:
        raise SystemExit(
            f"no usable (complete, phase-tagged) runs in {RUNS_DIR}"
        )

    outputs: list[str] = []
    if args.run_ids:
        if len(args.run_ids) < 2 or len(args.run_ids) > 3:
            raise SystemExit("--run-ids expects 2 or 3 run ids")
        # Explicit citation: scan all provenance files (including aborted /
        # pre-phase) so we can refuse clearly rather than say "not found".
        by_id: dict[str, dict] = {}
        for path in sorted(RUNS_DIR.glob("*.json")):
            if path.name == "index.jsonl":
                continue
            try:
                data = load_run(path)
            except SystemExit:
                continue
            meta = data["run_metadata"]
            by_id[meta["run_id"]] = {"path": path, "data": data, "meta": meta}

        arms: dict[str, list[dict]] = {}
        for rid in args.run_ids:
            if rid not in by_id:
                raise SystemExit(f"run id not found: {rid!r}")
            row = by_id[rid]
            if is_aborted(row["data"]):
                raise SystemExit(
                    f"REFUSE: run {rid} has status=aborted; "
                    f"not usable for comparison"
                )
            require_phase_main(row["data"], str(row["path"]))
            t = row["meta"]["target"]
            if t not in ARMS:
                raise SystemExit(f"run {rid} has unknown target {t!r}")
            if t in arms:
                raise SystemExit(f"duplicate target {t} in --run-ids")
            arms[t] = [row]
        outputs.append(
            render_group(arms, args.format, args.allow_mixed_commit, args.repeats)
        )
    elif args.latest:
        groups = latest_arm_maps(rows)
        if not groups:
            raise SystemExit(
                "no phase-tagged complete runs found for --latest "
                "(re-run after the warmup-exclusion fix)"
            )
        for arms in groups:
            outputs.append(
                render_group(
                    arms, args.format, args.allow_mixed_commit, args.repeats
                )
            )
    else:
        ap.error("specify --latest or --run-ids")

    print("\n\n".join(outputs))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""summarise-runs.py — emit comparison tables from archived immutable runs.

Standard library only. Never invents numbers: every figure comes from a file
under results/runs/ with a run_metadata provenance block.

Reads ep_*{phase:main} series only — unfiltered ep_* include warmup samples and
must not be cited. Runs that predate phase tagging are skipped, not silently
re-parsed. Aborted runs (status=aborted) are skipped and counted. Suspect runs
(status=suspect) are included and flagged — latency is usable; throughput is
always re-derived from iteration_duration{phase:main}.

Five-target decomposition (when available), per gateway:
  Δ proxy  = {gw}-passthrough − direct
  Δ policy = {gw}-governed − {gw}-passthrough   ← paper claim
  Δ total  = {gw}-governed − direct

Protocol overhead (when rest-python + mcp-direct available):
  Δ loadgen   = rest-python − direct          ← tooling artifact
  Δ protocol  = mcp-direct − rest-python      ← paper figure
  MCP policy  = mcp-governed − mcp-direct
  REST policy = kong-governed − kong-passthrough

Canonical targets: direct, kong-passthrough, kong-governed,
apisix-passthrough, apisix-governed, rest-python, mcp-direct, mcp-governed.
Legacy aliases when reading only:
passthrough → kong-passthrough, gateway → kong-governed.

When repeats share a configuration, every latency and throughput figure is
reported as median [min–max] across those repeats.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BENCH_DIR.parent
RUNS_DIR = BENCH_DIR / "results" / "runs"

# (filtered metric key in summary JSON, display label)
ENDPOINTS = [
    ("ep_list_tables{phase:main}", "list_tables"),
    ("ep_describe_schema{phase:main}", "describe_schema"),
    ("ep_get_rows{phase:main}", "get_rows"),
    ("ep_run_query{phase:main}", "run_query"),
]

ITER_DURATION_MAIN = "iteration_duration{phase:main}"

# Canonical targets written by new runs.
ARMS = (
    "direct",
    "kong-passthrough",
    "kong-governed",
    "apisix-passthrough",
    "apisix-governed",
)

# MCP protocol arms (reported in a separate Protocol overhead section).
MCP_ARMS = (
    "mcp-direct",
    "mcp-governed",
)

# Python REST control arm (httpx) — isolates loadgen cost from MCP protocol.
CONTROL_ARMS = (
    "rest-python",
)

# All arms the summariser may place in a cohort / provenance footer.
ALL_ARMS = ARMS + CONTROL_ARMS + MCP_ARMS

# Legacy aliases when reading archived runs (do NOT rewrite files).
LEGACY_TARGET_ALIASES = {
    "passthrough": "kong-passthrough",
    "gateway": "kong-governed",
}

ARM_LABEL = {
    "direct": "direct",
    "kong-passthrough": "kong-passthrough",
    "kong-governed": "kong-governed",
    "apisix-passthrough": "apisix-passthrough",
    "apisix-governed": "apisix-governed",
    "rest-python": "rest-python",
    "mcp-direct": "mcp-direct",
    "mcp-governed": "mcp-governed",
}

# Cross-loadgen pairs that are intentionally comparable (tooling / protocol).
# Same-loadgen pairs are always allowed. Everything else is refused.
ALLOWED_CROSS_LOADGEN_PAIRS = frozenset({
    frozenset({"direct", "rest-python"}),       # load generator cost
    frozenset({"rest-python", "mcp-direct"}),   # protocol overhead
    frozenset({"mcp-direct", "mcp-governed"}),  # MCP policy (both python)
})

# (short_id, display_name, passthrough_arm, governed_arm)
GATEWAYS = (
    ("kong", "Kong", "kong-passthrough", "kong-governed"),
    ("apisix", "APISIX", "apisix-passthrough", "apisix-governed"),
)

# Relative spread threshold: (max − min) / median > this → mark cell + WARNING.
SPREAD_WARN = 0.25

# Max gap between consecutive non-aborted runs in one repeat cluster.
# Main-phase repeats finish well under this; a larger gap is a new attempt
# (e.g. resume after an aborted mid-group run), not another repeat.
REPEAT_GAP_SECONDS = 10 * 60

# Unicode en-dash for median [min–max] ranges.
EN_DASH = "\u2013"


def canonicalize_target(target: str) -> str:
    """Map legacy aliases to canonical names; leave unknowns unchanged."""
    return LEGACY_TARGET_ALIASES.get(target, target)


def run_protocol(run: dict) -> str:
    """rest|mcp — legacy runs without protocol field are treated as rest."""
    meta = run.get("run_metadata") or {}
    p = meta.get("protocol")
    if p in ("rest", "mcp"):
        return str(p)
    target = canonicalize_target(str(meta.get("target") or ""))
    if target in MCP_ARMS:
        return "mcp"
    return "rest"


def run_loadgen(run: dict) -> str:
    """k6|python — normalize legacy values; infer when missing.

    Legacy runs without ``loadgen``: treat as ``k6`` when protocol is rest
    (or missing) and target is not mcp-*; otherwise ``python``. Values like
    ``python-fastmcp-client`` normalize to ``python``.
    """
    meta = run.get("run_metadata") or {}
    raw = meta.get("loadgen")
    if isinstance(raw, str) and raw:
        low = raw.lower()
        if low in ("k6", "k6-streamable-http"):
            return "k6"
        if low.startswith("python"):
            return "python"
        return low
    target = canonicalize_target(str(meta.get("target") or ""))
    if target in MCP_ARMS or target in CONTROL_ARMS:
        return "python"
    proto = meta.get("protocol")
    if proto == "mcp":
        return "python"
    return "k6"


def loadgen_delta_allowed(a: str, b: str, arms: dict[str, list[dict]]) -> bool:
    """True when a latency delta between arms a and b is safe to compute.

    Same loadgen → always ok. Cross-loadgen only for explicitly allowed pairs
    (direct↔rest-python loadgen cost; rest-python↔mcp-direct protocol; etc.).
    """
    if a not in arms or b not in arms:
        return True  # missing arm → fmt_delta returns —
    lg_a = run_loadgen(arms[a][0]["data"])
    lg_b = run_loadgen(arms[b][0]["data"])
    if lg_a == lg_b:
        return True
    pair = frozenset({a, b})
    return pair in ALLOWED_CROSS_LOADGEN_PAIRS


def safe_fmt_delta(
    a_target: str,
    b_target: str,
    a_med: float | None,
    b_med: float | None,
    arms: dict[str, list[dict]],
    *,
    digits: int = 2,
) -> str:
    """fmt_delta with cross-loadgen guard; returns — when refused."""
    if not loadgen_delta_allowed(a_target, b_target, arms):
        return "—"
    return fmt_delta(a_med, b_med, digits=digits)


def load_run(path: Path) -> dict | None:
    """Load a run JSON. Return None when provenance is incomplete.

    Normalizes ``run_metadata.target`` in memory (legacy aliases → canonical).
    Original string is kept as ``run_metadata.target_raw`` for provenance.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Skipped {path.name}: unreadable ({exc})", file=sys.stderr)
        return None
    meta = data.get("run_metadata")
    if not isinstance(meta, dict):
        print(f"Skipped {path.name}: no run_metadata", file=sys.stderr)
        return None
    if not meta.get("target"):
        print(f"Skipped {path.name}: missing run_metadata.target", file=sys.stderr)
        return None
    if not meta.get("run_id"):
        print(f"Skipped {path.name}: missing run_metadata.run_id", file=sys.stderr)
        return None
    raw = str(meta["target"])
    meta["target_raw"] = raw
    meta["target"] = canonicalize_target(raw)
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
    """Complete enough to cite: not aborted, and has phase:main series.

    Suspect runs are included (latency sound; throughput re-derived).
    Historical runs without a status field count as complete when they carry
    phase:main metrics.
    """
    if is_aborted(data):
        return False
    return has_phase_main(data)


def percentile(metric: dict, key: str) -> float | None:
    vals = metric.get("values") or {}
    if key not in vals:
        return None
    return float(vals[key])


def requests_per_iteration(run: dict) -> int:
    meta = run.get("run_metadata") or {}
    if meta.get("requests_per_iteration") is not None:
        return int(meta["requests_per_iteration"])
    return len(ENDPOINTS)


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


def throughput_derived(run: dict) -> float | None:
    """Main-phase throughput from iteration_duration — never trust stored value.

    duration_s = iteration_duration{phase:main}.avg_ms × iterations / vus / 1000
    throughput = iterations × requests_per_iteration / duration_s
    """
    meta = run.get("run_metadata") or {}
    metrics = run.get("metrics") or {}
    it_metric = metrics.get(ITER_DURATION_MAIN) or {}
    vals = it_metric.get("values") or {}
    avg_ms = vals.get("avg")
    iterations = meta.get("iterations")
    vus = meta.get("vus")
    if avg_ms is None or iterations is None or vus is None:
        return None
    try:
        avg_ms_f = float(avg_ms)
        iterations_f = float(iterations)
        vus_f = float(vus)
    except (TypeError, ValueError):
        return None
    if vus_f <= 0 or iterations_f <= 0:
        return None
    duration_s = avg_ms_f * iterations_f / vus_f / 1000.0
    if duration_s <= 0:
        return None
    rpi = requests_per_iteration(run)
    return iterations_f * float(rpi) / duration_s


# Back-compat alias for callers / generate-results-doc.
throughput_measured = throughput_derived


def run_id_matches_since(run_id: str, since: str) -> bool:
    """True when run_id's leading timestamp is at or after the since prefix."""
    ts = run_id.split("-", 1)[0]
    if not ts or not ts[0].isdigit():
        return False
    if ts.startswith(since):
        return True
    return ts >= since


def index_runs(
    since: str | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Load usable provenance-bearing runs.

    Returns (usable_rows, skip_counts).
    """
    rows: list[dict] = []
    skips = {
        "aborted": 0,
        "pre_phase": 0,
        "incomplete_meta": 0,
        "since_filtered": 0,
    }
    for path in sorted(RUNS_DIR.glob("*.json")):
        if path.name == "index.jsonl":
            continue
        data = load_run(path)
        if data is None:
            skips["incomplete_meta"] += 1
            continue
        meta = data["run_metadata"]
        if since and not run_id_matches_since(str(meta["run_id"]), since):
            skips["since_filtered"] += 1
            continue
        if is_aborted(data):
            skips["aborted"] += 1
            continue
        if not has_phase_main(data):
            skips["pre_phase"] += 1
            continue
        rows.append({"path": path, "data": data, "meta": meta})
    return rows, skips


def run_sort_key(meta: dict) -> str:
    """Chronological key from timestamp_utc or run_id prefix."""
    return str(meta.get("timestamp_utc") or meta["run_id"])


def run_epoch_seconds(meta: dict) -> float:
    """Parse run time as epoch seconds for proximity clustering."""
    ts = meta.get("timestamp_utc")
    if isinstance(ts, str) and ts:
        # Accept both …Z and +00:00
        text = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            pass
    rid = str(meta.get("run_id") or "")
    # run_id starts with YYYYMMDDTHHMMSSZ
    prefix = rid.split("-", 1)[0]
    try:
        dt = datetime.strptime(prefix, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
        return dt.timestamp()
    except ValueError:
        return 0.0

def cluster_by_proximity(rows: list[dict]) -> list[list[dict]]:
    """Split runs into proximity clusters.

    Rows are assumed non-aborted (callers filter aborted first). Consecutive
    runs within REPEAT_GAP_SECONDS belong to the same attempt; a larger gap
    starts a new cluster. Aborted runs are never present here, so they cannot
    split a cluster — only wall-clock gaps between complete/suspect runs do.
    """
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: (run_epoch_seconds(r["meta"]), run_sort_key(r["meta"])))
    clusters: list[list[dict]] = [[ordered[0]]]
    for row in ordered[1:]:
        prev = clusters[-1][-1]
        gap = run_epoch_seconds(row["meta"]) - run_epoch_seconds(prev["meta"])
        if gap <= REPEAT_GAP_SECONDS:
            clusters[-1].append(row)
        else:
            clusters.append([row])
    return clusters


def select_repeat_cluster(
    rows: list[dict],
    *,
    expected_repeats: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Newest proximity cluster for one (target, vus, iterations).

    When the newest cluster has more than ``expected_repeats`` runs, keep the
    newest N and report discards. Older clusters are discarded entirely.
    Returns (selected_rows, discard_notes).
    """
    notes: list[str] = []
    clusters = cluster_by_proximity(rows)
    if not clusters:
        return [], notes

    def cluster_end(c: list[dict]) -> float:
        return max(run_epoch_seconds(r["meta"]) for r in c)

    newest = max(clusters, key=cluster_end)
    for c in clusters:
        if c is newest:
            continue
        ids = ", ".join(r["meta"]["run_id"] for r in c)
        notes.append(
            f"discarded older proximity cluster ({len(c)} run(s)): {ids}"
        )

    selected = newest
    if expected_repeats is not None and expected_repeats > 0 and len(selected) > expected_repeats:
        # Keep the newest N within the cluster.
        ordered = sorted(
            selected,
            key=lambda r: (run_epoch_seconds(r["meta"]), run_sort_key(r["meta"])),
        )
        dropped = ordered[:-expected_repeats]
        selected = ordered[-expected_repeats:]
        ids = ", ".join(r["meta"]["run_id"] for r in dropped)
        notes.append(
            f"discarded {len(dropped)} excess run(s) above --repeats="
            f"{expected_repeats}: {ids}"
        )
    return selected, notes


def group_timestamp(runs: list[dict]) -> str:
    return max(run_sort_key(r["meta"]) for r in runs)


def warn_repeat_count_mismatch(
    arms: dict[str, list[dict]],
    expected: int | None,
) -> None:
    """Assert each arm's run count matches the expected repeat count."""
    if expected is None or expected <= 0:
        return
    meta0 = next(iter(arms.values()))[0]["meta"]
    vu = meta0["vus"]
    iterations = meta0["iterations"]
    for t in ALL_ARMS:
        if t not in arms:
            continue
        n = len(arms[t])
        if n != expected:
            print(
                f"WARNING: repeat-count mismatch "
                f"target={t} VU={vu} iterations={iterations}: "
                f"have {n}, expected {expected}",
                file=sys.stderr,
            )


def configs_from_rows(
    rows: list[dict],
    *,
    expected_repeats: int | None = None,
) -> dict[tuple, list[dict]]:
    """Map (target, vus, iterations) → selected proximity cluster."""
    by_cfg: dict[tuple, list[dict]] = {}
    for row in rows:
        m = row["meta"]
        key = (m["target"], int(m["vus"]), int(m["iterations"]))
        by_cfg.setdefault(key, []).append(row)

    selected: dict[tuple, list[dict]] = {}
    for key, cfg_rows in by_cfg.items():
        chosen, notes = select_repeat_cluster(
            cfg_rows, expected_repeats=expected_repeats
        )
        for note in notes:
            target, vus, iterations = key
            print(
                f"NOTE: {target} VU={vus} iter={iterations}: {note}",
                file=sys.stderr,
            )
        if chosen:
            selected[key] = chosen
            if expected_repeats is not None and len(chosen) != expected_repeats:
                print(
                    f"WARNING: repeat-count mismatch "
                    f"target={key[0]} VU={key[1]} iterations={key[2]}: "
                    f"have {len(chosen)}, expected {expected_repeats}",
                    file=sys.stderr,
                )
    return selected


def commit_of_runs(runs: list[dict]) -> str:
    return str(runs[0]["meta"].get("git_commit") or "")


def same_commit_cohort(
    arms: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """Keep only arms from the newest git_commit in this (vu, iter) set.

    MCP arms and rest-python are often added in a later harness commit than
    archived REST sweeps at the same VU/iterations. Mixing them trips
    check_commits when bench/ code differs. Prefer the newest commit's cohort
    so --latest can render protocol overhead without --allow-mixed-commit once
    matching ``rest-python`` + ``mcp-direct`` (and ideally ``direct``) runs
    exist on that commit.
    """
    if not arms:
        return arms
    by_commit: dict[str, dict[str, list[dict]]] = {}
    for t, runs in arms.items():
        by_commit.setdefault(commit_of_runs(runs), {})[t] = runs
    if len(by_commit) == 1:
        return arms

    def cohort_ts(cohort: dict[str, list[dict]]) -> str:
        return max((group_timestamp(r) for r in cohort.values()), default="")

    best_commit = max(by_commit.keys(), key=lambda c: cohort_ts(by_commit[c]))
    kept = by_commit[best_commit]
    dropped = sorted(set(arms) - set(kept))
    if dropped:
        print(
            "NOTE: --latest dropped arms from older git commits at this "
            f"VU/iterations (kept commit={best_commit[:12]}): "
            + ", ".join(dropped),
            file=sys.stderr,
        )
    return kept


def latest_arm_maps(
    rows: list[dict],
    *,
    expected_repeats: int | None = None,
) -> list[dict[str, list[dict]]]:
    """Newest usable arm groups per VU (proximity-clustered repeats)."""
    newest = configs_from_rows(rows, expected_repeats=expected_repeats)

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
        best_has_protocol = False
        for it in iter_counts:
            arms = same_commit_cohort(
                {
                    t: newest[(t, vu, it)]
                    for t in ALL_ARMS
                    if (t, vu, it) in newest
                }
            )
            n = len(arms)
            ts = max((group_timestamp(r) for r in arms.values()), default="")
            # Prefer cohorts that can decompose protocol overhead
            # (rest-python + mcp-direct); bonus if direct is present for
            # loadgen-cost column.
            has_protocol = "mcp-direct" in arms and "rest-python" in arms
            has_loadgen_cost = has_protocol and "direct" in arms
            # Prefer a cohort that can report protocol overhead; then richer
            # arm sets; then newer timestamps.
            better = False
            if has_protocol and not best_has_protocol:
                better = True
            elif has_protocol == best_has_protocol:
                if has_loadgen_cost and "direct" not in best:
                    better = True
                elif n > best_n or (n == best_n and ts > best_ts):
                    better = True
            if better:
                best = arms
                best_n = n
                best_ts = ts
                best_has_protocol = has_protocol
        if best:
            warn_repeat_count_mismatch(best, expected_repeats)
            result.append(best)
    return result


def since_arm_maps(
    rows: list[dict],
    *,
    expected_repeats: int | None = None,
) -> list[dict[str, list[dict]]]:
    """Per-VU arm tables for a --since sweep (proximity-clustered repeats)."""
    newest = configs_from_rows(rows, expected_repeats=expected_repeats)
    by_vu_iter: dict[tuple, dict[str, list[dict]]] = {}
    for (target, vus, iterations), runs in newest.items():
        by_vu_iter.setdefault((vus, iterations), {})[target] = runs
    result: list[dict[str, list[dict]]] = []
    for cfg in sorted(by_vu_iter.keys()):
        arms = by_vu_iter[cfg]
        warn_repeat_count_mismatch(arms, expected_repeats)
        result.append(arms)
    return result


def flatten_arm_maps(groups: list[dict[str, list[dict]]]) -> list[dict]:
    """Unique rows from selected arm maps, stable by run_id."""
    out: list[dict] = []
    seen: set[str] = set()
    for arms in groups:
        for runs in arms.values():
            for row in runs:
                rid = row["meta"]["run_id"]
                if rid not in seen:
                    seen.add(rid)
                    out.append(row)
    return out


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
            v = throughput_derived(row["data"])
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


def bench_code_differs(commits: set[str]) -> list[str]:
    """Return bench paths outside results/ that differ between commits."""
    ordered = sorted(commits)
    if len(ordered) < 2:
        return []
    differing: list[str] = []
    base = ordered[0]
    for other in ordered[1:]:
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", base, other],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            # If git is unavailable, fall back to refusing mixed commits.
            return ["<git unavailable>"]
        if proc.returncode not in (0, 1):
            return [f"<git diff failed: {proc.stderr.strip() or proc.returncode}>"]
        for line in proc.stdout.splitlines():
            path = line.strip()
            if not path:
                continue
            if path.startswith("bench/") and not path.startswith("bench/results/"):
                if path not in differing:
                    differing.append(path)
    return differing


def check_commits(arms: dict[str, list[dict]], allow_mixed: bool) -> str:
    commits = collect_commits(arms)
    if len(commits) <= 1:
        return ""
    detail_lines = []
    for t in ALL_ARMS:
        if t not in arms:
            continue
        for row in arms[t]:
            detail_lines.append(
                f"  {t}={row['meta']['run_id']} commit={row['meta']['git_commit']}"
            )
    detail = "\n".join(detail_lines)
    code_diffs = bench_code_differs(commits)
    if not code_diffs:
        return (
            "NOTE: mixed git commits, but bench/ code outside results/ is "
            "identical — comparison allowed.\n"
            f"{detail}\n\n"
        )
    if not allow_mixed:
        raise SystemExit(
            f"REFUSE: comparing different git commits that change bench code "
            f"outside results/\n"
            f"  differing: {', '.join(code_diffs)}\n{detail}\n"
            f"Pass --allow-mixed-commit to override (not for paper figures)."
        )
    return (
        "WARNING: MIXED GIT COMMITS — deltas are not attributable to mediation alone.\n"
        f"  differing bench code: {', '.join(code_diffs)}\n{detail}\n\n"
    )


def sample_meta(arms: dict[str, list[dict]]) -> dict:
    first_arm = next(iter(arms.values()))
    return first_arm[0]["meta"]


def count_suspect(arms: dict[str, list[dict]]) -> int:
    n = 0
    for runs in arms.values():
        for row in runs:
            if is_suspect(row["data"]):
                n += 1
    return n


def gateway_present(arms: dict[str, list[dict]], pt: str, gov: str) -> bool:
    """True when at least one of this gateway's arms is in the group."""
    return pt in arms or gov in arms


def arm_stats(
    arms: dict[str, list[dict]],
    target: str,
    metric_key: str,
    pct_key: str,
) -> tuple[float, float, float] | None:
    if target not in arms:
        return None
    return median_min_max(arm_metric_values(arms[target], metric_key, pct_key))


def med_of(stats: tuple[float, float, float] | None) -> float | None:
    return stats[0] if stats else None


def repeats_note(
    arms: dict[str, list[dict]],
    expected: int | None,
) -> list[str]:
    """Announce how many repeats were used, especially when below expected."""
    notes: list[str] = []
    counts = {t: len(arms[t]) for t in ALL_ARMS if t in arms}
    if not counts:
        return notes
    max_n = max(counts.values())
    has_repeat_meta = any(
        row["meta"].get("repeat_group_id") or row["meta"].get("repeat_index") is not None
        for runs in arms.values()
        for row in runs
    )
    exp = expected
    if exp is None and has_repeat_meta:
        exp = max_n
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
        # Only warn on shortfall when the caller set --repeats explicitly;
        # otherwise a longer arm (e.g. pooled multi-group sweep) would
        # falsely flag the others as missing.
        if short and expected is not None and expected > 1:
            missing = ", ".join(
                f"{ARM_LABEL[t]} has {n} of {expected}" for t, n in short.items()
            )
            notes.append(f"NOTE: fewer than expected repeats ({missing}).")

    n_suspect = count_suspect(arms)
    if n_suspect:
        suspect_ids = [
            row["meta"]["run_id"]
            for t in ALL_ARMS
            if t in arms
            for row in arms[t]
            if is_suspect(row["data"])
        ]
        notes.append(
            f"FLAGGED: {n_suspect} status=suspect run(s) included "
            f"(latency usable; throughput re-derived): "
            + ", ".join(suspect_ids)
        )
    return notes


def _emit_table(
    lines: list[str],
    fmt_kind: str,
    title: str,
    headers: list[str],
    rows_out: list[list[str]],
) -> None:
    if fmt_kind == "markdown":
        lines.append(f"### {title}")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for r in rows_out:
            lines.append("| " + " | ".join(r) + " |")
        lines.append("")
    else:
        lines.append(title)
        lines.append("\t".join(headers))
        for r in rows_out:
            lines.append("\t".join(r))
        lines.append("")


def render_protocol_overhead(
    arms: dict[str, list[dict]],
    pct_key: str,
    pct_label: str,
    title_prefix: str,
    fmt_kind: str,
    spread_warnings: list[str],
) -> list[str]:
    """Protocol overhead decomposition with rest-python control arm.

    Load generator cost: rest-python − direct   (tooling artifact; k6 vs python)
    Protocol overhead:    mcp-direct − rest-python  ← paper figure
    MCP policy cost:      mcp-governed − mcp-direct
    REST policy cost:     kong-governed − kong-passthrough
    """
    if "mcp-direct" not in arms or "rest-python" not in arms:
        return []

    vu = sample_meta(arms)["vus"]
    headers = [
        "endpoint",
        "direct",
        "rest-python",
        "mcp-direct",
        "Δ loadgen",
        "Δ protocol",
        "REST Δ policy",
        "MCP Δ policy",
    ]
    rows_out: list[list[str]] = []
    for metric_key, label in ENDPOINTS:
        d = arm_stats(arms, "direct", metric_key, pct_key)
        rp = arm_stats(arms, "rest-python", metric_key, pct_key)
        md = arm_stats(arms, "mcp-direct", metric_key, pct_key)
        kpt = arm_stats(arms, "kong-passthrough", metric_key, pct_key)
        kg = arm_stats(arms, "kong-governed", metric_key, pct_key)
        mg = arm_stats(arms, "mcp-governed", metric_key, pct_key)

        for t, stats in (
            ("direct", d),
            ("rest-python", rp),
            ("mcp-direct", md),
            ("kong-passthrough", kpt),
            ("kong-governed", kg),
            ("mcp-governed", mg),
        ):
            if t in arms and spread_wide(stats):
                spread_warnings.append(
                    f"WARNING: wide spread VU={vu} {label} {pct_label} "
                    f"{ARM_LABEL.get(t, t)}: {fmt_range(stats, digits=2)}"
                )

        rows_out.append([
            label,
            fmt_range(d, digits=2, warn=spread_wide(d)),
            fmt_range(rp, digits=2, warn=spread_wide(rp)),
            fmt_range(md, digits=2, warn=spread_wide(md)),
            # tooling artifact: k6 direct vs python httpx rest-python
            safe_fmt_delta(
                "direct", "rest-python", med_of(d), med_of(rp), arms
            ),
            # paper figure: same python loadgen, MCP vs REST
            safe_fmt_delta(
                "rest-python", "mcp-direct", med_of(rp), med_of(md), arms
            ),
            safe_fmt_delta(
                "kong-passthrough", "kong-governed",
                med_of(kpt), med_of(kg), arms,
            ),
            safe_fmt_delta(
                "mcp-direct", "mcp-governed",
                med_of(md), med_of(mg), arms,
            ),
        ])

    lines: list[str] = []
    _emit_table(
        lines,
        fmt_kind,
        f"{title_prefix} — Protocol overhead {pct_label} (ms)",
        headers,
        rows_out,
    )
    if fmt_kind == "markdown":
        lines.append(
            "Δ loadgen = rest-python − direct (tooling artifact: python/httpx "
            "vs k6). "
            "**Δ protocol = mcp-direct − rest-python** (paper figure; same "
            "python loadgen). "
            "REST Δ policy = kong-governed − kong-passthrough. "
            "MCP Δ policy = mcp-governed − mcp-direct."
        )
        lines.append("")
    else:
        lines.append(
            "Δ loadgen = rest-python − direct (tooling artifact); "
            "Δ protocol = mcp-direct − rest-python (paper figure); "
            "REST Δ policy = kong-governed − kong-passthrough; "
            "MCP Δ policy = mcp-governed − mcp-direct"
        )
        lines.append("")
    return lines


def render_gateway_latency(
    arms: dict[str, list[dict]],
    gw_label: str,
    pt: str,
    gov: str,
    pct_key: str,
    pct_label: str,
    title_prefix: str,
    fmt_kind: str,
    spread_warnings: list[str],
) -> list[str]:
    """One gateway decomposition table for one percentile."""
    vu = sample_meta(arms)["vus"]
    headers = [
        "endpoint",
        "direct",
        pt,
        gov,
        "Δ proxy",
        "Δ policy",
        "Δ total",
    ]
    rows_out: list[list[str]] = []
    for metric_key, label in ENDPOINTS:
        d = arm_stats(arms, "direct", metric_key, pct_key)
        p = arm_stats(arms, pt, metric_key, pct_key)
        g = arm_stats(arms, gov, metric_key, pct_key)
        for t, stats in (("direct", d), (pt, p), (gov, g)):
            if spread_wide(stats):
                spread_warnings.append(
                    f"WARNING: wide spread VU={vu} {label} {pct_label} "
                    f"{ARM_LABEL.get(t, t)}: "
                    f"{fmt_range(stats, digits=2)}"
                )
        rows_out.append([
            label,
            fmt_range(d, digits=2, warn=spread_wide(d)),
            fmt_range(p, digits=2, warn=spread_wide(p)),
            fmt_range(g, digits=2, warn=spread_wide(g)),
            fmt_delta(med_of(d), med_of(p)),
            fmt_delta(med_of(p), med_of(g)),
            fmt_delta(med_of(d), med_of(g)),
        ])

    lines: list[str] = []
    _emit_table(
        lines,
        fmt_kind,
        f"{title_prefix} — {gw_label} latency {pct_label} (ms)",
        headers,
        rows_out,
    )
    return lines


def render_cross_gateway_latency(
    arms: dict[str, list[dict]],
    pct_key: str,
    pct_label: str,
    title_prefix: str,
    fmt_kind: str,
) -> list[str]:
    """Cross-gateway Δ policy comparison at one percentile."""
    headers = ["endpoint", "Kong Δ policy", "APISIX Δ policy"]
    rows_out: list[list[str]] = []
    for metric_key, label in ENDPOINTS:
        cells = [label]
        for _gid, _glabel, pt, gov in GATEWAYS:
            p = arm_stats(arms, pt, metric_key, pct_key)
            g = arm_stats(arms, gov, metric_key, pct_key)
            cells.append(fmt_delta(med_of(p), med_of(g)))
        rows_out.append(cells)

    lines: list[str] = []
    _emit_table(
        lines,
        fmt_kind,
        f"{title_prefix} — Cross-gateway Δ policy {pct_label} (ms)",
        headers,
        rows_out,
    )
    return lines


def render_gateway_throughput(
    arms: dict[str, list[dict]],
    gw_label: str,
    pt: str,
    gov: str,
    fmt_kind: str,
    spread_warnings: list[str],
) -> list[str]:
    """Throughput decomposition for one gateway (shared direct)."""
    vu = sample_meta(arms)["vus"]
    thr_stats: dict[str, tuple[float, float, float] | None] = {}
    wall_stats: dict[str, tuple[float, float, float] | None] = {}
    for t in ("direct", pt, gov):
        if t not in arms:
            thr_stats[t] = None
            wall_stats[t] = None
            continue
        thr_stats[t] = median_min_max(arm_throughput_values(arms[t], "measured"))
        wall_stats[t] = median_min_max(arm_throughput_values(arms[t], "wall"))
        if spread_wide(thr_stats[t]):
            spread_warnings.append(
                f"WARNING: wide spread VU={vu} throughput_derived "
                f"{ARM_LABEL.get(t, t)}: {fmt_range(thr_stats[t], digits=1)}"
            )

    d_t = med_of(thr_stats["direct"])
    p_t = med_of(thr_stats[pt])
    g_t = med_of(thr_stats[gov])

    d_cell = fmt_range(thr_stats["direct"], digits=1, warn=spread_wide(thr_stats["direct"]))
    p_cell = fmt_range(thr_stats[pt], digits=1, warn=spread_wide(thr_stats[pt]))
    g_cell = fmt_range(thr_stats[gov], digits=1, warn=spread_wide(thr_stats[gov]))
    wall_d = fmt_range(wall_stats["direct"], digits=1)
    wall_p = fmt_range(wall_stats[pt], digits=1)
    wall_g = fmt_range(wall_stats[gov], digits=1)

    lines: list[str] = []
    if fmt_kind == "markdown":
        lines.append(
            f"**{gw_label} throughput_derived** (from iteration_duration{{phase:main}}, req/s): "
            f"direct {d_cell}, {pt} {p_cell}, {gov} {g_cell}.  "
            f"Δ proxy cost {pct_delta(d_t, p_t)}, "
            f"Δ policy cost {pct_delta(p_t, g_t)}, "
            f"Δ total cost {pct_delta(d_t, g_t)}."
        )
        lines.append(
            f"{gw_label} throughput_wall (reference only): "
            f"direct {wall_d}, {pt} {wall_p}, {gov} {wall_g}."
        )
    else:
        lines.append(
            f"{gw_label}_throughput_derived\tdirect={d_cell}\t{pt}={p_cell}\t"
            f"{gov}={g_cell}\t"
            f"proxy_cost={pct_delta(d_t, p_t)}\tpolicy_cost={pct_delta(p_t, g_t)}\t"
            f"total_cost={pct_delta(d_t, g_t)}"
        )
        lines.append(
            f"{gw_label}_throughput_wall\tdirect={wall_d}\t"
            f"{pt}={wall_p}\t"
            f"{gov}={wall_g}\t(reference only)"
        )
    return lines


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
    control_present = [ARM_LABEL[t] for t in CONTROL_ARMS if t in arms]
    if control_present:
        lines.append(f"Control arms present: {', '.join(control_present)}.")
    mcp_present = [ARM_LABEL[t] for t in MCP_ARMS if t in arms]
    if mcp_present:
        lines.append(f"MCP arms present: {', '.join(mcp_present)}.")

    active_gateways = [
        gw for gw in GATEWAYS if gateway_present(arms, gw[2], gw[3])
    ]
    has_protocol = "mcp-direct" in arms and "rest-python" in arms
    if not active_gateways and not has_protocol:
        lines.append(
            "NOTE: no gateway passthrough/governed arms present; "
            "nothing to decompose."
        )
        return "\n".join(lines)

    # Layout: Kong tables (all pct) → APISIX tables → cross-gateway Δ policy.
    for _gid, gw_label, pt, gov in active_gateways:
        for pct_key, pct_label in (("med", "p50"), ("p(95)", "p95"), ("p(99)", "p99")):
            lines.extend(
                render_gateway_latency(
                    arms,
                    gw_label,
                    pt,
                    gov,
                    pct_key,
                    pct_label,
                    title,
                    fmt_kind,
                    spread_warnings,
                )
            )

    if active_gateways:
        for pct_key, pct_label in (("med", "p50"), ("p(95)", "p95"), ("p(99)", "p99")):
            lines.extend(
                render_cross_gateway_latency(
                    arms, pct_key, pct_label, title, fmt_kind
                )
            )

    for _gid, gw_label, pt, gov in active_gateways:
        lines.extend(
            render_gateway_throughput(
                arms, gw_label, pt, gov, fmt_kind, spread_warnings
            )
        )
        if fmt_kind == "markdown":
            lines.append("")

    # Protocol overhead — needs rest-python + mcp-direct.
    if has_protocol:
        for pct_key, pct_label in (("med", "p50"), ("p(95)", "p95"), ("p(99)", "p99")):
            lines.extend(
                render_protocol_overhead(
                    arms,
                    pct_key,
                    pct_label,
                    title,
                    fmt_kind,
                    spread_warnings,
                )
            )

    if fmt_kind == "markdown":
        lines.append(
            "Δ proxy = {gw}-passthrough − direct. "
            "Δ policy = {gw}-governed − {gw}-passthrough (jwt + rate-limit + otel). "
            "Δ total = {gw}-governed − direct. "
            "Shared `direct` baseline for both gateways. "
            "Lead with Δ policy; cross-gateway table compares Kong vs APISIX Δ policy. "
            "Protocol overhead table (when rest-python + mcp-direct present): "
            "Δ loadgen = rest-python − direct (tooling artifact); "
            "Δ protocol = mcp-direct − rest-python (paper figure); "
            "REST vs MCP Δ policy. "
            "Latency/throughput cells are median [min–max] across repeats. "
            "Suspect runs are flagged above; their stored throughput_measured "
            "is ignored in favour of the derived figure. "
            "Legacy runs without run_metadata.protocol are treated as rest; "
            "without loadgen as k6 (or python for mcp-*/rest-python)."
        )
    else:
        lines.append(
            "deltas: proxy=passthrough-direct policy=governed-passthrough "
            "total=governed-direct (per gateway; shared direct); "
            "loadgen=rest-python-direct (tooling); "
            "protocol=mcp-direct-rest-python; "
            "mcp_policy=mcp-governed-mcp-direct"
        )

    # Provenance footer — every run_id that contributed.
    id_parts: list[str] = []
    for t in ALL_ARMS:
        if t not in arms:
            continue
        ids = ",".join(
            r["meta"]["run_id"]
            + ("†" if is_suspect(r["data"]) else "")
            for r in arms[t]
        )
        id_parts.append(f"{ARM_LABEL[t]}={ids}")
    commit = meta0["git_commit"][:12]
    host = (meta0.get("host") or {}).get("cpu_model", "?")
    os_name = (meta0.get("host") or {}).get("os", "?")
    lines.append("")
    lines.append(
        f"Provenance: {' '.join(id_parts)} git={commit} host={host} ({os_name})"
        + ("  († = status=suspect)" if count_suspect(arms) else "")
    )
    notes = []
    for t in ALL_ARMS:
        if t not in arms:
            continue
        for row in arms[t]:
            n = row["meta"].get("notes")
            if n:
                notes.append(f"{ARM_LABEL[t]}/{row['meta']['run_id']}={n!r}")
    if notes:
        lines.append("Notes: " + " ".join(notes))

    if spread_warnings:
        seen: set[str] = set()
        unique: list[str] = []
        for w in spread_warnings:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        lines.append("")
        lines.extend(unique)

    return "\n".join(lines)


def report_skips(skips: dict[str, int]) -> None:
    if skips.get("incomplete_meta"):
        print(
            f"Skipped {skips['incomplete_meta']} run(s) lacking "
            f"run_metadata.target or run_metadata.run_id.",
            file=sys.stderr,
        )
    if skips.get("aborted"):
        print(f"Skipped {skips['aborted']} aborted run(s).", file=sys.stderr)
    if skips.get("pre_phase"):
        print(
            f"Skipped {skips['pre_phase']} pre-phase-tag run(s) "
            f"(missing ep_*{{phase:main}}).",
            file=sys.stderr,
        )
    if skips.get("since_filtered"):
        print(
            f"Excluded {skips['since_filtered']} run(s) outside --since filter.",
            file=sys.stderr,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--latest",
        action="store_true",
        help=(
            "newest arm groups per VU (canonical targets: direct, "
            "kong-passthrough, kong-governed, apisix-passthrough, "
            "apisix-governed; legacy aliases passthrough/gateway map to Kong)"
        ),
    )
    ap.add_argument(
        "--since",
        metavar="PREFIX",
        help=(
            "summarise the sweep whose run_ids are at or after this timestamp "
            "prefix (e.g. 20260901T2137); emits Kong + APISIX decomposition "
            "and cross-gateway Δ policy tables like --latest"
        ),
    )
    ap.add_argument(
        "--run-ids",
        nargs="+",
        metavar="ID",
        help=(
            "compare 2–5 run ids (canonical targets or legacy "
            "passthrough/gateway aliases)"
        ),
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

    since = args.since
    rows, skips = index_runs(since=since)
    report_skips(skips)

    if not rows and not args.run_ids:
        raise SystemExit(
            f"no usable (complete/suspect, phase-tagged) runs in {RUNS_DIR}"
            + (f" matching --since {since}" if since else "")
        )

    outputs: list[str] = []
    if args.run_ids:
        if len(args.run_ids) < 2 or len(args.run_ids) > 5:
            raise SystemExit("--run-ids expects 2 to 5 run ids")
        by_id: dict[str, dict] = {}
        for path in sorted(RUNS_DIR.glob("*.json")):
            if path.name == "index.jsonl":
                continue
            data = load_run(path)
            if data is None:
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
            if t not in ALL_ARMS:
                raise SystemExit(
                    f"run {rid} has unknown target {t!r} "
                    f"(canonical: {', '.join(ALL_ARMS)}; "
                    f"legacy aliases: passthrough, gateway)"
                )
            if t in arms:
                raise SystemExit(f"duplicate target {t} in --run-ids")
            arms[t] = [row]
        outputs.append(
            render_group(arms, args.format, args.allow_mixed_commit, args.repeats)
        )
    elif since:
        groups = since_arm_maps(rows, expected_repeats=args.repeats)
        if not groups:
            raise SystemExit(
                f"no phase-tagged runs matching --since {since}"
            )
        for arms in groups:
            outputs.append(
                render_group(
                    arms, args.format, args.allow_mixed_commit, args.repeats
                )
            )
    elif args.latest:
        groups = latest_arm_maps(rows, expected_repeats=args.repeats)
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
        ap.error("specify --latest, --since, or --run-ids")

    print("\n\n".join(outputs))


if __name__ == "__main__":
    main()

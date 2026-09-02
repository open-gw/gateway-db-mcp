#!/usr/bin/env python3
"""generate-results-doc.py — emit bench/RESULTS.md content from archived runs.

Reads results/runs/ and prints a self-contained markdown document to stdout
(caller redirects to RESULTS.md). Standard library only; reuses summarise-runs
logic via importlib so tables cannot drift from the summariser.

Requires --since <run_id prefix> for a citable sweep, or --latest for the
newest complete three-arm sweep.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from collections import Counter
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = BENCH_DIR / "results" / "runs"
SUMMARISE_PATH = Path(__file__).resolve().parent / "summarise-runs.py"


def load_summarise():
    spec = importlib.util.spec_from_file_location("summarise_runs", SUMMARISE_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {SUMMARISE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def section_how_to_reproduce(since: str | None) -> str:
    sweep_note = since or "<sweep-prefix>"
    return f"""## How to reproduce

Exact commands for a citable three-arm sweep (VU 1 / 10 / 50, 20 000 iterations,
3 repeats). Expected wall time is about **45 minutes** on the recorded host.

```bash
cd bench
docker compose -f docker-compose.bench.yml down
docker compose -f docker-compose.bench.yml up -d --build
sleep 60
docker compose -f docker-compose.bench.yml restart jaeger && sleep 20
./scripts/run-benchmark.sh --sweep --iterations 20000 --repeats 3 --no-span-file \\
  --note "citable sweep: three-arm, VU 1/10/50, single commit"
./scripts/summarise-runs.py --since {sweep_note} --format markdown
./scripts/generate-results-doc.py --since {sweep_note} > RESULTS.md
```

Allocate at least 8 CPUs and 8 GB to Docker Desktop before measuring. Citable
latency runs must pass `--no-span-file`. Do not re-run a `status=suspect`
configuration hoping for a better number; selecting runs by outcome is not
acceptable — report the flagged count instead.
"""


def section_hardware(rows: list[dict]) -> str:
    # Prefer the most common host / images among contributing runs.
    hosts = [r["meta"].get("host") or {} for r in rows]
    images_list = [r["meta"].get("images") or {} for r in rows]

    def mode_dict(dicts: list[dict]) -> dict:
        if not dicts:
            return {}
        # Pick the dict that appears most often (by frozenset of items).
        keyed = [tuple(sorted((k, str(v)) for k, v in d.items())) for d in dicts]
        best = Counter(keyed).most_common(1)[0][0]
        return dict(best)

    host = mode_dict(hosts)
    images = mode_dict(images_list)

    cpu = host.get("cpu_model", "?")
    cores = host.get("cpu_cores", "?")
    mem = host.get("memory_gb", "?")
    vm_cpus = host.get("docker_vm_cpus", "?")
    vm_mem = host.get("docker_vm_memory_gb", "?")
    os_arch = host.get("os", "?")

    img_lines = []
    for name in sorted(images.keys()):
        img_lines.append(f"| `{name}` | `{images[name]}` |")
    if not img_lines:
        img_lines.append("| — | (no image digests in selected runs) |")

    return f"""## Hardware and platform

Taken from `run_metadata.host` and `run_metadata.images` of the selected runs
(not hardcoded).

| Field | Value |
| --- | --- |
| CPU model | {cpu} |
| Physical cores | {cores} |
| Physical memory (GB) | {mem} |
| Docker VM vCPUs | {vm_cpus} |
| Docker VM memory (GB) | {vm_mem} |
| OS / arch | {os_arch} |

### Container image digests

| Component | Digest |
| --- | --- |
{chr(10).join(img_lines)}

**Co-location note.** The load generator, gateway, bridge, database, and
identity provider all run on a single machine and share CPU. Absolute latency
and throughput are therefore properties of this harness. Only the deltas
between arms transfer, because all three arms carry the same co-location.
"""


def section_results(sr, rows: list[dict], allow_mixed: bool, repeats: int | None) -> str:
    groups = sr.since_arm_maps(rows)
    parts = ["## Results tables", ""]
    parts.append(
        "Three-arm decomposition. Latency from `ep_*{{phase:main}}`; "
        "throughput re-derived from `iteration_duration{{phase:main}}` "
        "(stored `throughput_measured` is not trusted). Cells are "
        "median [min–max] across repeats. Lead with **Δ policy**."
    )
    parts.append("")
    for arms in groups:
        parts.append(sr.render_group(arms, "markdown", allow_mixed, repeats))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def section_provenance(rows: list[dict], sr) -> str:
    lines = [
        "## Provenance table",
        "",
        "Every run contributing to the figures above.",
        "",
        "| run_id | target | VUs | iterations | git commit | status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    ordered = sorted(
        rows,
        key=lambda r: (
            int(r["meta"]["vus"]),
            r["meta"]["target"],
            r["meta"].get("timestamp_utc") or r["meta"]["run_id"],
        ),
    )
    for row in ordered:
        m = row["meta"]
        status = sr.resolve_status(row["data"]) or "complete"
        lines.append(
            f"| `{m['run_id']}` | {m['target']} | {m['vus']} | "
            f"{m['iterations']} | `{m['git_commit']}` | {status} |"
        )
    lines.append("")
    return "\n".join(lines)


def section_artifacts() -> str:
    return """## Known measurement artifacts

The six instrumentation faults found while building this harness, each with
what it looked like in the data and how the harness now detects or avoids it:

1. **Response-write stall.** Chunked responses without `Content-Length`
   produced a ~40 ms delayed-ACK floor on small bodies, attributed to the
   bridge. `describe_schema` p50 fell from ~45 ms to ~1.3 ms once fixed.
2. **Warmup contamination.** Custom Trends aggregated warmup with measured
   iterations; ~83% of samples in every percentile came from the discarded
   phase. Fixed by phase tagging (`{phase:main}` vs `{phase:warmup}`); the
   summariser reads only phase-tagged series.
3. **Wall-clock throughput.** `http_reqs.rate` divided total requests by total
   run duration including warmup and idle time, understating throughput ~3×.
   Throughput is now derived from `iteration_duration{phase:main}`:
   `duration_s = avg_ms × iterations / vus / 1000`,
   `throughput = iterations × requests_per_iteration / duration_s`.
4. **Telemetry backend absent.** Jaeger was not in the container set; the
   collector spent every governed run in a DNS-failure retry storm, dropping
   spans 8 200 at a time and back-pressuring the Kong plugin. Symptom was a
   non-monotonic throughput curve, 707 → 631 → 2497 req/s across increasing
   concurrency. Preflight now refuses governed runs when Jaeger/collector are
   down or traces are not arriving.
5. **Unbounded trace store.** Jaeger's in-memory store grew to 5.75 GiB of a
   7.75 GiB VM; three identical governed repeats measured 678.5 / 668.9 /
   405.2 req/s as it filled. Bounded with `MEMORY_MAX_TRACES`.
6. **Scenario duration bracket.** `main_scenario_duration_s` from a
   `Date.now()` Trend `(max − min)` across VUs can extend far beyond the
   scenario when VU creation/teardown are not simultaneous. Observed: k6
   console ~27.9 s vs stored duration implying ~86 req/s on a healthy
   gateway VU=50 run. Throughput is therefore re-derived from iteration
   duration (see §3); runs whose stored and derived throughput disagree by
   more than 2× are archived as `status=suspect`.

**Mitigation finding.** The first mitigation for artifact 5 — a per-run
telemetry reset (stop collector, restart Jaeger, drain Kong's OTLP buffer,
restart Kong) — initially made variance worse: three identical governed
repeats measured 410.1 / 160.3 / 549.5 req/s (97% spread, non-monotonic). That
routine was reverted to opt-in (`--reset-telemetry`); default governed runs do
not restart Jaeger/Kong per repeat. Sweeps restart Jaeger once up front.

In a governance benchmark the telemetry path is part of the system under test,
so its own failure modes are indistinguishable from the effect being measured
unless the harness verifies them independently. This is the material for a
threats-to-validity discussion.
"""


def section_repeats(rows: list[dict], sr, expected: int | None) -> str:
    by_cfg: Counter = Counter()
    suspect_by_cfg: Counter = Counter()
    for row in rows:
        m = row["meta"]
        key = (m["target"], int(m["vus"]), int(m["iterations"]))
        by_cfg[key] += 1
        if sr.is_suspect(row["data"]):
            suspect_by_cfg[key] += 1

    exp = expected if expected is not None else 3
    lines = [
        "## Repeat count and exclusions",
        "",
        f"Expected repeats per configuration: **{exp}** "
        f"(override with `--repeats`).",
        "",
        "Criterion: a run is included when it has `ep_*{phase:main}` metrics "
        "and is not `status=aborted`. Runs with `status=suspect` are **included "
        "and flagged** — latency is usable; throughput is re-derived from "
        "`iteration_duration{phase:main}` and must not use the stored "
        "`throughput_measured`. Aborted runs are excluded. Pre-phase-tag and "
        "incomplete-metadata files are skipped.",
        "",
        "| target | VUs | iterations | runs used | of which suspect |",
        "| --- | --- | --- | --- | --- |",
    ]
    for (target, vus, iterations), n in sorted(by_cfg.items()):
        lines.append(
            f"| {target} | {vus} | {iterations} | {n} | "
            f"{suspect_by_cfg.get((target, vus, iterations), 0)} |"
        )
    total_suspect = sum(suspect_by_cfg.values())
    lines.append("")
    lines.append(
        f"Total contributing runs: **{len(rows)}**. "
        f"Flagged suspect: **{total_suspect}**."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--since",
        metavar="PREFIX",
        help="citable sweep timestamp prefix (e.g. 20260901T2137); recommended",
    )
    ap.add_argument(
        "--latest",
        action="store_true",
        help="use newest complete three-arm sweep instead of --since",
    )
    ap.add_argument(
        "--repeats",
        type=int,
        default=3,
        metavar="N",
        help="expected repeats per configuration (default 3)",
    )
    ap.add_argument("--allow-mixed-commit", action="store_true")
    args = ap.parse_args()

    if not args.since and not args.latest:
        raise SystemExit(
            "specify --since <run_id prefix> for a citable sweep "
            "(or --latest for the newest three-arm set)"
        )

    sr = load_summarise()
    if not RUNS_DIR.is_dir():
        raise SystemExit(f"no runs directory at {RUNS_DIR}")

    since = args.since
    if args.latest and not since:
        rows, skips = sr.index_runs(since=None)
        groups = sr.latest_arm_maps(rows)
        if not groups:
            raise SystemExit("no usable runs for --latest")
        # Flatten selected arm groups into the contributing row set.
        selected: list[dict] = []
        seen: set[str] = set()
        for arms in groups:
            for runs in arms.values():
                for row in runs:
                    rid = row["meta"]["run_id"]
                    if rid not in seen:
                        seen.add(rid)
                        selected.append(row)
        rows = selected
    else:
        rows, skips = sr.index_runs(since=since)
        # For the doc, pool by (target, vus, iterations) like --since summariser.
        groups = sr.since_arm_maps(rows)
        if not groups:
            raise SystemExit(
                f"no usable runs matching --since {since}"
            )

    sr.report_skips(skips)

    title = "# Benchmark results"
    subtitle = (
        f"Generated from `results/runs/`"
        + (f" with `--since {since}`" if since else " (`--latest`)")
        + ".\n\n"
        "Figures are traceable to immutable run files. Do not edit this file by "
        "hand — regenerate with `./scripts/generate-results-doc.py`.\n"
    )

    doc = "\n".join([
        title,
        "",
        subtitle,
        section_how_to_reproduce(since),
        section_hardware(rows),
        section_results(sr, rows, args.allow_mixed_commit, args.repeats),
        section_provenance(rows, sr),
        section_artifacts(),
        section_repeats(rows, sr, args.repeats),
    ])
    sys.stdout.write(doc)
    if not doc.endswith("\n"):
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()

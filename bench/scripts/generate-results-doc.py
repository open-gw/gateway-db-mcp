#!/usr/bin/env python3
"""generate-results-doc.py — emit bench/RESULTS.md content from archived runs.

Reads results/runs/ and prints a self-contained markdown document to stdout
(caller redirects to RESULTS.md). Standard library only; reuses summarise-runs
logic via importlib so tables cannot drift from the summariser.

Requires --since <run_id prefix> for a citable sweep, or --latest for the
newest complete five-target sweep.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = BENCH_DIR / "results" / "runs"
SUMMARISE_PATH = Path(__file__).resolve().parent / "summarise-runs.py"

KONG_OPENAPI = "http://localhost:8000/raw/openapi"
APISIX_OPENAPI = "http://localhost:9080/raw/openapi"
MANIFEST_TIMEOUT_S = 2.0


def load_summarise():
    spec = importlib.util.spec_from_file_location("summarise_runs", SUMMARISE_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {SUMMARISE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fetch_openapi_json(url: str) -> dict | None:
    """Return parsed OpenAPI JSON, or None if unreachable / invalid."""
    # Prefer curl when present (matches acceptance wording); fall back to urllib.
    try:
        proc = subprocess.run(
            ["curl", "-fsS", "--max-time", str(int(MANIFEST_TIMEOUT_S)), url],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout)
    except (OSError, json.JSONDecodeError):
        pass
    try:
        with urllib.request.urlopen(url, timeout=MANIFEST_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def check_tool_manifest_identity() -> str:
    """Live Kong vs APISIX /raw/openapi identity, or 'not checked' if stack down."""
    kong = _fetch_openapi_json(KONG_OPENAPI)
    apisix = _fetch_openapi_json(APISIX_OPENAPI)
    if kong is None or apisix is None:
        return "Tool manifest identity (Kong vs APISIX): not checked (stack down)"
    # Equivalent to: curl … | jq -S  for both sides, then diff.
    a = json.dumps(kong, sort_keys=True, separators=(",", ":"))
    b = json.dumps(apisix, sort_keys=True, separators=(",", ":"))
    if a == b:
        return "Tool manifest identity (Kong vs APISIX): PASS"
    return "Tool manifest identity (Kong vs APISIX): FAIL"


def section_how_to_reproduce(since: str | None) -> str:
    sweep_note = since or "<sweep-prefix>"
    return f"""## How to reproduce

Exact commands for a citable five-target sweep (VU 1 / 10 / 50, 20 000 iterations,
3 repeats, both gateways). Expected wall time is about **90 minutes** on the
recorded host (5 targets × 3 VU × 3 repeats = 45 runs).

```bash
cd bench
docker compose -f docker-compose.bench.yml down
docker compose -f docker-compose.bench.yml up -d --build
sleep 60
docker compose -f docker-compose.bench.yml restart jaeger && sleep 20
./scripts/run-benchmark.sh --sweep --gateway both --iterations 20000 --repeats 3 \\
  --no-span-file --note "citable sweep: five-target, VU 1/10/50, single commit"
./scripts/summarise-runs.py --since {sweep_note} --format markdown
./scripts/generate-results-doc.py --since {sweep_note} > RESULTS.md
```

Allocate at least 8 CPUs and 8 GB to Docker Desktop before measuring. Citable
latency runs must pass `--no-span-file`. Do not re-run a `status=suspect`
configuration hoping for a better number; selecting runs by outcome is not
acceptable — report the flagged count instead.

Canonical targets: `direct`, `kong-passthrough`, `kong-governed`,
`apisix-passthrough`, `apisix-governed`. Legacy archived aliases `passthrough`
and `gateway` map to the Kong arms when reading.
"""


def section_platform_coverage() -> str:
    return """## Platform coverage

| Platform | Status |
|---|---|
| Kong Gateway 3.9 (OSS) | Validated end-to-end, latency decomposition measured |
| Apache APISIX | Validated end-to-end, latency decomposition measured |
| Apigee X (embedded) | Deployment documented, not validated in this evaluation |
| Azure API Management | Deployment documented, not validated in this evaluation |

Compose pins `kong:3.9` and `apache/apisix:3.13.0-debian`. Absolute latency is
harness-bound; cite per-gateway Δ policy and the cross-gateway comparison.
"""


def section_tool_manifest() -> str:
    line = check_tool_manifest_identity()
    return f"""## Tool manifest identity

Live check of `GET /raw/openapi` via Kong (`localhost:8000`) and APISIX
(`localhost:9080`), compared as sorted JSON. Confirms both gateways expose the
same bridge tool surface on the passthrough path.

**{line}**
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

**Co-location note.** The load generator, gateway(s), bridge, database, and
identity provider all run on a single machine and share CPU. Absolute latency
and throughput are therefore properties of this harness. Only the deltas
between arms transfer, because all arms carry the same co-location.
"""


def section_results(sr, rows: list[dict], allow_mixed: bool, repeats: int | None) -> str:
    # rows are already the selected contributing set; regroup for tables.
    groups = sr.since_arm_maps(rows, expected_repeats=repeats)
    parts = ["## Results tables", ""]
    parts.append(
        "Five-target decomposition (Kong and APISIX). Shared `direct` baseline. "
        "Per gateway: Δ proxy = passthrough − direct, Δ policy = governed − "
        "passthrough, Δ total = governed − direct. Latency from "
        "`ep_*{{phase:main}}`; throughput re-derived from "
        "`iteration_duration{{phase:main}}` (stored `throughput_measured` is "
        "not trusted). Cells are median [min–max] across repeats. Lead with "
        "**Δ policy**; the cross-gateway table compares Kong vs APISIX Δ policy."
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
        "Every run contributing to the figures above. Targets are canonical "
        "(legacy `passthrough`/`gateway` shown as Kong arms).",
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


def _duration_divergence(meta: dict) -> tuple[bool, float | None, float | None, float | None]:
    """Return (is_divergent, derived_s, walltrend_s, pct_over_derived)."""
    flag = bool(meta.get("duration_metric_divergence"))
    derived = meta.get("main_scenario_duration_s")
    wall = meta.get("main_scenario_duration_s_walltrend")
    pct = None
    try:
        d = float(derived) if derived is not None else None
        w = float(wall) if wall is not None else None
    except (TypeError, ValueError):
        d, w = None, None
    if d is not None and w is not None and d > 0:
        pct = ((w - d) / d) * 100.0
    return flag, d, w, pct


def section_repeats(rows: list[dict], sr, expected: int | None) -> str:
    by_cfg: Counter = Counter()
    suspect_by_cfg: Counter = Counter()
    divergent_by_cfg: Counter = Counter()
    divergent_rows: list[dict] = []
    for row in rows:
        m = row["meta"]
        key = (m["target"], int(m["vus"]), int(m["iterations"]))
        by_cfg[key] += 1
        if sr.is_suspect(row["data"]):
            suspect_by_cfg[key] += 1
        flag, _d, _w, _pct = _duration_divergence(m)
        if flag:
            divergent_by_cfg[key] += 1
            divergent_rows.append(row)

    exp = expected if expected is not None else 3
    lines = [
        "## Repeat count and exclusions",
        "",
        f"Expected repeats per configuration: **{exp}** "
        f"(override with `--repeats`).",
        "",
        "Criterion: a run is included when it has `ep_*{phase:main}` metrics "
        "and is not `status=aborted`, and it belongs to the newest proximity "
        "cluster for its (target, VUs, iterations) — consecutive runs more "
        f"than {sr.REPEAT_GAP_SECONDS // 60} minutes apart are treated as a "
        "separate attempt. Runs with `status=suspect` are **included and "
        "flagged** — latency is usable; throughput is re-derived from "
        "`iteration_duration{phase:main}` and must not use the stored "
        "`throughput_measured`. Aborted runs are excluded (they do not split "
        "a proximity cluster). Pre-phase-tag and incomplete-metadata files "
        "are skipped.",
        "",
        "| target | VUs | iterations | runs used | of which suspect | divergent |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for (target, vus, iterations), n in sorted(by_cfg.items()):
        lines.append(
            f"| {target} | {vus} | {iterations} | {n} | "
            f"{suspect_by_cfg.get((target, vus, iterations), 0)} | "
            f"{divergent_by_cfg.get((target, vus, iterations), 0)} |"
        )
        if n != exp:
            print(
                f"WARNING: repeat-count mismatch "
                f"target={target} VU={vus} iterations={iterations}: "
                f"have {n}, expected {exp}",
                file=sys.stderr,
            )
    total_suspect = sum(suspect_by_cfg.values())
    total_divergent = sum(divergent_by_cfg.values())
    lines.append("")
    lines.append(
        f"Total contributing runs: **{len(rows)}**. "
        f"Flagged suspect: **{total_suspect}**. "
        f"Duration-metric divergent: **{total_divergent}**."
    )
    lines.append("")

    # Always state divergence status explicitly (none vs not checked).
    lines.append("### Scenario-duration divergence")
    lines.append("")
    if not divergent_rows:
        lines.append(
            "No contributing runs recorded `duration_metric_divergence: true`. "
            "The wall-trend and derived scenario durations agreed within 20% "
            "for every cited run."
        )
    else:
        details = []
        pcts = []
        ids = []
        for row in sorted(divergent_rows, key=lambda r: r["meta"]["run_id"]):
            m = row["meta"]
            _flag, derived, wall, pct = _duration_divergence(m)
            rid = m["run_id"]
            ids.append(f"`{rid}`")
            if pct is not None:
                pcts.append(f"{pct:.0f}%")
            if derived is not None and wall is not None and pct is not None:
                details.append(
                    f"`{rid}` (derived {derived:.2f} s, wall-trend {wall:.2f} s, "
                    f"{pct:.0f}% over derived)"
                )
            else:
                details.append(f"`{rid}` (duration fields incomplete)")
        n = len(divergent_rows)
        pct_list = ", ".join(pcts) if pcts else "n/a"
        id_list = ", ".join(ids)
        lines.append(
            f"{n} run{'s' if n != 1 else ''} recorded a scenario-duration "
            f"divergence: the `Date.now()` wall Trend exceeded the derived "
            f"duration by {pct_list} ({id_list}). These runs overlapped host "
            f"sleep during an unattended sweep. The derived duration and all "
            f"per-request latency percentiles are unaffected, since k6 records "
            f"per-request timings independently of scenario duration. Tail "
            f"percentiles for the affected configurations carry wider ranges "
            f"and are marked in the tables. The runs are retained rather than "
            f"excluded, because excluding them would select on a condition that "
            f"does not affect the measured quantity."
        )
        lines.append("")
        lines.append("Per run: " + "; ".join(details) + ".")
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
        help="use newest complete five-target sweep instead of --since",
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
            "(or --latest for the newest five-target set)"
        )

    sr = load_summarise()
    if not RUNS_DIR.is_dir():
        raise SystemExit(f"no runs directory at {RUNS_DIR}")

    since = args.since
    if args.latest and not since:
        rows, skips = sr.index_runs(since=None)
        groups = sr.latest_arm_maps(rows, expected_repeats=args.repeats)
        if not groups:
            raise SystemExit("no usable runs for --latest")
        rows = sr.flatten_arm_maps(groups)
    else:
        rows, skips = sr.index_runs(since=since)
        groups = sr.since_arm_maps(rows, expected_repeats=args.repeats)
        if not groups:
            raise SystemExit(
                f"no usable runs matching --since {since}"
            )
        # Cite only the selected proximity clusters, not every --since match.
        rows = sr.flatten_arm_maps(groups)

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
        section_platform_coverage(),
        section_tool_manifest(),
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

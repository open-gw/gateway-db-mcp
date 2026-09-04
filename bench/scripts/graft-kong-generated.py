#!/usr/bin/env python3
"""Graft hand-written Kong JWT consumer + /db plugins onto generated list_tables.

Used by verify-kong-generated.sh inside a short-lived python:3.12-slim container
(host may lack PyYAML). Args: gen.yml hand.yml out.yml
"""
from __future__ import annotations

import copy
import sys

import yaml


def main() -> None:
    gen_path, hand_path, out_path = sys.argv[1:4]
    with open(gen_path, encoding="utf-8") as f:
        gen = yaml.safe_load(f)
    with open(hand_path, encoding="utf-8") as f:
        hand = yaml.safe_load(f)

    out = copy.deepcopy(gen)
    out["consumers"] = hand.get("consumers") or []
    out["jwt_secrets"] = hand.get("jwt_secrets") or []

    plugins = None
    for svc in hand.get("services") or []:
        paths: list[str] = []
        for route in svc.get("routes") or []:
            paths.extend(route.get("paths") or [])
        if "/db" in paths and svc.get("plugins"):
            plugins = copy.deepcopy(svc["plugins"])
            break
    if not plugins:
        raise SystemExit("could not locate /db plugins in hand-written kong.yml")

    attached = False
    for svc in out.get("services") or []:
        for route in svc.get("routes") or []:
            name = route.get("name") or ""
            rpaths = route.get("paths") or []
            if "list-tables" in name or any(
                p.endswith("tables$") and "schema" not in p and "rows" not in p
                for p in rpaths
            ):
                route["plugins"] = copy.deepcopy(plugins)
                attached = True
                break
        if attached:
            break
    if not attached:
        raise SystemExit("could not find list_tables route in generated config")

    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(out, f, sort_keys=False)
    print("grafted JWT plugins onto list_tables →", out_path, file=sys.stderr)


if __name__ == "__main__":
    main()

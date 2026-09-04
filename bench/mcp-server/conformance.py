#!/usr/bin/env python3
"""MCP conformance: tools/list + tools/call get_orders_rows.

Connects to MCP_URL (default http://localhost:9090/mcp) via FastMCP Client
(streamable HTTP). Writes a verbatim transcript to stdout (or --out PATH).

Expected tool names (operationId == x-mcp-tool on the bridge OpenAPI):
  list_tables, run_query,
  get_customers_rows, get_orders_rows, get_products_rows,
  describe_customers_schema, describe_orders_schema, describe_products_schema

If tools/list does not match these eight names, exit non-zero and stop.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client

EXPECTED = {
    "list_tables",
    "run_query",
    "get_customers_rows",
    "get_orders_rows",
    "get_products_rows",
    "describe_customers_schema",
    "describe_orders_schema",
    "describe_products_schema",
}


def _tool_names(tools) -> list[str]:
    names = []
    for t in tools:
        name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
        if name:
            names.append(str(name))
    return sorted(names)


def _tool_schema(tools, name: str):
    for t in tools:
        n = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
        if n == name:
            return getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or (
                t.get("inputSchema") if isinstance(t, dict) else None
            )
    return None


async def run(mcp_url: str) -> str:
    lines: list[str] = []
    lines.append(f"# MCP conformance against {mcp_url}")
    lines.append(f"# FastMCP Client streamable HTTP")
    lines.append("")

    async with Client(mcp_url) as client:
        tools = await client.list_tools()
        names = _tool_names(tools)
        lines.append("## tools/list")
        lines.append(json.dumps({"tools": names}, indent=2))
        lines.append("")

        got = set(names)
        if got != EXPECTED:
            lines.append("## FAIL: tool name mismatch")
            lines.append(f"expected: {sorted(EXPECTED)}")
            lines.append(f"got:      {names}")
            lines.append(f"missing:  {sorted(EXPECTED - got)}")
            lines.append(f"extra:    {sorted(got - EXPECTED)}")
            lines.append("")
            lines.append(
                "STOP: FastMCP derives names from operationId; bridge sets "
                "operationId and x-mcp-tool to the same eight names. Investigate "
                "before continuing the MCP measurement arm."
            )
            transcript = "\n".join(lines) + "\n"
            print(transcript, end="")
            raise SystemExit(2)

        schema = _tool_schema(tools, "get_orders_rows")
        lines.append("## get_orders_rows inputSchema")
        lines.append(json.dumps(schema, indent=2, default=str))
        lines.append("")

        # Query-param tools typically take limit as a top-level arg.
        args = {"limit": 3}
        lines.append("## tools/call get_orders_rows")
        lines.append(f"arguments: {json.dumps(args)}")
        result = await client.call_tool("get_orders_rows", args)
        # Prefer structured / data when present; fall back to content.
        payload = getattr(result, "data", None)
        if payload is None:
            payload = getattr(result, "structured_content", None)
        if payload is None:
            content = getattr(result, "content", None)
            if content is not None:
                serializable = []
                for c in content:
                    if hasattr(c, "model_dump"):
                        serializable.append(c.model_dump())
                    elif isinstance(c, dict):
                        serializable.append(c)
                    else:
                        serializable.append(str(c))
                payload = serializable
            else:
                payload = str(result)
        lines.append("result:")
        lines.append(json.dumps(payload, indent=2, default=str))
        lines.append("")
        lines.append("## PASS")
        lines.append(f"tools/list matched {len(EXPECTED)} expected names; get_orders_rows succeeded.")

    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--mcp-url",
        default=None,
        help="MCP streamable HTTP URL (default: env MCP_URL or http://localhost:9090/mcp)",
    )
    ap.add_argument("--out", default=None, help="Write transcript to this path as well as stdout")
    args = ap.parse_args()
    mcp_url = args.mcp_url or __import__("os").environ.get("MCP_URL", "http://localhost:9090/mcp")

    try:
        transcript = asyncio.run(run(mcp_url))
    except SystemExit:
        raise
    except Exception as e:
        msg = f"# MCP conformance FAILED against {mcp_url}\n\n{type(e).__name__}: {e}\n"
        print(msg, end="", file=sys.stderr)
        if args.out:
            Path(args.out).write_text(msg, encoding="utf-8")
        raise SystemExit(1) from e

    print(transcript, end="")
    if args.out:
        Path(args.out).write_text(transcript, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()

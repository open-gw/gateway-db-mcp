#!/usr/bin/env python3
"""OpenAPI → MCP server for the GatewayDB-MCP bench harness (FastMCP 2.x).

Fetches the bridge OpenAPI document unchanged, converts every operation to an
MCP tool via FastMCP.from_openapi, and serves streamable HTTP at /mcp.

Tool names come from OpenAPI operationId (not x-mcp-tool). The bridge sets both
to the same eight names, so tools/list should match list_tables, run_query,
get_{t}_rows, describe_{t}_schema for customers/orders/products.
"""
from __future__ import annotations

import os
import sys
import time

import httpx
from fastmcp import FastMCP
from fastmcp.server.openapi import MCPType, RouteMap
from starlette.requests import Request
from starlette.responses import JSONResponse


OPENAPI_URL = os.environ.get("OPENAPI_URL", "http://bridge:8080/openapi")
BRIDGE_BASE = os.environ.get("BRIDGE_BASE", "http://bridge:8080")
PORT = int(os.environ.get("PORT", "8080"))
FETCH_RETRIES = int(os.environ.get("OPENAPI_FETCH_RETRIES", "60"))
FETCH_INTERVAL_S = float(os.environ.get("OPENAPI_FETCH_INTERVAL_S", "2"))


def fetch_openapi(url: str) -> dict:
    last_err: Exception | None = None
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            with httpx.Client(timeout=10.0) as c:
                r = c.get(url)
                r.raise_for_status()
                spec = r.json()
                if not isinstance(spec, dict) or "paths" not in spec:
                    raise ValueError(f"OpenAPI response missing paths: {type(spec)}")
                print(f"Fetched OpenAPI from {url} (attempt {attempt})", file=sys.stderr)
                # Consume the bridge document unmodified — no harness edits.
                return spec
        except Exception as e:
            last_err = e
            print(
                f"OpenAPI fetch failed ({attempt}/{FETCH_RETRIES}): {e}",
                file=sys.stderr,
            )
            time.sleep(FETCH_INTERVAL_S)
    raise RuntimeError(f"Could not fetch OpenAPI from {url}: {last_err}")


def build_mcp(spec: dict, client: httpx.AsyncClient) -> FastMCP:
    # Exclude anything under /openapi if it ever appears (GenerateOpenAPI does
    # not put it in paths today). Default is TOOL for everything else.
    route_maps = [
        RouteMap(pattern=r"^/openapi$", mcp_type=MCPType.EXCLUDE),
    ]
    return FastMCP.from_openapi(
        openapi_spec=spec,
        client=client,
        name="gateway-db-mcp-bench",
        route_maps=route_maps,
    )


def main() -> None:
    spec = fetch_openapi(OPENAPI_URL)
    client = httpx.AsyncClient(base_url=BRIDGE_BASE, timeout=60.0)
    mcp = build_mcp(spec, client)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": "gateway-db-mcp-bench"})

    print(
        f"Starting FastMCP streamable HTTP on 0.0.0.0:{PORT}/mcp "
        f"(bridge={BRIDGE_BASE})",
        file=sys.stderr,
    )
    mcp.run(transport="http", host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()

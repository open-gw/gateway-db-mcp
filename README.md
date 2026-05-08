# GatewayDB-MCP

**A configuration-driven bridge from any JDBC database to MCP (Model Context Protocol) tool endpoints in enterprise API gateways.**

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
![Java](https://img.shields.io/badge/Java-17+-orange)
[![Stars](https://img.shields.io/github/stars/open-gw/gateway-db-mcp)](https://github.com/open-gw/gateway-db-mcp/stargazers)

---

## Overview

GatewayDB-MCP lets you **securely expose relational databases** as MCP tools for AI agents (like Claude) through **Apigee X, Kong Gateway, and Azure API Management** — **without writing custom backend code**.

It was developed in a large healthcare enterprise and presented as an **Experience Paper** at IEEE (ICSA/ICWS track).

**[Read the Paper →](https://github.com/open-gw/gateway-db-mcp/blob/main/docs/GatewayDB-MCP-IEEE-Paper.pdf)**

---

## ✨ Highlights

- Live OpenAPI 3.0 generation with `x-mcp-tool` annotations
- Strong read-only security model (defense-in-depth)
- Two deployment modes: **Embedded Java Callout** (Apigee X – zero infra) + **Docker Sidecar**
- Domain-scoped endpoints for better tool discovery and security
- Sub-100ms query latency
- Full reproducibility artifacts (H2 tests, JMeter, DDLs)

---

## Quick Start

```bash
git clone https://github.com/open-gw/gateway-db-mcp.git
cd gateway-db-mcp

cp .env.example .env
# Edit .env with your DB credentials

docker compose up -d

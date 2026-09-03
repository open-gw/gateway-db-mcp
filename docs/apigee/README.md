# Apigee X Integration Guide

Deploy **gateway-db-mcp** as an embedded Java Callout inside an Apigee X proxy — no external services, no additional infrastructure. Your database becomes an MCP tool endpoint in under an hour.

---

## Contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Step 1 — Validate JVM socket access](#step-1--validate-jvm-socket-access)
- [Step 2 — Build the JAR](#step-2--build-the-jar)
- [Step 3 — Configure your database credentials](#step-3--configure-your-database-credentials)
- [Step 4 — Deploy the bridge proxy](#step-4--deploy-the-bridge-proxy)
- [Step 5 — Test the bridge endpoints](#step-5--test-the-bridge-endpoints)
- [Step 6 — Generate your MCP spec](#step-6--generate-your-mcp-spec)
- [Step 7 — Create the Apigee MCP proxy](#step-7--create-the-apigee-mcp-proxy)
- [Step 8 — Validate end-to-end MCP](#step-8--validate-end-to-end-mcp)
- [Configuration reference](#configuration-reference)
- [Observability and audit logging](#observability-and-audit-logging)
- [Domain-scoped deployment model](#domain-scoped-deployment-model)
- [Troubleshooting](#troubleshooting)

---

## Architecture

```
AI Agent (Claude / custom)
        │
        │  MCP tools/call (JSON-RPC over HTTP)
        ▼
┌─────────────────────────────────┐
│  Apigee X — MCP Proxy           │
│  basepath: /mcp/your-domain     │
│  target:   mcp.apigee.internal  │
└─────────────────────────────────┘
        │
        │  HTTP  (Apigee internal routing)
        ▼
┌─────────────────────────────────┐
│  Apigee X — Bridge Proxy        │
│  basepath:  /db-mcp             │
│  RouteRule: NoRoute             │
│                                 │
│  ┌─ JavaCallout (JC-DBBridge) ─┐│
│  │  DBMCPCallout               ││
│  │  HikariCP pool              ││
│  │  JDBC → your database       ││
│  └─────────────────────────────┘│
└─────────────────────────────────┘
        │
        │  JDBC over TCP (Cloud SQL Private IP
        │  or external DB with VPC peering)
        ▼
  MySQL / MariaDB / PostgreSQL / MSSQL
```

The bridge proxy has `RouteRule = NoRoute` — the Java Callout writes the HTTP response directly into the Apigee message context without forwarding to any upstream target. No backend service is deployed.

Per-engine examples:

| Engine | Example |
|---|---|
| MySQL | [`examples/mysql/`](examples/mysql/) |
| MariaDB | [`examples/mariadb/`](examples/mariadb/) — build with `mvn clean package -Pmariadb` |
| PostgreSQL | [`examples/postgres/`](examples/postgres/) |

---

## Prerequisites

| Requirement | Check |
|---|---|
| Apigee X organization on Google Cloud | `gcloud apigee organizations list` |
| Apigee X environment deployed | Console → Apigee → Environments |
| Cloud SQL instance (Private IP recommended) | Console → Cloud SQL |
| VPC peering between Apigee X and Cloud SQL VPC | Console → VPC Network → VPC Peering |
| Java 11+ locally | `java -version` |
| Maven 3.8+ locally | `mvn -version` |
| `apigeecli` or `gcloud` CLI | `apigeecli -v` |
| Read-only database user created | See [Create read-only database user](#create-read-only-database-user) |

### Create a read-only database user

**This is mandatory.** The SQL validator is defence-in-depth, not the primary write-prevention control. You must provision a read-only user.

**MySQL:**
```sql
CREATE USER 'gateway_readonly'@'%' IDENTIFIED BY 'strong_password_here';
GRANT SELECT ON your_database.* TO 'gateway_readonly'@'%';
-- Grant only the specific tables you will expose:
-- GRANT SELECT ON your_database.orders TO 'gateway_readonly'@'%';
FLUSH PRIVILEGES;
```

**PostgreSQL:**
```sql
CREATE USER gateway_readonly WITH PASSWORD 'strong_password_here';
GRANT CONNECT ON DATABASE your_database TO gateway_readonly;
GRANT USAGE ON SCHEMA public TO gateway_readonly;
GRANT SELECT ON TABLE orders, products, customers TO gateway_readonly;
-- Or grant on all tables in a schema:
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO gateway_readonly;
```

---

## Step 1 — Validate JVM socket access

> **Do this before building anything.** Apigee X managed environments enforce a JVM SecurityManager. If outbound TCP connections to your database host are blocked, embedded mode will not work and you should use [sidecar mode](../sidecar/README.md) instead.

Deploy the socket test callout from `docs/socket-test/` to your Apigee org:

```bash
cd docs/socket-test
# Edit socket-test.xml to set your DB host and port
# Then deploy and call the test endpoint:
curl -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://your-org-eval.apigee.net/socket-test?host=YOUR_DB_IP&port=3306"
```

**Expected response (socket accessible):**
```json
{ "result": "success", "host": "10.x.x.x", "port": 3306 }
```

**If you get a `SecurityException` or timeout:** TCP is blocked. Use sidecar mode instead — see [`docs/sidecar/README.md`](../sidecar/README.md). Everything else in this guide still applies except the deployment model.

---

## Step 2 — Build the JAR

**Required before deployment.** The shaded callout JAR is not committed to the
repository. `mvn package` builds it and copies it into
`apiproxy/resources/java/` for the proxy bundle. Every other deployment path
already requires this step; Apigee embedded mode is the same.

### 2a. Install Apigee stub JARs (first time only)

```bash
chmod +x scripts/install-apigee-stubs.sh
./scripts/install-apigee-stubs.sh
```

Verify:
```bash
jar tf ~/.m2/repository/com/apigee/edge/expressions/1.0.0/expressions-1.0.0.jar
# Should print class file names — not an error
```

### 2b. Build

```bash
mvn clean package -DskipTests
```

The shaded JAR is written to `target/` and copied to
`apiproxy/resources/java/gateway-db-mcp-<version>.jar` (version from `pom.xml`).
For MariaDB, use `mvn clean package -DskipTests -Pmariadb`.

Verify the JAR is a valid ZIP (not a corrupt stub):
```bash
ls apiproxy/resources/java/gateway-db-mcp-*.jar
jar tf apiproxy/resources/java/gateway-db-mcp-*.jar | head -5
```

---

## Step 3 — Configure your database credentials

### 3a. Create an Apigee Property Set

Property Sets store non-sensitive configuration that can be updated without redeploying the proxy.

Go to **Apigee Console → Admin → Environments → [your env] → Property Sets → + New**

Name: `db-config`

Add these keys:

| Key | Example value | Notes |
|---|---|---|
| `type` | `mysql` | `mysql`, `postgres`, or `mssql` |
| `host` | `10.20.30.40` | Cloud SQL Private IP (recommended) or hostname |
| `port` | `3306` | Leave blank to use type default |
| `database` | `orders_db` | Database / catalog name |
| `username` | `gateway_readonly` | The read-only user you created in Step 0 |
| `allowedTables` | `orders,order_items,products` | Comma-separated. Leave blank = all tables |
| `apiTitle` | `Orders Domain` | Used in generated OpenAPI spec title |

> **Tip — domain-scoped naming:** Use a separate Property Set per application domain if you deploy multiple bridge instances. For example: `db-config-orders`, `db-config-inventory`, `db-config-finance`.

### 3b. Store the database password in a KVM

KVMs (Key Value Maps) store sensitive values encrypted at rest.

**Create the KVM:**
```bash
gcloud apigee environments describe-iam-policy \
  --environment=YOUR_ENV --organization=YOUR_ORG  # verify access

# Via Apigee Console: Admin → Environments → Key Value Maps → + New
# Name: db-secrets
# Encrypted: YES (required)
```

**Add the password entry:**
```bash
# Via Console: db-secrets → + Add Entry
# Key: password
# Value: your_readonly_user_password
```

### 3c. Add the KVM lookup policy to the proxy

Open `apiproxy/policies/` and create `KVM-GetDBPassword.xml`:

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<KeyValueMapOperations name="KVM-GetDBPassword" mapIdentifier="db-secrets">
  <Scope>environment</Scope>
  <Get assignTo="private.db.password">
    <Key>
      <Parameter>password</Parameter>
    </Key>
  </Get>
</KeyValueMapOperations>
```

Add it as the **first step** in the proxy PreFlow request (before the JavaCallout):

```xml
<!-- in apiproxy/proxies/default.xml PreFlow -->
<PreFlow name="PreFlow">
  <Request>
    <Step><Name>KVM-GetDBPassword</Name></Step>
    <!-- auth and rate limiting steps follow -->
  </Request>
</PreFlow>
```

The KVM populates `{private.db.password}`, which `JC-DBBridge.xml` references as `{private.db.password}`. The value is never logged or exposed in the proxy bundle.

---

## Step 4 — Deploy the bridge proxy

### 4a. Review JC-DBBridge.xml

Open `apiproxy/policies/JC-DBBridge.xml`. The critical section:

```xml
<Properties>
  <Property name="db.type">{propertyset.db-config.type}</Property>
  <Property name="db.host">{propertyset.db-config.host}</Property>
  <Property name="db.port">{propertyset.db-config.port}</Property>
  <Property name="db.database">{propertyset.db-config.database}</Property>
  <Property name="db.username">{propertyset.db-config.username}</Property>
  <Property name="db.password">{private.db.password}</Property>
  <Property name="security.readOnly">true</Property>
  <Property name="security.allowedTables">{propertyset.db-config.allowedTables}</Property>
  <Property name="security.maxRows">1000</Property>
  <Property name="security.queryTimeout">30</Property>
  <Property name="api.title">{propertyset.db-config.apiTitle}</Property>
</Properties>
```

Adjust `security.maxRows` and `security.queryTimeout` to match your database instance capacity.

> **Pool sizing:** Default `pool.maxSize` is 10. If you have multiple Apigee X instances, the total connections = `pool.maxSize × number_of_instances`. Check your Cloud SQL `max_connections` before deploying. For a db-n1-standard-4 instance, `max_connections` is typically 1000 — 10 connections per Apigee instance is safe for most deployments.

### 4b. Package the proxy bundle

```bash
cd apiproxy
zip -r ../gateway-db-mcp-bridge.zip . -x "*.DS_Store"
cd ..
```

### 4c. Deploy via apigeecli

```bash
apigeecli apis create bundle \
  --name gateway-db-mcp-bridge \
  --proxy-zip ./gateway-db-mcp-bridge.zip \
  --org YOUR_ORG \
  --env YOUR_ENV \
  --token $(gcloud auth print-access-token) \
  --ovr  # overwrite existing revision
```

Or via gcloud:
```bash
gcloud apigee apis deploy \
  --api=gateway-db-mcp-bridge \
  --environment=YOUR_ENV \
  --organization=YOUR_ORG
```

Or via the **Apigee Console** UI:
1. Develop → API Proxies → + Create
2. Upload proxy bundle → select `gateway-db-mcp-bridge.zip`
3. Deploy to environment

---

## Step 5 — Test the bridge endpoints

Get an access token and test each endpoint:

```bash
TOKEN=$(gcloud auth print-access-token)
BASE="https://YOUR_ORG-YOUR_ENV.apigee.net/db-mcp"

# List tables
curl -H "Authorization: Bearer $TOKEN" "$BASE/tables"
# Expected: {"tables":["orders","order_items","products"],"count":3,"database":"orders_db"}

# Describe schema
curl -H "Authorization: Bearer $TOKEN" "$BASE/tables/orders/schema"
# Expected: {"table":"orders","columns":[...],"primaryKeys":["id"]}

# Get rows (first 10)
curl -H "Authorization: Bearer $TOKEN" "$BASE/tables/orders/rows?limit=10"
# Expected: {"rows":[...],"count":10,"truncated":false}

# Run query
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sql":"SELECT id, status, total FROM orders WHERE status = ?","params":["completed"]}' \
  "$BASE/query"
# Expected: {"columns":["id","status","total"],"rows":[...],"count":N}
```

**All four return HTTP 200?** You are ready for Step 6.

**Getting 500 INTERNAL_ERROR?** See [Troubleshooting](#troubleshooting).

---

## Step 6 — Generate your MCP spec

```bash
curl -H "Authorization: Bearer $TOKEN" "$BASE/openapi" | python3 -m json.tool > my-db-mcp-spec.json
```

Open `my-db-mcp-spec.json` and verify:
- `paths` contains one `/tables/X/rows` and one `/tables/X/schema` for each allowed table
- Each GET operation has an `x-mcp-tool` object with `name` and `description`
- `info.title` matches the `api.title` you set in the Property Set

> **Spec updates:** If you change `security.allowedTables` or the database schema changes, re-run this command to get a fresh spec and re-import it into the MCP proxy (Step 7). The bridge always reflects the live schema.

---

## Step 7 — Create the Apigee MCP proxy

### 7a. Via Apigee Console (UI)

1. **Develop → API Proxies → + Create**
2. Select **MCP** proxy type
3. Set basepath: `/mcp/orders` (use your domain name — see [domain-scoped model](#domain-scoped-deployment-model))
4. Target: `mcp.apigee.internal`
5. Upload OpenAPI spec: paste or upload `my-db-mcp-spec.json`
6. Deploy to environment

### 7b. Via apigeecli

```bash
apigeecli apis create mcp \
  --name mcp-orders \
  --basepath /mcp/orders \
  --spec my-db-mcp-spec.json \
  --org YOUR_ORG \
  --env YOUR_ENV \
  --token $(gcloud auth print-access-token)
```

### 7c. Add OAuth authentication to the MCP proxy

The MCP proxy needs an `OAuthV2VerifyAccessToken` policy to authenticate AI agents:

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<OAuthV2 name="VA-VerifyToken">
  <Operation>VerifyAccessToken</Operation>
</OAuthV2>
```

Add to the MCP proxy PreFlow request as the first step.

### 7d. Add rate limiting per agent identity

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<SpikeArrest name="SA-AgentRateLimit">
  <Rate>10ps</Rate>
  <Identifier ref="token.client_id"/>
</SpikeArrest>
```

---

## Step 8 — Validate end-to-end MCP

Using the MCP Inspector tool or a Python MCP client:

```python
# pip install mcp
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def test():
    token = "YOUR_OAUTH_TOKEN"
    url   = "https://YOUR_ORG-YOUR_ENV.apigee.net/mcp/orders"

    async with streamablehttp_client(url,
            headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()

            # List tools
            tools = await session.list_tools()
            print("Tools:", [t.name for t in tools.tools])
            # Expected: ['list_tables', 'get_orders_rows', 'describe_orders_schema', ...]

            # Call a tool
            result = await session.call_tool("get_orders_rows", {"limit": 5})
            print("Result:", result.content)

asyncio.run(test())
```

Expected `tools` output:
```
['list_tables', 'get_orders_rows', 'describe_orders_schema',
 'get_order_items_rows', 'describe_order_items_schema',
 'get_products_rows', 'describe_products_schema', 'run_query']
```

---

## Configuration reference

Full property catalogue for `JC-DBBridge.xml`:

| Property | Required | Default | Description |
|---|---|---|---|
| `db.type` | No | `mysql` | `mysql` \| `postgres` \| `mssql` |
| `db.host` | **Yes** | — | Hostname or IP |
| `db.port` | No | type default | Override port |
| `db.database` | **Yes** | — | Database / catalog name |
| `db.username` | **Yes** | — | Database user (use read-only user) |
| `db.password` | **Yes** | — | Use `{private.db.password}` from KVM |
| `db.schema` | No | — | Schema filter (PostgreSQL / MSSQL only) |
| `pool.maxSize` | No | `10` | Max HikariCP connections per instance |
| `pool.minIdle` | No | `2` | Min idle connections |
| `pool.connectionTimeout` | No | `30000` | Pool checkout timeout (ms) |
| `pool.idleTimeout` | No | `600000` | Idle eviction timeout (ms) |
| `pool.maxLifetime` | No | `1800000` | Max connection lifetime (ms) |
| `security.readOnly` | No | `true` | Allow only SELECT via /query |
| `security.allowedTables` | No | all | Comma-separated table whitelist |
| `security.maxRows` | No | `1000` | Hard row cap (1–100,000) |
| `security.queryTimeout` | No | `30` | Query abort timeout (1–300 sec) |
| `api.title` | No | `DB MCP Bridge` | Title in generated OpenAPI spec |
| `api.version` | No | `1.0.0` | Version in generated OpenAPI spec |

---

## Observability and audit logging

The included `ML-OTELLog.xml` policy emits a structured JSON log entry for every operation. It writes to Cloud Logging automatically in Apigee X.

**Sample log entry:**
```json
{
  "timestamp":    "2026-05-12T08:23:11Z",
  "traceId":      "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
  "proxy":        "gateway-db-mcp-bridge",
  "clientIP":     "203.0.113.42",
  "httpMethod":   "POST",
  "pathSuffix":   "/query",
  "responseCode": "200",
  "dbOperation":  "RUN_QUERY",
  "rowCount":     "47",
  "clientId":     "my-ai-agent-app"
}
```

**Query in Cloud Logging:**
```
resource.type="apigee.googleapis.com/Environment"
jsonPayload.dbOperation="RUN_QUERY"
```

**View traces in Cloud Trace:**

If the AI agent sends a `traceparent` header (W3C Trace Context), the `traceId` field links the Apigee log entry to the full distributed trace in Cloud Trace.

---

## Domain-scoped deployment model

Deploy one bridge proxy per application domain. Each domain exposes only the tables relevant to its AI agent use case.

```
/db-mcp/orders     → orders, order_items, shipments
/db-mcp/inventory  → products, stock, warehouses
/db-mcp/finance    → invoices, payments, gl_entries

/mcp/orders        → MCP proxy for orders domain
/mcp/inventory     → MCP proxy for inventory domain
/mcp/finance       → MCP proxy for finance domain
```

Each bridge uses a separate Property Set (`db-config-orders`, `db-config-inventory`, etc.) with different `allowedTables` values. KVM `db-secrets` can be shared if the same read-only user is used, or separate KVMs per domain for stricter isolation.

**Why not one proxy for all tables?** MCP's `tools/list` returns the full tool manifest at agent connection time. Exposing all domains in one proxy gives an AI agent a semantically incoherent tool set (order management + HR + finance simultaneously), degrades tool selection quality, and widens the security blast radius of a single OAuth credential.

---

## Troubleshooting

### 500 INTERNAL_ERROR on first request

**Cause 1 — Pool initialization failed (most common)**

Check Apigee analytics for the detailed error. The most common causes:
- Database host unreachable (TCP blocked — run the socket test in Step 1)
- Wrong `db.host` or `db.port` in Property Set
- KVM `db-secrets` does not exist or key `password` is missing
- Database user does not exist or wrong password

**Cause 2 — JAR not deployed**

Verify the JAR is in the proxy bundle:
```bash
unzip -l gateway-db-mcp-bridge.zip | grep .jar
# Should show: apiproxy/resources/java/gateway-db-mcp-*.jar
```

If missing, run `mvn package` and repackage the bundle.

**Cause 3 — Corrupt JAR (zip END header not found)**

The JAR was installed as an empty stub. Rebuild:
```bash
rm -rf ~/.m2/repository/com/apigee/edge/
./scripts/install-apigee-stubs.sh
mvn clean package
```

---

### 403 TABLE_NOT_ALLOWED

The table name in the request is not in `security.allowedTables`. Options:
- Add the table to the Property Set `allowedTables` value
- If `allowedTables` is empty (all allowed), this error should not occur — check the KVM-to-property resolution

---

### 403 FORBIDDEN — "Only SELECT statements are permitted"

`security.readOnly=true` and the request body contains a non-SELECT statement. Confirm the agent is sending only SELECT queries or set `security.readOnly=false` (not recommended for production).

---

### GET /openapi returns incomplete spec (missing tables)

Tables not in `security.allowedTables` are silently excluded from the spec. Verify the Property Set value contains all tables you want exposed.

---

### Pool exhaustion under load (500 after pool.connectionTimeout)

Increase `pool.maxSize` in `JC-DBBridge.xml` — but verify your Cloud SQL `max_connections` can accommodate `pool.maxSize × number_of_Apigee_instances`. Alternatively, add a SpikeArrest policy at a lower rate to shed load before it reaches the pool.

---

### JVM SecurityException on TCP connect

Apigee X managed JVM blocked the outbound socket. Use sidecar mode: see [`docs/sidecar/README.md`](../sidecar/README.md).

---

## Next steps

- [Kong Gateway integration guide](../kong/README.md)
- [Azure API Management integration guide](../apim/README.md)
- [Sidecar deployment guide](../sidecar/README.md) — for when embedded mode is not possible
- [Security model](../../SECURITY.md)
- [Contributing](../../CONTRIBUTING.md)

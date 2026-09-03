# gateway-db-mcp

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Java 11+](https://img.shields.io/badge/Java-11%2B-orange.svg)](https://openjdk.org/)
[![Maven Central](https://img.shields.io/maven-central/v/io.github.open-gw/gateway-db-mcp.svg)](https://search.maven.org/artifact/io.github.open-gw/gateway-db-mcp)
[![Build](https://github.com/open-gw/gateway-db-mcp/actions/workflows/build.yml/badge.svg)](https://github.com/open-gw/gateway-db-mcp/actions)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20174425.svg)](https://doi.org/10.5281/zenodo.20174425)
[![Preprint](https://img.shields.io/badge/Preprint-SSRN%206763918-blue)](https://ssrn.com/abstract=6763918)

**Config-driven JDBC database bridge for enterprise API gateway MCP proxies — Apigee X, Kong, Azure APIM. Zero custom code.**

```
AI Agent  →MCP→  [Apigee X / Kong / Azure APIM]  →HTTP→  [gateway-db-mcp]  →JDBC→  MySQL / MariaDB / PostgreSQL / MSSQL
```

---

## Why this exists

Standalone MCP servers ([FreePeak/db-mcp-server](https://github.com/FreePeak/db-mcp-server) and others) connect AI agents directly to databases. For development workflows and local tooling, that is the right choice.

For regulated enterprise environments — healthcare, finance, government — AI agent database access must flow through the same API gateway governance layer that governs every other integration: OAuth 2.1 auth, per-agent rate limiting, immutable audit logs, and table-level access control. Existing database-to-MCP tools ship their own policy plane, which must then be separately configured, audited, and certified for each regulated deployment. **gateway-db-mcp takes the opposite approach: it ships no policy plane at all.** It emits an MCP-annotated OpenAPI specification consumed by a gateway whose controls are already in production and already certified.

Drop a JAR into an Apigee X proxy (or run a Docker sidecar for Kong/APIM), configure your database connection in XML, and your database is an MCP-governed tool endpoint in minutes.

---

## How it works

The project implements a two-layer architecture:

| Layer | Responsibility |
|---|---|
| **Gateway layer** | MCP protocol, OAuth 2.1, rate limiting, OTEL audit logs — handled by native gateway policies |
| **Bridge layer** | JDBC connectivity, schema introspection, query execution, SQL validation, OpenAPI generation |

The bridge exposes five REST endpoints (plus `GET /health` in sidecar mode). The
gateway's MCP proxy imports the `/openapi` output as its tool specification —
no manual spec authoring required.

```
GET  /tables                 →  list_tables         (schema discovery)
GET  /tables/{t}/schema      →  describe_{t}_schema (column types + PKs)
GET  /tables/{t}/rows        →  get_{t}_rows        (paginated, capped)
POST /query                  →  run_query           (parameterized SELECT)
GET  /openapi                →  (gateway config)    (live MCP-annotated spec)
GET  /health                 →  (sidecar only)      (liveness / readiness)
```

### Response shapes (live sidecar output)

Captured against the local bench stack (`curl localhost:8080/...`). Structure is
authoritative; long arrays are truncated with `…`.

```json
GET /health
{"status":"ok"}

GET /tables
{"tables":["customers","orders","products"],"count":3,"database":"testdb"}

GET /tables/orders/schema
{"table":"orders","columns":[{"name":"id","type":"INT","size":10,"nullable":false,"primaryKey":true},{"name":"customer_id","type":"INT","size":10,"nullable":false,"primaryKey":false},{"name":"status","type":"VARCHAR","size":32,"nullable":false,"primaryKey":false},{"name":"total","type":"DECIMAL","size":10,"nullable":false,"primaryKey":false},{"name":"placed_at","type":"TIMESTAMP","size":19,"nullable":true,"primaryKey":false}],"primaryKeys":["id"]}

GET /tables/orders/rows?limit=3
{"table":"orders","limit":3,"offset":0,"rows":[{"id":"1","customer_id":"1","status":"refunded","total":"1952.50","placed_at":null},{"id":"2","customer_id":"34","status":"completed","total":"1297.59","placed_at":null},{"id":"3","customer_id":"15","status":"cancelled","total":"1344.56","placed_at":null}],"count":3}

POST /query  (success)
{"columns":["id","status","total"],"rows":[{"id":"2","status":"completed","total":"1297.59"},{"id":"6","status":"completed","total":"107.15"}],"count":2,"truncated":false}

POST /query  (rejected)
{"error":{"code":"FORBIDDEN","message":"DDL and admin statements are not permitted (DROP, ALTER, CREATE, etc.)"}}

GET /openapi  (prefix)
{"openapi":"3.0.3","info":{"title":"Test DB MCP Bridge — testdb","version":"1.0.0","description":"Auto-generated from live DB schema. Import into Apigee X / Kong / Azure APIM MCP proxy configuration. Source: https://github.com/open-gw/gateway-db-mcp"},"paths":{"/tables":{"get":{"operationId":"list_tables",…}},…}}
```

---

## Quick start — Apigee X (embedded, zero infrastructure)

### 1. Build the shaded JAR

```bash
git clone https://github.com/open-gw/gateway-db-mcp.git
cd gateway-db-mcp
mvn clean package
# JAR auto-copied to apiproxy/resources/java/
```

### 2. Configure your database

Create an Apigee **Property Set** named `db-config`:

```
type         = mysql
host         = 10.0.0.5          # Cloud SQL Private IP or DB hostname
port         = 3306
database     = mydb
username     = readonly_user
allowedTables= orders,products,customers
apiTitle     = My Database
```

Create an Apigee **KVM** (encrypted) named `db-secrets` with key `password`.

Add a `KeyValueMapOperations` policy before the callout to populate `{private.db.password}`.

### 3. Deploy and fetch your MCP spec

```bash
# Deploy the proxy bundle
apigeecli apis create bundle \
  --name gateway-db-mcp \
  --proxy-zip ./apiproxy.zip \
  --org $ORG --env $ENV --token $TOKEN

# Get your MCP-ready OpenAPI spec
curl https://your-org.apigee.net/db-mcp/openapi
```

### 4. Create the Apigee MCP proxy

In the Apigee console: **Develop → API Proxies → + MCP Proxy**
- Basepath: `/mcp/mydb`
- Target: `mcp.apigee.internal`
- OpenAPI spec: paste the `/openapi` output from step 3

Your database is now an MCP tool endpoint. Claude and any MCP-compatible AI agent can call `list_tables`, `get_orders_rows`, `run_query`, and `describe_orders_schema` as governed tools.

---

## Quick start — Sidecar mode (Kong Gateway / Azure APIM)

```bash
# Pull and run
docker pull ghcr.io/open-gw/gateway-db-mcp:latest
docker run -p 8080:8080 \
  -e DB_TYPE=mysql \
  -e DB_HOST=your-db-host \
  -e DB_PORT=3306 \
  -e DB_DATABASE=mydb \
  -e DB_USERNAME=readonly_user \
  -e DB_PASSWORD=yourpassword \
  -e SECURITY_READ_ONLY=true \
  -e SECURITY_ALLOWED_TABLES=orders,products,customers \
  ghcr.io/open-gw/gateway-db-mcp:latest

# Get your spec
curl http://localhost:8080/openapi
```

Then register the sidecar as a backend in your gateway and import the `/openapi` output as the MCP tool specification.

See [Gateway Integration Guides](#gateway-integration) for per-gateway step-by-step instructions.

---

## Configuration reference

All properties are set in `JC-DBBridge.xml` `<Properties>` (embedded mode) or environment variables (sidecar mode). Property name = env var name (uppercased, dots → underscores).

### Database connection

| Property | Env Var | Required | Default | Description |
|---|---|---|---|---|
| `db.type` | `DB_TYPE` | No | `mysql` | `mysql` \| `mariadb` \| `postgres` \| `mssql` |
| `db.host` | `DB_HOST` | **Yes** | — | Hostname or IP |
| `db.port` | `DB_PORT` | No | type-specific | Override default port |
| `db.database` | `DB_DATABASE` | **Yes** | — | Database / catalog name |
| `db.username` | `DB_USERNAME` | **Yes** | — | Database user |
| `db.password` | `DB_PASSWORD` | **Yes** | — | Use KVM ref in Apigee; env var in sidecar |
| `db.schema` | `DB_SCHEMA` | No | — | Schema filter (PostgreSQL / MSSQL) |
| `db.sslMode` | `DB_SSL_MODE` | No | engine default | MySQL: `VERIFY_IDENTITY`; MariaDB: `verify-full`. Override for lab/self-signed (e.g. MySQL `PREFERRED`, MariaDB `trust`). Not applied to PostgreSQL / MSSQL URLs. |

> **Security note:** Never commit passwords to the proxy bundle. In Apigee X, use a KVM reference: `{private.db.password}`. In sidecar mode, use a Kubernetes Secret, AWS Secrets Manager, or Azure Key Vault.

### Connection pool (HikariCP)

| Property | Env Var | Default | Description |
|---|---|---|---|
| `pool.maxSize` | `POOL_MAX_SIZE` | `10` | Max connections |
| `pool.minIdle` | `POOL_MIN_IDLE` | `2` | Min idle connections |
| `pool.connectionTimeout` | `POOL_CONNECTION_TIMEOUT` | `30000` | Timeout in ms |
| `pool.idleTimeout` | `POOL_IDLE_TIMEOUT` | `600000` | Idle eviction in ms |
| `pool.maxLifetime` | `POOL_MAX_LIFETIME` | `1800000` | Max connection lifetime in ms |

> **Pool sizing:** `pool.maxSize × number of gateway instances ≤ database max_connections`. For Cloud SQL, check the instance's `max_connections` setting before deploying.

### Security

| Property | Env Var | Default | Description |
|---|---|---|---|
| `security.readOnly` | `SECURITY_READ_ONLY` | `true` | Allow only `SELECT` via `/query`. **Keep `true` in production.** |
| `security.allowedTables` | `SECURITY_ALLOWED_TABLES` | all | Comma-separated table whitelist. Empty = all accessible tables. |
| `security.maxRows` | `SECURITY_MAX_ROWS` | `1000` | Hard row cap on all operations (1–100,000) |
| `security.queryTimeout` | `SECURITY_QUERY_TIMEOUT` | `30` | Query timeout in seconds (1–300) |

### OpenAPI generation

| Property | Env Var | Default | Description |
|---|---|---|---|
| `api.title` | `API_TITLE` | `DB MCP Bridge` | Title in generated OpenAPI spec |
| `api.version` | `API_VERSION` | `1.0.0` | Version in generated OpenAPI spec |

---

## Security model

Security is enforced in five independent layers. **Layer 1 is mandatory and cannot be bypassed by failure at any other layer.**

| Layer | Control | Where enforced | Bypass risk |
|---|---|---|---|
| **1 — DB credential** | Read-only database user | Database server | None (DB enforces) |
| **2 — Gateway auth** | OAuth 2.1 token validation | Gateway | Token theft |
| **3 — Allowlist** | `allowedTables` filter at JDBC `getTables()` | Bridge | Discovery filter only; **not** enforced against the SQL target in `POST /query` |
| **4 — SQL validator** | Heuristic DDL/write/injection blocking | Bridge | 6 of 47 OWASP payloads bypass; Layer 1 preserves write integrity, confidentiality exposure remains |
| **5 — Resource caps** | `maxRows` + `queryTimeout` | Bridge | None |

> **Critical — read this before deploying against sensitive data.**
> Layer 4 blocked 41 of 47 (87%) OWASP SQL injection payloads in the regression suite. The six
> residual payloads are documented at `src/test/resources/owasp-sqli.txt` and marked `[BYPASS-L4]`.
>
> **Layer 1 is an integrity control, not a confidentiality control.** All six residual payloads
> issue read-only `SELECT` operations, so the mandatory read-only database credential prevents
> every one of them from modifying database state. It does not prevent them from reading data.
> Because `allowedTables` is applied as a discovery filter at `getTables()` and is not enforced
> against the SQL target in `POST /query`, a payload that defeats the Layer 4 heuristic and issues
> a crafted `SELECT` against a non-listed table will execute and return rows.
>
> You **must** provision a read-only database user. The SQL validator is a defence-in-depth layer,
> not a standalone security guarantee. Until the v2.0 Apache Calcite AST validator lands, treat
> `POST /query` as unsuitable for deployments where confidentiality of non-allowlisted tables is a
> requirement. The discovery endpoints (`/tables`, `/tables/{t}/schema`, `/tables/{t}/rows`) are
> not affected by this gap.

### What the validator blocks (unconditionally)

```sql
-- Always blocked regardless of readOnly setting:
DROP TABLE orders;
ALTER TABLE users ADD COLUMN backdoor TEXT;
CREATE USER hacker IDENTIFIED BY 'pw';
TRUNCATE TABLE audit_log;

-- Blocked when readOnly=true:
INSERT INTO orders VALUES (...);
UPDATE users SET role = 'admin';
DELETE FROM sessions;

-- Injection patterns blocked:
SELECT * FROM users; DROP TABLE users; --  (stacked query)
SELECT * FROM users UNION SELECT * FROM passwords  (UNION injection)
```

### Known limitations

The validator uses regex-based heuristics, not a full SQL AST parser. Six payloads in the 47-payload
OWASP regression suite defeat it. They are checked into the repository as expected failures marked
`[BYPASS-L4]` at `src/test/resources/owasp-sqli.txt`, in five categories:

| Category | Count | Example |
|---|---|---|
| MySQL / MariaDB executable comments | 2 | `/*!50000 SELECT */` (also `/*M! … */` on MariaDB) |
| Inline-comment keyword split | 1 | `SEL/**/ECT` |
| Percent-encoded keyword decoded upstream of the bridge | 1 | `%53ELECT` |
| Vendor-specific `HANDLER` statement | 1 | `HANDLER tbl OPEN` |
| MySQL file-read function | 1 | `LOAD_FILE()` |

MariaDB accepts MySQL-style executable comments (`/*! … */`). The documented
`/*!50000 … */` bypass therefore applies to MariaDB as well (MariaDB only ignores
versioned MySQL comments in the `50700..99999` range). MariaDB also supports
`/*M! … */`, which the heuristic does not special-case either.

This is a regression suite against one published payload set. It is **not** a penetration test and
makes no claim of completeness against SQL injection in general. Database-specific syntax outside
the denylist should be assumed to bypass the heuristic.

**Mitigation: always use a read-only database user (Layer 1),** and see the confidentiality caveat
above. An Apache Calcite AST-based validator that resolves query targets against the `allowedTables`
policy is planned for v2.0 (see [Roadmap](#roadmap)).

---

## Supported databases

| Database | JDBC Driver | Default Port | Notes |
|---|---|---|---|
| MySQL 8.x | `com.mysql.cj.jdbc.Driver` | 3306 | Bundled; JDBC URL uses `sslMode=VERIFY_IDENTITY` |
| MariaDB 10.6+ | `org.mariadb.jdbc.Driver` | 3306 | Optional — build with `-Pmariadb` (LGPL-2.1; see [LICENSING.md](LICENSING.md)); JDBC URL uses `sslMode=verify-full` |
| PostgreSQL 14+ | `org.postgresql.Driver` | 5432 | Bundled |
| SQL Server 2019+ | `com.microsoft.sqlserver.jdbc.SQLServerDriver` | 1433 | Bundled; TLS required |

Adding a new database requires a JDBC driver dependency (bundled or optional profile — see [LICENSING.md](LICENSING.md)) and `case` arms in `CalloutConfig.driverClassName()` / `jdbcUrl()`. Oracle's OJDBC JAR is not bundled by default — install instructions are under [Building](#building).

---

## Gateway integration

### Apigee X (embedded)

The `JC-DBBridge.xml` policy is the only file you need to edit. It accepts `{propertyset.*}` references for non-sensitive config and `{private.*}` references for credentials.

```xml
<JavaCallout name="JC-DBBridge">
  <ClassName>io.github.opengw.dbmcp.DBMCPCallout</ClassName>
  <ResourceURL>java://gateway-db-mcp-1.0.0.jar</ResourceURL>
  <Properties>
    <Property name="db.type">{propertyset.db-config.type}</Property>
    <Property name="db.host">{propertyset.db-config.host}</Property>
    <Property name="db.port">{propertyset.db-config.port}</Property>
    <Property name="db.database">{propertyset.db-config.database}</Property>
    <Property name="db.username">{propertyset.db-config.username}</Property>
    <Property name="db.password">{private.db.password}</Property>
    <Property name="security.readOnly">true</Property>
    <Property name="security.allowedTables">{propertyset.db-config.allowedTables}</Property>
    <Property name="security.maxRows">500</Property>
  </Properties>
</JavaCallout>
```

> **JVM sandbox note:** Apigee X managed (Google-hosted) enforces a JVM SecurityManager. Validate outbound TCP socket access to your database host before deploying. Run the included [socket test callout](docs/socket-test/README.md) against your Apigee org first. If socket access is blocked, use sidecar mode instead.

### Kong Gateway 3.12+

```yaml
services:
  - name: db-mcp-mydb
    url: http://gateway-db-mcp-sidecar:8080
    routes:
      - name: mcp-mydb
        paths: ["/mcp/mydb"]
plugins:
  - name: ai-mcp-proxy
    config:
      tools: <paste /openapi output here>
  - name: ai-mcp-oauth2
  - name: rate-limiting
    config:
      minute: 100
      policy: local
```

### Azure API Management

1. Deploy the sidecar (Cloud Run, AKS, or App Service).
2. In APIM: **APIs → + Add API → Import from OpenAPI** → paste the `/openapi` output.
3. Select the imported API → **Expose as MCP server**.
4. Configure subscription key or Entra ID OAuth under **Security**.

---

## Domain-scoped deployment model

Each deployed instance should expose tables from **one application domain only**.

```
/mcp/orders       → orders, order_items, shipments
/mcp/inventory    → products, stock, warehouses
/mcp/customers    → customers, contacts, accounts
```

**Why:** MCP's `tools/list` returns the complete tool manifest at agent connection time. A single endpoint spanning HR, finance, and CRM gives the AI agent a semantically incoherent tool set and widens the security blast radius of a single OAuth credential. The `security.allowedTables` whitelist enforces the domain boundary even when the physical database schema is shared.

API Hub (Apigee) or Kong Konnect's service catalog serves as the enterprise MCP registry — a second tier where orchestrating agents discover which domain endpoint to connect to for a given task.

---

## Performance

**No load-test results are published for this release.** A benchmark harness and measured
latency figures are planned; until they are available, do not use this project for capacity
planning without running load tests against your own deployment configuration.

Two structural properties are worth knowing when you design that test:

- `GET /openapi` performs `O(1 + 2n)` JDBC metadata queries, where `n` is the number of allowed
  tables, so its cost scales with allowlist size. It is called once when the gateway MCP proxy is
  configured, not on every MCP request, so it is not on the hot path.
- The remaining four endpoints issue a single JDBC round trip each against a HikariCP-pooled
  connection. End-to-end latency is dominated by gateway policy execution and database round-trip
  time, both of which are properties of your environment rather than of this bridge.

Contributions of a reproducible benchmark harness are welcome. See [Contributing](CONTRIBUTING.md).

---

## Compliance

### HIPAA

- `security.allowedTables` limits accessible tables to the minimum necessary set.
- OTEL audit logs via `ML-OTELLog.xml` provide the access log trail HIPAA requires (configure 6-year retention in Cloud Logging).
- **Gap 1 — column-level access:** Column-level access control (excluding individual PHI columns
  within accessible tables) is not yet implemented. Do not expose tables containing unrestricted
  PHI until v2.0 column-level ACL is available.
- **Gap 2 — `POST /query` confidentiality:** `allowedTables` is not enforced against the SQL target
  in `POST /query` (see [Security model](#security-model)). Until the v2.0 AST validator lands,
  either disable `POST /query` at the gateway for PHI-bearing deployments or restrict the database
  credential's own grants to the allowlisted tables so the database enforces the boundary.
- Business associate agreements are required for any AI platform (Claude, Azure OpenAI) processing PHI — this is outside the scope of this library.

### GDPR

- Read-only enforcement prevents write operations including erasure requests. GDPR Article 17 (right to erasure) must be handled through separate data management tooling, not through AI agent access.
- Use `security.allowedTables` to exclude tables containing EU personal data unless a legal basis for AI processing is established.

---

## Flow variables (Apigee)

Set on every response and available to downstream policies:

| Variable | Description |
|---|---|
| `dbmcp.operation` | `LIST_TABLES`, `RUN_QUERY`, `DESCRIBE_SCHEMA`, `GET_TABLE_ROWS`, `GENERATE_OPENAPI` |
| `dbmcp.rowCount` | Rows returned (where applicable), `-1` otherwise |
| `dbmcp.error.code` | Machine-readable error code on fault |
| `dbmcp.error.message` | Human-readable error description on fault |

---

## Roadmap

| Version | Feature | Status |
|---|---|---|
| v1.0.1 | Core JDBC bridge, Apigee X embedded mode, Docker sidecar, Kong/APIM guides | ✅ Released ([Zenodo](https://doi.org/10.5281/zenodo.20174426)) |
| v2.0 | Apache Calcite AST-based SQL validator | 🔄 In progress (`/dev/calcite-validator`) |
| v2.0 | Column-level access control for PHI exclusion | 📋 Planned |
| v2.1 | Google Secret Manager native credential resolution | 📋 Planned |
| v2.2 | Schema change webhook → auto `/openapi` refresh | 📋 Planned |
| v3.0 | Oracle and DB2 driver bundles | 📋 Planned |

---

## Project structure

```
gateway-db-mcp/
├── pom.xml                          Maven: shaded JAR, HikariCP, bundled JDBC drivers
├── README.md
├── CONTRIBUTING.md
├── LICENSING.md                     Third-party licence posture (not legal advice)
├── LICENSE                          Apache 2.0
│
├── src/main/java/io/github/opengw/dbmcp/
│   ├── DBMCPCallout.java            Apigee Execution interface — entry point
│   ├── CalloutConfig.java           Property map → typed validated config
│   ├── ConnectionPoolManager.java   HikariCP singleton keyed by config hash
│   ├── OperationRouter.java         HTTP method + path → operation handler
│   ├── security/
│   │   └── QueryValidator.java      Heuristic SQL security (DDL block, injection)
│   └── operations/
│       ├── Operations.java          ListTables, DescribeSchema, GetTableRows, RunQuery
│       └── GenerateOpenAPIOperation.java  Live spec generator with x-mcp-tool annotations
│
├── src/test/java/                   H2 in-memory test suite (no live DB needed)
│
├── apiproxy/                        Drop-in Apigee proxy bundle
│   ├── proxies/default.xml          RouteRule=NoRoute, callout-only proxy
│   └── policies/
│       ├── JC-DBBridge.xml          JavaCallout — the only file you edit
│       └── ML-OTELLog.xml           Structured OTEL → Cloud Trace
│
├── sidecar/
│   ├── Dockerfile
│   ├── docker-compose.yml           With MySQL + PostgreSQL test databases
│   └── server/                      HTTP wrapper for sidecar deployment
│
└── docs/
    ├── socket-test/                 Apigee JVM sandbox validation callout
    ├── kong/                        Kong Gateway step-by-step guide
    ├── apim/                        Azure APIM step-by-step guide
    └── compliance/                  HIPAA and GDPR deployment guidance
```

---

## Building

**Prerequisites:** Java 11+, Maven 3.8+

```bash
# Clone
git clone https://github.com/open-gw/gateway-db-mcp.git
cd gateway-db-mcp

# Build (produces shaded JAR + copies to apiproxy/resources/java/)
mvn clean package

# Include MariaDB Connector/J (LGPL-2.1) in the shaded JAR
mvn clean package -Pmariadb

# Run tests (H2 in-memory, no live DB required)
mvn test

# Build Docker sidecar image (from repository root)
docker build -f sidecar/Dockerfile -t gateway-db-mcp:local .
```

**Adding Oracle support** (licence-restricted JAR, not bundled by default):

```bash
# Install ojdbc11 to local Maven repo (if not resolving from Maven Central)
mvn install:install-file \
  -Dfile=/path/to/ojdbc11.jar \
  -DgroupId=com.oracle.database.jdbc \
  -DartifactId=ojdbc11 \
  -Dversion=21.9.0.0 \
  -Dpackaging=jar

# Package with the Oracle driver shaded in
mvn clean package -Poracle
```

Licence posture for bundled vs optional drivers is recorded in
[LICENSING.md](LICENSING.md).
---

## Relation to standalone DB MCP servers

[FreePeak/db-mcp-server](https://github.com/FreePeak/db-mcp-server) and similar standalone MCP servers connect AI clients **directly to databases**, bypassing API gateways. For development workflows, local tooling, and non-regulated environments, they are simpler and excellent choices.

**gateway-db-mcp serves a different context:** production enterprise deployments where AI agent database access must be governed by the same OAuth authentication, rate limiting, audit logging, and access control policies that govern every other API integration. If you need a governance layer, use this. If you don't, FreePeak may be what you want.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Priority areas:

- Apache Calcite AST validator (`/dev/calcite-validator` branch)
- Column-level ACL configuration model
- Oracle / DB2 driver installation guides
- Additional gateway integration guides (AWS API Gateway, Nginx)
- Performance benchmarks against PostgREST / Hasura baselines

All PRs require the H2 test suite to pass. For new database support, add corresponding H2-compatible test cases.

---

## Citing this work

If you use gateway-db-mcp in research, please cite the archived software release:

```bibtex
@software{dhanaraj2026gatewaydbmcp,
  author    = {Dhanaraj, Rinu},
  title     = {{GatewayDB-MCP}: A Configuration-Driven Bridge from {JDBC} Databases
               to {MCP} Tool Endpoints in Enterprise {API} Gateways},
  year      = {2026},
  publisher = {Zenodo},
  version   = {v1.0.2},
  doi       = {10.5281/zenodo.20174425},
  url       = {https://doi.org/10.5281/zenodo.20174425}
}
```

A companion preprint is available on SSRN (Abstract ID 6763918, DOI 10.2139/ssrn.6763918):
<https://ssrn.com/abstract=6763918>.

**No peer-reviewed version of this work has been published.** Please cite the Zenodo archive above.
If a peer-reviewed version is published in future, this section will be updated and the preferred
citation changed accordingly.

---

## License

[Apache 2.0](LICENSE) — free to use, modify, and distribute. Attribution appreciated.

---

## Acknowledgements

Built on [HikariCP](https://github.com/brettwooldridge/HikariCP) for connection pooling and the [Apigee Edge Java Callout SDK](https://cloud.google.com/apigee/docs/api-platform/develop/java-callout). Gateway MCP proxy capabilities provided by [Apigee X](https://cloud.google.com/apigee), [Kong Gateway 3.12](https://konghq.com), and [Azure API Management](https://azure.microsoft.com/en-us/products/api-management).

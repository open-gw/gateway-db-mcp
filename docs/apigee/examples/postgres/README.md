# PostgreSQL Example — Apigee X

Copy-paste configuration for **PostgreSQL 14+** on Cloud SQL or any PostgreSQL instance reachable from Apigee X.

## Files in this example

| File | Copy to | Purpose |
|---|---|---|
| `JC-DBBridge-Postgres.xml` | `apiproxy/policies/JC-DBBridge.xml` | Main callout — edit schema and table list here |
| `KVM-GetDBPassword.xml` | `apiproxy/policies/KVM-GetDBPassword.xml` | Loads password from encrypted KVM |

Use the same `default.xml` from the [MySQL example](../mysql/default.xml) — the proxy wiring is identical.

## 5-minute setup

**1. Create a read-only PostgreSQL user:**
```sql
-- Connect to your database first
\c your_database

CREATE USER gateway_readonly WITH PASSWORD 'YOUR_PASSWORD';
GRANT CONNECT ON DATABASE your_database TO gateway_readonly;
GRANT USAGE ON SCHEMA public TO gateway_readonly;

-- Grant on specific tables only (recommended):
GRANT SELECT ON TABLE orders, products, customers TO gateway_readonly;
```

**2. Create the Apigee Property Set** — name: `db-config-postgres`

```
type          = postgres
host          = 10.x.x.x        ← Cloud SQL Private IP
port          = 5432
database      = your_database
schema        = public           ← PostgreSQL schema name
username      = gateway_readonly
allowedTables = orders,products,customers
apiTitle      = Your Domain
```

**3. Create the Apigee KVM** — name: `db-secrets`, encrypted: YES

```
key: password    value: YOUR_PASSWORD
```

**4. Build and deploy:**
```bash
mvn clean package
# Deploy via apigeecli or Apigee Console
```

**5. Test:**
```bash
TOKEN=$(gcloud auth print-access-token)
curl -H "Authorization: Bearer $TOKEN" \
  https://YOUR_ORG.apigee.net/db-mcp/tables
```

---

## PostgreSQL schema scoping — the key differentiator from MySQL

Unlike MySQL, PostgreSQL organises tables into **schemas** within a database. The `db.schema` property filters all JDBC metadata queries to a single schema, making it powerful for multi-tenant and multi-domain deployments.

### Pattern 1 — Default public schema (simplest)

```
database = analytics_db
schema   = public
```

All tables in the `public` schema are accessible (subject to `allowedTables`).

### Pattern 2 — Dedicated reporting schema

```
database = main_db
schema   = reporting
```

Useful when you have a `reporting` schema with curated, denormalized views designed for read access. The AI agent only sees the reporting schema — the operational `public` schema with raw tables is completely invisible.

```sql
-- Create and populate the reporting schema
CREATE SCHEMA reporting;

-- Create read-optimized views for AI access
CREATE VIEW reporting.order_summary AS
  SELECT o.id, c.name AS customer, o.total, o.status, o.created_at
  FROM public.orders o
  JOIN public.customers c ON o.customer_id = c.id;

-- Grant access only to the reporting schema
GRANT USAGE ON SCHEMA reporting TO gateway_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO gateway_readonly;
```

This is the recommended pattern for production — the AI agent queries purpose-built views, never the raw operational tables.

### Pattern 3 — Dedicated AI access schema

```
database = main_db
schema   = ai_access
```

Create a dedicated schema with views that explicitly control what the AI agent can see:
- Exclude PII columns by not selecting them in the view
- Pre-aggregate sensitive data (e.g., expose total_revenue but not individual salaries)
- Enforce business logic (e.g., only show completed orders, not draft ones)

```sql
CREATE SCHEMA ai_access;

-- PII-safe customer view (no email, no phone)
CREATE VIEW ai_access.customers AS
  SELECT id, company_name, industry, region, tier
  FROM public.customers;

-- Aggregate financials (no individual transaction amounts)
CREATE VIEW ai_access.revenue_by_region AS
  SELECT region, date_trunc('month', created_at) AS month,
         SUM(total) AS total_revenue, COUNT(*) AS order_count
  FROM public.orders
  GROUP BY region, date_trunc('month', created_at);

GRANT USAGE ON SCHEMA ai_access TO gateway_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA ai_access TO gateway_readonly;
```

---

## PostgreSQL type mapping

| PostgreSQL type | JSON Schema type | Notes |
|---|---|---|
| `BOOLEAN` | `boolean` | Native boolean — correct (unlike MySQL TINYINT(1)) |
| `SMALLINT`, `INTEGER` | `integer` | |
| `BIGINT`, `SERIAL`, `BIGSERIAL` | `integer` | |
| `NUMERIC`, `DECIMAL` | `number` | Precision/scale not in schema |
| `REAL`, `DOUBLE PRECISION` | `number` | |
| `VARCHAR`, `TEXT`, `CHAR` | `string` | |
| `TIMESTAMP` | `string` | format: date-time — timezone-naive |
| `TIMESTAMPTZ` | `string` | format: date-time — timezone-aware |
| `DATE` | `string` | format: date |
| `UUID` | `string` | Values are UUID strings |
| `JSONB`, `JSON` | `string` | JSON structure not in schema — use views |
| `TEXT[]`, `INTEGER[]` | `array` | Element type not resolved |
| `ENUM` (user-defined) | `string` | Allowed values not in schema |
| `BYTEA` | `string` | format: byte (base64) |

---

## Pool sizing for Cloud SQL PostgreSQL

PostgreSQL uses more memory per connection than MySQL. Cloud SQL PostgreSQL `max_connections` is lower for equivalent machine types.

| Cloud SQL tier | max_connections | Safe pool.maxSize (10 Apigee instances) |
|---|---|---|
| db-f1-micro | 25 | 2 |
| db-g1-small | 100 | 9 |
| db-n1-standard-1 | 100 | 9 |
| db-n1-standard-2 | 200 | 19 |
| db-n1-standard-4 | 400 | 39 |
| db-n1-standard-8 | 800 | 79 |

> **PgBouncer:** For high-concurrency deployments, consider running PgBouncer between Apigee and Cloud SQL PostgreSQL. PgBouncer in transaction-mode pooling multiplexes many Apigee connections over a small number of PostgreSQL server connections, effectively removing the `max_connections` constraint.

---

## See also

- [MySQL example](../mysql/README.md)
- [Full Apigee X integration guide](../../README.md)
- [Troubleshooting](../../README.md#troubleshooting)

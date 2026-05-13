# MySQL Example — Apigee X

Copy-paste configuration for **MySQL 8.x** on Cloud SQL or any MySQL instance reachable from Apigee X.

## Files in this example

| File | Copy to | Purpose |
|---|---|---|
| `JC-DBBridge-MySQL.xml` | `apiproxy/policies/JC-DBBridge.xml` | Main callout policy — edit your table list here |
| `KVM-GetDBPassword.xml` | `apiproxy/policies/KVM-GetDBPassword.xml` | Loads password from encrypted KVM |
| `default.xml` | `apiproxy/proxies/default.xml` | Proxy endpoint wiring |

## 5-minute setup

**1. Create a read-only MySQL user:**
```sql
CREATE USER 'gateway_readonly'@'%' IDENTIFIED BY 'YOUR_PASSWORD';
GRANT SELECT ON your_db.orders   TO 'gateway_readonly'@'%';
GRANT SELECT ON your_db.products TO 'gateway_readonly'@'%';
FLUSH PRIVILEGES;
```

**2. Create the Apigee Property Set** — name: `db-config-mysql`

```
type          = mysql
host          = 10.x.x.x        ← Cloud SQL Private IP
port          = 3306
database      = your_database
username      = gateway_readonly
allowedTables = orders,products,customers
apiTitle      = Your Domain
```

**3. Create the Apigee KVM** — name: `db-secrets`, encrypted: YES

```
key: password    value: YOUR_PASSWORD
```

**4. Copy the three XML files into your proxy bundle, build, deploy:**
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

**6. Get your MCP spec:**
```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://YOUR_ORG.apigee.net/db-mcp/openapi > spec.json
# Import spec.json into your Apigee MCP proxy
```

## MySQL-specific notes

| Behaviour | Detail |
|---|---|
| SSL | Enabled by default (`useSSL=true` in JDBC URL) |
| TINYINT(1) | Maps to `integer` (0/1) — not `boolean`. MySQL convention. |
| JSON columns | Maps to `string` — JSON structure not reflected in schema |
| DATETIME | Maps to `string/date-time` — timezone-naive |
| TIMESTAMP | Maps to `string/date-time` — timezone-aware |
| db.schema | Not required — MySQL uses db.database as the catalog scope |

## Pool sizing for Cloud SQL MySQL

| Cloud SQL tier | max_connections | Safe pool.maxSize (10 Apigee instances) |
|---|---|---|
| db-f1-micro | 25 | 2 |
| db-g1-small | 100 | 9 |
| db-n1-standard-1 | 200 | 19 |
| db-n1-standard-2 | 400 | 39 |
| db-n1-standard-4 | 1,000 | 99 |

Formula: `pool.maxSize = floor((max_connections - 5) / apigee_instances)`

## See also

- [PostgreSQL example](../postgres/README.md)
- [Full Apigee X integration guide](../../README.md)
- [Troubleshooting](../../README.md#troubleshooting)

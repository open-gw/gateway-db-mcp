# MariaDB Example — Apigee X

Use this configuration for **MariaDB 10.6+** instances reachable from Apigee X.
The proxy wiring is identical to the [MySQL example](../mysql/README.md); only the
database type and JDBC driver differ.

## 5-minute setup

**1. Create a read-only MariaDB user:**

```sql
CREATE USER 'gateway_readonly'@'%' IDENTIFIED BY 'YOUR_PASSWORD';
GRANT SELECT ON your_db.orders   TO 'gateway_readonly'@'%';
GRANT SELECT ON your_db.products TO 'gateway_readonly'@'%';
FLUSH PRIVILEGES;
```

**2. Create the Apigee Property Set** — name: `db-config-mariadb`

```text
type          = mariadb
host          = 10.x.x.x
port          = 3306
database      = your_database
username      = gateway_readonly
allowedTables = orders,products,customers
apiTitle      = Your Domain
```

**3. Create the Apigee KVM** — name: `db-secrets`, encrypted: YES

```text
key: password    value: YOUR_PASSWORD
```

**4. Copy the proxy files from the MySQL example and update the property set references:**

- Copy `../mysql/JC-DBBridge-MySQL.xml` to your proxy as `JC-DBBridge.xml`.
- Replace `db-config-mysql` with `db-config-mariadb` in the copied policy.
- Reuse `../mysql/KVM-GetDBPassword.xml` and `../mysql/default.xml` unchanged.

**5. Build and test the proxy:**

```bash
# MariaDB Connector/J is LGPL-2.1 — not in the default shaded JAR.
mvn clean package -Pmariadb
TOKEN=$(gcloud auth print-access-token)
curl -H "Authorization: Bearer $TOKEN" \
  https://YOUR_ORG.apigee.net/db-mcp/tables
```

## MariaDB-specific notes

| Behaviour | Detail |
|---|---|
| JDBC URL | `jdbc:mariadb://host:3306/database?sslMode=verify-full` |
| Driver | `org.mariadb.jdbc.Driver` |
| SSL | `sslMode=verify-full` by default (certificate + hostname verification). For lab/self-signed hosts set `db.sslMode=trust` / `DB_SSL_MODE=trust`. |
| Default port | 3306 |
| Build | `mvn clean package -Pmariadb` (see [LICENSING.md](../../../LICENSING.md)) |
| `db.schema` | Not required — MariaDB uses `db.database` as the catalog scope |

## See also

- [MySQL example](../mysql/README.md)
- [Full Apigee X integration guide](../../README.md)
- [Troubleshooting](../../README.md#troubleshooting)

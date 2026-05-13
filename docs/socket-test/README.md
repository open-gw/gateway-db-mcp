# Socket Test — Apigee X JVM Validation

Run this **before** deploying gateway-db-mcp to confirm the Apigee X JVM sandbox allows outbound TCP connections to your database host. This is the go/no-go gate for embedded mode.

## Why this matters

Apigee X managed environments enforce a JVM SecurityManager. Whether outbound TCP to your database host is permitted depends on your VPC peering configuration and Google's runtime policies. A failed socket test means you need sidecar mode — discovering this after deploying gateway-db-mcp wastes significant time.

## Deploy and run

**Step 1 — Compile the test callout:**
```bash
cd docs/socket-test

# Compile against Apigee stubs (run install-apigee-stubs.sh first)
javac -cp ~/.m2/repository/com/apigee/edge/message-flow/1.0.0/message-flow-1.0.0.jar \
  SocketTestCallout.java

jar cf socket-test-1.0.0.jar io/
```

**Step 2 — Create a minimal Apigee proxy:**

Create a new proxy in the Apigee Console with:
- Basepath: `/socket-test`
- RouteRule: NoRoute
- Policies folder: copy `JC-SocketTest.xml`
- Resources/java: upload `socket-test-1.0.0.jar`

Wire the policy to a pass-through flow.

**Step 3 — Call the test endpoint:**
```bash
TOKEN=$(gcloud auth print-access-token)

# Test MySQL on default port
curl "https://YOUR_ORG.apigee.net/socket-test?host=YOUR_DB_IP&port=3306" \
  -H "Authorization: Bearer $TOKEN"

# Test PostgreSQL
curl "https://YOUR_ORG.apigee.net/socket-test?host=YOUR_DB_IP&port=5432" \
  -H "Authorization: Bearer $TOKEN"
```

## Interpreting results

**✅ Embedded mode is viable:**
```json
{
  "result": "success",
  "host": "10.20.30.40",
  "port": 3306,
  "message": "TCP connection succeeded — embedded mode will work"
}
```

**❌ Use sidecar mode instead:**
```json
{
  "result": "failed",
  "error": "SecurityException",
  "message": "JVM sandbox blocked TCP connection — use sidecar mode"
}
```

**❌ Network/routing issue (fixable):**
```json
{
  "result": "failed",
  "error": "ConnectException",
  "message": "Connection refused / Connection timed out"
}
```
A `ConnectException` means the JVM attempted the connection but the database is unreachable. Check VPC peering, Cloud SQL Private IP configuration, and firewall rules. This is different from a `SecurityException` (JVM blocked before attempting).

## If you get SecurityException

Use sidecar mode — see [`docs/sidecar/README.md`](../sidecar/README.md) and [`docs/apigee/README.md`](../apigee/README.md#troubleshooting).

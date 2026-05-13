#!/usr/bin/env bash
# scripts/setup-github.sh
# ════════════════════════════════════════════════════════════════════════════════
# Creates GitHub labels and seed issues for gateway-db-mcp.
# Run once after making the repo public.
#
# Prerequisites:
#   brew install gh        (macOS)
#   gh auth login          (authenticate once)
#
# Usage:
#   chmod +x scripts/setup-github.sh
#   ./scripts/setup-github.sh
# ════════════════════════════════════════════════════════════════════════════════

set -e

REPO="open-gw/gateway-db-mcp"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   gateway-db-mcp — GitHub Setup                 ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Verify gh CLI is installed and authenticated ─────────────────────────────
if ! command -v gh &>/dev/null; then
  echo "ERROR: GitHub CLI not found. Install with: brew install gh"
  exit 1
fi

if ! gh auth status &>/dev/null; then
  echo "ERROR: Not authenticated. Run: gh auth login"
  exit 1
fi

echo "✓ GitHub CLI authenticated"
echo "  Repository: $REPO"
echo ""

# ── Create Labels ────────────────────────────────────────────────────────────
echo "Creating labels..."

create_label() {
  local name="$1" color="$2" desc="$3"
  gh label create "$name" \
    --repo "$REPO" \
    --color "$color" \
    --description "$desc" \
    --force \
    2>/dev/null && echo "  ✓ $name" || echo "  ~ $name (already exists)"
}

# Triage
create_label "bug"              "d73a4a" "Something is not working as documented"
create_label "enhancement"      "a2eeef" "New feature or improvement"
create_label "question"         "d876e3" "Further information requested"
create_label "documentation"    "0075ca" "Improvements or additions to documentation"
create_label "duplicate"        "cfd3d7" "This issue or PR already exists"
create_label "wontfix"          "ffffff" "This will not be worked on"

# Contributor onboarding
create_label "good first issue" "7057ff" "Good for newcomers — well-scoped and documented"
create_label "help wanted"      "008672" "Extra attention is needed — community welcome"

# Priority
create_label "priority: high"   "e11d48" "Needs to be addressed in the next sprint"
create_label "priority: medium" "f59e0b" "Important but not blocking"
create_label "priority: low"    "6b7280" "Nice to have"

# Security
create_label "security"         "b91c1c" "Security-related — may need private disclosure"

# Database
create_label "database: mysql"      "00758f" "MySQL-specific behaviour or bug"
create_label "database: postgres"   "336791" "PostgreSQL-specific behaviour or bug"
create_label "database: mssql"      "cc2927" "MSSQL-specific behaviour or bug"
create_label "database: oracle"     "c74634" "Oracle DB support (planned)"
create_label "database: mariadb"    "003545" "MariaDB support (planned)"

# Gateway
create_label "gateway: apigee"  "4285f4" "Apigee X embedded or proxy bundle"
create_label "gateway: kong"    "003459" "Kong Gateway integration"
create_label "gateway: apim"    "0078d4" "Azure API Management integration"

# Components
create_label "component: security"  "fca5a5" "QueryValidator or security model"
create_label "component: openapi"   "86efac" "OpenAPI spec generation"
create_label "component: pool"      "fde68a" "HikariCP connection pool"
create_label "component: config"    "c4b5fd" "CalloutConfig or property parsing"
create_label "component: sidecar"   "fed7aa" "Docker sidecar mode"

# Meta
create_label "dependencies"     "0366d6" "Dependency version update"
create_label "java"             "f89820" "Java source changes"
create_label "github-actions"   "2088ff" "CI/CD workflow changes"
create_label "docker"           "0db7ed" "Docker or container changes"

echo ""
echo "✓ Labels created"

# ── Create Seed Issues ───────────────────────────────────────────────────────
echo ""
echo "Creating seed issues..."

# Issue 1 — Health endpoint (Easy, good first issue)
gh issue create \
  --repo "$REPO" \
  --title "feat: add GET /health endpoint for Kubernetes liveness probes" \
  --label "good first issue,enhancement,component: sidecar,gateway: apigee,priority: high" \
  --body "## Summary

Add a \`GET /health\` endpoint that returns the HikariCP connection pool status, enabling Kubernetes liveness and readiness probes for sidecar deployments.

## Motivation

Currently there is no way for Kubernetes or Cloud Run to determine if the bridge is healthy. Without a health endpoint, container orchestrators cannot restart unhealthy instances automatically.

## Expected behaviour

**Healthy response (HTTP 200):**
\`\`\`json
{
  \"status\": \"UP\",
  \"pool\": {
    \"active\": 2,
    \"idle\": 8,
    \"waiting\": 0,
    \"max\": 10
  },
  \"database\": \"orders_db\",
  \"dbType\": \"mysql\"
}
\`\`\`

**Unhealthy response (HTTP 503):**
\`\`\`json
{
  \"status\": \"DOWN\",
  \"error\": \"Cannot acquire JDBC connection\",
  \"pool\": {
    \"active\": 0,
    \"idle\": 0,
    \"waiting\": 5,
    \"max\": 10
  }
}
\`\`\`

## Acceptance criteria

- \`GET /health\` returns 200 with pool stats when DB is reachable
- \`GET /health\` returns 503 when DB is unreachable
- Response time < 100ms (uses pool stats, not a live DB ping)
- \`OperationRouter\` routes \`GET /health\` to a new \`HealthOperation\` class
- Unit test covering healthy and unhealthy states using Mockito
- Documented in \`docs/apigee/README.md\` and \`README.md\`

## Implementation hints

- Add \`P_HEALTH = Pattern.compile(\"^/health/?$\", ...)\` to \`OperationRouter\`
- Create \`HealthOperation.java\` in the \`operations\` package
- Use \`HikariDataSource.getHikariPoolMXBean()\` to get pool stats without a DB round-trip
- For Apigee embedded mode, expose through the existing proxy path suffix routing" \
  2>/dev/null && echo "  ✓ Issue 1: Health endpoint" || echo "  ~ Issue 1 skipped"

# Issue 2 — MariaDB support (Easy, good first issue)
gh issue create \
  --repo "$REPO" \
  --title "feat: add MariaDB driver support" \
  --label "good first issue,enhancement,database: mariadb,component: config,priority: medium" \
  --body "## Summary

Add MariaDB as a supported \`db.type\` value. MariaDB uses a compatible but distinct JDBC driver from MySQL and has subtle behavioural differences.

## Motivation

MariaDB is widely deployed in enterprises, particularly in on-premise and private cloud environments. Many teams running Apigee X on-premise also run MariaDB. Currently setting \`db.type=mysql\` with a MariaDB instance works partially but is not officially supported or tested.

## What needs to change

**\`CalloutConfig.java\`:**
\`\`\`java
// Add to jdbcUrl():
case \"mariadb\":
    return String.format(
        \"jdbc:mariadb://%s:%d/%s?useSSL=true\",
        host, port, database);

// Add to driverClassName():
case \"mariadb\": return \"org.mariadb.jdbc.Driver\";

// Add to defaultPort():
case \"mariadb\": return 3306;

// Add to validate():
Set<String> valid = new HashSet<>(Arrays.asList(\"mysql\", \"postgres\", \"mssql\", \"mariadb\"));
\`\`\`

**\`pom.xml\`:**
\`\`\`xml
<dependency>
    <groupId>org.mariadb.jdbc</groupId>
    <artifactId>mariadb-java-client</artifactId>
    <version>3.3.3</version>
</dependency>
\`\`\`

## Acceptance criteria

- \`db.type=mariadb\` is accepted without validation error
- JDBC URL uses \`jdbc:mariadb://\` prefix
- \`org.mariadb.jdbc.Driver\` is the driver class
- \`CalloutConfigTest\` covers mariadb URL and driver class assertions
- \`docs/apigee/examples/\` includes a \`mariadb/\` example directory with \`README.md\`
- \`README.md\` supported databases table updated

## Resources

- [MariaDB Connector/J docs](https://mariadb.com/kb/en/mariadb-connector-j/)
- [Maven Central: mariadb-java-client](https://central.sonatype.com/artifact/org.mariadb.jdbc/mariadb-java-client)" \
  2>/dev/null && echo "  ✓ Issue 2: MariaDB support" || echo "  ~ Issue 2 skipped"

# Issue 3 — H2 integration tests (Medium)
gh issue create \
  --repo "$REPO" \
  --title "test: add H2 in-memory integration test suite covering all 5 endpoints" \
  --label "help wanted,enhancement,priority: high" \
  --body "## Summary

Add an integration test suite that exercises all five bridge endpoints (\`/tables\`, \`/tables/{t}/schema\`, \`/tables/{t}/rows\`, \`/query\`, \`/openapi\`) against an H2 in-memory database without requiring a live database instance.

## Motivation

The current test suite covers unit logic (config parsing, routing, SQL validation) but does not test the full JDBC execution path. An H2-based integration test would catch issues in \`ListTablesOperation\`, \`DescribeSchemaOperation\`, \`GetTableRowsOperation\`, \`RunQueryOperation\`, and \`GenerateOpenAPIOperation\` before they reach production.

## Approach

Use H2 with the \`MODE=MySQL\` compatibility layer and a test schema defined in \`src/test/resources/test-schema.sql\`.

\`\`\`java
// BridgeIntegrationTest.java — skeleton
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class BridgeIntegrationTest {
    private HikariDataSource ds;
    private CalloutConfig cfg;

    @BeforeAll void setup() {
        // Configure H2 in MySQL compatibility mode
        // Create test tables: orders, products, customers
        // Seed with known data
    }

    @Test void list_tables_returns_allowed_tables() { ... }
    @Test void describe_schema_returns_column_definitions() { ... }
    @Test void get_rows_respects_max_rows_cap() { ... }
    @Test void run_query_with_bind_params_returns_filtered_rows() { ... }
    @Test void generate_openapi_includes_all_allowed_tables() { ... }
    @Test void table_not_in_allowlist_returns_403() { ... }
    @Test void write_query_blocked_in_read_only_mode() { ... }
}
\`\`\`

## Acceptance criteria

- All 5 operations tested via real JDBC execution against H2
- Both happy paths and error paths covered
- Tests run with \`mvn test\` — no external dependencies
- Test schema defined in \`src/test/resources/test-schema.sql\`
- JaCoCo coverage gate satisfied (currently 70% line coverage minimum)
- Existing 41 tests continue to pass" \
  2>/dev/null && echo "  ✓ Issue 3: H2 integration tests" || echo "  ~ Issue 3 skipped"

# Issue 4 — Oracle driver guide (Easy docs, good first issue)
gh issue create \
  --repo "$REPO" \
  --title "docs: Oracle DB installation guide (ojdbc11 is licence-restricted)" \
  --label "good first issue,documentation,database: oracle,priority: medium" \
  --body "## Summary

Create \`docs/apigee/examples/oracle/README.md\` documenting how to install the Oracle JDBC driver (ojdbc11) and configure \`db.type=oracle\`. The ojdbc11 JAR has a licence restriction that prevents bundling it in the shaded JAR, so users must install it manually.

## Why this can't be automated

Oracle's JDBC driver is not on Maven Central. It is available from Oracle's Maven repository but requires accepting Oracle's licence terms. The shaded JAR cannot include it without violating redistribution terms.

## What the guide should cover

1. Download ojdbc11 from the [Oracle Maven repository](https://www.oracle.com/database/technologies/maven-central-guide.html) or [Maven Central (unofficial)](https://mvnrepository.com/artifact/com.oracle.database.jdbc/ojdbc11)
2. Install to local Maven repo:
   \`\`\`bash
   mvn install:install-file \\
     -Dfile=/path/to/ojdbc11.jar \\
     -DgroupId=com.oracle.database.jdbc \\
     -DartifactId=ojdbc11 \\
     -Dversion=21.9.0.0 \\
     -Dpackaging=jar
   \`\`\`
3. Uncomment the Oracle section in \`pom.xml\` and rebuild
4. Sample \`JC-DBBridge-Oracle.xml\` with Oracle-specific JDBC URL format:
   \`jdbc:oracle:thin:@//{host}:{port}/{service_name}\`

## Files to create

- \`docs/apigee/examples/oracle/README.md\`
- \`docs/apigee/examples/oracle/JC-DBBridge-Oracle.xml\`
- \`docs/apigee/examples/oracle/KVM-GetDBPassword.xml\` (copy from mysql example)

## Acceptance criteria

- Guide covers both Oracle Database on-premise and Oracle Autonomous Database
- pom.xml has a commented-out Oracle dependency section ready to enable
- README notes the licence restriction clearly at the top" \
  2>/dev/null && echo "  ✓ Issue 4: Oracle driver guide" || echo "  ~ Issue 4 skipped"

# Issue 5 — Calcite SQL validator (Hard, high value)
gh issue create \
  --repo "$REPO" \
  --title "feat: replace heuristic QueryValidator with Apache Calcite AST parser" \
  --label "enhancement,component: security,priority: high,help wanted" \
  --body "## Summary

Replace the current regex-based \`QueryValidator\` with an Apache Calcite AST-based SQL parser. This eliminates the 13% OWASP bypass gap caused by MySQL conditional comments and Unicode normalization bypasses.

## Current limitation

The heuristic validator blocked 41/47 (87%) OWASP SQLi payloads. The 6 unblocked payloads bypass regex detection via:
- MySQL conditional comments: \`/*!50000 SELECT */\`
- Unicode normalization on keyword spelling
- Database-specific syntax not in the denylist (\`HANDLER\`, \`OPENROWSET\`)

These are currently mitigated by Layer 1 (read-only DB user) but a full AST parser would eliminate the residual risk entirely.

## Approach

Apache Calcite provides a standards-compliant SQL parser that produces an AST. Validating the AST is safer than pattern matching on the raw string because it is syntax-aware.

\`\`\`java
// Sketch of Calcite-based validation
SqlParser parser = SqlParser.create(sql,
    SqlParser.config().withLex(Lex.MYSQL));
SqlNode node = parser.parseQuery();

// Walk the AST to detect DML/DDL nodes
node.accept(new SqlBasicVisitor<Void>() {
    @Override public Void visit(SqlCall call) {
        if (call.getKind() == SqlKind.INSERT  ||
            call.getKind() == SqlKind.UPDATE  ||
            call.getKind() == SqlKind.DELETE  ||
            call.getKind() == SqlKind.DROP    ||
            call.getKind() == SqlKind.ALTER_TABLE) {
            throw new SecurityException(\"Write/DDL not permitted\");
        }
        return super.visit(call);
    }
});
\`\`\`

## Branch

Work in progress in \`/dev/calcite-validator\`. Check that branch before starting.

## Acceptance criteria

- All 47/47 OWASP SQLi payloads blocked (up from 41/47)
- No regression on legitimate SELECT queries (existing test suite passes)
- \`OWASPValidatorTest\` interception floor raised from 85% to 100%
- Calcite dependency added to \`pom.xml\` with appropriate shading
- Backwards compatible \`CalloutConfig\` property (\`security.validatorMode=calcite|heuristic\`)
- Javadoc updated" \
  2>/dev/null && echo "  ✓ Issue 5: Calcite validator" || echo "  ~ Issue 5 skipped"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   Setup complete                                         ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║   Labels created: 27                                     ║"
echo "║   Seed issues opened: 5                                  ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║   Now do these manually in GitHub (5 min):               ║"
echo "║                                                          ║"
echo "║   1. Enable Discussions:                                 ║"
echo "║      Settings → Features → ✅ Discussions               ║"
echo "║                                                          ║"
echo "║   2. Enable secret scanning:                             ║"
echo "║      Settings → Security → ✅ Secret scanning           ║"
echo "║                                                          ║"
echo "║   3. Branch protection (after repo goes public):         ║"
echo "║      Settings → Branches → main                         ║"
echo "║      ✅ Require PR  ✅ Code owners  ✅ Status checks    ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

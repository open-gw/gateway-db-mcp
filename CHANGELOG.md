# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

_Changes not yet released appear here._

---

## [1.2.0] — 2026-09-03

### Added

- Optional Maven profiles `-Pmariadb` and `-Poracle` for drivers that are not bundled by default
- MariaDB support (`db.type=mariadb`, `jdbc:mariadb://`, Apigee example, bench E4 third engine)
- Root `LICENSING.md` recording third-party licence posture (not legal advice)
- `NOTICE` embedded in the shaded JAR for Apache 2.0 §4(d) binary disclosure
- Clear startup failure when an optional JDBC driver is absent from the classpath
- `db.sslMode` / `DB_SSL_MODE` override for MySQL and MariaDB TLS mode

### Changed

- MySQL JDBC URL defaults to `sslMode=VERIFY_IDENTITY`; MariaDB to `sslMode=verify-full`
- Generated Apigee callout JAR is no longer tracked in git; `mvn package` is required before Apigee deploy
- `CONTRIBUTING.md` distinguishes copyleft-with-exception (bundleable) from LGPL without equivalent permission

### Security

- Stated default of certificate + hostname verification for MySQL/MariaDB connections; see `SECURITY.md`

---

## [1.0.0] — 2026-05-12

### Added

- `DBMCPCallout` — Apigee X Java Callout entry point implementing `com.apigee.flow.execution.spi.Execution`
- `CalloutConfig` — typed, validated configuration parsed from XML `<Properties>` block or environment variables; supports embedded and sidecar deployment parity
- `ConnectionPoolManager` — HikariCP singleton pool management keyed by `(dbType, host, port, database, username)` with thread-safe `ConcurrentHashMap`
- `OperationRouter` — regex-based HTTP method + path routing to five operation handlers
- `ListTablesOperation` — `GET /tables` returning allowlist-filtered table names
- `DescribeSchemaOperation` — `GET /tables/{t}/schema` with JDBC `DatabaseMetaData` introspection
- `GetTableRowsOperation` — `GET /tables/{t}/rows` with pagination, orderBy, and `maxRows` cap
- `RunQueryOperation` — `POST /query` with parameterized `PreparedStatement` and bind parameter array
- `GenerateOpenAPIOperation` — `GET /openapi` emitting live MCP-annotated OpenAPI 3.0.3 specification with `x-mcp-tool` extensions compatible with Apigee X, Kong 3.12, and Azure APIM
- `QueryValidator` — heuristic SQL security layer: DDL block, write block (readOnly mode), stacked query detection, UNION injection detection, identifier regex validation; 41/47 OWASP SQLi payloads blocked at Layer 4
- MySQL 8.x, PostgreSQL 14+, MSSQL 2019+ support via bundled JDBC drivers (shaded JAR)
- Apigee proxy bundle (`apiproxy/`) with `JC-DBBridge.xml` policy and `ML-OTELLog.xml` OTEL logging
- Docker sidecar (`sidecar/Dockerfile`, `docker-compose.yml`) with MySQL seed data for local development
- GitHub Actions CI workflow (build + test on every push and PR)
- `scripts/install-apigee-stubs.sh` — installs minimal Apigee JAR stubs for local development
- Test suite: 41 tests across `CalloutConfigTest`, `QueryValidatorTest`, `OperationRouterTest`
- OWASP SQLi test payload file (`src/test/resources/owasp-sqli.txt`)

### Security

- Read-only enforcement: DDL blocked unconditionally; DML blocked when `security.readOnly=true`
- `allowedTables` whitelist enforced at JDBC `getTables()` level
- Row cap via `security.maxRows` and query abort via `security.queryTimeout`
- Credential isolation: `poolKey()` excludes password; KVM/Secret Manager injection documented

[Unreleased]: https://github.com/open-gw/gateway-db-mcp/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/open-gw/gateway-db-mcp/releases/tag/v1.2.0
[1.0.0]: https://github.com/open-gw/gateway-db-mcp/releases/tag/v1.0.0

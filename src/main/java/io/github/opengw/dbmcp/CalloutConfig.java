package io.github.opengw.dbmcp;

import java.util.*;

/**
 * Immutable configuration parsed from the JavaCallout {@code <Properties>} block.
 *
 * <p>Example policy XML:
 * <pre>{@code
 * <JavaCallout name="JC-DBBridge">
 *   <ClassName>io.github.opengw.dbmcp.DBMCPCallout</ClassName>
 *   <ResourceURL>java://gateway-db-mcp-1.0.0.jar</ResourceURL>
 *   <Properties>
 *     <Property name="db.type">mysql</Property>
 *     <Property name="db.host">{propertyset.db-config.host}</Property>
 *     <Property name="db.port">3306</Property>
 *     <Property name="db.database">{propertyset.db-config.database}</Property>
 *     <Property name="db.username">{propertyset.db-config.username}</Property>
 *     <Property name="db.password">{private.db.password}</Property>
 *     <Property name="security.readOnly">true</Property>
 *     <Property name="security.allowedTables">orders,products,customers</Property>
 *     <Property name="security.maxRows">500</Property>
 *   </Properties>
 * </JavaCallout>
 * }</pre>
 */
public final class CalloutConfig {

    // ── Connection ────────────────────────────────────────────────────────────
    public final String  dbType;
    public final String  host;
    public final int     port;
    public final String  database;
    public final String  username;
    public final String  password;
    public final String  schema;

    // ── Pool ──────────────────────────────────────────────────────────────────
    public final int  poolMaxSize;
    public final int  poolMinIdle;
    public final long poolConnectionTimeoutMs;
    public final long poolIdleTimeoutMs;
    public final long poolMaxLifetimeMs;

    // ── Security ──────────────────────────────────────────────────────────────
    public final boolean     readOnly;
    public final Set<String> allowedTables;   // empty = all tables allowed
    public final int         maxRows;
    public final int         queryTimeoutSec;

    // ── OpenAPI meta ──────────────────────────────────────────────────────────
    public final String baseTitle;
    public final String apiVersion;

    private CalloutConfig(Builder b) {
        this.dbType                  = b.dbType;
        this.host                    = b.host;
        this.port                    = b.port;
        this.database                = b.database;
        this.username                = b.username;
        this.password                = b.password;
        this.schema                  = b.schema;
        this.poolMaxSize             = b.poolMaxSize;
        this.poolMinIdle             = b.poolMinIdle;
        this.poolConnectionTimeoutMs = b.poolConnectionTimeoutMs;
        this.poolIdleTimeoutMs       = b.poolIdleTimeoutMs;
        this.poolMaxLifetimeMs       = b.poolMaxLifetimeMs;
        this.readOnly                = b.readOnly;
        this.allowedTables           = Collections.unmodifiableSet(b.allowedTables);
        this.maxRows                 = b.maxRows;
        this.queryTimeoutSec         = b.queryTimeoutSec;
        this.baseTitle               = b.baseTitle;
        this.apiVersion              = b.apiVersion;
    }

    /** Parse the Apigee property map into a validated, typed config. */
    public static CalloutConfig from(Map<String, String> p) {
        Builder b = new Builder();
        b.dbType   = p.getOrDefault("db.type", "mysql").trim().toLowerCase();
        b.host     = require(p, "db.host");
        b.port     = parseInt(p, "db.port", defaultPort(b.dbType));
        b.database = require(p, "db.database");
        b.username = require(p, "db.username");
        b.password = require(p, "db.password");
        b.schema   = p.getOrDefault("db.schema", null);

        b.poolMaxSize             = parseInt(p, "pool.maxSize",            10);
        b.poolMinIdle             = parseInt(p, "pool.minIdle",             2);
        b.poolConnectionTimeoutMs = parseLong(p, "pool.connectionTimeout", 30_000L);
        b.poolIdleTimeoutMs       = parseLong(p, "pool.idleTimeout",      600_000L);
        b.poolMaxLifetimeMs       = parseLong(p, "pool.maxLifetime",    1_800_000L);

        b.readOnly        = parseBoolean(p, "security.readOnly",    true);
        b.maxRows         = parseInt(p,     "security.maxRows",     1000);
        b.queryTimeoutSec = parseInt(p,     "security.queryTimeout",  30);

        b.allowedTables = new LinkedHashSet<>();
        String tables = p.getOrDefault("security.allowedTables", "").trim();
        if (!tables.isEmpty()) {
            for (String t : tables.split(",")) {
                String trimmed = t.trim().toLowerCase();
                if (!trimmed.isEmpty()) b.allowedTables.add(trimmed);
            }
        }

        b.baseTitle  = p.getOrDefault("api.title",   "DB MCP Bridge");
        b.apiVersion = p.getOrDefault("api.version", "1.0.0");

        validate(b);
        return new CalloutConfig(b);
    }

    /** Also supports environment-variable-style keys (for sidecar mode). */
    public static CalloutConfig fromEnv() {
        Map<String, String> mapped = new HashMap<>();
        mapEnv(mapped, "DB_TYPE",                       "db.type");
        mapEnv(mapped, "DB_HOST",                       "db.host");
        mapEnv(mapped, "DB_PORT",                       "db.port");
        mapEnv(mapped, "DB_DATABASE",                   "db.database");
        mapEnv(mapped, "DB_USERNAME",                   "db.username");
        mapEnv(mapped, "DB_PASSWORD",                   "db.password");
        mapEnv(mapped, "DB_SCHEMA",                     "db.schema");
        mapEnv(mapped, "POOL_MAX_SIZE",                 "pool.maxSize");
        mapEnv(mapped, "POOL_MIN_IDLE",                 "pool.minIdle");
        mapEnv(mapped, "POOL_CONNECTION_TIMEOUT",       "pool.connectionTimeout");
        mapEnv(mapped, "POOL_IDLE_TIMEOUT",             "pool.idleTimeout");
        mapEnv(mapped, "POOL_MAX_LIFETIME",             "pool.maxLifetime");
        mapEnv(mapped, "SECURITY_READ_ONLY",            "security.readOnly");
        mapEnv(mapped, "SECURITY_ALLOWED_TABLES",       "security.allowedTables");
        mapEnv(mapped, "SECURITY_MAX_ROWS",             "security.maxRows");
        mapEnv(mapped, "SECURITY_QUERY_TIMEOUT",        "security.queryTimeout");
        mapEnv(mapped, "API_TITLE",                     "api.title");
        mapEnv(mapped, "API_VERSION",                   "api.version");
        return from(mapped);
    }

    private static void mapEnv(Map<String, String> target, String envKey, String propKey) {
        String val = System.getenv(envKey);
        if (val != null && !val.isBlank()) target.put(propKey, val.trim());
    }

    // ── JDBC helpers ──────────────────────────────────────────────────────────

    public String jdbcUrl() {
        switch (dbType) {
            case "postgres":
                return String.format("jdbc:postgresql://%s:%d/%s", host, port, database);
            case "mssql":
                return String.format(
                    "jdbc:sqlserver://%s:%d;databaseName=%s;encrypt=true;trustServerCertificate=false",
                    host, port, database);
            case "mariadb":
                return String.format(
                    "jdbc:mariadb://%s:%d/%s?useSSL=true", host, port, database);
            default: // mysql
                return String.format(
                    "jdbc:mysql://%s:%d/%s?useSSL=true&serverTimezone=UTC&allowPublicKeyRetrieval=false",
                    host, port, database);
        }
    }

    public String driverClassName() {
        switch (dbType) {
            case "postgres": return "org.postgresql.Driver";
            case "mssql":    return "com.microsoft.sqlserver.jdbc.SQLServerDriver";
            case "mariadb":  return "org.mariadb.jdbc.Driver";
            default:         return "com.mysql.cj.jdbc.Driver";
        }
    }

    /** Stable pool key — excludes credentials. */
    public String poolKey() {
        return dbType + "|" + host + ":" + port + "/" + database + "@" + username;
    }

    /** Returns true if the table name is accessible under this config. */
    public boolean isTableAllowed(String tableName) {
        if (allowedTables.isEmpty()) return true;
        return allowedTables.contains(tableName.toLowerCase());
    }

    // ── Validation & parsing ──────────────────────────────────────────────────

    private static void validate(Builder b) {
        Set<String> valid = new HashSet<>(Arrays.asList("mysql", "postgres", "mssql", "mariadb"));
        if (!valid.contains(b.dbType))
            throw new IllegalArgumentException(
                "[gateway-db-mcp] Unsupported db.type '" + b.dbType
                + "'. Supported: mysql, postgres, mssql, mariadb");
        if (b.maxRows < 1 || b.maxRows > 100_000)
            throw new IllegalArgumentException("security.maxRows must be 1–100000, got " + b.maxRows);
        if (b.queryTimeoutSec < 1 || b.queryTimeoutSec > 300)
            throw new IllegalArgumentException("security.queryTimeout must be 1–300, got " + b.queryTimeoutSec);
    }

    private static String require(Map<String, String> p, String key) {
        String v = p.get(key);
        if (v == null || v.isBlank())
            throw new IllegalArgumentException(
                "[gateway-db-mcp] Required property '" + key + "' is missing or empty. "
                + "Check the <Properties> block of JC-DBBridge.xml.");
        return v.trim();
    }

    private static int parseInt(Map<String, String> p, String key, int def) {
        String v = p.get(key);
        if (v == null || v.isBlank()) return def;
        try { return Integer.parseInt(v.trim()); }
        catch (NumberFormatException e) {
            throw new IllegalArgumentException("Property '" + key + "' must be an integer, got: " + v);
        }
    }

    private static long parseLong(Map<String, String> p, String key, long def) {
        String v = p.get(key);
        if (v == null || v.isBlank()) return def;
        try { return Long.parseLong(v.trim()); }
        catch (NumberFormatException e) {
            throw new IllegalArgumentException("Property '" + key + "' must be a long, got: " + v);
        }
    }

    private static boolean parseBoolean(Map<String, String> p, String key, boolean def) {
        String v = p.get(key);
        if (v == null || v.isBlank()) return def;
        return Boolean.parseBoolean(v.trim());
    }

    private static int defaultPort(String dbType) {
        switch (dbType) {
            case "postgres": return 5432;
            case "mssql":    return 1433;
            case "mariadb":  return 3306;
            default:         return 3306;
        }
    }

    private static class Builder {
        String dbType, host, database, username, password, schema, baseTitle, apiVersion;
        int port, poolMaxSize, poolMinIdle, maxRows, queryTimeoutSec;
        long poolConnectionTimeoutMs, poolIdleTimeoutMs, poolMaxLifetimeMs;
        boolean readOnly;
        Set<String> allowedTables;
    }
}

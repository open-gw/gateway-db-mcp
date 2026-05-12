package io.github.opengw.dbmcp;

import com.apigee.flow.message.MessageContext;
import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;

import javax.sql.DataSource;
import java.util.concurrent.ConcurrentHashMap;
import java.util.logging.Logger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

// ─────────────────────────────────────────────────────────────────────────────
// DBOperation — interface every operation implements
// ─────────────────────────────────────────────────────────────────────────────

interface DBOperation {
    String name();
    OperationResult execute(DataSource ds, MessageContext msgCtx, CalloutConfig config)
            throws Exception;
}

// ─────────────────────────────────────────────────────────────────────────────
// OperationResult — immutable response value object
// ─────────────────────────────────────────────────────────────────────────────

class OperationResult {
    private final String body;
    private final int    statusCode;
    private final int    rowCount;

    OperationResult(String body, int statusCode, int rowCount) {
        this.body       = body;
        this.statusCode = statusCode;
        this.rowCount   = rowCount;
    }

    static OperationResult ok(String body)               { return new OperationResult(body, 200, -1); }
    static OperationResult ok(String body, int rowCount) { return new OperationResult(body, 200, rowCount); }

    String body()       { return body; }
    int    statusCode() { return statusCode; }
    int    rowCount()   { return rowCount; }
}

// ─────────────────────────────────────────────────────────────────────────────
// Custom exceptions
// ─────────────────────────────────────────────────────────────────────────────

class OperationNotFoundException extends RuntimeException {
    OperationNotFoundException(String method, String path) {
        super("No handler for " + method + " " + path
            + ". Available: GET /tables, GET /tables/{t}/schema, "
            + "GET /tables/{t}/rows, POST /query, GET /openapi");
    }
}

class TableNotAllowedException extends RuntimeException {
    private final String table;
    TableNotAllowedException(String table) {
        super("Table not in allowedTables whitelist: " + table);
        this.table = table;
    }
    String tableName() { return table; }
}

// ─────────────────────────────────────────────────────────────────────────────
// OperationRouter — maps HTTP method + path suffix → operation handler
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Route table:
 * <pre>
 *   GET  /tables                → ListTablesOperation
 *   GET  /tables/{t}/schema     → DescribeSchemaOperation
 *   GET  /tables/{t}/rows       → GetTableRowsOperation
 *   POST /query                 → RunQueryOperation
 *   GET  /openapi               → GenerateOpenAPIOperation
 * </pre>
 */
class OperationRouter {

    private static final Pattern P_SCHEMA =
            Pattern.compile("^/tables/([\\w$]+)/schema$", Pattern.CASE_INSENSITIVE);
    private static final Pattern P_ROWS =
            Pattern.compile("^/tables/([\\w$]+)/rows$",   Pattern.CASE_INSENSITIVE);
    private static final Pattern P_TABLES =
            Pattern.compile("^/tables/?$",                Pattern.CASE_INSENSITIVE);
    private static final Pattern P_QUERY =
            Pattern.compile("^/query/?$",                 Pattern.CASE_INSENSITIVE);
    private static final Pattern P_OPENAPI =
            Pattern.compile("^/openapi/?$",               Pattern.CASE_INSENSITIVE);

    static DBOperation route(String method, String path, CalloutConfig config) {
        String p = (path == null || path.isBlank()) ? "/" : path.trim();
        Matcher m;

        if ("GET".equals(method)) {
            if (P_TABLES.matcher(p).matches())  return new ListTablesOperation();

            m = P_SCHEMA.matcher(p);
            if (m.matches()) return new DescribeSchemaOperation(m.group(1));

            m = P_ROWS.matcher(p);
            if (m.matches()) return new GetTableRowsOperation(m.group(1));

            if (P_OPENAPI.matcher(p).matches()) return new GenerateOpenAPIOperation();
        }

        if ("POST".equals(method) && P_QUERY.matcher(p).matches())
            return new RunQueryOperation();

        throw new OperationNotFoundException(method, p);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// ConnectionPoolManager — singleton HikariCP pool per config key
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Maintains one HikariCP DataSource per unique (dbType, host, port, database, username).
 * The static map survives proxy reloads within the same JVM instance.
 */
class ConnectionPoolManager {

    private static final Logger LOGGER = Logger.getLogger(ConnectionPoolManager.class.getName());
    private static final ConcurrentHashMap<String, HikariDataSource> POOLS =
            new ConcurrentHashMap<>();

    private ConnectionPoolManager() {}

    static void initialize(CalloutConfig config) {
        POOLS.computeIfAbsent(config.poolKey(), k -> createPool(config));
    }

    static DataSource getPool(CalloutConfig config) {
        return POOLS.computeIfAbsent(config.poolKey(), k -> createPool(config));
    }

    private static HikariDataSource createPool(CalloutConfig c) {
        LOGGER.info("[gateway-db-mcp] Creating pool — " + c.poolKey());

        HikariConfig hk = new HikariConfig();
        hk.setJdbcUrl(c.jdbcUrl());
        hk.setDriverClassName(c.driverClassName());
        hk.setUsername(c.username);
        hk.setPassword(c.password);

        hk.setMaximumPoolSize(c.poolMaxSize);
        hk.setMinimumIdle(c.poolMinIdle);
        hk.setConnectionTimeout(c.poolConnectionTimeoutMs);
        hk.setIdleTimeout(c.poolIdleTimeoutMs);
        hk.setMaxLifetime(c.poolMaxLifetimeMs);
        hk.setConnectionTestQuery("SELECT 1");
        hk.setPoolName("gw-dbmcp-" + c.dbType + "-" + c.database);

        applyDriverTweaks(hk, c);

        HikariDataSource ds = new HikariDataSource(hk);
        LOGGER.info("[gateway-db-mcp] Pool ready — " + c.poolKey()
                + " maxSize=" + c.poolMaxSize);
        return ds;
    }

    private static void applyDriverTweaks(HikariConfig hk, CalloutConfig c) {
        if ("mysql".equals(c.dbType)) {
            hk.addDataSourceProperty("cachePrepStmts",          "true");
            hk.addDataSourceProperty("prepStmtCacheSize",        "250");
            hk.addDataSourceProperty("prepStmtCacheSqlLimit",    "2048");
            hk.addDataSourceProperty("useServerPrepStmts",       "true");
            hk.addDataSourceProperty("rewriteBatchedStatements", "true");
        }
    }
}

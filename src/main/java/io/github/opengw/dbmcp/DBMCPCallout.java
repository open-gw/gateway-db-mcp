package io.github.opengw.dbmcp;

import com.apigee.flow.execution.ExecutionContext;
import com.apigee.flow.execution.ExecutionResult;
import com.apigee.flow.execution.spi.Execution;
import com.apigee.flow.message.MessageContext;

import java.util.Map;
import java.util.logging.Logger;

/**
 * DBMCPCallout — Apigee X Java Callout entry point.
 *
 * Exposes a JDBC database as REST endpoints consumable by Apigee's native
 * MCP proxy. Drop the shaded JAR into any Apigee proxy bundle and configure
 * via the {@code <Properties>} block of JC-DBBridge.xml.
 *
 * <p>Supported path suffixes (relative to proxy basepath):
 * <pre>
 *   GET  /tables               → list all accessible tables
 *   GET  /tables/{t}/schema    → column definitions for table t
 *   GET  /tables/{t}/rows      → paginated rows (?limit &offset &orderBy &dir)
 *   POST /query                → execute a parameterized SELECT
 *   GET  /openapi              → live MCP-annotated OpenAPI 3.0 spec
 * </pre>
 *
 * <p>Flow variables set on every response:
 * <pre>
 *   dbmcp.operation      — operation name (LIST_TABLES, RUN_QUERY, etc.)
 *   dbmcp.rowCount       — rows returned where applicable; -1 otherwise
 *   dbmcp.error.code     — machine-readable error code on fault
 *   dbmcp.error.message  — human-readable message on fault
 * </pre>
 *
 * @see CalloutConfig for full configuration reference
 * @see <a href="https://github.com/open-gw/gateway-db-mcp">github.com/open-gw/gateway-db-mcp</a>
 */
public class DBMCPCallout implements Execution {

    private static final Logger LOGGER = Logger.getLogger(DBMCPCallout.class.getName());

    private final CalloutConfig config;

    /**
     * Called once per proxy deployment by the Apigee runtime.
     * Eagerly initializes the HikariCP connection pool.
     */
    public DBMCPCallout(Map<String, String> properties) {
        this.config = CalloutConfig.from(properties);
        ConnectionPoolManager.initialize(config);
        LOGGER.info("[gateway-db-mcp] Initialized — db.type=" + config.dbType
                + " host=" + config.host + " database=" + config.database
                + " readOnly=" + config.readOnly
                + " allowedTables=" + (config.allowedTables.isEmpty() ? "(all)" : config.allowedTables));
    }

    @Override
    public ExecutionResult execute(MessageContext msgCtx, ExecutionContext execCtx) {
        try {
            String path   = resolveString(msgCtx, "proxy.pathsuffix", "/");
            String method = resolveString(msgCtx, "request.verb", "GET").toUpperCase();

            LOGGER.fine("[gateway-db-mcp] " + method + " " + path);

            DBOperation operation = OperationRouter.route(method, path, config);
            msgCtx.setVariable("dbmcp.operation", operation.name());

            OperationResult result = operation.execute(
                    ConnectionPoolManager.getPool(config), msgCtx, config);

            msgCtx.getMessage().setContent(result.body());
            msgCtx.getMessage().setHeader("Content-Type", "application/json");
            msgCtx.getMessage().setHeader("X-DBMCP-Operation", operation.name());
            msgCtx.setVariable("response.status.code", result.statusCode());

            if (result.rowCount() >= 0) {
                msgCtx.setVariable("dbmcp.rowCount", result.rowCount());
                msgCtx.getMessage().setHeader("X-DBMCP-Row-Count",
                        String.valueOf(result.rowCount()));
            }

            return ExecutionResult.SUCCESS;

        } catch (OperationNotFoundException e) {
            return writeError(msgCtx, 404, "OPERATION_NOT_FOUND", e.getMessage());
        } catch (SecurityException e) {
            LOGGER.warning("[gateway-db-mcp] Security violation: " + e.getMessage());
            return writeError(msgCtx, 403, "FORBIDDEN", e.getMessage());
        } catch (TableNotAllowedException e) {
            return writeError(msgCtx, 403, "TABLE_NOT_ALLOWED",
                    "Access to table '" + e.tableName() + "' is not permitted");
        } catch (IllegalArgumentException e) {
            return writeError(msgCtx, 400, "BAD_REQUEST", e.getMessage());
        } catch (Exception e) {
            LOGGER.severe("[gateway-db-mcp] Internal error: "
                    + e.getClass().getSimpleName() + " — " + e.getMessage());
            return writeError(msgCtx, 500, "INTERNAL_ERROR",
                    "Database operation failed. See Apigee logs for details.");
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private ExecutionResult writeError(MessageContext ctx, int status,
                                       String code, String message) {
        ctx.setVariable("response.status.code", status);
        ctx.setVariable("dbmcp.error.code", code);
        ctx.setVariable("dbmcp.error.message", message);
        String safe = message == null ? "" : message.replace("\"", "'").replace("\n", " ");
        ctx.getMessage().setContent(
                "{\"error\":{\"code\":\"" + code + "\",\"message\":\"" + safe + "\"}}");
        ctx.getMessage().setHeader("Content-Type", "application/json");
        return ExecutionResult.ABORT;
    }

    private String resolveString(MessageContext ctx, String variable, String defaultValue) {
        Object val = ctx.getVariable(variable);
        return (val != null) ? val.toString() : defaultValue;
    }
}

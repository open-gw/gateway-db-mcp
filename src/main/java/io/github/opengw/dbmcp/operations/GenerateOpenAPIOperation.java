package io.github.opengw.dbmcp.operations;

import com.apigee.flow.message.MessageContext;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.github.opengw.dbmcp.CalloutConfig;
import io.github.opengw.dbmcp.DBOperation;
import io.github.opengw.dbmcp.OperationResult;

import javax.sql.DataSource;
import java.sql.*;

/**
 * GET /openapi — introspects the live DB schema and returns a complete
 * OpenAPI 3.0.3 document with x-mcp-tool annotations, ready to import
 * into Apigee X, Kong, or Azure APIM MCP proxy configuration.
 */
public class GenerateOpenAPIOperation implements DBOperation {

    @Override public String name() { return "GENERATE_OPENAPI"; }

    @Override
    public OperationResult execute(DataSource ds, MessageContext ctx, CalloutConfig cfg)
            throws Exception {
        ObjectNode spec = Json.MAPPER.createObjectNode();
        spec.put("openapi", "3.0.3");

        ObjectNode info = spec.putObject("info");
        info.put("title",       cfg.baseTitle + " — " + cfg.database);
        info.put("version",     cfg.apiVersion);
        info.put("description", "Auto-generated from live DB schema. "
            + "Import into Apigee X / Kong / Azure APIM MCP proxy configuration. "
            + "Source: https://github.com/open-gw/gateway-db-mcp");

        ArrayNode servers = spec.putArray("servers");
        servers.addObject().put("url", resolveServerUrl(cfg, ctx));

        ObjectNode paths = spec.putObject("paths");

        // /tables
        ObjectNode tablesGet = addGetOp(paths, "/tables", "list_tables",
            "List all accessible tables in database: " + cfg.database);
        addMcpTool(tablesGet, "list_tables",
            "List the database tables exposed by this bridge");

        // /query
        ObjectNode queryPost = paths.putObject("/query").putObject("post");
        queryPost.put("operationId", "run_query");
        queryPost.put("summary",     "Execute a parameterized SELECT statement");
        addMcpTool(queryPost, "run_query",
            "Execute a parameterized SQL SELECT. Use ? placeholders with params[] array.");
        ObjectNode queryBody = queryPost.putObject("requestBody");
        queryBody.put("required", true);
        ObjectNode queryJson = queryBody.putObject("content").putObject("application/json");
        ObjectNode querySchema = queryJson.putObject("schema");
        querySchema.put("type", "object");
        ArrayNode required = querySchema.putArray("required");
        required.add("sql");
        ObjectNode props = querySchema.putObject("properties");
        ObjectNode sqlProp = props.putObject("sql");
        sqlProp.put("type", "string");
        sqlProp.put("description",
            "Parameterized SELECT statement. Only SELECT is accepted; "
            + "DDL and write statements are rejected.");
        ObjectNode paramsProp = props.putObject("params");
        paramsProp.put("type", "array");
        paramsProp.put("description",
            "Optional positional bind parameters substituted for ? placeholders in order.");
        // Permissive item typing: SQL bind values may be strings, numbers, or null.
        paramsProp.putObject("items");
        // Media-type example (OpenAPI 3.0.3); MCP clients often surface this to the model.
        ObjectNode example = queryJson.putObject("example");
        example.put("sql", "SELECT id, status, total FROM orders WHERE status = ?");
        ArrayNode exParams = example.putArray("params");
        exParams.add("completed");
        queryPost.putObject("responses").putObject("200").put("description", "Query results");

        // Per-table paths
        try (Connection conn = ds.getConnection()) {
            try (ResultSet rs = conn.getMetaData().getTables(
                    cfg.database, cfg.schema, "%", new String[]{"TABLE", "VIEW"})) {
                while (rs.next()) {
                    String t = rs.getString("TABLE_NAME");
                    if (!cfg.isTableAllowed(t)) continue;
                    addTablePaths(paths, t, cfg);
                }
            }
        }

        return OperationResult.ok(
            Json.MAPPER.writerWithDefaultPrettyPrinter().writeValueAsString(spec));
    }

    /**
     * Resolve the OpenAPI {@code servers[0].url} from bridge configuration.
     *
     * <ol>
     *   <li>{@code api.serverUrl} / {@code API_SERVER_URL} when set</li>
     *   <li>Apigee {@code proxy.url} or {@code proxy.basepath} (embedded)</li>
     *   <li>Sidecar {@code PORT} → {@code http://127.0.0.1:<port>}</li>
     *   <li>Relative {@code /} when uncertain (valid OpenAPI 3.0.3)</li>
     * </ol>
     */
    public static String resolveServerUrl(CalloutConfig cfg, MessageContext ctx) {
        if (cfg.serverUrl != null && !cfg.serverUrl.isBlank()) {
            return cfg.serverUrl.trim();
        }
        if (ctx != null) {
            Object proxyUrl = ctx.getVariable("proxy.url");
            if (proxyUrl != null) {
                String s = proxyUrl.toString().trim();
                if (!s.isEmpty()) return s;
            }
            Object basepath = ctx.getVariable("proxy.basepath");
            if (basepath != null) {
                String s = basepath.toString().trim();
                if (!s.isEmpty()) {
                    return s.startsWith("/") ? s : "/" + s;
                }
            }
        }
        String portEnv = System.getenv("PORT");
        if (portEnv != null && !portEnv.isBlank()) {
            try {
                int p = Integer.parseInt(portEnv.trim());
                if (p > 0 && p <= 65535) {
                    return "http://127.0.0.1:" + p;
                }
            } catch (NumberFormatException ignored) {
                // fall through
            }
        }
        return "/";
    }

    private void addTablePaths(ObjectNode paths, String table, CalloutConfig cfg) {
        // rows
        ObjectNode rowsGet = addGetOp(paths, "/tables/" + table + "/rows",
            "get_" + table + "_rows", "Retrieve rows from table: " + table);
        addMcpTool(rowsGet, "get_" + table + "_rows",
            "Query rows from database table: " + table);
        ArrayNode params = rowsGet.putArray("parameters");
        addQParam(params, "limit",   "integer",
            "Max rows (capped at " + cfg.maxRows + ")", false, 1, cfg.maxRows);
        addQParam(params, "offset",  "integer", "Pagination offset", false, 0, null);
        addQParam(params, "orderBy", "string",  "Column to order by", false, null, null);
        addQParam(params, "dir",     "string",  "asc or desc", false, null, null);

        // schema
        ObjectNode schemaGet = addGetOp(paths, "/tables/" + table + "/schema",
            "describe_" + table + "_schema", "Describe schema of table: " + table);
        addMcpTool(schemaGet, "describe_" + table + "_schema",
            "Returns column definitions and primary keys for table: " + table);
    }

    private ObjectNode addGetOp(ObjectNode paths, String path, String opId, String summary) {
        ObjectNode get = paths.putObject(path).putObject("get");
        get.put("operationId", opId);
        get.put("summary",     summary);
        get.putObject("responses").putObject("200").put("description", summary);
        return get;
    }

    private void addMcpTool(ObjectNode op, String name, String description) {
        op.putObject("x-mcp-tool")
          .put("name", name)
          .put("description", description);
    }

    private void addQParam(ArrayNode params, String name, String type,
                           String desc, boolean required,
                           Integer minimum, Integer maximum) {
        ObjectNode p = params.addObject();
        p.put("name", name);
        p.put("in",   "query");
        p.put("required", required);
        p.put("description", desc);
        ObjectNode schema = p.putObject("schema");
        schema.put("type", type);
        if (minimum != null) schema.put("minimum", minimum);
        if (maximum != null) schema.put("maximum", maximum);
    }
}

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

        ObjectNode paths = spec.putObject("paths");

        // /tables
        addGetOp(paths, "/tables", "list_tables",
            "List all accessible tables in database: " + cfg.database);

        // /query
        ObjectNode queryPost = paths.putObject("/query").putObject("post");
        queryPost.put("operationId", "run_query");
        queryPost.put("summary",     "Execute a parameterized SELECT statement");
        addMcpTool(queryPost, "run_query",
            "Execute a parameterized SQL SELECT. Use ? placeholders with params[] array.");
        queryPost.putObject("requestBody").put("required", true)
            .putObject("content").putObject("application/json")
            .putObject("schema").put("type", "object");
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

    private void addTablePaths(ObjectNode paths, String table, CalloutConfig cfg) {
        // rows
        ObjectNode rowsGet = addGetOp(paths, "/tables/" + table + "/rows",
            "get_" + table + "_rows", "Retrieve rows from table: " + table);
        addMcpTool(rowsGet, "get_" + table + "_rows",
            "Query rows from database table: " + table);
        ArrayNode params = rowsGet.putArray("parameters");
        addQParam(params, "limit",   "integer", "Max rows (capped at " + cfg.maxRows + ")", false);
        addQParam(params, "offset",  "integer", "Pagination offset", false);
        addQParam(params, "orderBy", "string",  "Column to order by", false);
        addQParam(params, "dir",     "string",  "asc or desc", false);

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
                           String desc, boolean required) {
        ObjectNode p = params.addObject();
        p.put("name", name);
        p.put("in",   "query");
        p.put("required", required);
        p.put("description", desc);
        p.putObject("schema").put("type", type);
    }
}

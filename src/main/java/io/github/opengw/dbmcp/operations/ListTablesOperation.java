package io.github.opengw.dbmcp.operations;

import com.apigee.flow.message.MessageContext;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.github.opengw.dbmcp.CalloutConfig;
import io.github.opengw.dbmcp.DBOperation;
import io.github.opengw.dbmcp.OperationResult;

import javax.sql.DataSource;
import java.sql.*;
import java.util.ArrayList;
import java.util.List;

/** GET /tables — returns all accessible table names. */
public class ListTablesOperation implements DBOperation {

    @Override public String name() { return "LIST_TABLES"; }

    @Override
    public OperationResult execute(DataSource ds, MessageContext ctx, CalloutConfig cfg)
            throws Exception {
        List<String> tables = new ArrayList<>();
        try (Connection conn = ds.getConnection()) {
            try (ResultSet rs = conn.getMetaData().getTables(
                    cfg.database, cfg.schema, "%", new String[]{"TABLE", "VIEW"})) {
                while (rs.next()) {
                    String t = rs.getString("TABLE_NAME");
                    if (cfg.isTableAllowed(t)) tables.add(t);
                }
            }
        }
        ObjectNode root = Json.MAPPER.createObjectNode();
        ArrayNode  arr  = root.putArray("tables");
        tables.forEach(arr::add);
        root.put("count",    tables.size());
        root.put("database", cfg.database);
        if (cfg.schema != null) root.put("schema", cfg.schema);
        return OperationResult.ok(Json.MAPPER.writeValueAsString(root), tables.size());
    }
}

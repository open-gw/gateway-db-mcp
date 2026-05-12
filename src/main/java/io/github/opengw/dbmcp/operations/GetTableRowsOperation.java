package io.github.opengw.dbmcp.operations;

import com.apigee.flow.message.MessageContext;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.github.opengw.dbmcp.CalloutConfig;
import io.github.opengw.dbmcp.DBOperation;
import io.github.opengw.dbmcp.OperationResult;
import io.github.opengw.dbmcp.TableNotAllowedException;
import io.github.opengw.dbmcp.security.QueryValidator;

import javax.sql.DataSource;
import java.sql.*;

/** GET /tables/{table}/rows — paginated row retrieval. */
public class GetTableRowsOperation implements DBOperation {

    private final String tableName;

    public GetTableRowsOperation(String tableName) {
        this.tableName = tableName;
    }

    @Override public String name() { return "GET_TABLE_ROWS"; }

    @Override
    public OperationResult execute(DataSource ds, MessageContext ctx, CalloutConfig cfg)
            throws Exception {
        QueryValidator.validateIdentifier(tableName);
        if (!cfg.isTableAllowed(tableName)) throw new TableNotAllowedException(tableName);

        int limit   = Math.min(intParam(ctx, "request.queryparam.limit",  cfg.maxRows), cfg.maxRows);
        int offset  = intParam(ctx, "request.queryparam.offset", 0);
        String orderBy = strParam(ctx, "request.queryparam.orderBy");
        String dir     = "desc".equalsIgnoreCase(strParam(ctx, "request.queryparam.dir")) ? "DESC" : "ASC";
        if (orderBy != null) QueryValidator.validateIdentifier(orderBy);

        StringBuilder sql = new StringBuilder("SELECT * FROM `").append(tableName).append("`");
        if (orderBy != null) sql.append(" ORDER BY `").append(orderBy).append("` ").append(dir);
        sql.append(" LIMIT ? OFFSET ?");

        ObjectNode root = Json.MAPPER.createObjectNode();
        root.put("table", tableName);
        root.put("limit", limit);
        root.put("offset", offset);
        ArrayNode rows = root.putArray("rows");

        try (Connection conn = ds.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql.toString())) {
            ps.setQueryTimeout(cfg.queryTimeoutSec);
            ps.setInt(1, limit);
            ps.setInt(2, offset);
            try (ResultSet rs = ps.executeQuery()) {
                ResultSetMetaData rsMeta = rs.getMetaData();
                int colCount = rsMeta.getColumnCount();
                while (rs.next()) {
                    ObjectNode row = rows.addObject();
                    for (int i = 1; i <= colCount; i++) {
                        String col = rsMeta.getColumnLabel(i);
                        Object val = rs.getObject(i);
                        if (val == null) row.putNull(col);
                        else             row.put(col, val.toString());
                    }
                }
            }
        }
        root.put("count", rows.size());
        return OperationResult.ok(Json.MAPPER.writeValueAsString(root), rows.size());
    }

    private int intParam(MessageContext ctx, String var, int def) {
        Object v = ctx.getVariable(var);
        if (v == null) return def;
        try { return Integer.parseInt(v.toString()); } catch (NumberFormatException e) { return def; }
    }

    private String strParam(MessageContext ctx, String var) {
        Object v = ctx.getVariable(var);
        return (v != null && !v.toString().isBlank()) ? v.toString().trim() : null;
    }
}

package io.github.opengw.dbmcp.operations;

import com.apigee.flow.message.MessageContext;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.github.opengw.dbmcp.CalloutConfig;
import io.github.opengw.dbmcp.DBOperation;
import io.github.opengw.dbmcp.OperationResult;
import io.github.opengw.dbmcp.security.QueryValidator;

import javax.sql.DataSource;
import java.sql.*;
import java.util.ArrayList;
import java.util.List;

/** POST /query — executes a parameterized SELECT statement. */
public class RunQueryOperation implements DBOperation {

    @Override public String name() { return "RUN_QUERY"; }

    @Override
    public OperationResult execute(DataSource ds, MessageContext ctx, CalloutConfig cfg)
            throws Exception {
        String body = ctx.getMessage().getContent().toString();
        if (body == null || body.isBlank())
            throw new IllegalArgumentException("Request body must be JSON with a 'sql' field");

        ObjectNode req;
        try {
            req = (ObjectNode) Json.MAPPER.readTree(body);
        } catch (Exception e) {
            throw new IllegalArgumentException("Request body is not valid JSON: " + e.getMessage());
        }
        if (!req.has("sql"))
            throw new IllegalArgumentException("Request body must contain a 'sql' field");

        String sql = req.get("sql").asText().trim();
        QueryValidator.validate(sql, cfg.readOnly);

        List<Object> params = new ArrayList<>();
        if (req.has("params") && req.get("params").isArray()) {
            req.get("params").forEach(n -> {
                if (n.isNull())        params.add(null);
                else if (n.isNumber()) params.add(n.numberValue());
                else                   params.add(n.asText());
            });
        }

        ObjectNode result   = Json.MAPPER.createObjectNode();
        ArrayNode  cols     = result.putArray("columns");
        ArrayNode  rows     = result.putArray("rows");
        boolean    truncated = false;

        try (Connection conn = ds.getConnection();
             PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setQueryTimeout(cfg.queryTimeoutSec);
            ps.setMaxRows(cfg.maxRows + 1);
            for (int i = 0; i < params.size(); i++) ps.setObject(i + 1, params.get(i));

            try (ResultSet rs = ps.executeQuery()) {
                ResultSetMetaData meta = rs.getMetaData();
                int colCount = meta.getColumnCount();
                for (int i = 1; i <= colCount; i++) cols.add(meta.getColumnLabel(i));

                int rowNum = 0;
                while (rs.next()) {
                    if (rowNum >= cfg.maxRows) { truncated = true; break; }
                    ObjectNode row = rows.addObject();
                    for (int i = 1; i <= colCount; i++) {
                        String col = meta.getColumnLabel(i);
                        Object val = rs.getObject(i);
                        if (val == null) row.putNull(col);
                        else             row.put(col, val.toString());
                    }
                    rowNum++;
                }
            }
        }
        result.put("count",     rows.size());
        result.put("truncated", truncated);
        if (truncated) result.put("maxRows", cfg.maxRows);
        return OperationResult.ok(Json.MAPPER.writeValueAsString(result), rows.size());
    }
}

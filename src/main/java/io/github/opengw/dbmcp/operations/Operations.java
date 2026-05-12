package io.github.opengw.dbmcp.operations;

import com.apigee.flow.message.MessageContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.github.opengw.dbmcp.*;
import io.github.opengw.dbmcp.security.QueryValidator;

import javax.sql.DataSource;
import java.sql.*;
import java.util.ArrayList;
import java.util.List;

/** Shared Jackson instance (thread-safe). */
class Json {
    static final ObjectMapper MAPPER = new ObjectMapper();
}

// ─────────────────────────────────────────────────────────────────────────────
// ListTablesOperation — GET /tables
// ─────────────────────────────────────────────────────────────────────────────
class ListTablesOperation implements DBOperation {

    @Override public String name() { return "LIST_TABLES"; }

    @Override
    public OperationResult execute(DataSource ds, MessageContext ctx, CalloutConfig cfg)
            throws Exception {
        List<String> tables = new ArrayList<>();
        try (Connection conn = ds.getConnection()) {
            DatabaseMetaData meta = conn.getMetaData();
            try (ResultSet rs = meta.getTables(cfg.database, cfg.schema, "%",
                    new String[]{"TABLE", "VIEW"})) {
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

// ─────────────────────────────────────────────────────────────────────────────
// DescribeSchemaOperation — GET /tables/{table}/schema
// ─────────────────────────────────────────────────────────────────────────────
class DescribeSchemaOperation implements DBOperation {
    private final String tableName;
    DescribeSchemaOperation(String tableName) { this.tableName = tableName; }

    @Override public String name() { return "DESCRIBE_SCHEMA"; }

    @Override
    public OperationResult execute(DataSource ds, MessageContext ctx, CalloutConfig cfg)
            throws Exception {
        QueryValidator.validateIdentifier(tableName);
        if (!cfg.isTableAllowed(tableName)) throw new TableNotAllowedException(tableName);

        ObjectNode root = Json.MAPPER.createObjectNode();
        root.put("table", tableName);
        ArrayNode cols = root.putArray("columns");
        ArrayNode pks  = root.putArray("primaryKeys");

        try (Connection conn = ds.getConnection()) {
            DatabaseMetaData meta = conn.getMetaData();
            List<String> pkList = new ArrayList<>();
            try (ResultSet pkRs = meta.getPrimaryKeys(cfg.database, cfg.schema, tableName)) {
                while (pkRs.next()) pkList.add(pkRs.getString("COLUMN_NAME"));
            }
            pkList.forEach(pks::add);
            try (ResultSet colRs = meta.getColumns(cfg.database, cfg.schema, tableName, "%")) {
                while (colRs.next()) {
                    ObjectNode col = cols.addObject();
                    col.put("name",       colRs.getString("COLUMN_NAME"));
                    col.put("type",       colRs.getString("TYPE_NAME"));
                    col.put("size",       colRs.getInt("COLUMN_SIZE"));
                    col.put("nullable",   colRs.getInt("NULLABLE") == DatabaseMetaData.columnNullable);
                    col.put("primaryKey", pkList.contains(colRs.getString("COLUMN_NAME")));
                    String def = colRs.getString("COLUMN_DEF");
                    if (def != null) col.put("default", def);
                }
            }
        }
        return OperationResult.ok(Json.MAPPER.writeValueAsString(root));
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// GetTableRowsOperation — GET /tables/{table}/rows
// ─────────────────────────────────────────────────────────────────────────────
class GetTableRowsOperation implements DBOperation {
    private final String tableName;
    GetTableRowsOperation(String tableName) { this.tableName = tableName; }

    @Override public String name() { return "GET_TABLE_ROWS"; }

    @Override
    public OperationResult execute(DataSource ds, MessageContext ctx, CalloutConfig cfg)
            throws Exception {
        QueryValidator.validateIdentifier(tableName);
        if (!cfg.isTableAllowed(tableName)) throw new TableNotAllowedException(tableName);

        int limit  = Math.min(parseIntParam(ctx, "request.queryparam.limit",  cfg.maxRows), cfg.maxRows);
        int offset = parseIntParam(ctx, "request.queryparam.offset", 0);
        String orderBy = stringParam(ctx, "request.queryparam.orderBy");
        String dir     = "desc".equalsIgnoreCase(stringParam(ctx, "request.queryparam.dir")) ? "DESC" : "ASC";
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
                int cols = rsMeta.getColumnCount();
                while (rs.next()) {
                    ObjectNode row = rows.addObject();
                    for (int i = 1; i <= cols; i++) {
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

    private int parseIntParam(MessageContext ctx, String var, int def) {
        Object v = ctx.getVariable(var);
        if (v == null) return def;
        try { return Integer.parseInt(v.toString()); } catch (NumberFormatException e) { return def; }
    }
    private String stringParam(MessageContext ctx, String var) {
        Object v = ctx.getVariable(var);
        return (v != null && !v.toString().isBlank()) ? v.toString().trim() : null;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// RunQueryOperation — POST /query
// ─────────────────────────────────────────────────────────────────────────────
class RunQueryOperation implements DBOperation {

    @Override public String name() { return "RUN_QUERY"; }

    @Override
    public OperationResult execute(DataSource ds, MessageContext ctx, CalloutConfig cfg)
            throws Exception {
        String body = ctx.getMessage().getContent().toString();
        if (body == null || body.isBlank())
            throw new IllegalArgumentException("Request body must be JSON with a 'sql' field");

        ObjectNode req;
        try { req = (ObjectNode) Json.MAPPER.readTree(body); }
        catch (Exception e) {
            throw new IllegalArgumentException("Request body is not valid JSON: " + e.getMessage());
        }
        if (!req.has("sql"))
            throw new IllegalArgumentException("Request body must contain a 'sql' field");

        String sql = req.get("sql").asText().trim();
        QueryValidator.validate(sql, cfg.readOnly);

        List<Object> params = new ArrayList<>();
        if (req.has("params") && req.get("params").isArray()) {
            req.get("params").forEach(n -> {
                if (n.isNull())          params.add(null);
                else if (n.isNumber())   params.add(n.numberValue());
                else                     params.add(n.asText());
            });
        }

        ObjectNode result = Json.MAPPER.createObjectNode();
        ArrayNode  cols   = result.putArray("columns");
        ArrayNode  rows   = result.putArray("rows");
        boolean truncated = false;

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

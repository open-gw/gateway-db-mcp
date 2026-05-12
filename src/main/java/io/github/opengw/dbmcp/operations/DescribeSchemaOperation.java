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
import java.util.ArrayList;
import java.util.List;

/** GET /tables/{table}/schema — returns column definitions and primary keys. */
public class DescribeSchemaOperation implements DBOperation {

    private final String tableName;

    public DescribeSchemaOperation(String tableName) {
        this.tableName = tableName;
    }

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

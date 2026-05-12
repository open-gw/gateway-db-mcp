package io.github.opengw.dbmcp;

public class TableNotAllowedException extends RuntimeException {
    private final String table;

    public TableNotAllowedException(String table) {
        super("Table not in allowedTables whitelist: " + table);
        this.table = table;
    }

    public String tableName() { return table; }
}

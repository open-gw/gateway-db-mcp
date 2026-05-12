package io.github.opengw.dbmcp;

/** Immutable response value object returned by every operation handler. */
public final class OperationResult {

    private final String body;
    private final int    statusCode;
    private final int    rowCount;

    public OperationResult(String body, int statusCode, int rowCount) {
        this.body       = body;
        this.statusCode = statusCode;
        this.rowCount   = rowCount;
    }

    public static OperationResult ok(String body)               { return new OperationResult(body, 200, -1); }
    public static OperationResult ok(String body, int rowCount) { return new OperationResult(body, 200, rowCount); }

    public String body()       { return body; }
    public int    statusCode() { return statusCode; }
    public int    rowCount()   { return rowCount; }
}

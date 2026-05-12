package io.github.opengw.dbmcp;

public class OperationNotFoundException extends RuntimeException {
    public OperationNotFoundException(String method, String path) {
        super("No handler for " + method + " " + path
            + ". Available: GET /tables, GET /tables/{t}/schema, "
            + "GET /tables/{t}/rows, POST /query, GET /openapi");
    }
}

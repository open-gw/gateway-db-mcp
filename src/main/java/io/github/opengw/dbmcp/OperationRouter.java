package io.github.opengw.dbmcp;

import io.github.opengw.dbmcp.operations.*;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Routes HTTP method + path suffix to the appropriate operation handler.
 *
 * GET  /tables               → ListTablesOperation
 * GET  /tables/{t}/schema    → DescribeSchemaOperation
 * GET  /tables/{t}/rows      → GetTableRowsOperation
 * POST /query                → RunQueryOperation
 * GET  /openapi              → GenerateOpenAPIOperation
 */
public class OperationRouter {

    private static final Pattern P_SCHEMA  = Pattern.compile("^/tables/([\\w$]+)/schema$", Pattern.CASE_INSENSITIVE);
    private static final Pattern P_ROWS    = Pattern.compile("^/tables/([\\w$]+)/rows$",   Pattern.CASE_INSENSITIVE);
    private static final Pattern P_TABLES  = Pattern.compile("^/tables/?$",                Pattern.CASE_INSENSITIVE);
    private static final Pattern P_QUERY   = Pattern.compile("^/query/?$",                 Pattern.CASE_INSENSITIVE);
    private static final Pattern P_OPENAPI = Pattern.compile("^/openapi/?$",               Pattern.CASE_INSENSITIVE);

    public static DBOperation route(String method, String path, CalloutConfig config) {
        String p = (path == null || path.isBlank()) ? "/" : path.trim();
        Matcher m;

        if ("GET".equals(method)) {
            if (P_TABLES.matcher(p).matches())  return new ListTablesOperation();
            m = P_SCHEMA.matcher(p);
            if (m.matches()) return new DescribeSchemaOperation(m.group(1));
            m = P_ROWS.matcher(p);
            if (m.matches()) return new GetTableRowsOperation(m.group(1));
            if (P_OPENAPI.matcher(p).matches()) return new GenerateOpenAPIOperation();
        }
        if ("POST".equals(method) && P_QUERY.matcher(p).matches())
            return new RunQueryOperation();

        throw new OperationNotFoundException(method, p);
    }
}

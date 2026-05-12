package io.github.opengw.dbmcp;

import io.github.opengw.dbmcp.*;
import io.github.opengw.dbmcp.operations.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class OperationRouterTest {

    private CalloutConfig cfg;

    @BeforeEach void setup() {
        Map<String, String> p = new HashMap<>();
        p.put("db.type", "mysql"); p.put("db.host", "localhost");
        p.put("db.port", "3306");  p.put("db.database", "testdb");
        p.put("db.username", "u"); p.put("db.password", "p");
        cfg = CalloutConfig.from(p);
    }

    @Test void get_tables_routes_to_list_tables() {
        DBOperation op = OperationRouter.route("GET", "/tables", cfg);
        assertEquals("LIST_TABLES", op.name());
    }

    @Test void get_tables_trailing_slash() {
        DBOperation op = OperationRouter.route("GET", "/tables/", cfg);
        assertEquals("LIST_TABLES", op.name());
    }

    @Test void get_tables_schema_routes_correctly() {
        DBOperation op = OperationRouter.route("GET", "/tables/orders/schema", cfg);
        assertEquals("DESCRIBE_SCHEMA", op.name());
    }

    @Test void get_tables_rows_routes_correctly() {
        DBOperation op = OperationRouter.route("GET", "/tables/order_items/rows", cfg);
        assertEquals("GET_TABLE_ROWS", op.name());
    }

    @Test void post_query_routes_correctly() {
        DBOperation op = OperationRouter.route("POST", "/query", cfg);
        assertEquals("RUN_QUERY", op.name());
    }

    @Test void get_openapi_routes_correctly() {
        DBOperation op = OperationRouter.route("GET", "/openapi", cfg);
        assertEquals("GENERATE_OPENAPI", op.name());
    }

    @Test void unknown_route_throws_operation_not_found() {
        assertThrows(OperationNotFoundException.class, () ->
            OperationRouter.route("DELETE", "/tables/orders", cfg));
        assertThrows(OperationNotFoundException.class, () ->
            OperationRouter.route("GET", "/unknown", cfg));
        assertThrows(OperationNotFoundException.class, () ->
            OperationRouter.route("PUT", "/tables/orders/rows", cfg));
    }

    @Test void case_insensitive_path_matching() {
        DBOperation op = OperationRouter.route("GET", "/TABLES", cfg);
        assertEquals("LIST_TABLES", op.name());
    }

    @Test void null_path_uses_default() {
        DBOperation op = OperationRouter.route("GET", "/tables", cfg);
        assertNotNull(op);
    }
}

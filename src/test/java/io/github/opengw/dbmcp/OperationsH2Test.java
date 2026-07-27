package io.github.opengw.dbmcp;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.github.opengw.dbmcp.operations.DescribeSchemaOperation;
import io.github.opengw.dbmcp.operations.GenerateOpenAPIOperation;
import io.github.opengw.dbmcp.operations.GetTableRowsOperation;
import io.github.opengw.dbmcp.operations.ListTablesOperation;
import io.github.opengw.dbmcp.operations.RunQueryOperation;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.StreamSupport;

import static org.junit.jupiter.api.Assertions.*;

/**
 * H2-backed tests for the five DB operations (U-01…U-07).
 */
class OperationsH2Test {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private H2Fixture fx;

    @BeforeEach
    void setUp() throws Exception {
        fx = H2Fixture.create("orders,products", 2);
    }

    @AfterEach
    void tearDown() {
        if (fx != null) fx.close();
    }

    @Test
    void list_tables_filters_by_allowed_tables() throws Exception {
        OperationResult result = new ListTablesOperation()
                .execute(fx.dataSource, H2Fixture.emptyContext(), fx.config);

        assertEquals(200, result.statusCode());
        JsonNode root = MAPPER.readTree(result.body());
        Set<String> tables = toLowerSet(root.get("tables"));
        assertEquals(Set.of("orders", "products"), tables);
        assertFalse(tables.contains("employees"));
        assertEquals(2, root.get("count").asInt());
        assertEquals(2, result.rowCount());
    }

    @Test
    void describe_schema_returns_columns_and_primary_keys() throws Exception {
        OperationResult result = new DescribeSchemaOperation("orders")
                .execute(fx.dataSource, H2Fixture.emptyContext(), fx.config);

        assertEquals(200, result.statusCode());
        JsonNode root = MAPPER.readTree(result.body());
        assertEquals("orders", root.get("table").asText());

        Set<String> colNames = StreamSupport.stream(root.get("columns").spliterator(), false)
                .map(n -> n.get("name").asText().toLowerCase())
                .collect(Collectors.toSet());
        assertTrue(colNames.containsAll(Set.of("id", "customer", "amount")));

        Set<String> pks = toLowerSet(root.get("primaryKeys"));
        assertEquals(Set.of("id"), pks);

        JsonNode idCol = StreamSupport.stream(root.get("columns").spliterator(), false)
                .filter(n -> "id".equalsIgnoreCase(n.get("name").asText()))
                .findFirst()
                .orElseThrow();
        assertTrue(idCol.get("primaryKey").asBoolean());
    }

    @Test
    void describe_schema_rejects_disallowed_table() {
        assertThrows(TableNotAllowedException.class, () ->
                new DescribeSchemaOperation("employees")
                        .execute(fx.dataSource, H2Fixture.emptyContext(), fx.config));
    }

    @Test
    void get_table_rows_respects_limit_offset_and_order() throws Exception {
        OperationResult result = new GetTableRowsOperation("orders")
                .execute(fx.dataSource,
                        H2Fixture.messageContext(null, Map.of(
                                "limit", "2",
                                "offset", "1",
                                "orderBy", "id",
                                "dir", "asc")),
                        fx.config);

        assertEquals(200, result.statusCode());
        JsonNode root = MAPPER.readTree(result.body());
        assertEquals(2, root.get("count").asInt());
        assertEquals(2, root.get("rows").size());
        // offset 1 on ids 1..5 asc → rows id=2, id=3 (also capped by maxRows=2)
        assertEquals("2", fieldIgnoreCase(root.get("rows").get(0), "id"));
        assertEquals("3", fieldIgnoreCase(root.get("rows").get(1), "id"));
    }

    @Test
    void get_table_rows_caps_at_max_rows() throws Exception {
        OperationResult result = new GetTableRowsOperation("orders")
                .execute(fx.dataSource,
                        H2Fixture.messageContext(null, Map.of("limit", "100")),
                        fx.config);

        JsonNode root = MAPPER.readTree(result.body());
        assertTrue(root.get("count").asInt() <= fx.config.maxRows);
        assertEquals(2, root.get("rows").size());
    }

    @Test
    void get_table_rows_rejects_disallowed_table() {
        assertThrows(TableNotAllowedException.class, () ->
                new GetTableRowsOperation("employees")
                        .execute(fx.dataSource, H2Fixture.emptyContext(), fx.config));
    }

    @Test
    void get_table_rows_rejects_bad_order_by_identifier() {
        assertThrows(SecurityException.class, () ->
                new GetTableRowsOperation("orders")
                        .execute(fx.dataSource,
                                H2Fixture.messageContext(null, Map.of("orderBy", "id; drop table orders")),
                                fx.config));
    }

    @Test
    void run_query_binds_params_and_returns_rows() throws Exception {
        String body = "{\"sql\":\"SELECT id, customer FROM orders WHERE id = ?\",\"params\":[2]}";
        OperationResult result = new RunQueryOperation()
                .execute(fx.dataSource, H2Fixture.messageContext(body), fx.config);

        assertEquals(200, result.statusCode());
        JsonNode root = MAPPER.readTree(result.body());
        assertEquals(1, root.get("count").asInt());
        assertFalse(root.get("truncated").asBoolean());
        assertEquals(1, result.rowCount());
        assertEquals("bob", fieldIgnoreCase(root.get("rows").get(0), "customer"));
    }

    @Test
    void run_query_sets_truncated_when_over_max_rows() throws Exception {
        String body = "{\"sql\":\"SELECT id FROM orders ORDER BY id\"}";
        OperationResult result = new RunQueryOperation()
                .execute(fx.dataSource, H2Fixture.messageContext(body), fx.config);

        JsonNode root = MAPPER.readTree(result.body());
        assertEquals(2, root.get("count").asInt());
        assertTrue(root.get("truncated").asBoolean());
        assertEquals(2, root.get("maxRows").asInt());
    }

    @Test
    void run_query_rejects_missing_sql_and_blocked_dml() {
        assertThrows(IllegalArgumentException.class, () ->
                new RunQueryOperation()
                        .execute(fx.dataSource, H2Fixture.messageContext("{}"), fx.config));
        assertThrows(IllegalArgumentException.class, () ->
                new RunQueryOperation()
                        .execute(fx.dataSource, H2Fixture.messageContext("not-json"), fx.config));
        assertThrows(SecurityException.class, () ->
                new RunQueryOperation()
                        .execute(fx.dataSource,
                                H2Fixture.messageContext("{\"sql\":\"DELETE FROM orders\"}"),
                                fx.config));
    }

    @Test
    void generate_openapi_includes_mcp_tools_for_allowed_tables_only() throws Exception {
        OperationResult result = new GenerateOpenAPIOperation()
                .execute(fx.dataSource, H2Fixture.emptyContext(), fx.config);

        assertEquals(200, result.statusCode());
        JsonNode spec = MAPPER.readTree(result.body());
        assertEquals("3.0.3", spec.get("openapi").asText());
        assertTrue(spec.get("info").get("title").asText().contains("Test DB MCP Bridge"));

        JsonNode paths = spec.get("paths");
        assertTrue(paths.has("/tables"));
        assertTrue(paths.has("/query"));
        assertTrue(paths.has("/tables/orders/rows"));
        assertTrue(paths.has("/tables/orders/schema"));
        assertTrue(paths.has("/tables/products/rows"));
        assertFalse(paths.has("/tables/employees/rows"));

        assertEquals("list_tables",
                paths.get("/tables").get("get").get("operationId").asText());
        assertEquals("run_query",
                paths.get("/query").get("post").get("operationId").asText());
        assertEquals("get_orders_rows",
                paths.get("/tables/orders/rows").get("get").get("x-mcp-tool").get("name").asText());
        assertEquals("describe_orders_schema",
                paths.get("/tables/orders/schema").get("get").get("x-mcp-tool").get("name").asText());
    }

    private static Set<String> toLowerSet(JsonNode array) {
        Set<String> out = new HashSet<>();
        array.forEach(n -> out.add(n.asText().toLowerCase()));
        return out;
    }

    private static String fieldIgnoreCase(JsonNode row, String name) {
        if (row.has(name)) return row.get(name).asText();
        if (row.has(name.toUpperCase())) return row.get(name.toUpperCase()).asText();
        var fields = row.fieldNames();
        while (fields.hasNext()) {
            String f = fields.next();
            if (f.equalsIgnoreCase(name)) return row.get(f).asText();
        }
        fail("Missing field: " + name + " in " + row);
        return null;
    }
}

package io.github.opengw.dbmcp.server;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.github.opengw.dbmcp.H2Fixture;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.HashSet;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

/**
 * HTTP-level tests for {@link SidecarServer} against in-memory H2.
 */
class SidecarServerTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final HttpClient CLIENT = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    private H2Fixture fx;
    private SidecarServer server;
    private String base;

    @BeforeEach
    void setUp() throws Exception {
        fx = H2Fixture.create("orders,products", 100);
        server = new SidecarServer(fx.config, fx.dataSource, 0, 4);
        server.start();
        base = "http://127.0.0.1:" + server.getPort();
    }

    @AfterEach
    void tearDown() {
        if (server != null) server.stop();
        if (fx != null) fx.close();
    }

    @Test
    void health_returns_200() throws Exception {
        HttpResponse<String> res = get("/health");
        assertEquals(200, res.statusCode());
        JsonNode body = MAPPER.readTree(res.body());
        assertEquals("ok", body.get("status").asText());
        assertExplicitContentLength(res);
    }

    @Test
    void tables_returns_only_allowlisted() throws Exception {
        HttpResponse<String> res = get("/tables");
        assertEquals(200, res.statusCode());
        JsonNode body = MAPPER.readTree(res.body());
        Set<String> tables = new HashSet<>();
        body.get("tables").forEach(n -> tables.add(n.asText().toLowerCase()));
        assertEquals(Set.of("orders", "products"), tables);
        assertFalse(tables.contains("employees"));
        assertExplicitContentLength(res);
    }

    @Test
    void query_ddl_returns_403_forbidden() throws Exception {
        HttpResponse<String> res = postJson("/query", "{\"sql\":\"DROP TABLE orders\"}");
        assertEquals(403, res.statusCode());
        JsonNode err = MAPPER.readTree(res.body()).get("error");
        assertEquals("FORBIDDEN", err.get("code").asText());
        assertTrue(err.get("message").asText().toLowerCase().contains("ddl"));
        assertExplicitContentLength(res);
    }

    /**
     * Guard against reintroducing chunked responses ({@code sendResponseHeaders(..., 0)}),
     * which stalls ~40 ms behind TCP delayed ACK on small bodies.
     */
    private static void assertExplicitContentLength(HttpResponse<String> res) {
        var lengths = res.headers().allValues("Content-Length");
        assertFalse(lengths.isEmpty(), "Content-Length header must be present (not chunked)");
        assertEquals(1, lengths.size(), "exactly one Content-Length value");
        assertEquals(res.body().getBytes(java.nio.charset.StandardCharsets.UTF_8).length,
                Integer.parseInt(lengths.get(0)),
                "Content-Length must match body byte length");
    }

    @Test
    void query_parameterized_select_returns_rows() throws Exception {
        String payload = "{\"sql\":\"SELECT id, customer FROM orders WHERE id = ?\",\"params\":[2]}";
        HttpResponse<String> res = postJson("/query", payload);
        assertEquals(200, res.statusCode());
        JsonNode body = MAPPER.readTree(res.body());
        assertEquals(1, body.get("count").asInt());
        assertEquals(1, body.get("rows").size());
        JsonNode row = body.get("rows").get(0);
        String customer = row.has("customer") ? row.get("customer").asText()
                : row.get("CUSTOMER").asText();
        assertEquals("bob", customer);
    }

    @Test
    void unknown_path_returns_404() throws Exception {
        HttpResponse<String> res = get("/no-such-route");
        assertEquals(404, res.statusCode());
        JsonNode err = MAPPER.readTree(res.body()).get("error");
        assertEquals("OPERATION_NOT_FOUND", err.get("code").asText());
    }

    private HttpResponse<String> get(String path) throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create(base + path))
                .timeout(Duration.ofSeconds(10))
                .GET()
                .build();
        return CLIENT.send(req, HttpResponse.BodyHandlers.ofString());
    }

    private HttpResponse<String> postJson(String path, String json) throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create(base + path))
                .timeout(Duration.ofSeconds(10))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(json))
                .build();
        return CLIENT.send(req, HttpResponse.BodyHandlers.ofString());
    }
}

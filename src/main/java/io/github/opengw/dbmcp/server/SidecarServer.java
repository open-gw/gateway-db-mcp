package io.github.opengw.dbmcp.server;

import com.apigee.flow.message.Message;
import com.apigee.flow.message.MessageContext;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import io.github.opengw.dbmcp.CalloutConfig;
import io.github.opengw.dbmcp.ConnectionPoolManager;
import io.github.opengw.dbmcp.DBOperation;
import io.github.opengw.dbmcp.OperationNotFoundException;
import io.github.opengw.dbmcp.OperationResult;
import io.github.opengw.dbmcp.OperationRouter;
import io.github.opengw.dbmcp.TableNotAllowedException;

import javax.sql.DataSource;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.logging.Logger;

/**
 * Sidecar HTTP server — same five REST endpoints as {@link io.github.opengw.dbmcp.DBMCPCallout},
 * plus {@code GET /health} for container probes.
 *
 * <p>Assembles config, pool, and router the same way as the Apigee callout;
 * only the transport differs ({@link HttpServer} instead of the Apigee message flow).
 */
public final class SidecarServer {

    private static final Logger LOGGER = Logger.getLogger(SidecarServer.class.getName());

    static {
        // JDK HttpServer flushes response headers before the body write
        // (ExchangeImpl.sendResponseHeaders → tmpout.flush). Content-Length
        // prevents chunked zero-length trailers; this socket option prevents
        // Nagle from delaying that second small segment behind a delayed ACK
        // (~40 ms on Linux). Must be set before ServerConfig is class-loaded.
        // Not a kernel sysctl — the JDK-documented sun.net.httpserver.nodelay.
        System.setProperty("sun.net.httpserver.nodelay", "true");
    }

    private final CalloutConfig config;
    private final DataSource dataSource;
    private final int port;
    private final int threads;

    private HttpServer httpServer;
    private ExecutorService executor;

    /**
     * Production constructor — uses the shared {@link ConnectionPoolManager} pool.
     */
    public SidecarServer(CalloutConfig config, int port, int threads) {
        this(config, ConnectionPoolManager.getPool(config), port, threads);
    }

    /**
     * Injectable constructor for tests (e.g. in-memory H2 DataSource).
     */
    public SidecarServer(CalloutConfig config, DataSource dataSource, int port, int threads) {
        this.config = config;
        this.dataSource = dataSource;
        this.port = port;
        this.threads = threads;
    }

    public static void main(String[] args) {
        // Dockerfile passes --server; accept and ignore any such flag.
        for (String a : args) {
            if ("--server".equals(a) || a.startsWith("--server=")) {
                // intentional no-op
            }
        }

        int port = parsePositiveInt(envOr("PORT", "8080"), 8080, "PORT");
        int threads = parsePositiveInt(envOr("SERVER_THREADS", "16"), 16, "SERVER_THREADS");

        CalloutConfig config;
        try {
            requireEnv("DB_HOST");
            requireEnv("DB_DATABASE");
            requireEnv("DB_USERNAME");
            requireEnv("DB_PASSWORD");
            config = CalloutConfig.fromEnv();
        } catch (IllegalArgumentException e) {
            System.err.println("[gateway-db-mcp] Configuration error: " + e.getMessage());
            System.exit(1);
            return;
        }

        ConnectionPoolManager.initialize(config);
        SidecarServer server = new SidecarServer(config, port, threads);
        try {
            server.start();
        } catch (IOException e) {
            System.err.println("[gateway-db-mcp] Failed to start HTTP server: " + e.getMessage());
            ConnectionPoolManager.shutdown();
            System.exit(1);
            return;
        }

        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            LOGGER.info("[gateway-db-mcp] Shutdown hook — stopping server");
            server.stop();
            ConnectionPoolManager.shutdown();
        }, "gateway-db-mcp-shutdown"));

        // Block the main thread so the JVM stays alive.
        try {
            Thread.currentThread().join();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            server.stop();
            ConnectionPoolManager.shutdown();
        }
    }

    public synchronized void start() throws IOException {
        if (httpServer != null) {
            throw new IllegalStateException("Server already started");
        }
        httpServer = HttpServer.create(new InetSocketAddress(port), 0);
        executor = Executors.newFixedThreadPool(threads);
        httpServer.setExecutor(executor);
        httpServer.createContext("/", this::handle);
        httpServer.start();

        String allow = config.allowedTables.isEmpty() ? "(all)" : config.allowedTables.toString();
        LOGGER.info("[gateway-db-mcp] Sidecar listening on port " + getPort()
                + " — db.type=" + config.dbType
                + " database=" + config.database
                + " allowedTables=" + allow);
    }

    public synchronized void stop() {
        if (httpServer != null) {
            httpServer.stop(0);
            httpServer = null;
        }
        if (executor != null) {
            executor.shutdown();
            try {
                if (!executor.awaitTermination(5, TimeUnit.SECONDS)) {
                    executor.shutdownNow();
                }
            } catch (InterruptedException e) {
                executor.shutdownNow();
                Thread.currentThread().interrupt();
            }
            executor = null;
        }
    }

    /** Bound port (useful when started with port {@code 0}). */
    public int getPort() {
        if (httpServer == null) return port;
        return httpServer.getAddress().getPort();
    }

    private void handle(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod().toUpperCase();
        String path = exchange.getRequestURI().getPath();
        if (path == null || path.isBlank()) path = "/";

        try {
            if ("GET".equals(method) && isHealth(path)) {
                writeHealth(exchange);
                return;
            }

            String body = readBody(exchange);
            MessageContext ctx = SidecarMessageContext.from(body, exchange.getRequestURI().getRawQuery());

            DBOperation operation = OperationRouter.route(method, path, config);
            OperationResult result = operation.execute(dataSource, ctx, config);
            writeJson(exchange, result.statusCode(), result.body());

        } catch (OperationNotFoundException e) {
            writeError(exchange, 404, "OPERATION_NOT_FOUND", e.getMessage());
        } catch (TableNotAllowedException e) {
            // Sidecar contract: non-allowlisted table → 404 (Apigee callout keeps 403).
            writeError(exchange, 404, "TABLE_NOT_ALLOWED",
                    "Access to table '" + e.tableName() + "' is not permitted");
        } catch (SecurityException e) {
            LOGGER.warning("[gateway-db-mcp] Security violation: " + e.getMessage());
            writeError(exchange, 403, "FORBIDDEN", e.getMessage());
        } catch (IllegalArgumentException e) {
            writeError(exchange, 400, "BAD_REQUEST", e.getMessage());
        } catch (Exception e) {
            LOGGER.severe("[gateway-db-mcp] Internal error: "
                    + e.getClass().getSimpleName() + " — " + e.getMessage());
            writeError(exchange, 500, "INTERNAL_ERROR",
                    "Database operation failed. See server logs for details.");
        } finally {
            exchange.close();
        }
    }

    private void writeHealth(HttpExchange exchange) throws IOException {
        try (Connection c = dataSource.getConnection()) {
            if (c.isValid(2)) {
                writeJson(exchange, 200, "{\"status\":\"ok\"}");
                return;
            }
        } catch (Exception e) {
            LOGGER.warning("[gateway-db-mcp] Health check failed: " + e.getMessage());
        }
        writeJson(exchange, 503, "{\"status\":\"unavailable\"}");
    }

    private static boolean isHealth(String path) {
        return "/health".equalsIgnoreCase(path) || "/health/".equalsIgnoreCase(path);
    }

    private static String readBody(HttpExchange exchange) throws IOException {
        try (InputStream in = exchange.getRequestBody()) {
            byte[] bytes = in.readAllBytes();
            if (bytes.length == 0) return "";
            return new String(bytes, StandardCharsets.UTF_8);
        }
    }

    /**
     * Serialise the body first, then send with an explicit content length and a
     * single write. Passing {@code 0} to {@link HttpExchange#sendResponseHeaders}
     * enables chunked transfer encoding; the terminating zero-length chunk is a
     * separate small TCP segment that stalls ~40 ms behind a delayed ACK on Linux.
     *
     * <p>JDK {@code HttpServer} also flushes headers before the body write. Set
     * {@code sun.net.httpserver.nodelay=true} before the server starts (see
     * {@link #start()}) so Nagle cannot hold that second segment.
     */
    private static void writeJson(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body == null ? new byte[0] : body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        // Explicit header + length argument (must be > 0 for a body; 0 means chunked).
        exchange.getResponseHeaders().set("Content-Length", Integer.toString(bytes.length));
        if (bytes.length == 0) {
            // No body: negative length means "headers only" in HttpServer (not chunked).
            exchange.sendResponseHeaders(status, -1);
            exchange.getResponseBody().close();
            return;
        }
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(bytes);
        }
    }

    private static void writeError(HttpExchange exchange, int status, String code, String message)
            throws IOException {
        String safe = message == null ? "" : message.replace("\"", "'").replace("\n", " ");
        writeJson(exchange, status,
                "{\"error\":{\"code\":\"" + code + "\",\"message\":\"" + safe + "\"}}");
    }

    private static void requireEnv(String name) {
        String v = System.getenv(name);
        if (v == null || v.isBlank()) {
            throw new IllegalArgumentException(
                    "Required environment variable '" + name + "' is missing or empty");
        }
    }

    private static String envOr(String name, String defaultValue) {
        String v = System.getenv(name);
        return (v == null || v.isBlank()) ? defaultValue : v.trim();
    }

    private static int parsePositiveInt(String raw, int fallback, String name) {
        try {
            int n = Integer.parseInt(raw.trim());
            return n > 0 ? n : fallback;
        } catch (NumberFormatException e) {
            LOGGER.warning("[gateway-db-mcp] Invalid " + name + "=" + raw + " — using " + fallback);
            return fallback;
        }
    }

    /**
     * Bridges {@link HttpExchange} request data into the Apigee {@link MessageContext}
     * shape expected by existing {@link DBOperation} implementations.
     */
    static final class SidecarMessageContext implements MessageContext {
        private final Message message;
        private final Map<String, Object> variables;

        private SidecarMessageContext(Message message, Map<String, Object> variables) {
            this.message = message;
            this.variables = variables;
        }

        static SidecarMessageContext from(String body, String rawQuery) {
            Map<String, Object> vars = new HashMap<>();
            if (rawQuery != null && !rawQuery.isBlank()) {
                for (String pair : rawQuery.split("&")) {
                    int eq = pair.indexOf('=');
                    String key = eq < 0 ? pair : pair.substring(0, eq);
                    String val = eq < 0 ? "" : decode(pair.substring(eq + 1));
                    key = decode(key);
                    if (!key.isEmpty()) {
                        vars.put("request.queryparam." + key, val);
                    }
                }
            }
            return new SidecarMessageContext(new SidecarMessage(body == null ? "" : body), vars);
        }

        private static String decode(String s) {
            try {
                return java.net.URLDecoder.decode(s, StandardCharsets.UTF_8);
            } catch (Exception e) {
                return s;
            }
        }

        @Override public Message getMessage() { return message; }
        @Override public Object getVariable(String name) { return variables.get(name); }
        @Override public boolean setVariable(String name, Object value) {
            variables.put(name, value);
            return true;
        }
    }

    static final class SidecarMessage implements Message {
        private Object content;
        private final Map<String, String> headers = new HashMap<>();

        SidecarMessage(Object content) { this.content = content; }

        @Override public Object getContent() { return content; }
        @Override public void setContent(Object content) { this.content = content; }
        @Override public void setHeader(String name, String value) { headers.put(name, value); }
        @Override public String getHeader(String name) { return headers.get(name); }
    }
}

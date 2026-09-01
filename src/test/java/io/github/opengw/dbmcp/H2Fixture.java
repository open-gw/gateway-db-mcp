package io.github.opengw.dbmcp;

import com.apigee.flow.message.Message;
import com.apigee.flow.message.MessageContext;
import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;

import java.sql.Connection;
import java.sql.Statement;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * In-memory H2 (MySQL mode) for operation / sidecar tests.
 * MySQL mode enables backtick quoting used by {@code GetTableRowsOperation}.
 */
public final class H2Fixture implements AutoCloseable {

    public final HikariDataSource dataSource;
    public final CalloutConfig config;
    public final String catalog;

    private H2Fixture(HikariDataSource dataSource, CalloutConfig config, String catalog) {
        this.dataSource = dataSource;
        this.config = config;
        this.catalog = catalog;
    }

    public static H2Fixture create(String allowedTables, int maxRows) throws Exception {
        String catalog = "dbmcp_" + UUID.randomUUID().toString().replace("-", "");
        String url = "jdbc:h2:mem:" + catalog
                + ";MODE=MySQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1;DB_CLOSE_ON_EXIT=FALSE";

        HikariConfig hc = new HikariConfig();
        hc.setJdbcUrl(url);
        hc.setUsername("sa");
        hc.setPassword("");
        hc.setMaximumPoolSize(4);
        HikariDataSource ds = new HikariDataSource(hc);

        try (Connection c = ds.getConnection(); Statement st = c.createStatement()) {
            st.execute("CREATE TABLE orders ("
                    + "id INT PRIMARY KEY, "
                    + "customer VARCHAR(64) NOT NULL, "
                    + "amount DECIMAL(10,2)"
                    + ")");
            st.execute("CREATE TABLE products ("
                    + "id INT PRIMARY KEY, "
                    + "name VARCHAR(64) NOT NULL"
                    + ")");
            st.execute("CREATE TABLE employees ("
                    + "id INT PRIMARY KEY, "
                    + "name VARCHAR(64) NOT NULL"
                    + ")");
            st.execute("INSERT INTO orders VALUES (1, 'alice', 10.00)");
            st.execute("INSERT INTO orders VALUES (2, 'bob', 20.00)");
            st.execute("INSERT INTO orders VALUES (3, 'carol', 30.00)");
            st.execute("INSERT INTO orders VALUES (4, 'dave', 40.00)");
            st.execute("INSERT INTO orders VALUES (5, 'erin', 50.00)");
            st.execute("INSERT INTO products VALUES (1, 'widget')");
            st.execute("INSERT INTO employees VALUES (1, 'hidden')");
        }

        Map<String, String> props = new HashMap<>();
        props.put("db.type", "mysql");
        props.put("db.host", "localhost");
        props.put("db.port", "3306");
        props.put("db.database", catalog);
        props.put("db.username", "sa");
        props.put("db.password", "unused");
        props.put("db.schema", "public");
        props.put("security.readOnly", "true");
        props.put("security.maxRows", String.valueOf(maxRows));
        props.put("security.queryTimeout", "30");
        props.put("api.title", "Test DB MCP Bridge");
        props.put("api.version", "1.0.0-test");
        if (allowedTables != null && !allowedTables.isBlank()) {
            props.put("security.allowedTables", allowedTables);
        }

        return new H2Fixture(ds, CalloutConfig.from(props), catalog);
    }

    static MessageContext messageContext(String body, Map<String, String> queryParams) {
        Map<String, Object> vars = new HashMap<>();
        if (queryParams != null) {
            queryParams.forEach((k, v) -> vars.put("request.queryparam." + k, v));
        }
        return new FakeMessageContext(new FakeMessage(body == null ? "" : body), vars);
    }

    static MessageContext messageContext(String body) {
        return messageContext(body, Map.of());
    }

    static MessageContext emptyContext() {
        return messageContext(null, Map.of());
    }

    @Override
    public void close() {
        dataSource.close();
    }

    /** Minimal Message stub — avoids Mockito/ByteBuddy JDK constraints. */
    static final class FakeMessage implements Message {
        private Object content;
        private final Map<String, String> headers = new HashMap<>();

        FakeMessage(Object content) { this.content = content; }

        @Override public Object getContent() { return content; }
        @Override public void setContent(Object content) { this.content = content; }
        @Override public void setHeader(String name, String value) { headers.put(name, value); }
        @Override public String getHeader(String name) { return headers.get(name); }
    }

    /** Minimal MessageContext stub — avoids Mockito/ByteBuddy JDK constraints. */
    static final class FakeMessageContext implements MessageContext {
        private final Message message;
        private final Map<String, Object> variables;

        FakeMessageContext(Message message, Map<String, Object> variables) {
            this.message = message;
            this.variables = variables;
        }

        @Override public Message getMessage() { return message; }
        @Override public Object getVariable(String name) { return variables.get(name); }
        @Override public boolean setVariable(String name, Object value) {
            variables.put(name, value);
            return true;
        }
    }
}

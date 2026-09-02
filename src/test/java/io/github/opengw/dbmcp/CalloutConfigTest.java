package io.github.opengw.dbmcp;

import io.github.opengw.dbmcp.security.QueryValidator;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Unit tests for CalloutConfig and QueryValidator.
 * No live database required — all tests run in-process.
 */
class CalloutConfigTest {

    private Map<String, String> base() {
        Map<String, String> p = new HashMap<>();
        p.put("db.type",     "mysql");
        p.put("db.host",     "localhost");
        p.put("db.port",     "3306");
        p.put("db.database", "testdb");
        p.put("db.username", "user");
        p.put("db.password", "secret");
        return p;
    }

    @Test void defaults_are_applied() {
        CalloutConfig cfg = CalloutConfig.from(base());
        assertTrue(cfg.readOnly,        "readOnly must default to true");
        assertEquals(1000, cfg.maxRows, "maxRows must default to 1000");
        assertEquals(30, cfg.queryTimeoutSec, "queryTimeout must default to 30");
        assertTrue(cfg.allowedTables.isEmpty(), "allowedTables must default to empty (all)");
        assertEquals(10, cfg.poolMaxSize, "poolMaxSize must default to 10");
    }

    @Test void mysql_jdbc_url() {
        CalloutConfig cfg = CalloutConfig.from(base());
        assertTrue(cfg.jdbcUrl().startsWith("jdbc:mysql://"),  "MySQL URL prefix");
        assertTrue(cfg.jdbcUrl().contains("useSSL=true"),       "MySQL requires SSL");
        assertEquals("com.mysql.cj.jdbc.Driver", cfg.driverClassName());
    }

    @Test void mariadb_jdbc_url() {
        Map<String, String> p = base();
        p.put("db.type", "mariadb");
        p.remove("db.port");
        CalloutConfig cfg = CalloutConfig.from(p);
        assertEquals(3306, cfg.port);
        assertTrue(cfg.jdbcUrl().startsWith("jdbc:mariadb://"), "MariaDB URL prefix");
        assertTrue(cfg.jdbcUrl().contains("useSSL=true"), "MariaDB requires SSL");
        assertEquals("org.mariadb.jdbc.Driver", cfg.driverClassName());
        assertDoesNotThrow(() -> Class.forName(cfg.driverClassName()));
    }

    @Test void postgres_jdbc_url() {
        Map<String, String> p = base();
        p.put("db.type", "postgres");
        p.put("db.port", "5432");
        CalloutConfig cfg = CalloutConfig.from(p);
        assertTrue(cfg.jdbcUrl().startsWith("jdbc:postgresql://"));
        assertEquals("org.postgresql.Driver", cfg.driverClassName());
    }

    @Test void mssql_jdbc_url() {
        Map<String, String> p = base();
        p.put("db.type", "mssql");
        p.put("db.port", "1433");
        CalloutConfig cfg = CalloutConfig.from(p);
        assertTrue(cfg.jdbcUrl().contains("jdbc:sqlserver://"));
        assertTrue(cfg.jdbcUrl().contains("encrypt=true"));
        assertEquals("com.microsoft.sqlserver.jdbc.SQLServerDriver", cfg.driverClassName());
    }

    @Test void allowed_tables_parsed_and_lowercased() {
        Map<String, String> p = base();
        p.put("security.allowedTables", "Orders, Products, CUSTOMERS");
        CalloutConfig cfg = CalloutConfig.from(p);
        assertTrue(cfg.isTableAllowed("orders"));
        assertTrue(cfg.isTableAllowed("ORDERS"));    // case-insensitive
        assertTrue(cfg.isTableAllowed("Products"));
        assertFalse(cfg.isTableAllowed("employees"));
    }

    @Test void empty_allowed_tables_means_all() {
        CalloutConfig cfg = CalloutConfig.from(base());
        assertTrue(cfg.isTableAllowed("anything"));
        assertTrue(cfg.isTableAllowed("sensitive_table"));
    }

    @Test void missing_required_host_throws() {
        Map<String, String> p = base();
        p.remove("db.host");
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class,
                () -> CalloutConfig.from(p));
        assertTrue(ex.getMessage().contains("db.host"));
    }

    @Test void invalid_db_type_throws() {
        Map<String, String> p = base();
        p.put("db.type", "oracle");
        assertThrows(IllegalArgumentException.class, () -> CalloutConfig.from(p));
    }

    @Test void max_rows_out_of_range_throws() {
        Map<String, String> p = base();
        p.put("security.maxRows", "0");
        assertThrows(IllegalArgumentException.class, () -> CalloutConfig.from(p));
        p.put("security.maxRows", "200000");
        assertThrows(IllegalArgumentException.class, () -> CalloutConfig.from(p));
    }

    @Test void pool_key_excludes_password() {
        CalloutConfig cfg = CalloutConfig.from(base());
        assertFalse(cfg.poolKey().contains("secret"), "Pool key must not include password");
        assertTrue(cfg.poolKey().contains("localhost"));
        assertTrue(cfg.poolKey().contains("testdb"));
    }
}

// ─────────────────────────────────────────────────────────────────────────────

class QueryValidatorTest {

    // ── Allowed queries ───────────────────────────────────────────────────────

    @Test void select_is_allowed_in_read_only() {
        assertDoesNotThrow(() -> QueryValidator.validate("SELECT * FROM orders", true));
    }

    @Test void select_with_where_is_allowed() {
        assertDoesNotThrow(() ->
            QueryValidator.validate("SELECT id, name FROM products WHERE category = ?", true));
    }

    @Test void select_with_join_is_allowed() {
        assertDoesNotThrow(() ->
            QueryValidator.validate("SELECT o.id, c.name FROM orders o JOIN customers c ON o.customer_id = c.id", true));
    }

    // ── DDL — always blocked ──────────────────────────────────────────────────

    @ParameterizedTest
    @ValueSource(strings = {
        "DROP TABLE orders",
        "ALTER TABLE users ADD COLUMN hacked TEXT",
        "CREATE TABLE evil (id INT)",
        "TRUNCATE TABLE audit_log",
        "GRANT ALL ON *.* TO 'hacker'@'%'",
    })
    void ddl_always_blocked(String sql) {
        assertThrows(SecurityException.class, () -> QueryValidator.validate(sql, true));
        assertThrows(SecurityException.class, () -> QueryValidator.validate(sql, false));
    }

    // ── Write ops — blocked when readOnly=true ────────────────────────────────

    @ParameterizedTest
    @ValueSource(strings = {
        "INSERT INTO orders VALUES (1, 2, 3)",
        "UPDATE users SET role = 'admin' WHERE 1=1",
        "DELETE FROM sessions",
        "REPLACE INTO orders VALUES (1, 2, 3)",
    })
    void write_blocked_in_read_only_mode(String sql) {
        assertThrows(SecurityException.class, () -> QueryValidator.validate(sql, true));
    }

    // ── Injection patterns ────────────────────────────────────────────────────

    @Test void stacked_query_blocked() {
        assertThrows(SecurityException.class, () ->
            QueryValidator.validate("SELECT 1; DROP TABLE orders", true));
    }

    @Test void union_injection_blocked() {
        assertThrows(SecurityException.class, () ->
            QueryValidator.validate("SELECT id FROM users UNION SELECT password FROM admin", true));
    }

    @Test void union_all_injection_blocked() {
        assertThrows(SecurityException.class, () ->
            QueryValidator.validate("SELECT 1 UNION ALL SELECT table_name FROM information_schema.tables", true));
    }

    // ── Comment handling ──────────────────────────────────────────────────────

    @Test void block_comments_stripped_before_analysis() {
        // DDL hidden inside comment should still be safe — but the resulting cleaned
        // string should start with SELECT
        assertDoesNotThrow(() ->
            QueryValidator.validate("SELECT /* DROP TABLE orders */ id FROM orders", true));
    }

    @Test void inline_comments_stripped() {
        assertDoesNotThrow(() ->
            QueryValidator.validate("SELECT id FROM orders -- WHERE 1=1", true));
    }

    // ── Edge cases ────────────────────────────────────────────────────────────

    @Test void empty_sql_throws_illegal_argument() {
        assertThrows(IllegalArgumentException.class, () ->
            QueryValidator.validate("", true));
        assertThrows(IllegalArgumentException.class, () ->
            QueryValidator.validate("   ", true));
        assertThrows(IllegalArgumentException.class, () ->
            QueryValidator.validate(null, true));
    }

    @Test void sql_exceeding_max_length_throws_security() {
        String huge = "SELECT " + "a".repeat(10_000);
        assertThrows(SecurityException.class, () ->
            QueryValidator.validate(huge, true));
    }

    // ── Identifier validation ─────────────────────────────────────────────────

    @Test void valid_identifiers_pass() {
        assertDoesNotThrow(() -> QueryValidator.validateIdentifier("orders"));
        assertDoesNotThrow(() -> QueryValidator.validateIdentifier("order_items"));
        assertDoesNotThrow(() -> QueryValidator.validateIdentifier("MyTable2"));
        assertDoesNotThrow(() -> QueryValidator.validateIdentifier("$special"));
    }

    @Test void injection_in_identifier_throws() {
        assertThrows(SecurityException.class, () ->
            QueryValidator.validateIdentifier("orders; DROP TABLE orders"));
        assertThrows(SecurityException.class, () ->
            QueryValidator.validateIdentifier("orders' OR '1'='1"));
        assertThrows(SecurityException.class, () ->
            QueryValidator.validateIdentifier("../etc/passwd"));
    }

    @Test void empty_identifier_throws() {
        assertThrows(IllegalArgumentException.class, () ->
            QueryValidator.validateIdentifier(""));
        assertThrows(IllegalArgumentException.class, () ->
            QueryValidator.validateIdentifier(null));
    }
}

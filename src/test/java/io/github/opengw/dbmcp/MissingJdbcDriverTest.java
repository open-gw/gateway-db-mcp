package io.github.opengw.dbmcp;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class MissingJdbcDriverTest {

    @Test
    void absent_driver_message_names_mariadb_profile() {
        MissingJdbcDriverException ex = assertThrows(
                MissingJdbcDriverException.class,
                () -> JdbcDriverSupport.requireOnClasspath(
                        "mariadb", "io.github.opengw.dbmcp.NoSuchJdbcDriver"));
        String msg = ex.getMessage();
        assertTrue(msg.contains("NoSuchJdbcDriver"), msg);
        assertTrue(msg.contains("db.type='mariadb'"), msg);
        assertTrue(msg.contains("-Pmariadb"), msg);
        assertTrue(msg.contains("not on the classpath"), msg);
    }

    @Test
    void absent_driver_message_names_oracle_profile() {
        MissingJdbcDriverException ex = assertThrows(
                MissingJdbcDriverException.class,
                () -> JdbcDriverSupport.requireOnClasspath(
                        "oracle", "oracle.jdbc.DoesNotExistDriver"));
        assertTrue(ex.getMessage().contains("-Poracle"), ex.getMessage());
        assertTrue(ex.getMessage().contains("ojdbc11"), ex.getMessage());
    }

    @Test
    void bundled_mysql_driver_is_present() {
        assertDoesNotThrow(() ->
                JdbcDriverSupport.requireOnClasspath("mysql", "com.mysql.cj.jdbc.Driver"));
    }

    @Test
    void message_builder_mentions_profile() {
        String msg = MissingJdbcDriverException.message("mariadb", "org.mariadb.jdbc.Driver");
        assertTrue(msg.contains("org.mariadb.jdbc.Driver"));
        assertTrue(msg.contains("-Pmariadb"));
    }
}

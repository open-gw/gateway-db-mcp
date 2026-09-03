package io.github.opengw.dbmcp;

/**
 * Ensures the configured JDBC driver class is loadable before HikariCP starts.
 * Optional-profile drivers (MariaDB, Oracle) fail here with an actionable
 * message instead of a bare {@link ClassNotFoundException} or a silent fallback.
 */
public final class JdbcDriverSupport {

    private JdbcDriverSupport() {}

    public static void requireOnClasspath(String dbType, String driverClassName) {
        if (driverClassName == null || driverClassName.isBlank()) {
            throw new IllegalArgumentException("driverClassName must not be blank");
        }
        try {
            Class.forName(driverClassName);
        } catch (ClassNotFoundException e) {
            throw new MissingJdbcDriverException(dbType, driverClassName, e);
        }
    }
}

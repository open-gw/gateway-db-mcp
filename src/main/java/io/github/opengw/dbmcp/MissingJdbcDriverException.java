package io.github.opengw.dbmcp;

/**
 * Thrown when {@code db.type} names a JDBC driver that is not on the runtime
 * classpath (typically an optional profile driver that was not packaged).
 */
public final class MissingJdbcDriverException extends IllegalStateException {

    public MissingJdbcDriverException(String dbType, String driverClassName) {
        super(message(dbType, driverClassName));
    }

    public MissingJdbcDriverException(String dbType, String driverClassName, Throwable cause) {
        super(message(dbType, driverClassName), cause);
    }

    static String message(String dbType, String driverClassName) {
        String how;
        switch (dbType == null ? "" : dbType) {
            case "mariadb":
                how = "Rebuild with Maven profile -Pmariadb "
                        + "(mvn clean package -Pmariadb). See docs/LICENSING.md.";
                break;
            case "oracle":
                how = "Install ojdbc11 into the local Maven repository, then rebuild "
                        + "with -Poracle. See README Building.";
                break;
            default:
                how = "Ensure the JDBC driver JAR for db.type='" + dbType
                        + "' is on the classpath.";
                break;
        }
        return "[gateway-db-mcp] JDBC driver '" + driverClassName
                + "' for db.type='" + dbType + "' is not on the classpath. " + how;
    }
}

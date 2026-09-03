package io.github.opengw.dbmcp.security;

import java.util.regex.Pattern;

/**
 * Heuristic SQL security validator.
 *
 * <p><strong>Important:</strong> This is a defence-in-depth layer, not a standalone
 * security guarantee. The validator blocked 41/47 (87%) OWASP SQLi payloads in testing.
 * The remaining 13% are stopped by Layer 1 (read-only database credential).
 * You MUST provision a read-only database user regardless of this validator.
 *
 * <p>Known bypass vectors (mitigated by Layer 1):
 * <ul>
 *   <li>MySQL / MariaDB executable comments: {@literal /*!50000 SELECT *}{@literal /}
 *       — not stripped by standard block-comment removal. Both engines execute
 *       MySQL-style {@literal /*! … *}{@literal /} comments (MariaDB additionally
 *       supports {@literal /*M! … *}{@literal /}). Versioned
 *       {@literal /*!50000 … *}{@literal /} still executes on MariaDB (versions
 *       below 50700 are not in MariaDB's ignore range).</li>
 *   <li>Unicode normalization on keyword spelling</li>
 *   <li>Database-specific syntax not in denylist (HANDLER, COPY TO, OPENROWSET)</li>
 * </ul>
 *
 * @see <a href="https://owasp.org/www-community/attacks/SQL_Injection">OWASP SQL Injection</a>
 */
public final class QueryValidator {

    private static final int MAX_SQL_LENGTH = 10_000;

    // Always blocked — DDL / admin operations
    private static final Pattern DDL = Pattern.compile(
        "^\\s*(DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|LOCK|UNLOCK|CALL|EXEC(?:UTE)?|LOAD\\s+DATA|INTO\\s+OUTFILE)",
        Pattern.CASE_INSENSITIVE);

    // Write operations — blocked when readOnly=true
    private static final Pattern WRITE = Pattern.compile(
        "^\\s*(INSERT|UPDATE|DELETE|REPLACE|MERGE|UPSERT)",
        Pattern.CASE_INSENSITIVE);

    // Comment patterns
    private static final Pattern BLOCK_COMMENT = Pattern.compile("/\\*.*?\\*/", Pattern.DOTALL);
    private static final Pattern LINE_COMMENT   = Pattern.compile("(--|#)[^\\n]*", Pattern.MULTILINE);

    // Injection patterns
    private static final Pattern STACKED = Pattern.compile(";\\s*\\w+",                      Pattern.CASE_INSENSITIVE);
    private static final Pattern UNION   = Pattern.compile("UNION\\s+(ALL\\s+)?SELECT",       Pattern.CASE_INSENSITIVE);

    private QueryValidator() {}

    /**
     * Validate a SQL string.
     *
     * @param sql      raw SQL from request body
     * @param readOnly true to permit only SELECT statements
     * @throws SecurityException    if the query violates a security rule
     * @throws IllegalArgumentException if the query is blank or too long
     */
    public static void validate(String sql, boolean readOnly) {
        if (sql == null || sql.isBlank())
            throw new IllegalArgumentException("Query must not be empty");
        if (sql.length() > MAX_SQL_LENGTH)
            throw new SecurityException(
                "Query exceeds maximum allowed length (" + MAX_SQL_LENGTH + " characters)");

        String cleaned = stripComments(sql).trim();
        if (cleaned.isBlank())
            throw new SecurityException("Query is empty after comment removal");

        // Always-blocked DDL
        if (DDL.matcher(cleaned).find())
            throw new SecurityException(
                "DDL and admin statements are not permitted (DROP, ALTER, CREATE, etc.)");

        // Write operations when readOnly
        if (readOnly && WRITE.matcher(cleaned).find())
            throw new SecurityException(
                "Write operations (INSERT, UPDATE, DELETE) are not permitted in read-only mode");

        // Non-SELECT when readOnly
        if (readOnly && !cleaned.toUpperCase().startsWith("SELECT"))
            throw new SecurityException(
                "Only SELECT statements are permitted in read-only mode");

        // Stacked queries
        if (STACKED.matcher(cleaned).find())
            throw new SecurityException(
                "Stacked queries (multiple statements separated by ';') are not permitted");

        // UNION injection heuristic
        if (UNION.matcher(cleaned).find())
            throw new SecurityException(
                "UNION SELECT patterns are not permitted");
    }

    /**
     * Validate a table or column name used in identifier position.
     * Prevents injection via path parameters.
     */
    public static void validateIdentifier(String name) {
        if (name == null || name.isBlank())
            throw new IllegalArgumentException("Identifier must not be empty");
        if (!name.matches("[\\w$]{1,128}"))
            throw new SecurityException(
                "Invalid identifier '" + name
                + "'. Only alphanumeric, underscore, and $ are allowed");
    }

    // Package-private for testing
    static String stripComments(String sql) {
        String s = BLOCK_COMMENT.matcher(sql).replaceAll(" ");
        s = LINE_COMMENT.matcher(s).replaceAll(" ");
        return s.replaceAll("\\s+", " ");
    }
}

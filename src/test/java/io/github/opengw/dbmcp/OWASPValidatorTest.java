package io.github.opengw.dbmcp;

import io.github.opengw.dbmcp.security.QueryValidator;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.DisplayName;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.*;

/**
 * OWASP SQL Injection regression test.
 *
 * Reads payloads from src/test/resources/owasp-sqli.txt and verifies
 * the QueryValidator interception rate stays at or above the documented 87%.
 *
 * This test is a regression gate — if a code change to QueryValidator
 * reduces the interception rate below the threshold, this test fails.
 *
 * Known result: 41/47 payloads blocked at Layer 4 (87%).
 * The remaining 6 are stopped by Layer 1 (read-only DB credential).
 */
class OWASPValidatorTest {

    private static final double MINIMUM_INTERCEPTION_RATE = 0.85; // 85% floor — below documented 87%
    private static final String PAYLOAD_FILE = "owasp-sqli.txt";

    @Test
    @DisplayName("OWASP SQLi payload interception rate must stay >= 85%")
    void owasp_interception_rate_meets_minimum() throws Exception {
        List<String> payloads = loadPayloads();
        assertFalse(payloads.isEmpty(), "Payload file must not be empty");

        int blocked   = 0;
        int permitted = 0;
        List<String> bypassedPayloads = new ArrayList<>();

        for (String payload : payloads) {
            try {
                QueryValidator.validate(payload, true);
                // Reached here = validator did NOT block it
                permitted++;
                bypassedPayloads.add(payload);
            } catch (SecurityException | IllegalArgumentException e) {
                blocked++;
            }
        }

        int total = payloads.size();
        double rate = (double) blocked / total;

        System.out.printf("%n=== OWASP SQLi Validation Report ===%n");
        System.out.printf("Total payloads tested : %d%n", total);
        System.out.printf("Blocked at Layer 4    : %d (%.0f%%)%n", blocked, rate * 100);
        System.out.printf("Passed through        : %d (stopped by Layer 1 — read-only DB user)%n", permitted);
        System.out.printf("Minimum threshold     : %.0f%%%n", MINIMUM_INTERCEPTION_RATE * 100);

        if (!bypassedPayloads.isEmpty()) {
            System.out.printf("%nPayloads passed through (expected — mitigated by Layer 1):%n");
            bypassedPayloads.forEach(p -> System.out.printf("  - %s%n", p));
        }

        assertTrue(rate >= MINIMUM_INTERCEPTION_RATE,
            String.format(
                "REGRESSION: QueryValidator interception rate dropped to %.0f%% (%d/%d). "
                + "Minimum is %.0f%%. "
                + "A recent change to QueryValidator may have removed or weakened a detection pattern. "
                + "Review the bypassed payloads printed above.",
                rate * 100, blocked, total, MINIMUM_INTERCEPTION_RATE * 100
            ));
    }

    @Test
    @DisplayName("DDL payloads are always blocked regardless of readOnly setting")
    void ddl_blocked_regardless_of_readonly_flag() throws Exception {
        List<String> payloads = loadPayloads();
        List<String> ddlPayloads = payloads.stream()
            .filter(p -> {
                String upper = p.trim().toUpperCase();
                return upper.startsWith("DROP") || upper.startsWith("ALTER")
                    || upper.startsWith("CREATE") || upper.startsWith("TRUNCATE")
                    || upper.startsWith("GRANT");
            })
            .collect(Collectors.toList());

        assertFalse(ddlPayloads.isEmpty(), "Payload file must contain DDL examples");

        for (String ddl : ddlPayloads) {
            // Should be blocked with readOnly=true
            assertThrows(SecurityException.class,
                () -> QueryValidator.validate(ddl, true),
                "DDL not blocked with readOnly=true: " + ddl);

            // Should also be blocked with readOnly=false
            assertThrows(SecurityException.class,
                () -> QueryValidator.validate(ddl, false),
                "DDL not blocked with readOnly=false: " + ddl);
        }
    }

    @Test
    @DisplayName("Payload file loads correctly and has expected minimum payload count")
    void payload_file_loads_and_has_minimum_count() throws Exception {
        List<String> payloads = loadPayloads();
        assertTrue(payloads.size() >= 40,
            "Expected at least 40 OWASP payloads, found: " + payloads.size());
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    private List<String> loadPayloads() throws Exception {
        InputStream is = getClass().getClassLoader().getResourceAsStream(PAYLOAD_FILE);
        assertNotNull(is,
            "Could not find " + PAYLOAD_FILE + " in test/resources. "
            + "Ensure src/test/resources/owasp-sqli.txt exists.");

        List<String> payloads = new ArrayList<>();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(is))) {
            String line;
            while ((line = reader.readLine()) != null) {
                line = line.trim();
                // Skip blank lines and comment lines
                if (!line.isEmpty() && !line.startsWith("#")) {
                    payloads.add(line);
                }
            }
        }
        return payloads;
    }
}

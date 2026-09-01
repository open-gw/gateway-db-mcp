package com.apigee.flow.message;

/**
 * Minimal Message API surface used by gateway-db-mcp.
 * On Apigee X the platform provides the real class (parent-first).
 * Bundled here so the sidecar fat JAR is runnable with {@code java -jar}.
 */
public interface Message {
    Object getContent();
    void setContent(Object content);
    void setHeader(String name, String value);
    String getHeader(String name);
}

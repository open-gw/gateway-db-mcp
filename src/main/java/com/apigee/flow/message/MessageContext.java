package com.apigee.flow.message;

/**
 * Minimal MessageContext API surface used by gateway-db-mcp.
 * On Apigee X the platform provides the real class (parent-first).
 * Bundled here so the sidecar fat JAR is runnable with {@code java -jar}.
 */
public interface MessageContext {
    Message getMessage();
    Object getVariable(String name);
    boolean setVariable(String name, Object value);
}

package com.apigee.flow.execution;

/** Minimal stub — Apigee platform supplies the real type at runtime (parent-first). */
public class ExecutionResult {
    public static final ExecutionResult SUCCESS = new ExecutionResult(true);
    public static final ExecutionResult ABORT   = new ExecutionResult(false);
    private final boolean success;
    public ExecutionResult(boolean success) { this.success = success; }
    public boolean isSuccess() { return success; }
}

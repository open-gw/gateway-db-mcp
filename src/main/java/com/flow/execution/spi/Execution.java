package com.apigee.flow.execution.spi;

import com.apigee.flow.execution.ExecutionContext;
import com.apigee.flow.execution.ExecutionResult;
import com.apigee.flow.message.MessageContext;

/** Minimal stub — Apigee platform supplies the real type at runtime (parent-first). */
public interface Execution {
    ExecutionResult execute(MessageContext messageContext, ExecutionContext executionContext);
}

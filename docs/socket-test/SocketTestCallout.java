package io.github.opengw.dbmcp.sockettest;

import com.apigee.flow.execution.ExecutionContext;
import com.apigee.flow.execution.ExecutionResult;
import com.apigee.flow.execution.spi.Execution;
import com.apigee.flow.message.MessageContext;

import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.Map;

/**
 * SocketTestCallout — Apigee X JVM TCP socket validation.
 *
 * Deploy this BEFORE deploying gateway-db-mcp to verify that the Apigee X
 * JVM sandbox permits outbound TCP connections to your database host.
 *
 * If this callout returns {"result":"success"}, embedded mode will work.
 * If it returns {"result":"failed"} with a SecurityException, use sidecar mode.
 *
 * Usage:
 *   GET /socket-test?host=10.x.x.x&port=3306
 *
 * See docs/socket-test/README.md for deployment instructions.
 */
public class SocketTestCallout implements Execution {

    private static final int TIMEOUT_MS = 5000;

    public SocketTestCallout(Map<String, String> properties) {}

    @Override
    public ExecutionResult execute(MessageContext msgCtx, ExecutionContext execCtx) {
        String host = getParam(msgCtx, "request.queryparam.host");
        String portStr = getParam(msgCtx, "request.queryparam.port");

        if (host == null || host.isBlank()) {
            return writeResult(msgCtx, 400,
                "{\"result\":\"error\",\"message\":\"Missing required query param: host\"}");
        }
        if (portStr == null || portStr.isBlank()) {
            return writeResult(msgCtx, 400,
                "{\"result\":\"error\",\"message\":\"Missing required query param: port\"}");
        }

        int port;
        try {
            port = Integer.parseInt(portStr.trim());
        } catch (NumberFormatException e) {
            return writeResult(msgCtx, 400,
                "{\"result\":\"error\",\"message\":\"Invalid port: " + portStr + "\"}");
        }

        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress(host.trim(), port), TIMEOUT_MS);
            return writeResult(msgCtx, 200,
                "{\"result\":\"success\","
                + "\"host\":\"" + host.trim() + "\","
                + "\"port\":" + port + ","
                + "\"message\":\"TCP connection succeeded — embedded mode will work\"}");
        } catch (SecurityException e) {
            return writeResult(msgCtx, 403,
                "{\"result\":\"failed\","
                + "\"host\":\"" + host.trim() + "\","
                + "\"port\":" + port + ","
                + "\"error\":\"SecurityException\","
                + "\"message\":\"JVM sandbox blocked TCP connection — use sidecar mode\","
                + "\"detail\":\"" + e.getMessage() + "\"}");
        } catch (Exception e) {
            return writeResult(msgCtx, 503,
                "{\"result\":\"failed\","
                + "\"host\":\"" + host.trim() + "\","
                + "\"port\":" + port + ","
                + "\"error\":\"" + e.getClass().getSimpleName() + "\","
                + "\"message\":\"" + sanitize(e.getMessage()) + "\"}");
        }
    }

    private ExecutionResult writeResult(MessageContext ctx, int status, String body) {
        ctx.setVariable("response.status.code", status);
        ctx.getMessage().setContent(body);
        ctx.getMessage().setHeader("Content-Type", "application/json");
        return ExecutionResult.SUCCESS;
    }

    private String getParam(MessageContext ctx, String var) {
        Object v = ctx.getVariable(var);
        return v != null ? v.toString().trim() : null;
    }

    private String sanitize(String msg) {
        if (msg == null) return "no detail";
        return msg.replace("\"", "'").replace("\n", " ").substring(0, Math.min(msg.length(), 200));
    }
}

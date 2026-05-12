#!/usr/bin/env bash
# install-apigee-stubs.sh
# Creates minimal Apigee Java Callout stub JARs and installs them
# into the local Maven repository. Run once before building.
#
# Usage:  chmod +x scripts/install-apigee-stubs.sh
#         ./scripts/install-apigee-stubs.sh

set -e

WORK_DIR=$(mktemp -d)
echo "Working in $WORK_DIR"

# ── Stub sources ─────────────────────────────────────────────────────────────

mkdir -p "$WORK_DIR/src/com/apigee/flow/execution/spi"
mkdir -p "$WORK_DIR/src/com/apigee/flow/message"

cat > "$WORK_DIR/src/com/apigee/flow/execution/ExecutionResult.java" << 'EOF'
package com.apigee.flow.execution;
public class ExecutionResult {
    public static final ExecutionResult SUCCESS = new ExecutionResult(true);
    public static final ExecutionResult ABORT   = new ExecutionResult(false);
    private final boolean success;
    public ExecutionResult(boolean success) { this.success = success; }
    public boolean isSuccess() { return success; }
}
EOF

cat > "$WORK_DIR/src/com/apigee/flow/execution/ExecutionContext.java" << 'EOF'
package com.apigee.flow.execution;
public interface ExecutionContext {}
EOF

cat > "$WORK_DIR/src/com/apigee/flow/execution/spi/Execution.java" << 'EOF'
package com.apigee.flow.execution.spi;
import com.apigee.flow.execution.ExecutionContext;
import com.apigee.flow.execution.ExecutionResult;
import com.apigee.flow.message.MessageContext;
public interface Execution {
    ExecutionResult execute(MessageContext messageContext, ExecutionContext executionContext);
}
EOF

cat > "$WORK_DIR/src/com/apigee/flow/message/Message.java" << 'EOF'
package com.apigee.flow.message;
public interface Message {
    Object  getContent();
    void    setContent(Object content);
    void    setHeader(String name, String value);
    String  getHeader(String name);
}
EOF

cat > "$WORK_DIR/src/com/apigee/flow/message/MessageContext.java" << 'EOF'
package com.apigee.flow.message;
public interface MessageContext {
    Message getMessage();
    Object  getVariable(String name);
    boolean setVariable(String name, Object value);
}
EOF

# ── Compile ───────────────────────────────────────────────────────────────────

mkdir -p "$WORK_DIR/classes"
echo "Compiling stubs..."
find "$WORK_DIR/src" -name "*.java" | xargs javac -d "$WORK_DIR/classes"

# ── Package ───────────────────────────────────────────────────────────────────

echo "Packaging JARs..."
jar cf "$WORK_DIR/expressions-1.0.0.jar"  -C "$WORK_DIR/classes" .
jar cf "$WORK_DIR/message-flow-1.0.0.jar" -C "$WORK_DIR/classes" .

# ── Install into local Maven repo ─────────────────────────────────────────────

echo "Installing expressions-1.0.0.jar..."
mvn install:install-file \
  -Dfile="$WORK_DIR/expressions-1.0.0.jar" \
  -DgroupId=com.apigee.edge \
  -DartifactId=expressions \
  -Dversion=1.0.0 \
  -Dpackaging=jar \
  -q

echo "Installing message-flow-1.0.0.jar..."
mvn install:install-file \
  -Dfile="$WORK_DIR/message-flow-1.0.0.jar" \
  -DgroupId=com.apigee.edge \
  -DartifactId=message-flow \
  -Dversion=1.0.0 \
  -Dpackaging=jar \
  -q

# ── Cleanup ───────────────────────────────────────────────────────────────────

rm -rf "$WORK_DIR"
echo ""
echo "Done. Apigee stub JARs installed to local Maven repository."
echo "Run: mvn clean test"

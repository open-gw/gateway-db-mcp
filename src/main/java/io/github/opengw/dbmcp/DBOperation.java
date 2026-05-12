package io.github.opengw.dbmcp;

import com.apigee.flow.message.MessageContext;
import javax.sql.DataSource;

/** Strategy interface implemented by every bridge operation. */
public interface DBOperation {
    String name();
    OperationResult execute(DataSource ds, MessageContext msgCtx, CalloutConfig config)
            throws Exception;
}

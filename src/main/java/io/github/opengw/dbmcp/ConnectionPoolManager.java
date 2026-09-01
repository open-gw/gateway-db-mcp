package io.github.opengw.dbmcp;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;

import javax.sql.DataSource;
import java.util.concurrent.ConcurrentHashMap;
import java.util.logging.Logger;

/**
 * Manages HikariCP connection pools — one pool per unique (dbType, host, port, database, username).
 * Pools are initialized eagerly at callout startup and reused across all requests.
 */
public class ConnectionPoolManager {

    private static final Logger LOGGER = Logger.getLogger(ConnectionPoolManager.class.getName());
    private static final ConcurrentHashMap<String, HikariDataSource> POOLS =
            new ConcurrentHashMap<>();

    private ConnectionPoolManager() {}

    public static void initialize(CalloutConfig config) {
        POOLS.computeIfAbsent(config.poolKey(), k -> createPool(config));
    }

    public static DataSource getPool(CalloutConfig config) {
        return POOLS.computeIfAbsent(config.poolKey(), k -> createPool(config));
    }

    /** Closes every pool. Used by the sidecar JVM shutdown hook. */
    public static void shutdown() {
        for (HikariDataSource ds : POOLS.values()) {
            try {
                ds.close();
            } catch (Exception e) {
                LOGGER.warning("[gateway-db-mcp] Error closing pool: " + e.getMessage());
            }
        }
        POOLS.clear();
        LOGGER.info("[gateway-db-mcp] All connection pools closed");
    }

    private static HikariDataSource createPool(CalloutConfig c) {
        LOGGER.info("[gateway-db-mcp] Creating pool — " + c.poolKey());

        HikariConfig hk = new HikariConfig();
        hk.setJdbcUrl(c.jdbcUrl());
        hk.setDriverClassName(c.driverClassName());
        hk.setUsername(c.username);
        hk.setPassword(c.password);
        hk.setMaximumPoolSize(c.poolMaxSize);
        hk.setMinimumIdle(c.poolMinIdle);
        hk.setConnectionTimeout(c.poolConnectionTimeoutMs);
        hk.setIdleTimeout(c.poolIdleTimeoutMs);
        hk.setMaxLifetime(c.poolMaxLifetimeMs);
        hk.setConnectionTestQuery("SELECT 1");
        hk.setPoolName("gw-dbmcp-" + c.dbType + "-" + c.database);

        if ("mysql".equals(c.dbType)) {
            hk.addDataSourceProperty("cachePrepStmts",          "true");
            hk.addDataSourceProperty("prepStmtCacheSize",        "250");
            hk.addDataSourceProperty("prepStmtCacheSqlLimit",    "2048");
            hk.addDataSourceProperty("useServerPrepStmts",       "true");
            hk.addDataSourceProperty("rewriteBatchedStatements", "true");
        }

        HikariDataSource ds = new HikariDataSource(hk);
        LOGGER.info("[gateway-db-mcp] Pool ready — " + c.poolKey());
        return ds;
    }
}

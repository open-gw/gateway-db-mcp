FROM eclipse-temurin:11-jre-jammy

LABEL org.opencontainers.image.title="gateway-db-mcp"
LABEL org.opencontainers.image.description="Config-driven JDBC bridge for enterprise API gateway MCP proxies"
LABEL org.opencontainers.image.source="https://github.com/open-gw/gateway-db-mcp"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# The Ubuntu base ships without curl or wget; the healthcheck needs one.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY target/gateway-db-mcp-*.jar app.jar

# Debian-family user creation. addgroup -S / adduser -S is BusyBox syntax and
# fails here.
RUN groupadd --system dbmcp \
 && useradd --system --gid dbmcp --no-create-home --shell /usr/sbin/nologin dbmcp
USER dbmcp

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1

ENTRYPOINT ["java", \
  "-Djava.security.egd=file:/dev/./urandom", \
  "-jar", "app.jar", \
  "--server"]

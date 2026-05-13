FROM eclipse-temurin:25-jre-alpine

LABEL org.opencontainers.image.title="gateway-db-mcp"
LABEL org.opencontainers.image.description="Config-driven JDBC bridge for enterprise API gateway MCP proxies"
LABEL org.opencontainers.image.source="https://github.com/open-gw/gateway-db-mcp"
LABEL org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app

# Copy the shaded JAR (built with: mvn package)
COPY target/gateway-db-mcp-*.jar app.jar

# Non-root user for security
RUN addgroup -S dbmcp && adduser -S dbmcp -G dbmcp
USER dbmcp

EXPOSE 8080

# Health check via the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD wget -qO- http://localhost:8080/health || exit 1

ENTRYPOINT ["java", \
  "-Djava.security.egd=file:/dev/./urandom", \
  "-jar", "app.jar", \
  "--server"]

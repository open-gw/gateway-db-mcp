# FastMCP OpenAPI→MCP server for the bench harness.
# python:3.12-slim is multi-arch (amd64/arm64).
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py conformance.py loadgen.py ./

ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=5s --retries=30 --start-period=15s \
  CMD curl -fsS http://127.0.0.1:8080/health || exit 1

CMD ["python", "server.py"]

# mcp-server-fetch (modelcontextprotocol/servers, MIT) as a sandboxed MCP image.
# Build context is deploy/mcp/ so bridge.sh is shared:
#   docker build -f deploy/mcp/fetch/Dockerfile -t gagent-mcp-fetch:2026.10.10 deploy/mcp
# Pins: base by digest, every Python dependency by hash (requirements.lock).
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY fetch/requirements.lock /tmp/requirements.lock
RUN pip install --require-hashes --no-deps -r /tmp/requirements.lock && rm /tmp/requirements.lock

COPY bridge.sh /usr/local/bin/mcp-bridge
RUN chmod 0755 /usr/local/bin/mcp-bridge \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin mcp

USER 10001:10001
WORKDIR /tmp
LABEL gagent.mcp.name="fetch" gagent.mcp.source="https://github.com/modelcontextprotocol/servers" gagent.mcp.version="2026.10.10" gagent.mcp.license="MIT"
ENTRYPOINT ["/usr/local/bin/mcp-bridge"]
CMD ["mcp-server-fetch"]

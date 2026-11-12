# arxiv-mcp-server (blazickjp/arxiv-mcp-server, Apache-2.0) as a sandboxed MCP image.
# Build context is deploy/mcp so bridge.sh is shared:
#   docker build -f deploy/mcp/arxiv/Dockerfile -t gagent-mcp-arxiv:0.8.2 deploy/mcp
# Pins: base by digest (same as fetch). Every Python package, including arxiv-mcp-server itself,
# is pinned by sha256 in requirements.lock: dependencies exported from the pinned commit's uv.lock
# (--no-dev), and the package by its PyPI wheel, verified identical to commit 572e301.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY arxiv/requirements.lock /tmp/requirements.lock
RUN pip install --require-hashes --no-deps -r /tmp/requirements.lock && rm /tmp/requirements.lock

COPY bridge.sh /usr/local/bin/mcp-bridge
RUN chmod 0755 /usr/local/bin/mcp-bridge \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin mcp

USER 10001:10001
WORKDIR /tmp
LABEL gagent.mcp.name="arxiv" gagent.mcp.source="https://github.com/blazickjp/arxiv-mcp-server" gagent.mcp.version="0.8.2" gagent.mcp.license="Apache-2.0"
ENTRYPOINT ["/usr/local/bin/mcp-bridge"]
CMD ["python", "-m", "arxiv_mcp_server"]

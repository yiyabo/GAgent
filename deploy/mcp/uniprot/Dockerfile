# @cyanheads/uniprot-mcp-server (Apache-2.0) as a sandboxed MCP image.
#   docker build -f deploy/mcp/uniprot/Dockerfile -t gagent-mcp-uniprot:0.2.6 deploy/mcp
# Pins: base by digest, npm dependencies by lockfile with integrity hashes,
# install scripts disabled.
FROM node:24-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5

WORKDIR /opt/uniprot
COPY uniprot/package.json uniprot/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

COPY bridge.sh /usr/local/bin/mcp-bridge
RUN chmod 0755 /usr/local/bin/mcp-bridge \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin mcp

# stdio transport; Node 24's fetch honours HTTPS_PROXY only with this switch.
ENV NODE_ENV=production MCP_TRANSPORT_TYPE=stdio MCP_LOG_LEVEL=warn NODE_USE_ENV_PROXY=1
USER 10001:10001
WORKDIR /tmp
LABEL gagent.mcp.name="uniprot" gagent.mcp.source="https://github.com/cyanheads/uniprot-mcp-server" gagent.mcp.version="0.2.6" gagent.mcp.license="Apache-2.0"
ENTRYPOINT ["/usr/local/bin/mcp-bridge"]
CMD ["node", "/opt/uniprot/node_modules/@cyanheads/uniprot-mcp-server/dist/index.js"]

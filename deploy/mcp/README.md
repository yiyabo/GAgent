# MCP server images

Each directory builds the image for one catalog server in
`app/services/extensions/catalog.json`. The catalog names the image tag; the Docker
proxy policy (`app/ops/docker_policy.py`) allows exactly those tags and nothing else.

```bash
docker build -f deploy/mcp/fetch/Dockerfile   -t gagent-mcp-fetch:2026.10.10 deploy/mcp
docker build -f deploy/mcp/uniprot/Dockerfile -t gagent-mcp-uniprot:0.2.6    deploy/mcp
```

What is pinned, and why it matters:

| layer | pinned by |
|---|---|
| base image | sha256 digest in the `FROM` line |
| fetch: Python dependencies | `fetch/requirements.lock`, every wheel by sha256 (`pip install --require-hashes --no-deps`) |
| uniprot: npm dependencies | `uniprot/package-lock.json` integrity hashes (`npm ci --ignore-scripts`) |

To bump a server: change the version, regenerate the lock
(`uv pip compile --generate-hashes` / `npm install --package-lock-only --ignore-scripts`),
re-read the diff of the server's own source, rebuild, and update the catalog entry
(tag, commit, verification text). A catalog change is the review step.

## Running one

The app starts the container itself, on the internal `gagent-sandbox` network, with
no capabilities, a read-only root filesystem, no mounts and a non-root user. The
container has no route to the internet; it dials back to the app (`bridge.sh`) and
reaches the outside only through the egress proxy, limited to the hosts the catalog
lists. Turn the feature on with `EXTENSIONS_MCP_ENABLED=1`; an admin then switches
servers on from the Extensions page.

On a development machine (Docker Desktop / colima) the container reaches the app as
`host.docker.internal`:

```bash
export MCP_CONTAINER_HOST_GATEWAY=1 CODE_MODE_RPC_BIND=0.0.0.0 \
       CODE_MODE_RPC_ADVERTISE=host.docker.internal SANDBOX_NETWORK=bridge
```

`SANDBOX_NETWORK=bridge` is for local development only: the bridge network *does*
have internet access, so the egress limits are then enforced by the proxy but not
backed by the network. Production uses the internal `gagent-sandbox` network.

## Building where Docker Hub is unreachable

The `.8` host cannot pull from Docker Hub and its mirror carries `library/python` only,
so the uniprot image (Node base) cannot be built there directly. Build it elsewhere and
move it with `docker save | docker load`, then verify the image digest.

#!/bin/bash
# Entrypoint of every MCP container image.
#
# The Docker API proxy offers no attach/exec, so the host cannot hold pipes into
# this container. Instead we dial OUT to the one-shot listener the app opened,
# prove we know the per-start token, and make the socket our stdin and stdout.
# After that the MCP server speaks plain JSON-RPC lines, exactly as under a pipe.
#
# usage: bridge.sh <server command> [args...]
set -u
endpoint="${GAGENT_MCP_ENDPOINT:-}"
token="${GAGENT_MCP_TOKEN:-}"
if [ -z "$endpoint" ] || [ -z "$token" ] || [ "$#" -eq 0 ]; then
  echo "bridge: GAGENT_MCP_ENDPOINT, GAGENT_MCP_TOKEN and a command are required" >&2
  exit 64
fi
hostport="${endpoint#tcp://}"
host="${hostport%:*}"
port="${hostport##*:}"
exec 3<>"/dev/tcp/${host}/${port}" || { echo "bridge: cannot reach ${host}:${port}" >&2; exit 69; }
printf '{"token":"%s"}\n' "$token" >&3
# The server must not see the token or the endpoint.
unset GAGENT_MCP_TOKEN GAGENT_MCP_ENDPOINT
exec "$@" <&3 >&3 3>&-

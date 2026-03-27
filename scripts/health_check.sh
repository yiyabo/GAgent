#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load only VAR=value lines from .env (avoid executing stray 'fi' or other code when sourced)
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ROOT_DIR/.env" 2>/dev/null || true)
  set +a
fi

BACKEND_HOST=${BACKEND_HOST:-0.0.0.0}
BACKEND_PORT=${BACKEND_PORT:-9000}
VITE_DEV_SERVER_HOST=${VITE_DEV_SERVER_HOST:-0.0.0.0}
VITE_DEV_SERVER_PORT=${VITE_DEV_SERVER_PORT:-3001}
AMEM_HOST=${AMEM_HOST:-0.0.0.0}
AMEM_PORT=${AMEM_PORT:-8001}
AMEM_HEALTH_TIMEOUT=${AMEM_HEALTH_TIMEOUT:-300}
START_AMEM=${START_AMEM:-false}
BACKEND_HEALTH_TIMEOUT=${BACKEND_HEALTH_TIMEOUT:-120}
FRONTEND_HEALTH_TIMEOUT=${FRONTEND_HEALTH_TIMEOUT:-60}
# Initial delay before polling (larger when amem is enabled — model load is slow)
if case "${START_AMEM}" in 1|true|TRUE|yes|YES|on|ON) true ;; *) false ;; esac; then
  HEALTH_CHECK_INITIAL_DELAY=${HEALTH_CHECK_INITIAL_DELAY:-15}
else
  HEALTH_CHECK_INITIAL_DELAY=${HEALTH_CHECK_INITIAL_DELAY:-8}
fi

normalize_host() {
  local host="$1"
  case "$host" in
    ""|0.0.0.0|::)
      #  health check  localhost， IP
      echo "127.0.0.1"
      ;;
    *)
      echo "$host"
      ;;
  esac
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local timeout="${3:-60}"
  local start

  start=$(date +%s)
  echo "Waiting for $name at $url..."

  while true; do
    if curl -4 -fs --max-time 5 "$url" > /dev/null 2>&1; then
      echo "$name healthy."
      return 0
    fi

    if [ $(( $(date +%s) - start )) -ge "$timeout" ]; then
      echo "$name health check timed out after ${timeout}s."
      return 1
    fi

    sleep 1
  done
}

if ! command -v curl > /dev/null 2>&1; then
  echo "curl not found; cannot run health checks."
  exit 1
fi

echo "Giving services ${HEALTH_CHECK_INITIAL_DELAY}s to bind..."
sleep "$HEALTH_CHECK_INITIAL_DELAY"

# Health checks hit localhost only; do not use http_proxy (avoids 502 via proxy)
export no_proxy="127.0.0.1,localhost,::1"
export NO_PROXY="${no_proxy}"

backend_host="$(normalize_host "$BACKEND_HOST")"
frontend_host="$(normalize_host "$VITE_DEV_SERVER_HOST")"
amem_host="$(normalize_host "$AMEM_HOST")"

failures=0

# Backend / frontend first; amem only when START_AMEM is enabled
if ! wait_for_url "backend" "http://${backend_host}:${BACKEND_PORT}/health" "$BACKEND_HEALTH_TIMEOUT"; then
  failures=$((failures + 1))
fi

if ! wait_for_url "frontend" "http://${frontend_host}:${VITE_DEV_SERVER_PORT}/" "$FRONTEND_HEALTH_TIMEOUT"; then
  failures=$((failures + 1))
fi

if case "${START_AMEM}" in 1|true|TRUE|yes|YES|on|ON) true ;; *) false ;; esac; then
  if ! wait_for_url "amem" "http://${amem_host}:${AMEM_PORT}/health" "$AMEM_HEALTH_TIMEOUT"; then
    failures=$((failures + 1))
  fi
else
  echo "Skipping amem health check (set START_AMEM=true to require it)."
fi

if [ "$failures" -ne 0 ]; then
  echo "Health check failed: ${failures} service(s) not ready."
  exit 1
fi

echo "All services healthy."

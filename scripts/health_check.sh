#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT_DIR/.env"
  set +a
fi

BACKEND_HOST=${BACKEND_HOST:-0.0.0.0}
BACKEND_PORT=${BACKEND_PORT:-9000}
VITE_DEV_SERVER_HOST=${VITE_DEV_SERVER_HOST:-0.0.0.0}
VITE_DEV_SERVER_PORT=${VITE_DEV_SERVER_PORT:-3000}

normalize_host() {
  local host="$1"
  case "$host" in
    ""|0.0.0.0|::)
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
    if curl -fs --max-time 2 "$url" > /dev/null 2>&1; then
      echo "$name healthy."
      return 0
    fi

    if [ $(( $(date +%s) - start )) -ge "$timeout" ]; then
      echo "$name health check timed out after ${timeout}s."
      return 1
    fi

    sleep 2
  done
}

if ! command -v curl > /dev/null 2>&1; then
  echo "curl not found; cannot run health checks."
  exit 1
fi

backend_host="$(normalize_host "$BACKEND_HOST")"
frontend_host="$(normalize_host "$VITE_DEV_SERVER_HOST")"

failures=0

if ! wait_for_url "amem" "http://127.0.0.1:8001/health" 120; then
  failures=$((failures + 1))
fi

if ! wait_for_url "backend" "http://${backend_host}:${BACKEND_PORT}/health" 60; then
  failures=$((failures + 1))
fi

if ! wait_for_url "frontend" "http://${frontend_host}:${VITE_DEV_SERVER_PORT}/" 60; then
  failures=$((failures + 1))
fi

if [ "$failures" -ne 0 ]; then
  echo "Health check failed: ${failures} service(s) not ready."
  exit 1
fi

echo "All services healthy."

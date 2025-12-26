#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/log"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT_DIR/.env"
  set +a
fi

AMEM_PORT=${AMEM_PORT:-8001}
BACKEND_PORT=${BACKEND_PORT:-9000}
VITE_DEV_SERVER_PORT=${VITE_DEV_SERVER_PORT:-3000}

kill_pid() {
  local name="$1"
  local pid="$2"

  if ! kill -0 "$pid" 2>/dev/null; then
    return 1
  fi

  echo "Stopping $name (pid $pid)..."
  kill "$pid" 2>/dev/null || true

  for _ in {1..10}; do
    if kill -0 "$pid" 2>/dev/null; then
      sleep 1
    else
      break
    fi
  done

  if kill -0 "$pid" 2>/dev/null; then
    echo "$name: did not stop, sending SIGKILL."
    kill -9 "$pid" 2>/dev/null || true
  fi

  return 0
}

kill_by_port() {
  local name="$1"
  local port="$2"

  if [ -z "$port" ]; then
    return 1
  fi

  if ! command -v lsof >/dev/null 2>&1; then
    echo "$name: lsof not found; cannot stop by port $port."
    return 1
  fi

  local pids
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | tr '\n' ' ')"

  if [ -z "$pids" ]; then
    echo "$name: no process listening on port $port."
    return 1
  fi

  echo "$name: stopping process(es) on port $port: $pids"
  for pid in $pids; do
    kill_pid "$name" "$pid" || true
  done

  return 0
}

stop_service() {
  local name="$1"
  local port="${2:-}"
  local pid_file="$LOG_DIR/${name}.pid"

  if [ ! -f "$pid_file" ]; then
    echo "$name: pid file not found."
    kill_by_port "$name" "$port" || true
    return 0
  fi

  local pid
  pid="$(cat "$pid_file" | tr -d ' ')"

  if [ -z "$pid" ]; then
    echo "$name: empty pid file."
    rm -f "$pid_file"
    kill_by_port "$name" "$port" || true
    return 0
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    echo "$name: not running (pid $pid)."
    rm -f "$pid_file"
    kill_by_port "$name" "$port" || true
    return 0
  fi

  kill_pid "$name" "$pid" || true

  rm -f "$pid_file"
  echo "$name stopped."

  kill_by_port "$name" "$port" || true
}

stop_service "frontend" "$VITE_DEV_SERVER_PORT"
stop_service "backend" "$BACKEND_PORT"
stop_service "amem" "$AMEM_PORT"

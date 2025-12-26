#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/log"

stop_service() {
  local name="$1"
  local pid_file="$LOG_DIR/${name}.pid"

  if [ ! -f "$pid_file" ]; then
    echo "$name: pid file not found."
    return 0
  fi

  local pid
  pid="$(cat "$pid_file" | tr -d ' ')"

  if [ -z "$pid" ]; then
    echo "$name: empty pid file."
    rm -f "$pid_file"
    return 0
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    echo "$name: not running (pid $pid)."
    rm -f "$pid_file"
    return 0
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

  rm -f "$pid_file"
  echo "$name stopped."
}

stop_service "frontend"
stop_service "backend"
stop_service "amem"

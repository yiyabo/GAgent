#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/log"

mkdir -p "$LOG_DIR"

start_bg() {
  local name="$1"
  local cmd="$2"
  local log_file="$LOG_DIR/${name}.log"
  local pid_file="$LOG_DIR/${name}.pid"

  nohup bash -c "$cmd" > "$log_file" 2>&1 &
  echo $! > "$pid_file"
  echo "$name started (pid $(cat "$pid_file"))"
  echo "log: $log_file"
}

start_bg "amem" "bash \"$ROOT_DIR/scripts/start_amem.sh\""
start_bg "backend" "cd \"$ROOT_DIR\" && bash \"$ROOT_DIR/start_backend.sh\""
start_bg "frontend" "cd \"$ROOT_DIR/web-ui\" && npm run dev"

echo "All services started."
echo "Running health checks..."
bash "$ROOT_DIR/scripts/health_check.sh"

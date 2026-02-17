#!/bin/bash
set -euo pipefail

# 设置 Hugging Face 镜像源（解决国内访问问题）
export HF_ENDPOINT=https://hf-mirror.com
echo "🌐 Using Hugging Face mirror: ${HF_ENDPOINT}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/log"

mkdir -p "$LOG_DIR"

# Stop existing services first (restart behavior)
echo "Stopping existing services (if any)..."
if [ -x "$ROOT_DIR/scripts/stop_all.sh" ]; then
    bash "$ROOT_DIR/scripts/stop_all.sh" || true
    echo ""
else
    echo "Warning: stop_all.sh not found, skipping stop"
fi

# Sync skills to ~/.claude/skills/ before starting services
echo "Syncing skills..."
if [ -x "$ROOT_DIR/scripts/sync_skills.sh" ]; then
    bash "$ROOT_DIR/scripts/sync_skills.sh"
else
    echo "Warning: sync_skills.sh not found or not executable"
fi

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

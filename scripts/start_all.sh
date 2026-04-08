#!/bin/bash
set -euo pipefail

# 设置 Hugging Face 镜像源（解决国内访问问题）
export HF_ENDPOINT=https://hf-mirror.com
echo "🌐 Using Hugging Face mirror: ${HF_ENDPOINT}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/log"

# Load .env for START_AMEM etc. (VAR=value lines only)
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ROOT_DIR/.env" 2>/dev/null || true)
  set +a
fi

mkdir -p "$LOG_DIR"

# A-mem loads embedding models and is slow; off by default. Set START_AMEM=true to enable.
START_AMEM=${START_AMEM:-false}
_truthy_start_amem() {
  case "${START_AMEM}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

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

# Ensure code_executor Docker image exists (Docker Desktop may garbage-collect it)
_CODE_EXECUTOR_IMAGE="${CODE_EXECUTOR_DOCKER_IMAGE:-gagent-python-runtime:latest}"
if docker image inspect "$_CODE_EXECUTOR_IMAGE" >/dev/null 2>&1; then
  echo "Docker image $_CODE_EXECUTOR_IMAGE found."
else
  echo "Docker image $_CODE_EXECUTOR_IMAGE missing — rebuilding..."
  if [ -x "$ROOT_DIR/scripts/build_code_executor_image.sh" ]; then
    bash "$ROOT_DIR/scripts/build_code_executor_image.sh"
    echo "Docker image rebuilt."
  else
    echo "Warning: build_code_executor_image.sh not found, code_executor may fail."
  fi
fi

start_bg() {
  local name="$1"
  local cmd="$2"
  local log_file="$LOG_DIR/${name}.log"
  local pid_file="$LOG_DIR/${name}.pid"

  # Run under nohup so SSH disconnects do not terminate the service.
  # Each command should exec its long-lived process so the recorded PID matches
  # the actual listener process and can be stopped cleanly later.
  nohup bash -c "$cmd" > "$log_file" 2>&1 < /dev/null &
  echo $! > "$pid_file.tmp"
  
  # Read PID and clean up
  local pid
  pid=$(cat "$pid_file.tmp")
  rm -f "$pid_file.tmp"
  
  echo "$pid" > "$pid_file"
  echo "$name started (pid $pid)"
  echo "log: $log_file"
  
  # Small delay to let process start
  sleep 1
}

if _truthy_start_amem; then
  start_bg "amem" "exec bash \"$ROOT_DIR/scripts/start_amem.sh\""
else
  echo "Skipping amem (slow / optional). Export START_AMEM=true to start it."
fi
# Login shell helps conda when only initialized in ~/.bash_profile; start_backend.sh also sources conda.sh + conda activate LLM.
start_bg "backend" "bash -lc 'cd \"${ROOT_DIR}\" && export BACKEND_RELOAD=false && exec bash \"${ROOT_DIR}/start_backend.sh\"'"
start_bg "frontend" "cd \"$ROOT_DIR/web-ui\" && exec npm run dev"

echo "All services started."
echo "Running health checks..."
bash "$ROOT_DIR/scripts/health_check.sh"

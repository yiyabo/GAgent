#!/bin/bash
# 后端启动脚本 - 从环境变量读取配置

# 解析项目根目录（无论从哪个 cwd 启动都一致）
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$ROOT_DIR/runtime"
DATA_DIR="$ROOT_DIR/data"

# 加载项目根目录下的 .env 文件
if [ -f "$ROOT_DIR/.env" ]; then
    export $(cat "$ROOT_DIR/.env" | grep -v '^#' | xargs)
fi

# 读取环境变量，提供默认值
BACKEND_HOST=${BACKEND_HOST:-0.0.0.0}
BACKEND_PORT=${BACKEND_PORT:-9000}
# Default to hot-reload for local development; set BACKEND_RELOAD=false to disable.
BACKEND_RELOAD=${BACKEND_RELOAD:-true}

echo "🚀 Starting backend server..."
echo "📍 Host: $BACKEND_HOST"
echo "🔌 Port: $BACKEND_PORT"
echo "🌐 CORS Origins: $CORS_ORIGINS"
echo "♻️  Reload enabled: $BACKEND_RELOAD"
echo "📂 Project root: $ROOT_DIR"

# 启动 FastAPI 应用
RELOAD_ARGS=()
case "$BACKEND_RELOAD" in
  true|TRUE|1|yes|YES|on|ON)
    RELOAD_ARGS=(
      --reload
      --reload-dir "app"
      --reload-dir "tool_box"
      --reload-include "app/**/*.py"
      --reload-include "tool_box/**/*.py"
      --reload-exclude "$RUNTIME_DIR"
      --reload-exclude "$DATA_DIR"
      --reload-exclude "runtime/**"
      --reload-exclude "**/runtime/**"
      --reload-exclude "data/**"
      --reload-exclude "**/data/**"
      --reload-exclude "*.db"
      --reload-exclude "*.sqlite"
    )
    ;;
esac

cd "$ROOT_DIR"

python -m uvicorn app.main:app \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    "${RELOAD_ARGS[@]}"

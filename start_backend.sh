#!/bin/bash
# 后端启动脚本 - 从环境变量读取配置

# 加载 .env 文件
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# 读取环境变量，提供默认值
BACKEND_HOST=${BACKEND_HOST:-0.0.0.0}
BACKEND_PORT=${BACKEND_PORT:-9000}
BACKEND_RELOAD=${BACKEND_RELOAD:-false}

echo "🚀 Starting backend server..."
echo "📍 Host: $BACKEND_HOST"
echo "🔌 Port: $BACKEND_PORT"
echo "🌐 CORS Origins: $CORS_ORIGINS"
echo "♻️  Reload enabled: $BACKEND_RELOAD"

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
      --reload-exclude "runtime/**"
      --reload-exclude "*.db"
      --reload-exclude "*.sqlite"
      --reload-exclude "data/*"
    )
    ;;
esac

python -m uvicorn app.main:app \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    "${RELOAD_ARGS[@]}"

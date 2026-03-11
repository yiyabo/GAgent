#!/bin/bash
#  - 

# （ cwd ）
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$ROOT_DIR/runtime"
DATA_DIR="$ROOT_DIR/data"

#  .env 
if [ -f "$ROOT_DIR/.env" ]; then
    export $(cat "$ROOT_DIR/.env" | grep -v '^#' | xargs)
fi

# ，
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

#  FastAPI 
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

exec python -m uvicorn app.main:app \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    "${RELOAD_ARGS[@]}"

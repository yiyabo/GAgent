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

# Optional: pin interpreter without conda (e.g. BACKEND_PYTHON_BIN=/path/to/python3.11)
if [ -n "${BACKEND_PYTHON_BIN:-}" ] && [ -x "${BACKEND_PYTHON_BIN}" ]; then
  exec "${BACKEND_PYTHON_BIN}" -m uvicorn app.main:app \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    "${RELOAD_ARGS[@]}"
fi

# Non-interactive bash must load conda before `conda activate`.
if [ -f "${HOME}/anaconda3/etc/profile.d/conda.sh" ]; then
  # shellcheck source=/dev/null
  . "${HOME}/anaconda3/etc/profile.d/conda.sh"
elif [ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]; then
  # shellcheck source=/dev/null
  . "${HOME}/miniconda3/etc/profile.d/conda.sh"
elif command -v conda >/dev/null 2>&1; then
  eval "$(conda shell.bash hook)"
fi

if ! conda activate LLM; then
  echo "ERROR: conda activate LLM failed (is env LLM created?)" >&2
  exit 1
fi

_py_ok="$(python -c 'import sys; print(1 if sys.version_info >= (3, 9) else 0)' 2>/dev/null || echo 0)"
if [ "$_py_ok" != "1" ]; then
  echo "ERROR: after conda activate LLM, need Python >= 3.9; got $(command -v python) $(python --version 2>&1)" >&2
  exit 1
fi

exec python -m uvicorn app.main:app \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    "${RELOAD_ARGS[@]}"

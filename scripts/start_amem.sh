#!/bin/bash
# A-mem 

echo "🧠 Starting A-mem (Agentic Memory) Service..."

# Load repo .env if present to provide API keys/base URLs.
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ROOT_DIR/.env"
    set +a
fi

#  Hugging Face （）
export HF_ENDPOINT=https://hf-mirror.com
echo "🌐 Using Hugging Face mirror: ${HF_ENDPOINT}"

#  A-mem 
cd "$(dirname "$0")/../execute_memory/A-mem-main" || exit 1

# 
if [ ! -f "config.cfg" ]; then
    echo "❌ config.cfg not found!"
    echo "Please create config.cfg from config.example.cfg and set your API key"
    exit 1
fi

# 
echo "📦 Checking dependencies..."
if ! python -c "import litellm" 2>/dev/null; then
    echo "Installing required packages..."
    pip install litellm chromadb sentence-transformers rank-bm25 scikit-learn fastapi uvicorn pydantic
fi

if ! python -c "import agentic_memory" 2>/dev/null; then
    echo "Installing A-mem package..."
    pip install -e .
fi

# （ 8001，）
echo "🚀 Starting A-mem API on port 8001..."
exec python api.py --port 8001

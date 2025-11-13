#!/bin/bash
# A-mem服务启动脚本

echo "🧠 Starting A-mem (Agentic Memory) Service..."

# 进入A-mem目录
cd "$(dirname "$0")/../execute_memory/A-mem-main" || exit 1

# 检查配置文件
if [ ! -f "config.cfg" ]; then
    echo "❌ config.cfg not found!"
    echo "Please create config.cfg from config.example.cfg and set your API key"
    exit 1
fi

# 检查并安装依赖
echo "📦 Checking dependencies..."
if ! python -c "import litellm" 2>/dev/null; then
    echo "Installing required packages..."
    pip install litellm chromadb sentence-transformers rank-bm25 scikit-learn fastapi uvicorn pydantic
fi

if ! python -c "import agentic_memory" 2>/dev/null; then
    echo "Installing A-mem package..."
    pip install -e .
fi

# 启动服务（端口8001，避免与主服务冲突）
echo "🚀 Starting A-mem API on port 8001..."
python api.py --port 8001

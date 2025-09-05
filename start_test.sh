#!/bin/bash

# 启动脚本 - 用于测试对话系统

echo "================================================"
echo "         GAgent 对话系统测试启动脚本"
echo "================================================"

# 检查环境变量
if [ -z "$GLM_API_KEY" ]; then
    echo "⚠️  警告: 未设置 GLM_API_KEY，将使用 Mock 模式"
    export LLM_MOCK=1
else
    echo "✅ 已设置 GLM_API_KEY"
fi

# 启动后端
echo ""
echo "1. 启动后端服务器..."
echo "------------------------------------------------"

# 检查端口是否被占用
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null ; then
    echo "⚠️  端口 8000 已被占用，尝试关闭..."
    kill $(lsof -Pi :8000 -sTCP:LISTEN -t)
    sleep 2
fi

# 启动后端服务器
echo "启动 FastAPI 服务器..."
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

# 等待后端启动
echo "等待后端启动..."
sleep 5

# 检查后端是否启动成功
if curl -s http://127.0.0.1:8000/plans > /dev/null; then
    echo "✅ 后端启动成功"
else
    echo "❌ 后端启动失败"
    exit 1
fi

# 启动前端
echo ""
echo "2. 启动前端服务器..."
echo "------------------------------------------------"

cd frontend

# 检查是否安装了依赖
if [ ! -d "node_modules" ]; then
    echo "安装前端依赖..."
    npm install
fi

# 确保 Element Plus 已安装
if ! npm list element-plus >/dev/null 2>&1; then
    echo "安装 Element Plus UI 库..."
    npm install element-plus @element-plus/icons-vue
fi

# 检查端口是否被占用
if lsof -Pi :3000 -sTCP:LISTEN -t >/dev/null ; then
    echo "⚠️  端口 3000 已被占用，尝试关闭..."
    kill $(lsof -Pi :3000 -sTCP:LISTEN -t)
    sleep 2
fi

# 启动前端开发服务器
echo "启动 Vue 开发服务器..."
npm run dev &
FRONTEND_PID=$!

cd ..

# 等待前端启动
echo "等待前端启动..."
sleep 5

echo ""
echo "================================================"
echo "              系统启动完成！"
echo "================================================"
echo ""
echo "📌 访问地址："
echo "   前端界面: http://localhost:3000"
echo "   后端API:  http://127.0.0.1:8000"
echo "   API文档:  http://127.0.0.1:8000/docs"
echo ""
echo "💡 测试步骤："
echo "   1. 打开 http://localhost:3000/#/chat"
echo "   2. 在聊天框输入以下命令测试："
echo "      - '帮助' - 显示可用命令"
echo "      - '创建一个关于人工智能的研究计划' - 创建新计划"
echo "      - '显示所有计划' - 查看计划列表"
echo "      - '执行计划1' - 执行指定计划"
echo ""
echo "📝 注意事项："
echo "   - 按 Ctrl+C 停止所有服务"
echo "   - 如使用 Mock 模式，LLM 响应为模拟数据"
echo ""
echo "================================================"

# 捕获退出信号
trap "echo '正在停止服务...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM

# 等待进程
wait
#!/bin/bash
# 测试A-mem与Claude Code集成

echo "🧪 测试A-mem与Claude Code集成"
echo "================================"
echo ""

# 1. 检查服务状态
echo "📊 步骤1: 检查服务状态"
echo "---"
echo "后端服务:"
curl -s http://localhost:9000/health | python -m json.tool
echo ""
echo "A-mem服务:"
curl -s http://localhost:8001/health | python -m json.tool
echo ""

# 2. 创建一个测试会话并发送消息
echo "📝 步骤2: 发送测试消息（触发Claude Code）"
echo "---"

# 创建会话并发送消息
SESSION_RESPONSE=$(curl -s -X POST http://localhost:9000/chat/message \
  -H "Content-Type: application/json" \
  -d '{
    "message": "请用Claude Code创建一个简单的Python脚本，计算1到10的和并保存到result.txt"
  }')

echo "后端响应:"
echo "$SESSION_RESPONSE" | python -m json.tool
echo ""

# 提取job_id（用于跟踪执行）
JOB_ID=$(echo "$SESSION_RESPONSE" | python -c "import sys, json; data=json.load(sys.stdin); print(data.get('data', {}).get('job_id', ''))" 2>/dev/null)

if [ -z "$JOB_ID" ]; then
    echo "⚠️  无法获取job_id，但任务可能已提交"
    echo "继续测试..."
else
    echo "✅ Job ID: $JOB_ID"
fi
echo ""

# 3. 等待Claude Code执行
echo "⏳ 步骤3: 等待Claude Code执行（30秒）..."
sleep 30
echo ""

# 4. 检查A-mem中的记忆
echo "🔍 步骤4: 查询A-mem中的记忆"
echo "---"
curl -s -X POST http://localhost:8001/query_memory \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Python脚本 计算",
    "top_k": 3
  }' | python -m json.tool
echo ""

# 5. 检查runtime目录（Claude Code生成的文件）
echo "📁 步骤5: 检查runtime目录"
echo "---"
echo "最近创建的任务目录:"
ls -lt runtime/ | head -5
echo ""

echo "✅ 测试完成！"
echo ""
echo "💡 如果看到A-mem中有新的记忆，说明集成成功！"

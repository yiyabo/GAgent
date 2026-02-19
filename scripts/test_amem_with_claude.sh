#!/bin/bash
# A-memClaude Code

echo "🧪 A-memClaude Code"
echo "================================"
echo ""

# 1. 
echo "📊 1: "
echo "---"
echo ":"
curl -s http://localhost:9000/health | python -m json.tool
echo ""
echo "A-mem:"
curl -s http://localhost:8001/health | python -m json.tool
echo ""

# 2. 
echo "📝 2: （Claude Code）"
echo "---"

# 
SESSION_RESPONSE=$(curl -s -X POST http://localhost:9000/chat/message \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Claude CodePython，110result.txt"
  }')

echo ":"
echo "$SESSION_RESPONSE" | python -m json.tool
echo ""

# job_id（）
JOB_ID=$(echo "$SESSION_RESPONSE" | python -c "import sys, json; data=json.load(sys.stdin); print(data.get('data', {}).get('job_id', ''))" 2>/dev/null)

if [ -z "$JOB_ID" ]; then
    echo "⚠️  job_id，"
    echo "..."
else
    echo "✅ Job ID: $JOB_ID"
fi
echo ""

# 3. Claude Code
echo "⏳ 3: Claude Code（30）..."
sleep 30
echo ""

# 4. A-mem
echo "🔍 4: A-mem"
echo "---"
curl -s -X POST http://localhost:8001/query_memory \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Python ",
    "top_k": 3
  }' | python -m json.tool
echo ""

# 5. runtime（Claude Code）
echo "📁 5: runtime"
echo "---"
echo ":"
ls -lt runtime/ | head -5
echo ""

echo "✅ ！"
echo ""
echo "💡 A-mem，！"

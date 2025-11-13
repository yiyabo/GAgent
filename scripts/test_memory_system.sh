#!/bin/bash
# Memory System 测试脚本

echo "======================================"
echo "🧪 Memory System Test Suite"
echo "======================================"
echo ""

BASE_URL="http://localhost:9000"

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 测试计数器
PASSED=0
FAILED=0

# 测试函数
test_endpoint() {
    local name=$1
    local method=$2
    local endpoint=$3
    local data=$4
    
    echo -n "Testing: $name ... "
    
    if [ "$method" = "GET" ]; then
        response=$(curl -s -w "\n%{http_code}" "$BASE_URL$endpoint")
    else
        response=$(curl -s -w "\n%{http_code}" -X "$method" "$BASE_URL$endpoint" \
            -H "Content-Type: application/json" \
            -d "$data")
    fi
    
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        echo -e "${GREEN}✓ PASSED${NC}"
        PASSED=$((PASSED + 1))
        return 0
    else
        echo -e "${RED}✗ FAILED${NC} (HTTP $http_code)"
        echo "Response: $body"
        FAILED=$((FAILED + 1))
        return 1
    fi
}

echo "1️⃣  Testing Basic Memory APIs"
echo "------------------------------"

# 测试统计接口
test_endpoint "Memory Stats" "GET" "/mcp/memory/stats"

# 测试钩子统计
test_endpoint "Hooks Stats" "GET" "/mcp/memory/hooks/stats"

echo ""
echo "2️⃣  Testing Memory Operations"
echo "------------------------------"

# 测试保存记忆
test_endpoint "Save Memory" "POST" "/mcp/save_memory" '{
  "content": "测试记忆：这是一个自动化测试",
  "memory_type": "experience",
  "importance": "medium",
  "tags": ["测试", "自动化"]
}'

# 测试查询记忆
test_endpoint "Query Memory" "POST" "/mcp/query_memory" '{
  "search_text": "测试",
  "limit": 5,
  "min_similarity": 0.3
}'

echo ""
echo "3️⃣  Testing Hooks Operations"
echo "------------------------------"

# 测试启用钩子
test_endpoint "Enable Hooks" "POST" "/mcp/memory/hooks/enable"

# 测试禁用钩子
test_endpoint "Disable Hooks" "POST" "/mcp/memory/hooks/disable"

# 重新启用
test_endpoint "Re-enable Hooks" "POST" "/mcp/memory/hooks/enable"

echo ""
echo "4️⃣  Testing Chat Memory"
echo "------------------------------"

# 测试聊天消息保存
test_endpoint "Save Chat Message" "POST" "/mcp/memory/chat/save" '{
  "content": "如何优化系统性能？这是一个重要的问题。",
  "role": "user",
  "session_id": "test_session_123"
}'

echo ""
echo "======================================"
echo "📊 Test Results"
echo "======================================"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo "Total:  $((PASSED + FAILED))"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}❌ Some tests failed!${NC}"
    exit 1
fi

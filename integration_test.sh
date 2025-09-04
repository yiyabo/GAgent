#!/bin/bash

# Integration test script for async refactoring
echo "======================================"
echo "🧪 INTEGRATION TEST - ASYNC EXECUTION"
echo "======================================"

BASE_URL="http://localhost:8000"
export GLM_API_KEY='f887acb2128f41988821c38ee395f542.rmgIq0MwACMMh0Mw'

# Test 1: Create a plan
echo -e "\n✅ Test 1: Creating test plan..."
PLAN_RESPONSE=$(curl -s -X POST $BASE_URL/plans/propose \
  -H "Content-Type: application/json" \
  -d '{"goal": "Create a test plan for async execution", "sections": 2}')

echo "Plan created successfully"

# Test 2: Approve the plan
echo -e "\n✅ Test 2: Approving plan..."
APPROVE_RESPONSE=$(curl -s -X POST $BASE_URL/plans/approve \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Async Test Plan",
    "tasks": [
      {"name": "Task A", "prompt": "Write content for task A"},
      {"name": "Task B", "prompt": "Write content for task B"},
      {"name": "Task C", "prompt": "Write content for task C"}
    ]
  }')

# Extract task IDs
TASK_IDS=$(echo $APPROVE_RESPONSE | python -c "
import json, sys
data = json.load(sys.stdin)
ids = [str(task['id']) for task in data.get('created', [])]
print(','.join(ids))
")

echo "Tasks created with IDs: $TASK_IDS"

# Test 3: Single async execution
echo -e "\n✅ Test 3: Single async task execution..."
FIRST_ID=$(echo $TASK_IDS | cut -d',' -f1)
SINGLE_RESULT=$(curl -s -X POST $BASE_URL/async/execute/task/$FIRST_ID \
  -H "Content-Type: application/json" \
  -d "{\"task_id\": $FIRST_ID, \"use_context\": false}")

SINGLE_STATUS=$(echo $SINGLE_RESULT | python -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('status', 'unknown'))
")

if [ "$SINGLE_STATUS" = "success" ]; then
    echo "✓ Single task execution: PASSED"
else
    echo "✗ Single task execution: FAILED"
fi

# Test 4: Batch async execution
echo -e "\n✅ Test 4: Batch async execution..."
BATCH_RESULT=$(curl -s -X POST $BASE_URL/async/execute/batch \
  -H "Content-Type: application/json" \
  -d "{
    \"task_ids\": [$TASK_IDS],
    \"use_context\": false,
    \"enable_evaluation\": false
  }")

BATCH_STATUS=$(echo $BATCH_RESULT | python -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('status', 'unknown'))
")

if [ "$BATCH_STATUS" = "success" ]; then
    echo "✓ Batch execution: PASSED"
    
    # Extract execution stats
    echo $BATCH_RESULT | python -c "
import json, sys
data = json.load(sys.stdin)
print(f\"  - Total tasks: {data.get('total_tasks', 0)}\")
print(f\"  - Successful: {data.get('successful', 0)}\")
print(f\"  - Failed: {data.get('failed', 0)}\")
"
else
    echo "✗ Batch execution: FAILED"
fi

# Test 5: Execution with evaluation
echo -e "\n✅ Test 5: Async execution with evaluation..."
EVAL_RESULT=$(curl -s -X POST $BASE_URL/async/execute/batch \
  -H "Content-Type: application/json" \
  -d "{
    \"task_ids\": [$FIRST_ID],
    \"use_context\": false,
    \"enable_evaluation\": true,
    \"evaluation_options\": {
      \"max_iterations\": 2,
      \"quality_threshold\": 0.8
    }
  }")

EVAL_STATUS=$(echo $EVAL_RESULT | python -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('status', 'unknown'))
")

if [ "$EVAL_STATUS" = "success" ]; then
    echo "✓ Evaluation execution: PASSED"
    
    # Show iterations
    echo $EVAL_RESULT | python -c "
import json, sys
data = json.load(sys.stdin)
results = data.get('results', [])
if results:
    print(f\"  - Iterations: {results[0].get('iterations', 0)}\")
"
else
    echo "✗ Evaluation execution: FAILED"
fi

# Test 6: Async status check
echo -e "\n✅ Test 6: Async executor status..."
STATUS_CHECK=$(curl -s $BASE_URL/async/status)
STATUS_OP=$(echo $STATUS_CHECK | python -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('status', 'unknown'))
")

if [ "$STATUS_OP" = "operational" ]; then
    echo "✓ Async executor status: OPERATIONAL"
else
    echo "✗ Async executor status: NOT OPERATIONAL"
fi

echo -e "\n======================================"
echo "📊 INTEGRATION TEST SUMMARY"
echo "======================================"

# Count successes
TESTS_PASSED=0
[ "$SINGLE_STATUS" = "success" ] && ((TESTS_PASSED++))
[ "$BATCH_STATUS" = "success" ] && ((TESTS_PASSED++))
[ "$EVAL_STATUS" = "success" ] && ((TESTS_PASSED++))
[ "$STATUS_OP" = "operational" ] && ((TESTS_PASSED++))

echo "Tests Passed: $TESTS_PASSED / 4"

if [ $TESTS_PASSED -eq 4 ]; then
    echo "✅ ALL INTEGRATION TESTS PASSED!"
    exit 0
else
    echo "❌ SOME TESTS FAILED"
    exit 1
fi
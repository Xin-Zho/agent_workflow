#!/bin/bash
# Quick smoke test — creates a task and checks that Worker processes it.
# Requires: API on :8000, Worker running.

API="http://127.0.0.1:8000"
export WORKFLOW_DB_PATH="${WORKFLOW_DB_PATH:-$HOME/.agent_workflow/data/workflow.db}"

echo "=== Workflow Smoke Test ==="
echo ""

# Get token
TOKEN=$(curl -s -X POST "$API/api/auth/token" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"alice","password":"test-pass"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)

if [ -z "$TOKEN" ]; then
    echo "❌ Cannot get token — is the API running on $API ?"
    exit 1
fi
echo "✅ Authenticated"

# Create task
TASK=$(curl -s -X POST "$API/api/research/tasks" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Smoke test","query":"test query"}')
TASK_ID=$(echo "$TASK" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)
echo "✅ Task created: $TASK_ID"

# Set definition
curl -s -X PUT "$API/api/research/tasks/$TASK_ID/definition" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"definition":{"research_object":"test","application":"test","hard_constraints":[],"optimization_objectives":[],"acceptable_tradeoffs":[]}}' > /dev/null
echo "✅ Definition set"

# Start search
STATUS=$(curl -s -X POST "$API/api/research/tasks/$TASK_ID/start" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null)
echo "✅ Search started — task status: $STATUS"

# Wait a few seconds for Worker to process
echo "⏳ Waiting for Worker..."
sleep 5

# Check progress
TASK_STATUS=$(curl -s "$API/api/research/tasks/$TASK_ID" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['task']['status'])" 2>/dev/null)
echo "✅ Task status: $TASK_STATUS"

echo ""
echo "=== Smoke test complete ==="
echo "Check Worker terminal for Pi activity logs."

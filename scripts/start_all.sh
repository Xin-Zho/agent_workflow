#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

source .venv/bin/activate 2>/dev/null || true

export APP_ENV=test
export AGENT_BACKEND="${AGENT_BACKEND:-pi}"
export PI_COMMAND="${PI_COMMAND:-pi}"
export PI_TIMEOUT_SECONDS="${PI_TIMEOUT_SECONDS:-300}"
export WORKFLOW_DB_PATH="${WORKFLOW_DB_PATH:-$HOME/.agent_workflow/data/workflow.db}"

echo "============================================"
echo "  Agent Workflow — 全栈启动"
echo "============================================"
echo ""

# 1. 检查 Ollama
echo "[1/3] Ollama..."
if curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
    echo "  ✅ Ollama 运行中"
else
    echo "  ⚠️  Ollama 未响应，请先启动: ollama serve"
fi
echo ""

# 2. 启动 Web API
echo "[2/3] Web API (端口 8000)..."
cd python-tools
python web_api_server.py --port 8000 &
API_PID=$!
echo "  ✅ API PID: $API_PID"
sleep 2
echo ""

# 3. 启动 Worker
echo "[3/3] Worker (backend=$AGENT_BACKEND)..."
python worker_main.py &
WORKER_PID=$!
echo "  ✅ Worker PID: $WORKER_PID"
echo ""

echo "============================================"
echo "  全部启动完成"
echo "============================================"
echo ""
echo "  API:     http://127.0.0.1:8000"
echo "  Worker:  PID $WORKER_PID ($AGENT_BACKEND)"
echo "  Ollama:  http://127.0.0.1:11434"
echo ""
echo "  停止: kill $API_PID $WORKER_PID"
echo "============================================"

wait

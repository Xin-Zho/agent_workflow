#!/bin/bash
# setup.sh — agent_workflow 环境初始化
# 用法: bash setup.sh

set -e

echo "=========================================="
echo "  Agent Workflow — 环境初始化"
echo "=========================================="
echo ""

# ── 1. 检查 Python ──
echo "[1/5] 检查 Python..."
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null; then
    PY=python
else
    echo "  ❌ Python 未找到。请安装 Python 3.10+"
    exit 1
fi
echo "  ✅ Python: $($PY --version 2>&1)"

# ── 2. 检查 agent_learning ──
echo ""
echo "[2/5] 检查 agent_learning 项目..."
AGENT_LEARNING="D:/agent_learning"
if [ -d "$AGENT_LEARNING" ]; then
    echo "  ✅ agent_learning: $AGENT_LEARNING"
    if [ -d "$AGENT_LEARNING/data/chroma" ]; then
        echo "  ✅ ChromaDB 数据目录存在"
    else
        echo "  ⚠️  ChromaDB 数据目录不存在 (首次运行会自动创建)"
    fi
    if [ -f "$AGENT_LEARNING/backend/memory/__init__.py" ]; then
        echo "  ✅ MemoryManager 模块可导入"
    else
        echo "  ❌ MemoryManager 模块未找到 — 检查 agent_learning 结构"
        exit 1
    fi
else
    echo "  ❌ agent_learning 未找到: $AGENT_LEARNING"
    echo "     请确保 agent_learning 项目在 D:\\agent_learning"
    exit 1
fi

# ── 3. 检查 Pi Agent (WSL) ──
echo ""
echo "[3/5] 检查 Pi Agent..."
PI_PATH=$(wsl which pi 2>/dev/null || true)
if [ -z "$PI_PATH" ]; then
    PI_PATH="/root/.local/share/pi-node/node-v22.23.1-linux-x64/bin/pi"
fi
PI_EXISTS=$(wsl test -f "$PI_PATH" 2>/dev/null && echo "yes" || echo "no")
if [ "$PI_EXISTS" = "yes" ]; then
    echo "  ✅ Pi Agent: $PI_PATH (WSL)"
else
    echo "  ⚠️  Pi Agent 未找到，请确保已在 WSL 中安装:"
    echo "     npm install -g @earendil-works/pi-coding-agent"
    echo "  (Web API 将不可用，CLI 模式仍可使用扩展)"
fi

# ── 4. 检查 Ollama ──
echo ""
echo "[4/5] 检查 Ollama..."
OLLAMA_OK=$(curl -s http://127.0.0.1:11434/api/tags 2>/dev/null || echo "")
if echo "$OLLAMA_OK" | grep -q "qwen2.5:7b" 2>/dev/null; then
    echo "  ✅ Ollama 运行中, qwen2.5:7b 已安装"
elif [ -n "$OLLAMA_OK" ]; then
    echo "  ⚠️  Ollama 运行中但 qwen2.5:7b 未安装:"
    echo "     ollama pull qwen2.5:7b"
else
    echo "  ❌ Ollama 未运行。请启动 Ollama:"
    echo "     ollama serve"
    echo "  然后拉取模型: ollama pull qwen2.5:7b"
fi

# ── 5. 安装 Python 依赖 ──
echo ""
echo "[5/5] 安装 Python 依赖..."
echo "  注意: 核心依赖 (sympy, pint, mendeleev, chromadb) 在 agent_learning 中已安装"
echo "  仅安装 Web API 额外依赖..."
PIP_OK=$( $PY -c "import fastapi, uvicorn, pydantic" 2>/dev/null && echo "yes" || echo "no" )
if [ "$PIP_OK" = "yes" ]; then
    echo "  ✅ fastapi/uvicorn/pydantic 已安装"
else
    echo "  安装 Web API 依赖..."
    $PY -m pip install fastapi "uvicorn[standard]" pydantic 2>/dev/null || \
        echo "  ⚠️  安装失败，请手动运行: pip install -r python-tools/requirements.txt"
fi

# ── 完成 ──
echo ""
echo "=========================================="
echo "  ✅ 环境检查完成"
echo "=========================================="
echo ""
echo "下一步:"
echo "  CLI 模式:  cd /d/agent_workflow && wsl pi"
echo "  Web API:   cd python-tools && python web_api_server.py"
echo "  验证桥接:  cd python-tools && python verify_bridge.py"
echo ""

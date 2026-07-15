#!/bin/bash
# setup.sh — agent_workflow 环境初始化
# 用法（在 WSL 中运行）:
#   cd /mnt/d/agent_workflow && bash setup.sh

set -e

echo "=========================================="
echo "  Agent Workflow — 环境初始化"
echo "  (WSL 环境)"
echo "=========================================="
echo ""

# ── 0. 确认在 WSL 中运行 ──
if [ -d /mnt/c ]; then
    echo "[0] 运行环境: WSL ✅"
else
    echo "[0] ⚠️  此脚本应在 WSL 中运行"
    echo "    在 Windows 终端执行: wsl bash /mnt/d/agent_workflow/setup.sh"
    exit 1
fi
echo ""

# ── 1. 检查 Python ──
echo "[1/5] 检查 Python..."
if command -v python3 &>/dev/null; then
    PY=python3
else
    echo "  ❌ Python3 未找到。请安装: sudo apt install python3 python3-pip"
    exit 1
fi
echo "  ✅ Python: $($PY --version 2>&1)"
echo ""

# ── 2. 检查 agent_learning ──
echo "[2/5] 检查 agent_learning 项目..."
AGENT_LEARNING="/mnt/d/agent_learning"
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
echo ""

# ── 3. 检查 Pi Agent ──
echo "[3/5] 检查 Pi Agent..."
if command -v pi &>/dev/null; then
    PI_VERSION=$(pi --version 2>/dev/null || echo "unknown")
    echo "  ✅ Pi Agent: $PI_VERSION"
elif [ -f "$HOME/.local/share/pi-node/node-v22.23.1-linux-x64/bin/pi" ]; then
    echo "  ⚠️  Pi Agent 已安装但不在 PATH 中"
    echo "     运行: export PATH=\"$HOME/.local/share/pi-node/node-v22.23.1-linux-x64/bin:\$PATH\""
    echo "     或添加到 ~/.bashrc"
else
    echo "  ❌ Pi Agent 未找到"
    echo "     安装: curl -fsSL https://pi.dev/install | bash"
    exit 1
fi
echo ""

# ── 4. 检查 Ollama ──
echo "[4/5] 检查 Ollama..."
# Ollama 通常在 Windows 侧运行，WSL 通过 localhost 访问
OLLAMA_OK=$(curl -s http://127.0.0.1:11434/api/tags 2>/dev/null || echo "")
if echo "$OLLAMA_OK" | grep -q "qwen2.5:7b" 2>/dev/null; then
    echo "  ✅ Ollama 运行中, qwen2.5:7b 已安装"
elif [ -n "$OLLAMA_OK" ]; then
    echo "  ⚠️  Ollama 运行中但 qwen2.5:7b 未安装:"
    echo "     ollama pull qwen2.5:7b"
else
    echo "  ❌ Ollama 未运行"
    echo "     请在 Windows 侧启动 Ollama，确保端口 11434 可从 WSL 访问"
fi
echo ""

# ── 5. 检查 Python 依赖（使用项目 venv）──
echo "[5/5] 检查 Python venv 和依赖..."
VENV_DIR="/mnt/d/agent_workflow/.venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "  创建 venv..."
    $PY -m venv "$VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"
echo "  ✅ venv: $VENV_PY ($($VENV_PY --version 2>&1))"

MISSING=""
for pkg in sympy pint mendeleev chromadb sentence_transformers fastapi uvicorn pydantic mcp httpx; do
    if ! $VENV_PY -c "import $pkg" 2>/dev/null; then
        MISSING="$MISSING $pkg"
    fi
done

if [ -z "$MISSING" ]; then
    echo "  ✅ 所有 Python 依赖已安装"
else
    echo "  ⚠️  缺少依赖:$MISSING"
    echo "  安装中..."
    $VENV_PY -m pip install sympy pint mendeleev chromadb sentence-transformers \
        fastapi "uvicorn[standard]" pydantic httpx PyPDF2 mcp 2>&1 | tail -5
fi
echo ""

# ── 完成 ──
echo "=========================================="
echo "  ✅ 环境检查完成"
echo "=========================================="
echo ""
echo "下一步:"
echo "  CLI 模式:  cd /mnt/d/agent_workflow && pi"
echo "  Web API:   cd /mnt/d/agent_workflow/python-tools && /mnt/d/agent_workflow/.venv/bin/python web_api_server.py"
echo "  验证桥接:  cd /mnt/d/agent_workflow/python-tools && /mnt/d/agent_workflow/.venv/bin/python verify_bridge.py"
echo ""

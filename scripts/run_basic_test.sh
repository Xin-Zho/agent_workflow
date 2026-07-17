#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

export APP_ENV=test
export PYTHONPATH="$PROJECT_ROOT/python-tools"

echo "============================================"
echo "  Workflow Basic Test Suite"
echo "  Phase 1A + 2A"
echo "============================================"
echo ""

# 1. Unit tests
echo "[1/6] Data models..."
python -m pytest tests/test_workflow_models.py -v

echo "[2/6] Workflow engine (state machine, leases, migrations)..."
python -m pytest tests/test_workflow_engine.py -v

echo "[3/6] Artifact utilities..."
python -m pytest tests/test_artifact_utils.py -v

echo "[4/6] API + auth..."
python -m pytest tests/test_workflow_api.py -v

echo "[5/6] Worker..."
python -m pytest tests/test_worker.py -v

echo "[6/6] E2E suite (12 scenarios)..."
python -m pytest tests/test_e2e_basic.py -v

echo ""
echo "============================================"
echo "  All tests passed"
echo "============================================"

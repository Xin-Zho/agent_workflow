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

python -m pytest tests -v

echo ""
echo "============================================"
echo "  All tests passed"
echo "============================================"

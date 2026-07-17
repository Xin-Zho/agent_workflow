#!/bin/bash
# run_basic_test.sh — complete test suite for Phase 1A + 2A
set -e
cd "$(dirname "$0")/../python-tools"

export APP_ENV=test
export WORKFLOW_DB_PATH=":memory:"

echo "=== Running unit tests ==="
python -m pytest ../tests/test_workflow_models.py ../tests/test_workflow_engine.py -v

echo "=== Running API tests ==="
python -m pytest ../tests/test_workflow_api.py -v

echo "=== Running Worker tests ==="
python -m pytest ../tests/test_worker.py -v

echo "=== Running artifact tests ==="
python -m pytest ../tests/test_artifact_utils.py -v

echo "=== Running E2E tests ==="
python -m pytest ../tests/test_e2e_basic.py -v

echo "=== All tests passed ==="

#!/bin/bash
echo "Stopping all agent-workflow processes..."
pkill -f "web_api_server.py" 2>/dev/null && echo "  ✅ API stopped" || echo "  ⚠️  API not running"
pkill -f "worker_main.py" 2>/dev/null && echo "  ✅ Worker stopped" || echo "  ⚠️  Worker not running"
echo "Done."

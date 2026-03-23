#!/bin/bash
# Auto-restart watchdog for the dashboard server
# Usage: cd ~/getjobs2026 && source .venv/bin/activate && bash run_server.sh

cd "$(dirname "$0")"

while true; do
    echo "===== Starting server at $(date) ====="
    python -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8080 --reload --reload-dir dashboard --reload-dir applicator
    EXIT_CODE=$?
    echo "===== Server exited with code $EXIT_CODE at $(date) ====="
    echo "Restarting in 3 seconds..."
    sleep 3
done

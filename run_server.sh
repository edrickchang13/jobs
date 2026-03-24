#!/bin/bash
# Auto-restart watchdog for the dashboard server
# Usage (local):  cd ~/getjobs2026 && source .venv/bin/activate && bash run_server.sh
# Usage (remote): cd ~/getjobs2026 && source .venv/bin/activate && REMOTE=1 bash run_server.sh

cd "$(dirname "$0")"

# Remote mode: bind on all interfaces so the dashboard is reachable over SSH/LAN
HOST="127.0.0.1"
if [ "${REMOTE:-0}" = "1" ]; then
    HOST="0.0.0.0"
    export HEADLESS=true
    echo ">>> Remote mode: binding on 0.0.0.0, headless browser enabled"
fi

while true; do
    echo "===== Starting server at $(date) ====="
    # Kill ALL processes holding port 8080 (uvicorn --reload spawns parent + child)
    _old_pids=$(lsof -ti:8080 2>/dev/null)
    if [ -n "$_old_pids" ]; then
        echo "Port 8080 in use by PID(s) $(echo $_old_pids | tr '\n' ' ')— killing..."
        echo "$_old_pids" | xargs kill 2>/dev/null || true
        sleep 2
        echo "$_old_pids" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
    # Auto-pull latest code from GitHub before each start
    git pull --rebase origin main 2>&1 | tail -3 || true
    python -m uvicorn dashboard.app:app \
        --host "$HOST" \
        --port 8080 \
        --reload \
        --reload-dir dashboard \
        --reload-dir applicator
    EXIT_CODE=$?
    echo "===== Server exited with code $EXIT_CODE at $(date) ====="
    echo "Restarting in 3 seconds..."
    sleep 3
done

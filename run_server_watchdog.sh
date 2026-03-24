#!/bin/bash
# Watchdog wrapper for the dashboard server
# - Auto-restarts on crash
# - Detects hangs via HTTP health checks (5min timeout)
# - Logs all restarts to watchdog_restarts.log
#
# Usage:
#   bash run_server_watchdog.sh          # start in foreground (Ctrl+C to stop)
#   bash run_server_watchdog.sh &        # start in background
#   kill $(cat watchdog.pid)             # stop background watchdog

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESTART_LOG="$SCRIPT_DIR/watchdog_restarts.log"
SERVER_LOG="$SCRIPT_DIR/server.log"
PID_FILE="$SCRIPT_DIR/watchdog.pid"

RESTART_DELAY=3            # seconds to wait before restart
HEALTH_CHECK_INTERVAL=30   # seconds between health checks
HEALTH_CHECK_TIMEOUT=10    # curl timeout per check
MAX_FAILED_CHECKS=10       # 10 × 30s = 5 minutes before declaring hang
STARTUP_GRACE=60           # seconds to wait before health checks begin

log_event() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg" | tee -a "$RESTART_LOG"
}

cleanup() {
    log_event "Watchdog stopping (signal received, PID: $$)"
    rm -f "$PID_FILE"
    if [ -n "${SERVER_PID:-}" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        log_event "Killing server PID $SERVER_PID"
        kill "$SERVER_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup INT TERM

# Write our PID so background usage can be stopped with: kill $(cat watchdog.pid)
echo $$ > "$PID_FILE"

log_event "=== Watchdog started (PID: $$) ==="
echo "Restart log: $RESTART_LOG"
echo "Server log:  $SERVER_LOG"
echo "Stop with:   kill \$(cat $PID_FILE)  or Ctrl+C"

RESTART_COUNT=0

while true; do
    RESTART_COUNT=$((RESTART_COUNT + 1))
    log_event "Starting server (attempt #$RESTART_COUNT)..."

    # Kill ALL processes holding port 8080 so we don't get [Errno 48]
    # uvicorn --reload spawns a parent reloader + worker child; both may hold the port.
    _old_pids=$(lsof -ti:8080 2>/dev/null)
    if [ -n "$_old_pids" ]; then
        log_event "Port 8080 occupied by PID(s) $(echo $_old_pids | tr '\n' ' ')— killing..."
        echo "$_old_pids" | xargs kill 2>/dev/null || true
        sleep 2
        echo "$_old_pids" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi

    # Start server, append stdout+stderr to server.log
    (
        cd "$SCRIPT_DIR"
        source .venv/bin/activate 2>/dev/null || true
        exec python -m uvicorn dashboard.app:app \
            --host 127.0.0.1 --port 8080
    ) >> "$SERVER_LOG" 2>&1 &
    SERVER_PID=$!

    log_event "Server started (PID: $SERVER_PID)"

    # Wait for startup grace period before health-checking
    grace_remaining=$STARTUP_GRACE
    while [ $grace_remaining -gt 0 ] && kill -0 "$SERVER_PID" 2>/dev/null; do
        sleep 5
        grace_remaining=$((grace_remaining - 5))
    done

    # Monitor: health-check loop
    failed_checks=0
    while kill -0 "$SERVER_PID" 2>/dev/null; do
        sleep "$HEALTH_CHECK_INTERVAL"

        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            break  # process died during sleep
        fi

        if curl -sf --max-time "$HEALTH_CHECK_TIMEOUT" http://127.0.0.1:8080/ > /dev/null 2>&1; then
            failed_checks=0
        else
            failed_checks=$((failed_checks + 1))
            log_event "Health check failed ($failed_checks/$MAX_FAILED_CHECKS)"

            if [ "$failed_checks" -ge "$MAX_FAILED_CHECKS" ]; then
                log_event "HANG DETECTED: $MAX_FAILED_CHECKS consecutive failures. Killing PID $SERVER_PID..."
                kill "$SERVER_PID" 2>/dev/null || true
                sleep 2
                kill -9 "$SERVER_PID" 2>/dev/null || true
                break
            fi
        fi
    done

    wait "$SERVER_PID" 2>/dev/null
    EXIT_CODE=$?
    log_event "Server exited (code: $EXIT_CODE). Restarting in ${RESTART_DELAY}s..."
    sleep "$RESTART_DELAY"
done

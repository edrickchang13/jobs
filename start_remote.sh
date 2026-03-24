#!/bin/bash
# ============================================================
# start_remote.sh  — Launch dashboard on DGX Spark with a
#                    virtual display (Xvfb) so Chromium runs
#                    in headed mode behind the scenes.
#
# Headed-over-Xvfb is preferred over pure headless because:
#   - Workday detects headless mode and may block scraping
#   - Full browser context mimics real user more accurately
#   - Screenshots still stream to the dashboard normally
#
# Usage:
#   ssh user@dgx-spark
#   cd ~/getjobs2026 && source .venv/bin/activate
#   bash start_remote.sh
#
# Then on your Mac, open an SSH tunnel:
#   ssh -L 8080:localhost:8080 user@<dgx-spark-ip>
#   Open http://localhost:8080 in your browser
# ============================================================
set -e
cd "$(dirname "$0")"

source .venv/bin/activate 2>/dev/null || true

# ── Virtual display ──────────────────────────────────────────
# Use display :99 (arbitrary unused number)
DISPLAY_NUM=99
export DISPLAY=:${DISPLAY_NUM}

# Kill any stale Xvfb on this display
pkill -f "Xvfb :${DISPLAY_NUM}" 2>/dev/null || true
sleep 0.5

echo ">>> Starting Xvfb on display :${DISPLAY_NUM}..."
Xvfb :${DISPLAY_NUM} -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 1

# Verify Xvfb started
if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "ERROR: Xvfb failed to start. Falling back to headless mode."
    export HEADLESS=true
    unset DISPLAY
else
    echo ">>> Xvfb running (PID $XVFB_PID) on display :${DISPLAY_NUM}"
    # Don't set HEADLESS — use headed mode via the virtual display
    # Remove any stale HEADLESS=true from env
    unset HEADLESS
fi

# Ensure remote server config
export REMOTE=1

# Cleanup Xvfb on exit
cleanup() {
    echo ">>> Stopping Xvfb..."
    kill $XVFB_PID 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo ">>> Starting dashboard server (remote mode, port 8080)..."
echo ">>> Access via SSH tunnel: ssh -L 8080:localhost:8080 $(whoami)@$(hostname -I | awk '{print $1}')"
echo ""

# Run the server watchdog
bash run_server.sh

#!/bin/bash
# ============================================================
# setup_dgx.sh  — Bootstrap getjobs2026 on DGX Spark (Ubuntu)
# Run once on the DGX Spark after cloning/copying the project.
#
# Usage:
#   scp -r ~/getjobs2026 user@dgx-spark:~/getjobs2026
#   ssh user@dgx-spark "cd ~/getjobs2026 && bash setup_dgx.sh"
# ============================================================
set -eo pipefail 2>/dev/null || true  # non-fatal if shell doesn't support pipefail
cd "$(dirname "$0")"

echo ""
echo "=== getjobs2026 DGX Spark Setup ==="
echo ""

# ── 1. System packages ──────────────────────────────────────
echo "[1/6] Checking system packages (skipping sudo — DGX Spark has these pre-installed)..."
# Try sudo if available, otherwise skip gracefully
if command -v sudo &>/dev/null && sudo -n true 2>/dev/null; then
    sudo apt-get update -qq 2>/dev/null || true
    sudo apt-get install -y xvfb x11-utils python3-venv python3-dev \
        libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
        libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
        2>/dev/null || true
    echo "  System packages installed."
else
    echo "  No sudo access — skipping system package install (DGX Spark has these pre-installed)."
fi

# ── 2. Python virtual environment ───────────────────────────
echo "[2/6] Creating Python virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip -q

# ── 3. Python dependencies ───────────────────────────────────
echo "[3/6] Installing Python packages..."
pip install -q \
    uvicorn[standard] \
    fastapi \
    python-dotenv \
    playwright \
    browser-use \
    langchain-openai \
    openai \
    groq \
    requests \
    beautifulsoup4 \
    aiofiles \
    pyyaml \
    weasyprint \
    aiosmtplib \
    imaplib2

# ── 4. Playwright browsers ───────────────────────────────────
echo "[4/6] Installing Playwright Chromium..."
playwright install chromium
playwright install-deps chromium 2>/dev/null || true

# ── 5. .env check ────────────────────────────────────────────
echo "[5/6] Checking .env..."
if [ ! -f ".env" ]; then
    echo ""
    echo "  ⚠️  No .env file found. Create one with your API keys:"
    echo ""
    echo "  cat > .env << 'EOF'"
    echo "  CEREBRAS_API_KEY=your_key_here"
    echo "  GROQ_API_KEY=your_key_here"
    echo "  GROQ_MODEL=llama-3.3-70b-versatile"
    echo "  GITHUB_REPO_URL=https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/README.md"
    echo "  CHECK_INTERVAL_MINUTES=30"
    echo "  AUTO_SUBMIT=false"
    echo "  HEADLESS=true"
    echo "  EOF"
    echo ""
else
    # Ensure HEADLESS=true is set for remote operation
    if ! grep -q "^HEADLESS=" .env; then
        echo "HEADLESS=true" >> .env
        echo "  Added HEADLESS=true to .env"
    fi
fi

# ── 6. Resume / credentials check ───────────────────────────
echo "[6/6] Checking assets..."
mkdir -p uploads resumes screenshots
if [ ! -f "uploads/EdrickChang_Resume.pdf" ] && [ ! -f "$HOME/Downloads/EdrickChang_Resume.pdf" ]; then
    echo ""
    echo "  ⚠️  Upload your resume to: ~/getjobs2026/uploads/EdrickChang_Resume.pdf"
    echo "     scp ~/Downloads/EdrickChang_Resume.pdf user@dgx-spark:~/getjobs2026/uploads/"
fi
if [ ! -f "credentials.yaml" ]; then
    echo ""
    echo "  ⚠️  Create credentials.yaml with your Workday login:"
    echo "     email: your@email.com"
    echo "     password: yourpassword"
fi

echo ""
echo "=== Setup complete! ==="
echo ""
echo "To start the dashboard in remote mode:"
echo "  cd ~/getjobs2026 && source .venv/bin/activate && REMOTE=1 bash run_server.sh"
echo ""
echo "Then access from your Mac via SSH tunnel:"
echo "  ssh -L 8080:localhost:8080 user@<dgx-spark-ip>"
echo "  Open: http://localhost:8080"
echo ""

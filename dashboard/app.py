import asyncio
import sys
import json
import os
import base64
# Reload trigger - forces uvicorn to restart the stuck worker
import time
from datetime import datetime
from pathlib import Path

# Playwright needs ProactorEventLoop on Windows for subprocess spawning.
# Uvicorn's --reload flag forces SelectorEventLoop which breaks this.
# We ensure the default (ProactorEventLoop) is used.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

from dotenv import load_dotenv
load_dotenv()

# Shared state
pipeline_events: list[dict] = []
pipeline_running = False
pipeline_stop_requested = False
latest_screenshot_b64: str = ""  # base64 encoded PNG of latest browser state
screenshot_version: int = 0  # incremented each time screenshot changes
browser_instance = None  # Keep browser ref for screenshots
active_page = None       # Current Playwright page - stays valid while browser open
active_context = None    # Browser context - needed for new tabs (email verify)
last_cdp_url = None      # CDP URL for reconnecting to browser-use Chromium

# Persist CDP URL to file so it survives server reloads
_CDP_URL_FILE = Path(__file__).parent.parent / ".cdp_url"
def _save_cdp_url(url: str):
    global last_cdp_url
    last_cdp_url = url
    try:
        _CDP_URL_FILE.write_text(url)
    except Exception:
        pass

def _load_cdp_url() -> str:
    global last_cdp_url
    if last_cdp_url:
        return last_cdp_url
    try:
        if _CDP_URL_FILE.exists():
            url = _CDP_URL_FILE.read_text().strip()
            if url:
                last_cdp_url = url
                return url
    except Exception:
        pass
    return ""

async def _reconnect_via_cdp(event_label="Reconnect") -> "Page | None":
    """Try to reconnect to browser-use Chromium via saved CDP URL."""
    global active_page, active_context
    cdp_url = _load_cdp_url()
    if not cdp_url:
        return None
    try:
        from playwright.async_api import async_playwright
        add_event(event_label, "info", f"Reconnecting via CDP: {cdp_url[:50]}...")
        pw = await async_playwright().start()
        browser_pw = await pw.chromium.connect_over_cdp(cdp_url)
        contexts = browser_pw.contexts
        page = None
        if contexts:
            pages = contexts[0].pages
            for p in reversed(pages):
                if p.url and "about:blank" not in p.url:
                    page = p
                    break
            if not page and pages:
                page = pages[-1]
        if page:
            active_page = page
            try:
                active_context = page.context
            except Exception:
                pass
            add_event(event_label, "success", f"Reconnected: {page.url[:60]}")
            print(f">>> CDP reconnect success: {page.url[:80]}")
        return page
    except Exception as e:
        print(f">>> CDP reconnect failed: {e}")
        add_event(event_label, "warning", f"CDP reconnect failed: {str(e)[:80]}")
        return None

# File uploads directory
UPLOADS_DIR = Path(__file__).parent.parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
uploaded_resume: str = ""  # path to uploaded resume
uploaded_transcript: str = ""  # path to uploaded transcript

# Check for existing files on startup
_default_resume = UPLOADS_DIR / "EdrickChang_Resume.pdf"
_default_transcript = UPLOADS_DIR / "Transcript.pdf"
if _default_resume.exists():
    uploaded_resume = str(_default_resume)
if _default_transcript.exists():
    uploaded_transcript = str(_default_transcript)


def add_event(step: str, status: str, detail: str = ""):
    event = {
        "id": len(pipeline_events),
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "step": step,
        "status": status,
        "detail": detail,
    }
    pipeline_events.append(event)
    return event


app = FastAPI(title="Auto-Apply Dashboard")


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Auto-Apply Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0a;
            color: #e0e0e0;
            min-height: 100vh;
        }
        .header {
            background: #111;
            border-bottom: 1px solid #222;
            padding: 12px 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .header h1 { font-size: 18px; color: #fff; }
        .header-right { display: flex; align-items: center; gap: 12px; }
        .status-badge {
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
        }
        .status-idle { background: #222; color: #888; }
        .status-running { background: #1a3a1a; color: #4ade80; animation: pulse 2s infinite; }
        @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.7; } }

        /* Tab navigation */
        .tab-nav {
            display: flex;
            gap: 0;
            background: #111;
            border-bottom: 1px solid #222;
        }
        .tab-btn {
            padding: 10px 24px;
            background: transparent;
            border: none;
            color: #666;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            transition: all 0.2s;
        }
        .tab-btn:hover { color: #aaa; }
        .tab-btn.active { color: #60a5fa; border-bottom-color: #60a5fa; }
        .tab-content { display: none; height: calc(100vh - 90px); }
        .tab-content.active { display: flex; }

        /* ===== JOBS TAB ===== */
        .jobs-container { display: flex; width: 100%; height: 100%; }
        .jobs-sidebar {
            width: 280px;
            border-right: 1px solid #222;
            background: #0d0d0d;
            display: flex;
            flex-direction: column;
            padding: 12px;
            gap: 10px;
            flex-shrink: 0;
        }
        .jobs-sidebar h3 { font-size: 13px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }
        .filter-group { display: flex; flex-direction: column; gap: 4px; }
        .filter-group label { font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: 0.5px; }
        .filter-group input, .filter-group select {
            background: #1a1a1a; border: 1px solid #333; color: #e0e0e0;
            padding: 7px 10px; border-radius: 5px; font-size: 13px; width: 100%;
        }
        .filter-toggle {
            display: flex; align-items: center; gap: 8px; cursor: pointer;
            padding: 6px 0; font-size: 13px; color: #ccc;
        }
        .filter-toggle input[type="checkbox"] {
            width: 16px; height: 16px; accent-color: #2563eb;
        }
        .filter-btn {
            background: #2563eb; color: #fff; border: none;
            padding: 8px 14px; border-radius: 5px; font-size: 13px;
            cursor: pointer; font-weight: 500;
        }
        .filter-btn:hover { background: #1d4ed8; }
        .filter-btn.secondary { background: #333; }
        .filter-btn.secondary:hover { background: #444; }
        .jobs-count {
            font-size: 12px; color: #666; padding: 4px 0;
        }

        .jobs-list-panel {
            flex: 1;
            overflow-y: auto;
            padding: 0;
        }
        .jobs-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        .jobs-table thead {
            position: sticky; top: 0; z-index: 1;
            background: #111;
        }
        .jobs-table th {
            padding: 10px 12px;
            text-align: left;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #555;
            border-bottom: 1px solid #222;
            font-weight: 600;
        }
        .jobs-table td {
            padding: 8px 12px;
            border-bottom: 1px solid #1a1a1a;
            vertical-align: middle;
        }
        .jobs-table tr:hover { background: #141414; }
        .jobs-table .company-name { color: #e0e0e0; font-weight: 500; }
        .jobs-table .role-name { color: #bbb; }
        .jobs-table .location { color: #888; font-size: 12px; }
        .jobs-table .age { color: #666; font-size: 12px; text-align: center; }
        .apply-btn {
            background: #16a34a; color: #fff; border: none;
            padding: 4px 12px; border-radius: 4px; font-size: 11px;
            cursor: pointer; font-weight: 500;
        }
        .apply-btn:hover { background: #15803d; }
        .bay-area-badge {
            display: inline-block; padding: 1px 6px; border-radius: 3px;
            font-size: 10px; background: #1a2a3a; color: #60a5fa;
            margin-left: 4px;
        }
        .applied-badge {
            display: inline-block; padding: 1px 6px; border-radius: 3px;
            font-size: 10px; background: #1a3a1a; color: #4ade80;
            font-weight: 600;
        }
        .applied-row { opacity: 1; }
        .ats-badge {
            display: inline-block; padding: 1px 6px; border-radius: 3px;
            font-size: 10px; margin-left: 4px; font-weight: 500;
        }
        .ats-workday { background: #2d1a4e; color: #a78bfa; }
        .ats-lever { background: #1a3a1a; color: #4ade80; }
        .ats-greenhouse { background: #1a3a3a; color: #5eead4; }
        .ats-icims { background: #3a2a1a; color: #fb923c; }
        .ats-ashby { background: #1a2a3a; color: #60a5fa; }
        .ats-unknown { background: #222; color: #888; }
        .applied-btns { display: flex; gap: 4px; margin-top: 4px; }
        .mark-applied-btn {
            background: #2563eb; color: #fff; border: none;
            padding: 4px 12px; border-radius: 4px; font-size: 11px;
            cursor: pointer; font-weight: 500; flex: 1;
        }
        .mark-applied-btn:hover { background: #1d4ed8; }
        .mark-not-applied-btn {
            background: #dc2626; color: #fff; border: none;
            padding: 4px 12px; border-radius: 4px; font-size: 11px;
            cursor: pointer; font-weight: 500; flex: 1;
        }
        .mark-not-applied-btn:hover { background: #b91c1c; }
        .loading-msg { padding: 40px; text-align: center; color: #555; }

        /* ===== APPLY TAB ===== */
        .apply-container { display: flex; width: 100%; height: 100%; }
        .left-panel {
            width: 380px;
            border-right: 1px solid #222;
            display: flex;
            flex-direction: column;
            background: #0d0d0d;
        }
        .controls {
            padding: 12px;
            border-bottom: 1px solid #222;
        }
        .controls input {
            background: #1a1a1a; border: 1px solid #333; color: #e0e0e0;
            padding: 7px 10px; border-radius: 5px; font-size: 13px;
            width: 100%; margin-bottom: 6px;
        }
        .controls label {
            font-size: 11px; color: #555; text-transform: uppercase;
            letter-spacing: 0.5px; display: block; margin-bottom: 2px;
        }
        .controls button {
            background: #2563eb; color: #fff; border: none;
            padding: 9px 14px; border-radius: 5px; font-size: 13px;
            cursor: pointer; width: 100%; font-weight: 500; margin-top: 4px;
        }
        .controls button:hover { background: #1d4ed8; }
        .controls button:disabled { background: #333; cursor: not-allowed; }
        .event-log-container { flex: 1; overflow-y: auto; padding: 8px; }
        .event-log { list-style: none; }
        .event-item {
            padding: 6px 8px; border-radius: 4px; margin-bottom: 2px;
            font-size: 12px; display: flex; gap: 6px; align-items: flex-start;
        }
        .event-time { color: #444; font-family: monospace; flex-shrink: 0; font-size: 11px; }
        .event-dot { width: 6px; height: 6px; border-radius: 50%; margin-top: 4px; flex-shrink: 0; }
        .event-start .event-dot { background: #3b82f6; }
        .event-success .event-dot { background: #4ade80; }
        .event-error .event-dot { background: #ef4444; }
        .event-info .event-dot { background: #666; }
        .event-content { flex: 1; min-width: 0; }
        .event-step { font-weight: 500; color: #ddd; font-size: 12px; }
        .event-detail { color: #666; font-size: 11px; word-break: break-word; }

        .right-panel { flex: 1; display: flex; flex-direction: column; background: #080808; }
        .browser-header {
            padding: 8px 16px; background: #111; border-bottom: 1px solid #222;
            display: flex; align-items: center; justify-content: space-between;
        }
        .browser-header span { font-size: 13px; color: #888; }
        .browser-dots { display: flex; gap: 6px; }
        .browser-dots span { width: 10px; height: 10px; border-radius: 50%; }
        .browser-dots .red { background: #ff5f57; }
        .browser-dots .yellow { background: #febc2e; }
        .browser-dots .green { background: #28c840; }
        .browser-view {
            flex: 1; display: flex; align-items: center; justify-content: center;
            overflow: auto; padding: 8px;
        }
        .browser-view img {
            max-width: 100%; max-height: 100%; border-radius: 4px; object-fit: contain;
            image-rendering: auto;
        }
        .browser-placeholder { color: #333; font-size: 14px; text-align: center; }
        .step-pills {
            display: flex; gap: 4px; padding: 8px 12px;
            background: #0d0d0d; border-bottom: 1px solid #222; flex-wrap: wrap;
        }
        .step-pill {
            padding: 3px 10px; border-radius: 12px; font-size: 11px;
            background: #1a1a1a; color: #555; border: 1px solid #222;
        }
        .step-pill.active { background: #1a3a1a; color: #4ade80; border-color: #2a4a2a; }
        .step-pill.done { background: #1a2a3a; color: #60a5fa; border-color: #2a3a4a; }
        .step-pill.failed { background: #3a1a1a; color: #f87171; border-color: #4a2a2a; }

        /* Upload section */
        .upload-section {
            padding: 12px;
            border-bottom: 1px solid #222;
        }
        .upload-section h4 {
            font-size: 11px; color: #555; text-transform: uppercase;
            letter-spacing: 0.5px; margin-bottom: 8px;
        }
        .upload-row {
            display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
        }
        .upload-row label.upload-label {
            font-size: 12px; color: #999; width: 70px; flex-shrink: 0;
            text-transform: none; letter-spacing: 0;
        }
        .upload-status {
            font-size: 11px; flex: 1; min-width: 0;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .upload-status.uploaded { color: #4ade80; }
        .upload-status.missing { color: #666; }
        .upload-input { display: none; }
        .upload-btn {
            background: #333; color: #ccc; border: none;
            padding: 4px 10px; border-radius: 4px; font-size: 11px;
            cursor: pointer; flex-shrink: 0;
        }
        .upload-btn:hover { background: #444; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Auto-Apply Dashboard</h1>
        <div class="header-right">
            <span class="status-badge status-idle" id="globalStatus">Idle</span>
        </div>
    </div>

    <!-- Tab Navigation -->
    <div class="tab-nav">
        <button class="tab-btn active" onclick="switchTab('jobs')">Jobs List</button>
        <button class="tab-btn" onclick="switchTab('apply')">Apply</button>
        <button class="tab-btn" onclick="switchTab('autoqueue')">⚡ Auto-Queue</button>
    </div>

    <!-- ===== JOBS TAB ===== -->
    <div class="tab-content active" id="tab-jobs">
        <div class="jobs-container">
            <div class="jobs-sidebar">
                <h3>Filters</h3>

                <div class="filter-group">
                    <label>Search</label>
                    <input type="text" id="filterSearch" placeholder="Company or role..." value="" oninput="applyFilters()">
                </div>

                <div class="filter-group">
                    <label>Location</label>
                    <select id="filterLocation" onchange="applyFilters()">
                        <option value="all">All Locations</option>
                        <option value="bayarea" selected>Bay Area Only</option>
                        <option value="california">All California</option>
                        <option value="remote">Remote</option>
                    </select>
                </div>

                <div class="filter-group">
                    <label>Portal Type</label>
                    <select id="filterATS" onchange="applyFilters()">
                        <option value="all" selected>All Portals</option>
                        <option value="workday">Workday</option>
                        <option value="lever">Lever</option>
                        <option value="greenhouse">Greenhouse</option>
                        <option value="icims">iCIMS</option>
                        <option value="ashby">Ashby</option>
                        <option value="smartrecruiters">SmartRecruiters</option>
                        <option value="bamboohr">BambooHR</option>
                        <option value="jobvite">Jobvite</option>
                        <option value="successfactors">SuccessFactors</option>
                        <option value="taleo">Taleo</option>
                        <option value="workable">Workable</option>
                        <option value="unknown">Other/Unknown</option>
                    </select>
                </div>

                <div class="filter-group">
                    <label>Posted Within</label>
                    <select id="filterAge" onchange="applyFilters()">
                        <option value="all" selected>Any Time</option>
                        <option value="1">Last 1 day</option>
                        <option value="3">Last 3 days</option>
                        <option value="7">Last 7 days</option>
                        <option value="14">Last 14 days</option>
                        <option value="30">Last 30 days</option>
                    </select>
                </div>

                <div style="border-top: 1px solid #222; padding-top: 8px;">
                    <button class="filter-btn" onclick="loadJobs(true)" style="width:100%; margin-bottom: 6px;">Refresh Jobs</button>
                    <button class="filter-btn secondary" onclick="resetFilters()" style="width:100%;">Reset Filters</button>
                </div>

                <div class="jobs-count" id="jobsCount">Loading...</div>
            </div>

            <div class="jobs-list-panel">
                <table class="jobs-table">
                    <thead>
                        <tr>
                            <th>Company</th>
                            <th>Role</th>
                            <th>Location</th>
                            <th>Portal</th>
                            <th style="text-align:center">Age</th>
                            <th style="text-align:center">Action</th>
                        </tr>
                    </thead>
                    <tbody id="jobsBody">
                        <tr><td colspan="6" class="loading-msg">Loading jobs...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- ===== AUTO-QUEUE TAB ===== -->
    <div class="tab-content" id="tab-autoqueue" style="padding:24px;flex-direction:column;gap:16px;overflow-y:auto;">
        <h3 style="margin:0;color:#f1f5f9;">⚡ Auto-Queue — Batch Apply</h3>
        <p style="color:#94a3b8;margin:0;font-size:13px;">Scrapes GitHub for new unprocessed jobs, filters by ATS, and applies sequentially with a pause between each. Browser stays open after each job for your review.</p>

        <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;">
            <div style="display:flex;flex-direction:column;gap:4px;">
                <label style="font-size:12px;color:#94a3b8;">Max jobs</label>
                <input id="aq-limit" type="number" value="10" min="1" max="100"
                    style="width:80px;background:#1e293b;border:1px solid #334155;color:#f1f5f9;padding:6px 10px;border-radius:6px;font-size:14px;">
            </div>
            <div style="display:flex;flex-direction:column;gap:4px;">
                <label style="font-size:12px;color:#94a3b8;">Pause between jobs (sec)</label>
                <input id="aq-delay" type="number" value="30" min="5" max="300"
                    style="width:90px;background:#1e293b;border:1px solid #334155;color:#f1f5f9;padding:6px 10px;border-radius:6px;font-size:14px;">
            </div>
            <div style="display:flex;flex-direction:column;gap:4px;">
                <label style="font-size:12px;color:#94a3b8;">ATS types</label>
                <div style="display:flex;gap:8px;align-items:center;">
                    <label style="font-size:13px;color:#cbd5e1;display:flex;align-items:center;gap:4px;">
                        <input type="checkbox" id="aq-greenhouse" checked> Greenhouse</label>
                    <label style="font-size:13px;color:#cbd5e1;display:flex;align-items:center;gap:4px;">
                        <input type="checkbox" id="aq-lever" checked> Lever</label>
                    <label style="font-size:13px;color:#cbd5e1;display:flex;align-items:center;gap:4px;">
                        <input type="checkbox" id="aq-workday" checked> Workday</label>
                </div>
            </div>
            <button onclick="startAutoQueue()" id="aqStartBtn"
                style="padding:8px 20px;background:#2563eb;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer;font-weight:600;">
                ▶ Start Batch
            </button>
            <button onclick="stopAutoQueue()" id="aqStopBtn"
                style="padding:8px 16px;background:#dc2626;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer;display:none;">
                ■ Stop
            </button>
            <button onclick="loadQueueStatus()"
                style="padding:8px 14px;background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:6px;font-size:13px;cursor:pointer;">
                ↻ Refresh
            </button>
        </div>

        <div id="aq-status" style="background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:14px;font-size:13px;color:#94a3b8;">
            Idle — click "Start Batch" to begin.
        </div>

        <div id="aq-queue-list" style="display:none;">
            <h4 style="color:#cbd5e1;margin:0 0 8px 0;font-size:13px;">Queue</h4>
            <div id="aq-queue-items" style="display:flex;flex-direction:column;gap:4px;max-height:300px;overflow-y:auto;"></div>
        </div>
    </div>

    <!-- ===== APPLY TAB ===== -->
    <div class="tab-content" id="tab-apply">
        <div class="apply-container">
            <div class="left-panel">
                <div class="upload-section">
                    <h4>Documents</h4>
                    <div class="upload-row">
                        <label class="upload-label">Resume</label>
                        <span class="upload-status missing" id="resumeStatus">Loading...</span>
                        <input type="file" class="upload-input" id="resumeFile" accept=".pdf" onchange="uploadFile('resume')">
                        <button class="upload-btn" onclick="document.getElementById('resumeFile').click()">Upload</button>
                    </div>
                    <div class="upload-row">
                        <label class="upload-label">Transcript</label>
                        <span class="upload-status missing" id="transcriptStatus">Not uploaded</span>
                        <input type="file" class="upload-input" id="transcriptFile" accept=".pdf" onchange="uploadFile('transcript')">
                        <button class="upload-btn" onclick="document.getElementById('transcriptFile').click()">Upload</button>
                    </div>
                </div>
                <div class="controls">
                    <label>Application URL</label>
                    <input type="text" id="jobUrl" value="">
                    <label>Company</label>
                    <input type="text" id="company" value="">
                    <label>Role</label>
                    <input type="text" id="role" value="">
                    <div style="display:flex;gap:6px;">
                        <button id="startBtn" onclick="startApplication()" style="flex:1">Start Application</button>
                        <button id="stopBtn" onclick="stopApplication()" style="flex:0 0 70px;background:#dc2626;display:none">Stop</button>
                    </div>
                    <div style="display:flex;gap:6px;margin-top:4px;">
                        <button id="continueBtn" onclick="continueApplication()" style="flex:1;background:#7c3aed;display:none;padding:8px;border:none;color:#fff;border-radius:6px;cursor:pointer;font-size:13px;">Continue</button>
                        <button id="emailVerifyBtn" onclick="triggerEmailVerify()" style="flex:1;background:#d97706;display:none;padding:8px;border:none;color:#fff;border-radius:6px;cursor:pointer;font-size:13px;">Get Email Code</button>
                    </div>
                    <div class="applied-btns">
                        <button class="mark-applied-btn" onclick="markAsApplied()">Applied</button>
                        <button class="mark-not-applied-btn" onclick="markNotApplied()">Not Applied</button>
                    </div>
                </div>
                <div class="step-pills" id="stepPills">
                    <span class="step-pill" id="pill-jd">1. Extract JD</span>
                    <span class="step-pill" id="pill-answers">2. Gen Answers</span>
                    <span class="step-pill" id="pill-nav">3. Navigate</span>
                    <span class="step-pill" id="pill-fill">4. Fill Form</span>
                    <span class="step-pill" id="pill-review">5. Review</span>
                </div>
                <div class="event-log-container">
                    <ul class="event-log" id="eventLog">
                        <li class="event-item event-info">
                            <span class="event-dot"></span>
                            <span class="event-time">--:--</span>
                            <div class="event-content">
                                <div class="event-step">Ready</div>
                                <div class="event-detail">Select a job from the Jobs tab or enter a URL</div>
                            </div>
                        </li>
                    </ul>
                </div>
            </div>
            <div class="right-panel">
                <div class="browser-header">
                    <div class="browser-dots">
                        <span class="red"></span>
                        <span class="yellow"></span>
                        <span class="green"></span>
                    </div>
                    <span id="browserUrl">No browser active</span>
                </div>
                <div class="browser-view" id="browserView">
                    <div class="browser-placeholder">
                        Browser view will appear here when the agent starts
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let allJobs = [];
        let appliedUrls = new Set();
        let eventSource = null;
        let seenEventIds = new Set();  // dedup SSE events on reconnect

        const BAY_AREA_CITIES = [
            'san francisco', 'sf', 'san jose', 'palo alto', 'mountain view',
            'sunnyvale', 'cupertino', 'santa clara', 'redwood city', 'menlo park',
            'san mateo', 'oakland', 'berkeley', 'fremont', 'milpitas', 'foster city',
            'south san francisco', 'san bruno', 'burlingame', 'daly city',
            'pleasanton', 'livermore', 'hayward', 'union city', 'newark',
            'san ramon', 'walnut creek', 'concord', 'dublin', 'campbell',
            'los gatos', 'saratoga', 'morgan hill', 'gilroy', 'alameda',
            'san leandro', 'richmond', 'emeryville', 'half moon bay',
            'woodside', 'portola valley', 'atherton', 'belmont', 'san carlos',
            'redwood shores', 'east palo alto', 'los altos', 'stanford',
            'bay area', 'silicon valley'
        ];

        const CA_KEYWORDS = ['california', ', ca', 'ca,'];

        // ===== TAB SWITCHING =====
        function switchTab(tab) {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector(`.tab-btn[onclick="switchTab('${tab}')"]`).classList.add('active');
            document.getElementById('tab-' + tab).classList.add('active');
        }

        // ===== JOBS LIST =====
        function loadJobs(forceRefresh) {
            const isRefresh = !!forceRefresh;
            document.getElementById('jobsBody').innerHTML = '<tr><td colspan="6" class="loading-msg">' + (isRefresh ? 'Refreshing from GitHub...' : 'Loading jobs...') + '</td></tr>';
            document.getElementById('jobsCount').textContent = 'Loading...';
            fetch('/api/applied').then(r => r.json()).then(data => {
                appliedUrls = new Set(data.urls || []);
            }).catch(() => {}).finally(() => {
                const url = isRefresh ? '/api/jobs?refresh=true' : '/api/jobs';
                fetch(url).then(r => r.json()).then(data => {
                    allJobs = data.jobs || [];
                    const cacheNote = data.cached ? ' (cached)' : '';
                    if (data.warning) console.warn('Jobs warning:', data.warning);
                    applyFilters();
                }).catch(err => {
                    document.getElementById('jobsBody').innerHTML = '<tr><td colspan="6" class="loading-msg">Failed to load jobs: ' + err + '</td></tr>';
                });
            });
        }

        function isBayArea(location) {
            const loc = location.toLowerCase();
            return BAY_AREA_CITIES.some(city => loc.includes(city));
        }

        function isCalifornia(location) {
            const loc = location.toLowerCase();
            return CA_KEYWORDS.some(kw => loc.includes(kw)) || isBayArea(loc);
        }

        function isUSRemote(location) {
            const loc = location.toLowerCase();
            // Must be remote but NOT in a foreign country
            const foreignCountries = ['canada', 'uk', 'india', 'germany', 'france', 'japan', 'australia', 'brazil', 'mexico', 'ireland', 'singapore', 'israel', 'netherlands', 'sweden', 'spain', 'italy', 'poland', 'china', 'korea', 'taiwan'];
            const hasForeign = foreignCountries.some(c => loc.includes(c));
            if (hasForeign && !loc.includes('usa') && !loc.includes('united states')) return false;
            return loc.includes('remote') || loc === 'united states';
        }

        function applyFilters() {
            const search = document.getElementById('filterSearch').value.toLowerCase();
            const locFilter = document.getElementById('filterLocation').value;
            const ageFilter = parseInt(document.getElementById('filterAge').value) || 0;
            const atsFilter = document.getElementById('filterATS').value;

            let filtered = allJobs.filter(job => {
                // Search filter
                if (search && !job.company.toLowerCase().includes(search) && !job.role.toLowerCase().includes(search)) {
                    return false;
                }
                // Location filter
                if (locFilter === 'bayarea' && !isBayArea(job.location)) return false;
                if (locFilter === 'california' && !isCalifornia(job.location) && !isUSRemote(job.location)) return false;
                if (locFilter === 'remote' && !isUSRemote(job.location)) return false;
                // ATS filter
                if (atsFilter !== 'all' && job.ats !== atsFilter) return false;
                // Age filter
                if (ageFilter > 0) {
                    const days = parseInt(job.date) || 999;
                    if (days > ageFilter) return false;
                }
                return true;
            });

            renderJobs(filtered);
            // ATS breakdown
            const atsCounts = {};
            filtered.forEach(job => {
                const ats = job.ats || 'unknown';
                atsCounts[ats] = (atsCounts[ats] || 0) + 1;
            });
            const breakdown = Object.entries(atsCounts)
                .sort((a, b) => b[1] - a[1])
                .map(([k, v]) => k.charAt(0).toUpperCase() + k.slice(1) + ': ' + v)
                .join(', ');
            document.getElementById('jobsCount').innerHTML = filtered.length + ' of ' + allJobs.length + ' jobs shown' +
                (breakdown ? '<br><span style="font-size:10px;color:#666">' + breakdown + '</span>' : '');
        }

        function renderJobs(jobs) {
            const tbody = document.getElementById('jobsBody');
            if (jobs.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="loading-msg">No jobs match your filters</td></tr>';
                return;
            }
            tbody.innerHTML = jobs.map((job, i) => {
                const locBadge = isBayArea(job.location) ? '<span class="bay-area-badge">Bay Area</span>' : '';
                const isApplied = appliedUrls.has(job.url);
                const appliedBadge = isApplied ? ' <span class="applied-badge">Applied</span>' : '';
                const rowClass = isApplied ? 'applied-row' : '';
                const escapedUrl = job.url.replace(/'/g, "\\\\'");
                const escapedCompany = job.company.replace(/'/g, "\\\\'");
                const escapedRole = job.role.replace(/'/g, "\\\\'");
                const actionBtn = '<button class="apply-btn" onclick="selectJob(\\'' + escapedUrl + '\\', \\'' + escapedCompany + '\\', \\'' + escapedRole + '\\')">Apply</button>';
                const ats = job.ats || 'unknown';
                const atsClass = {'workday':'ats-workday','lever':'ats-lever','greenhouse':'ats-greenhouse','icims':'ats-icims','ashby':'ats-ashby'}[ats] || 'ats-unknown';
                const atsBadge = '<span class="ats-badge ' + atsClass + '">' + ats + '</span>';
                return '<tr class="' + rowClass + '">' +
                    '<td class="company-name">' + job.company + appliedBadge + '</td>' +
                    '<td class="role-name">' + job.role + '</td>' +
                    '<td class="location">' + job.location + locBadge + '</td>' +
                    '<td>' + atsBadge + '</td>' +
                    '<td class="age">' + job.date + '</td>' +
                    '<td style="text-align:center">' + actionBtn + '</td>' +
                    '</tr>';
            }).join('');
        }

        function selectJob(url, company, role) {
            document.getElementById('jobUrl').value = url;
            document.getElementById('company').value = company;
            document.getElementById('role').value = role;
            switchTab('apply');
        }

        function resetFilters() {
            document.getElementById('filterSearch').value = '';
            document.getElementById('filterLocation').value = 'all';
            document.getElementById('filterATS').value = 'all';
            document.getElementById('filterAge').value = 'all';
            applyFilters();
        }

        // ===== APPLY TAB =====
        function startApplication() {
            const url = document.getElementById('jobUrl').value;
            const company = document.getElementById('company').value;
            const role = document.getElementById('role').value;
            if (!url) return;

            document.getElementById('startBtn').disabled = true;
            document.getElementById('startBtn').textContent = 'Running...';
            document.getElementById('stopBtn').style.display = 'block';
            document.getElementById('continueBtn').style.display = 'none';
            document.getElementById('emailVerifyBtn').style.display = 'none';
            document.getElementById('globalStatus').className = 'status-badge status-running';
            document.getElementById('globalStatus').textContent = 'Running';
            document.getElementById('eventLog').innerHTML = '';
            document.getElementById('browserUrl').textContent = url.substring(0, 60) + '...';

            document.querySelectorAll('.step-pill').forEach(p => p.className = 'step-pill');

            // Reset dedup set for new run
            seenEventIds = new Set();
            if (eventSource) { eventSource.close(); eventSource = null; }

            // POST /run first so pipeline_events.clear() runs server-side BEFORE
            // the EventSource connects (prevents old events from replaying).
            fetch('/run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url, company, role})
            }).then(() => {
                eventSource = new EventSource('/events');
                eventSource.onmessage = function(e) {
                    const event = JSON.parse(e.data);
                    // Deduplicate by server-assigned event ID (handles SSE auto-reconnect)
                    if (event.id !== undefined && seenEventIds.has(event.id)) return;
                    if (event.id !== undefined) seenEventIds.add(event.id);
                    addEvent(event);
                    updatePills(event);
                };
                // Start screenshot SSE stream only after run is confirmed started
                startScreenshotStream();
            }).catch(() => {
                // Even on fetch error, try to connect (server might be busy)
                eventSource = new EventSource('/events');
                eventSource.onmessage = function(e) {
                    const event = JSON.parse(e.data);
                    if (event.id !== undefined && seenEventIds.has(event.id)) return;
                    if (event.id !== undefined) seenEventIds.add(event.id);
                    addEvent(event);
                    updatePills(event);
                };
                startScreenshotStream();
            });
        }

        function stopApplication() {
            fetch('/stop', {method: 'POST'});
            document.getElementById('startBtn').disabled = false;
            document.getElementById('startBtn').textContent = 'Start Application';
            document.getElementById('stopBtn').style.display = 'none';
            document.getElementById('continueBtn').style.display = 'none';
            document.getElementById('emailVerifyBtn').style.display = 'none';
            document.getElementById('globalStatus').className = 'status-badge status-idle';
            document.getElementById('globalStatus').textContent = 'Stopped';
            if (screenshotSource) { screenshotSource.close(); screenshotSource = null; }
            addEvent({timestamp: new Date().toLocaleTimeString().slice(0,8), step: 'Stopped', status: 'error', detail: 'Stopped by user'});
        }

        function continueApplication() {
            var btn = document.getElementById('continueBtn');
            btn.disabled = true; btn.textContent = 'Analyzing...';
            addEvent({timestamp: new Date().toLocaleTimeString().slice(0,8), step: 'Continue', status: 'start', detail: 'Analyzing current page...'});
            if (!screenshotSource) startScreenshotStream();
            fetch('/continue', {method: 'POST'}).then(r => {
                if (!r.ok) throw new Error('Server error: ' + r.status);
                return r.json();
            }).then(data => {
                btn.disabled = false; btn.textContent = 'Continue';
                if (data.status === 'error') {
                    addEvent({timestamp: new Date().toLocaleTimeString().slice(0,8), step: 'Continue', status: 'error', detail: data.message || 'Unknown error'});
                }
            }).catch(err => {
                btn.disabled = false; btn.textContent = 'Continue';
                addEvent({timestamp: new Date().toLocaleTimeString().slice(0,8), step: 'Continue', status: 'error', detail: 'Request failed: ' + err.message});
            });
        }

        function triggerEmailVerify() {
            var btn = document.getElementById('emailVerifyBtn');
            btn.disabled = true; btn.textContent = 'Checking email...';
            addEvent({timestamp: new Date().toLocaleTimeString().slice(0,8), step: 'Email Verify', status: 'start', detail: 'Opening Gmail to find verification code...'});
            fetch('/email-verify', {method: 'POST'}).then(r => r.json()).then(data => {
                btn.disabled = false; btn.textContent = 'Get Email Code';
                if (data.code) {
                    addEvent({timestamp: new Date().toLocaleTimeString().slice(0,8), step: 'Email Verify', status: 'success', detail: 'Found code: ' + data.code});
                } else if (data.link) {
                    addEvent({timestamp: new Date().toLocaleTimeString().slice(0,8), step: 'Email Verify', status: 'success', detail: 'Clicked verification link'});
                } else {
                    addEvent({timestamp: new Date().toLocaleTimeString().slice(0,8), step: 'Email Verify', status: 'error', detail: data.error || 'No verification email found'});
                }
                if (!screenshotSource) startScreenshotStream();
            }).catch(() => { btn.disabled = false; btn.textContent = 'Get Email Code'; });
        }

        let screenshotSource = null;
        function startScreenshotStream() {
            if (screenshotSource) screenshotSource.close();
            screenshotSource = new EventSource('/screenshot-stream');
            screenshotSource.onmessage = function(e) {
                const data = JSON.parse(e.data);
                if (data.image) {
                    const view = document.getElementById('browserView');
                    const existing = view.querySelector('img');
                    if (existing) {
                        existing.src = 'data:image/png;base64,' + data.image;
                    } else {
                        view.innerHTML = '<img src="data:image/png;base64,' + data.image + '">';
                    }
                }
                if (data.done) {
                    document.getElementById('startBtn').disabled = false;
                    document.getElementById('startBtn').textContent = 'Start New';
                    document.getElementById('continueBtn').style.display = 'block';
                    document.getElementById('emailVerifyBtn').style.display = 'block';
                    document.getElementById('globalStatus').className = 'status-badge status-idle';
                    document.getElementById('globalStatus').textContent = 'Review';
                    // Keep screenshot stream alive for Continue/Email Verify
                }
                if (data.closed) {
                    if (screenshotSource) { screenshotSource.close(); screenshotSource = null; }
                    document.getElementById('continueBtn').style.display = 'none';
                    document.getElementById('emailVerifyBtn').style.display = 'none';
                }
            };
            screenshotSource.onerror = function() {
                // Reconnect on error
                setTimeout(() => {
                    if (screenshotSource) startScreenshotStream();
                }, 1000);
            };
        }

        function addEvent(event) {
            const log = document.getElementById('eventLog');
            const li = document.createElement('li');
            li.className = 'event-item event-' + event.status;
            li.innerHTML = '<span class="event-dot"></span>' +
                '<span class="event-time">' + event.timestamp + '</span>' +
                '<div class="event-content">' +
                '<div class="event-step">' + event.step + '</div>' +
                '<div class="event-detail">' + (event.detail || '') + '</div></div>';
            log.insertBefore(li, log.firstChild);
            // CAPTCHA alert
            var d = (event.detail || '').toLowerCase();
            var s = (event.step || '').toLowerCase();
            if (d.includes('captcha') || s.includes('captcha')) {
                playCaptchaAlert();
                var origTitle = document.title;
                var flashInt = setInterval(function() {
                    document.title = document.title === origTitle ? '\u26a0\ufe0f CAPTCHA NEEDED' : origTitle;
                }, 500);
                setTimeout(function() { clearInterval(flashInt); document.title = origTitle; }, 30000);
            }
        }

        function updatePills(event) {
            const map = {
                'Extract': 'pill-jd',
                'Answers': 'pill-answers',
                'Navigate': 'pill-nav',
                'Fill': 'pill-fill',
                'Review': 'pill-review',
                'Screenshot': 'pill-review',
                'Complete': 'pill-review',
            };
            for (const [key, id] of Object.entries(map)) {
                if (event.step.includes(key)) {
                    const pill = document.getElementById(id);
                    if (event.status === 'start') pill.className = 'step-pill active';
                    else if (event.status === 'success') pill.className = 'step-pill done';
                    else if (event.status === 'error') pill.className = 'step-pill failed';
                }
            }
        }

        // ===== MARK AS APPLIED =====
        function markAsApplied() {
            const url = document.getElementById('jobUrl').value;
            const company = document.getElementById('company').value;
            const role = document.getElementById('role').value;
            if (!url) return;
            fetch('/api/applied', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url, company, role})
            }).then(r => r.json()).then(() => {
                appliedUrls.add(url);
                applyFilters();
                addEvent({timestamp: new Date().toLocaleTimeString().slice(0,8), step: 'Applied', status: 'success', detail: 'Marked as applied: ' + company + ' - ' + role});
            });
        }

        function markNotApplied() {
            const url = document.getElementById('jobUrl').value;
            const company = document.getElementById('company').value;
            const role = document.getElementById('role').value;
            if (!url) return;
            fetch('/api/unapplied', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url})
            }).then(r => r.json()).then(() => {
                appliedUrls.delete(url);
                applyFilters();
                addEvent({timestamp: new Date().toLocaleTimeString().slice(0,8), step: 'Applied', status: 'error', detail: 'Marked as NOT applied: ' + company + ' - ' + role});
            });
        }

        // ===== FILE UPLOADS =====
        function uploadFile(type) {
            const input = document.getElementById(type + 'File');
            if (!input.files.length) return;
            const formData = new FormData();
            formData.append('file', input.files[0]);
            const statusEl = document.getElementById(type + 'Status');
            statusEl.textContent = 'Uploading...';
            statusEl.className = 'upload-status missing';
            fetch('/api/upload/' + type, { method: 'POST', body: formData })
                .then(r => r.json())
                .then(data => {
                    if (data.filename) {
                        statusEl.textContent = data.filename;
                        statusEl.className = 'upload-status uploaded';
                    } else {
                        statusEl.textContent = 'Upload failed';
                    }
                })
                .catch(() => { statusEl.textContent = 'Upload failed'; });
        }

        function checkUploads() {
            fetch('/api/uploads').then(r => r.json()).then(data => {
                const rEl = document.getElementById('resumeStatus');
                const tEl = document.getElementById('transcriptStatus');
                if (data.resume) { rEl.textContent = data.resume_name; rEl.className = 'upload-status uploaded'; }
                else { rEl.textContent = 'Not uploaded'; rEl.className = 'upload-status missing'; }
                if (data.transcript) { tEl.textContent = data.transcript_name; tEl.className = 'upload-status uploaded'; }
                else { tEl.textContent = 'Not uploaded'; tEl.className = 'upload-status missing'; }
            });
        }

        // CAPTCHA alert sound - plays beeps using Web Audio API
        function playCaptchaAlert() {
            try {
                var ctx = new (window.AudioContext || window.webkitAudioContext)();
                for (var i = 0; i < 3; i++) {
                    var osc = ctx.createOscillator();
                    var gain = ctx.createGain();
                    osc.connect(gain);
                    gain.connect(ctx.destination);
                    osc.frequency.value = 800;
                    osc.type = 'square';
                    gain.gain.value = 0.3;
                    osc.start(ctx.currentTime + i * 0.3);
                    osc.stop(ctx.currentTime + i * 0.3 + 0.15);
                }
            } catch(e) {}
            if (Notification.permission === 'granted') {
                new Notification('CAPTCHA Detected!', { body: 'Solve the CAPTCHA in the browser window to continue.' });
            } else if (Notification.permission !== 'denied') {
                Notification.requestPermission();
            }
        }

        // ===== AUTO-QUEUE =====
        let _aqPollInterval = null;

        function startAutoQueue() {
            const limit = parseInt(document.getElementById('aq-limit').value) || 10;
            const delay = parseInt(document.getElementById('aq-delay').value) || 30;
            const atsList = [];
            if (document.getElementById('aq-greenhouse').checked) atsList.push('greenhouse');
            if (document.getElementById('aq-lever').checked) atsList.push('lever');
            if (document.getElementById('aq-workday').checked) atsList.push('workday');

            document.getElementById('aqStartBtn').disabled = true;
            document.getElementById('aq-status').textContent = 'Building queue from GitHub...';

            fetch('/api/queue/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ limit, delay_seconds: delay, ats_filter: atsList })
            }).then(r => r.json()).then(data => {
                if (data.status === 'error') {
                    document.getElementById('aq-status').textContent = '❌ ' + data.message;
                    document.getElementById('aqStartBtn').disabled = false;
                    return;
                }
                if (data.status === 'empty') {
                    document.getElementById('aq-status').textContent = '✅ No new matching jobs found.';
                    document.getElementById('aqStartBtn').disabled = false;
                    return;
                }
                document.getElementById('aqStopBtn').style.display = 'inline-block';
                document.getElementById('aq-status').textContent =
                    `⚡ Running — ${data.queued} jobs queued. First 5: ${(data.preview||[]).map(j=>j.company+' ('+j.ats+')').join(', ')}`;
                _aqPollInterval = setInterval(loadQueueStatus, 3000);
            }).catch(e => {
                document.getElementById('aq-status').textContent = '❌ ' + e;
                document.getElementById('aqStartBtn').disabled = false;
            });
        }

        function stopAutoQueue() {
            fetch('/api/queue/stop', {method: 'POST'});
            document.getElementById('aq-status').textContent = 'Stopping after current job...';
        }

        function loadQueueStatus() {
            fetch('/api/queue').then(r => r.json()).then(data => {
                const total = data.queue ? data.queue.length : 0;
                const remaining = data.remaining || 0;
                const idx = data.index || 0;
                const running = data.running;

                if (!running && _aqPollInterval) {
                    clearInterval(_aqPollInterval);
                    _aqPollInterval = null;
                    document.getElementById('aqStartBtn').disabled = false;
                    document.getElementById('aqStopBtn').style.display = 'none';
                }

                const statusEl = document.getElementById('aq-status');
                if (running) {
                    const cur = data.queue && data.queue[idx];
                    const curStr = cur ? `${cur.company} — ${cur.role} (${cur.ats})` : '...';
                    statusEl.textContent = `⚡ Running [${idx+1}/${total}]: ${curStr}  |  ${remaining} remaining`;
                } else if (total > 0) {
                    statusEl.textContent = `✅ Batch complete. Processed ${Math.min(idx, total)}/${total} jobs.`;
                }

                // Render queue list
                if (data.queue && data.queue.length > 0) {
                    const listEl = document.getElementById('aq-queue-list');
                    const itemsEl = document.getElementById('aq-queue-items');
                    listEl.style.display = 'block';
                    itemsEl.innerHTML = data.queue.map((j, i) => {
                        const done = i < idx;
                        const active = i === idx && running;
                        const color = done ? '#22c55e' : active ? '#60a5fa' : '#475569';
                        const icon = done ? '✓' : active ? '▶' : '○';
                        return `<div style="font-size:12px;color:${color};padding:2px 6px;">
                            ${icon} [${i+1}] ${j.company} — ${j.role} <span style="color:#64748b;">(${j.ats})</span>
                        </div>`;
                    }).join('');
                }
            });
        }

        // Load jobs and check uploads on page load
        loadJobs();
        checkUploads();
        if ('Notification' in window && Notification.permission === 'default') {
            Notification.requestPermission();
        }

        // Auto-refresh jobs every 15 minutes
        setInterval(() => {
            loadJobs();
        }, 15 * 60 * 1000);
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return DASHBOARD_HTML


_jobs_cache: list = []
_jobs_cache_time: float = 0.0
_JOBS_CACHE_TTL = 1800  # 30 minutes

@app.get("/api/jobs")
async def get_jobs(refresh: bool = False):
    """Fetch and parse internship listings from SimplifyJobs GitHub repo.
    Cached for 30 minutes. Pass ?refresh=true to force a fresh fetch.
    """
    global _jobs_cache, _jobs_cache_time
    import time as _time
    now = _time.time()
    if not refresh and _jobs_cache and (now - _jobs_cache_time) < _JOBS_CACHE_TTL:
        return JSONResponse({"jobs": _jobs_cache, "total": len(_jobs_cache), "cached": True})
    try:
        from scraper.github_scraper import fetch_readme, parse_internship_table
        readme = await asyncio.to_thread(fetch_readme)
        postings = parse_internship_table(readme)
        _jobs_cache = postings
        _jobs_cache_time = now
        return JSONResponse({"jobs": postings, "total": len(postings), "cached": False})
    except Exception as e:
        # Return stale cache if available rather than empty
        if _jobs_cache:
            return JSONResponse({"jobs": _jobs_cache, "total": len(_jobs_cache), "cached": True, "warning": str(e)})
        return JSONResponse({"jobs": [], "total": 0, "error": str(e)}, status_code=500)


@app.get("/api/applied")
async def get_applied():
    """Return list of applied job URLs."""
    from database.tracker import get_applied_urls
    return JSONResponse({"urls": list(get_applied_urls())})


@app.post("/api/applied")
async def mark_as_applied(request: Request):
    """Mark a job as applied."""
    from database.tracker import mark_applied
    data = await request.json()
    mark_applied(data.get("url", ""), data.get("company", ""), data.get("role", ""))
    return JSONResponse({"status": "ok"})


@app.post("/api/unapplied")
async def mark_as_not_applied(request: Request):
    """Remove a job from the applied list."""
    from database.tracker import unmark_applied
    data = await request.json()
    unmark_applied(data.get("url", ""))
    return JSONResponse({"status": "ok"})


@app.get("/api/uploads")
async def get_uploads():
    """Return current upload status. Re-check disk for files that appeared after startup."""
    global uploaded_resume, uploaded_transcript
    if not uploaded_resume and _default_resume.exists():
        uploaded_resume = str(_default_resume)
    if not uploaded_transcript and _default_transcript.exists():
        uploaded_transcript = str(_default_transcript)
    return JSONResponse({
        "resume": bool(uploaded_resume),
        "resume_name": Path(uploaded_resume).name if uploaded_resume else "",
        "transcript": bool(uploaded_transcript),
        "transcript_name": Path(uploaded_transcript).name if uploaded_transcript else "",
    })


@app.post("/api/upload/{doc_type}")
async def upload_document(doc_type: str, file: UploadFile = File(...)):
    """Upload resume or transcript PDF."""
    global uploaded_resume, uploaded_transcript

    if doc_type not in ("resume", "transcript"):
        return JSONResponse({"error": "Invalid doc type"}, status_code=400)

    filename = file.filename or f"{doc_type}.pdf"
    save_path = UPLOADS_DIR / filename
    content = await file.read()
    save_path.write_bytes(content)

    if doc_type == "resume":
        uploaded_resume = str(save_path)
    else:
        uploaded_transcript = str(save_path)

    return JSONResponse({"filename": filename, "path": str(save_path)})


@app.get("/events")
async def events(request: Request):
    # Support Last-Event-ID header for reconnect (browser sends this automatically)
    last_event_id_header = request.headers.get("last-event-id", "")
    try:
        resume_from = int(last_event_id_header) + 1 if last_event_id_header else 0
    except ValueError:
        resume_from = 0

    async def event_stream():
        # Yield control immediately so any concurrent /run POST can clear
        # pipeline_events before we start streaming (prevents old-event replay).
        await asyncio.sleep(0)
        last_id = max(resume_from, 0)
        while True:
            if last_id < len(pipeline_events):
                for event in pipeline_events[last_id:]:
                    # Include SSE `id:` so browser sends Last-Event-ID on reconnect
                    yield f"id: {event['id']}\ndata: {json.dumps(event)}\n\n"
                last_id = len(pipeline_events)
            await asyncio.sleep(0.3)
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/screenshot")
async def get_screenshot():
    return JSONResponse({
        "image": latest_screenshot_b64,
        "done": not pipeline_running,
    })


@app.get("/screenshot-stream")
async def screenshot_stream():
    """SSE stream that pushes screenshots. Stays alive for Continue/Email Verify."""
    async def stream():
        last_version = -1
        sent_done = False
        idle_ticks = 0
        while True:
            if screenshot_version != last_version and latest_screenshot_b64:
                last_version = screenshot_version
                idle_ticks = 0
                yield f"data: {json.dumps({'image': latest_screenshot_b64, 'done': not pipeline_running, 'v': screenshot_version})}\n\n"
                if not pipeline_running and not sent_done:
                    sent_done = True

            idle_ticks += 1
            # Send keepalive every ~5s to prevent SSE timeout
            if idle_ticks > 100:
                idle_ticks = 0
                yield f"data: {json.dumps({'keepalive': True, 'done': not pipeline_running, 'v': screenshot_version})}\n\n"

            # Only close if browser is gone AND pipeline done AND we already sent done
            if not pipeline_running and browser_instance is None and sent_done:
                yield f"data: {json.dumps({'closed': True})}\n\n"
                break
            await asyncio.sleep(0.05)  # 20fps check rate for low-latency live view
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/test_llm")
async def test_llm_providers():
    """Test which LLM providers are working. Hit this in browser to check status."""
    import asyncio as _aio
    from openai import OpenAI as _OAI
    import os as _os
    results = {}
    ollama_model = _os.getenv("OLLAMA_MODEL", "")
    ollama_base  = _os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    providers = [
        ("ollama (local)",  ollama_base,                                                   "ollama",                         ollama_model or "qwen2.5:72b"),
        ("gemini",          "https://generativelanguage.googleapis.com/v1beta/openai/",    _os.getenv("GEMINI_API_KEY"),     "gemini-2.0-flash"),
        ("cerebras",        "https://api.cerebras.ai/v1",                                  _os.getenv("CEREBRAS_API_KEY"),   "qwen-3-235b-a22b-instruct-2507"),
        ("groq",            "https://api.groq.com/openai/v1",                              _os.getenv("GROQ_API_KEY"),       "llama-3.3-70b-versatile"),
    ]
    for name, base_url, key, model in providers:
        if name.startswith("ollama") and not ollama_model:
            results[name] = "NOT CONFIGURED (set OLLAMA_MODEL in .env)"
            continue
        if not key and not name.startswith("ollama"):
            results[name] = "NO KEY"
            continue
        try:
            client = _OAI(base_url=base_url, api_key=key, timeout=15.0)
            resp = await _aio.to_thread(
                lambda c=client, m=model: c.chat.completions.create(
                    model=m, max_tokens=5,
                    messages=[{"role":"user","content":"Reply OK"}]
                )
            )
            results[name] = f"OK — '{resp.choices[0].message.content.strip()[:30]}'"
        except Exception as e:
            results[name] = f"FAIL: {str(e)[:120]}"
    return JSONResponse(results)


@app.post("/continue")
async def continue_application_endpoint():
    """Analyze current page and TAKE ACTION: fill credentials, run Workday handler, fill forms."""
    global pipeline_running, latest_screenshot_b64, screenshot_version, active_page, active_context
    print(">>> /continue endpoint called")

    page = active_page
    print(f">>> active_page: {active_page}")
    if not page:
        # Fallback 1: try browser-use browser
        print(">>> trying _bu_browser fallback...")
        try:
            from applicator.form_filler import _bu_browser
            if _bu_browser:
                page = await _bu_browser.get_current_page()
                if page:
                    print(f">>> _bu_browser fallback got page: {page.url}")
                    active_page = page
                    try:
                        active_context = page.context
                    except Exception:
                        pass
                else:
                    print(">>> _bu_browser.get_current_page() returned None")
            else:
                print(">>> _bu_browser is None")
        except Exception as e:
            print(f">>> _bu_browser fallback failed: {e}")

    if not page:
        # Fallback 2: Reconnect via CDP URL (file-backed, survives server reload)
        page = await _reconnect_via_cdp("Continue")

    if not page:
        msg = "No browser page available. Start an application first."
        print(f">>> FAIL: {msg}")
        add_event("Continue", "error", msg)
        return JSONResponse({"status": "error", "message": msg})

    # Verify page is alive
    try:
        current_url = page.url
        print(f">>> page alive, url: {current_url[:80]}")
    except Exception as e:
        print(f">>> page dead ({e}), trying _bu_browser refresh...")
        # Page ref is stale — try getting a fresh one from _bu_browser
        page = None
        try:
            from applicator.form_filler import _bu_browser
            if _bu_browser:
                page = await _bu_browser.get_current_page()
                if page:
                    active_page = page
                    try:
                        active_context = page.context
                    except Exception:
                        pass
                    current_url = page.url
                    print(f">>> refreshed page from _bu_browser: {current_url[:80]}")
        except Exception as e2:
            print(f">>> _bu_browser refresh also failed: {e2}")
        # Try CDP reconnect as last resort
        if not page:
            page = await _reconnect_via_cdp("Continue")
            if page:
                current_url = page.url
        if not page:
            active_page = None
            add_event("Continue", "error", "Browser page closed. Start a new application.")
            return JSONResponse({"status": "error", "message": "Browser page closed"})

    # Screenshot
    try:
        ss = await page.screenshot(type="png")
        latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
        screenshot_version += 1
    except Exception:
        pass

    add_event("Continue", "info", f"Analyzing: {current_url[:80]}")

    try:
        state = await asyncio.wait_for(page.evaluate("""() => {
            const t = document.body.innerText.toLowerCase();
            const url = window.location.href.toLowerCase();
            return {
                url: window.location.href,
                bodyText: document.body.innerText.substring(0, 2000),
                isWorkday: url.includes('workday') || url.includes('myworkdayjobs'),
                hasProgressBar: !!document.querySelector('[data-automation-id="progressBar"]'),
                isLogin: (
                    !!document.querySelector('[data-automation-id="signInSubmitButton"]') ||
                    !!document.querySelector('[data-automation-id="createAccountSubmitButton"]') ||
                    !!document.querySelector('[data-automation-id="createAccountLink"]') ||
                    (t.includes('sign in') && !!document.querySelector('input[type="password"]'))
                ),
                hasEmailField: !!document.querySelector('[data-automation-id="email"], input[type="email"]'),
                hasCreateAccount: !!document.querySelector('[data-automation-id="createAccountSubmitButton"]'),
                hasSignIn: !!document.querySelector('[data-automation-id="signInSubmitButton"]'),
                isVerify: ['verify your email','verification code','check your email','check your inbox','enter the code'].some(k => t.includes(k)),
                isSuccess: ['application submitted','thank you for applying','application received','successfully submitted'].some(k => t.includes(k)),
                errorMsgs: Array.from(document.querySelectorAll('[class*="error"],[role="alert"],[data-automation-id="errorMessage"]')).filter(e => e.offsetParent !== null).map(e => e.innerText.trim()).filter(t => t),
                visibleFields: Array.from(document.querySelectorAll('input:not([type="hidden"]),textarea,select')).filter(e => e.offsetParent !== null).length,
                activeStep: (() => { const s = document.querySelector('[data-automation-id="progressBarActiveStep"]'); return s ? s.innerText.trim() : ''; })(),
                buttons: Array.from(document.querySelectorAll('button')).filter(b => b.offsetParent !== null).map(b => b.innerText.trim().substring(0,40)).filter(t => t).slice(0,10),
            };
        }"""), timeout=15.0)

        # --- SUCCESS ---
        if state.get("isSuccess"):
            add_event("Continue", "success", "Application submitted successfully!")
            return JSONResponse({"status": "ok", "action": "success"})

        # --- VERIFICATION ---
        if state.get("isVerify"):
            add_event("Continue", "info",
                "Verification page detected. Automatically checking Gmail for security code...")

            # Auto-fetch and enter the security code
            try:
                from applicator.email_handler import auto_handle_security_code
                company = ""
                try:
                    company = await page.title()
                except Exception:
                    pass
                async def on_event(step, status, detail=""):
                    add_event(step, status, detail)

                code_entered = await auto_handle_security_code(
                    page, company_name=company, event_callback=on_event
                )

                # Take screenshot after attempt
                try:
                    ss = await page.screenshot(type="png")
                    latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
                    screenshot_version += 1
                except Exception:
                    pass

                if code_entered:
                    add_event("Continue", "success",
                        "Security code entered! Waiting for page to load...")
                    await asyncio.sleep(3)
                    # Take another screenshot after page loads
                    try:
                        ss = await page.screenshot(type="png")
                        latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
                        screenshot_version += 1
                    except Exception:
                        pass
                    return JSONResponse({"status": "ok", "action": "code_entered"})
                else:
                    add_event("Continue", "warning",
                        "Auto code retrieval failed. Click 'Get Email Code' to try again or enter manually.")
            except Exception as e:
                add_event("Continue", "warning", f"Auto email check error: {e}")

            # Take a fresh screenshot so user can see the verification page
            try:
                ss = await page.screenshot(type="png")
                latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
                screenshot_version += 1
            except Exception:
                pass
            return JSONResponse({"status": "ok", "action": "verify"})

        # --- ERRORS ---
        if state.get("errorMsgs"):
            for err in state["errorMsgs"][:3]:
                add_event("Continue", "error", f"Page error: {err[:150]}")

        # --- LOGIN/ACCOUNT ---
        if state.get("isLogin"):
            add_event("Continue", "info", "Login page detected. Filling credentials...")
            import yaml
            creds_path = Path(__file__).parent.parent / "credentials.yaml"
            creds = {}
            try:
                with open(creds_path) as f:
                    creds = yaml.safe_load(f) or {}
            except Exception:
                pass
            wd = creds.get("workday", {})
            email = wd.get("email", "")
            pw = wd.get("password", "")
            if not email or not pw:
                add_event("Continue", "error", "No credentials in credentials.yaml")
                return JSONResponse({"status": "error", "message": "No credentials"})

            # Fill email
            for sel in ['[data-automation-id="email"]', 'input[type="email"]', 'input[name="email"]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=1000):
                        await el.fill(email, timeout=3000)
                        break
                except Exception:
                    continue
            # Fill password
            for sel in ['[data-automation-id="password"]', 'input[type="password"]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=1000):
                        await el.fill(pw, timeout=3000)
                        break
                except Exception:
                    continue
            # Fill verify password
            try:
                vp = page.locator('[data-automation-id="verifyPassword"]').first
                if await vp.is_visible(timeout=1000):
                    await vp.fill(pw, timeout=3000)
            except Exception:
                pass
            # Check consent checkboxes — click the click_filter inside them
            from applicator.workday_handler import check_workday_consent
            checkbox_ok = await check_workday_consent(page, event_callback=lambda s, st, d: add_event(s, st, d))

            if not checkbox_ok and state.get("hasCreateAccount"):
                add_event("Continue", "error", "Checkbox not checked. Please check it manually, then click Continue.")
                return JSONResponse({"status": "ok", "action": "checkbox_failed"})

            # Click appropriate button
            clicked = False
            for sel in ['[data-automation-id="createAccountSubmitButton"]', '[data-automation-id="signInSubmitButton"]',
                        'div[data-automation-id="click_filter"][aria-label="Create Account"]',
                        'div[data-automation-id="click_filter"][aria-label="Sign In"]',
                        'button:has-text("Sign In")', 'button:has-text("Create Account")']:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=1000):
                        await btn.click(force=True, timeout=5000)
                        clicked = True
                        add_event("Continue", "info", f"Clicked: {sel[:50]}")
                        break
                except Exception:
                    continue
            if not clicked:
                # JS fallback
                await page.evaluate("""() => {
                    for (const sel of ['[data-automation-id="createAccountSubmitButton"]','[data-automation-id="signInSubmitButton"]']) {
                        const el = document.querySelector(sel);
                        if (el) { el.click(); return; }
                    }
                    for (const btn of document.querySelectorAll('button, div[role="button"]')) {
                        const t = btn.innerText.trim();
                        if (t === 'Create Account' || t === 'Sign In') { btn.click(); return; }
                    }
                }""")
                add_event("Continue", "info", "Clicked auth button via JS")

            await asyncio.sleep(5.0)
            try:
                ss = await page.screenshot(type="png")
                latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
                screenshot_version += 1
            except Exception:
                pass
            # Check if still stuck on Create Account (checkbox may not have worked)
            still_on_create = await page.evaluate(
                "() => !!document.querySelector('[data-automation-id=\"createAccountSubmitButton\"]')")
            if still_on_create:
                add_event("Continue", "info", "Still on Create Account page. Retrying checkbox + submit...")
                await check_workday_consent(page, event_callback=lambda s, st, d: add_event(s, st, d))
                await asyncio.sleep(1.0)
                # Try clicking again with JS
                await page.evaluate("""() => {
                    const btn = document.querySelector('[data-automation-id="createAccountSubmitButton"]')
                        || document.querySelector('div[data-automation-id="click_filter"][aria-label="Create Account"]');
                    if (btn) btn.click();
                }""")
                await asyncio.sleep(5.0)
                try:
                    ss = await page.screenshot(type="png")
                    latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
                    screenshot_version += 1
                except Exception:
                    pass
                # Check for error messages
                errs = await page.evaluate("""() =>
                    Array.from(document.querySelectorAll('[class*="error"],[role="alert"]'))
                    .filter(e => e.offsetParent !== null).map(e => e.innerText.trim()).filter(t => t)
                """)
                if errs:
                    add_event("Continue", "error", f"Create Account errors: {errs}")

            add_event("Continue", "info", f"After auth: {page.url[:80]}. Click Continue again to proceed.")
            return JSONResponse({"status": "ok", "action": "auth"})

        # --- Resume path for all form-filling below ---
        resume_path = uploaded_resume or ""
        if not resume_path:
            for p in [
                str(Path(__file__).parent.parent / "uploads" / "EdrickChang_Resume.pdf"),
                r"C:\Users\Owner\jobs\uploads\EdrickChang_Resume.pdf",
                r"C:\Users\Owner\Downloads\EdrickChang_Resume.pdf",
                str(Path(os.path.expanduser("~/Downloads/EdrickChang.pdf"))),
            ]:
                if os.path.exists(p):
                    resume_path = p
                    break
        print(f">>> /continue resume_path: {resume_path}")

        # --- WORKDAY WIZARD ---
        if state.get("isWorkday") and (state.get("hasProgressBar") or state.get("activeStep")):
            step = state.get("activeStep", "Unknown")
            add_event("Continue", "info", f"Workday wizard step: {step}. Running handler...")

            # === DIRECT DROPDOWN FILLER (bypasses workday_handler module caching issues) ===
            # First try to fill Application Questions dropdowns directly via Playwright
            try:
                dropdown_info = await asyncio.wait_for(page.evaluate("""() => {
                    const results = [];
                    const containers = document.querySelectorAll('[data-automation-id^="formField-"]');
                    for (const c of containers) {
                        if (c.offsetParent === null) continue;
                        // Look for Workday dropdown buttons or native selects
                        const btn = c.querySelector('button[aria-haspopup], [data-automation-id="selectWidget"], [data-automation-id="dropdownWidget"]');
                        const sel = c.querySelector('select');
                        const dropEl = btn || sel;
                        if (!dropEl) continue;

                        const dropText = (dropEl.innerText || dropEl.textContent || '').trim();
                        if (sel) {
                            if (sel.selectedIndex > 0) continue;
                        } else {
                            if (dropText && !dropText.toLowerCase().includes('select') && dropText !== '--') continue;
                        }

                        // Extract label using multiple strategies
                        const dataid = c.getAttribute('data-automation-id') || '';
                        let label = '';

                        // Strategy 1 (PRIMARY): formLabel-<uuid> is a SIBLING of formField-<uuid> in Workday
                        if (!label && dataid.startsWith('formField-')) {
                            const uuid = dataid.replace('formField-', '');
                            const formLabel = document.querySelector('[data-automation-id="formLabel-' + uuid + '"]');
                            if (formLabel) label = formLabel.innerText.trim();
                        }
                        // Strategy 2: check parent element for any label siblings
                        if (!label) {
                            const parent = c.parentElement;
                            if (parent) {
                                const allLabels = parent.querySelectorAll('[data-automation-id*="formLabel"], [data-automation-id*="label"], label');
                                for (const pl of allLabels) {
                                    if (!c.contains(pl)) { label = pl.innerText.trim(); if (label) break; }
                                }
                            }
                        }
                        // Strategy 3: label inside container
                        if (!label) { const lbl = c.querySelector('label'); if (lbl) label = lbl.innerText.trim(); }
                        // Strategy 4: aria-label on the dropdown element
                        if (!label) { label = dropEl.getAttribute('aria-label') || ''; }
                        // Strategy 5: aria-labelledby
                        if (!label) {
                            const lid = dropEl.getAttribute('aria-labelledby');
                            if (lid) {
                                const parts = lid.split(' ');
                                for (const p of parts) { const r = document.getElementById(p); if (r) { const t = r.innerText.trim(); if (t && !t.toLowerCase().includes('select one')) { label = t; break; } } }
                            }
                        }
                        // Strategy 6: legend
                        if (!label) { const leg = c.querySelector('legend'); if (leg) label = leg.innerText.trim(); }
                        const rect = dropEl.getBoundingClientRect();
                        results.push({
                            label, dataid, isNativeSelect: !!sel, currentText: dropText,
                            x: rect.x + rect.width / 2, y: rect.y + rect.height / 2,
                            tagName: dropEl.tagName, outerSnippet: dropEl.outerHTML.slice(0, 120),
                        });
                    }
                    return results;
                }"""), timeout=10.0)

                add_event("Continue", "info", f"Direct scan: found {len(dropdown_info or [])} unfilled dropdown(s)")
                filled_direct = 0

                # Answer mapping for yes/no Application Questions
                answer_map = {
                    "unrestricted": "Yes",
                    "authorization to work": "Yes",
                    "authorized to work": "Yes",
                    "sponsorship": "No",
                    "non-compete": "No",
                    "employment agreement": "No",
                    "contractor": "No",
                    "auditor": "No",
                    "kpmg": "No",
                    "bsr": "No",
                    "previously employed": "No",
                }

                for dd in (dropdown_info or []):
                    lbl = dd.get("label", "")
                    add_event("Continue", "info", f"DD: label='{lbl[:70]}' tag={dd.get('tagName','')} native={dd.get('isNativeSelect')} dataid={dd.get('dataid','')[:30]}")

                    if not lbl:
                        add_event("Continue", "warn", f"Empty label, snippet: {dd.get('outerSnippet','')[:80]}")
                        continue

                    lbl_lower = lbl.lower()
                    answer = None
                    for pattern, ans in answer_map.items():
                        if pattern in lbl_lower:
                            answer = ans
                            break

                    if not answer:
                        add_event("Continue", "warn", f"No answer for: '{lbl[:60]}'")
                        continue

                    add_event("Continue", "info", f"Filling '{answer}' for '{lbl[:50]}'")

                    if dd.get("isNativeSelect"):
                        try:
                            sel_locator = f'[data-automation-id="{dd["dataid"]}"] select'
                            await page.locator(sel_locator).first.select_option(label=answer)
                            filled_direct += 1
                            add_event("Continue", "success", f"Selected '{answer}' for '{lbl[:40]}'")
                        except Exception as e:
                            add_event("Continue", "warn", f"Native select err: {str(e)[:60]}")
                    else:
                        # Custom Workday dropdown: click to open, then pick option
                        try:
                            await page.mouse.click(dd["x"], dd["y"])
                            await asyncio.sleep(0.6)

                            # Try to find and click matching option
                            option_clicked = False
                            options = page.locator('[role="option"], [role="listbox"] [role="option"], [data-automation-id*="option"]')
                            count = await options.count()
                            add_event("Continue", "info", f"Popup options: {count}")
                            for i in range(count):
                                opt = options.nth(i)
                                text = (await opt.inner_text()).strip()
                                if text.lower() == answer.lower():
                                    await opt.click(timeout=3000)
                                    option_clicked = True
                                    filled_direct += 1
                                    add_event("Continue", "success", f"Clicked '{answer}' for '{lbl[:40]}'")
                                    break

                            if not option_clicked:
                                # JS fallback
                                js_ok = await page.evaluate(f"""() => {{
                                    const opts = document.querySelectorAll('[role="option"], [role="listbox"] li, [data-automation-id*="option"]');
                                    for (const o of opts) {{
                                        if (o.innerText.trim().toLowerCase() === '{answer.lower()}') {{ o.click(); return true; }}
                                    }}
                                    return false;
                                }}""")
                                if js_ok:
                                    filled_direct += 1
                                    add_event("Continue", "success", f"JS-clicked '{answer}' for '{lbl[:40]}'")
                                else:
                                    await page.keyboard.press("Escape")
                                    add_event("Continue", "warn", f"No '{answer}' option found for '{lbl[:40]}'")
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            add_event("Continue", "warn", f"Custom DD err: {str(e)[:60]}")

                if filled_direct > 0:
                    add_event("Continue", "success", f"Direct filler: {filled_direct} dropdown(s) filled!")
                    # Click Save and Continue
                    try:
                        next_btn = page.locator('button:has-text("Save and Continue"), button:has-text("Next"), button[data-automation-id="bottom-navigation-next-button"]')
                        if await next_btn.count() > 0:
                            await next_btn.first.click(timeout=5000)
                            add_event("Continue", "info", "Clicked Save and Continue")
                    except Exception:
                        pass
                    return JSONResponse({"status": "ok", "action": "workday_direct"})

            except Exception as e:
                add_event("Continue", "warn", f"Direct dropdown scan error: {str(e)[:80]}")

            # === FALLBACK: Use workday_handler module ===
            import importlib
            import applicator.workday_handler as _wh_mod
            importlib.reload(_wh_mod)
            from applicator.workday_handler import handle_workday_application
            async def on_evt(s, st, d=""):
                add_event(s, st, d)
            async def on_ss(b):
                global latest_screenshot_b64, screenshot_version
                if b:
                    latest_screenshot_b64 = base64.b64encode(b).decode("utf-8")
                    screenshot_version += 1

            try:
                result = await asyncio.wait_for(
                    handle_workday_application(page, resume_path, "", "", "", on_evt, on_ss),
                    timeout=180.0  # 3 minute max for Workday handler
                )
            except asyncio.TimeoutError:
                add_event("Continue", "error", "Workday handler timed out after 3 minutes. Click Continue to retry.")
                return JSONResponse({"status": "ok", "action": "timeout"})
            add_event("Continue", "success" if not result.get("errors") else "info",
                f"Workday: {result.get('filled',0)} filled, {result.get('failed',0)} failed. Click Continue if more steps remain.")
            return JSONResponse({"status": "ok", "action": "workday"})

        # --- REGULAR FORM ---
        if state.get("visibleFields", 0) > 3:
            add_event("Continue", "info", f"Form with {state['visibleFields']} fields. Filling...")

            from applicator.form_filler import JS_EXTRACT_FIELDS, map_fields_to_profile, fill_form
            fields = await page.evaluate(JS_EXTRACT_FIELDS)
            if fields:
                company = await page.evaluate("document.title") or ""
                mappings = await asyncio.to_thread(map_fields_to_profile, fields, "", company, "")
                mappings = [m for m in mappings if isinstance(m, dict)]
                async def on_evt(s, st, d=""):
                    add_event(s, st, d)
                result = await fill_form(page, mappings, resume_path, event_callback=on_evt, screenshot_page=page)
                add_event("Continue", "info", f"Filled {result.get('filled',0)}, failed {result.get('failed',0)}")
            else:
                add_event("Continue", "info", "No extractable fields on this page.")

            # Run custom dropdown/radio/checkbox handler (Phase A-E)
            add_event("Continue", "info", "Running custom dropdown & EEO handler...")
            await _handle_custom_fields(page, add_event)
            return JSONResponse({"status": "ok", "action": "form"})

        # --- UNKNOWN / FEW FIELDS ---
        # Still try the custom dropdown handler — the self-ID section may have
        # custom React Select dropdowns that don't count as native inputs
        add_event("Continue", "info",
            f"{state.get('visibleFields',0)} visible fields. Running custom dropdown handler...")
        await _handle_custom_fields(page, add_event)
        return JSONResponse({"status": "ok", "action": "form_custom"})

    except Exception as e:
        add_event("Continue", "error", f"Failed: {e}")
        import traceback
        add_event("Continue", "info", traceback.format_exc()[:500])
        return JSONResponse({"status": "error", "message": str(e)})


@app.post("/email-verify")
async def email_verify_endpoint():
    """Open Gmail, find verification code/link, enter it on current page."""
    global pipeline_running, latest_screenshot_b64, screenshot_version

    page = active_page
    context = active_context
    if not page:
        try:
            from applicator.form_filler import _bu_browser
            if _bu_browser:
                page = await _bu_browser.get_current_page()
                if page:
                    context = page.context
        except Exception:
            pass

    if not page or not context:
        return JSONResponse({"status": "error", "error": "No browser page available"})

    try:
        from applicator.email_handler import handle_email_verification, enter_verification_code

        company = ""
        try:
            company = await page.title()
        except Exception:
            pass

        async def on_event(step, status, detail=""):
            add_event(step, status, detail)

        async def on_ss(ss_bytes):
            global latest_screenshot_b64, screenshot_version
            if ss_bytes:
                latest_screenshot_b64 = base64.b64encode(ss_bytes).decode("utf-8")
                screenshot_version += 1

        result = await handle_email_verification(
            context=context, original_page=page,
            company_name=company, event_callback=on_event, screenshot_callback=on_ss,
        )

        if result["success"] and result["method"] == "code" and result["code"]:
            entered = await enter_verification_code(page, result["code"], on_event)
            try:
                ss = await page.screenshot(type="png")
                latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
                screenshot_version += 1
            except Exception:
                pass
            return JSONResponse({"status": "ok", "code": result["code"]})

        elif result["success"] and result["method"] == "link":
            add_event("Email Verify", "success", "Verification link clicked. Refreshing...")
            await asyncio.sleep(3.0)
            try:
                await page.reload(wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            try:
                ss = await page.screenshot(type="png")
                latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
                screenshot_version += 1
            except Exception:
                pass
            return JSONResponse({"status": "ok", "link": result["link"]})

        else:
            add_event("Email Verify", "warning", "No verification email found. Try again in a few seconds.")
            return JSONResponse({"status": "error", "error": "No verification email found"})

    except Exception as e:
        add_event("Email Verify", "error", f"Failed: {e}")
        return JSONResponse({"status": "error", "error": str(e)})


@app.post("/stop")
async def stop_pipeline():
    global pipeline_stop_requested, pipeline_running, browser_instance, active_page, active_context
    pipeline_stop_requested = True
    pipeline_running = False
    active_page = None
    active_context = None
    add_event("Stopped", "error", "Stopped by user")
    if browser_instance:
        try:
            await browser_instance.close()
        except Exception:
            pass
        browser_instance = None
    try:
        from applicator.form_filler import close_browser, close_browser_agent
        await close_browser()
        await close_browser_agent()
    except Exception:
        pass
    return JSONResponse({"status": "stopped"})


@app.post("/run")
async def run_pipeline(request: Request):
    global pipeline_running
    if pipeline_running:
        return JSONResponse({"status": "error", "message": "Already running"})

    data = await request.json()
    global pipeline_stop_requested
    pipeline_events.clear()
    pipeline_running = True
    pipeline_stop_requested = False
    asyncio.create_task(_background_screenshot_loop())  # live view during pipeline
    asyncio.create_task(_run_application(
        data.get("url", ""),
        data.get("company", ""),
        data.get("role", ""),
    ))
    return JSONResponse({"status": "started"})


# ─────────────────────────────────────────────────────────────────────────────
# Auto-queue: scraper → apply batch loop
# ─────────────────────────────────────────────────────────────────────────────

_auto_queue: list[dict] = []          # jobs waiting to be processed
_auto_queue_running: bool = False     # True while the batch loop is active
_auto_queue_stop: bool = False        # signal to abort the loop
_auto_queue_index: int = 0            # current position in queue


@app.get("/api/queue")
async def get_queue():
    return JSONResponse({
        "queue": _auto_queue,
        "running": _auto_queue_running,
        "index": _auto_queue_index,
        "remaining": max(0, len(_auto_queue) - _auto_queue_index),
    })


@app.post("/api/queue/stop")
async def stop_queue():
    global _auto_queue_stop
    _auto_queue_stop = True
    return JSONResponse({"status": "stopping"})


@app.post("/api/queue/start")
async def start_queue(request: Request):
    """
    Start the auto-apply batch loop.

    Body (all optional):
      {
        "ats_filter": ["greenhouse", "lever", "workday"],  // only run these ATS types
        "limit": 10,                                        // max jobs to attempt
        "delay_seconds": 30,                               // pause between applications
        "refresh": false                                    // re-fetch jobs from GitHub
      }
    """
    global _auto_queue, _auto_queue_running, _auto_queue_stop, _auto_queue_index

    if _auto_queue_running:
        return JSONResponse({"status": "error", "message": "Queue already running"})
    if pipeline_running:
        return JSONResponse({"status": "error", "message": "Single-job pipeline is active; stop it first"})

    data = await request.json()
    ats_filter: list = data.get("ats_filter", ["greenhouse", "lever", "workday"])
    limit: int = data.get("limit", 20)
    delay_seconds: int = data.get("delay_seconds", 30)
    refresh: bool = data.get("refresh", False)

    # Build queue from scraper
    try:
        from scraper.github_scraper import fetch_readme, parse_internship_table
        from database.tracker import is_posting_seen
        from applicator.ats_profiles import detect_ats

        readme = await asyncio.to_thread(fetch_readme)
        postings = parse_internship_table(readme)

        queued = []
        for p in postings:
            if len(queued) >= limit:
                break
            if is_posting_seen(p["url"]):
                continue
            ats = detect_ats(p["url"])
            if ats_filter and ats not in ats_filter:
                continue
            queued.append({**p, "ats": ats or "unknown"})

        _auto_queue = queued
        _auto_queue_index = 0
        _auto_queue_stop = False
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"Failed to build queue: {e}"}, status_code=500)

    if not _auto_queue:
        return JSONResponse({"status": "empty", "message": "No new matching jobs found"})

    asyncio.create_task(_run_auto_queue(delay_seconds))
    return JSONResponse({
        "status": "started",
        "queued": len(_auto_queue),
        "preview": [{"company": j["company"], "role": j["role"], "ats": j["ats"]} for j in _auto_queue[:5]],
    })


async def _run_auto_queue(delay_seconds: int = 30):
    """Background task: apply to each job in _auto_queue sequentially."""
    global _auto_queue_running, _auto_queue_stop, _auto_queue_index, pipeline_events, pipeline_running

    _auto_queue_running = True
    add_event("Auto-Queue", "start", f"Starting batch run: {len(_auto_queue)} jobs")

    for idx, job in enumerate(_auto_queue):
        _auto_queue_index = idx

        if _auto_queue_stop:
            add_event("Auto-Queue", "warning", "Batch stopped by user")
            break

        company = job.get("company", "")
        role = job.get("role", "")
        url = job.get("url", "")
        ats = job.get("ats", "unknown")

        add_event("Auto-Queue", "info",
            f"[{idx+1}/{len(_auto_queue)}] {company} — {role} ({ats})")

        # Wait for any running single pipeline to finish
        waited = 0
        while pipeline_running and waited < 600:
            await asyncio.sleep(5)
            waited += 5
        if pipeline_running:
            add_event("Auto-Queue", "error", f"Timed out waiting for previous job to finish")
            continue

        # Trigger the single-job pipeline for this URL
        pipeline_events.clear()
        pipeline_running = True
        asyncio.create_task(_run_application(url, company, role))

        # Wait for it to complete (max 10 min per job)
        wait_ticks = 0
        while pipeline_running and wait_ticks < 120:
            await asyncio.sleep(5)
            wait_ticks += 1

        if pipeline_running:
            add_event("Auto-Queue", "warning", f"Job timed out after 10 min: {company}")
            # Force-stop and carry on
            pipeline_running = False

        add_event("Auto-Queue", "success",
            f"Finished [{idx+1}/{len(_auto_queue)}]: {company} — {role}")

        # Pause between jobs (skip delay on last job)
        if idx < len(_auto_queue) - 1 and not _auto_queue_stop:
            add_event("Auto-Queue", "info", f"Waiting {delay_seconds}s before next job...")
            await asyncio.sleep(delay_seconds)

    _auto_queue_running = False
    _auto_queue_index = len(_auto_queue)
    total = len(_auto_queue)
    add_event("Auto-Queue", "success",
        f"Batch complete. Processed {min(_auto_queue_index, total)}/{total} jobs.")


async def _handle_custom_fields(page, add_event_func):
    """Handle ALL custom dropdowns, radio buttons, checkboxes, and EEO selects on a Greenhouse form.

    This is extracted as a standalone function so it can be called from both
    _run_application() (initial pipeline) and /continue endpoint (re-runs).
    """
    global latest_screenshot_b64, screenshot_version
    from applicator.form_filler import _load_personal_info
    info = _load_personal_info()

    # Build a label-to-value mapping for custom dropdowns
    dd_value_map = {
        "state": info.get("state", "California"),
        "resident": info.get("state", "California"),
        "graduation": info.get("graduation_year", "2028"),
        "graduate": info.get("graduation_year", "2028"),
        "intern season": info.get("intern_season", "Summer"),
        "season": info.get("intern_season", "Summer"),
        "sponsorship": info.get("sponsorship_needed", "No"),
        "relocat": info.get("willing_to_relocate", "Yes"),
        "internship": "No",
        "co-op": "No",
        "coop": "No",
        "prior": "No",
        "gender": info.get("gender", "Male"),
        "gender identity": "Man",
        "describe your gender": "Man",
        "race": info.get("race_ethnicity", "Asian"),
        "ethnicity": info.get("race_ethnicity", "Asian"),
        "racial": info.get("race_ethnicity", "Asian"),
        "ethnic": info.get("race_ethnicity", "Asian"),
        "identify your race": info.get("race_ethnicity", "Asian"),
        "hispanic": "No",
        "latino": "No",
        "sexual orientation": "Decline to self-identify",
        "transgender": "No",
        "identify as transgender": "No",
        "veteran": info.get("veteran_status", "I am not a protected veteran"),
        "armed forces": info.get("veteran_status", "I am not a protected veteran"),
        "disability": info.get("disability_status", "I do not wish to answer"),
        "chronic condition": info.get("disability_status", "I do not wish to answer"),
        "hear": info.get("how_did_you_hear", "LinkedIn"),
        "education": info.get("degree", "Bachelor's"),
        "country": info.get("country", "United States"),
        # Education fields
        "confirm your school": info.get("school", "Santa Clara University"),
        "school": info.get("school", "Santa Clara University"),
        "university": info.get("school", "Santa Clara University"),
        "college": info.get("school", "Santa Clara University"),
        "degree": info.get("degree", "Bachelor's Degree"),
        "discipline": info.get("major", "Computer Science"),
        "major": info.get("major", "Computer Science"),
        "field of study": info.get("major", "Computer Science"),
        "area of study": info.get("major", "Computer Science"),
        # Date fields
        "start date month": "September",
        "end date month": "June",
    }

    # Mapping for radio buttons
    radio_value_map = {
        "authorized": "Yes",
        "work in the united states": "Yes",
        "legally authorized": "Yes",
        "on-site": "Yes",
        "onsite": "Yes",
        "18": "Yes",
        "over 18": "Yes",
        "felony": "No",
        "convicted": "No",
        "criminal": "No",
    }

    try:
        # --- PHASE A: Find ALL unfilled custom dropdowns ---
        unfilled_dropdowns = await page.evaluate("""() => {
            const results = [];
            const containers = document.querySelectorAll('[class*="select__container"]');
            for (let i = 0; i < containers.length; i++) {
                const container = containers[i];
                const style = window.getComputedStyle(container);
                if (style.display === 'none' || style.visibility === 'hidden') continue;

                const singleVal = container.querySelector('[class*="single-value"], [class*="singleValue"]');
                const multiVals = container.querySelectorAll('[class*="multi-value"], [class*="multiValue"]');
                const placeholder = container.querySelector('[class*="placeholder"]');
                const displayText = singleVal ? singleVal.innerText.trim() : (placeholder ? placeholder.innerText.trim() : '');
                const isUnfilled = (!singleVal && multiVals.length === 0) || displayText.toLowerCase().startsWith('select');

                if (!isUnfilled) continue;

                const fieldContainer = container.closest('li, .field, .form-group, .question, .select-field, [class*="application-question"]') || container.parentElement;
                const lbl = fieldContainer ? fieldContainer.querySelector('label') : null;
                const lblText = lbl ? lbl.innerText.trim() : '';
                const isRequired = lblText.includes('*');

                results.push({
                    index: i,
                    label: lblText.replace('*', '').trim(),
                    displayText: displayText,
                    isRequired: isRequired,
                });
            }
            return results;
        }""")

        if unfilled_dropdowns:
            add_event_func("Custom DD", "info", f"Found {len(unfilled_dropdowns)} unfilled custom dropdown(s)")

        for dd_info in (unfilled_dropdowns or []):
            dd_label = dd_info.get("label", "")
            dd_idx = dd_info.get("index", 0)
            dd_label_lower = dd_label.lower()

            # Determine target value from mapping
            target_value = None
            for keyword, val in dd_value_map.items():
                if keyword in dd_label_lower:
                    target_value = val
                    break

            pick_first = "onsite location" in dd_label_lower or "which.*location" in dd_label_lower

            # For demographic/EEO fields with no mapping, prefer "Decline" or "I do not wish" over first option
            is_demographic = any(kw in dd_label_lower for kw in [
                "gender", "race", "ethnic", "racial", "sexual orientation",
                "transgender", "disability", "chronic condition", "veteran",
                "armed forces", "mark all that apply"
            ])

            if not target_value and not pick_first:
                if is_demographic:
                    add_event_func("Custom DD", "info", f"No mapping for demographic dropdown: '{dd_label[:60]}' — will try 'Decline to self-identify'")
                    target_value = "Decline to self-identify"
                else:
                    add_event_func("Custom DD", "warning", f"No mapping for dropdown: '{dd_label[:60]}' — will try first option")
                    pick_first = True

            add_event_func("Custom DD", "info", f"Fixing dropdown #{dd_idx}: '{dd_label[:50]}' → target='{target_value or 'first option'}'")

            try:
                all_containers = page.locator('[class*="select__container"]')
                container_loc = all_containers.nth(dd_idx)
                await container_loc.scroll_into_view_if_needed(timeout=3000)
                await asyncio.sleep(0.3)

                async def verify_dd_filled(idx):
                    return await page.evaluate(f"""() => {{
                        const containers = document.querySelectorAll('[class*="select__container"]');
                        const c = containers[{idx}];
                        if (!c) return null;
                        // Check single-value (regular select)
                        const sv = c.querySelector('[class*="single-value"], [class*="singleValue"]');
                        if (sv) return sv.innerText.trim();
                        // Check multi-value (multi-select) — return comma-joined selected values
                        const mvs = c.querySelectorAll('[class*="multi-value"], [class*="multiValue"]');
                        if (mvs.length > 0) return Array.from(mvs).map(v => v.innerText.trim()).join(', ');
                        return null;
                    }}""")

                async def open_dd(container_locator):
                    await container_locator.click(timeout=3000)
                    await asyncio.sleep(0.8)
                    menu_open = await page.locator('[class*="select__menu"]').count()
                    if menu_open == 0:
                        try:
                            indicator = container_locator.locator('[class*="indicator"], [class*="IndicatorsContainer"] div').first
                            await indicator.click(timeout=1000)
                            await asyncio.sleep(0.5)
                        except Exception:
                            pass

                await open_dd(container_loc)

                picked = False

                if target_value:
                    # Strategy 1: Direct click on option with text match
                    for selector in ['[class*="select__option"]', '[role="option"]']:
                        try:
                            option_loc = page.locator(f'{selector}:has-text("{target_value}")').first
                            await option_loc.click(timeout=2000)
                            await asyncio.sleep(0.5)
                            filled_val = await verify_dd_filled(dd_idx)
                            if filled_val and filled_val.lower() != 'select...':
                                add_event_func("Custom DD", "success", f"Selected '{filled_val[:40]}' for '{dd_label[:40]}'")
                                picked = True
                                break
                            else:
                                add_event_func("Custom DD", "info", f"Click seemed to work but value shows '{filled_val}', retrying...")
                        except Exception:
                            continue

                    # Strategy 2: Type to filter + click best matching option
                    # This is the CRITICAL strategy for large searchable dropdowns (School, etc.)
                    if not picked:
                        type_keywords = {
                            "I am not a protected veteran": "not a protected",
                            "I do not wish to answer": "do not wish",
                            "No, I do not have a disability": "do not have",
                            "Decline to self-identify": "Decline",
                            "Decline to self identify": "Decline",
                            "California": "Calif",
                            "United States": "United",
                            "Bachelor's": "Bachelor",
                            "Bachelor's Degree": "Bachelor",
                            "Santa Clara University": "Santa Clara Uni",
                            "Computer Science and Engineering": "Computer Science",
                            "Computer Science": "Computer Sci",
                            "Asian": "Asian",
                            "September": "Sep",
                            "June": "Jun",
                            "Male": "Male",
                            "Man": "Man",
                            "No": "No",
                            "Yes": "Yes",
                            "LinkedIn": "LinkedIn",
                        }
                        type_text = type_keywords.get(target_value, target_value[:15])
                        add_event_func("Custom DD", "info", f"Trying type-to-filter: '{type_text}'...")

                        # Close any stale menu first, then re-open fresh
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.3)
                        await open_dd(container_loc)

                        # Type directly using keyboard (more reliable than input.fill/type)
                        # The React Select listens for keyboard input when focused
                        await page.keyboard.type(type_text, delay=80)
                        # Wait longer for large lists (schools) to filter via API/search
                        await asyncio.sleep(1.5)

                        try:
                            opts = page.locator('[class*="select__option"]')
                            count = await opts.count()
                            add_event_func("Custom DD", "info", f"After typing '{type_text}': {count} option(s) visible")

                            # Find best matching option (prefer exact match over partial)
                            best_idx = -1
                            best_text = ""
                            for oi in range(min(count, 10)):
                                try:
                                    opt_text = await opts.nth(oi).inner_text(timeout=1000)
                                except Exception:
                                    continue
                                if "no options" in opt_text.lower() or "loading" in opt_text.lower():
                                    continue
                                # Exact match
                                if opt_text.strip().lower() == target_value.lower():
                                    best_idx = oi
                                    best_text = opt_text.strip()
                                    break
                                # Partial match (target in option or option in target)
                                if best_idx < 0 and (
                                    target_value.lower() in opt_text.lower() or
                                    opt_text.strip().lower() in target_value.lower()
                                ):
                                    best_idx = oi
                                    best_text = opt_text.strip()
                                # First valid option as fallback
                                if best_idx < 0:
                                    best_idx = oi
                                    best_text = opt_text.strip()

                            if best_idx >= 0:
                                add_event_func("Custom DD", "info", f"Clicking option: '{best_text[:40]}'")
                                await opts.nth(best_idx).click(timeout=2000)
                                await asyncio.sleep(0.5)
                                filled_val = await verify_dd_filled(dd_idx)
                                if filled_val and filled_val.lower() != 'select...':
                                    add_event_func("Custom DD", "success",
                                        f"Type+click selected '{filled_val[:40]}' for '{dd_label[:40]}'")
                                    picked = True
                                else:
                                    add_event_func("Custom DD", "info", f"Clicked but verify shows '{filled_val}', not picked")
                        except Exception as e2:
                            add_event_func("Custom DD", "info", f"Type-filter click error: {str(e2)[:60]}")

                        if not picked:
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.3)

                    # Strategy 3: Use JS to simulate React Select's onChange
                    if not picked:
                        add_event_func("Custom DD", "info", f"Trying JS-based selection for '{dd_label[:40]}'...")
                        try:
                            js_picked = await page.evaluate(f"""(targetVal) => {{
                                const containers = document.querySelectorAll('[class*="select__container"]');
                                const container = containers[{dd_idx}];
                                if (!container) return false;

                                container.querySelector('[class*="control"]')?.click();

                                return new Promise(resolve => {{
                                    setTimeout(() => {{
                                        const options = document.querySelectorAll('[class*="select__option"]');
                                        for (const opt of options) {{
                                            const text = opt.innerText.trim();
                                            if (text.toLowerCase().includes(targetVal.toLowerCase()) ||
                                                targetVal.toLowerCase().includes(text.toLowerCase())) {{
                                                opt.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true}}));
                                                opt.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true}}));
                                                opt.click();
                                                resolve(true);
                                                return;
                                            }}
                                        }}
                                        resolve(false);
                                    }}, 500);
                                }});
                            }}""", target_value)

                            if js_picked:
                                await asyncio.sleep(0.5)
                                filled_val = await verify_dd_filled(dd_idx)
                                if filled_val and filled_val.lower() != 'select...':
                                    add_event_func("Custom DD", "success",
                                        f"JS selected '{filled_val[:40]}' for '{dd_label[:40]}'")
                                    picked = True
                        except Exception as js_err:
                            add_event_func("Custom DD", "info", f"JS selection failed: {str(js_err)[:60]}")

                # Strategy 4: For demographic fields, try multiple decline-type phrases
                if not picked and is_demographic:
                    decline_phrases = [
                        "Decline", "decline", "Prefer not", "prefer not",
                        "do not wish", "Don't wish", "not to disclose",
                        "choose not", "rather not",
                    ]
                    for phrase in decline_phrases:
                        if picked:
                            break
                        try:
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.3)
                            await open_dd(container_loc)
                            await page.keyboard.type(phrase, delay=80)
                            await asyncio.sleep(1.0)
                            opts = page.locator('[class*="select__option"]')
                            count = await opts.count()
                            if count > 0:
                                opt_text = await opts.first.inner_text(timeout=1000)
                                if "no options" not in opt_text.lower() and "loading" not in opt_text.lower():
                                    await opts.first.click(timeout=2000)
                                    await asyncio.sleep(0.5)
                                    filled_val = await verify_dd_filled(dd_idx)
                                    if filled_val and filled_val.lower() != 'select...':
                                        add_event_func("Custom DD", "success",
                                            f"Decline phrase '{phrase}' selected '{filled_val[:40]}' for '{dd_label[:40]}'")
                                        picked = True
                        except Exception:
                            continue

                if pick_first and not picked:
                    try:
                        menu_count = await page.locator('[class*="select__menu"]').count()
                        if menu_count == 0:
                            await open_dd(container_loc)
                        first_opt = page.locator('[class*="select__option"]').first
                        opt_text = await first_opt.inner_text(timeout=2000)
                        await first_opt.click(timeout=2000)
                        await asyncio.sleep(0.5)
                        add_event_func("Custom DD", "success", f"Picked first option '{opt_text[:30]}' for '{dd_label[:40]}'")
                        picked = True
                    except Exception:
                        add_event_func("Custom DD", "warning", f"Could not pick option for '{dd_label[:40]}'")

                if not picked:
                    await page.keyboard.press("Escape")

                await asyncio.sleep(0.3)
            except Exception as e:
                add_event_func("Custom DD", "warning", f"Error fixing '{dd_label[:40]}': {str(e)[:80]}")

        # --- PHASE A2: Fix phone country code dropdown ---
        try:
            from applicator.form_filler import _handle_phone_country
            async def _phone_evt(s, st, d=""):
                add_event_func(s, st, d)
            phone_fixed = await _handle_phone_country(page, "United States", _phone_evt)
            if phone_fixed:
                add_event_func("Phone CC", "success", "Phone country code set to US (+1)")
            else:
                phone_dd = await page.evaluate("""() => {
                    const telInputs = document.querySelectorAll('input[type="tel"]');
                    for (const tel of telInputs) {
                        let parent = tel.parentElement;
                        for (let i = 0; i < 5 && parent; i++) {
                            const dd = parent.querySelector('[class*="select__container"]');
                            if (dd) {
                                const sv = dd.querySelector('[class*="single-value"], [class*="singleValue"]');
                                const text = sv ? sv.innerText.trim() : '';
                                if (text.includes('United States') || text.includes('+1') || text.includes('\U0001f1fa\U0001f1f8')) {
                                    return null;
                                }
                                const allContainers = document.querySelectorAll('[class*="select__container"]');
                                for (let j = 0; j < allContainers.length; j++) {
                                    if (allContainers[j] === dd) return {index: j, currentText: text};
                                }
                            }
                            parent = parent.parentElement;
                        }
                    }
                    return null;
                }""")
                if phone_dd:
                    ph_idx = phone_dd["index"]
                    add_event_func("Phone CC", "info", f"Found phone country dropdown at index {ph_idx}")
                    try:
                        ph_container = page.locator('[class*="select__container"]').nth(ph_idx)
                        await ph_container.scroll_into_view_if_needed(timeout=3000)
                        await ph_container.click(timeout=3000)
                        await asyncio.sleep(0.8)
                        await page.keyboard.type("United States", delay=40)
                        await asyncio.sleep(0.8)
                        us_opt = page.locator('[class*="select__option"]:has-text("United States")').first
                        await us_opt.click(timeout=2000)
                        add_event_func("Phone CC", "success", "Selected United States for phone country")
                    except Exception as phe:
                        await page.keyboard.press("Escape")
                        add_event_func("Phone CC", "warning", f"Phone country select error: {str(phe)[:60]}")
        except Exception as e:
            add_event_func("Phone CC", "info", f"Phone country check: {str(e)[:60]}")

        # --- PHASE B: Handle unfilled RADIO BUTTONS ---
        unfilled_radios = await page.evaluate("""() => {
            const results = [];
            const radioInputs = document.querySelectorAll('input[type="radio"]');
            const groups = {};
            for (const r of radioInputs) {
                const name = r.name || r.id || 'unknown';
                if (!groups[name]) groups[name] = [];
                groups[name].push(r);
            }
            for (const [name, radios] of Object.entries(groups)) {
                const anyChecked = radios.some(r => r.checked);
                if (anyChecked) continue;

                const firstRadio = radios[0];
                let questionText = '';
                let ancestor = firstRadio;
                for (let i = 0; i < 10 && ancestor; i++) {
                    ancestor = ancestor.parentElement;
                    if (!ancestor || ancestor === document.body) break;
                    const lbl = ancestor.querySelector('label, legend, .field-label, h3, h4');
                    if (lbl) {
                        const t = lbl.innerText.trim();
                        if (t.length > 10 && t !== 'Yes' && t !== 'No') {
                            questionText = t.substring(0, 200);
                            break;
                        }
                    }
                }

                const options = radios.map(r => {
                    const wrapper = r.closest('li, div, label');
                    const optText = wrapper ? wrapper.innerText.trim() : (r.value || '');
                    return {
                        value: r.value,
                        text: optText,
                        selector: r.id ? '#' + r.id : (r.name ? '[name="' + r.name + '"][value="' + r.value + '"]' : ''),
                    };
                });

                results.push({
                    name: name,
                    question: questionText,
                    options: options,
                    isRequired: questionText.includes('*'),
                });
            }
            return results;
        }""")

        if unfilled_radios:
            add_event_func("Radio/Check", "info", f"Found {len(unfilled_radios)} unfilled radio group(s)")

        for radio_group in (unfilled_radios or []):
            question = radio_group.get("question", "")
            options = radio_group.get("options", [])
            question_lower = question.lower()

            target_answer = None
            for keyword, val in radio_value_map.items():
                if keyword in question_lower:
                    target_answer = val
                    break

            if not target_answer:
                if radio_group.get("isRequired"):
                    target_answer = "Yes"
                else:
                    continue

            target_selector = None
            for opt in options:
                if opt["text"].strip().lower() == target_answer.lower():
                    target_selector = opt["selector"]
                    break
            if not target_selector:
                for opt in options:
                    if target_answer.lower() in opt["text"].strip().lower():
                        target_selector = opt["selector"]
                        break

            if target_selector:
                try:
                    loc = page.locator(target_selector).first
                    await loc.scroll_into_view_if_needed(timeout=3000)
                    await loc.click(timeout=3000)
                    add_event_func("Radio/Check", "success", f"Clicked '{target_answer}' for '{question[:50]}'")
                except Exception as e:
                    add_event_func("Radio/Check", "warning", f"Failed to click radio for '{question[:40]}': {str(e)[:60]}")
            else:
                add_event_func("Radio/Check", "warning", f"No matching option for '{question[:40]}' → '{target_answer}'")

        # --- PHASE C: Handle unfilled CHECKBOXES ---
        unfilled_checkboxes = await page.evaluate("""() => {
            const results = [];
            const checkboxes = document.querySelectorAll('input[type="checkbox"]');
            for (const cb of checkboxes) {
                if (cb.checked) continue;
                if (cb.offsetParent === null) continue;
                if ((cb.name || '').includes('cookie') || (cb.id || '').includes('ot-')) continue;

                const wrapper = cb.closest('li, div, label, .field, .form-group');
                const text = wrapper ? wrapper.innerText.trim() : '';
                const isRequired = text.includes('*') || cb.required;

                const textLower = text.toLowerCase();
                const shouldCheck = isRequired || textLower.includes('understand') || textLower.includes('agree')
                    || textLower.includes('acknowledge') || textLower.includes('on-site')
                    || textLower.includes('onsite') || textLower.includes('authorize')
                    || textLower.includes('certif');

                if (!shouldCheck) continue;

                results.push({
                    selector: cb.id ? '#' + cb.id : (cb.name ? '[name="' + cb.name + '"]' : ''),
                    text: text.substring(0, 150),
                });
            }
            return results;
        }""")

        if unfilled_checkboxes:
            add_event_func("Radio/Check", "info", f"Found {len(unfilled_checkboxes)} unchecked checkbox(es) to check")

        for cb_info in (unfilled_checkboxes or []):
            selector = cb_info.get("selector", "")
            text = cb_info.get("text", "")
            if not selector:
                continue
            try:
                loc = page.locator(selector).first
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=3000)
                add_event_func("Radio/Check", "success", f"Checked: '{text[:50]}'")
            except Exception as e:
                add_event_func("Radio/Check", "warning", f"Failed to check: {str(e)[:60]}")

        # --- PHASE D: Handle Voluntary Self-Identification (EEO) native <select> elements ---
        eeo_selects = await page.evaluate("""() => {
            const results = [];
            const selects = document.querySelectorAll('select');
            for (const s of selects) {
                if (s.offsetParent === null) continue;
                const section = s.closest('fieldset, .voluntary-self-id, [class*="demographic"], [class*="eeo"], [class*="voluntary"]');
                const label = s.closest('li, .field, .form-group')?.querySelector('label, .field-label')?.innerText?.trim() || '';
                const currentText = s.options[s.selectedIndex]?.text?.trim() || '';
                const isPlaceholder = ['select...', 'select', 'choose', '--', ''].includes(currentText.toLowerCase());

                if (!isPlaceholder) continue;

                const opts = Array.from(s.options).map(o => ({v: o.value, t: o.text.trim(), i: o.index}));
                results.push({
                    selector: s.id ? '#' + s.id : (s.name ? 'select[name="' + s.name + '"]' : ''),
                    label: label,
                    currentText: currentText,
                    options: opts,
                    inEEO: !!section,
                });
            }
            return results;
        }""")

        if eeo_selects:
            add_event_func("EEO", "info", f"Found {len(eeo_selects)} unfilled select(s)")

        eeo_value_map = {
            "gender": info.get("gender", "Male"),
            "race": info.get("race_ethnicity", "Asian"),
            "ethnicity": info.get("race_ethnicity", "Asian"),
            "hispanic": "No",
            "veteran": info.get("veteran_status", "I am not a protected veteran"),
            "disability": info.get("disability_status", "I do not wish to answer"),
            "state": info.get("state", "California"),
            "resident": info.get("state", "California"),
        }

        for sel_info in (eeo_selects or []):
            selector = sel_info.get("selector", "")
            label = sel_info.get("label", "")
            label_lower = label.lower()

            target_value = None
            for keyword, val in eeo_value_map.items():
                if keyword in label_lower:
                    target_value = val
                    break

            if not target_value:
                continue

            target_idx = None
            for opt in sel_info.get("options", []):
                if opt["t"].lower().strip() == target_value.lower().strip():
                    target_idx = opt["i"]
                    break
                if target_value.lower() in opt["t"].lower():
                    target_idx = opt["i"]

            if target_idx is None:
                add_event_func("EEO", "warning", f"No option matching '{target_value}' for '{label[:40]}'")
                continue

            try:
                loc = page.locator(selector).first
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.select_option(index=target_idx, timeout=3000)
                await page.evaluate(f"""() => {{
                    const el = document.querySelector('{selector}');
                    if (el) {{
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}""")
                add_event_func("EEO", "success", f"Selected '{target_value}' for '{label[:40]}'")
            except Exception as e:
                add_event_func("EEO", "warning", f"Failed to select for '{label[:40]}': {str(e)[:60]}")

        # --- PHASE F: Clean up resume wrongly uploaded to cover letter fields ---
        # The browser-use agent sometimes uploads resume to ALL file inputs including cover letter.
        # We detect non-resume upload fields that have a file attached and remove it.
        try:
            cover_letter_uploads = await page.evaluate("""() => {
                const results = [];
                // Find all upload field containers on Greenhouse forms
                const fields = document.querySelectorAll('.field, .application-field, .form-group, li[class*="field"]');
                for (const field of fields) {
                    if (field.offsetParent === null) continue;
                    // Get the label
                    const lbl = field.querySelector('label, .field-label, h3, h4');
                    if (!lbl) continue;
                    const labelText = lbl.innerText.trim().toLowerCase();
                    // Skip if this IS a resume/CV field
                    if (labelText.match(/resume|\\bcv\\b/)) continue;
                    // Check if this is a cover letter or other non-resume upload field
                    const isUploadField = labelText.match(/cover.?letter|additional|supplement|other|portfolio|writing.?sample|transcript/);
                    if (!isUploadField) continue;
                    // Check if a file has been uploaded here
                    // Greenhouse shows filename after upload, or has attachment elements
                    const hasFile = field.querySelector('.filename, .file-name, .attachment-filename, [class*="attachment"], [class*="upload-success"], [class*="uploaded"]');
                    const hasRemoveBtn = field.querySelector('button[aria-label*="Remove"], button[aria-label*="remove"], button[aria-label*="Delete"], button[aria-label*="delete"], a[aria-label*="Remove"], .remove-file, [class*="remove"], button:has(svg), button.close, [data-automation-id*="delete"]');
                    // Also check if there's a displayed filename text (Greenhouse pattern)
                    const filenameEls = field.querySelectorAll('span, div, a');
                    let hasFilename = false;
                    for (const el of filenameEls) {
                        if (el.offsetParent === null) continue;
                        const txt = el.innerText.trim().toLowerCase();
                        if (txt.match(/\\.(pdf|doc|docx|txt|rtf)$/)) { hasFilename = true; break; }
                    }
                    if (hasFile || hasFilename || hasRemoveBtn) {
                        // Find the remove/X button
                        const removeBtn = field.querySelector('button[aria-label*="Remove"], button[aria-label*="remove"], button[aria-label*="Delete"], button[aria-label*="delete"], a[aria-label*="Remove"], .remove-file, [class*="remove"], button.close, [data-automation-id*="delete"]');
                        results.push({
                            label: lbl.innerText.trim().substring(0, 80),
                            hasRemoveBtn: !!removeBtn,
                        });
                    }
                }
                return results;
            }""")

            for cl_info in cover_letter_uploads:
                cl_label = cl_info.get("label", "")
                add_event_func("CoverLetter", "info", f"Found file in non-resume field: '{cl_label[:50]}' — attempting removal")

                # Try to click the remove button within that field
                try:
                    # Find the field container by label text
                    field_loc = page.locator(f'.field:has(label:has-text("{cl_label[:30]}")), .application-field:has(label:has-text("{cl_label[:30]}")), li:has(label:has-text("{cl_label[:30]}"))').first
                    # Look for remove/X button
                    remove_selectors = [
                        'button[aria-label*="Remove"]', 'button[aria-label*="remove"]',
                        'button[aria-label*="Delete"]', 'button[aria-label*="delete"]',
                        'a[aria-label*="Remove"]', '.remove-file', '[class*="remove"]',
                        'button.close', '[data-automation-id*="delete"]',
                    ]
                    removed = False
                    for sel in remove_selectors:
                        try:
                            btn = field_loc.locator(sel).first
                            if await btn.is_visible(timeout=1000):
                                await btn.click(timeout=2000)
                                await asyncio.sleep(0.5)
                                add_event_func("CoverLetter", "success", f"Removed file from '{cl_label[:50]}'")
                                removed = True
                                break
                        except Exception:
                            continue

                    if not removed:
                        # Try clearing the file input directly
                        try:
                            file_input = field_loc.locator('input[type="file"]').first
                            if await file_input.count() > 0:
                                await file_input.evaluate("el => { el.value = ''; el.dispatchEvent(new Event('change', {bubbles: true})); }")
                                add_event_func("CoverLetter", "success", f"Cleared file input in '{cl_label[:50]}'")
                                removed = True
                        except Exception:
                            pass

                    if not removed:
                        add_event_func("CoverLetter", "warning", f"Could not remove file from '{cl_label[:50]}' — may need manual removal")

                except Exception as e_cl:
                    add_event_func("CoverLetter", "warning", f"Error cleaning '{cl_label[:40]}': {str(e_cl)[:60]}")

            if not cover_letter_uploads:
                add_event_func("CoverLetter", "info", "No wrongly-uploaded files found in non-resume fields")
        except Exception as e_clf:
            add_event_func("CoverLetter", "info", f"Cover letter cleanup check skipped: {str(e_clf)[:60]}")

        # --- PHASE E: Log all remaining unfilled REQUIRED fields ---
        unfilled_required = await page.evaluate("""() => {
            const results = [];
            const inputs = document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea, select');
            for (const el of inputs) {
                if (el.offsetParent === null) continue;
                const isRequired = el.required || el.getAttribute('aria-required') === 'true';
                if (!isRequired) continue;
                if (el.closest('[class*="select__container"]')) continue;
                if ((el.id || '').includes('security') || (el.name || '').includes('security')) continue;

                let isEmpty = false;
                if (el.tagName === 'SELECT') {
                    const currentText = el.options[el.selectedIndex]?.text?.trim()?.toLowerCase() || '';
                    isEmpty = ['select...', 'select', '', '--'].includes(currentText);
                } else if (el.type === 'radio') {
                    const name = el.name;
                    if (name) {
                        const group = document.querySelectorAll('input[name="' + name + '"]');
                        isEmpty = !Array.from(group).some(r => r.checked);
                    }
                } else if (el.type === 'checkbox') {
                    isEmpty = !el.checked;
                } else {
                    isEmpty = !el.value.trim();
                }

                if (!isEmpty) continue;

                let label = '';
                if (el.id) {
                    const lbl = document.querySelector('label[for="' + el.id + '"]');
                    if (lbl) label = lbl.innerText.trim();
                }
                if (!label) {
                    const parent = el.closest('li, .field, .form-group');
                    const lbl = parent?.querySelector('label');
                    if (lbl) label = lbl.innerText.trim();
                }

                results.push({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    name: el.name || el.id || '',
                    label: label.substring(0, 100),
                });
            }

            // Also check custom dropdowns still showing Select...
            const customDDs = document.querySelectorAll('[class*="select__container"]');
            for (const dd of customDDs) {
                if (dd.offsetParent === null) continue;
                const singleVal = dd.querySelector('[class*="single-value"], [class*="singleValue"]');
                if (singleVal) continue;
                const fieldContainer = dd.closest('li, .field, .form-group') || dd.parentElement;
                const lbl = fieldContainer?.querySelector('label');
                const lblText = lbl ? lbl.innerText.trim() : '';
                if (lblText.includes('*')) {
                    results.push({tag: 'div', type: 'custom-dropdown', name: '', label: lblText.substring(0, 100)});
                }
            }

            return results;
        }""")

        if unfilled_required:
            add_event_func("Unfilled", "warning", f"{len(unfilled_required)} required field(s) still empty:")
            for uf in unfilled_required[:10]:
                add_event_func("Unfilled", "warning", f"  - [{uf.get('type','')}] {uf.get('label','') or uf.get('name','unknown')}")
        else:
            add_event_func("Verify", "success", "All required fields appear to be filled!")

        # Final screenshot
        try:
            ss = await page.screenshot(type="png")
            latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
            screenshot_version += 1
        except Exception:
            pass

    except Exception as e:
        import traceback as _tb
        add_event_func("Post-Fill", "warning", f"Post-fill handler error: {str(e)[:100]}\n{_tb.format_exc()[:300]}")


async def _run_application(url: str, company: str, role: str):
    global pipeline_running, latest_screenshot_b64, browser_instance, active_page, active_context
    active_page = None
    active_context = None

    # ── Clean restart: kill old browser refs and orphan processes ──
    if browser_instance:
        try:
            await browser_instance.close()
        except Exception:
            pass
        browser_instance = None
    try:
        from applicator.form_filler import close_browser as cb, close_browser_agent as cba
        await cb()
        await cba()
    except Exception:
        pass
    # Reset form_filler module-level browser refs in case uvicorn --reload made them stale
    try:
        import applicator.form_filler as _ff
        _ff._bu_browser = None
        _ff._pw_for_cdp = None
        _ff._playwright = None
        _ff._browser = None
    except Exception:
        pass
    # Kill orphan Chromium processes left behind by previous server reloads
    try:
        import subprocess as _sp
        _sp.run(["pkill", "-f", "chromium.*--remote-debugging"], timeout=5, capture_output=True)
    except Exception:
        pass
    await asyncio.sleep(1)  # Give OS time to clean up

    resume_path = uploaded_resume or str(Path(__file__).parent.parent / "uploads" / "EdrickChang_Resume.pdf")

    try:
        # Step 1: Extract JD
        add_event("Extract JD", "start", f"Fetching {url[:60]}...")
        jd = "Software engineering internship"
        try:
            from scraper.job_description import extract_job_description
            jd = extract_job_description(url)
            add_event("Extract JD", "success", f"Got {len(jd)} chars of job description")
        except Exception as e:
            add_event("Extract JD", "error", f"JD extraction failed: {e}, using fallback")

        if pipeline_stop_requested:
            return

        # Steps 2-4: Use browser-use AI agent to fill the application
        from applicator.form_filler import fill_with_browser_agent, close_browser_agent

        async def on_event(step, status, detail=""):
            add_event(step, status, detail)

        async def on_screenshot(screenshot_bytes):
            global latest_screenshot_b64, screenshot_version
            if screenshot_bytes:
                latest_screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                screenshot_version += 1

        result = await fill_with_browser_agent(
            url=url,
            company=company,
            role=role,
            resume_path=resume_path,
            job_description=jd,
            event_callback=on_event,
            screenshot_callback=on_screenshot,
            transcript_path=uploaded_transcript,
        )

        browser_instance = result.get("browser")
        page = result.get("page")
        completed = result.get("completed", False)

        # Save CDP URL for later reconnection (persisted to file)
        try:
            from applicator.form_filler import _bu_browser
            if _bu_browser and hasattr(_bu_browser, 'cdp_url') and _bu_browser.cdp_url:
                _save_cdp_url(_bu_browser.cdp_url)
                print(f">>> Saved CDP URL: {_bu_browser.cdp_url}")
                add_event("Pipeline", "info", f"Saved CDP URL for reconnection: {_bu_browser.cdp_url[:50]}")
        except Exception as e:
            print(f">>> Could not save CDP URL: {e}")

        # Set global page refs for Continue/Email Verify buttons
        # If result didn't include a page, try getting it from _bu_browser directly
        if not page:
            try:
                from applicator.form_filler import _bu_browser
                if _bu_browser:
                    page = await _bu_browser.get_current_page()
                    add_event("Page Recovery", "info", f"Recovered page from browser-use: {page.url[:60] if page else 'None'}")
            except Exception as e:
                add_event("Page Recovery", "warning", f"Could not recover page: {e}")

        active_page = page
        if page:
            try:
                active_context = page.context
            except Exception:
                active_context = None
        print(f">>> _run_application: active_page set to {active_page}")

        # --- Auto Security Code: if agent landed on a verification page ---
        if page and not pipeline_stop_requested:
            try:
                is_verify_page = await page.evaluate("""() => {
                    const t = document.body.innerText.toLowerCase();
                    return ['verify your email','verification code','security code',
                        'check your email','check your inbox','enter the code','enter code',
                        'we sent','sent a code'].some(k => t.includes(k));
                }""")
                if is_verify_page:
                    add_event("Auto Email", "info", "Agent landed on verification page. Auto-fetching code from Gmail...")
                    from applicator.email_handler import auto_handle_security_code
                    code_ok = await auto_handle_security_code(page, company_name=company, event_callback=on_event)
                    if code_ok:
                        add_event("Auto Email", "success", "Security code entered! Waiting for form to load...")
                        await asyncio.sleep(5)
                        # Take screenshot of post-verification page
                        try:
                            ss = await page.screenshot(type="png")
                            latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
                            screenshot_version += 1
                        except Exception:
                            pass
                        # Now re-run the form filler on the actual application form
                        add_event("Auto Email", "info", "Re-running form filler on application form...")
                        result2 = await fill_with_browser_agent(
                            url=page.url,
                            company=company,
                            role=role,
                            resume_path=resume_path,
                            job_description=jd,
                            event_callback=on_event,
                            screenshot_callback=on_screenshot,
                            transcript_path=uploaded_transcript,
                        )
                        completed = result2.get("completed", False)
                        # Update page ref
                        page2 = result2.get("page")
                        if page2:
                            page = page2
                            active_page = page
                            try:
                                active_context = page.context
                            except Exception:
                                pass
                    else:
                        add_event("Auto Email", "warning",
                            "Could not auto-enter code. Click 'Get Email Code' or enter manually, then click Continue.")
            except Exception as e:
                add_event("Auto Email", "warning", f"Auto security code check error: {str(e)[:100]}")

        # --- Recovery: if agent returned incomplete on a Workday auth page ---
        is_workday = "workday" in url.lower() or "myworkdayjobs" in url.lower()
        if not completed and page and is_workday:
            from applicator.form_filler import _detect_workday_page_state, _handle_workday_auth
            state = await _detect_workday_page_state(page)
            if state == "auth":
                add_event("Recovery", "info", "Still on auth page after agent. Running auth handler...")
                auth_ok = await _handle_workday_auth(page, on_event)
                if auth_ok:
                    import asyncio as _aio
                    await _aio.sleep(3)
                    state = await _detect_workday_page_state(page)
                    add_event("Recovery", "info", f"After auth: state={state}")
                if state == "form":
                    add_event("Recovery", "info", "Auth succeeded. Running Workday form handler...")
                    from applicator.workday_handler import handle_workday_application
                    wd_result = await handle_workday_application(
                        page=page, resume_path=resume_path,
                        company=company, role=role,
                        job_description=jd,
                        event_callback=on_event,
                        screenshot_callback=on_screenshot,
                    )
                    wd_filled = wd_result.get("filled", 0)
                    wd_errors = wd_result.get("errors", [])
                    add_event("Recovery", "success" if not wd_errors else "info",
                        f"Workday form: {wd_filled} filled, {wd_result.get('failed', 0)} failed")
                    completed = wd_filled > 0

        # Step 5: Final screenshot for review
        add_event("Screenshot & Review", "start", "Capturing final state...")

        if page:
            try:
                screenshot_bytes = await page.screenshot(type="png")
                latest_screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                screenshot_version += 1
            except Exception:
                pass

        # Save screenshot to disk
        if latest_screenshot_b64:
            try:
                screenshots_dir = Path(__file__).parent.parent / "screenshots"
                screenshots_dir.mkdir(exist_ok=True)
                fname = f"{company}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                (screenshots_dir / fname).write_bytes(base64.b64decode(latest_screenshot_b64))
                add_event("Screenshot & Review", "info", f"Saved screenshot: {fname}")
            except Exception as e:
                add_event("Screenshot & Review", "info", f"Screenshot save failed: {e}")

        summary = result.get("summary", {})
        if not isinstance(summary, dict):
            summary = {"final_result": str(summary)}
        agent_steps = summary.get("steps", 0)
        # completed may already be True from Workday handler above
        if not completed:
            completed = result.get("completed", False)

        # Verify completion: check if the page still has empty required fields
        has_empty_fields = False
        if page:
            try:
                empty_count = await page.evaluate("""() => {
                    const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]),textarea,select'));
                    return inputs.filter(el => el.offsetParent !== null && !el.value && !el.disabled).length;
                }""")
                has_empty_fields = empty_count > 2
                if has_empty_fields and completed:
                    add_event("Verify", "info", f"{empty_count} empty fields remain — click Continue to fill more")
            except Exception:
                pass

        # If fields were filled but some remain, run one more fallback pass
        if page and completed and has_empty_fields:
            add_event("Fallback Filler", "info", "Running extra fill pass for remaining empty fields...")
            try:
                from applicator.form_filler import JS_EXTRACT_FIELDS, map_fields_to_profile, fill_form
                fields = await page.evaluate(JS_EXTRACT_FIELDS)
                if fields:
                    mappings = await asyncio.to_thread(map_fields_to_profile, fields, jd, company, role)
                    mappings = [m for m in mappings if isinstance(m, dict)]
                    async def fallback_evt(s, st, d=""):
                        add_event(s, st, d)
                    fb_result = await fill_form(page, mappings, resume_path, event_callback=fallback_evt, screenshot_page=page)
                    fb_filled = fb_result.get("filled", 0)
                    fb_failed = fb_result.get("failed", 0)
                    add_event("Fallback Filler", "success" if fb_filled > 0 else "warning",
                        f"Deterministic filler: {fb_filled} filled, {fb_failed} failed")
                    try:
                        ss = await page.screenshot(type="png")
                        latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
                        screenshot_version += 1
                    except Exception:
                        pass
            except Exception as e:
                import traceback as _tb
                tb_str = _tb.format_exc()
                add_event("Fallback Filler", "error", f"Fallback filler failed: {e}\n{tb_str[:500]}")

        # Fix native <select> dropdowns that select_option can't change (React-controlled)
        add_event("Fill Form", "info", f"Select fixer: page={page is not None}, completed={completed}")
        if page and completed:
            try:
                # First check how many selects exist on the page
                select_count = await page.evaluate("document.querySelectorAll('select').length")
                add_event("Fill Form", "info", f"Select fixer: found {select_count} native select elements")

                # Look for the state dropdown by finding label containing "state" or "resident"
                state_dropdown_info = await page.evaluate("""() => {
                    const labels = document.querySelectorAll('label');
                    for (const lbl of labels) {
                        const text = (lbl.innerText || '').toLowerCase();
                        if (text.includes('state') && (text.includes('resident') || text.includes('currently'))) {
                            const container = lbl.closest('.field, .form-group, .question, li, .select-field')
                                || lbl.parentElement;
                            if (!container) continue;
                            // Find the interactive dropdown element
                            const selectEl = container.querySelector('select');
                            const reactSelect = container.querySelector('[class*="react-select"], [class*="indicatorContainer"]');
                            const customDD = container.querySelector('[role="combobox"], [role="listbox"], [class*="dropdown"]');
                            const allChildren = container.innerHTML.substring(0, 500);
                            return {
                                labelText: lbl.innerText.trim(),
                                hasNativeSelect: !!selectEl,
                                hasReactSelect: !!reactSelect,
                                hasCustomDD: !!customDD,
                                containerTag: container.tagName,
                                containerClasses: container.className?.substring(0, 200) || '',
                                htmlSnippet: allChildren
                            };
                        }
                    }
                    return null;
                }""")
                if state_dropdown_info:
                    add_event("Fill Form", "info",
                        f"State dropdown: native={state_dropdown_info.get('hasNativeSelect')}, "
                        f"react={state_dropdown_info.get('hasReactSelect')}, "
                        f"custom={state_dropdown_info.get('hasCustomDD')}, "
                        f"classes={state_dropdown_info.get('containerClasses','')[:80]}")
                    add_event("Fill Form", "info", f"State HTML: {state_dropdown_info.get('htmlSnippet','')[:200]}")
                else:
                    add_event("Fill Form", "warning", "Could not find state/resident dropdown label")

                unfilled_selects = await page.evaluate("""() => {
                    const selects = document.querySelectorAll('select');
                    const result = [];
                    for (const s of selects) {
                        // Check ALL selects — even if select_option set the DOM value,
                        // the visual rendering may not match (React doesn't re-render).
                        // We identify selects that SHOULD have a value (more than just placeholder options).
                        const nonPlaceholderOpts = Array.from(s.options).filter(o =>
                            o.value && o.text.trim() && !['select...','select','choose','--',''].includes(o.text.trim().toLowerCase())
                        );
                        if (nonPlaceholderOpts.length > 0) {
                            const label = s.closest('.field, .form-group, .question')
                                ?.querySelector('label, .field-label')?.textContent?.trim() || '';
                            const opts = Array.from(s.options).map(o => ({v: o.value, t: o.text.trim(), i: o.index}));
                            const id = s.id || '';
                            const name = s.name || '';
                            const currentVal = s.value;
                            const currentText = s.options[s.selectedIndex]?.text?.trim() || '';
                            result.push({id, name, label, options: opts,
                                selector: s.id ? '#'+CSS.escape(s.id) : 'select[name=\"'+s.name+'\"]',
                                currentVal, currentText});
                        }
                    }
                    return result;
                }""")
                if unfilled_selects:
                    add_event("Fill Form", "info", f"Found {len(unfilled_selects)} select(s) to verify/fix with keyboard...")
                    from applicator.form_filler import _load_personal_info
                    info = _load_personal_info()
                    # Map field labels to known values
                    select_value_map = {
                        "state": info.get("state", "California"),
                        "resident": info.get("state", "California"),
                        "country": info.get("country", "United States"),
                        "gender": info.get("gender", "Male"),
                        "race": info.get("race_ethnicity", "Asian"),
                        "ethnicity": info.get("race_ethnicity", "Asian"),
                        "veteran": info.get("veteran_status", "I am not a protected veteran"),
                        "disability": info.get("disability_status", "I do not wish to answer"),
                        "hear": info.get("how_did_you_hear", "LinkedIn"),
                        "education": info.get("degree", "Bachelor's"),
                    }
                    for sel_info in unfilled_selects:
                        label_lower = (sel_info.get("label", "") + " " + sel_info.get("name", "") + " " + sel_info.get("id", "")).lower()
                        target_value = None
                        for keyword, val in select_value_map.items():
                            if keyword in label_lower:
                                target_value = val
                                break

                        if not target_value:
                            continue

                        # Skip if the visible selected option text already matches target
                        current_text = sel_info.get("currentText", "").lower().strip()
                        placeholder_texts = ["select...", "select", "choose", "--", "", "choose..."]
                        if current_text and current_text not in placeholder_texts and current_text == target_value.lower().strip():
                            add_event("Fill Form", "info", f"Select already showing '{current_text[:30]}', skipping")
                            continue
                        # Only fix selects that are stuck on placeholder
                        if current_text not in placeholder_texts:
                            add_event("Fill Form", "info", f"Select shows '{current_text[:30]}' (not placeholder), skipping")
                            continue

                        add_event("Fill Form", "info", f"Fixing select: label='{sel_info.get('label','')[:30]}' current='{current_text[:20]}' target='{target_value[:20]}'")

                        # Find the option index for the target value
                        target_idx = None
                        for opt in sel_info.get("options", []):
                            if opt["t"].lower().strip() == target_value.lower().strip():
                                target_idx = opt["i"]
                                break
                            if target_value.lower() in opt["t"].lower():
                                target_idx = opt["i"]
                                # Don't break — keep looking for exact match

                        if target_idx is None:
                            add_event("Fill Form", "info", f"No option matching '{target_value}' for {sel_info.get('label','')[:30]}")
                            continue

                        # Use keyboard navigation: focus select, ArrowDown to target, Tab to confirm
                        # IMPORTANT: Do NOT press Enter — it submits the form!
                        try:
                            css_sel = sel_info["selector"]
                            loc = page.locator(css_sel).first
                            await loc.scroll_into_view_if_needed(timeout=3000)
                            await loc.focus(timeout=3000)
                            await asyncio.sleep(0.2)
                            # First reset to the beginning by pressing Home
                            await page.keyboard.press("Home")
                            await asyncio.sleep(0.1)
                            # ArrowDown directly changes the selected option without opening dropdown
                            for _ in range(target_idx):
                                await page.keyboard.press("ArrowDown")
                                await asyncio.sleep(0.05)
                            await asyncio.sleep(0.3)
                            # Tab away to trigger change event (NOT Enter which submits form!)
                            await page.keyboard.press("Tab")
                            await asyncio.sleep(0.5)

                            # Verify
                            new_val = await page.evaluate(f"document.querySelector('{css_sel}')?.options[document.querySelector('{css_sel}')?.selectedIndex]?.text || ''")
                            if new_val.strip():
                                add_event("Fill Form", "success", f"Fixed select (keyboard): {new_val[:50]}")
                            else:
                                add_event("Fill Form", "warning", f"Keyboard select may not have worked for {sel_info.get('label','')[:30]}")
                        except Exception as e:
                            add_event("Fill Form", "warning", f"Keyboard select error: {str(e)[:80]}")

                    # Take updated screenshot
                    try:
                        ss = await page.screenshot(type="png")
                        latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
                        screenshot_version += 1
                    except Exception:
                        pass
            except Exception as e:
                add_event("Fill Form", "warning", f"Select fixer error: {str(e)[:100]}")

        # === GENERALIZED CUSTOM DROPDOWN, RADIO, CHECKBOX HANDLER ===
        if page and completed:
            await _handle_custom_fields(page, add_event)

        # Report status — completed means fields were filled, user reviews before submit
        if completed:
            add_event("Screenshot & Review", "success",
                f"Form filling done ({agent_steps} agent steps). Review in browser, then submit or click Continue for more.")
        else:
            add_event("Screenshot & Review", "warning",
                "No fields were filled automatically. Click Continue to try again or fill manually.")
            final_result = summary.get("final_result", "")
            error = summary.get("error", "")
            detail = final_result or error or "Form may need manual navigation"
            add_event("Pipeline Incomplete", "info",
                      f"{detail[:200]}. Click Continue to fill remaining fields.")

        # Keep browser open so user can review and manually submit/retry
        add_event("Pipeline Complete", "info", "Browser stays open. Use Continue to analyze page, or Get Email Code for verification.")

        # Start background screenshot loop
        asyncio.create_task(_background_screenshot_loop())

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        add_event("Pipeline Error", "error", f"{e}")
        add_event("Pipeline Error", "info", f"Traceback: {tb[:1500]}")
    finally:
        # Try to preserve page reference even after errors
        if not active_page:
            try:
                from applicator.form_filler import _bu_browser
                if _bu_browser:
                    recovered = await _bu_browser.get_current_page()
                    if recovered:
                        active_page = recovered
                        try:
                            active_context = recovered.context
                        except Exception:
                            pass
                        print(f">>> finally: recovered active_page from _bu_browser: {recovered.url[:60]}")
            except Exception as e:
                print(f">>> finally: could not recover page from _bu_browser: {e}")
            # Also try CDP reconnect
            if not active_page:
                recovered = await _reconnect_via_cdp("Finally")
        print(f">>> finally: active_page={active_page}")
        pipeline_running = False


async def _background_screenshot_loop():
    """Continuous live-view: capture browser screenshots at ~4fps during and after pipeline."""
    global latest_screenshot_b64, screenshot_version
    idle_miss = 0
    while True:
        page = active_page
        if not page:
            try:
                from applicator.form_filler import _bu_browser
                if _bu_browser:
                    page = await _bu_browser.get_current_page()
            except Exception:
                pass
        if not page:
            idle_miss += 1
            # Give up after 10s of no page and pipeline also done
            if idle_miss > 20 and not pipeline_running:
                break
            await asyncio.sleep(0.5)
            continue
        idle_miss = 0
        try:
            ss = await page.screenshot(type="jpeg", quality=70)
            if ss:
                new_b64 = base64.b64encode(ss).decode("utf-8")
                if new_b64 != latest_screenshot_b64:  # only update if changed
                    latest_screenshot_b64 = new_b64
                    screenshot_version += 1
        except Exception:
            await asyncio.sleep(0.5)
            continue
        # 4fps during pipeline, 2fps during review (reduce CPU when idle)
        await asyncio.sleep(0.25 if pipeline_running else 0.5)


if __name__ == "__main__":
    import uvicorn
    _host = "0.0.0.0" if os.getenv("REMOTE", "0") == "1" else "127.0.0.1"
    uvicorn.run(app, host=_host, port=8080, loop="asyncio")

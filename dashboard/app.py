import asyncio
import sys
import json
import os
import base64
import time
from datetime import datetime
from pathlib import Path

# Playwright needs ProactorEventLoop on Windows for subprocess spawning.
# Uvicorn's --reload flag forces SelectorEventLoop which breaks this.
# We ensure the default (ProactorEventLoop) is used.

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
                    <button class="filter-btn" onclick="loadJobs()" style="width:100%; margin-bottom: 6px;">Refresh Jobs</button>
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
        function loadJobs() {
            document.getElementById('jobsBody').innerHTML = '<tr><td colspan="6" class="loading-msg">Loading jobs from GitHub...</td></tr>';
            document.getElementById('jobsCount').textContent = 'Loading...';
            // Load applied URLs first, then jobs
            fetch('/api/applied').then(r => r.json()).then(data => {
                appliedUrls = new Set(data.urls || []);
            }).catch(() => {}).finally(() => {
                fetch('/api/jobs').then(r => r.json()).then(data => {
                    allJobs = data.jobs || [];
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

            if (eventSource) eventSource.close();
            eventSource = new EventSource('/events');
            eventSource.onmessage = function(e) {
                const event = JSON.parse(e.data);
                addEvent(event);
                updatePills(event);
            };

            // Start screenshot SSE stream
            startScreenshotStream();

            fetch('/run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url, company, role})
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
            fetch('/continue', {method: 'POST'}).then(r => r.json()).then(data => {
                btn.disabled = false; btn.textContent = 'Continue';
                if (!screenshotSource) startScreenshotStream();
            }).catch(() => { btn.disabled = false; btn.textContent = 'Continue'; });
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

        // Load jobs and check uploads on page load
        loadJobs();
        checkUploads();

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


@app.get("/api/jobs")
async def get_jobs():
    """Fetch and parse internship listings from SimplifyJobs GitHub repo."""
    try:
        from scraper.github_scraper import fetch_readme, parse_internship_table
        readme = fetch_readme()
        postings = parse_internship_table(readme)
        return JSONResponse({"jobs": postings, "total": len(postings)})
    except Exception as e:
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
    """Return current upload status."""
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
async def events():
    async def event_stream():
        last_id = 0
        while True:
            if last_id < len(pipeline_events):
                for event in pipeline_events[last_id:]:
                    yield f"data: {json.dumps(event)}\n\n"
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
            if idle_ticks > 33:
                idle_ticks = 0
                yield f"data: {json.dumps({'keepalive': True, 'done': not pipeline_running, 'v': screenshot_version})}\n\n"

            # Only close if browser is gone AND pipeline done AND we already sent done
            if not pipeline_running and browser_instance is None and sent_done:
                yield f"data: {json.dumps({'closed': True})}\n\n"
                break
            await asyncio.sleep(0.15)
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/continue")
async def continue_application_endpoint():
    """Analyze current page and TAKE ACTION: fill credentials, run Workday handler, fill forms."""
    global pipeline_running, latest_screenshot_b64, screenshot_version

    page = active_page
    if not page:
        # Fallback: try browser-use browser
        try:
            from applicator.form_filler import _bu_browser
            if _bu_browser:
                page = await _bu_browser.get_current_page()
        except Exception:
            pass

    if not page:
        return JSONResponse({"status": "error", "message": "No browser open"})

    # Verify page is alive
    try:
        current_url = page.url
    except Exception:
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
        state = await page.evaluate("""() => {
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
        }""")

        # --- SUCCESS ---
        if state.get("isSuccess"):
            add_event("Continue", "success", "Application submitted successfully!")
            return JSONResponse({"status": "ok", "action": "success"})

        # --- VERIFICATION ---
        if state.get("isVerify"):
            add_event("Continue", "info",
                "Verification page detected. Click 'Get Email Code' button to auto-fetch code from Gmail.")
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

        # --- WORKDAY WIZARD ---
        if state.get("isWorkday") and (state.get("hasProgressBar") or state.get("activeStep")):
            step = state.get("activeStep", "Unknown")
            add_event("Continue", "info", f"Workday wizard step: {step}. Running handler...")

            resume_path = uploaded_resume or ""
            if not resume_path:
                for c in [Path(__file__).parent.parent / "uploads" / "EdrickChang_Resume.pdf",
                           Path(os.path.expanduser("~/Downloads/EdrickChang.pdf"))]:
                    if c.exists():
                        resume_path = str(c.resolve())
                        break

            from applicator.workday_handler import handle_workday_application
            async def on_evt(s, st, d=""):
                add_event(s, st, d)
            async def on_ss(b):
                global latest_screenshot_b64, screenshot_version
                if b:
                    latest_screenshot_b64 = base64.b64encode(b).decode("utf-8")
                    screenshot_version += 1

            result = await handle_workday_application(page, resume_path, "", "", "", on_evt, on_ss)
            add_event("Continue", "success" if not result.get("errors") else "info",
                f"Workday: {result.get('filled',0)} filled, {result.get('failed',0)} failed. Click Continue if more steps remain.")
            return JSONResponse({"status": "ok", "action": "workday"})

        # --- REGULAR FORM ---
        if state.get("visibleFields", 0) > 3:
            add_event("Continue", "info", f"Form with {state['visibleFields']} fields. Filling...")

            resume_path = uploaded_resume or ""
            if not resume_path:
                for c in [Path(__file__).parent.parent / "uploads" / "EdrickChang_Resume.pdf",
                           Path(os.path.expanduser("~/Downloads/EdrickChang.pdf"))]:
                    if c.exists():
                        resume_path = str(c.resolve())
                        break

            from applicator.form_filler import JS_EXTRACT_FIELDS, map_fields_to_profile, fill_form
            fields = await page.evaluate(JS_EXTRACT_FIELDS)
            if fields:
                company = await page.evaluate("document.title") or ""
                mappings = map_fields_to_profile(fields, "", company, "")
                async def on_evt(s, st, d=""):
                    add_event(s, st, d)
                result = await fill_form(page, mappings, resume_path, event_callback=on_evt, screenshot_page=page)
                add_event("Continue", "info", f"Filled {result.get('filled',0)}, failed {result.get('failed',0)}")
            else:
                add_event("Continue", "info", "No extractable fields on this page.")
            return JSONResponse({"status": "ok", "action": "form"})

        # --- UNKNOWN ---
        add_event("Continue", "info",
            f"{state.get('visibleFields',0)} fields. Buttons: {state.get('buttons',[])}. "
            f"Text: {state.get('bodyText','')[:200]}...")
        return JSONResponse({"status": "ok", "action": "unknown"})

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
    asyncio.create_task(_run_application(
        data.get("url", ""),
        data.get("company", ""),
        data.get("role", ""),
    ))
    return JSONResponse({"status": "started"})


async def _run_application(url: str, company: str, role: str):
    global pipeline_running, latest_screenshot_b64, browser_instance, active_page, active_context
    active_page = None
    active_context = None

    # Close any previous browser before starting a new one
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

    resume_path = uploaded_resume or r"C:\Users\Owner\Downloads\EdrickChang.pdf"

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

        # Set global page refs for Continue/Email Verify buttons
        active_page = page
        if page:
            try:
                active_context = page.context
            except Exception:
                active_context = None

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
        # completed may already be True from Workday handler above
        if not completed:
            completed = result.get("completed", False)

        if completed:
            add_event("Screenshot & Review", "success", "Ready for review.")
            add_event("Pipeline Complete", "success",
                      f"Agent completed in {summary.get('steps', 0)} steps. "
                      f"Auto-marked as applied.")

            # Only auto-mark as applied if the agent actually completed
            from database.tracker import mark_applied
            mark_applied(url, company, role)
        else:
            add_event("Screenshot & Review", "warning", "Agent did NOT complete the application.")
            final_result = summary.get("final_result", "")
            error = summary.get("error", "")
            detail = final_result or error or "Agent stopped without completing the form"
            add_event("Pipeline Incomplete", "error",
                      f"NOT marked as applied. {detail[:200]}")

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
        pipeline_running = False


async def _background_screenshot_loop():
    """Keep taking screenshots while browser is alive after pipeline finishes."""
    global latest_screenshot_b64, screenshot_version
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
            await asyncio.sleep(2)
            if not active_page:
                break
            continue
        try:
            ss = await page.screenshot(type="png")
            if ss:
                latest_screenshot_b64 = base64.b64encode(ss).decode("utf-8")
                screenshot_version += 1
        except Exception:
            break
        await asyncio.sleep(1)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080, loop="asyncio")

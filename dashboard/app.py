import asyncio
import json
import os
import base64
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

from dotenv import load_dotenv
load_dotenv()

# Shared state
pipeline_events: list[dict] = []
pipeline_running = False
pipeline_stop_requested = False
latest_screenshot_b64: str = ""  # base64 encoded PNG of latest browser state
browser_instance = None  # Keep browser ref for screenshots


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
        .status-badge {
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
        }
        .status-idle { background: #222; color: #888; }
        .status-running { background: #1a3a1a; color: #4ade80; animation: pulse 2s infinite; }
        @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.7; } }

        .container { display: flex; height: calc(100vh - 48px); }

        /* Left panel - controls + event log */
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
            background: #1a1a1a;
            border: 1px solid #333;
            color: #e0e0e0;
            padding: 7px 10px;
            border-radius: 5px;
            font-size: 13px;
            width: 100%;
            margin-bottom: 6px;
        }
        .controls label {
            font-size: 11px;
            color: #555;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: block;
            margin-bottom: 2px;
        }
        .controls button {
            background: #2563eb;
            color: #fff;
            border: none;
            padding: 9px 14px;
            border-radius: 5px;
            font-size: 13px;
            cursor: pointer;
            width: 100%;
            font-weight: 500;
            margin-top: 4px;
        }
        .controls button:hover { background: #1d4ed8; }
        .controls button:disabled { background: #333; cursor: not-allowed; }

        .event-log-container {
            flex: 1;
            overflow-y: auto;
            padding: 8px;
        }
        .event-log { list-style: none; }
        .event-item {
            padding: 6px 8px;
            border-radius: 4px;
            margin-bottom: 2px;
            font-size: 12px;
            display: flex;
            gap: 6px;
            align-items: flex-start;
        }
        .event-time {
            color: #444;
            font-family: monospace;
            flex-shrink: 0;
            font-size: 11px;
        }
        .event-dot {
            width: 6px; height: 6px;
            border-radius: 50%;
            margin-top: 4px;
            flex-shrink: 0;
        }
        .event-start .event-dot { background: #3b82f6; }
        .event-success .event-dot { background: #4ade80; }
        .event-error .event-dot { background: #ef4444; }
        .event-info .event-dot { background: #666; }
        .event-content { flex: 1; min-width: 0; }
        .event-step { font-weight: 500; color: #ddd; font-size: 12px; }
        .event-detail { color: #666; font-size: 11px; word-break: break-word; }

        /* Right panel - live browser view */
        .right-panel {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: #080808;
        }
        .browser-header {
            padding: 8px 16px;
            background: #111;
            border-bottom: 1px solid #222;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .browser-header span { font-size: 13px; color: #888; }
        .browser-dots {
            display: flex; gap: 6px;
        }
        .browser-dots span {
            width: 10px; height: 10px; border-radius: 50%;
        }
        .browser-dots .red { background: #ff5f57; }
        .browser-dots .yellow { background: #febc2e; }
        .browser-dots .green { background: #28c840; }
        .browser-view {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: auto;
            padding: 8px;
        }
        .browser-view img {
            max-width: 100%;
            max-height: 100%;
            border-radius: 4px;
            object-fit: contain;
        }
        .browser-placeholder {
            color: #333;
            font-size: 14px;
            text-align: center;
        }
        .step-pills {
            display: flex;
            gap: 4px;
            padding: 8px 12px;
            background: #0d0d0d;
            border-bottom: 1px solid #222;
            flex-wrap: wrap;
        }
        .step-pill {
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 11px;
            background: #1a1a1a;
            color: #555;
            border: 1px solid #222;
        }
        .step-pill.active { background: #1a3a1a; color: #4ade80; border-color: #2a4a2a; }
        .step-pill.done { background: #1a2a3a; color: #60a5fa; border-color: #2a3a4a; }
        .step-pill.failed { background: #3a1a1a; color: #f87171; border-color: #4a2a2a; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Auto-Apply Dashboard</h1>
        <span class="status-badge status-idle" id="globalStatus">Idle</span>
    </div>
    <div class="container">
        <div class="left-panel">
            <div class="controls">
                <label>Application URL</label>
                <input type="text" id="jobUrl"
                       value="https://jobs.lever.co/aofl/4b91076d-8937-4dbc-a502-a7d6a66e2e19/apply?utm_source=Simplify&ref=Simplify">
                <label>Company</label>
                <input type="text" id="company" value="Age of Learning">
                <label>Role</label>
                <input type="text" id="role" value="Software Engineer Intern">
                <div style="display:flex;gap:6px;">
                    <button id="startBtn" onclick="startApplication()" style="flex:1">Start Application Test</button>
                    <button id="stopBtn" onclick="stopApplication()" style="flex:0 0 70px;background:#dc2626;display:none">Stop</button>
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
                            <div class="event-detail">Click Start to begin</div>
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

    <script>
        let eventSource = null;
        let screenshotInterval = null;

        function startApplication() {
            const url = document.getElementById('jobUrl').value;
            const company = document.getElementById('company').value;
            const role = document.getElementById('role').value;
            if (!url) return;

            document.getElementById('startBtn').disabled = true;
            document.getElementById('startBtn').textContent = 'Running...';
            document.getElementById('stopBtn').style.display = 'block';
            document.getElementById('globalStatus').className = 'status-badge status-running';
            document.getElementById('globalStatus').textContent = 'Running';
            document.getElementById('eventLog').innerHTML = '';
            document.getElementById('browserUrl').textContent = url.substring(0, 60) + '...';

            // Reset pills
            document.querySelectorAll('.step-pill').forEach(p => p.className = 'step-pill');

            // SSE for events
            if (eventSource) eventSource.close();
            eventSource = new EventSource('/events');
            eventSource.onmessage = function(e) {
                const event = JSON.parse(e.data);
                addEvent(event);
                updatePills(event);
            };

            // Poll for screenshots every 2 seconds
            if (screenshotInterval) clearInterval(screenshotInterval);
            screenshotInterval = setInterval(fetchScreenshot, 2000);

            fetch('/run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url, company, role})
            });
        }

        function stopApplication() {
            fetch('/stop', {method: 'POST'});
            document.getElementById('startBtn').disabled = false;
            document.getElementById('startBtn').textContent = 'Start Application Test';
            document.getElementById('stopBtn').style.display = 'none';
            document.getElementById('globalStatus').className = 'status-badge status-idle';
            document.getElementById('globalStatus').textContent = 'Stopped';
            if (screenshotInterval) clearInterval(screenshotInterval);
            addEvent({timestamp: new Date().toLocaleTimeString().slice(0,8), step: 'Stopped', status: 'error', detail: 'Stopped by user'});
        }

        function fetchScreenshot() {
            fetch('/screenshot').then(r => r.json()).then(data => {
                if (data.image) {
                    document.getElementById('browserView').innerHTML =
                        '<img src="data:image/png;base64,' + data.image + '">';
                }
                if (data.done) {
                    clearInterval(screenshotInterval);
                    document.getElementById('startBtn').disabled = false;
                    document.getElementById('startBtn').textContent = 'Start Application Test';
                    document.getElementById('stopBtn').style.display = 'none';
                    document.getElementById('globalStatus').className = 'status-badge status-idle';
                    document.getElementById('globalStatus').textContent = 'Done';
                }
            }).catch(() => {});
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
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return DASHBOARD_HTML


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


@app.post("/stop")
async def stop_pipeline():
    global pipeline_stop_requested, pipeline_running, browser_instance
    pipeline_stop_requested = True
    pipeline_running = False
    add_event("Stopped", "error", "Stopped by user")
    if browser_instance:
        try:
            await browser_instance.close()
        except Exception:
            pass
        browser_instance = None
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


async def _capture_screenshot(agent: "Agent"):
    """Callback for on_step_end - captures screenshot after each agent step.

    Signature must be: async def callback(agent: Agent) -> None
    The agent has a .browser_session attribute with .take_screenshot() method.
    """
    global latest_screenshot_b64
    try:
        if agent.browser_session:
            screenshot_bytes = await agent.browser_session.take_screenshot()
            if screenshot_bytes:
                latest_screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
    except Exception as e:
        # Silently fail - screenshot is best-effort
        pass


async def _run_application(url: str, company: str, role: str):
    global pipeline_running, latest_screenshot_b64, browser_instance
    resume_path = "/Users/edrickchang/Downloads/EdrickChang_Resume.pdf"

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

        # Step 2: Generate answers
        add_event("Generate Answers", "start", "Creating answers for common questions...")
        answers = {}
        try:
            from applicator.field_generator import generate_field_answer
            for q in ["Why do you want to work at this company?", "Why are you interested in this role?"]:
                if pipeline_stop_requested:
                    return
                answer = generate_field_answer(q, company, role, jd)
                answers[q] = answer
                add_event("Generate Answers", "info", f"Done: {q[:40]}...")
            add_event("Generate Answers", "success", f"Generated {len(answers)} answers")
        except Exception as e:
            add_event("Generate Answers", "error", f"Answer generation failed: {e}")
            # Continue anyway - we can still fill the form without pre-generated answers

        if pipeline_stop_requested:
            return

        # Step 3: Launch browser and navigate
        add_event("Navigate", "start", "Launching browser...")
        try:
            from browser_use import Agent, Browser
            from browser_use.llm import ChatCerebras

            # Create browser - headless=False so it opens a visible window
            browser_instance = Browser(headless=False, keep_alive=True)

            # Use browser-use's native ChatCerebras wrapper
            # This handles JSON output by injecting schema into prompt (Cerebras doesn't support response_format)
            cerebras_key = os.getenv("CEREBRAS_API_KEY")
            if not cerebras_key:
                raise ValueError("CEREBRAS_API_KEY not set in .env")

            llm = ChatCerebras(
                model="qwen-3-235b-a22b-instruct-2507",
                api_key=cerebras_key,
            )

            add_event("Navigate", "info", "Browser launched, navigating to application page...")

            # Phase 1: Just navigate to the page and observe the form
            agent = Agent(
                task=f"""Navigate to this URL: {url}

Your ONLY job is to navigate to the page and observe what form fields exist.

Steps:
1. Go to the URL
2. Wait for the page to fully load
3. Scroll down to see all form fields
4. Report what form fields you see (text inputs, file uploads, dropdowns, checkboxes)

CRITICAL RULES:
- Do NOT click "Apply with LinkedIn" button
- Do NOT click any Submit button
- Do NOT fill in any fields yet
- Just observe and report the form structure""",
                llm=llm,
                browser=browser_instance,
                use_vision=False,
                max_actions_per_step=3,
            )

            result = await agent.run(max_steps=10, on_step_end=_capture_screenshot)
            add_event("Navigate", "success", "Application form loaded and observed")

            if pipeline_stop_requested:
                return

            # Step 4: Fill form fields
            add_event("Fill Form", "start", "Filling form fields...")

            answers_text = "\n".join(f'Q: "{q}"\nA: "{a}"' for q, a in answers.items())

            agent2 = Agent(
                task=f"""Fill out this job application form. The form is already visible on the page.

FILL THESE FIELDS IN ORDER:

1. RESUME/CV: Find the file upload field labeled "Resume/CV" and upload this file:
   {resume_path}
   Look for an "ATTACH RESUME/CV" button or file input.

2. FULL NAME: Type "Edrick Chang" in the Full name field.

3. EMAIL: Type "eachang@scu.edu" in the Email field.

4. PHONE: Type "(408) 806-6495" in the Phone field.

5. CURRENT COMPANY: Type "Santa Clara University" if there is a current company field.

6. LINKEDIN: Type "https://linkedin.com/in/edrickchang" in the LinkedIn URL field.

7. GITHUB: Type "https://github.com/edrickchang" in any GitHub field.

8. For any PRONOUNS section: select "He/him"

9. For any text area questions asking why you want to work here or why interested:
{answers_text}

10. For DROPDOWNS about work authorization: Select "Yes" for authorized, "No" for sponsorship needed.

11. For EDUCATION fields: Santa Clara University, BS Computer Science & Engineering, GPA 3.78, June 2028

CRITICAL RULES:
- Do NOT click "Apply with LinkedIn"
- Do NOT click Submit or Send Application
- Fill fields one at a time, verify each one is filled before moving to the next
- If a field is already filled, skip it
- After filling all fields, STOP and report what you filled""",
                llm=llm,
                browser=browser_instance,
                use_vision=False,
                max_actions_per_step=3,
                max_failures=3,
            )

            result2 = await agent2.run(max_steps=30, on_step_end=_capture_screenshot)
            add_event("Fill Form", "success", "Form fields filled")

            if pipeline_stop_requested:
                return

            # Step 5: Final screenshot for review
            add_event("Screenshot & Review", "start", "Capturing final state...")

            # Take one more screenshot
            try:
                if browser_instance:
                    # Create a minimal agent just to take a final screenshot
                    final_agent = Agent(
                        task="Scroll to the top of the page so the full form is visible. Then scroll down slowly to show all filled fields. Do not click anything.",
                        llm=llm,
                        browser=browser_instance,
                        use_vision=False,
                        max_actions_per_step=2,
                    )
                    await final_agent.run(max_steps=3, on_step_end=_capture_screenshot)
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

            add_event("Screenshot & Review", "success", "Ready for your review. Check the browser view.")
            add_event("Pipeline Complete", "success",
                      "Form is filled. Review it in the browser view above, then submit manually in the browser window.")

            # Keep browser open for manual review - don't close it

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            add_event("Navigate", "error", f"Browser agent failed: {e}")
            # Log more traceback for debugging (first 1500 chars has root cause)
            add_event("Navigate", "info", f"Traceback: {tb[:1500]}")

    except Exception as e:
        add_event("Pipeline Error", "error", str(e))
    finally:
        pipeline_running = False


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)

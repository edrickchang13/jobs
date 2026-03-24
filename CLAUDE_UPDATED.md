# Auto-Apply: Internship Application Pipeline

## Project Summary
Automated job application pipeline that scrapes SimplifyJobs GitHub for internship postings, uses browser-use (AI browser agent) to navigate to application pages, then fills forms deterministically via Playwright. Monitored and controlled through a FastAPI web dashboard.

## Current State (March 23, 2026)
- Dashboard running at http://127.0.0.1:8080 with Jobs tab + Apply tab
- Workday multi-step form filling is hardcoded (no LLM) and working
- CDP browser session persistence across server reloads
- Error recovery for Workday "Something went wrong" pages
- CAPTCHA detection with size-based filtering (>=70x65px)
- Auto-submit disabled; manual review required before final submit
- NOT YET TESTED: Lever/Greenhouse deterministic form filling (agent navigation works, form filling needs platform-specific handlers)

## Tech Stack
- Python 3.14 (causes pydantic/langchain compatibility issues - need `object.__setattr__` for setting attributes on LangChain models)
- browser-use 0.12+: AI browser automation (Playwright under the hood)
- Playwright: Direct DOM manipulation via CDP connection after agent navigation
- Cerebras API (free tier, 1M tokens/day): Qwen 3 235B model for browser agent + field mapping
- FastAPI dashboard with SSE screenshot streaming and event log
- SQLite for tracking postings/applications
- Gmail IMAP for automatic email verification code extraction

## Architecture Overview

The system operates in two phases:
1. **AI Agent Phase**: browser-use agent navigates to job URL, clicks Apply, handles modals (max 15 steps)
2. **Deterministic Phase**: Playwright connects via CDP to the same browser, then fills forms using hardcoded mappings (Workday) or LLM field mapping (other ATS)

Browser session persists via CDP URL saved to `.cdp_url` file, allowing server reloads without losing the browser tab.

## Key Files

### Dashboard
- `dashboard/app.py` - FastAPI dashboard (~37KB): Jobs list with ATS/location filters, Apply tab with live screenshot streaming, SSE event log, pipeline orchestration, Continue/Get Email Code buttons for manual intervention

### Applicator (Form Filling)
- `applicator/form_filler.py` (~223KB) - Core orchestrator: Phase 1 (browser-use agent navigation) -> Phase 2 (Playwright CDP connection) -> Phase 3 (deterministic form filling). Routes to Workday handler or generic field mapper based on detected ATS
- `applicator/workday_handler.py` (~101KB) - Workday-specific multi-step wizard handler: My Information, My Experience, Application Questions, Voluntary Disclosures, Self Identify, Review. All hardcoded label-to-value mapping, no LLM
- `applicator/ats_profiles.py` (~47KB) - ATS platform detection & profiles for Workday, Lever, Greenhouse, iCIMS, Ashby, SmartRecruiters, BambooHR, Jobvite, SuccessFactors, Taleo, Workable. Contains URL patterns, DOM markers, field selectors, known gotchas
- `applicator/email_handler.py` (~21KB) - Gmail IMAP verification code extraction (searches recent unseen emails for 4-8 digit codes)
- `applicator/email_verifier.py` (~7KB) - Fallback: opens Gmail in browser tab to find verification codes
- `applicator/field_generator_cerebras.py` - Generates field answers via Cerebras API (used for non-Workday platforms)
- `applicator/field_generator.py` - Claude CLI wrapper (deprecated, not used in current pipeline)
- `applicator/browser_agent.py` - Original standalone browser-use agent (not used by dashboard)
- `applicator/stuck_detector.py` - Detects automation loops by tracking URL/content hash history

### Scraper
- `scraper/github_scraper.py` - Parses SimplifyJobs GitHub README for internship postings, extracts company/role/location/URL/age
- `scraper/job_description.py` - Extracts job description text from posting URLs via requests + BeautifulSoup

### Database
- `database/tracker.py` - SQLite ORM: `seen_postings`, `applications`, `applied_jobs` tables

### Resume
- `resume/generator.py` - Generates tailored HTML resume via Groq LLM (not used currently, using existing PDF)
- `resume/compiler.py` - Compiles HTML resume to PDF via WeasyPrint (not used currently)

### Config & Data
- `config.py` - Environment vars, candidate profile text, writing style rules
- `personal_info.yaml` - Structured candidate data for form filling (name, address, education, EEO, work auth, etc.)
- `credentials.yaml` - Gmail app password + Workday account credentials (gitignored)
- `.env` - API keys (CEREBRAS_API_KEY, GROQ_API_KEY, GEMINI_API_KEY)

### Pipeline & Utilities
- `main.py` - CLI pipeline orchestrator (not used by dashboard; loops: scrape -> extract JD -> generate resume -> apply)
- `notifications/notifier.py` - Telegram alerts (used by main.py)
- `run_server_watchdog.sh` - Auto-restart watchdog for uvicorn server
- `run_server.sh` - Simple server start script

### Debug & Tests
- `_debug_workday.py` - Manual test script for Workday navigation & page structure
- `_debug_workday_auth.py` - Tests Workday authentication flow
- `tests/level0_env.py` - Pre-flight checks (packages, Playwright, API keys, files)
- `tests/self_heal.py` - Self-healing form filler test

## API Keys (in .env)
- `CEREBRAS_API_KEY`: Primary LLM provider (free, 1M tokens/day, Qwen 3 235B)
- `GROQ_API_KEY`: Backup (free but only 100k tokens/day - too low for browser-use)
- `GEMINI_API_KEY`: Not working (quota exhausted/not provisioned)

## Critical Implementation Details

### browser-use LLM Setup (CRITICAL)
- MUST use `from browser_use.llm import ChatOpenAI` (browser-use's native wrapper), NOT `from langchain_openai import ChatOpenAI`
- browser-use's ChatOpenAI handles structured output, tool calling, and message serialization correctly
- LangChain's ChatOpenAI does NOT work with browser-use's `output_format` parameter and `.completion` attribute
- For Cerebras: MUST set `frequency_penalty=None` (Cerebras API rejects it with 422)
- Set `dont_force_structured_output=True` for non-OpenAI providers

### Browser-Use API (current version 0.12+)
- `Browser(headless=False, keep_alive=True)` - creates browser instance
- `Agent(task=..., llm=..., browser=browser_instance, use_vision=False)` - creates agent
- `agent.run(max_steps=N, on_step_end=callback)` - runs agent
- `on_step_end` callback signature: `async def callback(agent: Agent) -> None`
- Screenshot: `agent.browser_session.take_screenshot()` returns bytes
- Agent accepts `browser=` parameter (alias for `browser_session=`)

### CDP Browser Persistence
- Browser CDP URL saved to `.cdp_url` file after agent launches browser
- On server reload, `/continue` endpoint reconnects via CDP instead of launching new browser
- Playwright connects to existing browser: `playwright.chromium.connect_over_cdp(cdp_url)`
- Allows manual intervention -> server restart -> resume without losing browser state

### Workday Handler Architecture
- Uses `data-automation-id` attributes (Workday class names are obfuscated)
- React-based custom dropdowns, not native `<select>` elements
- Requires `isTrusted=true` mouse clicks for radio buttons (synthetic events rejected)
- Multi-step wizard: detects active step, fills fields, clicks Next, loops
- Error recovery: detects "Something went wrong" page -> reload -> re-navigate -> retry
- Session timeout ~15-20 min idle
- Hardcoded 80+ yes/no question patterns for Application Questions step
- EEO section: fills Race/Ethnicity, Veteran Status, Disability dropdowns

### Cerebras Setup
- Base URL: https://api.cerebras.ai/v1
- Model: qwen-3-235b-a22b-instruct-2507
- Supports tool calling (function calling) - required by browser-use
- OpenAI-compatible API
- 8K context limit on free tier (tight for browser-use DOM content)

### Form Filling Strategy
- **Workday**: Hardcoded label-to-value mapping from `personal_info.yaml` (fastest, most reliable)
- **Other ATS**: JavaScript extracts form fields -> LLM maps fields to candidate profile -> Playwright fills values
- **Modal handling**: Agent detects "Start Your Application" modal, prefers "Autofill with Resume", falls back to "Apply Manually"
- **Explicit instruction**: Agent told NOT to click "Apply with LinkedIn" or any OAuth buttons

### Dashboard API Endpoints
- `GET /` - Serve HTML dashboard
- `GET /api/jobs` - Fetch postings from SimplifyJobs README
- `GET /api/applied` - List applied URLs
- `POST /api/applied` - Mark job as applied
- `POST /api/unapplied` - Unmark applied
- `POST /api/upload/{type}` - Upload resume/transcript (stored in `uploads/`)
- `GET /events` - SSE stream of pipeline events
- `GET /screenshot-stream` - SSE stream of browser screenshots (base64)
- `POST /run` - Start application pipeline
- `POST /stop` - Stop running pipeline
- `POST /continue` - Manual action trigger (reconnects via CDP if needed, analyzes page, fills forms)
- `POST /email-verify` - Check Gmail for verification codes via IMAP

## Candidate Info (for form filling)
- Name: Edrick Chang
- Email: eachang@scu.edu
- Phone: (408) 806-6495
- LinkedIn: https://linkedin.com/in/edrickchang
- GitHub: https://github.com/edrickchang
- School: Santa Clara University
- Degree: BS Computer Science & Engineering
- GPA: 3.78
- Graduation: June 2028
- Work Authorization: Yes (US Citizen)
- Sponsorship Needed: No
- Pronouns: He/Him
- Full structured data in `personal_info.yaml`

## Known Issues / Risks
1. Cerebras 8K context limit may be tight for browser-use (lots of DOM content) - consider OpenRouter if needed
2. Agent might click "Apply with LinkedIn" despite instructions (explicit warnings added)
3. File upload via browser-use agent is unreliable
4. Workday session timeout ~15-20 min idle
5. Lever/Greenhouse form filling not yet implemented as deterministic handlers (uses generic LLM mapper)
6. Resume auto-generation not active (using existing PDF at /Users/edrickchang/Downloads/EdrickChang_Resume.pdf)
7. Auto-submit disabled - manual review required

## Recent Changes (March 20-23, 2026)
- **CDP reconnect**: Browser session persists across server reloads via `.cdp_url`
- **Hardcoded Workday filler**: No LLM needed for Workday forms, all label-to-value mapping
- **Workday error recovery**: Detects "Something went wrong", reloads, retries
- **Diagnostic logging**: Field counts, visible inputs, active step, button labels
- **Modal preference**: Changed from "Use My Last Application" to "Autofill with Resume"
- **Greenhouse dropdowns**: Custom dropdown handler for Greenhouse forms
- **Cover letter cleanup**: Removed unnecessary cover letter generation
- **Multi-select EEO**: Support for multi-select EEO dropdowns
- **CAPTCHA detection**: Size-based filtering (>=70x65px) to avoid false positives

## Next Steps
1. Build deterministic form fillers for Lever and Greenhouse (like Workday handler)
2. If Cerebras context is too small, switch to OpenRouter (free, 65K+ context)
3. Test end-to-end on more Workday applications
4. Implement resume auto-generation with LaTeX template
5. Add Simplify Chrome extension integration
6. Connect full pipeline (scraper -> resume gen -> apply -> notify)
7. Add scheduling (APScheduler or cron loop)
8. Handle file upload reliably (resume PDF, transcript)

## How to Run
```bash
cd ~/getjobs2026
source .venv/bin/activate

# Standard
python -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8080

# With watchdog (auto-restart on hang)
bash run_server_watchdog.sh

# Then open http://127.0.0.1:8080
```

## File Sizes Reference
| File | Size | Notes |
|------|------|-------|
| `applicator/form_filler.py` | ~223KB | Main orchestrator, largest file |
| `applicator/workday_handler.py` | ~101KB | Workday-specific, growing fast |
| `applicator/ats_profiles.py` | ~47KB | ATS detection & profiles |
| `dashboard/app.py` | ~37KB | Full dashboard + API |
| `applicator/email_handler.py` | ~21KB | Gmail IMAP |
| `_debug_workday_auth.py` | ~14KB | Debug script |

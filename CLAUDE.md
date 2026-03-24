# Auto-Apply: Internship Application Pipeline

## Project Summary
Automated job application pipeline that scrapes GitHub for internship postings, generates tailored answers, and uses browser-use (AI browser agent) to fill out application forms.

## Current State (March 20, 2026)
- Dashboard working at http://127.0.0.1:8080
- All pre-flight checks pass (API, resume, JD extractor, LLM, browser-use)
- Ready for first real test of browser agent filling a Lever form
- NOT YET TESTED: the browser agent actually filling form fields end-to-end

## Tech Stack
- Python 3.14 (causes pydantic/langchain compatibility issues - need `object.__setattr__` for setting attributes on LangChain models)
- browser-use: AI browser automation (Playwright under the hood)
- Cerebras API (free tier, 1M tokens/day): Qwen 3 235B model for both LLM calls and browser agent
- FastAPI dashboard for monitoring
- SQLite for tracking postings/applications

## Key Files
- `dashboard/app.py` - FastAPI dashboard with live screenshot streaming, SSE event log, pipeline orchestration
- `applicator/field_generator.py` - Generates answers for text fields via Cerebras API
- `applicator/browser_agent.py` - Original browser-use agent (not used by dashboard currently)
- `scraper/github_scraper.py` - Parses SimplifyJobs GitHub README for internship postings
- `scraper/job_description.py` - Extracts job description text from posting URLs
- `resume/generator.py` - Generates tailored resumes (currently using user's existing PDF instead)
- `resume/compiler.py` - Compiles HTML resume to PDF via WeasyPrint
- `database/tracker.py` - SQLite tracking for seen postings and applications
- `config.py` - Configuration and candidate profile
- `main.py` - Full pipeline orchestrator (CLI mode)

## API Keys (in .env)
- CEREBRAS_API_KEY: Primary LLM provider (free, 1M tokens/day, Qwen 3 235B)
- GROQ_API_KEY: Backup (free but only 100k tokens/day - too low for browser-use)
- GEMINI_API_KEY: Not working (quota exhausted/not provisioned)

## Critical Implementation Details

### browser-use LLM Setup (CRITICAL)
- MUST use `from browser_use.llm import ChatOpenAI` (browser-use's native wrapper), NOT `from langchain_openai import ChatOpenAI`
- browser-use's ChatOpenAI handles structured output, tool calling, and message serialization correctly
- LangChain's ChatOpenAI does NOT work with browser-use's `output_format` parameter and `.completion` attribute
- For Cerebras: MUST set `frequency_penalty=None` (Cerebras API rejects it with 422)
- Set `dont_force_structured_output=True` for non-OpenAI providers

### Browser-Use API (current version)
- `Browser(headless=False, keep_alive=True)` - creates browser instance
- `Agent(task=..., llm=..., browser=browser_instance, use_vision=False)` - creates agent
- `agent.run(max_steps=N, on_step_end=callback)` - runs agent
- `on_step_end` callback signature: `async def callback(agent: Agent) -> None`
- Screenshot: `agent.browser_session.take_screenshot()` returns bytes
- Agent accepts `browser=` parameter (alias for `browser_session=`)

### Cerebras Setup
- Base URL: https://api.cerebras.ai/v1
- Model: qwen-3-235b-a22b-instruct-2507
- Supports tool calling (function calling) - required by browser-use
- OpenAI-compatible API

### Resume
- Using existing PDF at: /Users/edrickchang/Downloads/EdrickChang_Resume.pdf
- LaTeX resume template and formatting guidelines saved in conversation but not yet implemented as auto-generation
- Resume generator exists but switched to using existing PDF for testing

### Candidate Info (for form filling)
- Name: Edrick Chang
- Email: eachang@scu.edu
- Phone: (408) 806-6495
- LinkedIn: https://linkedin.com/in/edrickchang
- GitHub: https://github.com/edrickchang
- School: Santa Clara University
- Degree: BS Computer Science & Engineering
- GPA: 3.78
- Graduation: June 2028
- Work Authorization: Yes
- Sponsorship Needed: No
- Pronouns: He/him

## Test URLs

### Lever
https://jobs.lever.co/aofl/4b91076d-8937-4dbc-a502-a7d6a66e2e19/apply?utm_source=Simplify&ref=Simplify
(Age of Learning - Software Engineer Intern)

### Greenhouse
https://boards.greenhouse.io/optiverus/jobs/7973726002
(Optiver - Software Engineer Intern Summer 2026, Chicago)
https://boards.greenhouse.io/embed/job_app?token=8106513002
(C3 AI - Software Engineer Intern Summer 2026, Redwood City CA)

### Ashby
https://jobs.ashbyhq.com/notion/23ac2477-0008-4bed-b1c1-81f90a32e9e6/application
(Notion - Software Engineer Intern Summer 2026)
https://jobs.ashbyhq.com/replit/12737078-74c7-4e63-98a7-5e8da1e9deb1/application
(Replit - Software Engineering Intern Summer 2026)

### SmartRecruiters
https://jobs.smartrecruiters.com/LinkedIn3/744000085237793-software-engineer-intern-undergraduate-summer-2026-mountain-view-ca-
(LinkedIn - Software Engineer Intern Undergraduate Summer 2026, Mountain View CA)
https://jobs.smartrecruiters.com/Visa/744000109722936-software-engineer-intern-summer-2026-foster-city
(Visa - Software Engineer Intern Summer 2026, Foster City CA)

## Known Issues / Risks
1. Browser agent has not yet successfully filled a form end-to-end
2. Agent might click "Apply with LinkedIn" despite instructions not to (added explicit warnings)
3. Cerebras 8K context limit on free tier may be tight for browser-use (lots of DOM content)
4. File upload via browser-use agent is untested
5. The dashboard keeps browser open after completion for manual review/submit

## Next Steps
1. Test browser agent filling the Lever form end-to-end
2. If Cerebras context is too small, switch to OpenRouter (free, 65K+ context)
3. Add Simplify Chrome extension integration
4. Implement resume auto-generation with LaTeX template
5. Connect full pipeline (scraper -> resume gen -> apply -> notify)
6. Add scheduling (APScheduler or cron loop)

## How to Run
```bash
cd ~/getjobs2026
source .venv/bin/activate
python -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8080
# Then open http://127.0.0.1:8080 and click Start
```

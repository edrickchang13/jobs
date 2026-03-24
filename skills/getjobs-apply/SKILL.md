---
name: getjobs-apply
description: Trigger automated internship applications via the getjobs2026 pipeline. Use for "apply to jobs", "start batch apply", "run auto-queue", "check application status", "apply to this job URL", or "how many jobs are queued".
version: 1.0.0
requires:
  bins:
    - curl
---

# getjobs-apply — OpenClaw Skill

You control the **getjobs2026** automated application pipeline running on
`http://127.0.0.1:8080`. The pipeline handles Greenhouse, Lever, and Workday
forms using browser automation + LLM-generated answers.

## Dashboard API Reference

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/jobs` | GET | List all scraped jobs (cached 30 min) |
| `/api/jobs?refresh=true` | GET | Force re-scrape GitHub |
| `/api/queue` | GET | Get current auto-queue status |
| `/api/queue/start` | POST | Start batch auto-apply loop |
| `/api/queue/stop` | POST | Stop the batch loop after current job |
| `/run` | POST | Apply to a single specific URL |
| `/stop` | POST | Stop the current single-job run |
| `/api/applied` | GET | List URLs already applied to |
| `/events` | GET | SSE stream of live pipeline events |

## How to handle user requests

### "Apply to jobs" / "Start batch apply" / "Run auto-queue"
1. Check if pipeline is already running: `curl -s http://127.0.0.1:8080/api/queue`
2. If not running, start the queue:
```bash
curl -s -X POST http://127.0.0.1:8080/api/queue/start \
  -H 'Content-Type: application/json' \
  -d '{"ats_filter": ["greenhouse", "lever", "workday"], "limit": 10, "delay_seconds": 30}'
```
3. Report back how many jobs were queued and their companies/ATS types.
4. Tell the user: "The pipeline will apply sequentially. The browser stays open after each job for your review before moving to the next."

### "Apply to [specific URL]"
Extract the URL from the user's message, then:
```bash
curl -s -X POST http://127.0.0.1:8080/run \
  -H 'Content-Type: application/json' \
  -d '{"url": "<URL>", "company": "<company>", "role": "<role>"}'
```
Tell the user to watch the browser window that opens. They'll need to click Submit manually after reviewing.

### "How many jobs are left?" / "Queue status"
```bash
curl -s http://127.0.0.1:8080/api/queue
```
Parse the JSON and report: running status, current job, remaining count.

### "Stop" / "Pause"
```bash
curl -s -X POST http://127.0.0.1:8080/api/queue/stop
```

### "What jobs are available?" / "Show me new jobs"
```bash
curl -s http://127.0.0.1:8080/api/jobs | head -c 3000
```
Summarize the companies and roles. Filter to show only Greenhouse/Lever/Workday if asked.

### "Apply only to [ATS type] jobs"
Start the queue with the appropriate `ats_filter`:
- Greenhouse only: `{"ats_filter": ["greenhouse"]}`
- Lever only: `{"ats_filter": ["lever"]}`
- Workday only: `{"ats_filter": ["workday"]}`
- All: `{"ats_filter": ["greenhouse", "lever", "workday"]}`

### "Check what I've applied to"
```bash
curl -s http://127.0.0.1:8080/api/applied
```

## Important notes for the user

- **The browser stays open after each job** — you must review the filled form and click Submit manually, OR the pipeline will auto-move to the next job after the delay.
- **Workday requires account creation** — if a Workday job requires a new account, the pipeline will pause and alert you.
- **CAPTCHA** — if a CAPTCHA is detected, the dashboard will beep and flash. You must solve it manually in the browser.
- **Resume** — make sure a resume is uploaded in the dashboard's Apply tab before starting the queue.
- The pipeline uses **Gemini Flash** (free, 1M context) if `GEMINI_API_KEY` is set, otherwise falls back to Cerebras.

## Status indicators in API responses

```json
{
  "running": true,       // batch loop is active
  "index": 3,            // currently processing job #3
  "remaining": 7,        // jobs left in queue
  "queue": [...]         // full job list with company, role, ats, url
}
```

## Example conversation flows

**User**: "Apply to 5 new Greenhouse jobs"
**You**: Call `/api/queue/start` with `{"ats_filter": ["greenhouse"], "limit": 5}`, report results.

**User**: "Apply to https://boards.greenhouse.io/stripe/jobs/12345"
**You**: Call `/run` with the URL, confirm it's been started, tell user to watch the browser.

**User**: "Stop after this one"
**You**: Call `/api/queue/stop`, confirm stopping.

**User**: "How's it going?"
**You**: Call `/api/queue`, parse and report current job + remaining count.

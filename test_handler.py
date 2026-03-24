#!/usr/bin/env python3
"""
Standalone handler test script — runs any ATS handler directly without the dashboard.

Usage (from ~/getjobs2026 with venv active):

  # Test Greenhouse (Pinterest Apprentice Engineer)
  python test_handler.py greenhouse "https://boards.greenhouse.io/pinterest/jobs/6272165"

  # Test Lever (Age of Learning)
  python test_handler.py lever "https://jobs.lever.co/aofl/4b91076d-8937-4dbc-a502-a7d6a66e2e19/apply"

  # Test Ashby (Creatify Lab — find current URL on Simplify)
  python test_handler.py ashby "https://jobs.ashbyhq.com/creatify/..."

  # Test SmartRecruiters
  python test_handler.py smartrecruiters "https://jobs.smartrecruiters.com/..."

  # Test Workday (Cohesity)
  python test_handler.py workday "https://cohesity.wd5.myworkdayjobs.com/Cohesity_Careers/job/Santa-Clara-CA---USA-Office/Software-Engineering-Intern--Summer-2026_R01589-1"

Options:
  --headless    Run in headless mode (no browser window)
  --resume PATH Path to resume PDF (default: ~/Downloads/EdrickChang_Resume.pdf)
"""

import asyncio
import sys
import os
import time
import argparse
from datetime import datetime

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

RESET  = "\033[0m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

STATUS_COLORS = {
    "start":   CYAN,
    "info":    DIM,
    "success": GREEN,
    "warning": YELLOW,
    "error":   RED,
    "warn":    YELLOW,
}

events_log = []


async def event_callback(step: str, status: str, detail: str = ""):
    ts = datetime.now().strftime("%H:%M:%S")
    color = STATUS_COLORS.get(status.lower(), "")
    icon = {"start": "▶", "info": "·", "success": "✓", "warning": "⚠", "error": "✗", "warn": "⚠"}.get(status.lower(), "·")
    line = f"{DIM}{ts}{RESET} {color}{icon} [{step}] {detail}{RESET}"
    print(line, flush=True)
    events_log.append({"ts": ts, "step": step, "status": status, "detail": detail})


async def screenshot_callback(data: bytes):
    pass  # Drop screenshots — CLI doesn't need them


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

async def run_test(ats: str, url: str, resume_path: str, headless: bool):
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}Testing {ats.upper()} handler{RESET}")
    print(f"URL: {url}")
    print(f"Resume: {resume_path}")
    print(f"Headless: {headless}")
    print(f"{BOLD}{'='*60}{RESET}\n")

    # Bootstrap Playwright
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(f"{RED}ERROR: playwright not installed. Run: pip install playwright{RESET}")
        sys.exit(1)

    # Detect company/role from URL for better LLM context
    company = url.split("/")[2].replace("jobs.", "").replace("boards.", "").split(".")[0].title()
    role = "Software Engineer Intern"
    try:
        parts = [p for p in url.split("/") if p and len(p) > 5 and not p.startswith("http")]
        if len(parts) >= 2:
            # Try to extract role from URL path
            role_part = parts[-1] if len(parts[-1]) > 10 else parts[-2]
            role = role_part.replace("-", " ").replace("_", " ").title()[:60]
    except Exception:
        pass

    print(f"{DIM}Company: {company}  Role: {role}{RESET}\n")

    start_time = time.time()
    result = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        # Import generator
        from applicator.field_generator_cerebras import generate_field_answer as generate_answer

        try:
            if ats == "greenhouse":
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3.0)
                from applicator.greenhouse_handler import handle_greenhouse_apply
                result = await handle_greenhouse_apply(
                    page=page,
                    resume_path=resume_path,
                    job_description="",
                    company=company,
                    role=role,
                    event_callback=event_callback,
                    screenshot_callback=screenshot_callback,
                    generate_answer_fn=generate_answer,
                )

            elif ats == "lever":
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3.0)
                from applicator.lever_handler import handle_lever_apply
                result = await handle_lever_apply(
                    page=page,
                    resume_path=resume_path,
                    job_description="",
                    company=company,
                    role=role,
                    event_callback=event_callback,
                    screenshot_callback=screenshot_callback,
                    generate_answer_fn=generate_answer,
                )

            elif ats == "ashby":
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3.0)
                from applicator.ashby_handler import handle_ashby_apply
                result = await handle_ashby_apply(
                    page=page,
                    resume_path=resume_path,
                    job_description="",
                    company=company,
                    role=role,
                    event_callback=event_callback,
                    screenshot_callback=screenshot_callback,
                    generate_answer_fn=generate_answer,
                )

            elif ats == "smartrecruiters":
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3.0)
                from applicator.smartrecruiters_handler import handle_smartrecruiters_apply
                result = await handle_smartrecruiters_apply(
                    page=page,
                    resume_path=resume_path,
                    job_description="",
                    company=company,
                    role=role,
                    event_callback=event_callback,
                    screenshot_callback=screenshot_callback,
                    generate_answer_fn=generate_answer,
                )

            elif ats in ("workday", "myworkdayjobs"):
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3.0)

                # Step 1: Accept cookies / legal notice
                try:
                    btn = page.locator('[data-automation-id="legalNoticeAcceptButton"]')
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        await asyncio.sleep(1.0)
                except Exception:
                    pass

                # Step 2: Click Apply button on the job listing page
                apply_selectors = [
                    'a[data-uxi-element-id="Apply_adventureButton"]',
                    '[data-automation-id="adventureButton"]',
                    '[data-automation-id="jobPostingApplyButton"]',
                    'a[role="button"]:has-text("Apply")',
                    'button:has-text("Apply")',
                ]
                for asel in apply_selectors:
                    try:
                        abtn = page.locator(asel).first
                        if await abtn.is_visible(timeout=2000):
                            await abtn.click()
                            await asyncio.sleep(5.0)
                            await event_callback("Workday", "info", "Clicked Apply button")
                            break
                    except Exception:
                        continue

                # Step 3: Click "Apply Manually" if the option modal appears
                try:
                    manual = page.locator('[data-automation-id="applyManually"]')
                    if await manual.is_visible(timeout=4000):
                        await manual.click()
                        await asyncio.sleep(5.0)
                        await event_callback("Workday", "info", "Clicked Apply Manually")
                except Exception:
                    pass

                # Step 4: Handle auth (create account / sign in) via form_filler helper
                try:
                    from applicator.form_filler import _handle_workday_auth
                    auth_ok = await _handle_workday_auth(page, event_callback)
                    if not auth_ok:
                        await event_callback("Workday", "warning",
                            "Auth failed or no credentials — trying form fill anyway")
                except Exception as auth_e:
                    await event_callback("Workday", "warning", f"Auth step error: {auth_e}")

                # Step 5: Fill the multi-step form
                from applicator.workday_handler import handle_workday_application
                result = await handle_workday_application(
                    page=page,
                    resume_path=resume_path,
                    company=company,
                    role=role,
                    job_description="",
                    event_callback=event_callback,
                    screenshot_callback=screenshot_callback,
                )

            else:
                print(f"{RED}Unknown ATS: {ats}{RESET}")
                print("Valid options: greenhouse, lever, ashby, smartrecruiters, workday")
                await browser.close()
                return

        except Exception as e:
            print(f"\n{RED}FATAL ERROR: {e}{RESET}")
            import traceback
            traceback.print_exc()
            result = {"filled": 0, "failed": 0, "errors": [str(e)]}

        # Don't close browser immediately so user can review the form
        elapsed = time.time() - start_time
        print(f"\n{BOLD}{'='*60}{RESET}")
        print(f"{BOLD}RESULTS — {ats.upper()}{RESET}")
        print(f"  Filled:   {GREEN}{result.get('filled', 0)}{RESET}")
        print(f"  Failed:   {RED}{result.get('failed', 0)}{RESET}")
        print(f"  Elapsed:  {elapsed:.1f}s")
        errors = result.get("errors", [])
        if errors:
            print(f"\n  {YELLOW}Errors:{RESET}")
            for e in errors[:10]:
                print(f"    {RED}• {e}{RESET}")
        else:
            print(f"\n  {GREEN}No errors!{RESET}")

        if not headless:
            print(f"\n{CYAN}Browser left open for review. Press Enter to close...{RESET}", end="", flush=True)
            await asyncio.get_event_loop().run_in_executor(None, input)

        await browser.close()

    print(f"\n{BOLD}Done.{RESET}\n")


def main():
    parser = argparse.ArgumentParser(description="Test an ATS handler end-to-end")
    parser.add_argument("ats", help="ATS to test: greenhouse | lever | ashby | smartrecruiters | workday")
    parser.add_argument("url", help="Job application URL")
    parser.add_argument("--headless", action="store_true", help="Run headless (no browser UI)")
    parser.add_argument(
        "--resume",
        default=os.path.expanduser("~/Downloads/EdrickChang_Resume.pdf"),
        help="Path to resume PDF",
    )
    args = parser.parse_args()

    if not os.path.exists(args.resume):
        print(f"{YELLOW}Warning: Resume not found at {args.resume}{RESET}")
        # Try common locations
        for alt in [
            os.path.expanduser("~/Desktop/EdrickChang_Resume.pdf"),
            os.path.expanduser("~/Documents/EdrickChang_Resume.pdf"),
        ]:
            if os.path.exists(alt):
                print(f"Using {alt}")
                args.resume = alt
                break

    # Run
    asyncio.run(run_test(
        ats=args.ats.lower(),
        url=args.url,
        resume_path=args.resume,
        headless=args.headless,
    ))


if __name__ == "__main__":
    main()

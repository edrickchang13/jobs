"""
Hybrid Playwright + LLM form filler.

1. Playwright navigates and extracts form fields from the DOM
2. LLM maps candidate profile to form fields in a single call
3. Playwright fills fields deterministically
"""

import asyncio
import json
import os
import re
import sys
import threading
from pathlib import Path

import yaml
from openai import OpenAI
from playwright.async_api import async_playwright, Page, Browser, Frame
from config import CANDIDATE_PROFILE, WRITING_STYLE
from applicator.email_verifier import complete_email_verification
from applicator.ats_profiles import detect_ats, get_profile


def _load_personal_info() -> dict:
    """Load personal info from YAML file."""
    info_path = Path(__file__).parent.parent / "personal_info.yaml"
    if info_path.exists():
        with open(info_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def _build_known_values(info: dict) -> str:
    """Build the KNOWN VALUES section of the prompt from personal info."""
    lines = []
    mappings = [
        ("first_name", "First name"),
        ("last_name", "Last name"),
        ("full_name", "Full name / Name"),
        ("email", "Email"),
        ("phone", "Phone"),
        ("phone_country", "Phone country / Phone number country"),
        ("phone_country_code", "Phone country code"),
        ("pronouns", "Pronouns"),
        ("street_address", "Street address / Address"),
        ("city", "City"),
        ("state", "State"),
        ("zip_code", "Zip code / Postal code"),
        ("country", "Country"),
        ("location", "Current location / Location"),
        ("linkedin", "LinkedIn URL"),
        ("github", "GitHub URL"),
        ("portfolio", "Portfolio / Website"),
        ("school", "School / University"),
        ("degree", "Degree"),
        ("gpa", "GPA"),
        ("graduation_date", "Graduation date"),
        ("graduation_year", "Graduation year / Expected graduation year / What year will you graduate"),
        ("authorized_to_work", "Work authorization in US"),
        ("sponsorship_needed", "Sponsorship needed"),
        ("citizenship", "Citizenship"),
        ("visa_status", "Visa status"),
        ("gender", "Gender"),
        ("race_ethnicity", "Race / Ethnicity"),
        ("veteran_status", "Veteran status"),
        ("disability_status", "Disability status"),
        ("current_company", "Current company / organization"),
        ("currently_employed", "Currently employed"),
        ("previous_employer", "Previous employer / Has had previous job"),
        ("years_of_experience", "Years of experience"),
        ("internship_experience", "Previous internship experience / Has had internship"),
        ("coop_experience", "Co-op experience / Has had co-op"),
        ("previous_internships", "Number of previous internships"),
        ("start_date", "Available start date"),
        ("desired_salary", "Desired salary"),
        ("willing_to_relocate", "Willing to relocate / Able to relocate"),
        ("intern_season", "Intern season / What intern season are you interested in"),
        ("onsite_location_preference", "Onsite location preference / Which onsite location"),
        ("drivers_license", "Driver's license"),
        ("has_vehicle", "Has vehicle / transportation"),
        ("emergency_contact_name", "Emergency contact name"),
        ("emergency_contact_phone", "Emergency contact phone"),
        ("emergency_contact_relationship", "Emergency contact relationship"),
        ("how_did_you_hear", "How did you hear about us / this position / this role / Referral source"),
        ("previously_worked_here", "Previously worked at this company / Have you worked here before"),
        ("previously_applied_here", "Previously applied / Have you applied before"),
        ("referral", "Were you referred / Referral / Do you know anyone who works here"),
        ("referral_name", "Referral name / Who referred you"),
        ("age_over_18", "Are you 18 or older / Are you over 18"),
        ("felony_conviction", "Felony / conviction / criminal record"),
        ("drug_test_consent", "Drug test / screening consent"),
        ("background_check_consent", "Background check consent"),
        ("available_start_date", "Available start date / When can you start / Earliest start date"),
        ("highest_education", "Highest level of education / education level"),
        ("reason_for_leaving", "Reason for leaving current position"),
    ]
    # Keys where empty string is meaningful (tells LLM to leave blank)
    always_include = {"current_company", "currently_employed"}
    for key, label in mappings:
        value = info.get(key, "")
        if value:
            lines.append(f'- {label}: "{value}"')
        elif key in always_include:
            lines.append(f'- {label}: "" (leave blank)')
    # Always add referral rule
    lines.append('- Referred by employee: ALWAYS "No" - never say the candidate was referred')
    return "\n".join(lines)

# Module-level refs so browser stays alive after fill
_playwright = None
_browser = None
_bu_browser = None  # browser-use browser instance
_pw_for_cdp = None  # Playwright instance connected to browser-use via CDP


async def _detect_workday_page_state(page) -> str:
    """Detect what Workday page we're on: auth, form, verify, success, upload, unknown."""
    try:
        return await page.evaluate("""() => {
            const t = document.body.innerText.toLowerCase();

            // Error page ("Something went wrong") — check FIRST since error page still has progress bar
            if (t.includes('something went wrong') || t.includes('please refresh the page')) {
                return 'error';
            }

            // Auth page (sign in or create account)
            if (document.querySelector('[data-automation-id="signInContent"]')
                || document.querySelector('[data-automation-id="createAccountLink"]')
                || document.querySelector('[data-automation-id="signInSubmitButton"]')
                || document.querySelector('[data-automation-id="createAccountSubmitButton"]')
                || document.querySelector('input[data-automation-id="password"]')) {
                return 'auth';
            }

            // Form page (application wizard with progress bar or form fields)
            if (document.querySelector('[data-automation-id="applyFlowPage"]')
                || document.querySelector('[data-automation-id="pageFooterNextButton"]')
                || document.querySelector('[data-automation-id="progressBar"]')
                || document.querySelector('[data-automation-id="bottom-navigation-next-button"]')) {
                return 'form';
            }

            // Verification
            if (['verify your email', 'verification code', 'check your email',
                 'check your inbox', 'enter the code'].some(k => t.includes(k))) {
                return 'verify';
            }

            // Success
            if (['application submitted', 'thank you for applying',
                 'application received', 'successfully submitted'].some(k => t.includes(k))) {
                return 'success';
            }

            // Resume upload page (before auth)
            if (document.querySelector('[data-automation-id="file-upload-drop-zone"]')
                && !document.querySelector('[data-automation-id="progressBar"]')) {
                return 'upload';
            }

            return 'unknown';
        }""")
    except Exception:
        return "unknown"


async def fill_with_browser_agent(
    url: str,
    company: str,
    role: str,
    resume_path: str,
    job_description: str,
    event_callback=None,
    screenshot_callback=None,
    transcript_path: str = "",
) -> dict:
    """Fully autonomous job application pipeline.

    Phase 1: Browser-use agent navigates to URL, clicks Apply, uploads resume (max 15 steps)
    Phase 2: Deterministic code handles auth (_handle_workday_auth) with real Playwright clicks
    Phase 3: Deterministic code fills multi-step form (handle_workday_application)

    Zero user intervention required.
    """
    global _bu_browser

    personal_info = _load_personal_info()
    known_values = _build_known_values(personal_info)
    creds = _load_credentials()

    # Pick credentials for this ATS
    ats_key = detect_ats(url)
    ats_creds = creds.get(ats_key or "workday", creds.get("workday", {}))
    cred_email = ats_creds.get("email", "")
    cred_password = ats_creds.get("password", "")
    is_workday = "workday" in url.lower() or "myworkdayjobs" in url.lower()

    if event_callback:
        await event_callback("Navigate", "info", f"Starting pipeline (ATS: {ats_key or 'unknown'})")

    # Set up LLM
    from browser_use import Agent, Browser
    from browser_use.llm import ChatOpenAI

    llm = ChatOpenAI(
        model="qwen-3-235b-a22b-instruct-2507",
        base_url="https://api.cerebras.ai/v1",
        api_key=os.getenv("CEREBRAS_API_KEY"),
        frequency_penalty=None,
        dont_force_structured_output=True,
    )

    _headless = os.getenv("HEADLESS", "false").lower() == "true"
    _bu_browser = Browser(headless=_headless, keep_alive=True, disable_security=True)

    # ====================================================================
    # PHASE 1: Agent ONLY navigates + clicks Apply. Does NOT handle auth.
    # ====================================================================
    task = f"""You are starting a job application for {company} - {role}.

URL: {url}

YOUR TASK IS LIMITED — do ONLY these steps, then STOP:

1. Navigate to the URL above
2. Click "Apply" or "Apply Now" button. On Workday sites the button may be a link with text "Apply" inside an element with data-uxi-element-id="Apply_adventureButton".
3. If you see a "Start Your Application" dialog with options like "Apply Manually", "Autofill with Resume", or "Use My Last Application": click "Autofill with Resume" (preferred). If not available, click "Apply Manually". Do NOT click "Use My Last Application" (it causes errors).
4. If you see a cookie consent banner, click "Accept" or "Accept All"

STOP CONDITIONS — stop immediately when you see ANY of these:
- A Sign In or Create Account page (has email/password fields)
- An application form with fields to fill out
- An email verification page
- "Already applied" or "Position has been filled" message
- A CAPTCHA

DO NOT DO ANY OF THESE:
- Do NOT fill in any form fields (name, email, etc.)
- Do NOT sign in or create an account
- Do NOT click any Sign In or Create Account buttons
- Do NOT click "Apply with LinkedIn" or any OAuth/social login buttons
- Do NOT click "Sign in with Google/Facebook/Apple/SSO"

The system will handle authentication and form filling automatically after you stop.
Report what page you're on when you stop."""

    file_paths = [resume_path]
    if transcript_path:
        file_paths.append(transcript_path)

    agent = Agent(
        task=task,
        llm=llm,
        browser=_bu_browser,
        use_vision=False,
        max_failures=5,
        max_actions_per_step=3,
        loop_detection_enabled=True,
        loop_detection_window=5,
        available_file_paths=file_paths,
    )

    steps = 0
    agent_error = None

    async def on_step_end(agent_instance: Agent) -> None:
        nonlocal steps
        steps += 1
        if screenshot_callback:
            try:
                ss = await agent_instance.browser_session.take_screenshot()
                if ss:
                    await screenshot_callback(ss)
            except Exception:
                pass
        if event_callback:
            await event_callback("Agent", "info", f"Navigation step {steps}")

        # Handle "Start Your Application" modal if it appears during agent navigation
        try:
            bu_page = await agent_instance.browser_session.get_current_page()
            if bu_page:
                modal_btn = await bu_page.evaluate("""() => {
                    const btns = document.querySelectorAll('button, a[role="button"], div[role="button"]');
                    const preferred = ['use my last application', 'apply manually'];
                    for (const pref of preferred) {
                        for (const btn of btns) {
                            const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                            if (text === pref || text.includes(pref)) {
                                const r = btn.getBoundingClientRect();
                                if (r.width > 0 && r.height > 0) {
                                    return {text: text, x: r.x + r.width / 2, y: r.y + r.height / 2, found: true};
                                }
                            }
                        }
                    }
                    return {found: false};
                }""")
                if modal_btn and modal_btn.get("found"):
                    await bu_page.mouse.click(modal_btn["x"], modal_btn["y"])
                    if event_callback:
                        await event_callback("Navigate", "success", f"Clicked modal: '{modal_btn['text']}'")
                    await asyncio.sleep(3.0)
        except Exception:
            pass

    history = None
    try:
        if event_callback:
            await event_callback("Agent", "info", "Agent navigating to application page...")

        history = await agent.run(max_steps=15, on_step_end=on_step_end)

        if event_callback:
            await event_callback("Agent", "info", f"Agent stopped after {steps} steps")

    except Exception as e:
        import traceback as _tb
        agent_error = f"{type(e).__name__}: {e}"
        tb_str = _tb.format_exc()
        if event_callback:
            await event_callback("Agent", "error", f"Agent error: {agent_error[:200]}")
            await event_callback("Agent", "info", f"Traceback: {tb_str[:500]}")

    # Get page after agent stops — browser-use returns a CDP Page, but we need
    # a Playwright Page for form filling (locator, fill, click, frames, etc.)
    page = None
    browser_pw = None
    pw_instance = None

    # Connect Playwright to the same browser via CDP
    try:
        cdp_url = _bu_browser.cdp_url
        if cdp_url:
            if event_callback:
                await event_callback("Pipeline", "info", f"Connecting Playwright to browser via CDP: {cdp_url[:50]}...")
            from playwright.async_api import async_playwright
            global _pw_for_cdp
            pw_instance = await async_playwright().start()
            _pw_for_cdp = pw_instance
            browser_pw = await pw_instance.chromium.connect_over_cdp(cdp_url)
            # Get the most recently active page
            contexts = browser_pw.contexts
            if contexts:
                pages = contexts[0].pages
                if pages:
                    # Find the page that's not about:blank
                    for p in reversed(pages):
                        if p.url and "about:blank" not in p.url:
                            page = p
                            break
                    if not page:
                        page = pages[-1]
            if page and event_callback:
                await event_callback("Pipeline", "success", f"Got Playwright page: {page.url[:80]}")
        else:
            if event_callback:
                await event_callback("Pipeline", "warning", "No CDP URL available from browser-use")
    except Exception as e:
        import traceback as _tb
        if event_callback:
            await event_callback("Pipeline", "error", f"Playwright connect failed: {e}")
            await event_callback("Pipeline", "info", f"Traceback: {_tb.format_exc()[:500]}")

    # Fallback: try the old way (browser-use CDP page)
    if not page:
        try:
            bu_page = await _bu_browser.get_current_page()
            if bu_page:
                if event_callback:
                    await event_callback("Pipeline", "warning", "Using browser-use CDP page (limited Playwright support)")
                # browser-use Page has evaluate() but not locator() — form filling will be limited
                page = bu_page
        except Exception:
            pass

    if not page:
        if event_callback:
            await event_callback("Pipeline", "error", "No browser page available after agent")
        return {"browser": browser_pw, "page": None, "completed": False,
                "summary": {"steps": steps, "error": agent_error or "No page"}}

    # Take screenshot
    if screenshot_callback:
        try:
            ss = await page.screenshot(type="png")
            await screenshot_callback(ss)
        except Exception:
            pass

    # ====================================================================
    # PHASE 2: Detect page state and handle auth automatically
    # ====================================================================
    completed = False

    if is_workday:
        state = await _detect_workday_page_state(page)
        if event_callback:
            await event_callback("Pipeline", "info", f"Page state after agent: {state}")

        # Handle "Start Your Application" modal (appears when user already has account)
        # Options: "Autofill with Resume", "Apply Manually", "Use My Last Application"
        try:
            modal_handled = await page.evaluate("""() => {
                // Look for the modal buttons
                const btns = document.querySelectorAll('button, a[role="button"], div[role="button"]');
                for (const btn of btns) {
                    const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                    if (text === 'autofill with resume' || text === 'apply manually') {
                        const r = btn.getBoundingClientRect();
                        return {text: text, x: r.x + r.width / 2, y: r.y + r.height / 2, found: true};
                    }
                }
                return {found: false};
            }""")
            if modal_handled and modal_handled.get("found"):
                btn_text = modal_handled.get("text", "")
                if event_callback:
                    await event_callback("Navigate", "info", f"Found '{btn_text}' modal button, clicking...")
                # Click "Autofill with Resume" or "Apply Manually" (avoid "Use My Last Application")
                await page.mouse.click(modal_handled["x"], modal_handled["y"])
                await asyncio.sleep(5.0)
                state = await _detect_workday_page_state(page)
                if event_callback:
                    await event_callback("Navigate", "success", f"After modal click: state={state}")
        except Exception:
            pass

        # If still on job listing page (agent couldn't click Apply), use trusted Playwright clicks
        if state == "unknown":
            if event_callback:
                await event_callback("Navigate", "info", "Still on job page — clicking Apply with trusted Playwright click...")
            try:
                # Strategy 1: Workday adventure button (data-uxi-element-id)
                apply_selectors = [
                    'a[data-uxi-element-id="Apply_adventureButton"]',
                    '[data-automation-id="adventureButton"]',
                    '[data-automation-id="jobPostingApplyButton"]',
                    'a[role="button"]:has-text("Apply")',
                    'button:has-text("Apply")',
                    'a:has-text("Apply")',
                ]
                apply_clicked = False
                for sel in apply_selectors:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=2000):
                            # Use bounding box + mouse.click for trusted events (bypasses click_filter)
                            box = await btn.bounding_box()
                            if box:
                                cx = box['x'] + box['width'] / 2
                                cy = box['y'] + box['height'] / 2
                                await page.mouse.click(cx, cy)
                                apply_clicked = True
                                if event_callback:
                                    await event_callback("Navigate", "success", f"Clicked Apply via mouse.click ({sel})")
                                await asyncio.sleep(5.0)
                                break
                    except Exception:
                        continue

                if not apply_clicked:
                    # JS fallback: find and click Apply link/button
                    apply_clicked = await page.evaluate("""() => {
                        const els = document.querySelectorAll('a, button, div[role="button"]');
                        for (const el of els) {
                            const text = (el.innerText || el.textContent || '').trim();
                            if (/^Apply$/i.test(text) && el.offsetParent !== null) {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }""")
                    if apply_clicked:
                        if event_callback:
                            await event_callback("Navigate", "info", "Clicked Apply via JS fallback")
                        await asyncio.sleep(5.0)

                if apply_clicked:
                    # Check for "Start Your Application" modal with options
                    try:
                        modal_btn = await page.evaluate("""() => {
                            const btns = document.querySelectorAll('button, a[role="button"], div[role="button"], [data-automation-id="applyManually"]');
                            // Prefer "Autofill with Resume" > "Apply Manually" (avoid "Use My Last Application")
                            const preferred = ['autofill with resume', 'apply manually'];
                            for (const pref of preferred) {
                                for (const btn of btns) {
                                    const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                                    if (text === pref || text.includes(pref)) {
                                        const r = btn.getBoundingClientRect();
                                        return {text: text, x: r.x + r.width / 2, y: r.y + r.height / 2, found: true};
                                    }
                                }
                            }
                            return {found: false};
                        }""")
                        if modal_btn and modal_btn.get("found"):
                            await page.mouse.click(modal_btn["x"], modal_btn["y"])
                            if event_callback:
                                await event_callback("Navigate", "success", f"Clicked '{modal_btn['text']}'")
                            await asyncio.sleep(5.0)
                    except Exception:
                        pass

                    if screenshot_callback:
                        try:
                            ss = await page.screenshot(type="png")
                            await screenshot_callback(ss)
                        except Exception:
                            pass

                    # Re-detect state
                    state = await _detect_workday_page_state(page)
                    if event_callback:
                        await event_callback("Pipeline", "info", f"After Apply click: state={state}")
                else:
                    if event_callback:
                        await event_callback("Navigate", "warning", "Could not find Apply button on Workday page")
            except Exception as e:
                if event_callback:
                    await event_callback("Navigate", "error", f"Apply click error: {e}")

        # Handle resume upload if we landed on upload page
        if state == "upload":
            if event_callback:
                await event_callback("Pipeline", "info", "Resume upload page detected. Uploading...")
            from applicator.workday_handler import upload_file_robust
            await upload_file_robust(page, resume_path, event_callback)
            await asyncio.sleep(3)
            state = await _detect_workday_page_state(page)
            if event_callback:
                await event_callback("Pipeline", "info", f"After upload: {state}")

        # Handle auth (sign in / create account with real Playwright clicks)
        if state == "auth":
            if event_callback:
                await event_callback("Auth", "info", "Auth page detected. Running deterministic auth handler...")
            auth_ok = await _handle_workday_auth(page, event_callback)
            if auth_ok:
                await asyncio.sleep(3)
                state = await _detect_workday_page_state(page)
                if event_callback:
                    await event_callback("Auth", "success" if state == "form" else "info",
                        f"After auth: {state}")

            # Handle email verification if needed
            if state == "verify":
                if event_callback:
                    await event_callback("Auth", "info", "Email verification required...")
                from applicator.email_handler import handle_email_verification, enter_verification_code
                try:
                    context = page.context
                    result = await handle_email_verification(
                        context=context, original_page=page,
                        company_name=company,
                        event_callback=event_callback,
                        screenshot_callback=screenshot_callback,
                    )
                    if result["success"]:
                        if result["method"] == "code" and result["code"]:
                            await enter_verification_code(page, result["code"], event_callback)
                        elif result["method"] == "link":
                            await asyncio.sleep(3)
                            try:
                                await page.reload()
                            except Exception:
                                pass
                            await asyncio.sleep(5)
                        # After verification, try signing in
                        state = await _detect_workday_page_state(page)
                        if state == "auth":
                            await _handle_workday_auth(page, event_callback)
                            await asyncio.sleep(3)
                            state = await _detect_workday_page_state(page)
                    else:
                        if event_callback:
                            await event_callback("Auth", "warning",
                                "Auto-verify failed. Waiting up to 90s for manual verification...")
                        for _ in range(90):
                            await asyncio.sleep(1)
                            state = await _detect_workday_page_state(page)
                            if state != "verify":
                                break
                        if state == "auth":
                            await _handle_workday_auth(page, event_callback)
                            await asyncio.sleep(3)
                            state = await _detect_workday_page_state(page)
                except Exception as e:
                    if event_callback:
                        await event_callback("Auth", "error", f"Verification error: {e}")

        # Handle "Something went wrong" error page — reload and retry
        if state == "error":
            if event_callback:
                await event_callback("Navigate", "warning", "Workday error page detected. Refreshing...")
            try:
                await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(5)
                state = await _detect_workday_page_state(page)
                if event_callback:
                    await event_callback("Navigate", "info", f"After refresh: state={state}")
            except Exception:
                pass

            # If still error after refresh, navigate to the application URL directly
            if state == "error" or state == "unknown":
                if event_callback:
                    await event_callback("Navigate", "info", "Still error. Re-navigating to application URL...")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(5)
                    state = await _detect_workday_page_state(page)
                    if event_callback:
                        await event_callback("Navigate", "info", f"After re-navigate: state={state}")

                    # Handle the Start Your Application modal again
                    if state == "unknown":
                        modal_btn = await page.evaluate("""() => {
                            const btns = document.querySelectorAll('button, a[role="button"], div[role="button"]');
                            for (const btn of btns) {
                                const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                                if (text === 'apply manually') {
                                    const r = btn.getBoundingClientRect();
                                    return {text: text, x: r.x + r.width / 2, y: r.y + r.height / 2, found: true};
                                }
                            }
                            // Try Apply button if no modal
                            const applyLink = document.querySelector('a[data-uxi-element-id="Apply_adventureButton"]');
                            if (applyLink) {
                                const r = applyLink.getBoundingClientRect();
                                return {text: 'apply', x: r.x + r.width / 2, y: r.y + r.height / 2, found: true};
                            }
                            return {found: false};
                        }""")
                        if modal_btn and modal_btn.get("found"):
                            await page.mouse.click(modal_btn["x"], modal_btn["y"])
                            await asyncio.sleep(5)
                            state = await _detect_workday_page_state(page)
                            if state == "auth":
                                await _handle_workday_auth(page, event_callback)
                                await asyncio.sleep(3)
                                state = await _detect_workday_page_state(page)
                except Exception as e:
                    if event_callback:
                        await event_callback("Navigate", "error", f"Re-navigate error: {e}")

        # ====================================================================
        # PHASE 3: Fill multi-step Workday form automatically
        # ====================================================================
        if state == "form":
            if event_callback:
                await event_callback("Form Fill", "info", "On application form. Running Workday form handler...")
            try:
                from applicator.workday_handler import handle_workday_application
                wd_result = await handle_workday_application(
                    page=page,
                    resume_path=resume_path,
                    company=company,
                    role=role,
                    job_description=job_description,
                    event_callback=event_callback,
                    screenshot_callback=screenshot_callback,
                )
                wd_filled = wd_result.get("filled", 0)
                wd_failed = wd_result.get("failed", 0)
                wd_errors = wd_result.get("errors", [])
                if event_callback:
                    await event_callback("Form Fill", "success" if not wd_errors else "info",
                        f"Workday form: {wd_filled} filled, {wd_failed} failed")
                completed = wd_filled > 0
            except Exception as e:
                if event_callback:
                    await event_callback("Form Fill", "error", f"Workday handler error: {e}")
                import traceback
                if event_callback:
                    await event_callback("Form Fill", "info", traceback.format_exc()[:500])

        elif state == "success":
            completed = True
            if event_callback:
                await event_callback("Pipeline", "success", "Application already submitted!")

    else:
        # Non-Workday ATS (Greenhouse, Lever, iCIMS, etc.)
        # The browser-use agent's job is ONLY to navigate + click Apply, so
        # is_successful() is irrelevant — we always attempt form filling.
        total_filled = 0
        total_failed = 0
        max_pages = 8  # safety limit for multi-page forms

        if page:
          # --- SAFETY: If agent didn't click Apply, do it now ---
          try:
              try:
                  current_url = page.url.lower()
              except Exception:
                  current_url = (await page.evaluate("window.location.href")).lower()
              has_apply_in_url = "/apply" in current_url or "/application" in current_url
              if not has_apply_in_url:
                  if event_callback:
                      await event_callback("Navigate", "info", "Not on application form yet — clicking Apply button...")
                  apply_selectors = [
                      # Greenhouse-specific
                      'a[href*="/apply"]',                         # any href containing /apply
                      '[data-qa="btn-apply"]',                    # Greenhouse data-qa
                      '[data-qa="btn-apply-now"]',
                      '#apply_button',                            # Greenhouse legacy id
                      '.btn-apply', 'a.js-btn-apply',
                      'a.btn[href*="apply"]',
                      # Generic apply button text (case insensitive via has-text)
                      'a:has-text("Apply for this job")', 'button:has-text("Apply for this job")',
                      'a:has-text("Apply for this Job")', 'button:has-text("Apply for this Job")',
                      'a:has-text("Apply Now")', 'button:has-text("Apply Now")',
                      'a:has-text("Apply now")', 'button:has-text("Apply now")',
                      'a:text-is("Apply")', 'button:text-is("Apply")',
                      '.apply-button', '[class*="apply-btn"]', '[class*="applyBtn"]',
                      'a.postings-btn',                           # Lever
                      '[data-automation-id="btn-apply"]',
                  ]
                  apply_clicked = False
                  for sel in apply_selectors:
                      try:
                          btn = page.locator(sel).first
                          if await btn.is_visible(timeout=2000):
                              await btn.click(timeout=5000)
                              apply_clicked = True
                              if event_callback:
                                  await event_callback("Navigate", "success", f"Clicked Apply button ({sel})")
                              await asyncio.sleep(3.0)
                              break
                      except Exception:
                          continue

                  # JS fallback: find any visible button/link with "apply" in text
                  if not apply_clicked:
                      try:
                          clicked = await page.evaluate("""() => {
                              const els = [...document.querySelectorAll('a, button, [role="button"]')];
                              for (const el of els) {
                                  if (el.offsetParent === null) continue;
                                  const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                                  const href = (el.href || '').toLowerCase();
                                  if (text === 'apply' || text.startsWith('apply for') || text === 'apply now'
                                      || href.includes('/apply')) {
                                      el.click();
                                      return el.tagName + ': ' + (el.innerText || '').trim().slice(0, 40);
                                  }
                              }
                              return null;
                          }""")
                          if clicked:
                              apply_clicked = True
                              if event_callback:
                                  await event_callback("Navigate", "success", f"Clicked Apply via JS fallback: {clicked}")
                              await asyncio.sleep(3.0)
                      except Exception as e:
                          if event_callback:
                              await event_callback("Navigate", "info", f"JS Apply fallback error: {e}")

                  if not apply_clicked and event_callback:
                      await event_callback("Navigate", "warning", "Could not find Apply button — trying to fill current page")
                  # Take screenshot after clicking Apply
                  if screenshot_callback and apply_clicked:
                      try:
                          ss = await page.screenshot(type="png")
                          await screenshot_callback(ss)
                      except Exception:
                          pass
          except Exception as e:
              if event_callback:
                  await event_callback("Navigate", "info", f"Apply button check error: {e}")

          # === DIRECT PRE-FILL: fill standard personal info fields without LLM ===
          # These never change so we fill them immediately via Playwright keyboard input.
          # This handles both main-page and iframe-embedded forms (Greenhouse embedded boards).
          try:
              pi = _load_personal_info()
              direct_fills = {
                  # label patterns → value
                  "first": pi.get("first_name", "Edrick"),
                  "last": pi.get("last_name", "Chang"),
                  "email": pi.get("email", "eachang@scu.edu"),
                  "phone": pi.get("phone", "4088066495"),
                  "linkedin": pi.get("linkedin_url", "https://linkedin.com/in/edrickchang"),
                  "github": pi.get("github_url", "https://github.com/edrickchang"),
                  "website": pi.get("portfolio_url", ""),
              }
              # Try both main page and all frames
              fill_targets = [page] + [f for f in page.frames if f != page.main_frame]
              for fill_ctx in fill_targets:
                  try:
                      inputs = await fill_ctx.evaluate("""() => {
                          const results = [];
                          for (const el of document.querySelectorAll('input[type="text"], input[type="email"], input[type="tel"], input:not([type])')) {
                              if (el.offsetParent === null) continue;
                              if (el.value && el.value.trim()) continue;  // already filled
                              const label = (el.getAttribute('aria-label') || el.placeholder || el.name || el.id || '').toLowerCase();
                              const r = el.getBoundingClientRect();
                              results.push({label, x: r.x + r.width/2, y: r.y + r.height/2,
                                  name: el.name || '', id: el.id || '', placeholder: el.placeholder || ''});
                          }
                          return results;
                      }""")
                      for inp in (inputs or []):
                          lbl = inp.get("label", "")
                          val = None
                          if any(k in lbl for k in ["first", "fname", "given"]):
                              val = direct_fills["first"]
                          elif any(k in lbl for k in ["last", "lname", "surname", "family"]):
                              val = direct_fills["last"]
                          elif "email" in lbl:
                              val = direct_fills["email"]
                          elif any(k in lbl for k in ["phone", "mobile", "cell"]):
                              val = direct_fills["phone"]
                          elif "linkedin" in lbl:
                              val = direct_fills["linkedin"]
                          elif "github" in lbl:
                              val = direct_fills["github"]
                          elif any(k in lbl for k in ["website", "portfolio", "url"]):
                              val = direct_fills.get("website", "")
                          if val:
                              try:
                                  await fill_ctx.mouse.click(inp["x"], inp["y"])
                                  await asyncio.sleep(0.1)
                                  await fill_ctx.keyboard.press("Control+a")
                                  await fill_ctx.keyboard.type(val, delay=20)
                                  await fill_ctx.keyboard.press("Tab")
                                  if event_callback:
                                      await event_callback("Pre-Fill", "info", f"Filled '{lbl[:30]}' = '{val[:30]}'")
                              except Exception as pfe:
                                  if event_callback:
                                      await event_callback("Pre-Fill", "info", f"Skip '{lbl[:30]}': {pfe}")
                  except Exception:
                      continue
          except Exception as pre_e:
              if event_callback:
                  await event_callback("Pre-Fill", "info", f"Pre-fill error: {pre_e}")

          for page_num in range(max_pages):
            try:
                # Scroll down to load lazy content (Greenhouse loads sections on scroll)
                try:
                    page_height = await page.evaluate("document.body.scrollHeight")
                    for scroll_pos in range(0, page_height + 900, 900):
                        await page.evaluate(f"window.scrollTo(0, {scroll_pos})")
                        await asyncio.sleep(0.4)
                    await page.evaluate("window.scrollTo(0, 0)")
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

                # Extract fields from main page
                fields = await page.evaluate(JS_EXTRACT_FIELDS)
                form_ctx = page

                # If no fields on main page, check iframes (Greenhouse, etc.)
                if len(fields) == 0:
                    for frame in page.frames:
                        if frame == page.main_frame:
                            continue
                        try:
                            frame_fields = await frame.evaluate(JS_EXTRACT_FIELDS)
                            if len(frame_fields) > len(fields):
                                fields = frame_fields
                                form_ctx = frame
                        except Exception:
                            continue

                # If still nothing on first page, wait for JS-heavy pages
                if len(fields) == 0 and page_num == 0:
                    if event_callback:
                        await event_callback("Form Fill", "info", "No fields yet, waiting for page to render...")
                    await asyncio.sleep(5.0)
                    fields = await page.evaluate(JS_EXTRACT_FIELDS)
                    form_ctx = page
                    if len(fields) == 0:
                        for frame in page.frames:
                            if frame == page.main_frame:
                                continue
                            try:
                                frame_fields = await frame.evaluate(JS_EXTRACT_FIELDS)
                                if len(frame_fields) > len(fields):
                                    fields = frame_fields
                                    form_ctx = frame
                            except Exception:
                                continue

                # Filter out non-dict entries (JS may return strings/nulls)
                fields = [f for f in fields if isinstance(f, dict)]

                # Sanity check: if we got a huge number of fields, we're probably
                # on the JD page, not the application form. Skip filling.
                if len(fields) > 200:
                    if event_callback:
                        await event_callback("Form Fill", "warning",
                            f"Page {page_num + 1}: {len(fields)} fields found — too many, likely not an application form. Skipping.")
                    break

                if len(fields) == 0:
                    if page_num == 0 and event_callback:
                        await event_callback("Form Fill", "info", "No form fields found on this page")
                    break

                if event_callback:
                    await event_callback("Form Fill", "info",
                        f"Page {page_num + 1}: {len(fields)} fields found. Filling...")

                # Map and fill with retry passes (up to 2 per page)
                page_filled = 0
                for pass_num in range(2):
                    try:
                        if pass_num > 0:
                            # Re-extract and filter to unfilled only
                            await asyncio.sleep(1.5)
                            re_fields = await form_ctx.evaluate(JS_EXTRACT_FIELDS)
                            fields = []
                            for f in re_fields:
                                if not isinstance(f, dict):
                                    continue
                                val = f.get("value", "")
                                tag = f.get("tag", "")
                                if not str(val).strip():
                                    fields.append(f)
                                elif tag == "select" and val in ("", "0", "--"):
                                    fields.append(f)
                            if len(fields) == 0:
                                break

                        if event_callback:
                            field_labels = [f.get("label") or f.get("name") or f.get("placeholder","?") for f in fields[:8]]
                            await event_callback("LLM Map", "info", f"Sending {len(fields)} fields to LLM: {field_labels}")
                        mappings = await asyncio.to_thread(map_fields_to_profile, fields, job_description, company, role)
                        # Ensure all mappings are dicts (LLM may return strings)
                        mappings = [m for m in mappings if isinstance(m, dict)]
                        if event_callback:
                            await event_callback("LLM Map", "info", f"LLM returned {len(mappings)} mappings")
                        if pass_num > 0:
                            mappings = [m for m in mappings if m.get("action") != "skip" or m.get("value")]
                        if not mappings:
                            if event_callback:
                                await event_callback("LLM Map", "error", "LLM returned no mappings — check API key or rate limit")
                            break

                        result = await fill_form(form_ctx, mappings, resume_path,
                            event_callback=event_callback, screenshot_page=page)
                        if isinstance(result, dict):
                            page_filled += result.get("filled", 0)
                            total_failed += result.get("failed", 0)
                            if result.get("failed", 0) == 0:
                                break
                        else:
                            break
                    except Exception as e:
                        import traceback as _tb
                        tb_str = _tb.format_exc()
                        err_str = str(e)
                        if event_callback:
                            await event_callback("Form Fill", "error", f"Page {page_num+1} pass {pass_num+1}: {err_str[:200]}")
                            # Surface rate limit / auth errors clearly
                            if any(k in err_str.lower() for k in ["rate", "429", "quota", "api key", "auth", "context"]):
                                await event_callback("LLM Error", "error", f"LLM provider failed: {err_str[:300]}")
                            else:
                                await event_callback("Form Fill", "info", f"Traceback: {tb_str[:600]}")
                        break

                total_filled += page_filled

                # Screenshot after filling this page
                if screenshot_callback:
                    try:
                        ss = await page.screenshot(type="png")
                        await screenshot_callback(ss)
                    except Exception:
                        pass

                # Try to advance to the next page (Next/Continue/Submit)
                await asyncio.sleep(1.0)
                next_clicked = False
                next_selectors = [
                    'button:has-text("Next")', 'button:has-text("Continue")',
                    'button:has-text("Save and Continue")',
                    'input[type="submit"][value*="Next" i]',
                    'input[type="submit"][value*="Continue" i]',
                    '[data-automation-id="bottom-navigation-next-button"]',
                    # NOTE: Do NOT include Submit Application / #submit_app here.
                    # The user must review and submit manually. Auto-clicking Submit
                    # causes loops on single-page forms (Greenhouse, Lever).
                ]
                for sel in next_selectors:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=1500):
                            await btn.scroll_into_view_if_needed(timeout=3000)
                            await asyncio.sleep(0.5)
                            await btn.click(timeout=5000)
                            next_clicked = True
                            if event_callback:
                                await event_callback("Navigate", "info", f"Clicked '{sel}' to advance")
                            await asyncio.sleep(3.0)
                            break
                    except Exception:
                        continue

                # Also check iframes for Next buttons
                if not next_clicked:
                    for frame in page.frames:
                        if frame == page.main_frame:
                            continue
                        for sel in next_selectors[:6]:
                            try:
                                btn = frame.locator(sel).first
                                if await btn.is_visible(timeout=1000):
                                    await btn.click(timeout=5000)
                                    next_clicked = True
                                    if event_callback:
                                        await event_callback("Navigate", "info", "Clicked Next in iframe")
                                    await asyncio.sleep(3.0)
                                    break
                            except Exception:
                                continue
                        if next_clicked:
                            break

                if not next_clicked:
                    if event_callback:
                        await event_callback("Form Fill", "info",
                            f"No Next/Continue button found. Filled {total_filled} fields total.")
                    break

            except Exception as e:
                if event_callback:
                    await event_callback("Form Fill", "error", f"Page {page_num + 1} error: {e}")
                import traceback
                if event_callback:
                    await event_callback("Form Fill", "info", traceback.format_exc()[:500])
                break

          completed = total_filled > 0

    # Take final screenshot
    if screenshot_callback and page:
        try:
            ss = await page.screenshot(type="png")
            await screenshot_callback(ss)
        except Exception:
            pass

    return {
        "browser": browser_pw,
        "page": page,
        "completed": completed,
        "summary": {
            "steps": steps,
            "agent_done": history.is_done() if history else False,
            "agent_successful": (history.is_successful() or False) if history else False,
            "error": agent_error,
            "final_result": history.final_result() if history else None,
        },
    }


async def close_browser_agent():
    """Clean up browser-use browser and Playwright CDP connection."""
    global _bu_browser, _pw_for_cdp
    if _bu_browser:
        try:
            await _bu_browser.close()
        except Exception:
            pass
        _bu_browser = None
    if _pw_for_cdp:
        try:
            await _pw_for_cdp.stop()
        except Exception:
            pass
        _pw_for_cdp = None


JS_EXTRACT_FIELDS = """
() => {
    const fields = [];
    // Find the form with the most inputs (not just the first form)
    const allForms = document.querySelectorAll('form');
    let form = document.body;
    let maxInputs = 0;
    for (const f of allForms) {
        const count = f.querySelectorAll('input, textarea, select').length;
        if (count > maxInputs) {
            maxInputs = count;
            form = f;
        }
    }
    // If the best form has very few fields, search the whole document
    if (maxInputs < 3) form = document.body;

    function getLabel(el) {
        // 1. <label for="id">
        if (el.id) {
            const label = document.querySelector('label[for="' + el.id + '"]');
            if (label) return label.innerText.trim();
        }
        // 2. Closest parent with label-like element (broad search)
        let parent = el.closest('.application-question, .field, .form-group, .application-field, .field-wrap, li, .form-field');
        if (parent) {
            const lbl = parent.querySelector('label, .application-label, .field-label, legend, .label');
            if (lbl) return lbl.innerText.trim();
        }
        // 3. Previous sibling label
        let prev = el.previousElementSibling;
        while (prev) {
            if (prev.tagName === 'LABEL' || prev.classList.contains('label')) {
                return prev.innerText.trim();
            }
            prev = prev.previousElementSibling;
        }
        // 4. Parent's previous sibling (Greenhouse pattern: label then field-wrap)
        if (el.parentElement) {
            let parentPrev = el.parentElement.previousElementSibling;
            if (parentPrev && parentPrev.tagName === 'LABEL') {
                return parentPrev.innerText.trim();
            }
        }
        // 5. Walk up to find any label within 3 levels
        let ancestor = el.parentElement;
        for (let i = 0; i < 3 && ancestor; i++) {
            const lbl = ancestor.querySelector('label');
            if (lbl && lbl.innerText.trim()) return lbl.innerText.trim();
            ancestor = ancestor.parentElement;
        }
        // 6. aria-label
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
        // 7. placeholder
        if (el.placeholder) return el.placeholder;
        // 8. name attribute cleaned up
        if (el.name) return el.name.replace(/[\\[\\]_-]/g, ' ').replace(/question \\d+/i, 'question').trim();
        return '';
    }

    function getSelector(el) {
        // Prefer name (most reliable for form fields)
        if (el.name) {
            // For radio/checkbox, include value to make selector unique
            if ((el.type === 'radio' || el.type === 'checkbox') && el.value) {
                return '[name="' + el.name + '"][value="' + el.value + '"]';
            }
            return '[name="' + el.name + '"]';
        }
        if (el.id) return '#' + CSS.escape(el.id);
        // Build a unique path walking up the DOM
        const tag = el.tagName.toLowerCase();
        const parts = [];
        let current = el;
        while (current && current !== document.body) {
            let seg = current.tagName.toLowerCase();
            if (current.id) {
                parts.unshift('#' + CSS.escape(current.id));
                break;
            }
            const parent = current.parentElement;
            if (parent) {
                const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
                if (siblings.length > 1) {
                    const idx = siblings.indexOf(current) + 1;
                    seg += ':nth-of-type(' + idx + ')';
                }
            }
            parts.unshift(seg);
            current = current.parentElement;
        }
        return parts.join(' > ');
    }

    // Process inputs, textareas, selects
    const elements = form.querySelectorAll('input, textarea, select');
    for (const el of elements) {
        const type = el.type || el.tagName.toLowerCase();

        // Skip hidden, submit, button, search, and cookie-related inputs
        if (['hidden', 'submit', 'button', 'search', 'image'].includes(type)) continue;

        // Skip invisible elements that are clearly not form fields (cookie checkboxes etc)
        if (el.name && (el.name.includes('ot-group') || el.name.includes('vendor') || el.name.includes('cookie'))) continue;
        if (el.id && (el.id.includes('ot-') || el.id.includes('vendor') || el.id.includes('cookie'))) continue;

        // Skip elements with 0 dimensions that aren't file inputs
        if (type !== 'file' && el.offsetParent === null && type !== 'checkbox' && type !== 'radio') continue;

        const field = {
            selector: getSelector(el),
            tag: el.tagName.toLowerCase(),
            type: type,
            name: el.name || '',
            label: getLabel(el),
            required: el.required || el.getAttribute('aria-required') === 'true',
            value: el.value || '',
            placeholder: el.placeholder || '',
            options: [],
        };

        // Collect options for select
        if (el.tagName === 'SELECT') {
            field.options = Array.from(el.options).map(o => ({
                value: o.value,
                text: o.text.trim()
            }));
        }

        // For radio/checkbox, find the actual question text and option text
        if (type === 'radio' || type === 'checkbox') {
            const wrapper = el.closest('li, div, label');
            const optionText = wrapper ? wrapper.innerText.trim() : '';

            // Walk up to find question text (a sibling branch that doesn't contain the radio)
            let questionText = '';
            let ancestor = el;
            for (let i = 0; i < 10 && ancestor; i++) {
                ancestor = ancestor.parentElement;
                if (!ancestor || ancestor === document.body || ancestor === form) break;
                for (const child of ancestor.children) {
                    if (child.contains(el)) continue;
                    // Skip sibling field groups that contain other inputs
                    if (child.querySelector && child.querySelector('input, select, textarea')) continue;
                    const t = (child.innerText || '').trim();
                    if (t.length > 10 && t !== 'Yes' && t !== 'No' && t !== optionText) {
                        questionText = t.substring(0, 200);
                        break;
                    }
                }
                if (questionText) break;
            }

            if (questionText) {
                field.label = questionText + ' :: ' + optionText;
            } else if (optionText && optionText !== field.label) {
                field.label = field.label + ' :: ' + optionText;
            }
        }

        fields.push(field);
    }

    // === Workday data-automation-id fields ===
    const wdFields = form.querySelectorAll('[data-automation-id]');
    for (const el of wdFields) {
        const dataid = el.getAttribute('data-automation-id') || '';
        if (!dataid.startsWith('formField-') && !dataid.startsWith('multiSelectContainer')
            && dataid !== 'countryDropdown' && dataid !== 'phone'
            && !dataid.startsWith('legalCountry') && !dataid.startsWith('addressSection'))
            continue;
        if (el.offsetParent === null) continue;
        // Skip if we already captured an input inside this container
        const hasInput = el.querySelector('input, textarea, select');
        const alreadyCaptured = hasInput && fields.some(f => el.contains(document.querySelector(f.selector)));
        if (alreadyCaptured) continue;

        const label = el.querySelector('label');
        const labelText = label ? label.innerText.trim() : '';
        const btn = el.querySelector('button');
        const btnText = btn ? btn.innerText.trim() : '';
        const input = el.querySelector('input');
        fields.push({
            selector: '[data-automation-id="' + dataid + '"]',
            tag: 'div',
            type: btn ? 'workday-dropdown' : (input ? input.type : 'workday-field'),
            name: dataid,
            label: labelText || dataid.replace('formField-', '').replace(/-/g, ' '),
            required: el.querySelector('[required]') !== null || el.querySelector('[aria-required="true"]') !== null,
            value: btnText || (input ? input.value : ''),
            placeholder: input ? (input.placeholder || '') : '',
            options: [],
        });
    }

    // === React Select dropdowns ===
    const reactSelects = form.querySelectorAll('[class*="react-select"], [class*="css-"][role="combobox"]');
    for (const el of reactSelects) {
        if (el.offsetParent === null) continue;
        const container = el.closest('.field, .form-group, .application-field, li') || el.parentElement;
        const label = container ? container.querySelector('label') : null;
        const labelText = label ? label.innerText.trim() : (el.getAttribute('aria-label') || '');
        const valueEl = el.querySelector('[class*="singleValue"], [class*="placeholder"]');
        fields.push({
            selector: el.id ? '#' + CSS.escape(el.id) : '[class*="react-select"]',
            tag: 'div',
            type: 'react-select',
            name: el.getAttribute('name') || '',
            label: labelText,
            required: el.getAttribute('aria-required') === 'true',
            value: valueEl ? valueEl.innerText.trim() : '',
            placeholder: '',
            options: [],
        });
    }

    // === contenteditable divs ===
    const editables = form.querySelectorAll('[contenteditable="true"]');
    for (const el of editables) {
        if (el.offsetParent === null) continue;
        const parent = el.closest('.field, .form-group, li, .application-field') || el.parentElement;
        const label = parent ? parent.querySelector('label') : null;
        fields.push({
            selector: el.id ? '#' + CSS.escape(el.id) : '[contenteditable="true"]',
            tag: 'div',
            type: 'contenteditable',
            name: el.getAttribute('name') || el.getAttribute('data-placeholder') || '',
            label: label ? label.innerText.trim() : (el.getAttribute('aria-label') || ''),
            required: el.getAttribute('aria-required') === 'true',
            value: el.innerText.trim(),
            placeholder: el.getAttribute('data-placeholder') || el.getAttribute('placeholder') || '',
            options: [],
        });
    }

    // === role="combobox" and role="listbox" custom dropdowns ===
    const comboboxes = form.querySelectorAll('[role="combobox"], [role="listbox"]');
    for (const el of comboboxes) {
        if (el.offsetParent === null) continue;
        // Skip if already captured via React Select or Workday
        const already = fields.some(f => {
            try { return el.matches(f.selector) || el.closest(f.selector); } catch(e) { return false; }
        });
        if (already) continue;
        const container = el.closest('.field, .form-group, .application-field, li, [class*="formField"]') || el.parentElement;
        const lbl = container ? container.querySelector('label') : null;
        const lblText = lbl ? lbl.innerText.trim() : (el.getAttribute('aria-label') || '');
        const input = el.querySelector('input') || (el.tagName === 'INPUT' ? el : null);
        fields.push({
            selector: el.id ? '#' + CSS.escape(el.id) : '[role="' + el.getAttribute('role') + '"]',
            tag: el.tagName.toLowerCase(),
            type: 'custom-dropdown',
            name: el.getAttribute('name') || (input ? input.name : '') || '',
            label: lblText,
            required: el.getAttribute('aria-required') === 'true',
            value: input ? input.value : el.innerText.trim().substring(0, 100),
            placeholder: input ? (input.placeholder || '') : '',
            options: [],
        });
    }

    // === intl-tel-input (ITI) phone country dropdowns ===
    const itiContainers = form.querySelectorAll('.iti, [class*="intl-tel"], .iti__flag-container');
    for (const el of itiContainers) {
        if (el.offsetParent === null) continue;
        const flagBtn = el.querySelector('.iti__selected-flag, .iti__flag-container');
        if (!flagBtn) continue;
        const already = fields.some(f => {
            try { return el.contains(document.querySelector(f.selector)); } catch(e) { return false; }
        });
        if (already) continue;
        fields.push({
            selector: '.iti__selected-flag',
            tag: 'div',
            type: 'iti-country',
            name: 'phone_country',
            label: 'Phone Country',
            required: false,
            value: flagBtn.getAttribute('title') || '',
            placeholder: '',
            options: [],
        });
    }

    // === Upload zones with labels containing "resume" or "cv" ===
    const uploadLabels = form.querySelectorAll('label, .field-label, .upload-label, [class*="upload"], [class*="dropzone"], [class*="file-upload"], [data-automation-id*="upload"], [data-automation-id*="file"]');
    for (const el of uploadLabels) {
        if (el.offsetParent === null) continue;
        const text = (el.innerText || '').toLowerCase();
        if (!text.match(/resume|cv|cover.?letter|transcript|upload.*file|attach/i)) continue;
        // Check if there's already a file input captured nearby
        const container = el.closest('.field, .form-group, li, .application-field, [class*="upload"]') || el.parentElement;
        const fileInput = container ? container.querySelector('input[type="file"]') : null;
        if (fileInput) continue; // Already captured by standard input scan
        const already = fields.some(f => f.type === 'file' && f.label.toLowerCase().includes('resume'));
        if (already) continue;
        const btn = container ? container.querySelector('button, a, [role="button"]') : null;
        fields.push({
            selector: btn ? getSelector(btn) : getSelector(el),
            tag: 'div',
            type: 'upload-zone',
            name: 'resume_upload',
            label: el.innerText.trim().substring(0, 80),
            required: true,
            value: '',
            placeholder: '',
            options: [],
        });
    }

    // === Greenhouse custom select__container dropdowns ===
    const ghCustomSelects = form.querySelectorAll('[class*="select__container"], [class*="select__control"]');
    for (const el of ghCustomSelects) {
        if (el.offsetParent === null) continue;
        // Get the container (could be select__container itself or parent)
        const container = el.closest('[class*="select__container"]') || el;
        // Skip if already captured
        const alreadyCaptured = fields.some(f => {
            try {
                const fEl = document.querySelector(f.selector);
                return fEl && (container.contains(fEl) || fEl.contains(container));
            } catch(e) { return false; }
        });
        if (alreadyCaptured) continue;

        // Find label by walking up to the field container
        const fieldContainer = container.closest('li, .field, .form-group, .question, .select-field, [class*="application-question"]') || container.parentElement;
        const lbl = fieldContainer ? fieldContainer.querySelector('label') : null;
        const lblText = lbl ? lbl.innerText.trim() : '';

        // Get current displayed value
        const singleVal = container.querySelector('[class*="single-value"], [class*="singleValue"]');
        const placeholder = container.querySelector('[class*="placeholder"]');
        const displayText = singleVal ? singleVal.innerText.trim() : (placeholder ? placeholder.innerText.trim() : '');
        const isPlaceholder = !singleVal || displayText.toLowerCase().startsWith('select');

        // Build a unique selector
        let selector = '';
        if (fieldContainer && fieldContainer.id) {
            selector = '#' + CSS.escape(fieldContainer.id) + ' [class*="select__container"]';
        } else if (lbl && lbl.getAttribute('for')) {
            selector = '[id="' + lbl.getAttribute('for') + '"]';
        } else {
            // Use nth-of-type based on index among all select__container elements
            const allGHSelects = Array.from(form.querySelectorAll('[class*="select__container"]'));
            const idx = allGHSelects.indexOf(container);
            selector = '[class*="select__container"]:nth-of-type(' + (idx + 1) + ')';
        }

        const isRequired = fieldContainer ? (fieldContainer.querySelector('[aria-required="true"]') !== null || (lbl && lbl.innerText.includes('*'))) : false;

        fields.push({
            selector: selector,
            tag: 'div',
            type: 'greenhouse-custom-select',
            name: lblText.toLowerCase().replace(/[^a-z0-9]/g, '_').substring(0, 50),
            label: lblText,
            required: isRequired,
            value: isPlaceholder ? '' : displayText,
            placeholder: placeholder ? placeholder.innerText.trim() : 'Select...',
            options: [],
        });
    }

    // === Workday formField-* wrapper divs (interactive ones not yet captured) ===
    const wdWrappers = form.querySelectorAll('[data-automation-id^="formField-"]');
    for (const el of wdWrappers) {
        if (el.offsetParent === null) continue;
        const dataid = el.getAttribute('data-automation-id');
        // Skip if already captured
        if (fields.some(f => f.selector === '[data-automation-id="' + dataid + '"]')) continue;
        // Only add if it has interactive content
        const hasInteractive = el.querySelector('input, textarea, select, button, [role="button"], [role="combobox"], [role="listbox"]');
        if (!hasInteractive) continue;
        const label = el.querySelector('label');
        const labelText = label ? label.innerText.trim() : dataid.replace('formField-', '').replace(/-/g, ' ');
        const btn = el.querySelector('button');
        const input = el.querySelector('input');
        fields.push({
            selector: '[data-automation-id="' + dataid + '"]',
            tag: 'div',
            type: btn ? 'workday-dropdown' : (input ? input.type : 'workday-field'),
            name: dataid,
            label: labelText,
            required: el.querySelector('[required]') !== null || el.querySelector('[aria-required="true"]') !== null,
            value: btn ? btn.innerText.trim() : (input ? input.value : ''),
            placeholder: input ? (input.placeholder || '') : '',
            options: [],
        });
    }

    // === Interactive [data-automation-id] elements (buttons, inputs, dropdowns) ===
    const wdInteractive = form.querySelectorAll('[data-automation-id]:is(button, input, select, textarea, [role="button"], [role="combobox"])');
    for (const el of wdInteractive) {
        if (el.offsetParent === null) continue;
        const dataid = el.getAttribute('data-automation-id') || '';
        // Skip non-form elements (navigation, utility, chrome)
        if (['click_filter', 'uiAction', 'Apply',
             'navigationItem', 'utilityMenuButton', 'settingsButton',
             'headerTitle', 'signOut', 'signIn', 'searchButton',
             'globalSearchButton', 'notificationButton', 'profileImage',
             'candidate-home', 'Candidate Home', 'Search for Job',
             'inboxButton', 'tasksButton', 'helpButton',
        ].some(s => dataid.includes(s))) continue;
        // Skip elements in the header/nav bar area
        if (el.closest('header, nav, [role="banner"], [role="navigation"], [data-automation-id="headerWrapper"]')) continue;
        // Skip if already captured
        if (fields.some(f => f.selector.includes(dataid))) continue;
        const container = el.closest('[data-automation-id^="formField-"]') || el.parentElement;
        const label = container ? container.querySelector('label') : null;
        fields.push({
            selector: '[data-automation-id="' + dataid + '"]',
            tag: el.tagName.toLowerCase(),
            type: el.tagName === 'BUTTON' || el.getAttribute('role') === 'button' ? 'workday-dropdown' : (el.type || 'text'),
            name: dataid,
            label: label ? label.innerText.trim() : dataid.replace(/-/g, ' '),
            required: el.required || el.getAttribute('aria-required') === 'true',
            value: el.value || el.innerText.trim().substring(0, 100),
            placeholder: el.placeholder || '',
            options: [],
        });
    }

    return fields;
}
"""


def _get_llm_client(provider="gemini"):
    """Get LLM client.
    Priority: ollama (local DGX) > gemini > cerebras > groq.
    Set OLLAMA_MODEL in .env to activate local inference.
    """
    if provider == "ollama":
        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        return OpenAI(base_url=base, api_key="ollama", timeout=120.0)
    if provider == "groq":
        return OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=os.getenv("GROQ_API_KEY"),
            timeout=30.0,
        )
    if provider == "cerebras":
        return OpenAI(
            base_url="https://api.cerebras.ai/v1",
            api_key=os.getenv("CEREBRAS_API_KEY"),
            timeout=30.0,
        )
    # Default: Gemini via OpenAI-compatible endpoint (1M context, free tier)
    return OpenAI(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=os.getenv("GEMINI_API_KEY"),
        timeout=60.0,
    )

# Model names per provider
_OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL", "qwen2.5:72b")   # local DGX Spark (no limits)
_GEMINI_MODEL   = "gemini-2.0-flash"                          # 1M context, 1500 req/day free
_LLM_MODEL      = "qwen-3-235b-a22b-instruct-2507"            # Cerebras (8K ctx fallback)
_GROQ_MODEL     = "llama-3.3-70b-versatile"                   # Groq last resort


def _parse_json_response(text: str) -> list:
    """Parse JSON from LLM response, handling markdown fences and think tags."""
    # Strip <think>...</think> tags aggressively (Qwen 3) - handle partial, nested, unclosed
    # First pass: remove complete think blocks (greedy to handle nested)
    while '<think>' in text and '</think>' in text:
        text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.DOTALL)
    # Handle unclosed <think> tags (model cut off mid-think)
    text = re.sub(r'<think>[\s\S]*$', '', text, flags=re.DOTALL)
    # Handle orphan </think> at start
    text = re.sub(r'^[\s\S]*?</think>', '', text, flags=re.DOTALL)
    # Strip any remaining think tags
    text = re.sub(r'</?think>', '', text)
    # Strip markdown code fences
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()
    # Find the JSON array
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1:
        text = text[start:end + 1]
    parsed = json.loads(text)
    return [item for item in parsed if isinstance(item, dict)]


def map_fields_to_profile(
    fields: list[dict],
    job_description: str,
    company: str,
    role: str,
    cover_letter_text: str = "",
) -> list[dict]:
    """Send extracted fields to LLM, get back field->value mappings."""
    client = _get_llm_client()

    # Trim fields to reduce token usage - only send what the LLM needs
    slim_fields = []
    for f in fields:
        slim = {
            "selector": f.get("selector", ""),
            "tag": f.get("tag", ""),
            "type": f.get("type", ""),
            "label": f.get("label", "") or f.get("name", "") or f.get("dataid", ""),
            "required": f.get("required", True),
        }
        if f.get("options"):
            slim["options"] = [
                (o.get("text", "") if isinstance(o, dict) else str(o))
                for o in f["options"]
                if (o.get("value") if isinstance(o, dict) else o)
            ]
        if f.get("placeholder"):
            slim["placeholder"] = f["placeholder"]
        slim_fields.append(slim)

    personal_info = _load_personal_info()
    known_values = _build_known_values(personal_info)

    # Cover letter instructions
    if cover_letter_text:
        cover_letter_instruction = f'- Cover letter / "anything else" fields: If REQUIRED, fill with the provided cover letter text. If optional, skip.\n  Cover letter text: "{cover_letter_text[:500]}"'
    else:
        cover_letter_instruction = '- Cover letter / additional information / "anything else" / "Is there anything else you would like us to know": ALWAYS skip (action "skip") - leave blank. NEVER paste resume content into text areas.'

    # "How did you hear" preference
    how_heard = personal_info.get("how_did_you_hear", "LinkedIn")

    prompt = f"""You are a form-filling assistant. Given form fields and a candidate profile, return a JSON array mapping each field to its value.

CRITICAL RULE: You MUST fill EVERY field. Do NOT skip any field unless it is a cover letter/additional info field that is not required. If you are unsure how to answer a question, use your best judgment based on the candidate's profile, resume, and context. NEVER leave a required field empty.

CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

KNOWN VALUES (use these exactly when the field matches):
{known_values}
{cover_letter_instruction}

{WRITING_STYLE}

FIELD-SPECIFIC RULES:
- Phone country / country code dropdowns: select "United States" or "US (+1)" or the closest match
- Graduation year / "what year will you graduate": "2028"
- Internship or co-op experience: "No" or "0" (candidate has no prior internship/co-op experience)
- Currently employed / current company / current organization / current employer: The candidate is NOT currently employed. LEAVE BLANK. Do not type anything.
- "Are you currently employed": "No"
- Previous employer / Has had previous job: "No"
- For location / "Current location" fields: use "Santa Clara, CA". If it is an autocomplete dropdown, type "Santa Clara" and select the suggestion.
- Years of experience: "0"
- Location / Current location / City: "Santa Clara, CA". For autocomplete location fields, type "Santa Clara" and select the suggestion containing "Santa Clara".
- For any referral question: ALWAYS answer "No" - the candidate was NOT referred by an employee
- For "how did you hear about us": "{how_heard}"
- For open-ended text questions (why this company, why interested, tell us about yourself, etc.): write authentic answers connecting the candidate's experience to {company} and the {role} role. Keep under 150 words.
- For 'How did you hear about us' or similar: ALWAYS answer 'Job Board' or select the closest option like 'Job Board', 'Online', 'Internet', or 'Other'.
- For 'Have you previously worked/applied here': ALWAYS answer 'No'.
- For 'Country': ALWAYS answer 'United States' or select 'US' / 'USA' / 'United States'.
- For 'State' or 'Which state are you currently a resident in' or any state/region dropdown: use action "select" with value "California".
- For EEO/demographic questions (gender, race, veteran, disability): use the values from KNOWN VALUES or select 'Prefer not to say' / 'I do not wish to answer' if available.
- IMPORTANT: For any <select> dropdown element, ALWAYS use action "select" (NOT "fill"). Check the field type in the form fields JSON.
- For referral questions: ALWAYS answer 'No'.
- For dropdowns (including custom dropdowns and greenhouse-custom-select): use action "select" with the EXACT text of the option you want. Use common phrasing (e.g. 'United States' not 'US', 'No' not 'N/A', 'Yes' not 'Y').
- For radio buttons: use action "click" with the selector of the CORRECT option (e.g. the Yes or No radio button).
- For checkboxes: use action "click" to check/toggle the checkbox.
- For file inputs (resume/CV): use action "upload_file" with value "resume". ONLY upload to inputs labeled resume or CV.
- For file inputs asking for transcript: use action "upload_file" with value "transcript".
- For file inputs labeled "cover letter", "writing sample", "additional document", or anything that is NOT resume/CV/transcript: ALWAYS use action "skip". Do NOT upload the resume to these fields.
- CRITICAL: For cover letter fields, "additional information" textareas, or "anything else you'd like us to know" fields: ALWAYS use action "skip". Do NOT paste resume content into these fields.
- CRITICAL: Do NOT put resume text into ANY textarea field. Resumes are uploaded via file inputs only.
- For "Do you have prior internship or co-op experience": select "No" (candidate has hackathon project experience but no formal internship/co-op).
- For "What year will you graduate": select "2028".
- For "Are you currently authorized to work in the United States": click "Yes".
- For "Will you require employer sponsorship": select "No".
- For "Are you able to relocate": select "Yes".
- For "What intern season are you interested in": select "Summer" or the closest available option.
- For "Which onsite location would you like to apply to": select the FIRST available option (any location works).
- For "I understand that this position requires me to work on-site": click/check "Yes".
- For any question you don't have information for: use your BEST JUDGMENT based on the candidate profile and job context. Select the most reasonable option. NEVER leave a required field empty.
- For any question you don't have an exact answer for: use context from the candidate profile and job description to give a reasonable answer. Do NOT skip it. NEVER leave a required field empty.

Company: {company}
Role: {role}
Job Description: {job_description[:1500]}

FORM FIELDS:
{json.dumps(slim_fields, indent=2)}

Return ONLY a JSON array. Each element must have exactly these keys:
- "selector": the CSS selector (from input)
- "action": one of "fill", "select", "click", "upload_file", "skip"
- "value": the value to fill/select, or file path for upload_file, or empty string for skip

CRITICAL: NEVER interact with navigation elements, menus, or settings buttons. Skip ANY field with a selector containing "navigationItem", "utilityMenu", "settingsButton", "searchButton", "signOut", "headerWrapper", "Candidate Home", "Search for Job". These are page chrome, not form fields.

REMEMBER: Fill EVERY actual form field. Only use "skip" for optional cover letter / additional info fields and navigation elements. Do NOT include any explanation, only the JSON array."""

    # Retry with exponential backoff for 429 rate limit errors
    # NOTE: This is a sync function, so uses time.sleep(). Callers should run
    # this in a thread pool (asyncio.to_thread) to avoid blocking the event loop.
    import time as _time

    # Build provider list. Ollama (local DGX) goes first if OLLAMA_MODEL is set.
    providers = []
    if os.getenv("OLLAMA_MODEL"):
        providers.append(("ollama", _OLLAMA_MODEL, _get_llm_client("ollama")))
    providers += [
        ("gemini",   _GEMINI_MODEL, _get_llm_client("gemini")),
        ("cerebras", _LLM_MODEL,    _get_llm_client("cerebras")),
        ("groq",     _GROQ_MODEL,   _get_llm_client("groq")),
    ]
    last_error = None
    for provider_name, model, llm_client in providers:
        max_retries = 3 if provider_name == "cerebras" else 2
        for attempt in range(max_retries):
            try:
                msgs = [
                    {"role": "system", "content": "You return only valid JSON arrays. No markdown, no explanation. /no_think"},
                    {"role": "user", "content": prompt},
                ]
                response = llm_client.chat.completions.create(
                    model=model,
                    max_tokens=8000,
                    messages=msgs,
                )
                raw = response.choices[0].message.content
                result = _parse_json_response(raw)
                if result:
                    print(f"[LLM] Success with {provider_name} ({model}) on attempt {attempt+1}")
                    return result
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                if "429" in err_str or "rate" in err_str or "too_many" in err_str or "queue" in err_str:
                    wait_time = min(2 ** attempt * 5, 30)  # 5, 10, 20 seconds
                    print(f"[LLM] {provider_name} 429 rate limit, attempt {attempt+1}/{max_retries}, waiting {wait_time}s...")
                    _time.sleep(wait_time)
                    continue
                print(f"[LLM] {provider_name} error: {e}")
                break  # Non-rate-limit error, try next provider
        print(f"[LLM] {provider_name} exhausted, trying next provider...")

    raise RuntimeError(f"All LLM providers rate limited/failed: {last_error}")


async def _dismiss_cookie_banners(page: Page):
    """Try to dismiss common cookie consent banners."""
    cookie_selectors = [
        # Common cookie banner buttons
        'button:has-text("Accept")',
        'button:has-text("Accept All")',
        'button:has-text("Accept all")',
        'button:has-text("Accept Cookies")',
        'button:has-text("Accept all cookies")',
        'button:has-text("I Accept")',
        'button:has-text("I agree")',
        'button:has-text("Agree")',
        'button:has-text("Got it")',
        'button:has-text("OK")',
        'button:has-text("Allow")',
        'button:has-text("Allow All")',
        '[id*="cookie"] button',
        '[class*="cookie"] button',
        '[id*="consent"] button',
        '[class*="consent"] button',
        '[id*="gdpr"] button',
        '#onetrust-accept-btn-handler',
        '.cc-accept',
        '.cc-btn.cc-allow',
        '[data-testid="cookie-accept"]',
    ]
    for selector in cookie_selectors:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=500):
                await btn.click(timeout=2000)
                await asyncio.sleep(0.5)
                return True
        except Exception:
            continue
    return False


async def _check_for_captcha(page: Page) -> bool:
    """Check if the page has a user-facing CAPTCHA challenge.

    reCAPTCHA v3 (invisible) loads iframes and .g-recaptcha badge elements
    that are technically in the DOM and even "visible", but they are tiny
    (badge ~70x28, scoring iframe 0x0).  A real v2 checkbox/challenge or
    hCaptcha widget is at least ~300x70.  We require minimum 70x65 to
    distinguish an actual blocking challenge from background scoring.
    """
    return await page.evaluate("""
    () => {
        function isChallengeSize(el) {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            return r.width >= 70 && r.height >= 65;
        }

        // Check for large captcha iframes (v2 checkbox / hCaptcha widget)
        const captchaIframes = document.querySelectorAll(
            'iframe[src*="recaptcha/api2"], iframe[src*="recaptcha/enterprise"], iframe[src*="hcaptcha"]'
        );
        for (const iframe of captchaIframes) {
            if (isChallengeSize(iframe)) return true;
        }

        // Check for large captcha challenge divs
        const captchaDivs = document.querySelectorAll(
            '.g-recaptcha, .h-captcha, [class*="captcha-container"]'
        );
        for (const div of captchaDivs) {
            if (isChallengeSize(div)) return true;
        }

        // Check for the reCAPTCHA v2 overlay challenge popup
        const overlay = document.querySelector('iframe[src*="recaptcha"][title*="challenge"]');
        if (overlay && isChallengeSize(overlay)) return true;

        return false;
    }
    """)


def _load_credentials() -> dict:
    """Load credentials from YAML file."""
    creds_path = Path(__file__).parent.parent / "credentials.yaml"
    if creds_path.exists():
        with open(creds_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def _generate_cover_letter(company: str, role: str, job_description: str) -> str:
    """Generate a short cover letter via LLM when the portal requires one."""
    client = _get_llm_client()
    personal_info = _load_personal_info()

    prompt = f"""Write a brief, authentic cover letter (under 200 words) for this job application.

CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

{WRITING_STYLE}

Company: {company}
Role: {role}
Job Description: {job_description[:1500]}

Write the cover letter. Do NOT use "Dear Hiring Manager" or other generic openers. Start with something specific about the company. Output ONLY the letter text."""

    # Retry with exponential backoff for 429 rate limit errors
    import time as _time
    for attempt in range(5):
        try:
            response = client.chat.completions.create(
                model=_LLM_MODEL,
                max_tokens=1000,
                messages=[
                    {"role": "system", "content": "You write concise, authentic cover letters. No markdown, no explanation."},
                    {"role": "user", "content": prompt},
                ],
            )
            raw = response.choices[0].message.content
            # Strip think tags aggressively
            while '<think>' in raw and '</think>' in raw:
                raw = re.sub(r'<think>[\s\S]*?</think>', '', raw, flags=re.DOTALL)
            raw = re.sub(r'<think>[\s\S]*$', '', raw, flags=re.DOTALL)
            raw = re.sub(r'^[\s\S]*?</think>', '', raw, flags=re.DOTALL)
            raw = re.sub(r'</?think>', '', raw)
            return raw.strip()
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "rate" in err_str or "too_many" in err_str or "queue" in err_str:
                wait_time = min(2 ** attempt * 5, 60)
                _time.sleep(wait_time)
                continue
            raise
    raise RuntimeError("LLM rate limited after 5 retries")


async def _handle_ats_auth(page, ats_key: str, event_callback=None) -> bool:
    """Handle sign-in or account creation for ATS portals that require accounts.

    Supports: iCIMS, Taleo, SuccessFactors.
    Workday has its own handler (_handle_workday_auth).
    """
    creds = _load_credentials().get(ats_key, {})
    email = creds.get("email", "")
    password = creds.get("password", "")
    if not email or not password:
        if event_callback:
            await event_callback("Navigate", "error", f"No {ats_key} credentials in credentials.yaml")
        return False

    personal = _load_personal_info()
    first_name = personal.get("first_name", "")
    last_name = personal.get("last_name", "")

    if ats_key == "icims":
        return await _handle_icims_auth(page, email, password, first_name, last_name, event_callback)
    elif ats_key == "taleo":
        return await _handle_taleo_auth(page, email, password, first_name, last_name, event_callback)
    elif ats_key == "successfactors":
        return await _handle_successfactors_auth(page, email, password, first_name, last_name, event_callback)

    return True  # No auth needed for other portals


async def _handle_icims_auth(page, email, password, first_name, last_name, event_callback=None) -> bool:
    """Handle iCIMS sign-in or account creation. iCIMS forms are in iframes."""
    if event_callback:
        await event_callback("Navigate", "info", "iCIMS: Looking for login/create account...")

    # iCIMS application is often in an iframe
    target = page
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            has_inputs = await frame.evaluate("document.querySelectorAll('input').length")
            if has_inputs > 2:
                target = frame
                break
        except Exception:
            continue

    # Try to find sign-in form
    try:
        # Look for email/username field
        email_field = target.locator('input[type="email"], input[name*="email"], input[name*="user"], input[id*="email"], input[id*="user"]')
        if await email_field.first.is_visible(timeout=3000):
            await email_field.first.fill(email)

            pw_field = target.locator('input[type="password"]')
            if await pw_field.first.is_visible(timeout=2000):
                await pw_field.first.fill(password)

            # Submit
            submit = target.locator('button[type="submit"], input[type="submit"], .iCIMS_PrimaryButton')
            if await submit.first.is_visible(timeout=2000):
                await submit.first.click()
                await asyncio.sleep(5.0)

            # Check if we landed on the form
            inputs = await target.evaluate("document.querySelectorAll('input, textarea, select').length")
            if inputs > 5:
                if event_callback:
                    await event_callback("Navigate", "success", "iCIMS: Signed in")
                return True

            # Sign-in failed, try creating account
            if event_callback:
                await event_callback("Navigate", "info", "iCIMS: Sign-in failed, creating account...")
    except Exception:
        pass

    # Try creating account
    try:
        create_link = target.locator('a:has-text("Create"), a:has-text("Register"), a:has-text("Sign Up"), button:has-text("Create")')
        if await create_link.first.is_visible(timeout=3000):
            await create_link.first.click()
            await asyncio.sleep(3.0)

        # Fill account creation fields
        for selector, value in [
            ('input[name*="first" i], input[id*="first" i]', first_name),
            ('input[name*="last" i], input[id*="last" i]', last_name),
            ('input[type="email"], input[name*="email" i]', email),
            ('input[type="password"]', password),
        ]:
            try:
                field = target.locator(selector)
                if await field.first.is_visible(timeout=2000):
                    await field.first.fill(value)
            except Exception:
                continue

        # Confirm password if present
        try:
            confirm = target.locator('input[name*="confirm" i], input[name*="verify" i], input[id*="confirm" i]')
            if await confirm.first.is_visible(timeout=2000):
                await confirm.first.fill(password)
        except Exception:
            pass

        # Submit
        submit = target.locator('button[type="submit"], input[type="submit"], .iCIMS_PrimaryButton')
        if await submit.first.is_visible(timeout=2000):
            await submit.first.click()
            await asyncio.sleep(5.0)

        if event_callback:
            await event_callback("Navigate", "success", "iCIMS: Account created")
        return True
    except Exception as e:
        if event_callback:
            await event_callback("Navigate", "error", f"iCIMS auth failed: {e}")
        return False


async def _handle_taleo_auth(page, email, password, first_name, last_name, event_callback=None) -> bool:
    """Handle Taleo sign-in or account creation."""
    if event_callback:
        await event_callback("Navigate", "info", "Taleo: Looking for login page...")

    try:
        # Taleo uses #ftlform with email/password fields
        email_field = page.locator('input[type="email"], input[name*="email" i], input[id*="email" i], input[name*="user" i]')
        if await email_field.first.is_visible(timeout=3000):
            await email_field.first.fill(email)

            pw_field = page.locator('input[type="password"]')
            if await pw_field.first.is_visible(timeout=2000):
                await pw_field.first.fill(password)

            submit = page.locator('button[type="submit"], input[type="submit"], a:has-text("Sign In"), button:has-text("Sign In"), button:has-text("Log In")')
            if await submit.first.is_visible(timeout=2000):
                await submit.first.click()
                await asyncio.sleep(5.0)

            # Check if signed in
            still_login = await page.locator('input[type="password"]').first.is_visible(timeout=2000)
            if not still_login:
                if event_callback:
                    await event_callback("Navigate", "success", "Taleo: Signed in")
                return True

            if event_callback:
                await event_callback("Navigate", "info", "Taleo: Sign-in failed, creating account...")
    except Exception:
        pass

    # Create account
    try:
        create_link = page.locator('a:has-text("New User"), a:has-text("Create"), a:has-text("Register")')
        if await create_link.first.is_visible(timeout=3000):
            await create_link.first.click()
            await asyncio.sleep(3.0)

        for selector, value in [
            ('input[name*="first" i], input[id*="first" i]', first_name),
            ('input[name*="last" i], input[id*="last" i]', last_name),
            ('input[type="email"], input[name*="email" i]', email),
            ('input[type="password"]', password),
        ]:
            try:
                field = page.locator(selector)
                if await field.first.is_visible(timeout=2000):
                    await field.first.fill(value)
            except Exception:
                continue

        # Confirm password
        try:
            confirm = page.locator('input[name*="confirm" i], input[name*="verify" i]')
            if await confirm.first.is_visible(timeout=2000):
                await confirm.first.fill(password)
        except Exception:
            pass

        submit = page.locator('button[type="submit"], input[type="submit"]')
        if await submit.first.is_visible(timeout=2000):
            await submit.first.click()
            await asyncio.sleep(5.0)

        if event_callback:
            await event_callback("Navigate", "success", "Taleo: Account created")
        return True
    except Exception as e:
        if event_callback:
            await event_callback("Navigate", "error", f"Taleo auth failed: {e}")
        return False


async def _handle_successfactors_auth(page, email, password, first_name, last_name, event_callback=None) -> bool:
    """Handle SuccessFactors sign-in or account creation (Talent Community)."""
    if event_callback:
        await event_callback("Navigate", "info", "SuccessFactors: Looking for login...")

    try:
        email_field = page.locator('input[type="email"], input[name*="email" i], input[id*="email" i]')
        if await email_field.first.is_visible(timeout=3000):
            await email_field.first.fill(email)

            pw_field = page.locator('input[type="password"]')
            if await pw_field.first.is_visible(timeout=2000):
                await pw_field.first.fill(password)

            submit = page.locator('button[type="submit"], input[type="submit"], button:has-text("Sign In"), button:has-text("Log In")')
            if await submit.first.is_visible(timeout=2000):
                await submit.first.click()
                await asyncio.sleep(5.0)

            still_login = await page.locator('input[type="password"]').first.is_visible(timeout=2000)
            if not still_login:
                if event_callback:
                    await event_callback("Navigate", "success", "SuccessFactors: Signed in")
                return True

            if event_callback:
                await event_callback("Navigate", "info", "SuccessFactors: Creating account (joining Talent Community)...")
    except Exception:
        pass

    # Create account / join Talent Community
    try:
        create_link = page.locator('a:has-text("Create"), a:has-text("Register"), a:has-text("Join"), button:has-text("Create")')
        if await create_link.first.is_visible(timeout=3000):
            await create_link.first.click()
            await asyncio.sleep(3.0)

        for selector, value in [
            ('input[name*="first" i], input[id*="first" i]', first_name),
            ('input[name*="last" i], input[id*="last" i]', last_name),
            ('input[type="email"], input[name*="email" i]', email),
            ('input[type="password"]', password),
        ]:
            try:
                field = page.locator(selector)
                if await field.first.is_visible(timeout=2000):
                    await field.first.fill(value)
            except Exception:
                continue

        # Confirm password
        try:
            confirm = page.locator('input[name*="confirm" i], input[name*="verify" i]')
            if await confirm.first.is_visible(timeout=2000):
                await confirm.first.fill(password)
        except Exception:
            pass

        # Accept data privacy consent if present
        try:
            consent = page.locator('input[type="checkbox"][name*="consent" i], input[type="checkbox"][name*="privacy" i], input[type="checkbox"][name*="agree" i]')
            if await consent.first.is_visible(timeout=2000):
                await consent.first.check()
        except Exception:
            pass

        submit = page.locator('button[type="submit"], input[type="submit"]')
        if await submit.first.is_visible(timeout=2000):
            await submit.first.click()
            await asyncio.sleep(5.0)

        # Handle email verification if required
        page_text = await page.evaluate("document.body.innerText")
        if "verify" in page_text.lower() and "email" in page_text.lower():
            verified = await complete_email_verification(
                page, sender_filter="successfactors", event_callback=event_callback
            )
            if verified:
                await asyncio.sleep(3.0)
                await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(5.0)
            else:
                if event_callback:
                    await event_callback("Navigate", "warning",
                        "Auto-verify failed. Please verify your email manually.")
                for _ in range(90):
                    await asyncio.sleep(1.0)
                    try:
                        page_text = await page.evaluate("document.body.innerText")
                        if "verify" not in page_text.lower():
                            break
                    except Exception:
                        pass

        if event_callback:
            await event_callback("Navigate", "success", "SuccessFactors: Account created")
        return True
    except Exception as e:
        if event_callback:
            await event_callback("Navigate", "error", f"SuccessFactors auth failed: {e}")
        return False


async def _handle_workday_apply(page, resume_path, event_callback=None, screenshot_callback=None, job_url=None) -> dict:
    """Handle the full Workday multi-step application flow.

    Correct Workday flow:
    1. Click Apply on job page
    2. Click "Apply Manually"
    3. Create Account / Sign In page appears
    4. Fill create account form, submit
    5. Sign in with same credentials
    6. If email verification needed, verify then sign in again
    7. Fill the multi-step application form
    """
    creds = _load_credentials().get("workday", {})
    email = creds.get("email", "")
    password = creds.get("password", "")
    personal = _load_personal_info()

    filled_total = 0
    failed_total = 0
    errors = []

    # Step 0: Accept cookies if present
    try:
        btn = page.locator('[data-automation-id="legalNoticeAcceptButton"]')
        if await btn.is_visible(timeout=2000):
            await btn.click()
            await asyncio.sleep(1.0)
    except Exception:
        pass

    # Step 1: Click Apply button on job page
    if event_callback:
        await event_callback("Navigate", "info", "Workday: Clicking Apply...")

    try:
        apply_btn = page.locator('[data-automation-id="adventureButton"], a:has-text("Apply"), button:has-text("Apply")')
        if await apply_btn.first.is_visible(timeout=5000):
            await apply_btn.first.click()
            await asyncio.sleep(5.0)
            if event_callback:
                await event_callback("Navigate", "info", "Workday: Clicked Apply")
    except Exception:
        pass

    if screenshot_callback:
        ss = await _take_screenshot(page)
        if ss:
            await screenshot_callback(ss)

    # Step 2: Click "Apply Manually"
    if event_callback:
        await event_callback("Navigate", "info", "Workday: Clicking Apply Manually...")

    try:
        manual = page.locator('[data-automation-id="applyManually"]')
        if await manual.is_visible(timeout=5000):
            await manual.click()
            await asyncio.sleep(5.0)
            if event_callback:
                await event_callback("Navigate", "info", "Workday: Clicked Apply Manually")
    except Exception:
        pass

    if screenshot_callback:
        ss = await _take_screenshot(page)
        if ss:
            await screenshot_callback(ss)

    # Step 3: We should now be on the Create Account / Sign In page
    # Try signing in first (in case account already exists)
    if event_callback:
        await event_callback("Navigate", "info", "Workday: On auth page, trying sign in first...")

    signed_in = False

    # Check if email field is visible (we're on an auth page)
    email_field = page.locator('[data-automation-id="email"]')
    if await email_field.is_visible(timeout=5000):
        # Try sign in first
        await email_field.fill(email)
        pw_field = page.locator('[data-automation-id="password"]')
        if await pw_field.is_visible(timeout=3000):
            await pw_field.fill(password)

        # Look for Sign In button and click it
        try:
            await page.evaluate("""() => {
                const btn = document.querySelector('[data-automation-id="signInSubmitButton"]');
                if (btn) btn.click();
            }""")
            await asyncio.sleep(5.0)
        except Exception:
            pass

        # Check if sign-in succeeded
        error_visible = False
        try:
            error_visible = await page.locator(
                '[data-automation-id="errorMessage"], [data-automation-id="formErrorBanner"]'
            ).first.is_visible(timeout=2000)
        except Exception:
            pass

        still_on_auth = await page.locator('[data-automation-id="signInSubmitButton"]').is_visible(timeout=2000)

        if not still_on_auth and not error_visible:
            signed_in = True
            if event_callback:
                await event_callback("Navigate", "success", "Workday: Signed in (existing account)")
        else:
            if event_callback:
                await event_callback("Navigate", "info", "Sign-in failed, creating account...")

    # Step 4: Create account if sign-in failed
    if not signed_in:
        # Click Create Account link/tab
        try:
            create_link = page.locator(
                '[data-automation-id="createAccountLink"], '
                'a:has-text("Create Account"), '
                'button:has-text("Create Account"), '
                'a:has-text("create account"), '
                'a:has-text("Create an Account")'
            )
            if await create_link.first.is_visible(timeout=5000):
                await create_link.first.click()
                await asyncio.sleep(3.0)
                if event_callback:
                    await event_callback("Navigate", "info", "Workday: On Create Account form")
            else:
                if event_callback:
                    await event_callback("Navigate", "info", "No Create Account link found, may already be on create form")
        except Exception:
            pass

        # Fill create account form
        try:
            email_field = page.locator('[data-automation-id="email"]')
            if await email_field.is_visible(timeout=5000):
                await email_field.fill(email)

            pw_field = page.locator('[data-automation-id="password"]')
            if await pw_field.is_visible(timeout=3000):
                await pw_field.fill(password)

            # Verify password
            verify_pw = page.locator('[data-automation-id="verifyPassword"]')
            try:
                if await verify_pw.is_visible(timeout=3000):
                    await verify_pw.fill(password)
            except Exception:
                pass

            # Accept terms checkbox — use the robust Workday consent handler
            # (Workday uses role="checkbox" with a click_filter overlay, not <input type="checkbox">)
            try:
                from applicator.workday_handler import check_workday_consent
                checkbox_ok = await check_workday_consent(page, event_callback=event_callback, max_wait_seconds=10)
                if not checkbox_ok and event_callback:
                    await event_callback("Navigate", "error", "Consent checkbox could not be checked automatically")
            except Exception:
                pass

            if event_callback:
                await event_callback("Navigate", "info", "Workday: Submitting Create Account...")

            # Click Create Account submit
            await page.evaluate("""() => {
                const btn = document.querySelector('[data-automation-id="createAccountSubmitButton"]')
                    || document.querySelector('button[data-automation-id*="create"]')
                    || document.querySelector('button[type="submit"]');
                if (btn) btn.click();
            }""")
            await asyncio.sleep(8.0)

            if screenshot_callback:
                ss = await _take_screenshot(page)
                if ss:
                    await screenshot_callback(ss)

            if event_callback:
                await event_callback("Navigate", "info", "Account created, now signing in...")

        except Exception as e:
            if event_callback:
                await event_callback("Navigate", "error", f"Create account failed: {e}")
            return {"filled": 0, "failed": 1, "skipped": 0, "errors": [str(e)]}

        # Step 5: After account creation, sign in with same credentials
        try:
            email_field = page.locator('[data-automation-id="email"]')
            if await email_field.is_visible(timeout=5000):
                await email_field.fill(email)

            pw_field = page.locator('[data-automation-id="password"]')
            if await pw_field.is_visible(timeout=3000):
                await pw_field.fill(password)

            await page.evaluate("""() => {
                const btn = document.querySelector('[data-automation-id="signInSubmitButton"]');
                if (btn) btn.click();
            }""")
            await asyncio.sleep(5.0)
        except Exception:
            pass

        if screenshot_callback:
            ss = await _take_screenshot(page)
            if ss:
                await screenshot_callback(ss)

        # Step 6: Check for email verification
        try:
            page_text = await page.evaluate("document.body.innerText")
            if "verify" in page_text.lower() and "email" in page_text.lower():
                if event_callback:
                    await event_callback("Navigate", "info",
                        "Email verification required. Please check your email and verify. Waiting up to 2 minutes...")

                verified = await complete_email_verification(
                    page, sender_filter="workday", event_callback=event_callback
                )
                if verified:
                    await asyncio.sleep(3.0)
                else:
                    if event_callback:
                        await event_callback("Navigate", "warning",
                            "Auto-verify failed. Please verify manually in your email, then come back here.")
                    # Wait for manual verification
                    for _ in range(120):
                        await asyncio.sleep(1.0)
                        try:
                            page_text = await page.evaluate("document.body.innerText")
                            if "verify" not in page_text.lower():
                                break
                        except Exception:
                            pass

                # After verification, click Sign In again on the SAME tab
                if event_callback:
                    await event_callback("Navigate", "info", "Verification done, signing in again...")

                # May need to reload or the page may have updated
                try:
                    email_field = page.locator('[data-automation-id="email"]')
                    if await email_field.is_visible(timeout=5000):
                        await email_field.fill(email)
                    pw_field = page.locator('[data-automation-id="password"]')
                    if await pw_field.is_visible(timeout=3000):
                        await pw_field.fill(password)
                    await page.evaluate("""() => {
                        const btn = document.querySelector('[data-automation-id="signInSubmitButton"]');
                        if (btn) btn.click();
                    }""")
                    await asyncio.sleep(5.0)
                except Exception:
                    pass

                # Also try clicking any Sign In button/link that might be visible
                try:
                    sign_in = page.locator('button:has-text("Sign In"), a:has-text("Sign In"), [data-automation-id="utilityButtonSignIn"]')
                    if await sign_in.first.is_visible(timeout=3000):
                        await sign_in.first.click()
                        await asyncio.sleep(5.0)
                        # Fill credentials again if we're on a sign-in form
                        email_field = page.locator('[data-automation-id="email"]')
                        if await email_field.is_visible(timeout=3000):
                            await email_field.fill(email)
                            pw_field = page.locator('[data-automation-id="password"]')
                            if await pw_field.is_visible(timeout=2000):
                                await pw_field.fill(password)
                            await page.evaluate("""() => {
                                const btn = document.querySelector('[data-automation-id="signInSubmitButton"]');
                                if (btn) btn.click();
                            }""")
                            await asyncio.sleep(5.0)
                except Exception:
                    pass

        except Exception:
            pass

        if event_callback:
            await event_callback("Navigate", "success", "Workday: Signed in")

    if screenshot_callback:
        ss = await _take_screenshot(page)
        if ss:
            await screenshot_callback(ss)

    # Step 7: We should now be on the application form (or need to re-navigate)
    # Check if we're on a form page or need to go back to the job
    try:
        has_form_fields = await page.evaluate("document.querySelectorAll('input:not([type=hidden]), textarea, select').length")
        if has_form_fields < 3:
            # Not on a form yet - might need to re-navigate to the job and click Apply again
            if event_callback:
                await event_callback("Navigate", "info", "Workday: Re-navigating to job to start application...")

            # Navigate back to the original job URL
            if job_url:
                await page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(5.0)
                # Accept cookies again
                try:
                    btn = page.locator('[data-automation-id="legalNoticeAcceptButton"]')
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        await asyncio.sleep(1.0)
                except Exception:
                    pass
                # Click Apply again
                apply_btn = page.locator('[data-automation-id="adventureButton"], a:has-text("Apply")')
                if await apply_btn.first.is_visible(timeout=5000):
                    await apply_btn.first.click()
                    await asyncio.sleep(5.0)
            else:
                await page.go_back()
                await asyncio.sleep(3.0)

            # Choose apply method again if prompted
            try:
                manual = page.locator('[data-automation-id="applyManually"]')
                if await manual.is_visible(timeout=3000):
                    await manual.click()
                    await asyncio.sleep(5.0)
            except Exception:
                pass
            # Never click autofillWithResume — it opens a file upload that breaks the flow.
            # Resume upload happens later on My Experience page via upload_file_robust.
    except Exception:
        pass

    # Step 4: Process each page of the multi-step form
    max_pages = 10
    for page_num in range(max_pages):
        await asyncio.sleep(2.0)

        if screenshot_callback:
            ss = await _take_screenshot(page)
            if ss:
                await screenshot_callback(ss)

        # Check current step
        step_text = ""
        try:
            step_el = page.locator('[data-automation-id="progressBarActiveStep"]')
            if await step_el.is_visible(timeout=2000):
                step_text = await step_el.inner_text()
        except Exception:
            pass

        if event_callback:
            await event_callback("Fill Form", "info", f"Workday page {page_num + 1}: {step_text[:40]}")

        # Check if we're on the Review page
        if "review" in step_text.lower():
            if event_callback:
                await event_callback("Fill Form", "success", "Workday: Reached Review page - stopping before submit")
            break

        # Check for "Something went wrong" error page — refresh to recover
        try:
            page_text = await page.evaluate("() => document.body.innerText.toLowerCase()")
            if "something went wrong" in page_text or "please refresh the page" in page_text:
                if event_callback:
                    await event_callback("Fill Form", "warning", "Workday error page detected. Refreshing...")
                await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(5)
                # Check if still errored after refresh
                page_text2 = await page.evaluate("() => document.body.innerText.toLowerCase()")
                if "something went wrong" in page_text2:
                    if event_callback:
                        await event_callback("Fill Form", "error", "Still error after refresh. Navigating back to application...")
                    # Try going back to the job posting URL and re-entering
                    if job_url:
                        await page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
                    else:
                        await page.go_back()
                        await asyncio.sleep(3)
                    await asyncio.sleep(5)
                    # Click Apply again
                    try:
                        apply_btn = page.locator('a[data-uxi-element-id="Apply_adventureButton"]').first
                        if await apply_btn.is_visible(timeout=3000):
                            box = await apply_btn.bounding_box()
                            if box:
                                await page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                                await asyncio.sleep(5)
                    except Exception:
                        pass
                    continue  # Retry the step loop
                else:
                    if event_callback:
                        await event_callback("Fill Form", "success", "Page recovered after refresh!")
                    continue  # Retry with the refreshed page
        except Exception as e:
            if event_callback:
                await event_callback("Fill Form", "warning", f"Error check failed: {e}")

        # Scroll to load all content
        for i in range(5):
            await page.evaluate(f"window.scrollTo(0, {i * 900})")
            await asyncio.sleep(0.3)
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.5)

        # Extract fields using data-automation-id
        fields_on_page = await page.evaluate("""() => {
            const results = [];
            document.querySelectorAll('input, textarea, select').forEach(el => {
                const visible = el.offsetParent !== null || el.offsetHeight > 0;
                if (!visible) return;
                const type = el.type || el.tagName.toLowerCase();
                if (['hidden', 'submit', 'button'].includes(type)) return;
                results.push({
                    selector: el.getAttribute('data-automation-id') ? '[data-automation-id="' + el.getAttribute('data-automation-id') + '"]' : (el.id ? '#' + el.id : ''),
                    tag: el.tagName.toLowerCase(),
                    type: type,
                    dataid: el.getAttribute('data-automation-id') || '',
                    label: '',
                    required: el.required || el.getAttribute('aria-required') === 'true',
                    value: el.value || '',
                    name: el.name || '',
                    placeholder: el.placeholder || ''
                });
            });
            // Also get Workday-specific button/dropdown fields
            document.querySelectorAll('[data-automation-id]').forEach(el => {
                const dataid = el.getAttribute('data-automation-id');
                if (dataid && dataid.startsWith('formField-') && el.offsetParent !== null) {
                    const label = el.querySelector('label');
                    const labelText = label ? label.innerText.trim() : '';
                    results.push({
                        selector: '[data-automation-id="' + dataid + '"]',
                        tag: 'div',
                        type: 'workday-field',
                        dataid: dataid,
                        label: labelText,
                        required: true,
                        value: '',
                        name: '',
                        placeholder: ''
                    });
                }
            });
            return results;
        }""")

        if len(fields_on_page) > 0:
            if event_callback:
                await event_callback("Fill Form", "info", f"Found {len(fields_on_page)} fields on this page")

            # Use LLM to map fields
            try:
                mappings = await asyncio.to_thread(map_fields_to_profile, fields_on_page, "", "", "")
                for m in mappings:
                    if m.get("action") == "skip":
                        continue
                    selector = m.get("selector", "")
                    value = m.get("value", "")
                    action = m.get("action", "fill")
                    if not selector:
                        continue
                    try:
                        if action == "fill":
                            try:
                                await page.fill(selector, value, timeout=3000)
                            except Exception:
                                await page.evaluate(f"""() => {{
                                    const el = document.querySelector('{selector}');
                                    if (el) {{ el.value = '{value.replace(chr(39), "")}'; el.dispatchEvent(new Event('input', {{bubbles:true}})); el.dispatchEvent(new Event('change', {{bubbles:true}})); }}
                                }}""")
                            filled_total += 1
                        elif action == "select":
                            success = await _handle_custom_dropdown(page, selector, value, event_callback)
                            if success:
                                filled_total += 1
                            else:
                                failed_total += 1
                                errors.append(f"Dropdown {selector}: could not select '{value}'")
                        elif action == "click":
                            try:
                                await page.click(selector, timeout=3000)
                            except Exception:
                                await page.evaluate(f"""document.querySelector('{selector}').click()""")
                            filled_total += 1
                    except Exception as e:
                        failed_total += 1
                        errors.append(f"{selector}: {e}")
            except Exception as e:
                if event_callback:
                    await event_callback("Fill Form", "info", f"LLM mapping failed for this page: {e}")

        if screenshot_callback:
            ss = await _take_screenshot(page)
            if ss:
                await screenshot_callback(ss)

        # Click Continue/Next to go to next page
        try:
            next_btn = page.locator('[data-automation-id="pageFooterNextButton"], [data-automation-id="bottom-navigation-next-button"]')
            if await next_btn.is_visible(timeout=3000):
                try:
                    await next_btn.click(timeout=5000)
                except Exception:
                    await page.evaluate("""() => {
                        const btn = document.querySelector('[data-automation-id="pageFooterNextButton"]') || document.querySelector('[data-automation-id="bottom-navigation-next-button"]');
                        if (btn) btn.click();
                    }""")
                await asyncio.sleep(3.0)
            else:
                break  # No next button - we're done
        except Exception:
            break

    return {"filled": filled_total, "failed": failed_total, "skipped": 0, "errors": errors}


async def _handle_workday_auth(page, event_callback=None) -> bool:
    """Handle Workday sign-in or account creation. Returns True if auth succeeded.

    Real Workday DOM patterns (confirmed via live testing):
    - Apply: a[data-uxi-element-id="Apply_adventureButton"] or a[data-automation-id="adventureButton"]
    - Apply Manually: a[data-automation-id="applyManually"]
    - Sign In nav: button[data-automation-id="utilityButtonSignIn"]
    - Sign In submit: div[data-automation-id="click_filter"][aria-label="Sign In"] OR button:has-text("Sign In")
    - Create Account link: button[data-automation-id="createAccountLink"]
    - Create Account submit: button[data-automation-id="createAccountSubmitButton"] AND div[data-automation-id="click_filter"][aria-label="Create Account"]
    - Sign In link (from create account popup): button[data-automation-id="signInLink"]
    - Email: input[data-automation-id="email"]
    - Password: input[data-automation-id="password"]
    - Verify Password: input[data-automation-id="verifyPassword"]
    - Form fields appear in: div[data-automation-id="applyFlowPage"]
    - Auth popup: div[data-automation-id="popUpDialog"]
    """
    creds = _load_credentials().get("workday", {})
    email = creds.get("email", "")
    password = creds.get("password", "")
    if not email or not password:
        if event_callback:
            await event_callback("Navigate", "error", "No Workday credentials in credentials.yaml")
        return False

    async def _log(msg):
        if event_callback:
            await event_callback("Navigate", "info", f"Workday: {msg}")

    async def _log_ok(msg):
        if event_callback:
            await event_callback("Navigate", "success", f"Workday: {msg}")

    # --- Dismiss Google SSO overlays ---
    try:
        await page.evaluate("""() => {
            document.querySelectorAll('iframe[src*="accounts.google.com"]').forEach(el => el.remove());
            document.querySelectorAll('#credential_picker_container, #credential_picker_iframe').forEach(el => el.remove());
        }""")
    except Exception:
        pass

    # --- Check if already on application form (no auth needed) ---
    async def _is_on_form() -> bool:
        try:
            return await page.evaluate("""() => {
                function isVis(sel) {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }

                // Auth indicators — if ANY are visible, we are NOT on the form
                if (isVis('input[data-automation-id="email"]')
                    || isVis('input[data-automation-id="password"]')
                    || isVis('[data-automation-id="signInSubmitButton"]')
                    || isVis('[data-automation-id="createAccountSubmitButton"]')
                    || isVis('[data-automation-id="createAccountLink"]')) {
                    return false;
                }
                // Also check for Sign In + Create Account text appearing together
                const bodyText = document.body.innerText || '';
                if (bodyText.includes('Sign In') && bodyText.includes('Create Account')) {
                    return false;
                }

                // Form indicators — need at least one real form field
                const hasFormField = isVis('input[name="legalName--firstName"]')
                    || isVis('[data-automation-id="formField-legalName--firstName"]')
                    || isVis('[data-automation-id="file-upload-drop-zone"]')
                    || isVis('[data-automation-id="progressBar"]');
                return hasFormField;
            }""")
        except Exception:
            return False

    if await _is_on_form():
        await _log_ok("Already on application form, no auth needed!")
        return True

    # --- Helper: try clicking with multiple methods ---
    async def _multi_click(selectors, js_fallback=None, force=False, timeout=2000, use_mouse=False):
        """Try Playwright selectors, then JS. Returns True if clicked.
        If use_mouse=True, uses page.mouse.click() for isTrusted=true events (required for Workday click_filter).
        """
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=timeout):
                    if use_mouse:
                        box = await loc.bounding_box()
                        if box:
                            await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                            return True
                    else:
                        await loc.click(force=force, timeout=5000)
                        return True
            except Exception:
                continue
        if js_fallback:
            try:
                result = await page.evaluate(js_fallback)
                if result:
                    return True
            except Exception:
                pass
        return False

    # --- Helper: fill credentials ---
    async def _fill_creds():
        for field_aid, value in [("email", email), ("password", password)]:
            try:
                loc = page.locator(f'input[data-automation-id="{field_aid}"]').first
                if await loc.is_visible(timeout=3000):
                    await loc.fill(value)
                    continue
            except Exception:
                pass
            # JS fallback with proper event dispatch
            await page.evaluate(f"""() => {{
                const el = document.querySelector('[data-automation-id="{field_aid}"]');
                if (el) {{
                    el.focus();
                    el.value = {json.dumps(value)};
                    el.dispatchEvent(new Event('input', {{bubbles: true}}));
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    el.dispatchEvent(new Event('blur', {{bubbles: true}}));
                }}
            }}""")

    # --- Check: is Sign In nav button present? ---
    # Some Workday sites show Sign In in the header, others don't require auth at all
    needs_auth = False
    try:
        needs_auth = await page.locator('button[data-automation-id="utilityButtonSignIn"]').is_visible(timeout=2000)
    except Exception:
        pass

    # Also check if a sign-in popup is already showing
    has_auth_popup = False
    try:
        has_auth_popup = await page.locator('[data-automation-id="signInContent"]').is_visible(timeout=1000)
    except Exception:
        pass

    # --- Check for "Sign in with email" / "Sign in with Google" pattern ---
    # Some Workday sites (e.g. NVIDIA) show a choice page instead of direct email/password
    has_sign_in_with_email = False
    try:
        has_sign_in_with_email = await page.evaluate("""() => {
            const buttons = document.querySelectorAll('button, a, div[role="button"]');
            for (const btn of buttons) {
                const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                if (text.includes('sign in with email') && btn.offsetParent !== null) {
                    return true;
                }
            }
            return false;
        }""")
    except Exception:
        pass

    if has_sign_in_with_email:
        needs_auth = True
        await _log("Found 'Sign in with email' button pattern")

    if not needs_auth and not has_auth_popup:
        # Might already be on the form or no auth required
        if await _is_on_form():
            await _log_ok("No auth required, already on form!")
            return True
        await _log("No sign-in button found, proceeding anyway")
        return True

    # --- Step 0.5: Handle "Sign in with email" button if present ---
    if has_sign_in_with_email:
        clicked_email_btn = await _multi_click(
            [
                'button:has-text("Sign in with email")',
                'a:has-text("Sign in with email")',
                'div[role="button"]:has-text("Sign in with email")',
            ],
            js_fallback="""() => {
                const buttons = document.querySelectorAll('button, a, div[role="button"]');
                for (const btn of buttons) {
                    const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                    if (text.includes('sign in with email') && btn.offsetParent !== null) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""",
        )
        if clicked_email_btn:
            await _log("Clicked 'Sign in with email' button")
            await asyncio.sleep(3.0)
        else:
            await _log("Could not click 'Sign in with email' button")

    # --- Step 1: Click Sign In nav button (header) if not already in auth popup ---
    elif needs_auth and not has_auth_popup:
        clicked = await _multi_click(
            ['button[data-automation-id="utilityButtonSignIn"]'],
            js_fallback="""() => {
                const btn = document.querySelector('[data-automation-id="utilityButtonSignIn"]');
                if (btn) { btn.click(); return true; }
                return false;
            }"""
        )
        if clicked:
            await _log("Clicked Sign In nav button")
            await asyncio.sleep(3.0)
        else:
            await _log("Could not click Sign In nav")

    # --- Step 2: Try signing in first (account may already exist) ---
    # Wait for the sign-in popup/form to appear
    try:
        await page.locator('input[data-automation-id="email"]').wait_for(state="visible", timeout=5000)
    except Exception:
        await _log("Email field not visible after clicking Sign In")
        # After clicking "Sign in with email", there might be a Create Account / Sign In choice
        # Check for Create Account link which means we need to create an account first
        try:
            create_link = page.locator('[data-automation-id="createAccountLink"], button:has-text("Create Account"), a:has-text("Create Account")')
            if await create_link.first.is_visible(timeout=2000):
                await _log("Create Account page detected after Sign in with email")
                # Fall through to the create account flow below
                pass
            else:
                if await _is_on_form():
                    await _log_ok("On form already!")
                    return True
                return True
        except Exception:
            if await _is_on_form():
                await _log_ok("On form already!")
                return True
            return True

    # --- Step 2a: Check if we landed on Create Account instead of Sign In ---
    # Some Workday sites open Create Account modal when you click Sign In nav.
    # Detect this by checking for "Verify New Password" field or "Create Account" heading.
    on_create_account = False
    try:
        verify_pw_visible = await page.locator('input[data-automation-id="verifyPassword"]').is_visible(timeout=1000)
        if verify_pw_visible:
            on_create_account = True
    except Exception:
        pass
    if not on_create_account:
        try:
            heading_text = await page.evaluate("""() => {
                const headings = document.querySelectorAll('h2, h3, [role="heading"]');
                for (const h of headings) {
                    if ((h.innerText || '').trim() === 'Create Account' && h.offsetParent !== null) return true;
                }
                return false;
            }""")
            on_create_account = heading_text
        except Exception:
            pass

    if on_create_account:
        await _log("On Create Account modal, looking for 'Sign In' link to switch...")
        sign_in_link_clicked = await _multi_click(
            [
                'a[data-automation-id="signInLink"]',
                'button[data-automation-id="signInLink"]',
            ],
            js_fallback="""() => {
                // Look for "Sign In" link near "Already have an account?"
                for (const a of document.querySelectorAll('a, button')) {
                    const parent = a.closest('.css-1fqoep4, .css-1q2dra3, [data-automation-id="signInLink"]') || a.parentElement;
                    const parentText = (parent ? parent.innerText : '').toLowerCase();
                    const t = (a.innerText || '').trim();
                    if (t === 'Sign In' && a.offsetParent !== null && parentText.includes('already')) {
                        a.click(); return true;
                    }
                }
                // Fallback: any signInLink
                let el = document.querySelector('[data-automation-id="signInLink"]');
                if (el && el.offsetParent !== null) { el.click(); return true; }
                return false;
            }""",
        )
        if sign_in_link_clicked:
            await _log("Clicked Sign In link from Create Account modal")
            await asyncio.sleep(3.0)
            # Now we should be on the real Sign In form
        else:
            await _log("Could not find Sign In link, proceeding with Create Account flow...")

    await _log("Sign-in form visible. Trying sign in...")
    await _fill_creds()
    await asyncio.sleep(0.5)

    # Click Sign In submit - use mouse.click for trusted events (Workday click_filter)
    sign_in_clicked = await _multi_click(
        [
            'div[data-automation-id="click_filter"][aria-label="Sign In"]',
            'div[role="button"][aria-label="Sign In"]',
            'button[data-automation-id="signInSubmitButton"]',
            'button:has-text("Sign In")',
        ],
        js_fallback="""() => {
            // Try click_filter with Sign In aria-label
            let el = document.querySelector('[data-automation-id="click_filter"][aria-label="Sign In"]');
            if (el && el.offsetParent !== null) { el.click(); return true; }
            // Try any visible Sign In button
            for (const btn of document.querySelectorAll('button, div[role="button"]')) {
                if ((btn.innerText || '').trim() === 'Sign In' && btn.offsetParent !== null) {
                    btn.click(); return true;
                }
            }
            return false;
        }""",
        force=True,
        use_mouse=True,
    )

    if sign_in_clicked:
        await _log("Clicked Sign In submit")
        await asyncio.sleep(5.0)
        if await _is_on_form():
            await _log_ok("Signed in successfully!")
            return True

    # --- Step 3: Sign-in failed. Try Create Account ---
    # Flag: once we create an account, NEVER go back to Create Account
    account_just_created = False

    await _log("Sign-in didn't work, trying Create Account...")

    # Click "Create Account" link in the sign-in popup
    create_clicked = await _multi_click(
        [
            'button[data-automation-id="createAccountLink"]',
            '[data-automation-id="createAccountLink"]',
            'button:has-text("Create Account")',
            'a:has-text("Create Account")',
        ],
        js_fallback="""() => {
            // Look for createAccountLink
            let el = document.querySelector('[data-automation-id="createAccountLink"]');
            if (el && el.offsetParent !== null) { el.click(); return true; }
            // Text-based search
            for (const btn of document.querySelectorAll('button, a, div[role="button"]')) {
                const t = (btn.innerText || '').trim();
                if ((t === 'Create Account' || t === 'Create an Account') && btn.offsetParent !== null) {
                    btn.click(); return true;
                }
            }
            return false;
        }""",
    )

    if not create_clicked:
        await _log("Could not find Create Account link")
        return False

    await _log("Clicked Create Account link")
    await asyncio.sleep(3.0)

    # --- Step 4: Fill create account form ---
    await _fill_creds()

    # Verify password
    try:
        vp = page.locator('input[data-automation-id="verifyPassword"]').first
        if await vp.is_visible(timeout=3000):
            await vp.fill(password)
    except Exception:
        await page.evaluate(f"""() => {{
            const el = document.querySelector('[data-automation-id="verifyPassword"]');
            if (el) {{
                el.focus();
                el.value = {json.dumps(password)};
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}
        }}""")

    # Terms/privacy/consent checkboxes — use robust Workday handler
    from applicator.workday_handler import check_workday_consent
    checkbox_ok = await check_workday_consent(page, event_callback)
    if not checkbox_ok:
        await _log("Checkbox not confirmed. Create Account may fail.")

    await asyncio.sleep(0.5)

    # Submit create account — use mouse.click for trusted events (Workday click_filter)
    submit_clicked = await _multi_click(
        [
            'button[data-automation-id="createAccountSubmitButton"]',
            'div[data-automation-id="click_filter"][aria-label="Create Account"]',
            'div[role="button"][aria-label="Create Account"]',
        ],
        js_fallback="""() => {
            // Try the createAccountSubmitButton first
            let el = document.querySelector('[data-automation-id="createAccountSubmitButton"]');
            if (el && el.offsetParent !== null) { el.click(); return true; }
            // Try click_filter with Create Account
            el = document.querySelector('[data-automation-id="click_filter"][aria-label="Create Account"]');
            if (el && el.offsetParent !== null) { el.click(); return true; }
            // Dispatch full mouse event sequence as last resort
            for (const btn of document.querySelectorAll('button, div[role="button"]')) {
                const t = (btn.innerText || '').trim();
                if (t === 'Create Account' && btn.offsetParent !== null) {
                    btn.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));
                    btn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    btn.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
                    btn.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    btn.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                    return true;
                }
            }
            return false;
        }""",
        force=True,
        use_mouse=True,
    )

    if submit_clicked:
        await _log("Clicked Create Account submit")
        account_just_created = True
        await asyncio.sleep(8.0)
    else:
        await _log("Could not click Create Account submit")
        return False

    # --- Check for "account already exists" error ---
    already_exists = await page.evaluate("""() => {
        const t = document.body.innerText.toLowerCase();
        return t.includes('already in use') || t.includes('already exists')
            || t.includes('already have an account') || t.includes('sign into this account');
    }""")

    if already_exists:
        await _log("Account already exists, switching to sign in...")
        account_just_created = True  # Still prevent going back to Create Account

        # Click "Sign In" link at the bottom of Create Account dialog
        # ("Already have an account? Sign In")
        sign_in_link_clicked = await _multi_click(
            [
                'button[data-automation-id="signInLink"]',
                'a[data-automation-id="signInLink"]',
                'a:has-text("Sign In")',
            ],
            js_fallback="""() => {
                // Look for signInLink
                let el = document.querySelector('[data-automation-id="signInLink"]');
                if (el && el.offsetParent !== null) { el.click(); return true; }
                // Find "Sign In" link near "Already have an account?"
                for (const a of document.querySelectorAll('a, button')) {
                    const t = (a.innerText || '').trim();
                    if (t === 'Sign In' && a.offsetParent !== null) {
                        a.click(); return true;
                    }
                }
                return false;
            }""",
        )
        if sign_in_link_clicked:
            await _log("Clicked 'Sign In' link from Create Account dialog")
            await asyncio.sleep(3.0)
        else:
            await _log("Could not find Sign In link in Create Account dialog")

    # --- Step 5: Email verification (only if page shows actual verification prompt) ---
    try:
        needs_verification = await page.evaluate("""() => {
            const bodyText = (document.body.innerText || '').toLowerCase();
            // Must be a REAL verification prompt, not just "sign in with email"
            const verifyPhrases = [
                'verify your email', 'verification email', 'check your email',
                'sent a verification', 'confirm your email', 'verify your account',
                'enter the code', 'enter verification', 'verification code',
            ];
            return verifyPhrases.some(p => bodyText.includes(p));
        }""")
        if needs_verification:
            await _log("Email verification required...")
            # Use IMAP-based verification first (faster, more reliable)
            from applicator.email_handler import handle_email_verification, enter_verification_code
            result = await handle_email_verification(
                context=page.context,
                original_page=page,
                company_name="workday",
                event_callback=event_callback,
            )
            if result.get("success"):
                code = result.get("code")
                link = result.get("link")
                if code:
                    await _log(f"Got verification code: {code}")
                    await enter_verification_code(page, code, event_callback)
                elif link:
                    await _log("Got verification link, navigating...")
                    verify_page = await page.context.new_page()
                    try:
                        await verify_page.goto(link, wait_until="domcontentloaded", timeout=30000)
                        await verify_page.wait_for_timeout(5000)
                    finally:
                        await verify_page.close()
                await asyncio.sleep(3.0)
                await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(5.0)
            else:
                if event_callback:
                    await event_callback("Navigate", "warning",
                        "Auto-verify failed. Waiting up to 90s for manual verification...")
                for _ in range(90):
                    await asyncio.sleep(1.0)
                    try:
                        t = await page.evaluate("document.body.innerText")
                        if "verify" not in t.lower():
                            break
                    except Exception:
                        pass
    except Exception as e:
        await _log(f"Verification check error: {e}")

    # --- Step 6: IMMEDIATELY sign in after account creation ---
    # After Create Account submit (or "already exists"), we should now be on Sign In.
    # Do NOT check for createAccountLink — go straight to sign in.

    # First check if we need to click "Sign in with email" again
    try:
        has_email_btn_again = await page.evaluate("""() => {
            const buttons = document.querySelectorAll('button, a, div[role="button"]');
            for (const btn of buttons) {
                const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                if (text.includes('sign in with email') && btn.offsetParent !== null) {
                    return true;
                }
            }
            return false;
        }""")
    except Exception:
        has_email_btn_again = False

    if has_email_btn_again:
        await _log("Back on 'Sign in with email' page, clicking it again...")
        await _multi_click(
            [
                'button:has-text("Sign in with email")',
                'a:has-text("Sign in with email")',
                'div[role="button"]:has-text("Sign in with email")',
            ],
            js_fallback="""() => {
                const buttons = document.querySelectorAll('button, a, div[role="button"]');
                for (const btn of buttons) {
                    const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                    if (text.includes('sign in with email') && btn.offsetParent !== null) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""",
        )
        await asyncio.sleep(3.0)

    try:
        email_vis = await page.locator('input[data-automation-id="email"]').is_visible(timeout=5000)
    except Exception:
        email_vis = False

    if email_vis:
        await _log("Sign-in form visible after account creation. Signing in...")
        await _fill_creds()
        await asyncio.sleep(0.5)
        await _multi_click(
            [
                'div[data-automation-id="click_filter"][aria-label="Sign In"]',
                'button[data-automation-id="signInSubmitButton"]',
                'button:has-text("Sign In")',
            ],
            force=True,
            use_mouse=True,
        )
        await asyncio.sleep(5.0)

        if await _is_on_form():
            await _log_ok("Signed in after account creation!")
            return True

        # If still not on form, try clicking signInLink first then sign in
        if account_just_created:
            await _log("Still not on form. Trying signInLink...")
            await _multi_click(
                [
                    'button[data-automation-id="signInLink"]',
                    'a:has-text("Sign In")',
                ],
            )
            await asyncio.sleep(3.0)
            await _fill_creds()
            await asyncio.sleep(0.5)
            await _multi_click(
                [
                    'div[data-automation-id="click_filter"][aria-label="Sign In"]',
                    'button[data-automation-id="signInSubmitButton"]',
                    'button:has-text("Sign In")',
                ],
                force=True,
                use_mouse=True,
            )
            await asyncio.sleep(5.0)

    if await _is_on_form():
        await _log_ok("Auth completed, on application form!")
        return True

    await _log_ok("Auth flow completed")
    return True


async def _take_screenshot(page: Page) -> bytes:
    """Take a screenshot, return bytes."""
    try:
        return await page.screenshot(type="png")
    except Exception:
        return b""


def _is_dropdown_match(option_text: str, target_value: str) -> bool:
    """Check if a dropdown option matches the target value, with alias support."""
    opt = option_text.lower().strip()
    target = target_value.lower().strip()

    if not opt or not target:
        return False
    if opt == target or target in opt or opt in target:
        return True

    # Check aliases
    aliases = {
        "united states": ["us", "usa", "united states of america", "u.s.", "u.s.a.", "united states (us)", "united states of america (usa)", "us (+1)"],
        "no": ["none", "n/a", "not applicable"],
        "yes": ["y"],
        "male": ["man", "m", "he/him", "he / him", "he/him/his"],
        "female": ["woman", "f", "she/her", "she / her", "she/her/hers"],
        "job board": ["online", "internet", "website", "web", "other", "job site", "jobs board", "linkedin", "online job board"],
        "linkedin": ["job board", "online", "internet", "other", "online job board", "job site"],
        "asian": ["asian or pacific islander", "asian american", "asian (not hispanic)", "asian / pacific islander", "asian or asian american"],
        "bachelor": ["bachelor's", "bachelors", "bachelor's degree", "4-year degree", "undergraduate", "bachelor of science", "bs", "b.s."],
        "i do not wish to answer": ["prefer not to say", "decline to self-identify", "prefer not to answer", "decline", "choose not to disclose", "i don't wish to answer", "prefer not to disclose", "i choose not to disclose"],
        "i am not a protected veteran": ["not a veteran", "non-veteran", "i am not a veteran", "not a protected veteran", "i am not", "i am not a protected veteran (or) i am not a veteran", "no, i am not a protected veteran"],
        "california": ["ca", "calif", "calif."],
        "no, i don't have a disability": ["no disability", "i do not have a disability", "no, i don't have a disability, or a history/record of having a disability", "none"],
    }

    # Collect all acceptable matches
    acceptable = [target]
    for key, alias_list in aliases.items():
        if target == key or target in alias_list:
            acceptable = [key] + alias_list
            break

    for acc in acceptable:
        if acc in opt or opt in acc:
            return True
        # Check if all words appear
        words = acc.split()
        if len(words) > 1 and all(w in opt for w in words):
            return True

    return False


async def _handle_custom_dropdown(page, selector: str, value: str, event_callback=None) -> bool:
    """Universal dropdown handler for both native <select> and custom dropdowns.

    Strategy order:
    1. Try native select_option with exact and fuzzy matching
    2. Click to open + search/type + select from visible options
    3. Click to open + JS fuzzy match on all visible option elements
    4. Type into the dropdown and press Enter
    """
    # Strategy 1: native <select> with alias matching
    try:
        tag = await page.evaluate(f"document.querySelector('{selector}')?.tagName")
        if tag and tag.lower() == "select":
            # Get all options
            options = await page.evaluate(f"""() => {{
                const el = document.querySelector('{selector}');
                if (!el) return [];
                return Array.from(el.options).map(o => ({{value: o.value, text: o.text.trim()}}));
            }}""")

            # Find best match — prefer exact match, then starts-with, then fuzzy
            target_lower = value.lower().strip()
            best_match = None
            best_score = 0  # 3=exact, 2=starts-with, 1=fuzzy
            for opt in options:
                opt_lower = opt["text"].lower().strip()
                if not opt_lower or opt_lower in ("select", "select...", "choose", "--", ""):
                    continue
                if opt_lower == target_lower:
                    best_match = opt
                    best_score = 3
                    break
                if opt_lower.startswith(target_lower) and best_score < 2:
                    best_match = opt
                    best_score = 2
                elif _is_dropdown_match(opt["text"], value) and best_score < 1:
                    best_match = opt
                    best_score = 1

            if best_match:
                # Find the option index (skip placeholder options)
                target_idx = None
                for idx, opt in enumerate(options):
                    if opt["value"] == best_match["value"]:
                        target_idx = idx
                        break

                # Strategy 1a: Use keyboard navigation on native <select>
                # This triggers proper browser events that React picks up
                try:
                    loc = page.locator(selector).first
                    await loc.scroll_into_view_if_needed(timeout=3000)
                    await loc.click(timeout=3000)
                    await asyncio.sleep(0.3)
                    # Press Home to go to first option, then ArrowDown to target
                    await page.keyboard.press("Home")
                    await asyncio.sleep(0.1)
                    if target_idx is not None:
                        for _ in range(target_idx):
                            await page.keyboard.press("ArrowDown")
                            await asyncio.sleep(0.05)
                    await asyncio.sleep(0.1)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(0.5)

                    # Verify
                    actual_text = await page.evaluate(f"""() => {{
                        const el = document.querySelector('{selector}');
                        if (!el) return '';
                        return el.options[el.selectedIndex]?.text || '';
                    }}""")
                    if actual_text.strip().lower() == best_match["text"].strip().lower():
                        if event_callback:
                            await event_callback("Fill Form", "info", f"Selected (keyboard): {best_match['text'][:50]}")
                        return True
                    else:
                        if event_callback:
                            await event_callback("Fill Form", "info",
                                f"Keyboard select got '{actual_text}' instead of '{best_match['text']}', trying select_option...")
                except Exception as e:
                    if event_callback:
                        await event_callback("Fill Form", "info", f"Keyboard select error: {e}")

                # Strategy 1b: Fallback to programmatic select_option
                try:
                    await page.select_option(selector, value=best_match["value"], timeout=3000)
                    await asyncio.sleep(0.3)
                    if event_callback:
                        await event_callback("Fill Form", "info", f"Selected (select_option): {best_match['text'][:50]}")
                    return True
                except Exception as e:
                    if event_callback:
                        await event_callback("Fill Form", "info", f"select_option also failed: {e}")

            if not best_match:
                if event_callback:
                    opt_texts = [o["text"] for o in options[:10]]
                    await event_callback("Fill Form", "info", f"No match for '{value}' in select. Options: {opt_texts}")
                return False
            # If we got here, best_match was found but both strategies didn't work
            # Fall through to Strategy 2+
            if event_callback:
                await event_callback("Fill Form", "info", f"Native select failed for '{value}', trying click-based strategies...")
    except Exception:
        pass

    # Strategy 1.5: Workday button-based dropdowns (data-automation-id)
    try:
        is_workday_dd = await page.evaluate(f"""() => {{
            const el = document.querySelector('{selector}');
            if (!el) return false;
            const dataid = el.getAttribute('data-automation-id') || '';
            if (dataid.startsWith('formField-') || dataid.startsWith('multiSelectContainer')) return true;
            if (el.querySelector('[data-automation-id="searchBox"]')) return true;
            if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-haspopup') === 'listbox') return true;
            // Check parent
            const parent = el.closest('[data-automation-id^="formField-"]');
            if (parent) return true;
            return false;
        }}""")
        if is_workday_dd:
            if event_callback:
                await event_callback("Fill Form", "info", f"Workday dropdown detected for '{value[:30]}', trying click→type→select...")
            # Try clicking the dropdown button/icon to open it
            clicked_open = False
            for btn_sel in [
                f'{selector} button',
                f'{selector} [role="button"]',
                f'{selector} [data-automation-id="searchBox"]',
                f'{selector} svg',
                f'{selector} [aria-haspopup]',
                selector,  # click the container itself as last resort
            ]:
                try:
                    btn = page.locator(btn_sel).first
                    if await btn.is_visible(timeout=1000):
                        # Use real mouse click for Workday click_filter
                        box = await btn.bounding_box()
                        if box:
                            await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                        else:
                            await btn.click(timeout=3000)
                        clicked_open = True
                        await asyncio.sleep(0.8)
                        break
                except Exception:
                    continue

            if not clicked_open:
                if event_callback:
                    await event_callback("Fill Form", "info", "Could not click Workday dropdown open")
            else:
                # Type in search box if available
                for search_sel in [
                    '[data-automation-id="searchBox"] input',
                    'input[data-automation-id="searchBox"]',
                    f'{selector} input[type="text"]',
                    'input[role="combobox"]:visible',
                ]:
                    try:
                        wd_search = page.locator(search_sel).first
                        if await wd_search.is_visible(timeout=1000):
                            await wd_search.fill("", timeout=2000)
                            await asyncio.sleep(0.2)
                            await wd_search.fill(value, timeout=3000)
                            await asyncio.sleep(1.0)
                            break
                    except Exception:
                        continue

                # Click matching option
                for opt_sel in [
                    '[data-automation-id*="promptOption"]',
                    '[role="option"]',
                    '[data-automation-id="menuItem"]',
                    '[data-automation-id*="selectWidget"] [role="option"]',
                    'ul li:visible',
                ]:
                    try:
                        opts = page.locator(opt_sel)
                        count = await opts.count()
                        for i in range(min(count, 20)):
                            opt = opts.nth(i)
                            if await opt.is_visible(timeout=300):
                                text = await opt.inner_text()
                                if _is_dropdown_match(text, value):
                                    box = await opt.bounding_box()
                                    if box:
                                        await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                                    else:
                                        await opt.click(timeout=3000)
                                    if event_callback:
                                        await event_callback("Fill Form", "info", f"Workday dropdown: {text[:50]}")
                                    return True
                    except Exception:
                        continue

                # If no matching option found, try ArrowDown + Enter
                try:
                    first_opt = page.locator('[role="option"]:visible, [data-automation-id*="promptOption"]:visible').first
                    if await first_opt.is_visible(timeout=500):
                        text = await first_opt.inner_text()
                        box = await first_opt.bounding_box()
                        if box:
                            await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                        else:
                            await first_opt.click(timeout=3000)
                        if event_callback:
                            await event_callback("Fill Form", "info", f"Workday dropdown (first option): {text[:50]}")
                        return True
                except Exception:
                    pass
    except Exception:
        pass

    # Strategy 2: click to open, then search/type in filter input
    try:
        el = page.locator(selector).first
        await el.scroll_into_view_if_needed(timeout=3000)
        await el.click(timeout=3000)
        await asyncio.sleep(0.8)

        # Check for a search/filter input inside the dropdown
        search_selectors = [
            'input[type="search"]:visible',
            'input[role="combobox"]:visible',
            'input[aria-autocomplete]:visible',
            'input[placeholder*="Search" i]:visible',
            'input[placeholder*="Type" i]:visible',
            'input[placeholder*="Filter" i]:visible',
        ]
        for search_sel in search_selectors:
            try:
                search_input = page.locator(search_sel).first
                if await search_input.is_visible(timeout=500):
                    await search_input.fill(value, timeout=3000)
                    await asyncio.sleep(0.8)
                    # Click the first visible matching option
                    for opt_sel in ['[role="option"]', 'li', '[class*="option"]']:
                        try:
                            opts = page.locator(opt_sel)
                            count = await opts.count()
                            for i in range(min(count, 10)):
                                opt = opts.nth(i)
                                if await opt.is_visible(timeout=300):
                                    text = await opt.inner_text()
                                    if _is_dropdown_match(text, value):
                                        await opt.click(timeout=3000)
                                        if event_callback:
                                            await event_callback("Fill Form", "info", f"Selected: {text[:50]}")
                                        return True
                        except Exception:
                            continue
                    # If typed and there's a single visible option, click it
                    try:
                        first_opt = page.locator('[role="option"]:visible, li:visible').first
                        if await first_opt.is_visible(timeout=500):
                            text = await first_opt.inner_text()
                            if text.strip():
                                await first_opt.click(timeout=3000)
                                return True
                    except Exception:
                        pass
                    break
            except Exception:
                continue
    except Exception:
        pass

    # Strategy 3: JS fuzzy match on all visible option-like elements
    try:
        # Re-click to make sure dropdown is open
        try:
            el = page.locator(selector).first
            await el.click(timeout=2000)
            await asyncio.sleep(0.5)
        except Exception:
            pass

        clicked = await page.evaluate("""(targetValue) => {
            const aliases = {
                "united states": ["us", "usa", "united states of america", "u.s.", "u.s.a.", "united states (us)", "us (+1)"],
                "job board": ["online", "internet", "website", "other", "linkedin", "online job board", "job site"],
                "linkedin": ["job board", "online", "internet", "other", "online job board"],
                "asian": ["asian or pacific islander", "asian american", "asian (not hispanic)", "asian or asian american"],
                "no": ["none", "n/a", "not applicable"],
                "california": ["ca", "calif"],
                "male": ["man", "he/him", "he / him", "he/him/his"],
                "female": ["woman", "she/her", "she / her"],
                "bachelor": ["bachelor's", "bachelors", "bachelor's degree", "4-year degree", "undergraduate"],
                "i do not wish to answer": ["prefer not to say", "decline to self-identify", "decline", "prefer not to answer", "choose not to disclose", "i don't wish to answer"],
                "i am not a protected veteran": ["not a veteran", "i am not a veteran", "not a protected veteran", "non-veteran", "no, i am not a protected veteran"],
                "no, i don't have a disability": ["no disability", "i do not have a disability", "none"],
            };
            const target = targetValue.toLowerCase().trim();
            let acceptable = [target];
            for (const [key, alts] of Object.entries(aliases)) {
                if (target === key || alts.includes(target)) {
                    acceptable = [key, ...alts];
                    break;
                }
            }
            const candidates = document.querySelectorAll(
                '[role="option"], [role="listbox"] *, [role="menuitem"], ' +
                'li, [class*="option"], [class*="menu-item"], [class*="dropdown-item"], ' +
                '[data-automation-id*="promptOption"], [class*="listbox"] *'
            );
            for (const el of candidates) {
                if (el.offsetParent === null) continue;
                const text = (el.innerText || '').trim().toLowerCase();
                if (!text) continue;
                for (const acc of acceptable) {
                    if (text === acc || text.includes(acc) || acc.includes(text)) {
                        el.click();
                        return true;
                    }
                }
            }
            return false;
        }""", value)

        if clicked:
            await asyncio.sleep(0.5)
            return True
    except Exception:
        pass

    # Strategy 4: type into the dropdown element and press Enter
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
        el = page.locator(selector).first
        await el.click(timeout=2000)
        await page.keyboard.type(value, delay=50)
        await asyncio.sleep(1.0)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.5)
        return True
    except Exception:
        pass

    if event_callback:
        await event_callback("Fill Form", "info", f"Could not select '{value[:40]}' in dropdown {selector[:30]}")
    return False


async def _handle_phone_country(page_or_frame, target_country: str = "United States", event_callback=None) -> bool:
    """Handle intl-tel-input (ITI) phone country dropdown used by Greenhouse etc."""
    has_iti = await page_or_frame.evaluate("""() => {
        return document.querySelector('.iti__flag-container, .iti__selected-flag, [class*="intl-tel"]') !== null
            || (() => {
                for (const pf of document.querySelectorAll('input[type="tel"]')) {
                    const p = pf.parentElement;
                    if (p && (p.classList.contains('iti') || p.querySelector('.iti__flag'))) return true;
                }
                return false;
            })();
    }""")
    if not has_iti:
        return False

    try:
        # Check if US already selected
        is_us = await page_or_frame.evaluate("""() => {
            const flag = document.querySelector('.iti__selected-flag .iti__flag');
            if (flag) {
                const cls = Array.from(flag.classList);
                return cls.some(c => c === 'iti__us');
            }
            return false;
        }""")
        if is_us:
            if event_callback:
                await event_callback("Fill Form", "info", "Phone country already US")
            return True

        # Click flag to open dropdown
        flag_btn = page_or_frame.locator('.iti__selected-flag').first
        await flag_btn.click(timeout=3000)
        await asyncio.sleep(0.5)

        # Try search box if available
        try:
            search = page_or_frame.locator('.iti__search-input, .iti__country-list input').first
            if await search.is_visible(timeout=1000):
                await search.fill("United States")
                await asyncio.sleep(0.5)
        except Exception:
            pass

        # Click US option
        for sel in [
            '.iti__country-list li[data-country-code="us"]',
            '.iti__country-list li:has-text("United States")',
        ]:
            try:
                opt = page_or_frame.locator(sel).first
                if await opt.is_visible(timeout=2000):
                    await opt.click(timeout=3000)
                    if event_callback:
                        await event_callback("Fill Form", "info", "Selected US (+1) phone country")
                    return True
            except Exception:
                continue

        # JS fallback
        clicked = await page_or_frame.evaluate("""() => {
            const items = document.querySelectorAll('.iti__country-list li');
            for (const item of items) {
                if (item.getAttribute('data-country-code') === 'us') { item.click(); return true; }
            }
            return false;
        }""")
        if clicked:
            if event_callback:
                await event_callback("Fill Form", "info", "Selected US via JS")
            return True

        await page_or_frame.keyboard.press("Escape")
    except Exception as e:
        try:
            await page_or_frame.keyboard.press("Escape")
        except Exception:
            pass
        if event_callback:
            await event_callback("Fill Form", "info", f"Phone country error: {e}")
    return False


async def _handle_resume_upload(page_or_frame, resume_path: str, event_callback=None) -> bool:
    """Robust resume upload — delegates to workday_handler.upload_file_robust.

    Tries 4 strategies in order:
    1. Make hidden file inputs visible (+ parents), set_input_files, dispatch change+input
    2. Click "Select files" link, intercept file chooser
    3. Click drop zone div, intercept file chooser
    4. Programmatic drag-and-drop via JS DataTransfer
    """
    try:
        from applicator.workday_handler import upload_file_robust
        return await upload_file_robust(page_or_frame, resume_path, event_callback)
    except Exception:
        pass

    # Fallback if import fails — mirrors upload_file_robust logic
    if not resume_path or not os.path.exists(resume_path):
        if event_callback:
            await event_callback("Fill Form", "error", f"Resume not found: {resume_path}")
        return False

    abs_path = os.path.abspath(resume_path)
    fname = os.path.basename(abs_path)

    # Helper: check if upload succeeded
    async def _check_upload():
        return await page_or_frame.evaluate("""() => {
            for (const fi of document.querySelectorAll('input[type="file"]')) {
                if (fi.files && fi.files.length > 0) return fi.files[0].name;
            }
            for (const sel of ['.filename', '.file-name', '.resume-filename',
                               '.attachment-filename', '[data-automation-id="file-upload-item"]',
                               '[class*="upload-success"]', '[class*="attachment"]']) {
                const el = document.querySelector(sel);
                if (el && el.offsetParent !== null && el.innerText.trim()) return el.innerText.trim();
            }
            return '';
        }""")

    # Check if already uploaded
    already = await _check_upload()
    if already:
        if event_callback:
            await event_callback("Fill Form", "info", f"Resume already uploaded: {already}")
        return True

    # STRATEGY 1: Make hidden file inputs visible (+ parents), set_input_files, dispatch events
    try:
        file_count = await page_or_frame.evaluate("""() => {
            const inputs = document.querySelectorAll('input[type="file"]');
            for (const inp of inputs) {
                inp.removeAttribute('hidden');
                inp.style.cssText = 'display:block!important;visibility:visible!important;opacity:1!important;position:relative!important;width:200px!important;height:30px!important;z-index:99999!important;';
                let el = inp.parentElement;
                for (let d = 0; el && d < 5; d++, el = el.parentElement) {
                    const s = getComputedStyle(el);
                    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') {
                        el.style.display = 'block';
                        el.style.visibility = 'visible';
                        el.style.opacity = '1';
                    }
                    if (el.hasAttribute('hidden')) el.removeAttribute('hidden');
                }
                if (inp.hasAttribute('accept')) inp.removeAttribute('accept');
            }
            return inputs.length;
        }""")
        if event_callback:
            await event_callback("Fill Form", "info", f"Found {file_count} file input(s)")

        for i in range(file_count):
            ctx_lower = await page_or_frame.evaluate(f"""() => {{
                const el = document.querySelectorAll('input[type="file"]')[{i}];
                if (!el) return '';
                const parent = el.closest('.field, .application-field, .form-group, li, div[class*="upload"]');
                if (parent) {{
                    const lbl = parent.querySelector('label, .field-label, h3, h4');
                    if (lbl) return lbl.innerText.trim().toLowerCase();
                }}
                return '';
            }}""")

            is_resume = "resume" in (ctx_lower or "") or "cv" in (ctx_lower or "") or i == 0
            if not is_resume and file_count > 1:
                continue

            fi = page_or_frame.locator('input[type="file"]').nth(i)
            await fi.set_input_files(abs_path, timeout=10000)
            await asyncio.sleep(1)
            # Dispatch change+input events
            await page_or_frame.evaluate(f"""() => {{
                const inp = document.querySelectorAll('input[type="file"]')[{i}];
                if (inp && inp.files && inp.files.length > 0) {{
                    inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                    inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                }}
            }}""")
            await asyncio.sleep(3)

            has = await _check_upload()
            if has:
                if event_callback:
                    await event_callback("Fill Form", "success", f"Strategy 1 (file input {i}): {has}")
                return True
    except Exception as e:
        if event_callback:
            await event_callback("Fill Form", "info", f"Strategy 1 failed: {e}")

    # STRATEGY 2: Click "Select files" / "Attach" link + file chooser
    for sel in [
        '[data-automation-id="file-upload-drop-zone"] a',
        'a:has-text("Select files")', 'button:has-text("Select files")',
        'button:has-text("Attach")', 'button:has-text("Upload")',
        'button:has-text("Choose File")', 'a:has-text("Attach")',
        'a:has-text("Upload")', 'label:has-text("Attach")',
        'label:has-text("Upload")', 'a.attachment-link',
        '[class*="upload"] a', '[class*="upload"] button',
    ]:
        try:
            btn = page_or_frame.locator(sel).first
            if not await btn.is_visible(timeout=1500):
                continue
            async with page_or_frame.expect_file_chooser(timeout=8000) as fc:
                await btn.click(force=True, timeout=5000)
            chooser = await fc.value
            await chooser.set_files(abs_path)
            await asyncio.sleep(5)
            has = await _check_upload()
            if event_callback:
                await event_callback("Fill Form", "success", f"Strategy 2 (file chooser): {sel[:40]}" + (f" ({has})" if has else ""))
            return True
        except Exception:
            continue

    # STRATEGY 3: Click drop zone + file chooser
    for sel in [
        '[data-automation-id="file-upload-drop-zone"]',
        '[class*="dropzone"]', '[class*="drop-zone"]', '[class*="drop_zone"]',
        '[class*="file-upload"]', '[class*="resume-upload"]',
    ]:
        try:
            zone = page_or_frame.locator(sel).first
            if not await zone.is_visible(timeout=1500):
                continue
            async with page_or_frame.expect_file_chooser(timeout=8000) as fc:
                await zone.click(force=True, timeout=5000)
            chooser = await fc.value
            await chooser.set_files(abs_path)
            await asyncio.sleep(5)
            has = await _check_upload()
            if event_callback:
                await event_callback("Fill Form", "success", f"Strategy 3 (drop zone): {sel[:40]}" + (f" ({has})" if has else ""))
            return True
        except Exception:
            continue

    # STRATEGY 4: Programmatic drag-and-drop via JS
    try:
        import base64
        with open(abs_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        dropped = await page_or_frame.evaluate("""(args) => {
            const [b64, name] = args;
            const bin = atob(b64);
            const bytes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            const ext = name.split('.').pop().toLowerCase();
            const mimeMap = {pdf:'application/pdf', doc:'application/msword', docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document', txt:'text/plain', rtf:'application/rtf'};
            const file = new File([bytes], name, {type: mimeMap[ext] || 'application/octet-stream'});
            const dt = new DataTransfer();
            dt.items.add(file);
            let ok = false;
            const fi = document.querySelector('input[type="file"]');
            if (fi) {
                fi.files = dt.files;
                fi.dispatchEvent(new Event('change', {bubbles: true}));
                fi.dispatchEvent(new Event('input', {bubbles: true}));
                ok = fi.files.length > 0;
            }
            for (const zs of ['[data-automation-id="file-upload-drop-zone"]','[class*="dropzone"]','[class*="drop-zone"]','[class*="file-upload"]']) {
                const zone = document.querySelector(zs);
                if (!zone) continue;
                const rect = zone.getBoundingClientRect();
                const cx = rect.left + rect.width / 2;
                const cy = rect.top + rect.height / 2;
                const evtInit = {dataTransfer: dt, bubbles: true, cancelable: true, clientX: cx, clientY: cy};
                zone.dispatchEvent(new DragEvent('dragenter', evtInit));
                zone.dispatchEvent(new DragEvent('dragover', evtInit));
                zone.dispatchEvent(new DragEvent('drop', evtInit));
                ok = true;
            }
            return ok ? 'ok' : 'no target';
        }""", [b64, fname])
        if dropped == "ok":
            await asyncio.sleep(5)
            result = await _check_upload()
            if result:
                if event_callback:
                    await event_callback("Fill Form", "success", f"Strategy 4 (drag-drop): {result}")
                return True
    except Exception as e:
        if event_callback:
            await event_callback("Fill Form", "info", f"Strategy 4 failed: {e}")

    if event_callback:
        await event_callback("Fill Form", "warning", "All resume upload strategies failed")
    return False


async def _highlight_element(page, selector: str):
    """Flash an orange outline on an element so the user can see what's being filled.
    Mimics browser-use's visual indicator. Non-blocking, best-effort."""
    try:
        await page.evaluate("""(sel) => {
            const el = document.querySelector(sel);
            if (!el) return;
            el.scrollIntoView({behavior: 'smooth', block: 'center'});
            const prev = el.style.cssText;
            el.style.outline = '3px solid #FF6611';
            el.style.outlineOffset = '2px';
            el.style.boxShadow = '0 0 8px rgba(255,102,17,0.6)';
            el.style.transition = 'outline 0.2s, box-shadow 0.2s';
            setTimeout(() => { el.style.cssText = prev; }, 1500);
        }""", selector)
    except Exception:
        pass


async def fill_form(
    page,
    mappings: list[dict],
    resume_path: str,
    transcript_path: str = "",
    event_callback=None,
    screenshot_callback=None,
    screenshot_page=None,
) -> dict:
    """Fill form fields using Playwright based on LLM mappings."""
    # Filter out non-dict elements (LLM may return malformed JSON with strings)
    mappings = [m for m in mappings if isinstance(m, dict)]

    # Safety: remove any mappings that target navigation/utility elements
    _NAV_BLOCKLIST = [
        'navigationItem', 'utilityMenuButton', 'settingsButton', 'signOut',
        'signIn', 'searchButton', 'globalSearch', 'headerWrapper', 'inboxButton',
        'tasksButton', 'helpButton', 'profileImage', 'Candidate Home', 'Search for Job',
    ]
    safe_mappings = []
    for m in mappings:
        sel = m.get("selector", "")
        if any(blocked in sel for blocked in _NAV_BLOCKLIST):
            if event_callback:
                import asyncio as _aio
                _aio.ensure_future(event_callback("Fill Form", "warning", f"Blocked nav element: {sel[:60]}"))
            continue
        safe_mappings.append(m)
    mappings = safe_mappings
    filled = 0
    skipped = 0
    failed = 0
    errors = []

    # Determine the top-level page for highlights (form_ctx might be a Frame)
    highlight_page = screenshot_page or page

    # Post-process: normalize phone numbers for Workday (digits only, no parens/dashes)
    is_workday_page = 'myworkdayjobs' in (page.url or '')
    if is_workday_page:
        import re as _re
        for m in mappings:
            if m.get("action") == "fill" and m.get("value"):
                label_lower = (m.get("label") or "").lower()
                sel_lower = (m.get("selector") or "").lower()
                if "phone" in label_lower or "phone" in sel_lower:
                    raw = m["value"]
                    # Strip to digits only (remove parens, dashes, spaces, dots)
                    digits = _re.sub(r'[^\d]', '', raw)
                    # Remove leading 1 if it's a US number with country code
                    if len(digits) == 11 and digits.startswith('1'):
                        digits = digits[1:]
                    if digits and digits != raw:
                        m["value"] = digits

    # Post-process: fix action type for <select> elements that LLM may have mapped as "fill"
    for m in mappings:
        if m.get("action") == "fill" and m.get("selector") and m.get("value"):
            try:
                tag = await page.evaluate(
                    f"document.querySelector('{m['selector']}')?.tagName?.toLowerCase()"
                )
                if tag == "select":
                    m["action"] = "select"
            except Exception:
                pass

    # Post-process: convert Workday dropdown fields from "fill" to "select"
    # Workday searchable dropdowns (promptList) have buttons/icons and need click→type→select
    for m in mappings:
        if m.get("action") == "fill" and m.get("selector") and m.get("value"):
            try:
                result = await page.evaluate(f"""(sel) => {{
                    const el = document.querySelector(sel);
                    if (!el) return {{isWdDd: false}};
                    // Helper: check if a container has dropdown affordances
                    function hasDropdownAffordance(container) {{
                        if (!container) return false;
                        return !!(
                            container.querySelector('button') ||
                            container.querySelector('[role="button"]') ||
                            container.querySelector('[data-automation-id="searchBox"]') ||
                            container.querySelector('[data-automation-id*="promptIcon"]') ||
                            container.querySelector('[data-automation-id*="dropdown"]') ||
                            container.querySelector('[aria-haspopup]') ||
                            container.querySelector('svg') // icon button for dropdown
                        );
                    }}
                    // Check direct element
                    const dataid = el.getAttribute('data-automation-id') || '';
                    if ((dataid.startsWith('formField-') || dataid.startsWith('multiSelectContainer'))
                        && hasDropdownAffordance(el)) {{
                        return {{isWdDd: true}};
                    }}
                    // Check if this is an input with role=combobox
                    if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-haspopup') === 'listbox') {{
                        const parent = el.closest('[data-automation-id^="formField-"], [data-automation-id^="multiSelectContainer"]');
                        if (parent) {{
                            const pid = parent.getAttribute('data-automation-id');
                            return {{isWdDd: true, parentSelector: '[data-automation-id="' + pid + '"]'}};
                        }}
                        return {{isWdDd: true}};
                    }}
                    // Check parent container (input inside a Workday dropdown)
                    const parent = el.closest('[data-automation-id^="formField-"], [data-automation-id^="multiSelectContainer"]');
                    if (parent && hasDropdownAffordance(parent)) {{
                        const pid = parent.getAttribute('data-automation-id');
                        return {{isWdDd: true, parentSelector: '[data-automation-id="' + pid + '"]'}};
                    }}
                    return {{isWdDd: false}};
                }}""", m["selector"])
                if result.get("isWdDd"):
                    m["action"] = "select"
                    # Use parent container selector for Workday dropdown handler
                    if result.get("parentSelector"):
                        m["selector"] = result["parentSelector"]
            except Exception:
                pass

    # Post-process: skip Workday email field if it already has a value (clicking it opens Change Email panel)
    if is_workday_page:
        for m in mappings:
            if m.get("action") == "fill" and m.get("value"):
                sel = m.get("selector", "")
                label_lower = (m.get("label") or "").lower()
                if "email" in label_lower or "email" in sel.lower():
                    try:
                        current_val = await page.evaluate(f"""() => {{
                            const el = document.querySelector('{sel}');
                            if (!el) return '';
                            return (el.value || el.textContent || '').trim();
                        }}""")
                        if current_val and '@' in current_val:
                            m["action"] = "skip"
                            if event_callback:
                                import asyncio as _aio
                                _aio.ensure_future(event_callback(
                                    "Fill Form", "info",
                                    f"Skipping email field (already has: {current_val[:30]}) to prevent Change Email panel"
                                ))
                    except Exception:
                        pass

    # Post-process: prevent resume/long text from being pasted into cover letter or textarea fields
    for m in mappings:
        if m.get("action") == "fill" and m.get("value"):
            label_lower = m.get("label", "").lower()
            value = m.get("value", "")
            # Skip if this looks like resume content pasted into a cover letter field
            is_cover_letter = any(kw in label_lower for kw in [
                "cover letter", "cover_letter", "additional info", "anything else",
                "is there anything", "additional comments"
            ])
            # Detect resume-like content: very long text with bullet-point patterns
            looks_like_resume = len(value) > 500 and any(kw in value.lower() for kw in [
                "experience", "education", "gpa", "university", "bachelor",
                "technical skills", "programming", "hackathon"
            ])
            if is_cover_letter or looks_like_resume:
                if event_callback:
                    import asyncio as _aio
                    _aio.ensure_future(event_callback(
                        "Fill Form", "warning",
                        f"Blocked resume/long text from being pasted into '{label_lower[:40]}' — skipping"
                    ))
                m["action"] = "skip"

    # Post-process: prevent resume upload to cover letter / non-resume file inputs
    for m in mappings:
        if m.get("action") == "upload_file":
            label_lower = m.get("label", "").lower()
            is_resume_field = any(kw in label_lower for kw in ["resume", "cv"])
            is_transcript_field = "transcript" in label_lower
            if not is_resume_field and not is_transcript_field:
                if event_callback:
                    import asyncio as _aio
                    _aio.ensure_future(event_callback(
                        "Fill Form", "warning",
                        f"Blocked file upload to non-resume field '{label_lower[:40]}' — skipping"
                    ))
                m["action"] = "skip"

    for m in mappings:
        selector = m.get("selector", "")
        action = m.get("action", "skip")
        value = m.get("value", "")

        if action == "skip" or not selector:
            skipped += 1
            continue

        # Flash orange highlight on the target element
        await _highlight_element(highlight_page, selector)

        try:
            if action == "fill":
                label_lower = m.get("label", "").lower()
                is_location = ("location" in selector.lower() or "location" in label_lower
                               or "city" in label_lower)
                is_school = any(k in label_lower for k in ["school", "university", "college", "institution", "degree", "major", "discipline"])
                try:
                    loc = page.locator(selector).first
                    await loc.scroll_into_view_if_needed(timeout=3000)
                    if is_location or is_school:
                        # Autocomplete fields: click, type, then pick first matching suggestion
                        await loc.click(timeout=3000)
                        await loc.fill("", timeout=2000)  # Clear first
                        # For school, type enough to get a unique match
                        type_val = value[:20] if is_school else value
                        await page.keyboard.type(type_val, delay=80)
                        await asyncio.sleep(2.0)
                        # Look through visible autocomplete suggestions for one matching CA/California/USA
                        picked = await page.evaluate("""() => {
                            const suggestions = document.querySelectorAll(
                                '[role="option"], [role="listbox"] li, .pac-item, .pac-container .pac-item, ' +
                                '.autocomplete-suggestion, [class*="suggestion"], [class*="Suggestion"], ' +
                                '[class*="dropdown"] li, [class*="listbox"] li, ul[class*="auto"] li'
                            );
                            // preferred keywords cover both location and school lookups
                            const preferred = ["santa clara", "california", "ca,", "ca ", ", ca", "united states", "usa"];
                            const avoid = ["peru", "cuba", "colombia", "mexico", "brazil", "chile", "argentina"];
                            for (const s of suggestions) {
                                if (s.offsetParent === null) continue;
                                const text = (s.innerText || s.textContent || '').toLowerCase();
                                if (!text) continue;
                                // Check if this suggestion matches California/US
                                for (const p of preferred) {
                                    if (text.includes(p)) {
                                        s.click();
                                        return text.trim().substring(0, 80);
                                    }
                                }
                            }
                            // If no preferred match found, check first suggestion doesn't contain avoided countries
                            for (const s of suggestions) {
                                if (s.offsetParent === null) continue;
                                const text = (s.innerText || s.textContent || '').toLowerCase();
                                if (!text) continue;
                                let bad = false;
                                for (const a of avoid) {
                                    if (text.includes(a)) { bad = true; break; }
                                }
                                if (!bad) {
                                    s.click();
                                    return text.trim().substring(0, 80);
                                }
                            }
                            return null;
                        }""")
                        if picked:
                            if event_callback:
                                lbl_kind = "School" if is_school else "Location"
                                await event_callback("Fill Form", "info", f"{lbl_kind} autocomplete: {picked[:60]}")
                            await asyncio.sleep(0.5)
                        else:
                            # Fallback: ArrowDown + Enter for first suggestion
                            await page.keyboard.press("ArrowDown")
                            await asyncio.sleep(0.3)
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(0.5)
                        # If autocomplete cleared the value, just set it directly
                        current = await loc.input_value()
                        if not current:
                            await page.fill(selector, value, timeout=3000)
                    else:
                        # Check if this is a Workday input (needs keyboard.type for React)
                        is_wd_input = 'data-automation-id' in selector or 'myworkdayjobs' in (page.url or '')
                        if is_wd_input:
                            await loc.click(timeout=3000)
                            await asyncio.sleep(0.1)
                            # Triple-click to select all (works on Mac & Linux; Control+a is emacs-home on Mac)
                            box = await loc.bounding_box()
                            if box:
                                await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2, click_count=3)
                            else:
                                await page.keyboard.press("Meta+a")
                            await asyncio.sleep(0.15)
                            await page.keyboard.type(value, delay=30)
                            await asyncio.sleep(0.3)
                            await page.keyboard.press("Tab")  # Blur to trigger validation
                        else:
                            await page.fill(selector, value, timeout=5000)
                except Exception:
                    # Fallback: use JS to set value + keyboard.type for Workday
                    try:
                        loc = page.locator(selector).first
                        await loc.click(timeout=3000)
                        await asyncio.sleep(0.1)
                        box = await loc.bounding_box()
                        if box:
                            await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2, click_count=3)
                        else:
                            await page.keyboard.press("Meta+a")
                        await asyncio.sleep(0.15)
                        await page.keyboard.type(value, delay=30)
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass
                    try:
                        await page.evaluate(
                            """(args) => {
                                const [sel, val] = args;
                                const el = document.querySelector(sel);
                                if (el) {
                                    el.focus();
                                    if (el.contentEditable === 'true') {
                                        el.textContent = val;
                                    } else {
                                        el.value = val;
                                    }
                                    el.dispatchEvent(new Event('input', {bubbles: true}));
                                    el.dispatchEvent(new Event('change', {bubbles: true}));
                                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                                }
                            }""",
                            [selector, value]
                        )
                    except Exception:
                        pass
                filled += 1
                if event_callback:
                    label = m.get("label", selector)
                    await event_callback("Fill Form", "info", f"Filled: {label[:50]}")

            elif action == "select":
                success = await _handle_custom_dropdown(page, selector, value, event_callback)
                if success:
                    filled += 1
                    if event_callback:
                        await event_callback("Fill Form", "info", f"Selected: {value[:50]}")
                else:
                    failed += 1
                    errors.append(f"Dropdown {selector}: could not select '{value}'")
                    if event_callback:
                        await event_callback("Fill Form", "info", f"Dropdown failed: {value[:50]}")

            elif action == "click":
                click_ok = False
                try:
                    loc = page.locator(selector).first
                    await loc.scroll_into_view_if_needed(timeout=3000)
                    await loc.click(timeout=5000)
                    click_ok = True
                except Exception:
                    pass
                if not click_ok:
                    # Fallback: use JS click with proper escaping
                    try:
                        click_ok = await page.evaluate(
                            "sel => { const el = document.querySelector(sel); if (el) { el.click(); return true; } return false; }",
                            selector
                        )
                    except Exception:
                        pass
                if not click_ok:
                    # Fallback: try by name + value (for radio/checkbox with [name="x"][value="y"] selectors)
                    try:
                        import re as _re
                        name_match = _re.search(r'\[name="([^"]*)"\]', selector)
                        value_match = _re.search(r'\[value="([^"]*)"\]', selector)
                        if name_match and value_match:
                            click_ok = await page.evaluate("""(args) => {
                                const [fieldName, fieldValue] = args;
                                const inputs = document.querySelectorAll('input');
                                for (const inp of inputs) {
                                    if (inp.name === fieldName && inp.value === fieldValue) {
                                        inp.click();
                                        return true;
                                    }
                                }
                                return false;
                            }""", [name_match.group(1), value_match.group(1)])
                    except Exception:
                        pass
                if not click_ok:
                    # Last resort: try by label text (for radio/checkbox)
                    try:
                        import re as _re
                        label_match = _re.search(r'label="([^"]*)"', selector)
                        if label_match:
                            label_text = label_match.group(1).split(" :: ")[-1].strip()
                            name_match = _re.search(r'name="([^"]*)"', selector)
                            if name_match:
                                field_name = name_match.group(1)
                                click_ok = await page.evaluate("""(args) => {
                                    const [fieldName, labelText] = args;
                                    const inputs = document.querySelectorAll('input[name="' + fieldName + '"]');
                                    for (const inp of inputs) {
                                        const wrapper = inp.closest('li, div, label');
                                        if (wrapper && wrapper.innerText.trim().includes(labelText)) {
                                            inp.click();
                                            return true;
                                        }
                                    }
                                    return false;
                                }""", [field_name, label_text])
                    except Exception:
                        pass
                if click_ok:
                    filled += 1
                    if event_callback:
                        await event_callback("Fill Form", "info", f"Clicked: {selector[:50]}")
                else:
                    failed += 1
                    errors.append(f"{selector[:60]}: click failed")
                    if event_callback:
                        await event_callback("Fill Form", "info", f"Click failed: {selector[:50]}")

            elif action == "upload_file":
                try:
                    # Check if file inputs still exist — if not, upload already succeeded
                    has_file_inputs = await page.evaluate("document.querySelectorAll('input[type=\"file\"]').length > 0")
                    if not has_file_inputs:
                        if event_callback:
                            await event_callback("Upload", "info", "File already uploaded (no file inputs on page)")
                        filled += 1
                    else:
                        file_path = resume_path
                        file_label = "resume"
                        if value == "transcript" and transcript_path:
                            file_path = transcript_path
                            file_label = "transcript"

                        success = await _handle_resume_upload(page, file_path, event_callback)
                        if success:
                            filled += 1
                        else:
                            failed += 1
                            errors.append(f"{file_label} upload failed")
                except Exception as e:
                    errors.append(f"File upload error: {e}")
                    failed += 1

            # Take screenshot after each successful action for live feed
            if screenshot_callback and screenshot_page:
                ss = await _take_screenshot(screenshot_page)
                if ss:
                    await screenshot_callback(ss)

        except Exception as e:
            failed += 1
            errors.append(f"{selector}: {e}")
            if event_callback:
                await event_callback("Fill Form", "info", f"Failed: {selector[:40]} - {str(e)[:60]}")

    # Post-fill: handle phone country dropdown (ITI) if present
    try:
        await _handle_phone_country(page, "United States", event_callback)
    except Exception:
        pass

    # Post-fill: verify resume was uploaded, retry if not
    try:
        resume_ok = await page.evaluate("""() => {
            // Check if file inputs have files
            for (const fi of document.querySelectorAll('input[type="file"]')) {
                if (fi.files && fi.files.length > 0) return true;
            }
            // Check common upload confirmation selectors
            for (const sel of ['.filename', '.file-name', '.resume-filename', '.attachment-filename',
                               '[data-automation-id="file-upload-item"]', '[class*="upload-success"]',
                               '[class*="attachment"]']) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim()) return true;
            }
            // Greenhouse: after upload, the file input is removed and replaced with
            // a filename display. If there are NO file inputs at all, the upload likely
            // succeeded (the form originally had one).
            const fileInputs = document.querySelectorAll('input[type="file"]');
            if (fileInputs.length === 0) return true;
            return false;
        }""")
        if not resume_ok and resume_path:
            if event_callback:
                await event_callback("Fill Form", "info", "Resume not detected after fill, retrying upload...")
            await _handle_resume_upload(page, resume_path, event_callback)
    except Exception:
        pass

    return {"filled": filled, "skipped": skipped, "failed": failed, "errors": errors}


async def _start_playwright_windows():
    """Start Playwright on Windows by running the subprocess spawn in a separate thread
    with its own event loop that supports subprocess creation."""
    import concurrent.futures

    def _start_in_thread():
        # Create a new event loop with ProactorEventLoop (supports subprocesses on Windows)
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            from playwright.async_api import async_playwright as ap
            pw = loop.run_until_complete(ap().start())
            return pw, loop
        except Exception:
            loop.close()
            raise

    # Run the playwright startup in a thread with its own ProactorEventLoop
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_start_in_thread)
        pw, _thread_loop = future.result(timeout=30)

    return pw


async def fill_application(
    url: str,
    company: str,
    role: str,
    resume_path: str,
    job_description: str,
    event_callback=None,
    screenshot_callback=None,
    transcript_path: str = "",
) -> dict:
    """
    Main orchestrator: navigate, extract, map, fill.
    Returns dict with browser, page, and summary.
    """
    global _playwright, _browser

    # Launch browser
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(headless=os.getenv("HEADLESS", "false").lower() == "true")
    page = await _browser.new_page(viewport={"width": 1280, "height": 900})

    if event_callback:
        await event_callback("Navigate", "info", "Browser launched")

    # Detect ATS before navigating
    ats_key = detect_ats(url)
    ats_profile = get_profile(ats_key) if ats_key else None
    if event_callback and ats_key:
        ats_name = ats_profile.get("name", ats_key) if ats_profile else ats_key
        await event_callback("Navigate", "info", f"Detected ATS: {ats_name}")

    # Navigate
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # Wait a bit for JS rendering
    await asyncio.sleep(3.0)

    if event_callback:
        await event_callback("Navigate", "success", "Page loaded")

    # Dismiss cookie banners
    dismissed = await _dismiss_cookie_banners(page)
    if dismissed and event_callback:
        await event_callback("Navigate", "info", "Dismissed cookie banner")

    # Handle ATS-specific authentication (account creation / sign-in)
    if ats_profile and ats_profile.get("account_required"):
        if ats_key == "workday" or ats_key == "myworkdayjobs":
            pass  # Workday auth is handled later in its own flow
        else:
            auth_ok = await _handle_ats_auth(page, ats_key, event_callback)
            if not auth_ok and event_callback:
                await event_callback("Navigate", "warning", f"Auth for {ats_key} may have failed, continuing anyway...")

    # Screenshot
    if screenshot_callback:
        ss = await _take_screenshot(page)
        await screenshot_callback(ss)

    # CAPTCHA detection removed — was producing false positives and blocking the pipeline

    # Scroll down incrementally to load lazy content (Greenhouse, etc.)
    page_height = await page.evaluate("document.body.scrollHeight")
    viewport_height = 900
    scroll_pos = 0
    while scroll_pos < page_height:
        scroll_pos += viewport_height
        await page.evaluate(f"window.scrollTo(0, {scroll_pos})")
        await asyncio.sleep(0.5)
        # Page might grow as we scroll
        page_height = await page.evaluate("document.body.scrollHeight")
    await asyncio.sleep(1.0)
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(0.5)

    # Extract form fields - try main page first, then iframes
    if event_callback:
        await event_callback("Extract Fields", "start", "Analyzing form structure...")

    fields = await page.evaluate(JS_EXTRACT_FIELDS)
    form_context = page  # which context to fill fields in

    # If no fields found on main page, check iframes (Greenhouse, Workday, etc.)
    if len(fields) == 0:
        if event_callback:
            await event_callback("Extract Fields", "info", "No fields on main page, checking iframes...")

        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                frame_fields = await frame.evaluate(JS_EXTRACT_FIELDS)
                if len(frame_fields) > len(fields):
                    fields = frame_fields
                    form_context = frame
                    if event_callback:
                        await event_callback("Extract Fields", "info",
                            f"Found {len(fields)} fields in iframe: {frame.url[:60]}")
            except Exception:
                continue

    # If no fields found, try clicking an "Apply" button (job listing pages)
    if len(fields) == 0:
        if event_callback:
            await event_callback("Extract Fields", "info", "No form found, looking for Apply button...")

        apply_clicked = False
        apply_selectors = [
            'a:has-text("Apply for this job")',
            'a:has-text("Apply Now")',
            'a:has-text("Apply on company site")',
            'a:has-text("Apply to this job")',
            'button:has-text("Apply for this job")',
            'button:has-text("Apply Now")',
            'button:has-text("Apply to this job")',
            'button:has-text("Apply")',
            'a:has-text("Apply")',
            '[data-qa="btn-apply"]',
            '.postings-btn',
            'a.postings-btn',
            # SmartRecruiters - uses "I'm interested" instead of "Apply"
            'a:has-text("I\'m interested")',
            'button:has-text("I\'m interested")',
            'a[href*="smartr.me"]',
            'a.apply-btn',
            'button.js-apply-btn',
            '[data-test="apply-button"]',
            # iCIMS
            '.iCIMS_PrimaryButton',
            'a.iCIMS_PrimaryButton',
        ]
        # Scroll down to find apply button
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.0)

        for sel in apply_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    # Check if it's a link that navigates to a new page
                    href = await btn.get_attribute("href") or ""
                    if href and href.startswith("http"):
                        # Navigate directly instead of clicking (avoids popup blockers)
                        await page.goto(href, wait_until="domcontentloaded", timeout=30000)
                        apply_clicked = True
                        if event_callback:
                            await event_callback("Extract Fields", "info", f"Navigated to application form: {page.url[:60]}")
                        await asyncio.sleep(5.0)
                        # Dismiss cookies on the new page too
                        await _dismiss_cookie_banners(page)
                    else:
                        await btn.click(timeout=5000)
                        apply_clicked = True
                        if event_callback:
                            await event_callback("Extract Fields", "info", "Clicked Apply button, waiting for form...")
                        await asyncio.sleep(5.0)
                    break
            except Exception:
                continue

        # CAPTCHA detection removed — was producing false positives and blocking the pipeline

        if apply_clicked:
            await asyncio.sleep(2.0)

            # Handle iCIMS email login gate
            try:
                email_input = page.locator('#email, input[name*="loginName"], input[name*="email"]')
                if await email_input.first.is_visible(timeout=2000):
                    creds = _load_credentials()
                    ats_creds = creds.get(ats_key or "", creds.get("icims", {}))
                    login_email = ats_creds.get("email", "")
                    if login_email:
                        await email_input.first.fill(login_email)
                        # Click submit/continue
                        submit = page.locator('#enterEmailSubmitButton, button[type="submit"], input[type="submit"]')
                        if await submit.first.is_visible(timeout=2000):
                            await submit.first.click()
                            await asyncio.sleep(5.0)
                        # Check for password field (existing account)
                        pw_field = page.locator('input[type="password"]')
                        if await pw_field.first.is_visible(timeout=3000):
                            pw = ats_creds.get("password", "")
                            if pw:
                                await pw_field.first.fill(pw)
                                submit2 = page.locator('button[type="submit"], input[type="submit"]')
                                if await submit2.first.is_visible(timeout=2000):
                                    await submit2.first.click()
                                    await asyncio.sleep(5.0)
                        if event_callback:
                            await event_callback("Extract Fields", "info", f"Logged in, now at: {page.url[:60]}")
            except Exception:
                pass

        # After clicking apply, re-extract from main page and iframes
        fields = await page.evaluate(JS_EXTRACT_FIELDS)
        form_context = page
        if len(fields) == 0:
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                try:
                    frame_fields = await frame.evaluate(JS_EXTRACT_FIELDS)
                    if len(frame_fields) > len(fields):
                        fields = frame_fields
                        form_context = frame
                except Exception:
                    continue

    # If still no fields, try waiting longer for JS-heavy pages
    if len(fields) == 0:
        if event_callback:
            await event_callback("Extract Fields", "info", "No fields yet, waiting for JS to render...")
        await asyncio.sleep(5.0)
        fields = await page.evaluate(JS_EXTRACT_FIELDS)
        form_context = page
        # Check iframes again
        if len(fields) == 0:
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                try:
                    frame_fields = await frame.evaluate(JS_EXTRACT_FIELDS)
                    if len(frame_fields) > len(fields):
                        fields = frame_fields
                        form_context = frame
                except Exception:
                    continue

    if event_callback:
        if len(fields) == 0:
            await event_callback("Extract Fields", "error", "No form fields found on this page. The form may require manual navigation.")
        else:
            await event_callback("Extract Fields", "success", f"Found {len(fields)} form fields")

    if screenshot_callback:
        ss = await _take_screenshot(page)
        await screenshot_callback(ss)

    # If no fields found, check if it's a Workday page
    if len(fields) == 0:
        current_url = page.url.lower()
        if "workday" in current_url or "myworkdayjobs" in current_url:
            if event_callback:
                await event_callback("Extract Fields", "info", "Workday detected - using Workday-specific handler...")

            summary = await _handle_workday_apply(page, resume_path, event_callback, screenshot_callback, job_url=url)
            return {"browser": _browser, "page": page, "summary": summary}

        if len(fields) == 0:
            return {"browser": _browser, "page": page, "summary": {"filled": 0, "failed": 0, "skipped": 0, "errors": ["No form fields found"]}}

    # Check if cover letter is needed (required field with cover letter label)
    cover_letter_text = ""
    has_required_cover_letter = any(
        f.get("required") and any(kw in (f.get("label", "") + f.get("name", "")).lower()
            for kw in ["cover letter", "cover_letter", "coverletter"])
        for f in fields
    )
    if has_required_cover_letter:
        if event_callback:
            await event_callback("Generate Answers", "info", "Cover letter required, generating...")
        try:
            cover_letter_text = _generate_cover_letter(company, role, job_description)
        except Exception as e:
            if event_callback:
                await event_callback("Generate Answers", "warning", f"Cover letter generation failed: {e}")

    # LLM mapping
    if event_callback:
        await event_callback("Generate Answers", "start", "Mapping fields to profile (single LLM call)...")

    try:
        mappings = await asyncio.to_thread(map_fields_to_profile, fields, job_description, company, role, cover_letter_text)
        if event_callback:
            fill_count = sum(1 for m in mappings if m.get("action") != "skip")
            await event_callback("Generate Answers", "success", f"Mapped {fill_count} fields to fill")
    except Exception as e:
        if event_callback:
            await event_callback("Generate Answers", "error", f"LLM mapping failed: {e}")
        return {"browser": _browser, "page": page, "summary": {"filled": 0, "failed": 0, "errors": [str(e)]}}

    # Fill the form with continuous background screenshots and retry loop
    if event_callback:
        await event_callback("Fill Form", "start", "Filling fields...")

    # Background screenshot loop for smooth live feed
    _screenshot_active = True

    async def _bg_screenshots():
        while _screenshot_active:
            try:
                if screenshot_callback and page:
                    ss = await _take_screenshot(page)
                    if ss:
                        await screenshot_callback(ss)
            except Exception:
                pass
            await asyncio.sleep(0.3)

    bg_task = asyncio.create_task(_bg_screenshots())

    total_filled = 0
    total_failed = 0
    total_skipped = 0
    all_errors = []
    max_passes = 5  # retry up to 5 times

    for pass_num in range(max_passes):
        if pass_num == 0:
            # First pass uses the mappings we already have
            summary = await fill_form(form_context, mappings, resume_path, transcript_path, event_callback, screenshot_callback, page)
        else:
            # Re-extract fields and check which are still empty
            await asyncio.sleep(1.5)

            # Scroll to load any lazy content
            page_height = await form_context.evaluate("document.body.scrollHeight") if hasattr(form_context, 'evaluate') else await page.evaluate("document.body.scrollHeight")
            for i in range(0, page_height, 900):
                target = form_context if hasattr(form_context, 'evaluate') else page
                await target.evaluate(f"window.scrollTo(0, {i})")
                await asyncio.sleep(0.3)
            target = form_context if hasattr(form_context, 'evaluate') else page
            await target.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.5)

            re_fields = await form_context.evaluate(JS_EXTRACT_FIELDS)

            # Filter to only unfilled fields (empty value, no selection made)
            unfilled = [f for f in re_fields if not f.get("value", "").strip()]
            # Also include dropdowns that might show placeholder text
            for f in re_fields:
                if f.get("tag") == "select" and f.get("value", "") in ("", "0", "--"):
                    if f not in unfilled:
                        unfilled.append(f)

            if len(unfilled) == 0:
                if event_callback:
                    await event_callback("Fill Form", "success", f"All fields filled after {pass_num + 1} passes")
                break

            if event_callback:
                await event_callback("Fill Form", "info", f"Pass {pass_num + 1}: {len(unfilled)} fields still empty, retrying...")

            try:
                retry_mappings = await asyncio.to_thread(map_fields_to_profile, unfilled, job_description, company, role, cover_letter_text)
                # Filter out skips for retry - we want to fill everything
                retry_mappings = [m for m in retry_mappings if m.get("action") != "skip" or m.get("value")]
                if not retry_mappings:
                    if event_callback:
                        await event_callback("Fill Form", "info", f"No new mappings on pass {pass_num + 1}, moving on")
                    break
                summary = await fill_form(form_context, retry_mappings, resume_path, transcript_path, event_callback, screenshot_callback, page)
            except Exception as e:
                if event_callback:
                    await event_callback("Fill Form", "info", f"Retry pass {pass_num + 1} failed: {e}")
                break

        total_filled += summary.get("filled", 0)
        total_failed += summary.get("failed", 0)
        total_skipped += summary.get("skipped", 0)
        all_errors.extend(summary.get("errors", []))

        # If nothing failed, we're done
        if summary.get("failed", 0) == 0 and pass_num > 0:
            break

    _screenshot_active = False
    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        pass

    final_summary = {"filled": total_filled, "failed": total_failed, "skipped": total_skipped, "errors": all_errors}

    if event_callback:
        status = "success" if total_failed == 0 else "info"
        await event_callback(
            "Fill Form", status,
            f"Done: {total_filled} filled, {total_skipped} skipped, {total_failed} failed across {min(pass_num + 1, max_passes)} passes"
        )

    # Final screenshot
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(0.5)
    if screenshot_callback:
        ss = await _take_screenshot(page)
        await screenshot_callback(ss)

    return {"browser": _browser, "page": page, "summary": final_summary}


async def close_browser():
    """Clean up browser and playwright."""
    global _playwright, _browser
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright:
        try:
            await _playwright.stop()
        except Exception:
            pass
        _playwright = None

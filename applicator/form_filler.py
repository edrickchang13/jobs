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
from pathlib import Path

import yaml
from openai import OpenAI
from playwright.async_api import async_playwright, Page, Browser, Frame
from config import CANDIDATE_PROFILE, WRITING_STYLE


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
        ("pronouns", "Pronouns"),
        ("street_address", "Street address / Address"),
        ("city", "City"),
        ("state", "State"),
        ("zip_code", "Zip code / Postal code"),
        ("country", "Country"),
        ("linkedin", "LinkedIn URL"),
        ("github", "GitHub URL"),
        ("portfolio", "Portfolio / Website"),
        ("school", "School / University"),
        ("degree", "Degree"),
        ("gpa", "GPA"),
        ("graduation_date", "Graduation date"),
        ("graduation_year", "Graduation year"),
        ("authorized_to_work", "Work authorization in US"),
        ("sponsorship_needed", "Sponsorship needed"),
        ("citizenship", "Citizenship"),
        ("visa_status", "Visa status"),
        ("gender", "Gender"),
        ("race_ethnicity", "Race / Ethnicity"),
        ("veteran_status", "Veteran status"),
        ("disability_status", "Disability status"),
        ("current_company", "Current company / organization"),
        ("years_of_experience", "Years of experience"),
        ("start_date", "Available start date"),
        ("desired_salary", "Desired salary"),
        ("willing_to_relocate", "Willing to relocate"),
        ("drivers_license", "Driver's license"),
        ("has_vehicle", "Has vehicle / transportation"),
        ("emergency_contact_name", "Emergency contact name"),
        ("emergency_contact_phone", "Emergency contact phone"),
        ("emergency_contact_relationship", "Emergency contact relationship"),
    ]
    for key, label in mappings:
        value = info.get(key, "")
        if value:
            lines.append(f'- {label}: "{value}"')
    # Always add referral rule
    lines.append('- Referred by employee: ALWAYS "No" - never say the candidate was referred')
    return "\n".join(lines)

# Module-level refs so browser stays alive after fill
_playwright = None
_browser = None

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

        // For radio/checkbox, get the label for this specific option
        if (type === 'radio' || type === 'checkbox') {
            const wrapper = el.closest('li, div, label');
            if (wrapper) {
                const text = wrapper.innerText.trim();
                if (text) field.label = field.label + ' :: ' + text;
            }
        }

        fields.push(field);
    }

    return fields;
}
"""


def _get_llm_client():
    return OpenAI(
        base_url="https://api.cerebras.ai/v1",
        api_key=os.getenv("CEREBRAS_API_KEY"),
    )


def _parse_json_response(text: str) -> list:
    """Parse JSON from LLM response, handling markdown fences and think tags."""
    # Strip <think>...</think> tags (Qwen 3)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Strip markdown code fences
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()
    # Find the JSON array
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


def map_fields_to_profile(
    fields: list[dict],
    job_description: str,
    company: str,
    role: str,
) -> list[dict]:
    """Send extracted fields to LLM, get back field->value mappings."""
    client = _get_llm_client()

    # Trim fields to reduce token usage - only send what the LLM needs
    slim_fields = []
    for f in fields:
        slim = {
            "selector": f["selector"],
            "tag": f["tag"],
            "type": f["type"],
            "label": f["label"],
            "required": f["required"],
        }
        if f.get("options"):
            slim["options"] = [o["text"] for o in f["options"] if o["value"]]
        if f.get("placeholder"):
            slim["placeholder"] = f["placeholder"]
        slim_fields.append(slim)

    personal_info = _load_personal_info()
    known_values = _build_known_values(personal_info)

    prompt = f"""You are a form-filling assistant. Given form fields and a candidate profile, return a JSON array mapping each field to its value.

CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

KNOWN VALUES (use exactly):
{known_values}
- Additional information / cover letter / "anything else": ALWAYS skip (action "skip") - leave blank

{WRITING_STYLE}

For open-ended text questions (why this company, why interested in role, etc.), write authentic answers connecting the candidate's experience to {company} and the {role} role. Keep under 150 words.
IMPORTANT: Do NOT fill the "additional information" or "cover letter" or "anything else you want to share" field. Skip it.
IMPORTANT: For any referral question, ALWAYS answer "No" - the candidate was NOT referred by an employee.

For dropdowns, pick the best matching option from the available choices.
For radio buttons, return the selector of the correct option to click.
For file inputs (resume/CV), use action "upload_file" with value "resume".
For file inputs asking for transcript, use action "upload_file" with value "transcript".

Company: {company}
Role: {role}
Job Description: {job_description[:1500]}

FORM FIELDS:
{json.dumps(slim_fields, indent=2)}

Return ONLY a JSON array. Each element must have exactly these keys:
- "selector": the CSS selector (from input)
- "action": one of "fill", "select", "click", "upload_file", "skip"
- "value": the value to fill/select, or file path for upload_file, or empty string for skip

Do NOT include any explanation, only the JSON array."""

    response = client.chat.completions.create(
        model="qwen-3-235b-a22b-instruct-2507",
        max_tokens=4000,
        messages=[
            {"role": "system", "content": "You return only valid JSON arrays. No markdown, no explanation."},
            {"role": "user", "content": prompt},
        ],
    )

    raw = response.choices[0].message.content
    return _parse_json_response(raw)


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
                await page.wait_for_timeout(500)
                return True
        except Exception:
            continue
    return False


async def _check_for_captcha(page: Page) -> bool:
    """Check if the page has a CAPTCHA that needs manual solving."""
    captcha_indicators = await page.evaluate("""
    () => {
        const html = document.documentElement.innerHTML.toLowerCase();
        const hasCaptcha = (
            html.includes('recaptcha') ||
            html.includes('hcaptcha') ||
            html.includes('captcha-container') ||
            html.includes('g-recaptcha') ||
            html.includes('h-captcha') ||
            document.querySelector('iframe[src*="recaptcha"]') !== null ||
            document.querySelector('iframe[src*="hcaptcha"]') !== null ||
            document.querySelector('.g-recaptcha') !== null ||
            document.querySelector('.h-captcha') !== null
        );
        return hasCaptcha;
    }
    """)
    return captcha_indicators


def _load_credentials() -> dict:
    """Load credentials from YAML file."""
    creds_path = Path(__file__).parent.parent / "credentials.yaml"
    if creds_path.exists():
        with open(creds_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


async def _handle_workday_apply(page, resume_path, event_callback=None, screenshot_callback=None) -> dict:
    """Handle the full Workday multi-step application flow."""
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
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    # Step 1: Sign in
    if event_callback:
        await event_callback("Navigate", "info", "Workday: Signing in...")

    try:
        sign_in = page.locator('[data-automation-id="utilityButtonSignIn"]')
        if await sign_in.is_visible(timeout=3000):
            await sign_in.click()
            await page.wait_for_timeout(3000)
            await page.fill('[data-automation-id="email"]', email)
            await page.fill('[data-automation-id="password"]', password)
            await page.evaluate("""document.querySelector('[data-automation-id="click_filter"]').click()""")
            await page.wait_for_timeout(5000)

            # Check if we need to create account
            sign_in_still = False
            try:
                sign_in_still = await page.locator('[data-automation-id="signInSubmitButton"]').is_visible(timeout=2000)
            except Exception:
                pass

            if sign_in_still:
                if event_callback:
                    await event_callback("Navigate", "info", "Creating new Workday account...")
                await page.click('[data-automation-id="createAccountLink"]')
                await page.wait_for_timeout(3000)
                await page.fill('[data-automation-id="email"]', email)
                await page.fill('[data-automation-id="password"]', password)
                await page.fill('[data-automation-id="verifyPassword"]', password)
                await page.evaluate("""() => {
                    const els = document.querySelectorAll('[data-automation-id="click_filter"]');
                    for (let i = els.length - 1; i >= 0; i--) {
                        if (els[i].offsetParent !== null) { els[i].click(); return; }
                    }
                }""")
                await page.wait_for_timeout(8000)

            if event_callback:
                await event_callback("Navigate", "success", "Workday: Signed in")
    except Exception as e:
        if event_callback:
            await event_callback("Navigate", "error", f"Workday sign-in failed: {e}")
        return {"filled": 0, "failed": 1, "skipped": 0, "errors": [str(e)]}

    if screenshot_callback:
        ss = await _take_screenshot(page)
        if ss:
            await screenshot_callback(ss)

    # Step 2: Navigate to apply page if not already there
    if "/apply" not in page.url:
        # Try clicking Apply button on job page
        try:
            apply = page.locator('[data-automation-id="adventureButton"], a:has-text("Apply")')
            if await apply.first.is_visible(timeout=3000):
                href = await apply.first.get_attribute("href")
                if href:
                    await page.goto(href if href.startswith("http") else page.url.split("/job/")[0] + href,
                                    wait_until="domcontentloaded", timeout=30000)
                else:
                    await apply.first.click()
                await page.wait_for_timeout(5000)
        except Exception:
            pass

    # Step 3: Choose "Autofill with Resume" if available
    try:
        autofill = page.locator('[data-automation-id="autofillWithResume"]')
        if await autofill.is_visible(timeout=3000):
            await autofill.click()
            await page.wait_for_timeout(3000)

            # Upload resume
            file_input = page.locator('input[type="file"]')
            await file_input.set_input_files(resume_path, timeout=10000)
            await page.wait_for_timeout(5000)  # Wait for Workday to process
            filled_total += 1

            if event_callback:
                await event_callback("Fill Form", "info", "Workday: Resume uploaded and parsed")

            if screenshot_callback:
                ss = await _take_screenshot(page)
                if ss:
                    await screenshot_callback(ss)

            # Click Continue
            await page.evaluate("""document.querySelector('[data-automation-id="pageFooterNextButton"]').click()""")
            await page.wait_for_timeout(5000)
    except Exception as e:
        # Try "Apply Manually" instead
        try:
            manual = page.locator('[data-automation-id="applyManually"]')
            if await manual.is_visible(timeout=2000):
                await manual.click()
                await page.wait_for_timeout(5000)
        except Exception:
            pass

    # Step 4: Process each page of the multi-step form
    max_pages = 10
    for page_num in range(max_pages):
        await page.wait_for_timeout(2000)

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

        # Scroll to load all content
        for i in range(5):
            await page.evaluate(f"window.scrollTo(0, {i * 900})")
            await page.wait_for_timeout(300)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(500)

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
                mappings = map_fields_to_profile(fields_on_page, "", "", "")
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
                                    if (el) {{ el.value = '{value.replace(chr(39), "")}'; el.dispatchEvent(new Event('input', {{bubbles:true}})); }}
                                }}""")
                            filled_total += 1
                        elif action == "select":
                            try:
                                await page.select_option(selector, label=value, timeout=3000)
                            except Exception:
                                await page.select_option(selector, value=value, timeout=3000)
                            filled_total += 1
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
                await page.wait_for_timeout(3000)
            else:
                break  # No next button - we're done
        except Exception:
            break

    return {"filled": filled_total, "failed": failed_total, "skipped": 0, "errors": errors}


async def _handle_workday_auth(page, event_callback=None) -> bool:
    """Handle Workday sign-in or account creation. Returns True if auth succeeded."""
    creds = _load_credentials().get("workday", {})
    email = creds.get("email", "")
    password = creds.get("password", "")
    if not email or not password:
        if event_callback:
            await event_callback("Navigate", "error", "No Workday credentials in credentials.yaml")
        return False

    # Click Sign In button
    try:
        sign_in_btn = page.locator('[data-automation-id="utilityButtonSignIn"]')
        if await sign_in_btn.is_visible(timeout=3000):
            await sign_in_btn.click()
            await page.wait_for_timeout(3000)
    except Exception:
        return False

    # Try to sign in first
    try:
        email_input = page.locator('[data-automation-id="email"]')
        if await email_input.is_visible(timeout=3000):
            await email_input.fill(email)
            await page.locator('[data-automation-id="password"]').fill(password)
            # Workday uses click_filter divs that overlay buttons - use JS click
            await page.evaluate('document.querySelector(\'[data-automation-id="signInSubmitButton"]\').click()')
            await page.wait_for_timeout(5000)

            if event_callback:
                await event_callback("Navigate", "info", "Attempted Workday sign-in...")

            # Check if sign-in succeeded (no error message visible)
            error_visible = False
            try:
                error_el = page.locator('[data-automation-id="errorMessage"], .WJCC, [data-automation-id="formErrorBanner"]')
                error_visible = await error_el.is_visible(timeout=2000)
            except Exception:
                pass

            if not error_visible:
                # Check if we're past the sign-in page
                sign_in_still = False
                try:
                    sign_in_still = await page.locator('[data-automation-id="signInSubmitButton"]').is_visible(timeout=2000)
                except Exception:
                    pass

                if not sign_in_still:
                    if event_callback:
                        await event_callback("Navigate", "success", "Signed in to Workday")
                    return True

            # Sign-in failed, try creating account
            if event_callback:
                await event_callback("Navigate", "info", "Sign-in failed, creating new account...")

            try:
                await page.locator('[data-automation-id="createAccountLink"]').click()
                await page.wait_for_timeout(3000)

                # Fill create account form
                await page.locator('[data-automation-id="email"]').fill(email)
                await page.locator('[data-automation-id="password"]').fill(password)
                await page.locator('[data-automation-id="verifyPassword"]').fill(password)
                await page.evaluate('document.querySelector(\'[data-automation-id="createAccountSubmitButton"]\').click()')
                await page.wait_for_timeout(8000)

                if event_callback:
                    await event_callback("Navigate", "info", "Created Workday account, waiting for form...")

                # Check for verification requirement
                page_text = await page.evaluate("document.body.innerText")
                if "verify" in page_text.lower() and "email" in page_text.lower():
                    if event_callback:
                        await event_callback("Navigate", "info",
                            "Email verification required. Check your email and verify, then the form will load.")
                    # Wait up to 60s for user to verify email
                    for _ in range(60):
                        await page.wait_for_timeout(1000)
                        try:
                            inputs = await page.evaluate("document.querySelectorAll('input, textarea, select').length")
                            if inputs > 5:
                                break
                        except Exception:
                            pass

                return True
            except Exception as e:
                if event_callback:
                    await event_callback("Navigate", "error", f"Account creation failed: {e}")
                return False
    except Exception as e:
        if event_callback:
            await event_callback("Navigate", "error", f"Workday auth failed: {e}")
        return False


async def _take_screenshot(page: Page) -> bytes:
    """Take a screenshot, return bytes."""
    try:
        return await page.screenshot(type="png")
    except Exception:
        return b""


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
    filled = 0
    skipped = 0
    failed = 0
    errors = []

    for m in mappings:
        selector = m.get("selector", "")
        action = m.get("action", "skip")
        value = m.get("value", "")

        if action == "skip" or not selector:
            skipped += 1
            continue

        try:
            if action == "fill":
                try:
                    await page.locator(selector).first.scroll_into_view_if_needed(timeout=3000)
                    await page.fill(selector, value, timeout=5000)
                except Exception:
                    # Fallback: use JS to set value (bypasses overlays)
                    escaped_val = value.replace("'", "\\'").replace("\n", "\\n")
                    await page.evaluate(f"document.querySelector('{selector}').value = '{escaped_val}'")
                    await page.evaluate(f"document.querySelector('{selector}').dispatchEvent(new Event('input', {{bubbles: true}}))")
                filled += 1
                if event_callback:
                    label = m.get("label", selector)
                    await event_callback("Fill Form", "info", f"Filled: {label[:50]}")

            elif action == "select":
                await page.locator(selector).first.scroll_into_view_if_needed(timeout=3000)
                try:
                    await page.select_option(selector, label=value, timeout=5000)
                except Exception:
                    await page.select_option(selector, value=value, timeout=5000)
                filled += 1
                if event_callback:
                    await event_callback("Fill Form", "info", f"Selected: {value[:50]}")

            elif action == "click":
                try:
                    await page.locator(selector).first.scroll_into_view_if_needed(timeout=3000)
                    await page.click(selector, timeout=5000)
                except Exception:
                    # Fallback: use JS click to bypass overlays (CAPTCHA, modals)
                    await page.evaluate(f'document.querySelector(\'{selector}\').click()')
                filled += 1
                if event_callback:
                    await event_callback("Fill Form", "info", f"Clicked: {selector[:50]}")

            elif action == "upload_file":
                try:
                    file_path = resume_path
                    file_label = "resume"
                    if value == "transcript" and transcript_path:
                        file_path = transcript_path
                        file_label = "transcript"

                    file_input = page.locator('input[type="file"]').first
                    await file_input.set_input_files(file_path, timeout=10000)
                    filled += 1
                    if event_callback:
                        await event_callback("Fill Form", "info", f"Uploaded {file_label}")
                except Exception as e:
                    errors.append(f"File upload failed: {e}")
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

    return {"filled": filled, "skipped": skipped, "failed": failed, "errors": errors}


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
    _browser = await _playwright.chromium.launch(headless=False)
    page = await _browser.new_page(viewport={"width": 1280, "height": 900})

    if event_callback:
        await event_callback("Navigate", "info", "Browser launched")

    # Navigate
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # Wait a bit for JS rendering
    await page.wait_for_timeout(3000)

    if event_callback:
        await event_callback("Navigate", "success", "Page loaded")

    # Dismiss cookie banners
    dismissed = await _dismiss_cookie_banners(page)
    if dismissed and event_callback:
        await event_callback("Navigate", "info", "Dismissed cookie banner")

    # Screenshot
    if screenshot_callback:
        ss = await _take_screenshot(page)
        await screenshot_callback(ss)

    # Check for CAPTCHA
    has_captcha = await _check_for_captcha(page)
    if has_captcha and event_callback:
        await event_callback("Navigate", "info",
            "CAPTCHA detected - please solve it in the browser window. Waiting 30s...")
        # Wait for user to solve CAPTCHA manually
        for _ in range(30):
            await page.wait_for_timeout(1000)
            still_captcha = await _check_for_captcha(page)
            if not still_captcha:
                break
        if event_callback:
            await event_callback("Navigate", "info", "Continuing after CAPTCHA wait")

    # Scroll down incrementally to load lazy content (Greenhouse, etc.)
    page_height = await page.evaluate("document.body.scrollHeight")
    viewport_height = 900
    scroll_pos = 0
    while scroll_pos < page_height:
        scroll_pos += viewport_height
        await page.evaluate(f"window.scrollTo(0, {scroll_pos})")
        await page.wait_for_timeout(500)
        # Page might grow as we scroll
        page_height = await page.evaluate("document.body.scrollHeight")
    await page.wait_for_timeout(1000)
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(500)

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
            # SmartRecruiters
            'a.apply-btn',
            'button.js-apply-btn',
            '[data-test="apply-button"]',
            # iCIMS
            '.iCIMS_PrimaryButton',
            'a.iCIMS_PrimaryButton',
        ]
        # Scroll down to find apply button
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)

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
                        await page.wait_for_timeout(5000)
                        # Dismiss cookies on the new page too
                        await _dismiss_cookie_banners(page)
                    else:
                        await btn.click(timeout=5000)
                        apply_clicked = True
                        if event_callback:
                            await event_callback("Extract Fields", "info", "Clicked Apply button, waiting for form...")
                        await page.wait_for_timeout(5000)
                    break
            except Exception:
                continue

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
        await page.wait_for_timeout(5000)
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

            summary = await _handle_workday_apply(page, resume_path, event_callback, screenshot_callback)
            return {"browser": _browser, "page": page, "summary": summary}

        if len(fields) == 0:
            return {"browser": _browser, "page": page, "summary": {"filled": 0, "failed": 0, "skipped": 0, "errors": ["No form fields found"]}}

    # LLM mapping
    if event_callback:
        await event_callback("Generate Answers", "start", "Mapping fields to profile (single LLM call)...")

    try:
        mappings = map_fields_to_profile(fields, job_description, company, role)
        if event_callback:
            fill_count = sum(1 for m in mappings if m.get("action") != "skip")
            await event_callback("Generate Answers", "success", f"Mapped {fill_count} fields to fill")
    except Exception as e:
        if event_callback:
            await event_callback("Generate Answers", "error", f"LLM mapping failed: {e}")
        return {"browser": _browser, "page": page, "summary": {"filled": 0, "failed": 0, "errors": [str(e)]}}

    # Fill the form with continuous background screenshots
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

    summary = await fill_form(form_context, mappings, resume_path, transcript_path, event_callback, screenshot_callback, page)

    _screenshot_active = False
    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        pass

    if event_callback:
        status = "success" if summary["failed"] == 0 else "info"
        await event_callback(
            "Fill Form", status,
            f"Done: {summary['filled']} filled, {summary['skipped']} skipped, {summary['failed']} failed"
        )

    # Final screenshot
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(500)
    if screenshot_callback:
        ss = await _take_screenshot(page)
        await screenshot_callback(ss)

    return {"browser": _browser, "page": page, "summary": summary}


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

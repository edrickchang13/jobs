"""
Taleo-specific application handler.

Oracle Taleo is an older enterprise ATS requiring account creation.
Typical URL: {company}.taleo.net/careersection/{section}/jobdetail.ftl?job={job_id}

Application flow:
  1. Job listing page → click "Apply Now" button
  2. Login page: Create account OR sign in with existing credentials
     (Email, password, security questions)
  3. Multi-step application wizard:
     Step 1: Personal Information (name, contact, address)
     Step 2: Resume / Education / Work History
     Step 3: Certifications / Other Info
     Step 4: Screening Questions (custom per job)
     Step 5: Review & Submit
  4. Each step has "Next >" or "Submit" button

NOTE: Taleo requires a pre-existing account. The pipeline will create/login
automatically using credentials from credentials.yaml.

UI specifics:
  - Old JSP-based UI with static HTML forms
  - Standard HTML inputs/selects with predictable name attributes
  - Tables-based layout
  - Session managed via cookies
  - Some Taleo versions have a newer "Enterprise" UI with different selectors
"""

import asyncio
import os
from typing import Callable, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Candidate value map
# ──────────────────────────────────────────────────────────────────────────────

def _build_taleo_map(personal: dict) -> dict:
    return {
        "firstName":      personal.get("first_name", "Edrick"),
        "lastName":       personal.get("last_name", "Chang"),
        "middleName":     "",
        "email":          personal.get("email", "eachang@scu.edu"),
        "phone":          personal.get("phone", "4088066495"),
        "phoneHome":      personal.get("phone", "4088066495"),
        "phoneMobile":    personal.get("phone", "4088066495"),
        "address":        personal.get("address", ""),
        "city":           personal.get("city", "Santa Clara"),
        "state":          personal.get("state", "California"),
        "zip":            personal.get("zip", "95050"),
        "country":        "United States",
        "linkedin":       personal.get("linkedin", "https://linkedin.com/in/edrickchang"),
        "website":        personal.get("github", "https://github.com/edrickchang"),
        "school":         personal.get("school", "Santa Clara University"),
        "degree":         "Bachelor of Science",
        "major":          personal.get("major", "Computer Science and Engineering"),
        "gpa":            str(personal.get("gpa", "3.78")),
        "grad_year":      personal.get("graduation_year", "2028"),
        "authorized":     "Yes",
        "sponsorship":    "No",
    }


def _value_for_label(label: str, taleo_map: dict) -> Optional[str]:
    l = label.lower().strip()
    if not l:
        return None

    if "first" in l and "name" in l:
        return taleo_map["firstName"]
    if "last" in l and "name" in l:
        return taleo_map["lastName"]
    if "middle" in l:
        return taleo_map["middleName"]
    if "email" in l:
        return taleo_map["email"]
    if "phone" in l or "mobile" in l or "tel" in l:
        return taleo_map["phone"]
    if "address" in l or "street" in l:
        return taleo_map["address"]
    if "city" in l:
        return taleo_map["city"]
    if "state" in l or "province" in l:
        return taleo_map["state"]
    if "zip" in l or "postal" in l:
        return taleo_map["zip"]
    if "country" in l:
        return taleo_map["country"]
    if "linkedin" in l:
        return taleo_map["linkedin"]
    if "website" in l or "portfolio" in l:
        return taleo_map["website"]
    if "school" in l or "institution" in l or "university" in l or "college" in l:
        return taleo_map["school"]
    if "degree" in l:
        return taleo_map["degree"]
    if "major" in l or "discipline" in l or "field of study" in l:
        return taleo_map["major"]
    if "gpa" in l or "grade point" in l:
        return taleo_map["gpa"]
    if "graduation" in l or "grad" in l:
        return taleo_map["grad_year"]
    if "authorized" in l or "eligible" in l or "legally" in l:
        return "Yes"
    if "sponsor" in l or "visa" in l:
        return "No"

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Main handler
# ──────────────────────────────────────────────────────────────────────────────

async def handle_taleo_apply(
    page,
    resume_path: str,
    job_description: str = "",
    company: str = "",
    role: str = "",
    event_callback: Optional[Callable] = None,
    screenshot_callback: Optional[Callable] = None,
    personal_info: Optional[dict] = None,
    generate_answer_fn: Optional[Callable] = None,
) -> dict:
    """
    Fill a Taleo application (multiple steps with authentication).

    Returns dict with: filled, failed, submitted, errors
    """
    from applicator.form_filler import _take_screenshot, _load_personal_info

    if personal_info is None:
        personal_info = _load_personal_info()

    taleo_map = _build_taleo_map(personal_info)
    filled = 0
    failed = 0
    errors: list[str] = []

    async def ev(step, status, detail=""):
        if event_callback:
            await event_callback(step, status, detail)

    async def ss():
        if screenshot_callback:
            data = await _take_screenshot(page)
            if data:
                await screenshot_callback(data)

    await ev("Taleo", "start", f"Starting Taleo handler for {company} - {role}")

    # ── Step 1: Click Apply Now if on listing ──────────────────────────────
    for sel in [
        'input[value="Apply Now"]', 'button:has-text("Apply Now")',
        'a:has-text("Apply Now")', 'input[type="submit"][value*="Apply"]',
        '#applyButton', '.apply-button',
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click()
                await asyncio.sleep(3.0)
                await ev("Taleo", "info", "Clicked Apply Now")
                break
        except Exception:
            continue

    await ss()

    # ── Step 2: Account login/creation ─────────────────────────────────────
    # Taleo auth is handled by _handle_taleo_auth in form_filler.py
    # If we're already past auth, we skip this.
    current_url = page.url.lower()
    on_login_page = any(kw in current_url for kw in ("login", "signin", "register", "profile.ftl"))

    if on_login_page:
        try:
            from applicator.form_filler import _handle_taleo_auth, _load_credentials
            creds = _load_credentials()
            taleo_creds = creds.get("taleo", {})
            email = taleo_creds.get("email", personal_info.get("email", "eachang@scu.edu"))
            password = taleo_creds.get("password", "")
            first_name = personal_info.get("first_name", "Edrick")
            last_name = personal_info.get("last_name", "Chang")

            auth_ok = await _handle_taleo_auth(page, email, password, first_name, last_name, event_callback)
            if not auth_ok:
                await ev("Taleo", "warning", "Auth may have failed. Check browser.")
            else:
                await asyncio.sleep(3.0)
        except Exception as e:
            await ev("Taleo", "warning", f"Auth handler error: {e}")

    await ss()

    # ── Step 3: Multi-step form filling ────────────────────────────────────
    # Taleo has 4-5 steps. We iterate through each page.
    max_steps = 6
    step_num = 0

    while step_num < max_steps:
        step_num += 1
        current_url = page.url.lower()

        # Check if we're done (review/confirmation page)
        if any(kw in current_url for kw in ("confirm", "submit", "thank", "complete")):
            await ev("Taleo", "success", "Reached confirmation/review page")
            break

        # Check for resume upload page
        if "resume" in current_url or "document" in current_url:
            if resume_path and os.path.exists(resume_path):
                for sel in ['input[type="file"]', '#attachmentDocumentFile']:
                    try:
                        fi = page.locator(sel).first
                        if await fi.count() > 0:
                            await fi.set_input_files(resume_path)
                            await asyncio.sleep(2.0)
                            await ev("Taleo", "success", "Resume uploaded")
                            filled += 1
                            break
                    except Exception:
                        continue

        # Extract and fill fields on current page
        form_fields = await page.evaluate("""() => {
            const fields = [];
            const seen = new Set();

            document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]), textarea').forEach(el => {
                if (el.offsetParent === null) return;
                const id = el.id || ''; const name = el.name || '';
                const sel = id ? '#' + CSS.escape(id) : (name ? '[name="' + name + '"]' : null);
                if (!sel || seen.has(sel)) return;
                seen.add(sel);
                let lbl = '';
                if (id) { const l = document.querySelector('label[for="' + id + '"]'); if (l) lbl = l.innerText.replace('*','').trim(); }
                if (!lbl) { const l = el.closest('tr')?.previousElementSibling?.querySelector('td, th'); if (l) lbl = l.innerText.replace('*','').trim(); }
                if (!lbl) lbl = el.placeholder || el.title || '';
                fields.push({selector: sel, type: el.type || 'text', label: lbl, value: el.value || '', required: el.required});
            });

            document.querySelectorAll('select').forEach(el => {
                if (el.offsetParent === null) return;
                const id = el.id || ''; const name = el.name || '';
                const sel = id ? '#' + CSS.escape(id) : (name ? 'select[name="' + name + '"]' : null);
                if (!sel || seen.has(sel)) return;
                seen.add(sel);
                let lbl = '';
                if (id) { const l = document.querySelector('label[for="' + id + '"]'); if (l) lbl = l.innerText.replace('*','').trim(); }
                if (!lbl) { const l = el.closest('tr')?.previousElementSibling?.querySelector('td, th'); if (l) lbl = l.innerText.replace('*','').trim(); }
                const opts = Array.from(el.options).filter(o => o.value && !['--','select'].includes(o.text.toLowerCase().trim())).map(o => o.text.trim());
                fields.push({selector: sel, type: 'select', label: lbl, value: el.value || '', required: el.required, options: opts});
            });

            return fields;
        }""")

        page_filled = 0
        custom_qs = []

        for field in form_fields:
            sel = field.get("selector", "")
            ftype = field.get("type", "text")
            label = field.get("label", "")
            current_val = field.get("value", "")
            opts = field.get("options", [])

            if current_val:
                continue

            value = _value_for_label(label, taleo_map)

            if ftype == "select":
                if value and opts:
                    v_lower = value.lower()
                    best = next((o for o in opts if v_lower in o.lower() or o.lower() in v_lower), None)
                    value = best or value
                if value:
                    try:
                        await page.select_option(sel, label=value, timeout=3000)
                        page_filled += 1
                    except Exception as e:
                        errors.append(f"Select {label}: {e}")
                else:
                    if field.get("required"):
                        custom_qs.append(field)
                continue

            if value:
                try:
                    el = page.locator(sel).first
                    await el.click(click_count=3, timeout=3000)
                    await el.fill(value, timeout=3000)
                    page_filled += 1
                except Exception as e:
                    errors.append(f"Fill {label}: {e}")
            else:
                if label and field.get("required"):
                    custom_qs.append(field)

        # Custom questions with LLM
        for field in custom_qs:
            sel = field.get("selector", "")
            ftype = field.get("type", "text")
            label = field.get("label", "")
            opts = field.get("options", [])

            answer = None
            if generate_answer_fn and label:
                try:
                    answer = await asyncio.to_thread(generate_answer_fn, label, company, role, job_description)
                except Exception:
                    pass
            if not answer:
                if opts:
                    answer = opts[0]
                elif "why" in label.lower():
                    answer = f"I'm eager to contribute to {company} as a {role}."
                elif "authorized" in label.lower() or "eligible" in label.lower():
                    answer = "Yes"
                elif "sponsor" in label.lower():
                    answer = "No"
                else:
                    answer = "N/A"

            if ftype == "select" and opts:
                a_lower = answer.lower()
                best = next((o for o in opts if a_lower in o.lower() or o.lower() in a_lower), opts[0])
                try:
                    await page.select_option(sel, label=best, timeout=3000)
                    page_filled += 1
                except Exception:
                    pass
            else:
                try:
                    el = page.locator(sel).first
                    await el.click(click_count=3, timeout=3000)
                    await el.fill(answer, timeout=3000)
                    page_filled += 1
                except Exception:
                    pass

        filled += page_filled
        await ev("Taleo", "info", f"Step {step_num}: filled {page_filled} fields")
        await ss()

        # Navigate to next step
        next_clicked = False
        for next_sel in [
            'input[type="submit"][value*="Next"]',
            'input[type="button"][value*="Next"]',
            'button:has-text("Next")',
            '#navigationBarContinue',
            'a:has-text("Next")',
        ]:
            try:
                btn = page.locator(next_sel).first
                if await btn.is_visible(timeout=1500):
                    await btn.click(timeout=3000)
                    await asyncio.sleep(3.0)
                    next_clicked = True
                    await ev("Taleo", "info", f"Moved to next step")
                    break
            except Exception:
                continue

        if not next_clicked:
            await ev("Taleo", "info", "No Next button found — assuming last page")
            break

    await ev("Taleo", "success",
        f"Taleo form filled ({filled} fields). Review in browser before submitting.")

    return {
        "filled":    filled,
        "failed":    failed,
        "submitted": False,
        "errors":    errors,
    }

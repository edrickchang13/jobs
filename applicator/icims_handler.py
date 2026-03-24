"""
iCIMS-specific application handler.

iCIMS is an enterprise ATS that requires account creation.
Typical URL: careers-{company}.icims.com/jobs/{job_id}/job

Application flow:
  1. Job listing page → click "Apply Now"
  2. Login/Registration page:
     - Create new profile (email, password, name)
     - Or log into existing account
     - May require email verification
  3. Multi-section application form:
     - Personal information (name, contact, address)
     - Resume/CV upload
     - Education history
     - Work experience
     - Cover letter (optional)
     - Custom questions
     - EEO / self-identification
  4. Review & submit

UI specifics:
  - Older enterprise UI with standard HTML forms
  - jQuery-based validation
  - Fields often have ids like "icims_*" or specific class names
  - Education/work history uses add-row patterns
  - File upload uses a special iframe in some versions
"""

import asyncio
import os
from typing import Callable, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Candidate value map
# ──────────────────────────────────────────────────────────────────────────────

def _build_icims_map(personal: dict) -> dict:
    return {
        "first_name":  personal.get("first_name", "Edrick"),
        "last_name":   personal.get("last_name", "Chang"),
        "name":        f"{personal.get('first_name','Edrick')} {personal.get('last_name','Chang')}",
        "email":       personal.get("email", "eachang@scu.edu"),
        "phone":       personal.get("phone", "4088066495"),
        "address":     personal.get("address", ""),
        "city":        personal.get("city", "Santa Clara"),
        "state":       personal.get("state", "California"),
        "zip":         personal.get("zip", "95050"),
        "country":     "United States",
        "linkedin":    personal.get("linkedin", "https://linkedin.com/in/edrickchang"),
        "website":     personal.get("github", "https://github.com/edrickchang"),
        "school":      personal.get("school", "Santa Clara University"),
        "degree":      "Bachelor of Science",
        "major":       personal.get("major", "Computer Science and Engineering"),
        "gpa":         str(personal.get("gpa", "3.78")),
        "grad_year":   personal.get("graduation_year", "2028"),
        "authorized":  "Yes",
        "sponsorship": "No",
    }


def _value_for_label(label: str, icims_map: dict) -> Optional[str]:
    l = label.lower().strip()
    if not l:
        return None

    if "first" in l and "name" in l:
        return icims_map["first_name"]
    if "last" in l and "name" in l:
        return icims_map["last_name"]
    if "full" in l and "name" in l:
        return icims_map["name"]
    if "email" in l:
        return icims_map["email"]
    if "phone" in l or "mobile" in l or "tel" in l:
        return icims_map["phone"]
    if "address" in l or "street" in l:
        return icims_map["address"]
    if "city" in l:
        return icims_map["city"]
    if "state" in l or "province" in l:
        return icims_map["state"]
    if "zip" in l or "postal" in l:
        return icims_map["zip"]
    if "country" in l:
        return icims_map["country"]
    if "linkedin" in l:
        return icims_map["linkedin"]
    if "website" in l or "portfolio" in l or "url" in l:
        return icims_map["website"]
    if "school" in l or "institution" in l or "university" in l or "college" in l:
        return icims_map["school"]
    if "degree" in l:
        return icims_map["degree"]
    if "major" in l or "discipline" in l or "field" in l:
        return icims_map["major"]
    if "gpa" in l:
        return icims_map["gpa"]
    if "graduation" in l or "grad" in l:
        return icims_map["grad_year"]
    if "authorized" in l or "eligible" in l or "legally" in l:
        return "Yes"
    if "sponsor" in l or "visa" in l:
        return "No"

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Main handler
# ──────────────────────────────────────────────────────────────────────────────

async def handle_icims_apply(
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
    Fill an iCIMS application form.

    Returns dict with: filled, failed, submitted, errors
    """
    from applicator.form_filler import _take_screenshot, _load_personal_info

    if personal_info is None:
        personal_info = _load_personal_info()

    icims_map = _build_icims_map(personal_info)
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

    await ev("iCIMS", "start", f"Starting iCIMS handler for {company} - {role}")

    # ── Step 1: Click Apply Now if on listing ──────────────────────────────
    for sel in [
        'a:has-text("Apply Now")', 'button:has-text("Apply Now")',
        'a:has-text("Apply")', 'button:has-text("Apply")',
        '#applyButton', '.iCIMS_JobHeaderArea .iCIMS_Button',
        'a[title*="Apply"]', 'input[value*="Apply"]',
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                href = await btn.get_attribute("href") or ""
                if href and href.startswith("http"):
                    await page.goto(href, wait_until="domcontentloaded", timeout=30000)
                else:
                    await btn.click()
                await asyncio.sleep(3.0)
                await ev("iCIMS", "info", "Clicked Apply")
                break
        except Exception:
            continue

    await ss()

    # ── Step 2: Account login/creation ─────────────────────────────────────
    current_url = page.url.lower()
    on_login_page = any(kw in current_url for kw in ("login", "signin", "register", "profile"))

    if on_login_page:
        try:
            from applicator.form_filler import _handle_icims_auth, _load_credentials
            creds = _load_credentials()
            icims_creds = creds.get("icims", {})
            email = icims_creds.get("email", personal_info.get("email", "eachang@scu.edu"))
            password = icims_creds.get("password", "")
            first_name = personal_info.get("first_name", "Edrick")
            last_name = personal_info.get("last_name", "Chang")

            auth_ok = await _handle_icims_auth(page, email, password, first_name, last_name, event_callback)
            if not auth_ok:
                await ev("iCIMS", "warning", "Auth may have failed. Continuing anyway...")
            else:
                await asyncio.sleep(3.0)
                await ev("iCIMS", "success", "Logged in / account created")
        except Exception as e:
            await ev("iCIMS", "warning", f"Auth error: {e}")

    await ss()

    # ── Step 3: Resume upload ───────────────────────────────────────────────
    if resume_path and os.path.exists(resume_path):
        resume_uploaded = False

        # iCIMS may have an iframe for file upload — check both
        contexts = [page] + [f for f in page.frames if f != page.main_frame]
        for ctx in contexts:
            for sel in [
                'input[type="file"][name*="resume"]',
                'input[type="file"][id*="resume"]',
                'input[type="file"]',
            ]:
                try:
                    fi = ctx.locator(sel).first
                    if await fi.count() > 0:
                        await fi.set_input_files(resume_path)
                        await asyncio.sleep(2.0)
                        resume_uploaded = True
                        await ev("iCIMS", "success", "Resume uploaded")
                        break
                except Exception:
                    continue
            if resume_uploaded:
                break

        if not resume_uploaded:
            await ev("iCIMS", "warning", "Resume upload failed")
            errors.append("Resume upload failed")

    await ss()

    # ── Step 4: Multi-step form filling ────────────────────────────────────
    max_steps = 6
    step_num = 0

    while step_num < max_steps:
        step_num += 1

        # Check for completion
        current_url = page.url.lower()
        if any(kw in current_url for kw in ("confirm", "submitted", "thank", "complete")):
            await ev("iCIMS", "success", "Application submitted successfully")
            break

        # Fill visible form fields
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
                if (!lbl) {
                    const wrapper = el.closest('.iCIMS_Field, .iCIMS_FormField, .field, .form-group, tr, li');
                    if (wrapper) { const l = wrapper.querySelector('label, .iCIMS_Label, td:first-child, th'); if (l) lbl = l.innerText.replace('*','').trim(); }
                }
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
                if (!lbl) {
                    const wrapper = el.closest('.iCIMS_Field, .field, tr, li');
                    if (wrapper) { const l = wrapper.querySelector('label, .iCIMS_Label, td:first-child'); if (l) lbl = l.innerText.replace('*','').trim(); }
                }
                const opts = Array.from(el.options).filter(o => o.value).map(o => o.text.trim());
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

            value = _value_for_label(label, icims_map)

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

        # LLM for custom questions
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
                    answer = f"I am very interested in this {role} role and believe my skills match well."
                elif "authorized" in label.lower():
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

        # Radio buttons on this page
        radio_groups = await page.evaluate("""() => {
            const groups = {};
            document.querySelectorAll('input[type="radio"]').forEach(r => {
                if (r.offsetParent === null) return;
                const name = r.name;
                if (!name) return;
                if (!groups[name]) groups[name] = {name, radios: [], checked: false};
                if (r.checked) groups[name].checked = true;
                const wrapper = r.closest('label, li, tr, .radio');
                groups[name].radios.push({
                    selector: r.id ? '#' + CSS.escape(r.id) : '[name="' + name + '"][value="' + r.value + '"]',
                    text: (wrapper ? wrapper.innerText.trim() : r.value).substring(0, 80),
                    value: r.value,
                });
            });
            return Object.values(groups).filter(g => !g.checked);
        }""")

        radio_map = {
            "authorized": "Yes", "legally": "Yes", "eligible": "Yes",
            "sponsor": "No", "visa": "No", "relocat": "Yes", "18": "Yes",
        }

        for group in radio_groups:
            q_name = group.get("name", "").lower()
            radios = group.get("radios", [])
            target = next((v for kw, v in radio_map.items() if kw in q_name), None)
            if not target:
                continue
            for r in radios:
                if target.lower() in r["text"].strip().lower():
                    try:
                        loc = page.locator(r["selector"]).first
                        await loc.scroll_into_view_if_needed(timeout=3000)
                        await loc.click(timeout=3000)
                        page_filled += 1
                    except Exception:
                        pass
                    break

        filled += page_filled
        await ev("iCIMS", "info", f"Step {step_num}: filled {page_filled} fields")
        await ss()

        # Navigate to next step
        next_clicked = False
        for next_sel in [
            'input[type="submit"][value*="Next"]',
            'input[type="submit"][value*="Continue"]',
            'button:has-text("Next")', 'button:has-text("Continue")',
            'a:has-text("Next")', 'a:has-text("Continue")',
            '#btnNext', '.iCIMS_ControlButton.iCIMS_NextButton',
        ]:
            try:
                btn = page.locator(next_sel).first
                if await btn.is_visible(timeout=1500):
                    btn_text = (await btn.inner_text(timeout=1000)).lower().strip()
                    if "submit" in btn_text:
                        break
                    await btn.scroll_into_view_if_needed(timeout=2000)
                    await btn.click(timeout=3000)
                    await asyncio.sleep(3.0)
                    next_clicked = True
                    await ev("iCIMS", "info", "Moved to next step")
                    break
            except Exception:
                continue

        if not next_clicked:
            await ev("iCIMS", "info", "No Next button found — assuming last step")
            break

    await ev("iCIMS", "success",
        f"iCIMS form filled ({filled} fields). Review in browser before submitting.")

    return {
        "filled":    filled,
        "failed":    failed,
        "submitted": False,
        "errors":    errors,
    }

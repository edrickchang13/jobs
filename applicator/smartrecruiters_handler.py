"""
SmartRecruiters-specific application handler.

SmartRecruiters is a modern ATS with a relatively standard HTML form.
No account required for most jobs.

Typical URL: jobs.smartrecruiters.com/{Company}/{posting-uuid}

Application flow:
  1. Listing page → click "Apply Now"
  2. Application form appears:
     - Basic contact info (name, email, phone, location)
     - Resume upload (PDF/DOCX file input)
     - Links (LinkedIn, portfolio)
     - Education section
     - Work experience / employment info
     - Custom screening questions (text, select, radio)
     - Consent/privacy checkboxes
  3. Submit

UI specifics:
  - Standard HTML5 inputs (not React Select in most cases)
  - Some custom dropdowns may use Select2 or similar
  - EEO section sometimes appears as native <select>
  - Each field typically has .js-input or .js-field classes
  - Data attributes like data-testid="field-input-..." or similar
"""

import asyncio
import os
from typing import Callable, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Candidate → form-value mapping
# ──────────────────────────────────────────────────────────────────────────────

def _build_sr_map(personal: dict) -> dict:
    return {
        "first_name":  personal.get("first_name", "Edrick"),
        "last_name":   personal.get("last_name", "Chang"),
        "name":        f"{personal.get('first_name','Edrick')} {personal.get('last_name','Chang')}",
        "email":       personal.get("email", "eachang@scu.edu"),
        "phone":       personal.get("phone", "4088066495"),
        "location":    personal.get("city", "Santa Clara, CA"),
        "city":        personal.get("city", "Santa Clara"),
        "state":       personal.get("state", "California"),
        "country":     "United States",
        "zip":         personal.get("zip", "95050"),
        "linkedin":    personal.get("linkedin", "https://linkedin.com/in/edrickchang"),
        "github":      personal.get("github", "https://github.com/edrickchang"),
        "portfolio":   personal.get("github", "https://github.com/edrickchang"),
        "website":     personal.get("github", "https://github.com/edrickchang"),
        "school":      personal.get("school", "Santa Clara University"),
        "degree":      personal.get("degree", "Bachelor of Science"),
        "major":       personal.get("major", "Computer Science and Engineering"),
        "gpa":         str(personal.get("gpa", "3.78")),
        "grad_year":   personal.get("graduation_year", "2028"),
        "authorized":  "Yes",
        "sponsorship": "No",
        "relocate":    "Yes",
    }


def _value_for_label(label: str, sr_map: dict) -> Optional[str]:
    l = label.lower().strip()
    if not l:
        return None

    if "first" in l and "name" in l:
        return sr_map["first_name"]
    if "last" in l and "name" in l:
        return sr_map["last_name"]
    if "full" in l and "name" in l:
        return sr_map["name"]
    if "name" in l and "company" not in l:
        return sr_map["name"]
    if "email" in l:
        return sr_map["email"]
    if "phone" in l or "mobile" in l or "tel" in l:
        return sr_map["phone"]
    if "linkedin" in l:
        return sr_map["linkedin"]
    if "github" in l:
        return sr_map["github"]
    if "portfolio" in l or "website" in l or "personal" in l:
        return sr_map["portfolio"]
    if "city" in l:
        return sr_map["city"]
    if "state" in l or "province" in l:
        return sr_map["state"]
    if "country" in l:
        return sr_map["country"]
    if "zip" in l or "postal" in l:
        return sr_map["zip"]
    if "location" in l:
        return sr_map["location"]
    if "school" in l or "university" in l or "college" in l:
        return sr_map["school"]
    if "degree" in l:
        return sr_map["degree"]
    if "major" in l or "discipline" in l or "field of study" in l:
        return sr_map["major"]
    if "gpa" in l:
        return sr_map["gpa"]
    if "graduation" in l:
        return sr_map["grad_year"]
    if "authorized" in l or "eligible" in l or "legally" in l or "work in the us" in l:
        return "Yes"
    if "sponsor" in l or "visa" in l:
        return "No"
    if "relocat" in l:
        return "Yes"
    if "hear" in l or "referral" in l or "source" in l:
        return "LinkedIn"

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Main handler
# ──────────────────────────────────────────────────────────────────────────────

async def handle_smartrecruiters_apply(
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
    Fill a SmartRecruiters application form.

    Returns dict with: filled, failed, submitted, errors
    """
    from applicator.form_filler import _take_screenshot, _load_personal_info

    if personal_info is None:
        personal_info = _load_personal_info()

    sr_map = _build_sr_map(personal_info)
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

    await ev("SmartRecruit", "start", f"Starting SmartRecruiters handler for {company} - {role}")

    # ── Step 1: Click Apply if on listing page ──────────────────────────────
    current_url = page.url
    apply_clicked = False
    for sel in [
        'a:has-text("Apply Now")', 'button:has-text("Apply Now")',
        'a:has-text("Apply")', 'button:has-text("Apply")',
        '[data-testid="button-apply"]', '.job-ad-display--apply-button',
        'a[href*="/apply"]',
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                href = await btn.get_attribute("href") or ""
                if href and href.startswith("http"):
                    await page.goto(href, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(3.0)
                else:
                    await btn.click()
                    await asyncio.sleep(3.0)
                apply_clicked = True
                await ev("SmartRecruit", "info", "Clicked Apply button")
                break
        except Exception:
            continue

    await ss()

    # ── Step 2: Resume upload ───────────────────────────────────────────────
    if resume_path and os.path.exists(resume_path):
        resume_uploaded = False
        resume_basename = os.path.basename(resume_path)

        # Strategy A: set_input_files directly on hidden file input
        for sel in [
            'input[type="file"][name*="resume"]',
            'input[type="file"][data-testid*="resume"]',
            'input[type="file"][accept*="pdf"]',
            'input[type="file"]',
        ]:
            try:
                fis = page.locator(sel)
                count = await fis.count()
                if count == 0:
                    continue
                for i in range(count):
                    fi = fis.nth(i)
                    parent_text = await page.evaluate(f"""() => {{
                        const fis = document.querySelectorAll('{sel}');
                        const fi = fis[{i}];
                        if (!fi) return '';
                        let el = fi;
                        for (let j = 0; j < 5; j++) {{
                            el = el.parentElement;
                            if (!el) break;
                            const t = (el.innerText || '').toLowerCase();
                            if (t.length > 2 && t.length < 200) return t;
                        }}
                        return '';
                    }}""")
                    if "cover" in parent_text and "resume" not in parent_text:
                        continue
                    await fi.set_input_files(resume_path)
                    await asyncio.sleep(2.0)
                    # Verify file actually registered
                    try:
                        verified = await page.evaluate(f"""() => {{
                            const fis = document.querySelectorAll('{sel}');
                            const f = fis[{i}];
                            if (f && f.files && f.files.length > 0) return true;
                            return document.body.innerText.includes('{resume_basename}');
                        }}""")
                    except Exception:
                        verified = True
                    if verified:
                        resume_uploaded = True
                        await ev("SmartRecruit", "success",
                                 f"Resume uploaded (direct): {resume_basename}")
                        break
                    else:
                        await ev("SmartRecruit", "info",
                                 "Strategy A: file set but not registered — trying Strategy B")
                if resume_uploaded:
                    break
            except Exception:
                continue

        # Strategy B: intercept the file-chooser dialog via upload button
        if not resume_uploaded:
            for btn_sel in [
                'button:has-text("Upload")', 'a:has-text("Upload")',
                'button:has-text("Choose")', 'a:has-text("Choose File")',
                'button:has-text("Attach")', '[data-testid*="upload"]',
                '[class*="resume"]', '[class*="upload"]',
            ]:
                try:
                    btn = page.locator(btn_sel).first
                    if await btn.count() == 0:
                        continue
                    async with page.expect_file_chooser(timeout=4000) as fc_info:
                        await btn.click(timeout=3000)
                    fc = await fc_info.value
                    await fc.set_files(resume_path)
                    await asyncio.sleep(2.0)
                    resume_uploaded = True
                    await ev("SmartRecruit", "success",
                             f"Resume uploaded (file-chooser): {resume_basename}")
                    break
                except Exception:
                    continue

        if not resume_uploaded:
            await ev("SmartRecruit", "warning", "Resume upload failed")
            errors.append("Resume upload failed")

    await ss()

    # ── Step 3: Extract and fill all form fields ────────────────────────────
    await ev("SmartRecruit", "info", "Filling standard fields...")

    # SmartRecruiters typically has a multi-section form
    # We'll iterate through all pages/sections
    max_pages = 5  # max pages to navigate
    page_num = 0
    total_filled = 0

    while page_num < max_pages:
        page_num += 1

        form_fields = await page.evaluate("""() => {
            const fields = [];
            const seen = new Set();

            document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), textarea').forEach(el => {
                if (el.offsetParent === null) return;
                const id = el.id || '';
                const name = el.name || '';
                const sel = id ? '#' + CSS.escape(id) : (name ? '[name="' + name + '"]' : null);
                if (!sel || seen.has(sel)) return;
                seen.add(sel);
                let lbl = '';
                if (id) { const l = document.querySelector('label[for="' + id + '"]'); if (l) lbl = l.innerText.replace('*','').trim(); }
                if (!lbl) {
                    const wrapper = el.closest('.js-field, ._form-group, .field, .form-group, .question, li, [class*="field"]');
                    if (wrapper) { const l = wrapper.querySelector('label, legend, .field-label'); if (l) lbl = l.innerText.replace('*','').trim(); }
                }
                if (!lbl) lbl = el.placeholder || el.getAttribute('aria-label') || el.name || '';
                fields.push({
                    selector: sel, tag: el.tagName.toLowerCase(),
                    type: el.type || (el.tagName === 'TEXTAREA' ? 'textarea' : 'text'),
                    label: lbl, name: name, value: el.value || '',
                    required: el.required || el.getAttribute('aria-required') === 'true',
                });
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
                    const wrapper = el.closest('.js-field, .field, li, [class*="field"]');
                    if (wrapper) { const l = wrapper.querySelector('label, legend, .field-label'); if (l) lbl = l.innerText.replace('*','').trim(); }
                }
                const opts = Array.from(el.options).filter(o => o.value).map(o => o.text.trim());
                fields.push({
                    selector: sel, tag: 'select', type: 'select',
                    label: lbl, name: name, value: el.value || '',
                    required: el.required, options: opts,
                });
            });

            return fields;
        }""")

        custom_questions = []
        page_filled = 0

        for field in form_fields:
            sel = field.get("selector", "")
            ftype = field.get("type", "text")
            label = field.get("label", "")
            current_val = field.get("value", "")
            opts = field.get("options", [])

            if current_val and ftype not in ("file",):
                continue
            if ftype == "file":
                continue

            value = _value_for_label(label, sr_map)

            if ftype == "select":
                if not value:
                    l = label.lower()
                    if "gender" in l: value = "Male"
                    elif "race" in l or "ethnic" in l: value = "Asian"
                    elif "veteran" in l: value = "I am not a protected veteran"
                    elif "disability" in l: value = "No"
                if value and opts:
                    v_lower = value.lower()
                    best = next((o for o in opts if v_lower in o.lower() or o.lower() in v_lower), None)
                    value = best or value
                if value:
                    try:
                        await page.select_option(sel, label=value, timeout=3000)
                        page_filled += 1
                    except Exception:
                        try:
                            await page.select_option(sel, value=value, timeout=3000)
                            page_filled += 1
                        except Exception:
                            custom_questions.append(field)
                else:
                    if field.get("required"):
                        custom_questions.append(field)
                continue

            if value:
                try:
                    el = page.locator(sel).first
                    await el.click(click_count=3, timeout=3000)
                    await el.fill("", timeout=2000)
                    if any(kw in label.lower() for kw in ("location", "city", "address", "where")):
                        await el.press_sequentially(value, delay=60)
                        await asyncio.sleep(1.8)
                        _dropdown_visible = False
                        try:
                            _dd = page.locator(
                                "ul[role='listbox'], [class*='autocomplete'], "
                                "[class*='suggestions'], [class*='dropdown-menu']"
                            )
                            _dropdown_visible = await _dd.first.is_visible(timeout=800)
                        except Exception:
                            pass
                        if _dropdown_visible:
                            await el.press("ArrowDown")
                            await asyncio.sleep(0.4)
                            await el.press("Enter")
                        else:
                            await el.press("Tab")
                        await asyncio.sleep(0.5)
                    else:
                        await el.fill(value, timeout=3000)
                    page_filled += 1
                except Exception as e:
                    errors.append(f"Fill {label}: {e}")
            else:
                if label and (field.get("required") or ftype == "textarea"):
                    custom_questions.append(field)

        # Handle custom questions with LLM
        for field in custom_questions:
            sel = field.get("selector", "")
            ftype = field.get("type", "text")
            label = field.get("label", "")
            opts = field.get("options", [])

            answer = None
            if generate_answer_fn and label:
                try:
                    answer = await asyncio.to_thread(
                        generate_answer_fn, label, company, role, job_description
                    )
                except Exception:
                    pass

            if not answer:
                l = label.lower()
                if opts:
                    answer = opts[0]
                elif "why" in l or "interest" in l:
                    answer = f"I'm very excited about this {role} opportunity at {company} and believe my skills align well."
                elif "availab" in l or "start" in l:
                    answer = "June 2026"
                elif "authorized" in l or "eligible" in l:
                    answer = "Yes"
                elif "sponsor" in l:
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
                except Exception as e:
                    errors.append(f"Custom {label}: {e}")

        # Handle radio buttons on this page
        radio_groups = await page.evaluate("""() => {
            const groups = {};
            document.querySelectorAll('input[type="radio"]').forEach(r => {
                if (r.offsetParent === null) return;
                const name = r.name;
                if (!name) return;
                if (!groups[name]) groups[name] = {name, radios: [], checked: false};
                if (r.checked) groups[name].checked = true;
                const wrapper = r.closest('label, li, .radio, div');
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
            "sponsor": "No", "visa": "No", "relocat": "Yes",
            "felony": "No", "criminal": "No", "18": "Yes",
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

        # Checkboxes
        checkboxes = await page.evaluate("""() => {
            const results = [];
            document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                if (cb.checked || cb.offsetParent === null) return;
                if ((cb.name || '').toLowerCase().includes('cookie')) return;
                const wrapper = cb.closest('label, li, div');
                const text = wrapper ? wrapper.innerText.trim().toLowerCase() : '';
                if (text.includes('agree') || text.includes('certif') || text.includes('acknowledge') || text.includes('privacy') || text.includes('terms')) {
                    results.push({
                        selector: cb.id ? '#' + CSS.escape(cb.id) : '[name="' + cb.name + '"]',
                    });
                }
            });
            return results;
        }""")

        for cb in checkboxes:
            if not cb.get("selector"):
                continue
            try:
                loc = page.locator(cb["selector"]).first
                await loc.click(timeout=3000)
                page_filled += 1
            except Exception:
                pass

        total_filled += page_filled
        await ev("SmartRecruit", "info", f"Page {page_num}: filled {page_filled} fields")
        await ss()

        # Look for a "Next" / "Continue" button
        next_clicked = False
        for next_sel in [
            'button:has-text("Next")', 'button:has-text("Continue")',
            'button[type="submit"]:has-text("Next")',
            '[data-testid="button-next"]', '[data-testid="button-continue"]',
            '.nav-button--next', '.js-next-button',
        ]:
            try:
                btn = page.locator(next_sel).first
                if await btn.is_visible(timeout=1500):
                    # Only click Next if not Submit
                    btn_text = (await btn.inner_text(timeout=1000)).lower()
                    if "submit" in btn_text:
                        break
                    await btn.scroll_into_view_if_needed(timeout=2000)
                    await btn.click(timeout=3000)
                    await asyncio.sleep(2.0)
                    next_clicked = True
                    await ev("SmartRecruit", "info", f"Moved to next section")
                    break
            except Exception:
                continue

        if not next_clicked:
            break  # No more pages

    filled = total_filled

    await ev("SmartRecruit", "success",
        f"SmartRecruiters form filled ({filled} fields). Review before submitting.")

    return {
        "filled":    filled,
        "failed":    failed,
        "submitted": False,
        "errors":    errors,
    }

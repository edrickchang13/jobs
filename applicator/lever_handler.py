"""
Lever-specific application handler.

Lever is a simple single-page form (no account required).
Typical URL: jobs.lever.co/{company}/{posting-id}/apply

Structure:
  - Basic contact fields (name, email, phone, location)
  - Resume upload (file input)
  - Social/portfolio links (LinkedIn, GitHub, portfolio, etc.)
  - Additional info textarea
  - Custom screening questions (text, select, textarea)
  - EEO / demographic section at the bottom (native selects)
"""

import asyncio
import os
from typing import Callable, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Candidate → form-value mapping
# ──────────────────────────────────────────────────────────────────────────────

def _build_lever_map(personal: dict) -> dict:
    return {
        # Contact
        "name":            f"{personal.get('first_name','Edrick')} {personal.get('last_name','Chang')}",
        "full name":       f"{personal.get('first_name','Edrick')} {personal.get('last_name','Chang')}",
        "first name":      personal.get("first_name", "Edrick"),
        "last name":       personal.get("last_name", "Chang"),
        "email":           personal.get("email", "eachang@scu.edu"),
        "phone":           personal.get("phone", "4088066495"),
        "location":        personal.get("city", "Santa Clara, CA"),
        "city":            personal.get("city", "Santa Clara"),
        # Links
        "linkedin":        personal.get("linkedin", "https://linkedin.com/in/edrickchang"),
        "github":          personal.get("github", "https://github.com/edrickchang"),
        "portfolio":       personal.get("github", "https://github.com/edrickchang"),
        "website":         personal.get("github", "https://github.com/edrickchang"),
        "twitter":         "",
        # Education
        "school":          personal.get("school", "Santa Clara University"),
        "university":      personal.get("school", "Santa Clara University"),
        "degree":          personal.get("degree", "Bachelor of Science"),
        "major":           personal.get("major", "Computer Science and Engineering"),
        "gpa":             str(personal.get("gpa", "3.78")),
        "graduation year": personal.get("graduation_year", "2028"),
        # Work auth
        "authorized":      "Yes",
        "sponsorship":     "No",
        "visa":            "No",
        "relocate":        "Yes",
        # How did you hear
        "referral":        "LinkedIn",
        "hear about":      "LinkedIn",
    }


def _value_for_label(label: str, lever_map: dict) -> Optional[str]:
    """Return a value for a Lever field based on its label."""
    l = label.lower().strip()
    if not l:
        return None

    # Direct map lookups
    for key, val in lever_map.items():
        if key in l:
            return val

    # Extra fuzzy matches
    if "first" in l and "name" in l:
        return lever_map["first name"]
    if "last" in l and "name" in l:
        return lever_map["last name"]
    if "full" in l and "name" in l:
        return lever_map["full name"]
    if "name" in l:
        return lever_map["name"]
    if "email" in l:
        return lever_map["email"]
    if "phone" in l or "mobile" in l:
        return lever_map["phone"]
    if "linkedin" in l:
        return lever_map["linkedin"]
    if "github" in l:
        return lever_map["github"]
    if "portfolio" in l or "website" in l or "personal site" in l:
        return lever_map["website"]
    if "school" in l or "university" in l or "college" in l or "institution" in l:
        return lever_map["school"]
    if "degree" in l:
        return lever_map["degree"]
    if "major" in l or "discipline" in l or "field of study" in l:
        return lever_map["major"]
    if "gpa" in l:
        return lever_map["gpa"]
    if "authorized" in l or "eligible" in l or "work in the us" in l or "legally" in l:
        return lever_map["authorized"]
    if "sponsor" in l or "visa" in l:
        return lever_map["sponsorship"]
    if "relocat" in l:
        return lever_map["relocate"]
    if "hear" in l or "referral" in l or "source" in l:
        return lever_map["referral"]
    if "location" in l or "city" in l:
        return lever_map["location"]

    return None


# ──────────────────────────────────────────────────────────────────────────────
# EEO helper
# ──────────────────────────────────────────────────────────────────────────────

EEO_LEVER_MAP = {
    "gender":      "Male",
    "race":        "Asian",
    "ethnicity":   "Asian",
    "hispanic":    "No",
    "veteran":     "I am not a protected veteran",
    "disability":  "No, I don't have a disability",
}


async def _fill_lever_eeo(page, ev):
    """Fill Lever EEO section at bottom of form (native <select> elements)."""
    eeo_selects = await page.evaluate("""() => {
        const results = [];
        const selects = document.querySelectorAll('select');
        for (const s of selects) {
            if (s.offsetParent === null) continue;
            const currentText = s.options[s.selectedIndex]?.text?.trim().toLowerCase() || '';
            const isPlaceholder = ['select...', 'select', 'choose', '--', '', 'prefer not to answer'].includes(currentText);
            const label = s.closest('.application-field, .lever-field, .field, .eeo-field, li, div')
                ?.querySelector('label, .field-label, legend')?.innerText?.replace('*','').trim() || '';
            const opts = Array.from(s.options).map(o => ({v: o.value, t: o.text.trim(), i: o.index}));
            results.push({
                selector: s.id ? '#' + CSS.escape(s.id) : (s.name ? 'select[name="' + s.name + '"]' : ''),
                label: label,
                isPlaceholder: isPlaceholder,
                currentText: currentText,
                options: opts,
            });
        }
        return results;
    }""")

    for sel_info in eeo_selects:
        selector = sel_info.get("selector", "")
        label = sel_info.get("label", "").lower()
        if not selector:
            continue

        target = None
        for kw, val in EEO_LEVER_MAP.items():
            if kw in label:
                target = val
                break

        if not target:
            continue

        opts = sel_info.get("options", [])
        # Find best matching option
        target_lower = target.lower()
        best_opt = None
        for opt in opts:
            ot = opt["t"].lower()
            if ot == target_lower:
                best_opt = opt
                break
        if not best_opt:
            for opt in opts:
                ot = opt["t"].lower()
                if target_lower[:5] in ot:
                    best_opt = opt
                    break

        if not best_opt:
            continue

        try:
            loc = page.locator(selector).first
            await loc.scroll_into_view_if_needed(timeout=3000)
            await loc.select_option(index=best_opt["i"], timeout=3000)
            await page.evaluate(f"""() => {{
                const el = document.querySelector('{selector}');
                if (el) el.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}""")
            await ev("Lever EEO", "success", f"Set '{target}' for '{sel_info['label'][:40]}'")
        except Exception as e:
            await ev("Lever EEO", "warning", f"EEO select failed '{sel_info['label'][:30]}': {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Main handler
# ──────────────────────────────────────────────────────────────────────────────

async def handle_lever_apply(
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
    Fill a Lever application form.

    Returns dict with: filled, failed, submitted, errors
    """
    from applicator.form_filler import _take_screenshot, _load_personal_info

    if personal_info is None:
        personal_info = _load_personal_info()

    lever_map = _build_lever_map(personal_info)
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

    await ev("Lever", "start", f"Starting Lever handler for {company} - {role}")

    # ── Step 1: Navigate to /apply URL if on listing page ──────────────────
    current_url = page.url
    if "/apply" not in current_url:
        apply_url = current_url.rstrip("/") + "/apply"
        try:
            await page.goto(apply_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2.0)
            await ev("Lever", "info", f"Navigated to apply URL")
        except Exception:
            # Try clicking Apply button
            for sel in ['a:has-text("Apply")', 'button:has-text("Apply")',
                        'a:has-text("Apply for this job")', '.template-btn-submit']:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=1500):
                        await btn.click()
                        await asyncio.sleep(2.0)
                        await ev("Lever", "info", "Clicked Apply button")
                        break
                except Exception:
                    continue

    await ss()

    # ── Step 2: Upload resume ───────────────────────────────────────────────
    if resume_path and os.path.exists(resume_path):
        resume_uploaded = False
        # Lever uses a file input inside a drop zone
        resume_selectors = [
            'input[type="file"][name*="resume"]',
            'input[type="file"][id*="resume"]',
            'input[type="file"][accept*="pdf"]',
            'input[type="file"]',
        ]
        for sel in resume_selectors:
            try:
                fi = page.locator(sel).first
                count = await fi.count()
                if count > 0:
                    # Force set even if element is hidden (Lever hides file input behind styled button)
                    try:
                        await fi.set_input_files(resume_path)
                    except Exception:
                        # Try with evaluate to bypass visibility restrictions
                        try:
                            await page.evaluate(f"""() => {{
                                const input = document.querySelector('{sel}');
                                if (input) input.style.display = 'block';
                            }}""")
                            await fi.set_input_files(resume_path)
                        except Exception:
                            continue
                    await asyncio.sleep(2.0)
                    # Verify something appeared in the resume field area
                    resume_uploaded = True
                    await ev("Lever", "success", f"Resume uploaded: {os.path.basename(resume_path)}")
                    break
            except Exception:
                continue
        if not resume_uploaded:
            # Try clicking ATTACH RESUME/CV button to open the upload modal, then set files
            attach_btns = [
                'a:has-text("ATTACH")', 'button:has-text("ATTACH")',
                'a:has-text("Attach")', 'button:has-text("Attach")',
                '.resume-upload-btn', '.attach-resume',
                'a[class*="resume"]', 'button[class*="resume"]',
            ]
            for btn_sel in attach_btns:
                try:
                    btn = page.locator(btn_sel).first
                    if await btn.count() > 0 and await btn.is_visible(timeout=1500):
                        await btn.click()
                        await asyncio.sleep(1.0)
                        # Now look for file input that appeared
                        for fi_sel in ['input[type="file"]', 'input[type="file"][accept*="pdf"]']:
                            try:
                                fi2 = page.locator(fi_sel).first
                                if await fi2.count() > 0:
                                    await fi2.set_input_files(resume_path)
                                    await asyncio.sleep(2.0)
                                    resume_uploaded = True
                                    await ev("Lever", "success", f"Resume uploaded via modal: {os.path.basename(resume_path)}")
                                    break
                            except Exception:
                                continue
                        if resume_uploaded:
                            break
                except Exception:
                    continue
        if not resume_uploaded:
            await ev("Lever", "warning", "Resume upload failed — file input not found")
            errors.append("Resume upload failed")
    else:
        await ev("Lever", "warning", f"Resume not found: {resume_path}")

    await ss()

    # ── Step 3: Fill standard text fields ──────────────────────────────────
    await ev("Lever", "info", "Filling standard fields...")

    # Extract all fillable inputs
    form_fields = await page.evaluate("""() => {
        const fields = [];
        const seen = new Set();

        // Text/email/tel inputs
        document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), textarea').forEach(el => {
            if (el.offsetParent === null) return;
            const id = el.id || '';
            const name = el.name || '';
            const sel = id ? '#' + CSS.escape(id) : (name ? '[name="' + name + '"]' : null);
            if (!sel || seen.has(sel)) return;
            seen.add(sel);
            // Label lookup
            let lbl = '';
            if (id) { const l = document.querySelector('label[for="' + id + '"]'); if (l) lbl = l.innerText.replace('*','').trim(); }
            if (!lbl) {
                const wrapper = el.closest('.application-field, .lever-field, .field, .form-group, .question, li');
                if (wrapper) { const l = wrapper.querySelector('label, .field-label, legend'); if (l) lbl = l.innerText.replace('*','').trim(); }
            }
            if (!lbl) lbl = el.placeholder || el.name || '';
            fields.push({
                selector: sel, tag: el.tagName.toLowerCase(),
                type: el.type || (el.tagName === 'TEXTAREA' ? 'textarea' : 'text'),
                label: lbl, name: name, value: el.value || '',
                required: el.required || el.getAttribute('aria-required') === 'true',
            });
        });

        // Native selects
        document.querySelectorAll('select').forEach(el => {
            if (el.offsetParent === null) return;
            const id = el.id || ''; const name = el.name || '';
            const sel = id ? '#' + CSS.escape(id) : (name ? 'select[name="' + name + '"]' : null);
            if (!sel || seen.has(sel)) return;
            seen.add(sel);
            let lbl = '';
            if (id) { const l = document.querySelector('label[for="' + id + '"]'); if (l) lbl = l.innerText.replace('*','').trim(); }
            if (!lbl) {
                const wrapper = el.closest('.application-field, .lever-field, .field, li');
                if (wrapper) { const l = wrapper.querySelector('label, .field-label, legend'); if (l) lbl = l.innerText.replace('*','').trim(); }
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

    for field in form_fields:
        sel = field.get("selector", "")
        ftype = field.get("type", "text")
        label = field.get("label", "")
        current_val = field.get("value", "")
        opts = field.get("options", [])

        # Skip already-filled fields
        if current_val and ftype not in ("file",):
            continue

        # Skip file inputs (handled in Step 2)
        if ftype == "file":
            continue

        # Get value for this field
        value = _value_for_label(label, lever_map)

        if ftype == "select":
            if not value:
                # Try EEO map
                lbl_lower = label.lower()
                for kw, v in EEO_LEVER_MAP.items():
                    if kw in lbl_lower:
                        value = v
                        break

            if value and opts:
                # Find best match in options
                v_lower = value.lower()
                best = next((o for o in opts if v_lower in o.lower() or o.lower() in v_lower), None)
                value = best or value

            if value:
                try:
                    await page.select_option(sel, label=value, timeout=3000)
                    filled += 1
                except Exception:
                    try:
                        await page.select_option(sel, value=value, timeout=3000)
                        filled += 1
                    except Exception as e:
                        # Mark as custom question
                        custom_questions.append(field)
            else:
                if field.get("required"):
                    custom_questions.append(field)
            continue

        # Text / textarea
        if value:
            try:
                el = page.locator(sel).first
                await el.click(click_count=3, timeout=3000)
                await el.fill("", timeout=2000)  # clear first
                # For location/city fields use pressSequentially to fire all keyboard
                # events (needed to trigger Google Places / Lever autocomplete).
                # Then check if a dropdown appeared; if so pick the first suggestion
                # with ArrowDown+Enter. Otherwise just Tab away so the typed value
                # is committed without accidentally submitting the form.
                lbl_lower = label.lower()
                if any(kw in lbl_lower for kw in ("location", "city", "address", "where")):
                    await el.press_sequentially(value, delay=60)
                    await asyncio.sleep(1.8)
                    # Check whether an autocomplete suggestion list is visible
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
                        await el.press("Tab")  # commit value without form submission
                    await asyncio.sleep(0.5)
                else:
                    await el.fill(value, timeout=3000)
                filled += 1
            except Exception as e:
                failed += 1
                errors.append(f"Fill {label}: {e}")
        else:
            # Custom question needing LLM
            if label and (field.get("required") or ftype == "textarea"):
                custom_questions.append(field)

    await ss()

    # ── Step 4: Handle custom/screening questions with LLM ─────────────────
    if custom_questions:
        await ev("Lever", "info", f"Generating answers for {len(custom_questions)} custom question(s)...")

        for field in custom_questions:
            sel = field.get("selector", "")
            ftype = field.get("type", "text")
            label = field.get("label", "")
            opts = field.get("options", [])

            answer = None

            # Try LLM
            if generate_answer_fn and label:
                try:
                    answer = await asyncio.to_thread(
                        generate_answer_fn, label, company, role, job_description
                    )
                except Exception as e:
                    await ev("Lever", "warning", f"LLM failed for '{label[:40]}': {e}")

            if not answer:
                l = label.lower()
                if opts:
                    answer = opts[0]
                elif "why" in l or "tell us" in l or "describe" in l or "passion" in l:
                    answer = (
                        f"I'm passionate about this {role} opportunity at {company}. "
                        "My CS & Engineering background and hands-on project experience make me "
                        "a great fit. I'm excited to contribute and grow with your team."
                    )
                elif "experience" in l or "background" in l:
                    answer = (
                        "I have experience in software development through academic projects and "
                        "coursework. I've worked with Python, JavaScript, and various frameworks."
                    )
                elif "availab" in l or "start" in l:
                    answer = "I am available to start in June 2026."
                elif "salary" in l or "compensation" in l:
                    answer = "I am flexible and open to discussion."
                elif "authorized" in l or "eligible" in l:
                    answer = "Yes"
                elif "sponsor" in l or "visa" in l:
                    answer = "No"
                else:
                    answer = "N/A"

            if ftype == "select" and opts:
                # find best matching option
                a_lower = answer.lower()
                best = next((o for o in opts if a_lower in o.lower() or o.lower() in a_lower), opts[0])
                try:
                    await page.select_option(sel, label=best, timeout=3000)
                    filled += 1
                except Exception as e:
                    failed += 1
                    errors.append(f"Custom select {label}: {e}")
            else:
                try:
                    el = page.locator(sel).first
                    await el.click(click_count=3, timeout=3000)
                    await el.fill(answer, timeout=3000)
                    filled += 1
                except Exception as e:
                    failed += 1
                    errors.append(f"Custom text {label}: {e}")

    # ── Step 5: Handle radio buttons (work auth, etc.) ─────────────────────
    radio_groups = await page.evaluate("""() => {
        const groups = {};
        document.querySelectorAll('input[type="radio"]').forEach(r => {
            if (r.offsetParent === null) return;
            const name = r.name || r.id;
            if (!groups[name]) groups[name] = {name, radios: [], checked: false};
            if (r.checked) groups[name].checked = true;
            const wrapper = r.closest('label, li, .radio-option, .lever-radio, div');
            const text = wrapper ? wrapper.innerText.trim() : r.value;
            groups[name].radios.push({
                selector: r.id ? '#' + CSS.escape(r.id) : '[name="' + name + '"][value="' + r.value + '"]',
                text: text, value: r.value,
            });
        });
        return Object.values(groups).filter(g => !g.checked);
    }""")

    radio_answer_map = {
        "authorized": "Yes",
        "legally":    "Yes",
        "eligible":   "Yes",
        "sponsor":    "No",
        "visa":       "No",
        "relocat":    "Yes",
        "remote":     "Yes",
        "hybrid":     "Yes",
        "felony":     "No",
        "criminal":   "No",
        "18":         "Yes",
    }

    for group in radio_groups:
        q_text = group.get("name", "").lower()
        radios = group.get("radios", [])

        # Find question text from first radio's ancestor
        try:
            q_text_full = await page.evaluate(f"""() => {{
                const r = document.querySelector('input[name="{group['name']}"]');
                if (!r) return '';
                let el = r;
                for (let i = 0; i < 8; i++) {{
                    el = el.parentElement;
                    if (!el || el === document.body) break;
                    const lbl = el.querySelector('label, legend, .field-label, h3, h4, p');
                    if (lbl) {{
                        const t = lbl.innerText.trim();
                        if (t.length > 5) return t.toLowerCase();
                    }}
                }}
                return '';
            }}""")
            q_text = q_text_full or q_text
        except Exception:
            pass

        target = None
        for kw, val in radio_answer_map.items():
            if kw in q_text:
                target = val
                break

        if not target:
            continue

        # Find radio with matching text
        target_sel = None
        for r in radios:
            if r["text"].strip().lower() == target.lower():
                target_sel = r["selector"]
                break
        if not target_sel:
            for r in radios:
                if target.lower() in r["text"].strip().lower():
                    target_sel = r["selector"]
                    break

        if target_sel:
            try:
                loc = page.locator(target_sel).first
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=3000)
                await ev("Lever", "success", f"Radio '{target}' for '{q_text[:50]}'")
                filled += 1
            except Exception as e:
                await ev("Lever", "warning", f"Radio click failed: {e}")

    # ── Step 6: EEO section ────────────────────────────────────────────────
    await _fill_lever_eeo(page, ev)

    # ── Step 7: Checkboxes (agree/acknowledge) ─────────────────────────────
    checkboxes = await page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            if (cb.checked || cb.offsetParent === null) return;
            const wrapper = cb.closest('label, li, .checkbox-field, div');
            const text = wrapper ? wrapper.innerText.trim().toLowerCase() : '';
            if (text.includes('agree') || text.includes('acknowledge') || text.includes('certif') || text.includes('understand')) {
                results.push({
                    selector: cb.id ? '#' + CSS.escape(cb.id) : '[name="' + cb.name + '"]',
                    text: text.substring(0, 100),
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
            await ev("Lever", "success", f"Checked: '{cb['text'][:50]}'")
        except Exception:
            pass

    await ss()

    await ev("Lever", "success",
        f"Lever form filled ({filled} fields, {failed} failed). Review in browser before submitting.")

    return {
        "filled":    filled,
        "failed":    failed,
        "submitted": False,
        "errors":    errors,
    }

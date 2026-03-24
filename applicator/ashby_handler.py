"""
Ashby-specific application handler.

Ashby HQ is a modern React-based ATS. No account required.
Typical URL: jobs.ashbyhq.com/{company}/{role-id}/application

Structure:
  - Basic contact section (name, email, phone)
  - Resume/CV upload (file input, sometimes drag-drop zone)
  - LinkedIn / portfolio / social links
  - Location / work authorization questions
  - Custom screening questions (text, select, textarea, multi-select)
  - Diversity & Inclusion section at the bottom (optional)
  - Single-page form, submit at the end

UI specifics:
  - React Select custom dropdowns (`[class*="select__"]`) for some fields
  - File drag-drop zone plus hidden file input
  - Radio buttons for Yes/No questions (work auth, remote)
  - Some questions use Ashby's own component classes like `.ashby-application-form-field`
"""

import asyncio
import os
from typing import Callable, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Candidate → form-value mapping
# ──────────────────────────────────────────────────────────────────────────────

def _build_ashby_map(personal: dict) -> dict:
    return {
        "first_name":   personal.get("first_name", "Edrick"),
        "last_name":    personal.get("last_name", "Chang"),
        "name":         f"{personal.get('first_name','Edrick')} {personal.get('last_name','Chang')}",
        "email":        personal.get("email", "eachang@scu.edu"),
        "phone":        personal.get("phone", "4088066495"),
        "location":     personal.get("city", "Santa Clara, CA"),
        "linkedin":     personal.get("linkedin", "https://linkedin.com/in/edrickchang"),
        "github":       personal.get("github", "https://github.com/edrickchang"),
        "portfolio":    personal.get("github", "https://github.com/edrickchang"),
        "website":      personal.get("github", "https://github.com/edrickchang"),
        "school":       personal.get("school", "Santa Clara University"),
        "degree":       personal.get("degree", "Bachelor of Science"),
        "major":        personal.get("major", "Computer Science and Engineering"),
        "gpa":          str(personal.get("gpa", "3.78")),
        "graduation":   personal.get("graduation_year", "2028"),
        "authorized":   "Yes",
        "sponsorship":  "No",
        "relocate":     "Yes",
        "country":      "United States",
    }


def _value_for_label(label: str, ashby_map: dict) -> Optional[str]:
    l = label.lower().strip()
    if not l:
        return None

    if "first" in l and "name" in l:
        return ashby_map["first_name"]
    if "last" in l and "name" in l:
        return ashby_map["last_name"]
    if "full" in l and "name" in l:
        return ashby_map["name"]
    if "name" in l and "company" not in l:
        return ashby_map["name"]
    if "email" in l:
        return ashby_map["email"]
    if "phone" in l or "mobile" in l or "tel" in l:
        return ashby_map["phone"]
    if "linkedin" in l:
        return ashby_map["linkedin"]
    if "github" in l:
        return ashby_map["github"]
    if "portfolio" in l or "website" in l or "personal" in l:
        return ashby_map["portfolio"]
    if "location" in l or "city" in l:
        return ashby_map["location"]
    if "country" in l:
        return ashby_map["country"]
    if "school" in l or "university" in l or "college" in l or "institution" in l:
        return ashby_map["school"]
    if "degree" in l:
        return ashby_map["degree"]
    if "major" in l or "discipline" in l or "field of study" in l:
        return ashby_map["major"]
    if "gpa" in l:
        return ashby_map["gpa"]
    if "graduation" in l:
        return ashby_map["graduation"]
    if "authorized" in l or "eligible" in l or "legally" in l or "work in the us" in l:
        return "Yes"
    if "sponsor" in l or "visa" in l:
        return "No"
    if "relocat" in l:
        return "Yes"
    if "how did you" in l or "hear" in l or "referral" in l or "source" in l:
        return "LinkedIn"

    return None


# ──────────────────────────────────────────────────────────────────────────────
# React-select coordinate clicker (same approach as in dashboard/app.py)
# ──────────────────────────────────────────────────────────────────────────────

async def _coord_click_option(page, target: str) -> tuple:
    """Click the best-matching visible option in any open React Select menu."""
    try:
        opts_data = await page.evaluate("""() => {
            const result = [];
            for (const ms of ['[class*="select__menu"]', '[role="listbox"]']) {
                for (const menu of document.querySelectorAll(ms)) {
                    for (const opt of menu.querySelectorAll('[class*="option"], [role="option"]')) {
                        const r = opt.getBoundingClientRect();
                        if (r.width > 10 && r.height > 5) {
                            result.push({
                                text: opt.innerText.trim(),
                                x: r.left + r.width / 2,
                                y: r.top + r.height / 2
                            });
                        }
                    }
                }
            }
            return result;
        }""")
    except Exception:
        return False, ""

    if not opts_data:
        return False, ""

    tgt = target.lower()
    best = None
    for o in opts_data:
        if o["text"].lower() == tgt:
            best = o
            break
    if not best:
        for o in opts_data:
            t = o["text"].lower()
            if "no options" in t or "loading" in t or not t:
                continue
            if tgt in t or t in tgt:
                if best is None or len(o["text"]) > len(best["text"]):
                    best = o

    if not best:
        return False, ""

    await page.mouse.click(best["x"], best["y"])
    await asyncio.sleep(0.4)
    return True, best["text"]


async def _fill_react_select(page, container_locator, value: str, ev=None, label="") -> bool:
    """Open a React Select container and click the best-matching option."""
    try:
        await container_locator.scroll_into_view_if_needed(timeout=3000)
        await container_locator.click(timeout=3000)
        await asyncio.sleep(0.8)

        ok, text = await _coord_click_option(page, value)
        if ok:
            if ev:
                await ev("Ashby", "success", f"React-Select '{text[:30]}' for '{label[:40]}'")
            return True

        # Try typeahead
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.2)
        await container_locator.click(timeout=3000)
        await asyncio.sleep(0.3)
        await page.keyboard.type(value[:20], delay=60)
        await asyncio.sleep(1.5)
        ok2, text2 = await _coord_click_option(page, value)
        if ok2:
            if ev:
                await ev("Ashby", "success", f"React-Select typeahead '{text2[:30]}' for '{label[:40]}'")
            return True

        await page.keyboard.press("Escape")
        if ev:
            await ev("Ashby", "warning", f"React-Select could not select '{value[:30]}' for '{label[:40]}'")
        return False
    except Exception as e:
        if ev:
            await ev("Ashby", "warning", f"React-Select error for '{label[:40]}': {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Main handler
# ──────────────────────────────────────────────────────────────────────────────

async def handle_ashby_apply(
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
    Fill an Ashby HQ application form.

    Returns dict with: filled, failed, submitted, errors
    """
    from applicator.form_filler import _take_screenshot, _load_personal_info

    if personal_info is None:
        personal_info = _load_personal_info()

    ashby_map = _build_ashby_map(personal_info)
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

    await ev("Ashby", "start", f"Starting Ashby handler for {company} - {role}")

    # ── Step 1: Ensure we're on the application page ────────────────────────
    current_url = page.url
    # Ashby listing URL: jobs.ashbyhq.com/{co}/{id}
    # Application URL:  jobs.ashbyhq.com/{co}/{id}/application
    if "/application" not in current_url:
        apply_url = current_url.rstrip("/") + "/application"
        try:
            apply_btn_clicked = False
            for sel in [
                'a:has-text("Apply")', 'button:has-text("Apply")',
                'a:has-text("Apply for this Job")', 'button:has-text("Apply for this Job")',
                '[data-testid="apply-button"]',
            ]:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=1500):
                        await btn.click()
                        await asyncio.sleep(2.0)
                        apply_btn_clicked = True
                        await ev("Ashby", "info", "Clicked Apply button")
                        break
                except Exception:
                    continue

            if not apply_btn_clicked:
                await page.goto(apply_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2.0)
                await ev("Ashby", "info", f"Navigated to application URL")
        except Exception as e:
            await ev("Ashby", "warning", f"Navigation error: {e}")

    await ss()

    # ── Step 2: Upload resume ───────────────────────────────────────────────
    if resume_path and os.path.exists(resume_path):
        resume_uploaded = False

        # Ashby usually has a file drop zone + hidden file input
        # The file input is often inside .ashby-file-upload or similar
        file_selectors = [
            'input[type="file"][name*="resume"]',
            'input[type="file"][name*="Resume"]',
            'input[type="file"][accept*="pdf"]',
            'input[type="file"]',
        ]
        for sel in file_selectors:
            try:
                fis = page.locator(sel)
                count = await fis.count()
                if count > 0:
                    # Check labels to find resume input (not cover letter)
                    for i in range(count):
                        fi = fis.nth(i)
                        # Get parent container text to check label
                        parent_text = await page.evaluate(f"""() => {{
                            const fis = document.querySelectorAll('{sel}');
                            const fi = fis[{i}];
                            if (!fi) return '';
                            let el = fi;
                            for (let j = 0; j < 5; j++) {{
                                el = el.parentElement;
                                if (!el) break;
                                const t = el.innerText || '';
                                if (t.length > 3 && t.length < 200) return t.toLowerCase();
                            }}
                            return '';
                        }}""")

                        # Skip cover letter inputs
                        if "cover" in parent_text and "resume" not in parent_text:
                            continue

                        await fi.set_input_files(resume_path)
                        await asyncio.sleep(2.0)
                        resume_uploaded = True
                        await ev("Ashby", "success", f"Resume uploaded: {os.path.basename(resume_path)}")
                        break

                    if resume_uploaded:
                        break
            except Exception:
                continue

        if not resume_uploaded:
            await ev("Ashby", "warning", "Resume upload failed — file input not found")
            errors.append("Resume upload failed")
    else:
        await ev("Ashby", "warning", f"Resume not found: {resume_path}")

    await ss()

    # ── Step 3: Extract and fill standard fields ────────────────────────────
    await ev("Ashby", "info", "Filling standard fields...")

    form_fields = await page.evaluate("""() => {
        const fields = [];
        const seen = new Set();

        // Text/email/tel/url/number inputs + textareas
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
                const wrapper = el.closest('.ashby-application-form-field, .field, .form-group, .question, li, [class*="field"]');
                if (wrapper) { const l = wrapper.querySelector('label, legend, .field-label, [class*="label"]'); if (l) lbl = l.innerText.replace('*','').trim(); }
            }
            if (!lbl) lbl = el.placeholder || el.getAttribute('aria-label') || el.name || '';
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
                const wrapper = el.closest('.ashby-application-form-field, .field, li, [class*="field"]');
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

        value = _value_for_label(label, ashby_map)

        if ftype == "select":
            if value and opts:
                v_lower = value.lower()
                best = next((o for o in opts if v_lower in o.lower() or o.lower() in v_lower), None)
                value = best or value
            if value:
                try:
                    await page.select_option(sel, label=value, timeout=3000)
                    filled += 1
                except Exception as e:
                    try:
                        await page.select_option(sel, value=value, timeout=3000)
                        filled += 1
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
                filled += 1
            except Exception as e:
                failed += 1
                errors.append(f"Fill {label}: {e}")
        else:
            if label and (field.get("required") or ftype == "textarea"):
                custom_questions.append(field)

    # ── Step 4: Handle React Select custom dropdowns ────────────────────────
    react_selects = await page.evaluate("""() => {
        const results = [];
        const containers = document.querySelectorAll('[class*="select__container"], [class*="SelectContainer"]');
        for (let i = 0; i < containers.length; i++) {
            const c = containers[i];
            if (c.offsetParent === null) continue;
            const sv = c.querySelector('[class*="single-value"], [class*="singleValue"]');
            const ph = c.querySelector('[class*="placeholder"]');
            const isUnfilled = !sv || (sv.innerText.trim().toLowerCase().startsWith('select'));
            if (!isUnfilled) continue;
            const wrapper = c.closest('.ashby-application-form-field, .field, li, [class*="field"]');
            const lbl = wrapper ? (wrapper.querySelector('label, legend, .field-label, [class*="label"]')?.innerText?.replace('*','').trim() || '') : '';
            results.push({index: i, label: lbl, placeholder: ph ? ph.innerText.trim() : ''});
        }
        return results;
    }""")

    for rs_info in react_selects:
        idx = rs_info.get("index", 0)
        label = rs_info.get("label", "")

        value = _value_for_label(label, ashby_map)
        if not value:
            # Demographic defaults
            l = label.lower()
            if "gender" in l:
                value = "Male"
            elif "race" in l or "ethnic" in l:
                value = "Asian"
            elif "veteran" in l:
                value = "I am not a protected veteran"
            elif "disability" in l:
                value = "No, I don't have a disability"
            elif "hispanic" in l or "latino" in l:
                value = "No"
            elif "pronoun" in l:
                value = "He/Him"
            else:
                continue

        container_loc = page.locator('[class*="select__container"], [class*="SelectContainer"]').nth(idx)
        ok = await _fill_react_select(page, container_loc, value, ev, label)
        if ok:
            filled += 1
        else:
            failed += 1
            errors.append(f"React-Select failed: {label}")

    await ss()

    # ── Step 5: Custom questions via LLM ───────────────────────────────────
    if custom_questions:
        await ev("Ashby", "info", f"Generating answers for {len(custom_questions)} custom question(s)...")

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
                except Exception as e:
                    await ev("Ashby", "warning", f"LLM failed for '{label[:40]}': {e}")

            if not answer:
                l = label.lower()
                if opts:
                    answer = opts[0]
                elif "why" in l or "tell us" in l or "describe" in l or "interest" in l:
                    answer = (
                        f"I'm excited about this {role} role at {company}. "
                        "My experience in software engineering through coursework and projects, "
                        "combined with strong fundamentals in CS & Engineering, makes me a great fit."
                    )
                elif "availab" in l or "start" in l:
                    answer = "Available June 2026"
                elif "authorized" in l or "eligible" in l:
                    answer = "Yes"
                elif "sponsor" in l:
                    answer = "No"
                elif "salary" in l or "compensation" in l:
                    answer = "Open to discussion"
                else:
                    answer = "N/A"

            if ftype == "select" and opts:
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

    # ── Step 6: Radio buttons ───────────────────────────────────────────────
    radio_groups = await page.evaluate("""() => {
        const groups = {};
        document.querySelectorAll('input[type="radio"]').forEach(r => {
            if (r.offsetParent === null) return;
            const name = r.name || r.id;
            if (!groups[name]) groups[name] = {name, radios: [], checked: false};
            if (r.checked) groups[name].checked = true;
            const wrapper = r.closest('label, li, div[class*="radio"], .radio-option');
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
        "remote": "Yes", "felony": "No", "criminal": "No", "18": "Yes",
    }

    for group in radio_groups:
        q_name = group.get("name", "").lower()
        radios = group.get("radios", [])

        # Try to get the question text from the ancestor
        try:
            q_text = await page.evaluate(f"""() => {{
                const r = document.querySelector('[name="{group["name"]}"]');
                if (!r) return '';
                let el = r;
                for (let i = 0; i < 8; i++) {{
                    el = el.parentElement;
                    if (!el || el === document.body) break;
                    const lbl = el.querySelector('label, legend, [class*="label"], h3, h4, p');
                    if (lbl) {{
                        const t = lbl.innerText.trim();
                        if (t.length > 5 && t.length < 300) return t.toLowerCase();
                    }}
                }}
                return '';
            }}""")
        except Exception:
            q_text = q_name

        target = None
        for kw, val in radio_map.items():
            if kw in q_text or kw in q_name:
                target = val
                break

        if not target:
            continue

        # Find matching radio
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
                await ev("Ashby", "success", f"Radio '{target}' for '{q_text[:50]}'")
                filled += 1
            except Exception as e:
                await ev("Ashby", "warning", f"Radio click failed: {e}")

    # ── Step 7: Checkboxes ─────────────────────────────────────────────────
    checkboxes = await page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            if (cb.checked || cb.offsetParent === null) return;
            const wrapper = cb.closest('label, li, .checkbox-field, div');
            const text = wrapper ? wrapper.innerText.trim().toLowerCase() : '';
            if (text.includes('agree') || text.includes('acknowledge') || text.includes('certif')
                || text.includes('understand') || text.includes('confirm')) {
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
            await ev("Ashby", "success", f"Checked: '{cb['text'][:50]}'")
        except Exception:
            pass

    await ss()

    await ev("Ashby", "success",
        f"Ashby form filled ({filled} fields, {failed} failed). Review in browser before submitting.")

    return {
        "filled":    filled,
        "failed":    failed,
        "submitted": False,
        "errors":    errors,
    }

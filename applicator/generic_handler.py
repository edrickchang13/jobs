"""
Generic application handler for Unknown / direct company career sites.

Used when no specific ATS is detected. Tries to fill any web-based
application form using a combination of:
  1. Label-based field mapping (known candidate data)
  2. React Select / custom dropdown coordinate clicking
  3. File upload for resume
  4. Radio button + checkbox handling
  5. LLM fallback for custom questions

This covers the 87 "Unknown" jobs in the pipeline that use custom portals.
"""

import asyncio
import os
from typing import Callable, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Comprehensive label → value map for generic forms
# ──────────────────────────────────────────────────────────────────────────────

def _build_generic_map(personal: dict) -> dict:
    return {
        # Contact
        "first_name":  personal.get("first_name", "Edrick"),
        "first name":  personal.get("first_name", "Edrick"),
        "last_name":   personal.get("last_name", "Chang"),
        "last name":   personal.get("last_name", "Chang"),
        "full name":   f"{personal.get('first_name','Edrick')} {personal.get('last_name','Chang')}",
        "name":        f"{personal.get('first_name','Edrick')} {personal.get('last_name','Chang')}",
        "email":       personal.get("email", "eachang@scu.edu"),
        "phone":       personal.get("phone", "4088066495"),
        "mobile":      personal.get("phone", "4088066495"),
        "tel":         personal.get("phone", "4088066495"),

        # Location
        "address":     personal.get("address", ""),
        "street":      personal.get("address", ""),
        "city":        personal.get("city", "Santa Clara"),
        "state":       personal.get("state", "California"),
        "province":    personal.get("state", "California"),
        "zip":         personal.get("zip", "95050"),
        "postal":      personal.get("zip", "95050"),
        "country":     "United States",
        "location":    personal.get("city", "Santa Clara, CA"),

        # Links
        "linkedin":    personal.get("linkedin", "https://linkedin.com/in/edrickchang"),
        "github":      personal.get("github", "https://github.com/edrickchang"),
        "portfolio":   personal.get("github", "https://github.com/edrickchang"),
        "website":     personal.get("github", "https://github.com/edrickchang"),
        "personal url": personal.get("github", "https://github.com/edrickchang"),

        # Education
        "school":      personal.get("school", "Santa Clara University"),
        "university":  personal.get("school", "Santa Clara University"),
        "college":     personal.get("school", "Santa Clara University"),
        "institution": personal.get("school", "Santa Clara University"),
        "degree":      personal.get("degree", "Bachelor of Science"),
        "major":       personal.get("major", "Computer Science and Engineering"),
        "discipline":  personal.get("major", "Computer Science and Engineering"),
        "field of study": personal.get("major", "Computer Science and Engineering"),
        "gpa":         str(personal.get("gpa", "3.78")),
        "graduation":  personal.get("graduation_year", "2028"),
        "grad year":   personal.get("graduation_year", "2028"),

        # Work auth
        "authorized":  "Yes",
        "eligible":    "Yes",
        "sponsorship": "No",
        "visa":        "No",
        "relocate":    "Yes",

        # How did you hear
        "hear":        "LinkedIn",
        "referral":    "LinkedIn",
        "source":      "LinkedIn",

        # Demographic defaults (for EEO sections)
        "gender":      personal.get("gender", "Male"),
        "race":        personal.get("race_ethnicity", "Asian"),
        "ethnicity":   personal.get("race_ethnicity", "Asian"),
        "veteran":     personal.get("veteran_status", "I am not a protected veteran"),
        "disability":  personal.get("disability_status", "No, I don't have a disability"),
    }


def _value_for_label(label: str, generic_map: dict) -> Optional[str]:
    """Return the best known value for a form field based on its label."""
    l = label.lower().strip()
    if not l:
        return None

    # Direct map key match (longest match wins)
    best_key = None
    best_len = 0
    for key in generic_map:
        if key in l and len(key) > best_len:
            best_key = key
            best_len = len(key)
    if best_key:
        return generic_map[best_key]

    # Extra fuzzy patterns
    if "first" in l and "name" in l:
        return generic_map["first_name"]
    if "last" in l and "name" in l:
        return generic_map["last_name"]
    if "full" in l and "name" in l:
        return generic_map["full name"]
    if "name" in l and "company" not in l and "school" not in l and "employer" not in l:
        return generic_map["name"]
    if "email" in l or "e-mail" in l:
        return generic_map["email"]
    if "phone" in l or "mobile" in l or "cell" in l:
        return generic_map["phone"]
    if "linkedin" in l:
        return generic_map["linkedin"]
    if "github" in l:
        return generic_map["github"]
    if "portfolio" in l or ("website" in l and "company" not in l):
        return generic_map["portfolio"]
    if "school" in l or "college" in l or "university" in l or "institution" in l:
        return generic_map["school"]
    if "degree" in l or "education level" in l:
        return generic_map["degree"]
    if "major" in l or "field of study" in l or "discipline" in l or "area of study" in l:
        return generic_map["major"]
    if "gpa" in l or "grade point" in l:
        return generic_map["gpa"]
    if "graduation" in l or "grad" in l and "year" in l:
        return generic_map["graduation"]
    if "city" in l and "company" not in l:
        return generic_map["city"]
    if ("state" in l or "province" in l) and "email" not in l:
        return generic_map["state"]
    if "zip" in l or "postal" in l:
        return generic_map["zip"]
    if "country" in l:
        return generic_map["country"]
    if "location" in l:
        return generic_map["location"]
    if "linkedin" in l:
        return generic_map["linkedin"]
    if "authorized" in l or "eligible" in l or "legally" in l or "work in the us" in l:
        return "Yes"
    if "sponsor" in l or "visa" in l:
        return "No"
    if "relocat" in l:
        return "Yes"
    if "hear" in l or "referral" in l or "how did you" in l:
        return "LinkedIn"
    if "gender" in l:
        return generic_map["gender"]
    if "race" in l or "ethnic" in l:
        return generic_map["race"]
    if "veteran" in l:
        return generic_map["veteran"]
    if "disability" in l or "disabled" in l:
        return generic_map["disability"]

    return None


# ──────────────────────────────────────────────────────────────────────────────
# React-select coordinate clicker
# ──────────────────────────────────────────────────────────────────────────────

async def _coord_click_option(page, target: str) -> tuple:
    """Click the best-matching visible option in any open React Select / listbox."""
    try:
        opts_data = await page.evaluate("""() => {
            const result = [];
            for (const ms of ['[class*="select__menu"]', '[role="listbox"]', '[class*="dropdown-menu"]', '[class*="options-list"]']) {
                for (const menu of document.querySelectorAll(ms)) {
                    for (const opt of menu.querySelectorAll('[class*="option"], [role="option"], li, .option')) {
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


# ──────────────────────────────────────────────────────────────────────────────
# Main handler
# ──────────────────────────────────────────────────────────────────────────────

async def handle_generic_apply(
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
    Generic form filler for any job application form.
    Used for Unknown / direct company career sites.

    Returns dict with: filled, failed, submitted, errors
    """
    from applicator.form_filler import _take_screenshot, _load_personal_info

    if personal_info is None:
        personal_info = _load_personal_info()

    generic_map = _build_generic_map(personal_info)
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

    await ev("Generic", "start", f"Generic handler: {company} - {role} (URL: {page.url[:60]})")

    # ── Step 1: Look for and click Apply button ────────────────────────────
    apply_selectors = [
        'a:has-text("Apply Now")', 'button:has-text("Apply Now")',
        'a:has-text("Apply for this Job")', 'button:has-text("Apply for this Job")',
        'a:has-text("Apply")', 'button:has-text("Apply")',
        'a[href*="apply"]', '[data-action="apply"]',
        'input[type="submit"][value*="Apply"]',
        '.apply-btn', '.apply-button', '#apply-now',
    ]
    for sel in apply_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=800):
                href = await btn.get_attribute("href") or ""
                if href and href.startswith("http") and "apply" in href.lower():
                    await page.goto(href, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(2.0)
                elif href and href.startswith("/"):
                    # Relative URL
                    base = page.url.split("/")[0] + "//" + page.url.split("/")[2]
                    await page.goto(base + href, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(2.0)
                else:
                    await btn.click()
                    await asyncio.sleep(2.0)
                await ev("Generic", "info", "Clicked Apply button")
                break
        except Exception:
            continue

    await ss()

    # ── Step 2: Check for iframes and use the one with the form ───────────
    ctx = page
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            field_count = await frame.evaluate("() => document.querySelectorAll('input:not([type=hidden]), textarea, select').length")
            if field_count > 3:
                ctx = frame
                await ev("Generic", "info", f"Using form iframe: {frame.url[:60]}")
                break
        except Exception:
            continue

    # ── Step 3: Resume upload ───────────────────────────────────────────────
    if resume_path and os.path.exists(resume_path):
        resume_uploaded = False
        file_sels = [
            'input[type="file"][name*="resume"]',
            'input[type="file"][id*="resume"]',
            'input[type="file"][name*="cv"]',
            'input[type="file"][accept*="pdf"]',
            'input[type="file"]',
        ]
        for sel in file_sels:
            try:
                fis = ctx.locator(sel)
                count = await fis.count()
                if count > 0:
                    for i in range(count):
                        fi = fis.nth(i)
                        parent_text = await ctx.evaluate(f"""() => {{
                            const inputs = document.querySelectorAll('{sel}');
                            const fi = inputs[{i}];
                            if (!fi) return '';
                            let el = fi;
                            for (let j = 0; j < 5; j++) {{
                                el = el.parentElement;
                                if (!el) break;
                                const t = (el.innerText || el.textContent || '').toLowerCase();
                                if (t.length > 2 && t.length < 200) return t;
                            }}
                            return '';
                        }}""")
                        if "cover" in parent_text and "resume" not in parent_text:
                            continue
                        await fi.set_input_files(resume_path)
                        await asyncio.sleep(2.0)
                        resume_uploaded = True
                        await ev("Generic", "success", f"Resume uploaded")
                        break
                    if resume_uploaded:
                        break
            except Exception:
                continue

        if not resume_uploaded:
            await ev("Generic", "warning", "Resume upload failed")
            errors.append("Resume upload failed")

    await ss()

    # ── Step 4: Fill all visible form fields ────────────────────────────────
    await ev("Generic", "info", "Filling form fields...")

    # Scroll to load all content
    try:
        page_height = await ctx.evaluate("document.body.scrollHeight")
        for scroll_y in range(0, page_height, 600):
            await ctx.evaluate(f"window.scrollTo(0, {scroll_y})")
            await asyncio.sleep(0.3)
        await ctx.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.5)
    except Exception:
        pass

    max_pages = 5
    page_num = 0

    while page_num < max_pages:
        page_num += 1

        # Check for completion
        page_text = ""
        try:
            page_text = await page.evaluate("() => document.body.innerText.toLowerCase()")
        except Exception:
            pass
        if any(kw in page_text for kw in ["application submitted", "thank you for applying", "application received", "successfully submitted"]):
            await ev("Generic", "success", "Application appears submitted")
            break

        # Extract fields
        form_fields = await ctx.evaluate("""() => {
            const fields = [];
            const seen = new Set();

            document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), textarea').forEach(el => {
                if (el.offsetParent === null) return;
                const id = el.id || '';
                const name = el.name || '';
                const sel = id ? '#' + CSS.escape(id) : (name ? '[name="' + name + '"]' : null);
                if (!sel || seen.has(sel)) return;
                seen.add(sel);
                let lbl = '';
                if (id) { const l = document.querySelector('label[for="' + id + '"]'); if (l) lbl = l.innerText.replace('*','').trim(); }
                if (!lbl) {
                    const wrapper = el.closest('.field, .form-group, .question, li, .form-field, [class*="field"], .input-group, tr');
                    if (wrapper) {
                        const l = wrapper.querySelector('label, legend, .field-label, [class*="label"], .form-label, th, td:first-child');
                        if (l) lbl = l.innerText.replace('*','').trim();
                    }
                }
                if (!lbl) lbl = el.placeholder || el.getAttribute('aria-label') || el.title || el.name || '';
                const isPrivacy = (el.name || '').toLowerCase().includes('cookie') || (el.id || '').toLowerCase().includes('cookie');
                if (isPrivacy) return;
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
                    const wrapper = el.closest('.field, .form-group, li, [class*="field"], tr');
                    if (wrapper) { const l = wrapper.querySelector('label, legend, .field-label, th, td:first-child'); if (l) lbl = l.innerText.replace('*','').trim(); }
                }
                const opts = Array.from(el.options).filter(o => o.value).map(o => o.text.trim());
                fields.push({selector: sel, tag: 'select', type: 'select', label: lbl, name: name, value: el.value || '', required: el.required, options: opts});
            });

            return fields;
        }""")

        page_custom_qs = []
        page_filled = 0

        for field in form_fields:
            sel = field.get("selector", "")
            ftype = field.get("type", "text")
            label = field.get("label", "")
            name = field.get("name", "")
            current_val = field.get("value", "")
            opts = field.get("options", [])

            # Skip already-filled
            if current_val and ftype not in ("file",):
                continue

            value = _value_for_label(label or name, generic_map)

            if ftype == "select":
                # Try to match EEO/demographic labels too
                if not value:
                    l = (label + " " + name).lower()
                    if "gender" in l: value = "Male"
                    elif "race" in l or "ethnic" in l: value = "Asian"
                    elif "veteran" in l: value = "I am not a protected veteran"
                    elif "disability" in l: value = "No"
                    elif "hispanic" in l: value = "No"
                    elif "hear" in l or "source" in l: value = "LinkedIn"

                if value and opts:
                    v_lower = value.lower()
                    best = next((o for o in opts if v_lower in o.lower() or o.lower() in v_lower), None)
                    value = best or value

                if value:
                    try:
                        await ctx.select_option(sel, label=value, timeout=3000)
                        page_filled += 1
                    except Exception:
                        try:
                            await ctx.select_option(sel, value=value, timeout=3000)
                            page_filled += 1
                        except Exception:
                            page_custom_qs.append(field)
                else:
                    if field.get("required"):
                        page_custom_qs.append(field)
                continue

            if value:
                try:
                    el = ctx.locator(sel).first
                    await el.click(click_count=3, timeout=3000)
                    await el.fill("", timeout=2000)
                    if any(kw in label.lower() for kw in ("location", "city", "address", "where")):
                        await el.press_sequentially(value, delay=60)
                        await asyncio.sleep(1.8)
                        _dropdown_visible = False
                        try:
                            _dd = ctx.locator(
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
                    errors.append(f"Fill '{label}': {e}")
            else:
                if label and (field.get("required") or ftype == "textarea"):
                    page_custom_qs.append(field)

        # Handle React Select / custom dropdowns
        react_selects = await page.evaluate("""() => {
            const results = [];
            const containers = document.querySelectorAll('[class*="select__container"], [class*="SelectContainer"], [data-select-id]');
            for (let i = 0; i < containers.length; i++) {
                const c = containers[i];
                if (c.offsetParent === null) continue;
                const sv = c.querySelector('[class*="single-value"], [class*="singleValue"]');
                const ph = c.querySelector('[class*="placeholder"]');
                const displayText = sv ? sv.innerText.trim() : (ph ? ph.innerText.trim() : '');
                const isUnfilled = !sv || displayText.toLowerCase().startsWith('select');
                if (!isUnfilled) continue;
                const wrapper = c.closest('.field, .form-group, li, [class*="field"]');
                const lbl = wrapper ? (wrapper.querySelector('label, legend, .field-label')?.innerText?.replace('*','').trim() || '') : '';
                results.push({index: i, label: lbl});
            }
            return results;
        }""")

        for rs_info in react_selects:
            idx = rs_info.get("index", 0)
            label = rs_info.get("label", "")
            value = _value_for_label(label, generic_map)
            if not value:
                l = label.lower()
                if "gender" in l: value = "Male"
                elif "race" in l or "ethnic" in l: value = "Asian"
                elif "veteran" in l: value = "I am not a protected veteran"
                elif "disability" in l: value = "No"
                elif "hispanic" in l: value = "No"
                elif "confirm" in l or "agree" in l or "availab" in l: value = "Yes"
                elif "authorized" in l or "eligible" in l: value = "Yes"
                elif "sponsor" in l: value = "No"
                elif "country" in l: value = "United States"
                elif "state" in l: value = personal_info.get("state", "California")
                else:
                    continue

            try:
                sel_containers = '[class*="select__container"], [class*="SelectContainer"], [data-select-id]'
                container_loc = page.locator(sel_containers).nth(idx)
                await container_loc.scroll_into_view_if_needed(timeout=3000)
                await container_loc.click(timeout=3000)
                await asyncio.sleep(0.8)
                ok, text = await _coord_click_option(page, value)
                if ok:
                    await ev("Generic", "success", f"React-Select '{text[:30]}' for '{label[:40]}'")
                    page_filled += 1
                else:
                    # Try typeahead
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.2)
                    await container_loc.click(timeout=3000)
                    await asyncio.sleep(0.3)
                    await page.keyboard.type(value[:20], delay=60)
                    await asyncio.sleep(1.5)
                    ok2, text2 = await _coord_click_option(page, value)
                    if ok2:
                        await ev("Generic", "success", f"React-Select typeahead '{text2[:30]}' for '{label[:40]}'")
                        page_filled += 1
                    else:
                        await page.keyboard.press("Escape")
            except Exception as e:
                await ev("Generic", "warning", f"React-Select error '{label[:30]}': {str(e)[:60]}")

        # Handle radio buttons
        radio_groups = await page.evaluate("""() => {
            const groups = {};
            document.querySelectorAll('input[type="radio"]').forEach(r => {
                if (r.offsetParent === null) return;
                const name = r.name;
                if (!name) return;
                if (!groups[name]) groups[name] = {name, radios: [], checked: false};
                if (r.checked) groups[name].checked = true;
                const wrapper = r.closest('label, li, div, .radio-option');
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
            "work in the us": "Yes", "work in united states": "Yes",
            "sponsor": "No", "require sponsor": "No", "visa": "No",
            "relocat": "Yes", "felony": "No", "criminal": "No", "18": "Yes",
            "background check": "Yes", "drug test": "Yes",
        }

        for group in radio_groups:
            q_name = group.get("name", "").lower()
            radios = group.get("radios", [])

            # Try to get full question text from ancestor
            try:
                q_text = await page.evaluate(f"""() => {{
                    const r = document.querySelector('[name="{group["name"]}"]');
                    if (!r) return '';
                    let el = r;
                    for (let i = 0; i < 8; i++) {{
                        el = el.parentElement;
                        if (!el || el === document.body) break;
                        const t = el.querySelector('label, legend, h3, h4, p, .question-text');
                        if (t && t.innerText.trim().length > 5) return t.innerText.trim().toLowerCase();
                    }}
                    return '';
                }}""")
            except Exception:
                q_text = q_name

            target = next((v for kw, v in radio_map.items() if kw in q_text or kw in q_name), None)
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

        # Handle checkboxes
        checkboxes = await page.evaluate("""() => {
            const results = [];
            document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                if (cb.checked || cb.offsetParent === null) return;
                if ((cb.name || cb.id || '').toLowerCase().includes('cookie')) return;
                const wrapper = cb.closest('label, li, div, .checkbox-field');
                const text = wrapper ? wrapper.innerText.trim().toLowerCase() : '';
                const shouldCheck = text.includes('agree') || text.includes('acknowledge') || text.includes('certif')
                    || text.includes('understand') || text.includes('confirm') || text.includes('privacy policy')
                    || text.includes('terms') || cb.required;
                if (!shouldCheck) return;
                results.push({
                    selector: cb.id ? '#' + CSS.escape(cb.id) : (cb.name ? '[name="' + cb.name + '"]' : ''),
                    text: text.substring(0, 100),
                });
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

        # Custom questions with LLM
        for field in page_custom_qs:
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
                    # Pick best option based on label
                    if any(kw in l for kw in ["authorized", "eligible", "legally"]):
                        answer = next((o for o in opts if "yes" in o.lower()), opts[0])
                    elif "sponsor" in l or "visa" in l:
                        answer = next((o for o in opts if "no" in o.lower()), opts[0])
                    else:
                        answer = opts[0]
                elif "why" in l or "interest" in l or "tell us" in l or "describe" in l:
                    answer = f"I'm very excited about this {role} opportunity at {company} and believe my CS & Engineering background makes me a strong fit."
                elif "availab" in l or "start" in l:
                    answer = "June 2026"
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
                    await ctx.select_option(sel, label=best, timeout=3000)
                    page_filled += 1
                except Exception:
                    pass
            else:
                try:
                    el = ctx.locator(sel).first
                    await el.click(click_count=3, timeout=3000)
                    await el.fill(answer, timeout=3000)
                    page_filled += 1
                except Exception as e:
                    errors.append(f"Custom '{label}': {e}")

        filled += page_filled
        await ev("Generic", "info", f"Page {page_num}: filled {page_filled} fields")
        await ss()

        # Try to navigate to next page/step
        next_clicked = False
        for next_sel in [
            'button:has-text("Next")', 'button:has-text("Continue")',
            'input[type="submit"][value*="Next"]', 'input[type="submit"][value*="Continue"]',
            'a:has-text("Next")', 'a:has-text("Continue")',
            '[data-testid*="next"]', '[class*="next-btn"]', '.btn-next',
        ]:
            try:
                btn = page.locator(next_sel).first
                if await btn.is_visible(timeout=1000):
                    btn_text = ""
                    try:
                        btn_text = (await btn.inner_text(timeout=1000)).lower()
                    except Exception:
                        try:
                            btn_text = (await btn.get_attribute("value") or "").lower()
                        except Exception:
                            pass
                    if "submit" in btn_text:
                        break
                    await btn.scroll_into_view_if_needed(timeout=2000)
                    await btn.click(timeout=3000)
                    await asyncio.sleep(2.5)
                    next_clicked = True
                    await ev("Generic", "info", "Moved to next step")
                    break
            except Exception:
                continue

        if not next_clicked:
            break

    await ev("Generic", "success",
        f"Generic form filled ({filled} fields). Review in browser before submitting.")

    return {
        "filled":    filled,
        "failed":    failed,
        "submitted": False,
        "errors":    errors,
    }

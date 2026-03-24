"""
Greenhouse-specific application handler.

Greenhouse is a single-page form with well-known IDs. It's much simpler than
Workday — no account creation, no multi-step wizard, no session tokens.

Typical URL patterns:
  boards.greenhouse.io/{board_token}/jobs/{job_id}
  boards.greenhouse.io/embed/job_app?for={company}&token={job_id}
  company.com/jobs?gh_jid={job_id}   ← custom domain with iframe
"""

import asyncio
import os
from typing import Callable, Optional

# ──────────────────────────────────────────────────────────────────────────────
# JS helpers injected into the page
# ──────────────────────────────────────────────────────────────────────────────

# Extract all fillable fields from a Greenhouse form context (page or iframe).
JS_GH_EXTRACT = """
() => {
    const fields = [];
    const seen = new Set();

    function addField(f) {
        if (!f.selector || seen.has(f.selector)) return;
        seen.add(f.selector);
        fields.push(f);
    }

    // ── Standard text / email / tel / url / number inputs ──
    document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), textarea').forEach(el => {
        if (el.offsetParent === null && el.type !== 'email') return;
        const id = el.id || '';
        const name = el.name || '';
        const sel = id ? '#' + CSS.escape(id) : (name ? '[name="' + name + '"]' : null);
        if (!sel) return;
        // Find label
        let labelText = '';
        if (id) {
            const lbl = document.querySelector('label[for="' + id + '"]');
            if (lbl) labelText = lbl.innerText.replace('*','').trim();
        }
        if (!labelText) {
            const wrapper = el.closest('li, .field, .form-group, .question');
            const lbl = wrapper ? wrapper.querySelector('label') : null;
            if (lbl) labelText = lbl.innerText.replace('*','').trim();
        }
        addField({
            selector: sel, tag: el.tagName.toLowerCase(), type: el.type || 'text',
            name: name || id, label: labelText, value: el.value || '',
            required: el.required || el.getAttribute('aria-required') === 'true',
            placeholder: el.placeholder || '',
        });
    });

    // ── Native <select> dropdowns ──
    document.querySelectorAll('select').forEach(el => {
        if (el.offsetParent === null) return;
        const id = el.id || ''; const name = el.name || '';
        const sel = id ? '#' + CSS.escape(id) : (name ? '[name="' + name + '"]' : null);
        if (!sel) return;
        let labelText = '';
        const lbl = id ? document.querySelector('label[for="' + id + '"]') : null;
        if (lbl) labelText = lbl.innerText.replace('*','').trim();
        const opts = Array.from(el.options).filter(o => o.value).map(o => o.text.trim());
        addField({
            selector: sel, tag: 'select', type: 'select',
            name: name || id, label: labelText, value: el.value || '',
            required: el.required, placeholder: '', options: opts,
        });
    });

    // ── React-select / custom select__container dropdowns ──
    document.querySelectorAll('[class*="select__container"], [class*="select__control"]').forEach(el => {
        if (el.offsetParent === null) return;
        const container = el.closest('[class*="select__container"]') || el;
        if (seen.has('__rc__' + container.className)) return;
        seen.add('__rc__' + container.className);
        const wrapper = container.closest('li, .field, .form-group, .question, [class*="application-question"]') || container.parentElement;
        const lbl = wrapper ? wrapper.querySelector('label') : null;
        const lblText = lbl ? lbl.innerText.replace('*','').trim() : '';
        const singleVal = container.querySelector('[class*="single-value"], [class*="singleValue"]');
        const ph = container.querySelector('[class*="placeholder"]');
        const displayText = singleVal ? singleVal.innerText.trim() : (ph ? ph.innerText.trim() : '');
        const isPlaceholder = !singleVal || displayText.toLowerCase().startsWith('select');
        const allRC = Array.from(document.querySelectorAll('[class*="select__container"]'));
        const idx = allRC.indexOf(container);
        const sel = '[class*="select__container"]:nth-of-type(' + (idx + 1) + ')';
        addField({
            selector: sel, tag: 'div', type: 'react-select',
            name: lblText.toLowerCase().replace(/[^a-z0-9]/g, '_').substring(0, 50) || ('rc_' + idx),
            label: lblText, value: isPlaceholder ? '' : displayText,
            required: wrapper ? (wrapper.querySelector('[aria-required="true"]') !== null || (lbl && lbl.innerText.includes('*'))) : false,
            placeholder: ph ? ph.innerText.trim() : 'Select...', options: [],
        });
    });

    // ── Checkboxes / radios ──
    document.querySelectorAll('input[type="checkbox"], input[type="radio"]').forEach(el => {
        if (el.offsetParent === null) return;
        const wrapper = el.closest('li, .field, .form-group, .question, [class*="application-question"]');
        const lbl = el.id ? document.querySelector('label[for="' + el.id + '"]') : (wrapper ? wrapper.querySelector('label') : null);
        const lblText = lbl ? lbl.innerText.replace('*','').trim() : '';
        const sel = el.id ? '#' + CSS.escape(el.id) : null;
        if (!sel) return;
        addField({
            selector: sel, tag: 'input', type: el.type,
            name: el.name || el.id, label: lblText, value: el.value || '',
            required: el.required, placeholder: '', options: [],
        });
    });

    // ── File inputs ──
    document.querySelectorAll('input[type="file"]').forEach(el => {
        if (el.id) {
            const lbl = document.querySelector('label[for="' + el.id + '"]');
            addField({
                selector: '#' + CSS.escape(el.id), tag: 'input', type: 'file',
                name: el.name || el.id, label: lbl ? lbl.innerText.trim() : el.id,
                value: '', required: el.required, placeholder: '', options: [],
            });
        }
    });

    return fields;
}
"""

# Open a react-select dropdown and pick an option by text.
JS_GH_REACT_SELECT = """
(args) => {
    const { selector, value } = args;
    const container = document.querySelector(selector);
    if (!container) return { ok: false, error: 'container not found' };
    const control = container.querySelector('[class*="select__control"], [class*="selectControl"]');
    if (!control) return { ok: false, error: 'control not found' };
    control.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
    control.click();
    return { ok: true };
}
"""

JS_GH_PICK_OPTION = """
(args) => {
    const { value } = args;
    const options = document.querySelectorAll('[class*="select__option"], [class*="selectOption"]');
    if (!options.length) return { ok: false, error: 'no options visible (typeahead not loaded yet?)' };
    const lower = value.toLowerCase();
    const words = lower.split(/\\s+and\\s+|\\s*&\\s*/);
    const firstPart = words[0].trim();
    // 1. Exact substring match on full value
    for (const opt of options) {
        if (opt.innerText.toLowerCase().includes(lower)) {
            opt.click();
            return { ok: true, picked: opt.innerText.trim() };
        }
    }
    // 2. Match on first part before "and"
    for (const opt of options) {
        if (opt.innerText.toLowerCase().includes(firstPart)) {
            opt.click();
            return { ok: true, picked: opt.innerText.trim(), fallback: true };
        }
    }
    // 3. Pick first option as last resort
    options[0].click();
    return { ok: true, picked: options[0].innerText.trim(), fallback: true };
}
"""


# ──────────────────────────────────────────────────────────────────────────────
# Static field → candidate value map  (known IDs / names)
# ──────────────────────────────────────────────────────────────────────────────

def _build_known_map(personal: dict) -> dict:
    """Map Greenhouse field ids/names → candidate values."""
    grad_month = "6"   # June
    grad_year  = personal.get("graduation_year", "2028")
    return {
        # ── Contact ──
        "first_name":           personal.get("first_name", "Edrick"),
        "last_name":            personal.get("last_name", "Chang"),
        "email":                personal.get("email", "eachang@scu.edu"),
        "phone":                personal.get("phone", "4088066495"),

        # ── Location ──
        "location":             personal.get("city", "Santa Clara, CA"),
        "city":                 personal.get("city", "Santa Clara"),
        "state":                personal.get("state", "California"),
        "country":              "United States",

        # ── Profiles ──
        "linkedin_profile":     personal.get("linkedin", "https://linkedin.com/in/edrickchang"),
        "linkedin":             personal.get("linkedin", "https://linkedin.com/in/edrickchang"),
        "website":              personal.get("github", "https://github.com/edrickchang"),
        "github":               personal.get("github", "https://github.com/edrickchang"),
        "portfolio":            personal.get("github", "https://github.com/edrickchang"),

        # ── Education (Greenhouse education section) ──
        "school_name":          personal.get("school", "Santa Clara University"),
        "school_name_0":        personal.get("school", "Santa Clara University"),
        "degree":               "Bachelor of Science",
        "discipline":           personal.get("major", "Computer Science and Engineering"),
        "start_year":           "2024",
        "end_year":             grad_year,
        "end_month":            grad_month,
        "gpa":                  str(personal.get("gpa", "3.78")),

        # ── Demographic / EEOC (safe defaults) ──
        "gender":               "Male",
        "race":                 "Asian",
        "veteran_status":       "I am not a protected veteran",
        "disability_status":    "No, I don't have a disability",

        # ── Common custom questions ──
        "authorized":           "Yes",
        "sponsorship":          "No",
        "referral":             "Job Board",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Label → value fuzzy matching
# ──────────────────────────────────────────────────────────────────────────────

def _value_for_label(label: str, known: dict, personal: dict) -> Optional[str]:
    """Return a value for a Greenhouse field based on its label text."""
    l = label.lower().strip()
    if not l:
        return None

    # Direct key hits
    for k, v in known.items():
        if k.replace("_", " ") in l or l.startswith(k.replace("_", " ")):
            return v

    # Fuzzy label matching
    if any(x in l for x in ("first name", "given name")):
        return known["first_name"]
    if any(x in l for x in ("last name", "surname", "family name")):
        return known["last_name"]
    if "email" in l:
        return known["email"]
    if "phone" in l or "mobile" in l or "tel" in l:
        return known["phone"]
    if "linkedin" in l:
        return known["linkedin"]
    if "github" in l:
        return known["github"]
    if "website" in l or "portfolio" in l or "url" in l:
        return known["website"]
    if "school" in l or "university" in l or "college" in l or "institution" in l:
        return known["school_name"]
    if "degree" in l:
        return known["degree"]
    if "major" in l or "discipline" in l or "field of study" in l:
        return known["discipline"]
    if "gpa" in l:
        return known["gpa"]
    if "graduation" in l and "year" in l:
        return known["end_year"]
    if "graduation" in l and "month" in l:
        return "June"
    if "authorized" in l or "eligible to work" in l or "work authorization" in l:
        return "Yes"
    if "sponsor" in l or "visa" in l:
        return "No"
    if "gender" in l:
        return known["gender"]
    if "race" in l or "ethnic" in l:
        return known["race"]
    if "veteran" in l:
        return known["veteran_status"]
    if "disability" in l or "disabled" in l:
        return known["disability_status"]
    if "referral" in l or "hear about" in l or "source" in l or "how did you" in l:
        return known["referral"]
    if "city" in l or "location" in l:
        return known["city"]
    if "state" in l:
        return known["state"]
    if "country" in l:
        return known["country"]
    if "name" in l:
        return f"{known['first_name']} {known['last_name']}"

    return None


# ──────────────────────────────────────────────────────────────────────────────
# React-select helper
# ──────────────────────────────────────────────────────────────────────────────

async def _fill_react_select(ctx, selector: str, value: str, event_cb=None) -> bool:
    """Click a React-select dropdown, pick the closest option.

    Strategy:
    1. Click to open the dropdown.
    2. Check if options are immediately visible (static dropdown, no typeahead).
       If yes, pick best match without typing — avoids "No options" from long search terms.
    3. If no options visible yet, type a short keyword to trigger typeahead, then pick.
    """
    try:
        await ctx.evaluate(JS_GH_REACT_SELECT, {"selector": selector, "value": value})
        await asyncio.sleep(0.5)

        # Step 2: Check if options are already visible WITHOUT typing
        immediate = await ctx.evaluate(JS_GH_PICK_OPTION, {"value": value})
        if immediate.get("ok"):
            if event_cb and immediate.get("fallback"):
                await event_cb("Fill Form", "warning",
                    f"React-select (immediate) fallback for '{value[:30]}' → '{immediate.get('picked')}'")
            return True

        # No options yet — could be a typeahead. Type a short keyword.
        # Use only the FIRST meaningful word (skip stopwords) to avoid "No options"
        stopwords = {"i", "a", "an", "the", "is", "am", "are", "not", "have", "has",
                     "no", "yes", "do", "does", "will", "would", "can", "cannot"}
        words = [w for w in value.lower().split() if w.isalpha() and w not in stopwords]
        search_term = words[0][:12] if words else value.split()[0][:8]

        try:
            input_sel = f"{selector} input"
            inp = ctx.locator(input_sel).first
            if await inp.is_visible(timeout=600):
                await inp.fill(search_term)
        except Exception:
            try:
                await ctx.keyboard.type(search_term, delay=50)
            except Exception:
                pass
        await asyncio.sleep(1.0)

        result = await ctx.evaluate(JS_GH_PICK_OPTION, {"value": value})
        await asyncio.sleep(0.4)
        if result.get("ok"):
            if event_cb and result.get("fallback"):
                await event_cb("Fill Form", "warning",
                    f"React-select fallback for '{value[:30]}' → picked '{result.get('picked')}'")
            return True
        if event_cb:
            await event_cb("Fill Form", "warning",
                f"React-select: {result.get('error')} (selector: {selector[:50]})")
        return False
    except Exception as e:
        if event_cb:
            await event_cb("Fill Form", "warning", f"React-select error: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Main handler
# ──────────────────────────────────────────────────────────────────────────────

async def handle_greenhouse_apply(
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
    Fill and optionally submit a Greenhouse application form.

    Returns dict with keys:
      filled       int   number of fields successfully filled
      failed       int   number of fields that errored
      submitted    bool  whether the form was submitted
      errors       list  error message strings
    """
    from applicator.form_filler import _take_screenshot, _load_personal_info

    if personal_info is None:
        personal_info = _load_personal_info()

    known = _build_known_map(personal_info)
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

    # ── Step 1: Click Apply button if on job listing page ──────────────────
    await ev("Greenhouse", "start", f"Starting Greenhouse handler for {company} - {role}")

    apply_selectors = [
        'a:has-text("Apply for this job")',
        'button:has-text("Apply for this job")',
        'a:has-text("Apply Now")',
        'button:has-text("Apply Now")',
        'a:has-text("Apply")',
        'button:has-text("Apply")',
        '[data-qa="btn-apply"]',
        '.postings-btn',
        'a.postings-btn',
    ]
    for sel in apply_selectors:
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
                await ev("Greenhouse", "info", "Clicked Apply button")
                break
        except Exception:
            continue

    await ss()

    # ── Step 2: Detect iframe (Greenhouse embeds) ──────────────────────────
    ctx = page   # default: main page
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        frame_url = frame.url or ""
        if "greenhouse" in frame_url or "embed/job_app" in frame_url:
            try:
                has_form = await frame.evaluate("() => !!document.querySelector('#application-form, #app_body, [data-gh]')")
                if has_form:
                    ctx = frame
                    await ev("Greenhouse", "info", f"Using iframe context: {frame_url[:60]}")
                    break
            except Exception:
                continue

    # Also check for any iframe with a Greenhouse form
    if ctx == page:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                n = await frame.evaluate("() => document.querySelectorAll('input, textarea, select').length")
                if n > 3:
                    ctx = frame
                    await ev("Greenhouse", "info", f"Found form in iframe: {frame.url[:60]}")
                    break
            except Exception:
                continue

    # ── Step 3: Upload resume ───────────────────────────────────────────────
    if resume_path and os.path.exists(resume_path):
        resume_uploaded = False
        resume_basename = os.path.basename(resume_path)

        # Strategy A: set_input_files directly on hidden file input
        # Note: 'input[type="file"]#resume' is invalid CSS; use '#resume' or proper selectors
        for file_sel in [
            '#resume',                                    # Greenhouse well-known ID
            'input[type="file"][name="resume"]',
            'input[type="file"][id*="resume"]',
            'input[type="file"][accept*="pdf"]',
            'input[type="file"]',
        ]:
            try:
                file_input = ctx.locator(file_sel).first
                if await file_input.count() == 0:
                    continue
                # Unhide if needed, then set files
                try:
                    await ctx.evaluate(f"""() => {{
                        const el = document.querySelector('{file_sel}');
                        if (el) {{ el.style.display = 'block'; el.style.opacity = '1'; }}
                    }}""")
                except Exception:
                    pass
                await file_input.set_input_files(resume_path)
                await asyncio.sleep(1.5)
                # Verify the file actually registered (check .files property or page text)
                try:
                    verified = await ctx.evaluate(f"""() => {{
                        const fi = document.querySelector('{file_sel}');
                        if (fi && fi.files && fi.files.length > 0) return true;
                        return document.body.innerText.includes('{resume_basename}');
                    }}""")
                except Exception:
                    verified = True  # assume success if we can't verify
                if verified:
                    resume_uploaded = True
                    await ev("Greenhouse", "success", f"Resume uploaded (direct): {resume_basename}")
                    break
                else:
                    await ev("Greenhouse", "info",
                             "Strategy A: file set but not registered — trying Strategy B")
            except Exception:
                continue

        # Strategy B: intercept the file-chooser dialog via button click
        if not resume_uploaded:
            for btn_sel in [
                'button:has-text("Upload")', 'a:has-text("Upload")',
                'button:has-text("Attach")', 'a:has-text("Attach")',
                '[class*="resume"]', '[data-qa*="resume"]',
            ]:
                try:
                    btn = ctx.locator(btn_sel).first
                    if await btn.count() == 0:
                        continue
                    async with page.expect_file_chooser(timeout=4000) as fc_info:
                        await btn.click(timeout=3000)
                    fc = await fc_info.value
                    await fc.set_files(resume_path)
                    await asyncio.sleep(1.5)
                    resume_uploaded = True
                    await ev("Greenhouse", "success",
                             f"Resume uploaded (file-chooser): {resume_basename}")
                    break
                except Exception:
                    continue

        if not resume_uploaded:
            await ev("Greenhouse", "warning", "Could not upload resume — file input not found")
            errors.append("Resume upload failed")
    else:
        await ev("Greenhouse", "warning", f"Resume not found at: {resume_path}")

    await ss()

    # ── Step 4: Fill known static fields ──────────────────────────────────
    await ev("Greenhouse", "info", "Filling standard fields...")

    # Direct known-ID fills
    direct_fills = [
        ("#first_name",     known["first_name"]),
        ("#last_name",      known["last_name"]),
        ("#email",          known["email"]),
        ("#phone",          known["phone"]),
        ("#linkedin_profile", known["linkedin"]),
        ("#website",        known["website"]),
        ("#location",       known["location"]),
    ]
    for sel, val in direct_fills:
        try:
            el = ctx.locator(sel).first
            if await el.is_visible(timeout=800):
                await el.click(click_count=3)
                await el.fill(val)
                filled += 1
        except Exception:
            pass  # Field might not exist on this form

    # ── Step 5: Extract all remaining fields and fill generically ──────────
    try:
        fields = await ctx.evaluate(JS_GH_EXTRACT)
        await ev("Greenhouse", "info", f"Found {len(fields)} fields to process")
    except Exception as e:
        await ev("Greenhouse", "error", f"Field extraction failed: {e}")
        fields = []

    # Build a list of custom questions that need LLM-generated answers
    custom_q_fields = []

    for field in fields:
        sel = field.get("selector", "")
        ftype = field.get("type", "text")
        label = field.get("label", "")
        fname = field.get("name", "")
        opts = field.get("options", [])
        current_val = field.get("value", "")

        # Skip if already filled
        if current_val and ftype not in ("file",):
            continue

        # Skip hidden / already-handled IDs
        if sel in ("#first_name", "#last_name", "#email", "#phone",
                   "#linkedin_profile", "#website", "#location"):
            continue

        # ── File inputs ──
        if ftype == "file":
            fname_lower = (label + fname).lower()
            upload_path = resume_path
            if "cover" in fname_lower and resume_path:
                upload_path = None  # Don't upload resume as cover letter
            if upload_path and os.path.exists(upload_path):
                try:
                    fi = ctx.locator(sel).first
                    await fi.set_input_files(upload_path)
                    await asyncio.sleep(1.0)
                    filled += 1
                except Exception as e:
                    failed += 1
                    errors.append(f"File upload {sel}: {e}")
            continue

        # ── React-select dropdowns ──
        if ftype == "react-select":
            val = _value_for_label(label, known, personal_info)
            if val and opts:
                # Try to match val to one of the available options
                val_lower = val.lower()
                best = next((o for o in opts if val_lower in o.lower() or o.lower() in val_lower), None)
                val = best or val
            if val:
                ok = await _fill_react_select(ctx, sel, val, event_callback)
                if ok:
                    filled += 1
                else:
                    failed += 1
                    errors.append(f"React-select failed: {label}")
            else:
                # Will need LLM answer
                if opts:
                    custom_q_fields.append(field)
            continue

        # ── Native select ──
        if ftype == "select":
            val = _value_for_label(label, known, personal_info)
            if val and opts:
                best = next((o for o in opts if val.lower() in o.lower() or o.lower() in val.lower()), None)
                val = best or val
            if val:
                try:
                    await ctx.select_option(sel, label=val)
                    filled += 1
                except Exception:
                    try:
                        await ctx.select_option(sel, value=val)
                        filled += 1
                    except Exception as e:
                        failed += 1
                        errors.append(f"Select {label}: {e}")
            else:
                custom_q_fields.append(field)
            continue

        # ── Checkboxes ──
        if ftype == "checkbox":
            try:
                el = ctx.locator(sel).first
                if not await el.is_checked():
                    await el.check()
                    filled += 1
            except Exception:
                pass
            continue

        # ── Text / textarea ──
        if ftype in ("text", "email", "tel", "url", "textarea", "number", "hidden"):
            val = _value_for_label(label, known, personal_info)
            if val:
                try:
                    el = ctx.locator(sel).first
                    await el.click(click_count=3)
                    await el.fill(val)
                    filled += 1
                except Exception as e:
                    failed += 1
                    errors.append(f"Text {label}: {e}")
            else:
                # Needs LLM answer
                if label and field.get("required"):
                    custom_q_fields.append(field)
            continue

    await ss()

    # ── Step 6: Generate answers for custom questions ──────────────────────
    if custom_q_fields:
        await ev("Greenhouse", "info", f"Generating answers for {len(custom_q_fields)} custom question(s)...")

        for field in custom_q_fields:
            sel = field.get("selector", "")
            ftype = field.get("type", "text")
            label = field.get("label", "")
            opts = field.get("options", [])

            answer = None

            if generate_answer_fn:
                try:
                    # generate_field_answer(question, company, role, job_description)
                    answer = await asyncio.to_thread(
                        generate_answer_fn,
                        label,           # question
                        company,
                        role,
                        job_description,
                    )
                except Exception as e:
                    await ev("Greenhouse", "warning", f"LLM answer failed for '{label}': {e}")

            if not answer:
                # Fallback heuristics
                l = label.lower()
                if opts:
                    answer = opts[0]
                elif "why" in l or "tell us" in l or "describe" in l or "explain" in l:
                    answer = (
                        f"I'm excited about this {role} opportunity at {company}. "
                        "My experience in software engineering and coursework in CS aligns well "
                        "with this role, and I'm eager to contribute to your team."
                    )
                else:
                    answer = "N/A"

            # Fill the answer
            if ftype in ("react-select",):
                ok = await _fill_react_select(ctx, sel, answer, event_callback)
                if ok:
                    filled += 1
                else:
                    failed += 1
                    errors.append(f"Custom react-select: {label}")
            elif ftype == "select":
                try:
                    await ctx.select_option(sel, label=answer)
                    filled += 1
                except Exception as e:
                    failed += 1
                    errors.append(f"Custom select {label}: {e}")
            else:
                try:
                    el = ctx.locator(sel).first
                    await el.click(click_count=3)
                    await el.fill(answer)
                    filled += 1
                except Exception as e:
                    failed += 1
                    errors.append(f"Custom text {label}: {e}")

    # ── Step 7: Handle Education section ──────────────────────────────────
    # Greenhouse education section uses specific selectors
    await _fill_education_section(ctx, known, ev)

    await ss()

    # ── Step 8: Handle EEOC/Demographic section ────────────────────────────
    await _fill_eeoc_section(ctx, known, ev)

    await ss()

    # ── Step 9: Final scan for any remaining empty required fields ─────────
    await ev("Greenhouse", "info", f"Fields filled: {filled}, failed: {failed}")
    if errors:
        await ev("Greenhouse", "warning", "Errors: " + "; ".join(errors[:3]))

    # ── Step 10: Submit ────────────────────────────────────────────────────
    submitted = False
    try:
        submit_btn = ctx.locator('#submit_app, button[type="submit"], input[type="submit"]').first
        if await submit_btn.is_visible(timeout=3000):
            await ev("Greenhouse", "info", "Form ready — submit when reviewed")
            # NOTE: We do NOT auto-submit. The dashboard keeps the browser open
            # for human review. Set submitted=True only when user confirms.
    except Exception:
        pass

    await ev("Greenhouse", "success",
        f"Greenhouse form filled ({filled} fields). Review in browser before submitting.")

    return {
        "filled": filled,
        "failed": failed,
        "submitted": submitted,
        "errors": errors,
    }


async def _fill_education_section(ctx, known: dict, ev):
    """Fill the Greenhouse education section (school, degree, discipline, dates)."""
    edu_map = {
        '[name*="school_name"], [id*="school_name"]':   known["school_name"],
        '[name*="degree"], [id*="degree_0"]':            known["degree"],
        '[name*="discipline"], [id*="discipline_0"]':    known["discipline"],
        '[name*="start_year"], [id*="start_year_0"]':    "2024",
        '[name*="end_year"], [id*="end_year_0"]':        known["end_year"],
        '[name*="gpa"], [id*="gpa_0"]':                 known["gpa"],
    }
    for sel, val in edu_map.items():
        try:
            el = ctx.locator(sel).first
            if await el.is_visible(timeout=600):
                tag = await el.evaluate("e => e.tagName")
                if tag == "SELECT":
                    await ctx.select_option(sel, label=val)
                else:
                    await el.click(click_count=3)
                    await el.fill(val)
        except Exception:
            pass

    # End month dropdown (June = 6)
    for month_sel in ['[id*="end_month_0"]', '[name*="end_month"]', 'select[id*="month"]']:
        try:
            el = ctx.locator(month_sel).first
            if await el.is_visible(timeout=600):
                await ctx.select_option(month_sel, label="June")
                break
        except Exception:
            continue


async def _fill_eeoc_section(ctx, known: dict, ev):
    """Fill EEOC/demographic section using label-based detection.

    Greenhouse dynamically generates element IDs (e.g. h29784) so we cannot
    rely on [id*="race"] or [name*="race"] selectors.  Instead we scan all
    <select> and radio-group elements, read their labels, and match by label
    text keywords.
    """
    JS_FIND_EEOC = """
    () => {
        const results = [];

        // ── <select> elements ──────────────────────────────────────────────
        document.querySelectorAll('select').forEach(el => {
            if (el.offsetParent === null) return;
            let labelText = '';
            if (el.id) {
                const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                if (lbl) labelText = lbl.innerText.replace('*', '').trim();
            }
            if (!labelText) {
                const wrapper = el.closest('li, .field, .form-group, .question, [class*="question"]');
                if (wrapper) {
                    const lbl = wrapper.querySelector('label');
                    if (lbl) labelText = lbl.innerText.replace('*', '').trim();
                }
            }
            if (!labelText) return;
            const opts = Array.from(el.options).filter(o => o.value).map(o => o.text.trim());
            if (!opts.length) return;
            const sel = el.id
                ? '#' + CSS.escape(el.id)
                : (el.name ? '[name="' + el.name + '"]' : null);
            if (!sel) return;
            results.push({ type: 'select', label: labelText, selector: sel, options: opts });
        });

        // ── Radio groups ───────────────────────────────────────────────────
        const radioGroups = {};
        document.querySelectorAll('input[type="radio"]').forEach(el => {
            if (el.offsetParent === null) return;
            const name = el.name;
            if (!name) return;
            if (!radioGroups[name]) radioGroups[name] = { name, options: [], labelText: '' };
            const lbl = el.id
                ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]')
                : null;
            const optText = lbl ? lbl.innerText.replace('*','').trim() : el.value;
            radioGroups[name].options.push({ value: el.value, text: optText, id: el.id });
        });
        Object.values(radioGroups).forEach(group => {
            const firstEl = document.querySelector(
                'input[type="radio"][name="' + group.name + '"]');
            if (!firstEl) return;
            const fieldset = firstEl.closest('fieldset');
            const legend = fieldset ? fieldset.querySelector('legend') : null;
            if (legend) group.labelText = legend.innerText.replace('*','').trim();
            if (!group.labelText) {
                const wrapper = firstEl.closest(
                    'li, .field, .form-group, .question, [class*="question"]');
                if (wrapper) {
                    const lbl = wrapper.querySelector('label:not([for])') ||
                                wrapper.querySelector('label');
                    if (lbl) group.labelText = lbl.innerText.replace('*','').trim();
                }
            }
            if (group.labelText) {
                results.push({
                    type: 'radio',
                    label: group.labelText,
                    name: group.name,
                    options: group.options,
                });
            }
        });

        return results;
    }
    """

    # keyword groups → desired value
    eeoc_patterns = [
        (["gender", "sex"],                         known["gender"]),
        (["race", "ethnic", "national origin"],     known["race"]),
        (["veteran", "military", "protected vet"],  known["veteran_status"]),
        (["disability", "disabled", "accommodat"],  known["disability_status"]),
    ]

    try:
        fields = await ctx.evaluate(JS_FIND_EEOC)
    except Exception as e:
        await ev("EEOC", "warning", f"EEOC field scan failed: {e}")
        return

    for field in fields:
        label_lower = field.get("label", "").lower()
        matched_val = None
        for keywords, val in eeoc_patterns:
            if any(kw in label_lower for kw in keywords):
                matched_val = val
                break
        if not matched_val:
            continue

        ftype = field.get("type")

        if ftype == "select":
            sel  = field["selector"]
            opts = field["options"]
            val_lower = matched_val.lower()
            # Try progressively looser matches
            best = next((o for o in opts if val_lower in o.lower()), None)
            if not best:
                best = next((o for o in opts if val_lower[:6] in o.lower()), None)
            if not best:
                best = next((o for o in opts
                             if any(w in o.lower()
                                    for w in val_lower.split()[:3]
                                    if len(w) > 2)), None)
            if best:
                try:
                    await ctx.select_option(sel, label=best)
                    await ev("EEOC", "success",
                             f"Set '{field['label']}' → '{best}'")
                except Exception as e:
                    await ev("EEOC", "warning",
                             f"EEOC select failed for '{field['label']}': {e}")
            else:
                await ev("EEOC", "warning",
                         f"No option match for '{field['label']}' (want: {matched_val})")

        elif ftype == "radio":
            opts = field["options"]
            val_lower = matched_val.lower()
            best_opt = next((o for o in opts if val_lower in o["text"].lower()), None)
            if not best_opt:
                best_opt = next((o for o in opts if val_lower[:6] in o["text"].lower()), None)
            if not best_opt:
                best_opt = next((o for o in opts
                                 if any(w in o["text"].lower()
                                        for w in val_lower.split()[:3]
                                        if len(w) > 2)), None)
            if best_opt and best_opt.get("id"):
                try:
                    radio = ctx.locator(f'#{best_opt["id"]}').first
                    if await radio.is_visible(timeout=500):
                        await radio.check()
                        await ev("EEOC", "success",
                                 f"Set radio '{field['label']}' → '{best_opt['text']}'")
                except Exception as e:
                    await ev("EEOC", "warning",
                             f"EEOC radio failed for '{field['label']}': {e}")

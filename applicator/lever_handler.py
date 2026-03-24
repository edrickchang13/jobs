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
        # Location — use full "City, State, Country" so Lever autocomplete matches
        "location":        personal.get("location", "Santa Clara, CA, USA"),
        "city":            personal.get("city", "Santa Clara"),
        "current location": personal.get("location", "Santa Clara, CA, USA"),
        # Links
        "linkedin":        personal.get("linkedin", "https://linkedin.com/in/edrickchang"),
        "github":          personal.get("github", "https://github.com/edrickchang"),
        "portfolio":       personal.get("github", "https://github.com/edrickchang"),
        "website":         personal.get("github", "https://github.com/edrickchang"),
        "twitter":         "",
        # Education / org
        "school":          personal.get("school", "Santa Clara University"),
        "university":      personal.get("school", "Santa Clara University"),
        "degree":          personal.get("degree", "Bachelor of Science"),
        "major":           personal.get("major", "Computer Science and Engineering"),
        "gpa":             str(personal.get("gpa", "3.78")),
        "graduation year": personal.get("graduation_year", "2028"),
        # Company / org field (student — no current employer)
        "company":         personal.get("current_company", ""),
        "employer":        personal.get("current_company", ""),
        "organization":    personal.get("current_company", ""),
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
    "sex":         "Male",
    "race":        "Asian",
    "ethnicity":   "Asian",
    "hispanic":    "No",
    "veteran":     "I am not a protected veteran",
    "disability":  "No, I don't have a disability",
    "age":         "20-29",   # Edrick is ~20-21 in 2026 (HS grad 2024)
}

# For checkbox-based ethnicity — ONLY check boxes whose text matches one of these
EEO_ETHNICITY_MATCH = ["asian"]

# For "prefer not to say" / decline fallback option keywords
_DECLINE_KEYWORDS = ["prefer not", "decline", "not wish", "no answer", "do not wish"]


def _best_eeo_option(target: str, opts: list) -> dict | None:
    """Find the best matching option dict from a list of {t, v, i} dicts."""
    t = target.lower()
    # Exact match
    for o in opts:
        if o["t"].lower() == t:
            return o
    # Prefix / substring match
    for o in opts:
        ot = o["t"].lower()
        if t[:6] in ot or ot[:6] in t:
            return o
    # Word overlap (≥1 meaningful word)
    t_words = set(w for w in t.split() if len(w) > 3)
    for o in opts:
        ot_words = set(w for w in o["t"].lower().split() if len(w) > 3)
        if t_words & ot_words:
            return o
    return None


async def _fill_lever_eeo(page, ev):
    """Fill Lever EEO section — handles <select>, radio, and checkbox variants."""

    # ── 1. <select> dropdowns ─────────────────────────────────────────────────
    eeo_selects = await page.evaluate("""() => {
        const results = [];
        for (const s of document.querySelectorAll('select')) {
            if (s.offsetParent === null) continue;
            const cur = s.options[s.selectedIndex]?.text?.trim().toLowerCase() || '';
            const isPlaceholder = ['select...','select','choose','--','','prefer not to answer'].includes(cur);
            if (!isPlaceholder) continue;   // already filled — skip
            // Get label — try parent label text nodes first (Lever wraps select in <label>)
            let lbl = '';
            const parentLbl = s.closest('label');
            if (parentLbl) {
                lbl = Array.from(parentLbl.childNodes)
                    .filter(n => n.nodeType === 3)
                    .map(n => n.textContent.trim())
                    .filter(t => t && t !== '✱' && t !== '*')
                    .join(' ').trim();
            }
            if (!lbl) {
                const wrapper = s.closest('.application-question,.application-field,.lever-field,.field,li');
                lbl = wrapper?.querySelector('label:not(:has(select)),.field-label,legend')?.innerText?.replace('*','').replace('✱','').trim() || s.name || '';
            }
            const opts = Array.from(s.options).map(o => ({v:o.value, t:o.text.trim(), i:o.index}));
            const sel = s.id ? '#'+CSS.escape(s.id) : (s.name ? 'select[name="'+s.name+'"]' : '');
            if (sel) results.push({selector:sel, label:lbl, options:opts});
        }
        return results;
    }""")

    for si in eeo_selects:
        selector = si.get("selector", "")
        label = si.get("label", "").lower()
        if not selector:
            continue
        target = None
        for kw, val in EEO_LEVER_MAP.items():
            if kw in label:
                target = val
                break
        if not target:
            continue
        best = _best_eeo_option(target, si.get("options", []))
        if not best:
            continue
        try:
            loc = page.locator(selector).first
            await loc.scroll_into_view_if_needed(timeout=3000)
            await loc.select_option(index=best["i"], timeout=3000)
            await page.evaluate(f"document.querySelector('{selector}')?.dispatchEvent(new Event('change',{{bubbles:true}}))")
            await ev("Lever EEO", "success", f"Select: '{target}' → '{si['label'][:40]}'")
        except Exception as e:
            await ev("Lever EEO", "warning", f"EEO select failed '{si['label'][:30]}': {e}")

    # ── 2. Radio button EEO groups (gender, age, veteran, disability) ─────────
    radio_eeo_groups = await page.evaluate("""() => {
        const groups = {};
        for (const r of document.querySelectorAll('input[type="radio"]')) {
            if (r.offsetParent === null) continue;
            if (r.checked) continue;  // already selected
            const name = r.name || r.id;
            if (!groups[name]) groups[name] = {name, radios: []};
            const wrapper = r.closest('label,li,.radio-option,div');
            const text = wrapper ? wrapper.innerText.trim() : r.value;
            const sel = r.id ? '#'+CSS.escape(r.id) : '[name="'+name+'"][value="'+r.value+'"]';
            groups[name].radios.push({selector: sel, text: text, value: r.value});
        }
        // Only keep groups where question text signals EEO
        const result = [];
        for (const g of Object.values(groups)) {
            const first = document.querySelector('input[name="'+g.name+'"]');
            if (!first) continue;
            let qText = '';
            let el = first;
            for (let i = 0; i < 10 && el; i++) {
                el = el.parentElement;
                if (!el || el === document.body) break;
                const lbl = el.querySelector('label,legend,h3,h4,p,.field-label');
                if (lbl) { const t = lbl.innerText.trim(); if (t.length > 4) { qText = t.toLowerCase(); break; } }
            }
            g.questionText = qText;
            const eeoKw = ['gender','sex','age','veteran','disab','ethnic','race','identify'];
            if (eeoKw.some(k => qText.includes(k))) result.push(g);
        }
        return result;
    }""")

    eeo_radio_map = {
        ("gender", "sex"):                     "Male",
        ("age",):                               "20-29",
        ("veteran",):                           "I am not a protected veteran",
        ("disab",):                             "No, I don't have a disability",
        ("ethnic", "race", "identify"):         "Asian",
    }

    for group in radio_eeo_groups:
        q = group.get("questionText", "")
        radios = group.get("radios", [])
        if not radios:
            continue

        target = None
        for kw_tuple, val in eeo_radio_map.items():
            if any(kw in q for kw in kw_tuple):
                target = val
                break

        if not target:
            continue

        # Find matching radio
        best_sel = None
        t_lower = target.lower()
        for r in radios:
            if r["text"].strip().lower() == t_lower:
                best_sel = r["selector"]
                break
        if not best_sel:
            for r in radios:
                rt = r["text"].strip().lower()
                if t_lower[:6] in rt or any(w in rt for w in t_lower.split() if len(w) > 3):
                    best_sel = r["selector"]
                    break

        if best_sel:
            try:
                loc = page.locator(best_sel).first
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=3000)
                await ev("Lever EEO", "success", f"Radio: '{target}' for '{q[:50]}'")
            except Exception as e:
                await ev("Lever EEO", "warning", f"EEO radio failed '{q[:30]}': {e}")

    # ── 3. Checkbox-based ethnicity ("Select all that apply") ─────────────────
    # Detect by label text (not by container structure — more reliable for all Lever configs).
    # Use index-based JS clicking via label.click() for React compatibility.
    _race_words = [
        'asian', 'white', 'caucasian', 'hispanic', 'latino', 'black', 'african',
        'native', 'pacific', 'indigenous', 'middle eastern', 'north african',
        'other race', 'other origin', 'other ethnicity', 'prefer not to answer',
        'prefer not to disclose',
    ]
    ethnicity_cbs = await page.evaluate("""(matchKw, raceWords) => {
        const allCbs = Array.from(document.querySelectorAll('input[type="checkbox"]'));
        const result = [];
        for (let i = 0; i < allCbs.length; i++) {
            const cb = allCbs[i];
            if (cb.offsetParent === null) continue;
            // Get label text using direct text nodes (avoids nested element noise)
            const lbl = cb.closest('label');
            const rawText = lbl
                ? Array.from(lbl.childNodes).filter(n => n.nodeType === 3)
                    .map(n => n.textContent.trim()).filter(t => t).join(' ')
                : (cb.value || '');
            const text = rawText.toLowerCase();
            if (!raceWords.some(w => text.includes(w))) continue;
            result.push({
                index: i,
                text: lbl ? lbl.innerText.trim() : cb.value,
                isChecked: cb.checked,
                shouldCheck: matchKw.some(kw => text.includes(kw)),
            });
        }
        return result;
    }""", EEO_ETHNICITY_MATCH, _race_words)

    if ethnicity_cbs:
        await ev("Lever EEO", "info", f"Found {len(ethnicity_cbs)} ethnicity checkboxes (checkbox-style EEO)")
        for item in ethnicity_cbs:
            idx   = item["index"]
            txt   = item["text"][:50]
            should = item["shouldCheck"]
            checked = item["isChecked"]

            await ev("Lever EEO", "info",
                     f"  '{txt}' | checked={checked} | shouldCheck={should}")

            if should == checked:
                continue  # Already in correct state

            try:
                via = await page.evaluate(f"""() => {{
                    const cbs = document.querySelectorAll('input[type="checkbox"]');
                    const cb = cbs[{idx}];
                    if (!cb) return 'not-found';
                    const lbl = cb.closest('label');
                    if (lbl) {{ lbl.click(); return 'label'; }}
                    cb.click(); return 'input';
                }}""")
                await asyncio.sleep(0.2)
                action = "Checked" if should else "Unchecked"
                await ev("Lever EEO", "success", f"{action} ethnicity '{txt}' (via {via})")
            except Exception as e:
                await ev("Lever EEO", "warning", f"Checkbox click failed '{txt}': {e}")
    else:
        await ev("Lever EEO", "info", "No checkbox-style ethnicity group found (likely uses <select>)")


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
        resume_basename = os.path.basename(resume_path)

        # ── Strategy A: set_input_files directly on the hidden file input ──
        # Playwright can set files on hidden inputs without making them visible.
        # After setting, verify the file actually registered (React may not fire
        # its onChange for a manually-unhidden input).
        for sel in [
            'input[type="file"][name*="resume"]',
            'input[type="file"][id*="resume"]',
            'input[type="file"][accept*="pdf"]',
            'input[type="file"]',
        ]:
            try:
                fi = page.locator(sel).first
                if await fi.count() == 0:
                    continue
                # Unhide if needed, then set files
                await page.evaluate(f"""() => {{
                    const el = document.querySelector('{sel}');
                    if (el) {{ el.style.display = 'block'; el.style.opacity = '1'; }}
                }}""")
                await fi.set_input_files(resume_path)
                await asyncio.sleep(1.5)
                # Verify: check that the file input's .files list is populated
                # AND that Lever's UI shows the filename (indicates React processed it)
                verified = await page.evaluate(f"""() => {{
                    const fi = document.querySelector('{sel}');
                    if (!fi) return false;
                    if (fi.files && fi.files.length > 0) return true;
                    // Also check if Lever rendered the filename anywhere on the page
                    const pageText = document.body.innerText;
                    return pageText.includes('{resume_basename}');
                }}""")
                if verified:
                    resume_uploaded = True
                    await ev("Lever", "success", f"Resume uploaded (direct): {resume_basename}")
                    break
                else:
                    await ev("Lever", "info",
                             f"Strategy A: set_input_files ran but file not registered by React — trying Strategy B")
            except Exception:
                continue

        # ── Strategy B: intercept the file-chooser dialog (most robust) ──
        # Lever's "ATTACH RESUME/CV" link opens a native file dialog.
        # expect_file_chooser intercepts it so we never see the OS dialog.
        if not resume_uploaded:
            attach_selectors = [
                'a:has-text("ATTACH")', 'button:has-text("ATTACH")',
                'a:has-text("Attach")', 'button:has-text("Attach")',
                'a:has-text("attach")', 'button:has-text("attach")',
                '[class*="resume"]', '[class*="upload"]',
            ]
            for btn_sel in attach_selectors:
                try:
                    btn = page.locator(btn_sel).first
                    if await btn.count() == 0:
                        continue
                    async with page.expect_file_chooser(timeout=4000) as fc_info:
                        await btn.click(timeout=3000)
                    fc = await fc_info.value
                    await fc.set_files(resume_path)
                    await asyncio.sleep(1.5)
                    resume_uploaded = True
                    await ev("Lever", "success",
                             f"Resume uploaded (file-chooser): {resume_basename}")
                    break
                except Exception:
                    continue

        if not resume_uploaded:
            await ev("Lever", "warning",
                     "Resume upload: could not find file input or ATTACH button")
            errors.append("Resume upload failed")
    else:
        if resume_path:
            await ev("Lever", "warning", f"Resume file not found on disk: {resume_path}")
        else:
            await ev("Lever", "warning", "No resume path configured")

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
            // Label lookup — Lever wraps inputs in <label>; get text nodes from that label
            let lbl = '';
            if (id) { const l = document.querySelector('label[for="' + id + '"]'); if (l) lbl = l.innerText.replace('*','').replace('✱','').trim(); }
            if (!lbl) {
                // Lever structure: input > div.application-field > label (text "Field Name") > li
                const parentLbl = el.closest('label');
                if (parentLbl) {
                    // Use direct text nodes only to avoid picking up dropdown result text
                    lbl = Array.from(parentLbl.childNodes)
                        .filter(n => n.nodeType === 3)
                        .map(n => n.textContent.trim())
                        .filter(t => t.length > 0 && t !== '✱' && t !== '*')
                        .join(' ').trim();
                }
            }
            if (!lbl) {
                const wrapper = el.closest('.application-question, .application-field, .field, .form-group, li');
                if (wrapper) { const l = wrapper.querySelector('label:not(:has(input)), .field-label, legend'); if (l) lbl = l.innerText.replace('*','').replace('✱','').trim(); }
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
                const parentLbl = el.closest('label');
                if (parentLbl) {
                    lbl = Array.from(parentLbl.childNodes)
                        .filter(n => n.nodeType === 3)
                        .map(n => n.textContent.trim())
                        .filter(t => t.length > 0 && t !== '✱' && t !== '*')
                        .join(' ').trim();
                }
            }
            if (!lbl) {
                const wrapper = el.closest('.application-question, .application-field, .field, li');
                if (wrapper) { const l = wrapper.querySelector('label:not(:has(select)), .field-label, legend'); if (l) lbl = l.innerText.replace('*','').replace('✱','').trim(); }
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
            _fill_ok = False
            lbl_lower = label.lower()
            try:
                el = page.locator(sel).first
                await el.click(click_count=3, timeout=3000)
                await el.fill("", timeout=2000)  # clear first
                # For location/city fields use pressSequentially to fire all keyboard
                # events (needed to trigger Google Places / Lever autocomplete).
                # Then check if a dropdown appeared; if so pick the first suggestion
                # with ArrowDown+Enter. Otherwise just Tab away so the typed value
                # is committed without accidentally submitting the form.
                if any(kw in lbl_lower for kw in ("location", "city", "address", "where")):
                    await el.press_sequentially(value, delay=60)
                    await asyncio.sleep(3.5)  # Lever autocomplete API can take 2-3s
                    # Try Lever-specific dropdown first (.dropdown-location items)
                    _clicked_dropdown = False
                    try:
                        _lever_item = page.locator(".dropdown-location").first
                        if await _lever_item.is_visible(timeout=1500):
                            await _lever_item.click(timeout=2000)
                            _clicked_dropdown = True
                    except Exception:
                        pass
                    if not _clicked_dropdown:
                        # Try Lever's #location-0 id (first autocomplete result)
                        try:
                            _clicked_dropdown = await page.evaluate("""() => {
                                const el = document.querySelector('#location-0');
                                if (el && el.offsetParent !== null) { el.click(); return true; }
                                return false;
                            }""")
                        except Exception:
                            pass
                    if not _clicked_dropdown:
                        # Fallback: generic autocomplete dropdowns
                        try:
                            _dd = page.locator(
                                "ul[role='listbox'] li, [class*='autocomplete'] li, "
                                "[class*='suggestion'], [class*='dropdown-item']"
                            )
                            if await _dd.first.is_visible(timeout=800):
                                await _dd.first.click(timeout=2000)
                                _clicked_dropdown = True
                        except Exception:
                            pass
                    if not _clicked_dropdown:
                        await el.press("Tab")  # commit typed value
                    await asyncio.sleep(0.5)
                else:
                    await el.fill(value, timeout=3000)
                _fill_ok = True
            except Exception:
                pass  # fall through to JS injection

            if not _fill_ok:
                # Fallback: JS value injection — bypasses CAPTCHA/overlay pointer interception.
                # Works for Lever URL fields (LinkedIn/GitHub/Portfolio) that sit below
                # the hCaptcha iframe which intercepts mouse events.
                try:
                    injected = await page.evaluate("""(args) => {
                        const el = document.querySelector(args.sel);
                        if (!el) return false;
                        // Use native setter so React's onChange fires correctly
                        const proto = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ) || Object.getOwnPropertyDescriptor(
                            window.HTMLTextAreaElement.prototype, 'value'
                        );
                        if (proto && proto.set) proto.set.call(el, args.val);
                        else el.value = args.val;
                        el.dispatchEvent(new Event('input',  { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('blur',   { bubbles: true }));
                        return true;
                    }""", {"sel": sel, "val": value})
                    if injected:
                        _fill_ok = True
                        if event_callback:
                            await event_callback("Lever", "info",
                                f"JS-injected '{label}' (captcha overlay workaround)")
                except Exception:
                    pass

            if _fill_ok:
                filled += 1
            else:
                failed += 1
                errors.append(f"Fill {label}: click and JS injection both failed")
        else:
            # Custom question needing LLM
            if label and (field.get("required") or ftype == "textarea"):
                custom_questions.append(field)

    await ss()

    # ── Step 3b: Pronouns checkboxes (label-text proximity search) ─────────
    # Lever forms often use name="cards[uuid]" instead of name="pronouns",
    # so we find the pronoun group by locating the "Pronouns" heading/label
    # and collecting all checkboxes inside its container.
    pronouns_val = (personal_info.get("pronouns") or "").lower().strip()  # e.g. "he/him"
    _PRONOUN_JS = """(target) => {
        // Normalise: lowercase, strip spaces/slashes so "He/him" == "he/him" == "hehim"
        function norm(s) { return s.toLowerCase().replace(/[\\s\\/]+/g, ''); }
        const normTarget = norm(target);

        // 1. Find the element whose direct text is "Pronouns" (label/legend/span/etc.)
        let pronounsLabel = null;
        const candidates = document.querySelectorAll(
            'label, legend, span, p, div, h3, h4, h5, li');
        for (const el of candidates) {
            // Use only direct text nodes to avoid matching child text
            const direct = Array.from(el.childNodes)
                .filter(n => n.nodeType === 3)
                .map(n => n.textContent.trim())
                .join(' ')
                .trim();
            if (/^pronouns[*:\\s]*$/i.test(direct) ||
                /^pronouns[*:\\s]*$/i.test(el.textContent.trim())) {
                pronounsLabel = el;
                break;
            }
        }
        if (!pronounsLabel) return [];

        // 2. Walk up from that label until we find the smallest ancestor
        //    that directly contains at least one checkbox (but fewer than 20,
        //    to avoid grabbing the whole form).
        let container = pronounsLabel.parentElement;
        for (let i = 0; i < 10 && container; i++) {
            const count = container.querySelectorAll('input[type="checkbox"]').length;
            if (count > 0 && count < 20) break;
            container = container.parentElement;
        }
        if (!container) return [];

        // 3. Map each checkbox in the container to {index, text, checked, shouldCheck}
        const allCbs = Array.from(document.querySelectorAll('input[type="checkbox"]'));
        const localCbs = Array.from(container.querySelectorAll('input[type="checkbox"]'));

        return localCbs.map(cb => {
            // Prefer direct text-node content of the wrapping <label>
            let text = cb.value || '';
            const lbl = cb.closest('label') ||
                        document.querySelector('label[for="' + cb.id + '"]');
            if (lbl) {
                const textNodes = Array.from(lbl.childNodes)
                    .filter(n => n.nodeType === 3)
                    .map(n => n.textContent.trim())
                    .filter(Boolean);
                text = textNodes.length > 0
                    ? textNodes.join(' ')
                    : lbl.textContent.trim();
            }
            return {
                index: allCbs.indexOf(cb),
                text: text,
                isChecked: cb.checked,
                shouldCheck: norm(text) === normTarget,
            };
        });
    }"""
    if pronouns_val:
        pronoun_data = await page.evaluate(_PRONOUN_JS, pronouns_val)

        if pronoun_data:
            await ev("Lever", "info",
                     f"Pronouns: found {len(pronoun_data)} option(s) in section")
        else:
            await ev("Lever", "warning",
                     "Pronouns: could not locate pronoun checkbox section")

        for item in pronoun_data:
            should = item["shouldCheck"]
            checked = item["isChecked"]
            if should == checked:
                continue  # already correct state
            idx = item["index"]
            try:
                await page.evaluate(f"""() => {{
                    const cbs = document.querySelectorAll('input[type="checkbox"]');
                    const cb = cbs[{idx}];
                    if (!cb) return;
                    const lbl = cb.closest('label');
                    if (lbl) lbl.click(); else cb.click();
                }}""")
                action = "Checked" if should else "Unchecked"
                await ev("Lever", "success", f"{action} pronoun: {item['text']}")
            except Exception as e:
                await ev("Lever", "warning",
                         f"Pronoun checkbox failed '{item['text']}': {e}")

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

        # Skip EEO/demographic questions — handled by _fill_lever_eeo below
        _eeo_kw = ["gender", "sex", "age", "ethnic", "race", "veteran", "disab", "identify"]
        if any(kw in q_text for kw in _eeo_kw):
            continue

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

    # ── Step 8: Final pronouns re-check (runs LAST so nothing can undo it) ─
    # Re-uses the same label-proximity JS from Step 3b (_PRONOUN_JS).
    # Runs after EEO + agree steps in case any prior step accidentally toggled them.
    pronouns_val_final = (personal_info.get("pronouns") or "").lower().strip()
    if pronouns_val_final:
        pronoun_data_final = await page.evaluate(_PRONOUN_JS, pronouns_val_final)

        changed_final = 0
        for item in pronoun_data_final:
            should = item["shouldCheck"]
            checked = item["isChecked"]
            if should == checked:
                continue
            idx = item["index"]
            try:
                await page.evaluate(f"""() => {{
                    const cbs = document.querySelectorAll('input[type="checkbox"]');
                    const cb = cbs[{idx}];
                    if (!cb) return;
                    const lbl = cb.closest('label');
                    if (lbl) lbl.click(); else cb.click();
                }}""")
                action = "Checked" if should else "Unchecked"
                await ev("Lever", "success",
                         f"Final re-check pronoun: {item['text']} ({action})")
                changed_final += 1
            except Exception as e:
                await ev("Lever", "warning",
                         f"Final pronoun re-check failed '{item['text']}': {e}")

        if not changed_final and pronoun_data_final:
            await ev("Lever", "success",
                     f"Pronouns already correct — '{pronouns_val_final}' only")

    await ev("Lever", "success",
        f"Lever form filled ({filled} fields, {failed} failed). Review in browser before submitting.")

    return {
        "filled":    filled,
        "failed":    failed,
        "submitted": False,
        "errors":    errors,
    }

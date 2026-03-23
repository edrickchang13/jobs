"""
Dedicated handler for Workday multi-step job applications.

Steps: My Information → My Experience → Application Questions →
       Voluntary Disclosures → Self Identify → Review
"""
import asyncio
import json
import os
import yaml
from pathlib import Path
from playwright.async_api import Page


def _load_personal_info_wd() -> dict:
    """Load personal info from YAML file."""
    info_path = Path(__file__).parent.parent / "personal_info.yaml"
    if info_path.exists():
        with open(info_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


async def fill_workday_info_hardcoded(page: Page, event_callback=None) -> dict:
    """Fill Workday 'My Information' step using hardcoded field mappings.

    Bypasses the LLM entirely. Uses Workday-specific DOM queries to find and
    fill fields. Workday uses custom React components, NOT standard HTML inputs.

    Key Workday DOM patterns:
    - Text inputs: inside [data-automation-id="formField-XXX"] containers
    - Radio buttons: <input type="radio" name="..." value="true"/"false">
      with [data-automation-id="click_filter"] overlays (need isTrusted clicks)
    - Dropdowns: custom promptList with button + popup pattern
    """
    info = _load_personal_info_wd()
    filled = 0
    failed = 0
    errors = []

    if event_callback:
        await event_callback("Fill Form", "info", "Using hardcoded Workday field filler (no LLM)")

    # Step 0: Diagnostic - dump current page state
    try:
        diag = await page.evaluate("""() => {
            return {
                url: window.location.href,
                title: document.title,
                bodyLen: document.body.innerText.length,
                formFieldCount: document.querySelectorAll('[data-automation-id^="formField-"]').length,
                inputCount: document.querySelectorAll('input').length,
                visibleInputCount: Array.from(document.querySelectorAll('input')).filter(i => i.offsetParent !== null).length,
                radioCount: document.querySelectorAll('input[type="radio"]').length,
                progressBar: !!document.querySelector('[data-automation-id="progressBar"]'),
                activeStep: (() => { const s = document.querySelector('[data-automation-id="progressBarActiveStep"]'); return s ? s.innerText.trim() : 'none'; })(),
                firstLabels: Array.from(document.querySelectorAll('[data-automation-id^="formField-"] label')).slice(0, 10).map(l => l.innerText.trim()),
            };
        }""")
        if event_callback:
            await event_callback("Fill Form", "diag", f"Page: {diag.get('url', '?')[:60]}")
            await event_callback("Fill Form", "diag", f"Step: {diag.get('activeStep')}, formFields: {diag.get('formFieldCount')}, inputs: {diag.get('visibleInputCount')}/{diag.get('inputCount')}, radios: {diag.get('radioCount')}")
            await event_callback("Fill Form", "diag", f"Labels found: {diag.get('firstLabels', [])}")
        print(f">>> DIAG: {json.dumps(diag, indent=2)}")
    except Exception as e:
        print(f">>> DIAG error: {e}")
        if event_callback:
            await event_callback("Fill Form", "warn", f"Diagnostic error: {str(e)[:80]}")

    # Step 1: Scan ALL visible text inputs and fill based on label matching
    try:
        text_fields = await page.evaluate("""() => {
            const results = [];
            // Scan all formField containers
            const containers = document.querySelectorAll('[data-automation-id^="formField-"]');
            for (const c of containers) {
                if (c.offsetParent === null) continue;
                const dataid = c.getAttribute('data-automation-id') || '';
                // Skip nav/menu elements
                if (dataid.includes('navigation') || dataid.includes('search') || dataid.includes('menu')) continue;
                // Find label
                const label = c.querySelector('label');
                const labelText = label ? label.innerText.trim() : '';
                // Find input
                const inp = c.querySelector('input[type="text"], input[type="tel"], input[type="email"], input[type="number"], input:not([type])');
                if (inp && inp.offsetParent !== null) {
                    const r = inp.getBoundingClientRect();
                    results.push({
                        dataid,
                        label: labelText.toLowerCase(),
                        value: inp.value || '',
                        x: r.x + r.width/2,
                        y: r.y + r.height/2,
                        visible: r.width > 0 && r.height > 0,
                    });
                }
            }
            return results;
        }""")

        # Log what we found
        print(f">>> TEXT FIELDS FOUND: {len(text_fields or [])}")
        if event_callback:
            await event_callback("Fill Form", "diag", f"Text fields found: {len(text_fields or [])}")
        for tf in (text_fields or []):
            print(f">>>   label='{tf.get('label')}' dataid='{tf.get('dataid')}' value='{tf.get('value', '')[:20]}' vis={tf.get('visible')} xy=({tf.get('x'):.0f},{tf.get('y'):.0f})")
            if event_callback:
                await event_callback("Fill Form", "diag",
                    f"  field: label='{tf.get('label', '')[:30]}' val='{tf.get('value', '')[:15]}' vis={tf.get('visible')}")

        # Map labels to values
        label_values = {
            "first name": info.get("first_name", "Edrick"),
            "last name": info.get("last_name", "Chang"),
            "phone number": info.get("phone", "(408) 806-6495"),
            "phone": info.get("phone", "(408) 806-6495"),
            "address line 1": info.get("street_address", ""),
            "address": info.get("street_address", ""),
            "city": info.get("city", "Santa Clara"),
            "postal code": info.get("zip_code", ""),
            "zip": info.get("zip_code", ""),
        }

        for tf in (text_fields or []):
            label = tf.get("label", "")
            if not tf.get("visible"):
                continue
            # Skip if already filled
            if tf.get("value", "").strip():
                filled += 1
                if event_callback:
                    await event_callback("Fill Form", "info", f"Already filled: {label[:30]} = '{tf['value'][:20]}'")
                continue

            # Find matching value
            matched_value = None
            for pattern, val in label_values.items():
                if pattern in label and val:
                    matched_value = val
                    break

            if matched_value:
                await page.mouse.click(tf["x"], tf["y"])
                await asyncio.sleep(0.2)
                # Triple-click to select all, then type
                await page.mouse.click(tf["x"], tf["y"], click_count=3)
                await asyncio.sleep(0.1)
                await page.keyboard.type(matched_value, delay=20)
                await asyncio.sleep(0.3)
                # Click elsewhere to trigger blur/validation
                await page.keyboard.press("Tab")
                await asyncio.sleep(0.3)
                filled += 1
                if event_callback:
                    await event_callback("Fill Form", "success", f"Filled '{label[:30]}' = '{matched_value}'")

    except Exception as e:
        if event_callback:
            await event_callback("Fill Form", "warn", f"Text field scan error: {str(e)[:80]}")

    # Step 2: Handle radio buttons using Workday's actual DOM
    # Workday uses <input type="radio" name="candidateIsPreviousWorker" value="true"/"false">
    # wrapped in labels, with click_filter overlays
    try:
        radio_info = await page.evaluate("""() => {
            // Find all radio inputs (Workday uses standard HTML radios, not role="radio")
            const radios = document.querySelectorAll('input[type="radio"]');
            const groups = {};
            for (const r of radios) {
                const name = r.getAttribute('name') || '';
                if (!name) continue;
                if (!groups[name]) groups[name] = [];
                // Get the label text
                const parentLabel = r.closest('label');
                let labelText = parentLabel ? parentLabel.innerText.trim() : '';
                if (!labelText) {
                    const nextSibling = r.nextElementSibling || r.parentElement;
                    labelText = nextSibling ? nextSibling.innerText?.trim() : '';
                }
                // Get the click target (click_filter or the radio itself)
                const filter = r.closest('div')?.querySelector('[data-automation-id="click_filter"]');
                const target = filter || r;
                const rect = target.getBoundingClientRect();
                groups[name].push({
                    value: r.value,
                    checked: r.checked,
                    labelText: labelText.toLowerCase(),
                    x: rect.x + rect.width/2,
                    y: rect.y + rect.height/2,
                    hasFilter: !!filter,
                });
            }
            return groups;
        }""")

        print(f">>> RADIO GROUPS FOUND: {len(radio_info or {})}")
        if event_callback:
            await event_callback("Fill Form", "diag", f"Radio groups found: {len(radio_info or {})}")
        for name, options in (radio_info or {}).items():
            labels = [o.get("labelText", "?") for o in options]
            checked = [o.get("checked", False) for o in options]
            print(f">>>   group='{name}' labels={labels} checked={checked}")
            if event_callback:
                await event_callback("Fill Form", "diag", f"  radio: name='{name}' labels={labels}")

        for name, options in (radio_info or {}).items():
            # Skip already-answered groups
            if any(o.get("checked") for o in options):
                filled += 1
                if event_callback:
                    checked_label = next((o["labelText"] for o in options if o.get("checked")), "?")
                    await event_callback("Fill Form", "info", f"Radio '{name}' already: {checked_label}")
                continue

            # Determine answer
            name_lower = name.lower()
            target_value = None
            if "previousworker" in name_lower or "previously" in name_lower:
                target_value = "false"  # No
            elif "authorized" in name_lower or "eligible" in name_lower:
                target_value = "true"  # Yes
            elif "sponsorship" in name_lower:
                target_value = "false"  # No

            if target_value:
                for o in options:
                    if o["value"] == target_value:
                        # Use real mouse click (isTrusted=true) for Workday
                        await page.mouse.click(o["x"], o["y"])
                        await asyncio.sleep(0.5)
                        filled += 1
                        answer_text = "No" if target_value == "false" else "Yes"
                        if event_callback:
                            await event_callback("Fill Form", "success",
                                f"Radio '{name}': clicked {answer_text} (filter={o.get('hasFilter')})")
                        break

    except Exception as e:
        if event_callback:
            await event_callback("Fill Form", "warn", f"Radio button error: {str(e)[:80]}")

    # Step 3: Check country dropdown
    try:
        country_text = await page.evaluate("""() => {
            // Check various selectors for country field
            for (const sel of ['[data-automation-id="formField-countryDropdown"]',
                               '[data-automation-id="formField-country"]',
                               'select[data-automation-id*="country"]']) {
                const el = document.querySelector(sel);
                if (el) {
                    const text = el.innerText || el.value || '';
                    if (text.includes('United States')) return text.trim().substring(0, 50);
                }
            }
            return '';
        }""")
        if country_text:
            if event_callback:
                await event_callback("Fill Form", "info", f"Country: {country_text}")
    except Exception:
        pass

    if event_callback:
        await event_callback("Fill Form", "info", f"Hardcoded fill done: {filled} filled, {failed} failed")

    return {"filled": filled, "failed": failed, "skipped": 0, "errors": errors}


async def fill_workday_questions_hardcoded(page: Page, company: str = "", role: str = "", event_callback=None) -> dict:
    """Fill Workday 'Application Questions' and similar steps using hardcoded logic.

    Scans for common question patterns (radio buttons, dropdowns, text fields)
    and fills them with known values. No LLM required.
    """
    info = _load_personal_info_wd()
    filled = 0
    failed = 0
    errors = []

    if event_callback:
        await event_callback("Fill Form", "info", "Filling Application Questions (hardcoded, no LLM)")

    # Common yes/no question patterns and their answers
    # Format: (label_pattern, answer)
    yes_no_answers = {
        "authorized to work": "Yes",
        "legally authorized": "Yes",
        "eligible to work": "Yes",
        "work authorization": "Yes",
        "require sponsorship": "No",
        "need sponsorship": "No",
        "visa sponsorship": "No",
        "immigration sponsorship": "No",
        "previously worked": "No",
        "previously employed": "No",
        "previously applied": "No",
        "worked for": "No",
        "employed by": "No",
        "applied to": "No",
        "relative": "No",
        "family member": "No",
        "felony": "No",
        "convicted": "No",
        "criminal": "No",
        "background check": "Yes",
        "drug test": "Yes",
        "drug screen": "Yes",
        "relocate": "Yes",
        "willing to relocate": "Yes",
        "able to relocate": "Yes",
        "on-site": "Yes",
        "onsite": "Yes",
        "work on site": "Yes",
        "currently employed": "No",
        "18 years": "Yes",
        "over 18": "Yes",
        "at least 18": "Yes",
        "age requirement": "Yes",
        "non-compete": "No",
        "non compete": "No",
        "nda": "No",
    }

    # Scan all visible radio button groups
    try:
        radio_groups = await page.evaluate("""() => {
            const groups = {};
            const radios = document.querySelectorAll('input[type="radio"]');
            for (const r of radios) {
                if (r.offsetParent === null && !r.closest('label')) continue;
                const name = r.getAttribute('name') || '';
                if (!name) continue;
                if (!groups[name]) groups[name] = [];
                // Find the label text for this radio
                const label = r.closest('label');
                const labelText = label ? label.innerText.trim() : (r.value || '');
                // Find the question text (usually in a parent or preceding element)
                let questionText = '';
                const fieldContainer = r.closest('[data-automation-id^="formField-"]') || r.closest('.css-1q3fhg7') || r.closest('fieldset');
                if (fieldContainer) {
                    const lbl = fieldContainer.querySelector('label, legend, [class*="label"]');
                    questionText = lbl ? lbl.innerText.trim() : '';
                }
                const rect = r.getBoundingClientRect();
                groups[name].push({
                    value: r.value,
                    labelText,
                    questionText,
                    checked: r.checked,
                    x: rect.x + rect.width/2,
                    y: rect.y + rect.height/2,
                });
            }
            return groups;
        }""")

        for name, options in (radio_groups or {}).items():
            if not options:
                continue
            # Get the question text from the first option
            question = (options[0].get("questionText", "") or "").lower()
            if not question:
                # Try to infer from the radio name
                question = name.lower()

            # Already answered?
            if any(o.get("checked") for o in options):
                filled += 1
                continue

            # Determine answer
            answer = None
            for pattern, ans in yes_no_answers.items():
                if pattern in question:
                    answer = ans
                    break

            if answer:
                # Find the matching option
                for o in options:
                    label = o.get("labelText", "").lower().strip()
                    if (answer == "Yes" and label in ("yes", "true")) or \
                       (answer == "No" and label in ("no", "false")):
                        await page.mouse.click(o["x"], o["y"])
                        await asyncio.sleep(0.3)
                        filled += 1
                        if event_callback:
                            await event_callback("Fill Form", "success",
                                f"Answered '{question[:40]}' = {answer}")
                        break
    except Exception as e:
        if event_callback:
            await event_callback("Fill Form", "warn", f"Radio scan error: {str(e)[:80]}")

    # Scan for select/dropdown fields
    try:
        selects = await page.evaluate("""() => {
            const results = [];
            const sels = document.querySelectorAll('select');
            for (const s of sels) {
                if (s.offsetParent === null) continue;
                const container = s.closest('[data-automation-id^="formField-"]');
                let label = '';
                if (container) {
                    const lbl = container.querySelector('label');
                    label = lbl ? lbl.innerText.trim() : '';
                }
                const options = Array.from(s.options).map(o => ({value: o.value, text: o.text}));
                results.push({
                    label: label.toLowerCase(),
                    currentValue: s.value,
                    dataid: s.getAttribute('data-automation-id') || '',
                    options,
                    selector: container ? `[data-automation-id="${container.getAttribute('data-automation-id')}"] select` : '',
                });
            }
            return results;
        }""")

        select_answers = {
            "country": "United States",
            "state": "California",
            "graduation": "2028",
            "year": "2028",
            "degree": "Bachelor",
            "education": "Bachelor",
            "experience": "0",
            "intern season": "Summer",
            "season": "Summer",
        }

        for sel_info in (selects or []):
            label = sel_info.get("label", "")
            if sel_info.get("currentValue"):
                filled += 1
                continue
            for pattern, ans in select_answers.items():
                if pattern in label:
                    # Find best matching option
                    for opt in sel_info.get("options", []):
                        if ans.lower() in opt.get("text", "").lower():
                            selector = sel_info.get("selector", "")
                            if selector:
                                await page.locator(selector).first.select_option(value=opt["value"])
                                filled += 1
                                if event_callback:
                                    await event_callback("Fill Form", "success",
                                        f"Selected '{opt['text']}' for '{label[:30]}'")
                            break
                    break
    except Exception as e:
        if event_callback:
            await event_callback("Fill Form", "warn", f"Select scan error: {str(e)[:80]}")

    # Scan for text inputs that are still empty
    try:
        text_fields = await page.evaluate("""() => {
            const results = [];
            const inputs = document.querySelectorAll('input[type="text"], input[type="tel"], input[type="number"], input[type="url"], input:not([type])');
            for (const inp of inputs) {
                if (inp.offsetParent === null) continue;
                if (inp.value && inp.value.trim()) continue;
                // Skip nav elements
                const dataid = inp.getAttribute('data-automation-id') || '';
                if (dataid.includes('navigation') || dataid.includes('search') || dataid.includes('menu')) continue;
                const container = inp.closest('[data-automation-id^="formField-"]');
                let label = '';
                if (container) {
                    const lbl = container.querySelector('label');
                    label = lbl ? lbl.innerText.trim() : '';
                }
                const rect = inp.getBoundingClientRect();
                results.push({
                    label: label.toLowerCase(),
                    dataid,
                    x: rect.x + rect.width/2,
                    y: rect.y + rect.height/2,
                    placeholder: inp.placeholder || '',
                });
            }
            return results;
        }""")

        text_answers = {
            "phone": info.get("phone", "(408) 806-6495"),
            "linkedin": info.get("linkedin", "https://linkedin.com/in/edrickchang"),
            "github": info.get("github", "https://github.com/edrickchang"),
            "gpa": info.get("gpa", "3.78"),
            "postal": info.get("zip_code", ""),
            "zip": info.get("zip_code", ""),
            "city": info.get("city", "Santa Clara"),
            "address": info.get("street_address", ""),
            "graduation year": info.get("graduation_year", "2028"),
        }

        for tf in (text_fields or []):
            label = tf.get("label", "")
            for pattern, ans in text_answers.items():
                if pattern in label and ans:
                    await page.mouse.click(tf["x"], tf["y"])
                    await asyncio.sleep(0.1)
                    await page.keyboard.type(ans, delay=20)
                    await asyncio.sleep(0.2)
                    filled += 1
                    if event_callback:
                        await event_callback("Fill Form", "success", f"Filled '{label[:30]}' = '{ans}'")
                    break
    except Exception as e:
        if event_callback:
            await event_callback("Fill Form", "warn", f"Text field scan error: {str(e)[:80]}")

    # Handle Workday promptList dropdowns on this page too
    try:
        await _fill_workday_prompt_dropdown(page, event_callback)
    except Exception:
        pass

    if event_callback:
        await event_callback("Fill Form", "info", f"Questions page: {filled} filled, {failed} failed")

    return {"filled": filled, "failed": failed, "skipped": 0, "errors": errors}


async def _fill_workday_prompt_dropdown(page: Page, event_callback=None):
    """Targeted handler for Workday promptList dropdowns like 'How Did You Hear About Us?'.

    Uses direct Playwright interaction: click the dropdown button, wait for popup,
    then click the matching option with real mouse events.
    """
    # Check if "How Did You Hear About Us?" field exists and is empty
    field_info = await page.evaluate("""() => {
        // Find formField containers that match "source" or "hear" patterns
        const candidates = [
            'sourceType', 'source', 'jobSource', 'howDidYouHear',
            'referralSource', 'sourceName',
        ];
        for (const cand of candidates) {
            const el = document.querySelector('[data-automation-id="formField-' + cand + '"]');
            if (el && el.offsetParent !== null) {
                // Check if it already has a selected value (pill/chip)
                const pill = el.querySelector('[data-automation-id="selectedItem"], [data-automation-id*="pill"], .css-1wc848c');
                if (pill) return { found: true, filled: true, dataid: 'formField-' + cand };
                // Check if input has value
                const input = el.querySelector('input');
                if (input && input.value && input.value.trim()) return { found: true, filled: true, dataid: 'formField-' + cand };
                return { found: true, filled: false, dataid: 'formField-' + cand };
            }
        }
        // Broader search: look for any formField with label containing "hear" or "source"
        const allFields = document.querySelectorAll('[data-automation-id^="formField-"]');
        for (const el of allFields) {
            if (el.offsetParent === null) continue;
            const label = el.querySelector('label');
            const labelText = (label ? label.innerText : '').toLowerCase();
            if (labelText.includes('hear') || labelText.includes('source') || labelText.includes('how did you')) {
                const pill = el.querySelector('[data-automation-id="selectedItem"], [data-automation-id*="pill"]');
                if (pill) return { found: true, filled: true, dataid: el.getAttribute('data-automation-id') };
                return { found: true, filled: false, dataid: el.getAttribute('data-automation-id') };
            }
        }
        return { found: false };
    }""")

    if not field_info or not field_info.get("found"):
        return  # No such field on this page
    if field_info.get("filled"):
        if event_callback:
            await event_callback("Fill Form", "info", "How Did You Hear dropdown already filled")
        return

    dataid = field_info.get("dataid", "")
    selector = f'[data-automation-id="{dataid}"]'

    if event_callback:
        await event_callback("Fill Form", "info", f"Filling 'How Did You Hear' dropdown ({dataid})...")

    # Preferred answers in order of priority
    preferred = ["LinkedIn", "Online Job Board", "Job Board", "Internet", "Online", "Other"]

    try:
        # Step 1: Scroll the field into view
        field_el = page.locator(selector).first
        await field_el.scroll_into_view_if_needed(timeout=3000)
        await asyncio.sleep(0.3)

        # Step 2: Click the dropdown button/icon to open the popup
        # Workday dropdowns have a button or the container itself is clickable
        clicked = False
        for btn_sel in [
            f'{selector} button',
            f'{selector} [data-automation-id="searchBox"]',
            f'{selector} [role="button"]',
            f'{selector} svg',
            selector,
        ]:
            try:
                btn = page.locator(btn_sel).first
                if await btn.is_visible(timeout=500):
                    box = await btn.bounding_box()
                    if box:
                        await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                        clicked = True
                        await asyncio.sleep(1.0)  # Wait for popup to render
                        break
            except Exception:
                continue

        if not clicked:
            if event_callback:
                await event_callback("Fill Form", "warn", "Could not open 'How Did You Hear' dropdown")
            return

        # Step 3: Check if a popup/popover appeared with options
        # Try typing in search if there's an input
        search_typed = False
        for search_sel in [
            '[data-automation-id="searchBox"] input',
            'input[data-automation-id="searchBox"]',
            f'{selector} input[type="text"]',
        ]:
            try:
                search = page.locator(search_sel).first
                if await search.is_visible(timeout=500):
                    await search.fill("")
                    await asyncio.sleep(0.1)
                    await page.keyboard.type("LinkedIn", delay=50)
                    search_typed = True
                    await asyncio.sleep(1.0)
                    break
            except Exception:
                continue

        # Step 4: Find and click the best matching option
        for pref in preferred:
            for opt_sel in [
                '[data-automation-id*="promptOption"]',
                '[role="option"]',
                '[data-automation-id="menuItem"]',
            ]:
                try:
                    opts = page.locator(f'{opt_sel}:visible')
                    count = await opts.count()
                    for i in range(min(count, 20)):
                        opt = opts.nth(i)
                        try:
                            text = (await opt.inner_text(timeout=300)).strip()
                        except Exception:
                            continue
                        if pref.lower() in text.lower() or text.lower() in pref.lower():
                            box = await opt.bounding_box()
                            if box:
                                await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                                await asyncio.sleep(0.5)
                                if event_callback:
                                    await event_callback("Fill Form", "success", f"How Did You Hear: selected '{text}'")
                                return
                except Exception:
                    continue

        # Step 5: If no preferred match, just click the first visible option
        try:
            first = page.locator('[data-automation-id*="promptOption"]:visible, [role="option"]:visible').first
            if await first.is_visible(timeout=500):
                text = await first.inner_text(timeout=500)
                box = await first.bounding_box()
                if box:
                    await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                    await asyncio.sleep(0.5)
                    if event_callback:
                        await event_callback("Fill Form", "success", f"How Did You Hear: selected first option '{text[:30]}'")
                    return
        except Exception:
            pass

        # Step 6: Last resort - press Escape and try keyboard approach
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)

        if event_callback:
            await event_callback("Fill Form", "warn", "Could not select 'How Did You Hear' option, continuing anyway")

    except Exception as e:
        if event_callback:
            await event_callback("Fill Form", "warn", f"How Did You Hear handler error: {str(e)[:100]}")


async def check_workday_consent(page: Page, event_callback=None, max_wait_seconds=15) -> bool:
    """Check Workday consent checkboxes by clicking the click_filter overlay inside them.

    Workday puts a transparent div[data-automation-id="click_filter"] on top of every
    interactive element. This IS the intended click target — Workday's event delegation
    listens for clicks on click_filter and updates framework state.

    CRITICAL: Synthetic JS dispatchEvent creates events with isTrusted=false which Workday
    ignores. We MUST use real Playwright mouse.click() to generate isTrusted=true events.
    """

    if event_callback:
        await event_callback("Checkbox", "info", "Waiting for consent checkbox...")

    # Step 1: Poll for checkbox to appear (500ms intervals, up to max_wait_seconds)
    # Check BOTH role="checkbox" (Workday custom) and input[type="checkbox"] (standard HTML)
    checkbox_type = None  # "role" or "html"
    for i in range(max_wait_seconds * 2):
        has = await page.evaluate("""() => {
            for (const cb of document.querySelectorAll('[role="checkbox"]'))
                if (cb.offsetParent !== null) return 'role';
            for (const cb of document.querySelectorAll('input[type="checkbox"]'))
                if (cb.offsetParent !== null || (cb.offsetWidth === 0 && cb.closest('label, div'))) return 'html';
            return null;
        }""")
        if has:
            checkbox_type = has
            if event_callback:
                await event_callback("Checkbox", "info", f"Checkbox appeared after {i * 0.5}s (type={has})")
            break
        await asyncio.sleep(0.5)
    else:
        if event_callback:
            await event_callback("Checkbox", "info", "No checkbox found — may not be required")
        return True  # No checkbox = nothing to check = proceed

    # Handle regular HTML checkboxes (e.g., "I agree" on some Workday create account pages)
    if checkbox_type == "html":
        try:
            html_cbs = page.locator('input[type="checkbox"]')
            count = await html_cbs.count()
            for idx in range(count):
                cb = html_cbs.nth(idx)
                is_checked = await cb.is_checked()
                if is_checked:
                    continue
                # Try clicking the checkbox directly
                try:
                    await cb.check(force=True, timeout=3000)
                    if event_callback:
                        await event_callback("Checkbox", "success", f"Checked HTML checkbox {idx} via .check()")
                except Exception:
                    pass
                # Verify
                if not await cb.is_checked():
                    # Try clicking the label or parent element
                    try:
                        label = page.locator(f'label[for="{await cb.get_attribute("id")}"]').first
                        if await label.is_visible(timeout=1000):
                            await label.click()
                            if event_callback:
                                await event_callback("Checkbox", "success", f"Checked via label click")
                    except Exception:
                        pass
                if not await cb.is_checked():
                    # Try clicking near the checkbox using mouse
                    try:
                        box = await cb.bounding_box()
                        if box:
                            await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                            await asyncio.sleep(0.5)
                            if event_callback:
                                await event_callback("Checkbox", "info", f"Tried mouse.click on checkbox {idx}")
                    except Exception:
                        pass
                if not await cb.is_checked():
                    # JS fallback
                    await page.evaluate(f"""() => {{
                        const cbs = document.querySelectorAll('input[type="checkbox"]');
                        const cb = cbs[{idx}];
                        if (cb) {{
                            cb.checked = true;
                            cb.dispatchEvent(new Event('change', {{bubbles: true}}));
                            cb.dispatchEvent(new Event('input', {{bubbles: true}}));
                            cb.dispatchEvent(new MouseEvent('click', {{bubbles: true}}));
                        }}
                    }}""")
                    if event_callback:
                        await event_callback("Checkbox", "info", f"Used JS fallback for checkbox {idx}")
            # Verify all checked
            all_checked = await page.evaluate("""() => {
                const cbs = document.querySelectorAll('input[type="checkbox"]');
                for (const cb of cbs) { if (!cb.checked) return false; }
                return true;
            }""")
            if all_checked:
                if event_callback:
                    await event_callback("Checkbox", "success", "All HTML checkboxes checked")
                return True
            else:
                if event_callback:
                    await event_callback("Checkbox", "error", "Some HTML checkboxes still unchecked")
                return False
        except Exception as e:
            if event_callback:
                await event_callback("Checkbox", "error", f"HTML checkbox error: {e}")
            return False

    # Step 2: Wait 2s extra for Workday to attach event handlers
    await asyncio.sleep(2)

    # Step 3: Click the click_filter div INSIDE each unchecked checkbox using REAL
    # Playwright mouse events (isTrusted=true). Verify each one individually.
    try:
        cbs = page.locator('[role="checkbox"]')
        total = await cbs.count()
        for idx in range(total):
            cb = cbs.nth(idx)
            if not await cb.is_visible(timeout=1000):
                continue
            state = await cb.get_attribute("aria-checked")
            if state == "true":
                continue

            checked = False

            # --- ATTEMPT A: Playwright mouse.click on click_filter child ---
            filter_loc = cb.locator('[data-automation-id="click_filter"]')
            try:
                if await filter_loc.count() > 0 and await filter_loc.first.is_visible(timeout=500):
                    box = await filter_loc.first.bounding_box()
                    if box:
                        cx = box['x'] + box['width'] / 2
                        cy = box['y'] + box['height'] / 2
                        # Full pointer event sequence with real coordinates
                        await page.mouse.move(cx, cy)
                        await page.mouse.down()
                        await asyncio.sleep(0.05)
                        await page.mouse.up()
                        await asyncio.sleep(0.8)
                        state = await cb.get_attribute("aria-checked")
                        if state == "true":
                            checked = True
                            if event_callback:
                                await event_callback("Checkbox", "success",
                                    f"Checked via click_filter mouse.click({cx:.0f},{cy:.0f})")
            except Exception:
                pass

            # --- ATTEMPT B: Playwright mouse.click directly on click_filter ---
            if not checked:
                try:
                    if await filter_loc.count() > 0 and await filter_loc.first.is_visible(timeout=500):
                        box = await filter_loc.first.bounding_box()
                        if box:
                            cx = box['x'] + box['width'] / 2
                            cy = box['y'] + box['height'] / 2
                            await page.mouse.click(cx, cy)
                            await asyncio.sleep(1)
                            state = await cb.get_attribute("aria-checked")
                            if state == "true":
                                checked = True
                                if event_callback:
                                    await event_callback("Checkbox", "success",
                                        f"Checked via click_filter page.mouse.click({cx:.0f},{cy:.0f})")
                except Exception:
                    pass

            # --- ATTEMPT C: Playwright mouse.click on the checkbox bounding box center ---
            if not checked:
                try:
                    box = await cb.bounding_box()
                    if box:
                        cx = box['x'] + box['width'] / 2
                        cy = box['y'] + box['height'] / 2
                        await page.mouse.click(cx, cy)
                        await asyncio.sleep(1)
                        state = await cb.get_attribute("aria-checked")
                        if state == "true":
                            checked = True
                            if event_callback:
                                await event_callback("Checkbox", "success",
                                    f"Checked via checkbox mouse.click({cx:.0f},{cy:.0f})")
                except Exception:
                    pass

            # --- ATTEMPT D: JS dispatchEvent fallback with full pointer sequence ---
            if not checked:
                try:
                    js_result = await page.evaluate("""(idx) => {
                        const cbs = Array.from(document.querySelectorAll('[role="checkbox"]'))
                            .filter(el => el.offsetParent !== null);
                        const cb = cbs[idx];
                        if (!cb || cb.getAttribute('aria-checked') === 'true') return 'skip';
                        const filter = cb.querySelector('[data-automation-id="click_filter"]');
                        const target = filter || cb.querySelector('div') || cb;
                        const rect = target.getBoundingClientRect();
                        const x = rect.left + rect.width / 2;
                        const y = rect.top + rect.height / 2;
                        const opts = {bubbles: true, cancelable: true, composed: true, view: window,
                                      button: 0, buttons: 1, clientX: x, clientY: y,
                                      screenX: x, screenY: y, pointerId: 1};
                        target.dispatchEvent(new PointerEvent('pointerdown', opts));
                        target.dispatchEvent(new MouseEvent('mousedown', opts));
                        target.dispatchEvent(new PointerEvent('pointerup', opts));
                        target.dispatchEvent(new MouseEvent('mouseup', opts));
                        target.dispatchEvent(new MouseEvent('click', opts));
                        return cb.getAttribute('aria-checked');
                    }""", idx)
                    if js_result == "true":
                        checked = True
                        if event_callback:
                            await event_callback("Checkbox", "success", "Checked via JS dispatchEvent fallback")
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

            if not checked:
                if event_callback:
                    await event_callback("Checkbox", "error",
                        f"Checkbox {idx} still unchecked after all attempts")

    except Exception as e:
        if event_callback:
            await event_callback("Checkbox", "info", f"Checkbox click error: {e}")

    # Step 4: Verify all checkboxes are checked BEFORE allowing Create Account click
    await asyncio.sleep(0.5)
    still_unchecked = await page.evaluate("""() =>
        Array.from(document.querySelectorAll('[role="checkbox"][aria-checked="false"]'))
            .filter(el => el.offsetParent !== null).length
    """)

    if still_unchecked == 0:
        if event_callback:
            await event_callback("Checkbox", "success", "All Workday checkboxes confirmed checked")
        return True
    else:
        if event_callback:
            await event_callback("Checkbox", "error",
                f"{still_unchecked} checkbox(es) still unchecked. Please check manually, then click Continue.")
        return False


# Keep old name as alias for backwards compatibility
check_workday_checkbox = check_workday_consent


async def _verify_upload(page: Page) -> str:
    """Check if a file upload succeeded. Returns filename or empty string."""
    return await page.evaluate("""() => {
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


async def upload_file_robust(page: Page, file_path: str, event_callback=None) -> bool:
    """Upload a file using multiple strategies. Works on Workday, Greenhouse, Lever."""
    if not file_path or not os.path.exists(file_path):
        if event_callback:
            await event_callback("Upload", "error", f"File not found: {file_path}")
        return False

    abs_path = os.path.abspath(file_path)
    fname = os.path.basename(abs_path)
    if event_callback:
        await event_callback("Upload", "info", f"Uploading: {fname}")

    # Check if already uploaded
    already = await _verify_upload(page)
    if already:
        if event_callback:
            await event_callback("Upload", "info", f"Already uploaded: {already}")
        return True

    # STRATEGY 1: Make hidden file inputs visible (+ parents), set_input_files, dispatch change+input
    try:
        count = await page.evaluate("""() => {
            const inputs = document.querySelectorAll('input[type="file"]');
            for (const inp of inputs) {
                // Unhide the input itself
                inp.removeAttribute('hidden');
                inp.style.cssText = 'display:block!important;visibility:visible!important;opacity:1!important;position:relative!important;width:200px!important;height:30px!important;z-index:99999!important;';
                // Unhide parent containers (Greenhouse wraps in hidden divs)
                let el = inp.parentElement;
                for (let depth = 0; el && depth < 5; depth++, el = el.parentElement) {
                    const s = getComputedStyle(el);
                    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') {
                        el.style.display = 'block';
                        el.style.visibility = 'visible';
                        el.style.opacity = '1';
                    }
                    if (el.hasAttribute('hidden')) el.removeAttribute('hidden');
                }
                // Remove accept restriction so any file type works
                if (inp.hasAttribute('accept')) inp.removeAttribute('accept');
            }
            return inputs.length;
        }""")
        if count > 0:
            if event_callback:
                await event_callback("Upload", "info", f"Strategy 1: Found {count} file input(s), setting files...")
            await asyncio.sleep(0.5)
            fi = page.locator('input[type="file"]').first
            await fi.set_input_files(abs_path, timeout=10000)
            if event_callback:
                await event_callback("Upload", "info", "Strategy 1: set_input_files succeeded, dispatching events...")
            await asyncio.sleep(1)
            await page.evaluate("""() => {
                for (const inp of document.querySelectorAll('input[type="file"]')) {
                    if (inp.files && inp.files.length > 0) {
                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                        inp.dispatchEvent(new Event('input', {bubbles: true}));
                    }
                }
            }""")
            await asyncio.sleep(4)
            has = await _verify_upload(page)
            if has:
                if event_callback:
                    await event_callback("Upload", "success", f"Strategy 1 (file input): {has}")
                return True
            else:
                # set_input_files succeeded without error — trust it even if verify fails
                # (Greenhouse/Lever React apps may not update DOM immediately)
                if event_callback:
                    await event_callback("Upload", "success", f"Strategy 1 (file input): set_input_files OK (verify pending)")
                return True
        else:
            if event_callback:
                await event_callback("Upload", "info", "Strategy 1: No file inputs found on page")
    except Exception as e:
        if event_callback:
            import traceback as _tb
            await event_callback("Upload", "info", f"Strategy 1 failed: {e}\n{_tb.format_exc()[:300]}")

    # STRATEGY 2: Click "Select files" / "Attach" link + file chooser
    for sel in [
        '[data-automation-id="file-upload-drop-zone"] a',
        'a:has-text("Select files")', 'button:has-text("Select files")',
        'a:has-text("Attach")', 'button:has-text("Attach")',
        'a:has-text("Upload")', 'button:has-text("Upload")',
        'a:has-text("Choose File")', 'button:has-text("Choose File")',
        'label:has-text("Attach")', 'label:has-text("Upload")',
        'a.attachment-link', '[class*="upload"] a', '[class*="upload"] button',
    ]:
        try:
            link = page.locator(sel).first
            if not await link.is_visible(timeout=1500):
                continue
            async with page.expect_file_chooser(timeout=8000) as fc:
                await link.click(force=True, timeout=5000)
            chooser = await fc.value
            await chooser.set_files(abs_path)
            await asyncio.sleep(5)
            has = await _verify_upload(page)
            if event_callback:
                await event_callback("Upload", "success", f"Strategy 2 (file chooser): {sel[:40]}" + (f" ({has})" if has else ""))
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
            zone = page.locator(sel).first
            if not await zone.is_visible(timeout=1500):
                continue
            async with page.expect_file_chooser(timeout=8000) as fc:
                await zone.click(force=True, timeout=5000)
            chooser = await fc.value
            await chooser.set_files(abs_path)
            await asyncio.sleep(5)
            has = await _verify_upload(page)
            if event_callback:
                await event_callback("Upload", "success", f"Strategy 3 (drop zone): {sel[:40]}" + (f" ({has})" if has else ""))
            return True
        except Exception:
            continue

    # STRATEGY 4: Programmatic drag-and-drop via JS
    try:
        import base64
        with open(abs_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        dropped = await page.evaluate("""(args) => {
            const [b64, name] = args;
            const bin = atob(b64);
            const bytes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            const ext = name.split('.').pop().toLowerCase();
            const mimeMap = {pdf:'application/pdf', doc:'application/msword', docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document', txt:'text/plain', rtf:'application/rtf'};
            const mime = mimeMap[ext] || 'application/octet-stream';
            const file = new File([bytes], name, {type: mime});
            const dt = new DataTransfer();
            dt.items.add(file);

            // Try setting file input directly
            let inputOk = false;
            const fi = document.querySelector('input[type="file"]');
            if (fi) {
                fi.files = dt.files;
                fi.dispatchEvent(new Event('change', {bubbles: true}));
                fi.dispatchEvent(new Event('input', {bubbles: true}));
                inputOk = fi.files.length > 0;
            }

            // Try drag-drop on all candidate zones
            let dropOk = false;
            const zoneSels = [
                '[data-automation-id="file-upload-drop-zone"]',
                '[class*="dropzone"]', '[class*="drop-zone"]',
                '[class*="file-upload"]', '[class*="resume-upload"]',
            ];
            for (const zs of zoneSels) {
                const zone = document.querySelector(zs);
                if (!zone) continue;
                const rect = zone.getBoundingClientRect();
                const cx = rect.left + rect.width / 2;
                const cy = rect.top + rect.height / 2;
                const evtInit = {dataTransfer: dt, bubbles: true, cancelable: true, clientX: cx, clientY: cy};
                zone.dispatchEvent(new DragEvent('dragenter', evtInit));
                zone.dispatchEvent(new DragEvent('dragover', evtInit));
                zone.dispatchEvent(new DragEvent('drop', evtInit));
                dropOk = true;
            }

            return inputOk || dropOk ? 'ok' : 'no target';
        }""", [b64, fname])
        if dropped == "ok":
            await asyncio.sleep(5)
            result = await _verify_upload(page)
            if result:
                if event_callback:
                    await event_callback("Upload", "success", f"Strategy 4 (drag-drop): {result}")
                return True
    except Exception as e:
        if event_callback:
            await event_callback("Upload", "info", f"Strategy 4 failed: {e}")

    if event_callback:
        await event_callback("Upload", "error", "All upload strategies failed. Upload manually in browser.")
    return False


def _load_personal_info() -> dict:
    p = Path(__file__).parent.parent / "personal_info.yaml"
    if p.exists():
        with open(p) as f:
            return yaml.safe_load(f) or {}
    return {}


async def detect_workday_step(page: Page) -> str:
    """Detect which step of the Workday wizard we're on."""
    return await page.evaluate("""() => {
        const active = document.querySelector('[data-automation-id="progressBarActiveStep"]');
        if (active) return active.innerText.trim();
        const body = document.body.innerText.toLowerCase();
        if (body.includes('my information') && (body.includes('first name') || body.includes('email'))) return 'My Information';
        if (body.includes('my experience') && (body.includes('work experience') || body.includes('education'))) return 'My Experience';
        if (body.includes('application questions')) return 'Application Questions';
        if (body.includes('voluntary disclosure')) return 'Voluntary Disclosures';
        if (body.includes('self identify') || body.includes('self-identify')) return 'Self Identify';
        if (body.includes('review') && body.includes('submit')) return 'Review';
        return 'Unknown';
    }""")


async def click_next(page: Page, event_callback=None) -> bool:
    """Click Save and Continue / Next button."""
    for sel in [
        '[data-automation-id="bottom-navigation-next-button"]',
        '[data-automation-id="pageFooterNextButton"]',
        'button:has-text("Save and Continue")',
        'button:has-text("Next")',
        'button:has-text("Continue")',
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.scroll_into_view_if_needed(timeout=3000)
                await asyncio.sleep(0.5)
                try:
                    await btn.click(timeout=5000)
                except Exception:
                    await page.evaluate("""(s) => {
                        const el = document.querySelector(s);
                        if (el) el.click();
                    }""", sel)
                await asyncio.sleep(3)
                if event_callback:
                    await event_callback("Navigate", "info", "Clicked Next/Continue")
                return True
        except Exception:
            continue

    if event_callback:
        await event_callback("Navigate", "warning", "Could not find Next button")
    return False


async def handle_my_experience(page: Page, resume_path: str, event_callback=None, screenshot_callback=None) -> dict:
    """Handle My Experience: upload resume, add education, skip work exp."""
    info = _load_personal_info()
    filled = 0
    errors = []

    if event_callback:
        await event_callback("Fill Form", "info", "Workday: My Experience - uploading resume + adding education")

    # --- Resume Upload ---
    if resume_path and os.path.exists(resume_path):
        # Scroll to Resume/CV section
        await page.evaluate("""() => {
            for (const h of document.querySelectorAll('h3, h4, [class*="header"]')) {
                if (h.innerText.toLowerCase().includes('resume') || h.innerText.toLowerCase().includes('cv')) {
                    h.scrollIntoView({behavior: 'smooth', block: 'center'});
                    return;
                }
            }
            window.scrollTo(0, document.body.scrollHeight / 2);
        }""")
        await asyncio.sleep(1)

        if await upload_file_robust(page, resume_path, event_callback):
            filled += 1
            if event_callback:
                await event_callback("Upload", "info", "Waiting for Workday to parse resume...")
            await asyncio.sleep(5)
        else:
            errors.append("Resume upload failed on My Experience")

    # --- Education ---
    if event_callback:
        await event_callback("Fill Form", "info", "Adding education entry...")

    try:
        # Find and click Education Add button
        edu_clicked = await page.evaluate("""() => {
            // Look for section headers containing "Education"
            const els = document.querySelectorAll('h3, h4, [class*="header"], [data-automation-id*="section"]');
            for (const el of els) {
                if (el.innerText.toLowerCase().includes('education')) {
                    const section = el.closest('[data-automation-id]') || el.parentElement?.parentElement || el.parentElement;
                    if (section) {
                        const btn = section.querySelector('button');
                        if (btn) { btn.click(); return true; }
                    }
                }
            }
            // Fallback: look for Add button with data-automation-id
            const addBtns = document.querySelectorAll('[data-automation-id="Add"], button:has(span)');
            for (const btn of addBtns) {
                if (btn.innerText.trim().toLowerCase() === 'add') {
                    // Check if near Education text
                    const prev = btn.parentElement?.previousElementSibling;
                    if (prev && prev.innerText.toLowerCase().includes('education')) {
                        btn.click(); return true;
                    }
                }
            }
            return false;
        }""")

        if edu_clicked:
            await asyncio.sleep(3.0)

            # Fill education fields
            school = info.get('school', 'Santa Clara University')
            degree = info.get('degree', "Bachelor's Degree")
            gpa = str(info.get('gpa', '3.78'))

            # School - often a search/autocomplete field
            for sel in ['[data-automation-id="school"]', 'input[aria-label*="School" i]', 'input[placeholder*="Search" i]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click(timeout=2000)
                        await page.keyboard.type(school, delay=50)
                        await asyncio.sleep(1.5)
                        # Click first autocomplete suggestion
                        try:
                            opt = page.locator('[role="option"], li[class*="option"]').first
                            if await opt.is_visible(timeout=2000):
                                await opt.click(timeout=3000)
                        except Exception:
                            await page.keyboard.press("Enter")
                        filled += 1
                        if event_callback:
                            await event_callback("Fill Form", "info", f"Filled school: {school}")
                        break
                except Exception:
                    continue

            # Degree dropdown
            for sel in ['[data-automation-id="degree"] button', 'button[aria-label*="Degree" i]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click(timeout=3000)
                        await asyncio.sleep(1.0)
                        # Type to filter
                        await page.keyboard.type("Bachelor", delay=50)
                        await asyncio.sleep(0.8)
                        opt = page.locator('[role="option"]:has-text("Bachelor"), li:has-text("Bachelor")').first
                        if await opt.is_visible(timeout=2000):
                            await opt.click(timeout=3000)
                            filled += 1
                            if event_callback:
                                await event_callback("Fill Form", "info", f"Selected degree: {degree}")
                        break
                except Exception:
                    continue

            # GPA
            for sel in ['[data-automation-id="gpa"]', 'input[aria-label*="GPA" i]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.fill(gpa, timeout=3000)
                        filled += 1
                        if event_callback:
                            await event_callback("Fill Form", "info", f"Filled GPA: {gpa}")
                        break
                except Exception:
                    continue

            # Save education entry
            for sel in ['button:has-text("Save")', 'button:has-text("Apply")', 'button:has-text("Done")', 'button:has-text("OK")']:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=1000):
                        await btn.click(timeout=3000)
                        await asyncio.sleep(2.0)
                        if event_callback:
                            await event_callback("Fill Form", "info", "Saved education entry")
                        break
                except Exception:
                    continue
        else:
            if event_callback:
                await event_callback("Fill Form", "info", "Education Add button not found, skipping")

    except Exception as e:
        if event_callback:
            await event_callback("Fill Form", "info", f"Education error: {e}")

    # --- Websites (LinkedIn/GitHub) ---
    try:
        linkedin = info.get('linkedin', '')
        if linkedin:
            websites_clicked = await page.evaluate("""() => {
                const els = document.querySelectorAll('h3, h4, [class*="header"]');
                for (const el of els) {
                    if (el.innerText.toLowerCase().includes('website')) {
                        const section = el.closest('[data-automation-id]') || el.parentElement;
                        const btn = section?.querySelector('button');
                        if (btn) { btn.click(); return true; }
                    }
                }
                return false;
            }""")
            if websites_clicked:
                await asyncio.sleep(2.0)
                for sel in ['[data-automation-id="website"]', 'input[aria-label*="URL" i]', 'input[placeholder*="http" i]']:
                    try:
                        el = page.locator(sel).first
                        if await el.is_visible(timeout=2000):
                            await el.fill(linkedin, timeout=3000)
                            filled += 1
                            break
                    except Exception:
                        continue
                # Save
                for sel in ['button:has-text("Save")', 'button:has-text("Apply")', 'button:has-text("Done")']:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=1000):
                            await btn.click(timeout=3000)
                            await asyncio.sleep(1.0)
                            break
                    except Exception:
                        continue
    except Exception:
        pass

    if event_callback:
        await event_callback("Fill Form", "info", f"My Experience done: {filled} filled, {len(errors)} errors")

    return {"filled": filled, "failed": len(errors), "skipped": 0, "errors": errors}


async def handle_eeo_page(page: Page, event_callback=None, screenshot_callback=None) -> dict:
    """Handle Voluntary Disclosures / Self Identify / EEO pages."""
    info = _load_personal_info_wd()
    filled = 0

    if event_callback:
        await event_callback("Fill Form", "info", "Workday: EEO/Disclosures page (hardcoded, no LLM)")

    # Try clicking specific EEO radio/dropdown options
    eeo_values = [
        info.get('gender', 'Male'),
        info.get('race_ethnicity', 'Asian'),
        info.get('veteran_status', 'I am not a protected veteran'),
        info.get('disability_status', 'I do not wish to answer'),
    ]
    for value in eeo_values:
        try:
            opt = page.locator(f'label:has-text("{value}"), [role="radio"]:has-text("{value}"), [role="option"]:has-text("{value}")').first
            if await opt.is_visible(timeout=1000):
                await opt.click(timeout=3000)
                filled += 1
        except Exception:
            pass

    return {"filled": filled, "failed": 0, "skipped": 0, "errors": []}


async def handle_workday_application(
    page: Page,
    resume_path: str,
    company: str,
    role: str,
    job_description: str,
    event_callback=None,
    screenshot_callback=None,
    max_steps: int = 8,
) -> dict:
    """Main Workday orchestrator. Detects step, handles it, clicks Next, repeats."""
    total_filled = 0
    total_failed = 0
    all_errors = []

    for step_num in range(max_steps):
        await asyncio.sleep(2.0)

        # Dismiss any unexpected dialogs/panels (e.g., "Change Email" panel)
        try:
            # First try: find any close button in a panel containing "change email" or "verify"
            close_info = await page.evaluate("""() => {
                // Strategy 1: Find close/X buttons near "Change Email" text
                const closeSelectors = [
                    '[data-automation-id="closeButton"]',
                    'button[aria-label="Close"]',
                    'button[aria-label="close"]',
                    '[data-automation-id="panelCloseButton"]',
                    '.css-1fqoep4 button[type="button"]',
                    'button.css-1qi9rcf',
                    'div[role="dialog"] button[type="button"]',
                ];
                for (const sel of closeSelectors) {
                    for (const btn of document.querySelectorAll(sel)) {
                        if (btn.offsetParent === null) continue;
                        // Check if this button's ancestor contains "change email" text
                        const ancestor = btn.closest('[role="dialog"], [data-automation-id="panel"], [class*="Panel"], [class*="panel"], [class*="flyout"], [class*="sidebar"], [class*="drawer"]')
                                      || btn.parentElement?.parentElement;
                        if (ancestor) {
                            const text = (ancestor.innerText || '').toLowerCase();
                            if (text.includes('change email') || text.includes('verify it') || text.includes('new email')) {
                                const r = btn.getBoundingClientRect();
                                return {x: r.x + r.width / 2, y: r.y + r.height / 2, found: true};
                            }
                        }
                    }
                }
                // Strategy 2: Look for any visible X icon near top-right of a visible panel
                for (const btn of document.querySelectorAll('button, [role="button"]')) {
                    if (btn.offsetParent === null) continue;
                    const text = (btn.textContent || '').trim();
                    const label = btn.getAttribute('aria-label') || '';
                    if (text === '×' || text === 'X' || text === '✕' || label.toLowerCase() === 'close') {
                        const r = btn.getBoundingClientRect();
                        if (r.x > 800) {  // Right side of viewport = likely a panel close button
                            return {x: r.x + r.width / 2, y: r.y + r.height / 2, found: true};
                        }
                    }
                }
                return {found: false};
            }""")
            if close_info and close_info.get("found"):
                # Use real mouse click for Workday compatibility
                await page.mouse.click(close_info["x"], close_info["y"])
                await asyncio.sleep(1.0)
                if event_callback:
                    await event_callback("Navigate", "info", "Dismissed Change Email panel")
        except Exception:
            pass

        if screenshot_callback:
            try:
                ss = await page.screenshot(type="png")
                await screenshot_callback(ss)
            except Exception:
                pass

        current_step = await detect_workday_step(page)
        if event_callback:
            await event_callback("Navigate", "info", f"Workday step {step_num + 1}: {current_step}")

        if "review" in current_step.lower():
            if event_callback:
                await event_callback("Navigate", "success", "Reached Review page. Stopping before Submit.")
            break

        result = {"filled": 0, "failed": 0, "errors": []}

        if "information" in current_step.lower():
            # Use hardcoded field filler -- bypasses LLM entirely
            result = await fill_workday_info_hardcoded(page, event_callback)

            # Handle "How Did You Hear About Us?" promptList dropdown
            try:
                await _fill_workday_prompt_dropdown(page, event_callback)
            except Exception as e:
                if event_callback:
                    await event_callback("Fill Form", "warn", f"How Did You Hear handler: {str(e)[:80]}")


        elif "experience" in current_step.lower():
            result = await handle_my_experience(page, resume_path, event_callback, screenshot_callback)

        elif "question" in current_step.lower():
            # Scroll to reveal all fields
            for i in range(5):
                await page.evaluate(f"window.scrollTo(0, {i * 500})")
                await asyncio.sleep(0.2)
            await page.evaluate("window.scrollTo(0, 0)")
            result = await fill_workday_questions_hardcoded(page, company, role, event_callback)

        elif "disclos" in current_step.lower() or "identify" in current_step.lower():
            result = await handle_eeo_page(page, event_callback, screenshot_callback)

        else:
            # Unknown step -- try hardcoded questions handler
            if event_callback:
                await event_callback("Fill Form", "info", f"Unknown step '{current_step}', trying hardcoded filler")
            result = await fill_workday_questions_hardcoded(page, company, role, event_callback)

        total_filled += result.get("filled", 0)
        total_failed += result.get("failed", 0)
        all_errors.extend(result.get("errors", []))

        # Click Next
        if not await click_next(page, event_callback):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.0)
            if not await click_next(page, event_callback):
                all_errors.append(f"Stuck on step: {current_step}")
                break

        await asyncio.sleep(3.0)

    return {"filled": total_filled, "failed": total_failed, "skipped": 0, "errors": all_errors}

"""
Dedicated handler for Workday multi-step job applications.

Steps: My Information → My Experience → Application Questions →
       Voluntary Disclosures → Self Identify → Review
"""
import asyncio
import os
import yaml
from pathlib import Path
from playwright.async_api import Page


async def check_workday_consent(page: Page, event_callback=None, max_wait_seconds=15) -> bool:
    """Check Workday consent checkboxes by clicking the click_filter overlay inside them.

    Workday puts a transparent div[data-automation-id="click_filter"] on top of every
    interactive element. This IS the intended click target — Workday's event delegation
    listens for clicks on click_filter and updates framework state. Clicking the checkbox
    element directly bypasses this and the state never changes.
    """

    if event_callback:
        await event_callback("Checkbox", "info", "Waiting for consent checkbox...")

    # Step 1: Wait for checkbox to appear
    for i in range(max_wait_seconds * 2):
        has = await page.evaluate("""() => {
            for (const cb of document.querySelectorAll('[role="checkbox"]'))
                if (cb.offsetParent !== null) return true;
            return false;
        }""")
        if has:
            if event_callback:
                await event_callback("Checkbox", "info", f"Checkbox appeared after {i * 0.5}s")
            break
        await asyncio.sleep(0.5)
    else:
        if event_callback:
            await event_callback("Checkbox", "info", "No checkbox found — may not be required")
        return True  # No checkbox = nothing to check = proceed

    # Step 2: Wait for Workday to finish rendering + attach event handlers
    await asyncio.sleep(3)

    # Step 3: Click the click_filter INSIDE each unchecked checkbox
    result = await page.evaluate("""() => {
        const results = [];
        for (const cb of document.querySelectorAll('[role="checkbox"]')) {
            if (cb.offsetParent === null) continue;
            if (cb.getAttribute('aria-checked') === 'true') continue;

            // Find the click_filter overlay inside this checkbox
            const filter = cb.querySelector('[data-automation-id="click_filter"]');
            const target = filter || cb.querySelector('div');
            if (!target) continue;

            const rect = target.getBoundingClientRect();
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;
            const opts = {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, screenX: x, screenY: y, pointerId: 1};

            target.dispatchEvent(new PointerEvent('pointerdown', opts));
            target.dispatchEvent(new MouseEvent('mousedown', opts));
            target.dispatchEvent(new PointerEvent('pointerup', opts));
            target.dispatchEvent(new MouseEvent('mouseup', opts));
            target.dispatchEvent(new MouseEvent('click', opts));

            results.push({
                method: filter ? 'click_filter' : 'child_div',
                dataId: cb.getAttribute('data-automation-id') || '',
                after: cb.getAttribute('aria-checked'),
            });
        }
        return results;
    }""")

    if event_callback:
        await event_callback("Checkbox", "info", f"Click filter results: {result}")

    # Step 4: Wait for framework to process
    await asyncio.sleep(1)

    # Step 5: Verify
    unchecked = await page.evaluate("""() =>
        Array.from(document.querySelectorAll('[role="checkbox"][aria-checked="false"]'))
            .filter(el => el.offsetParent !== null).length
    """)

    if unchecked == 0:
        if event_callback:
            await event_callback("Checkbox", "success", "All checkboxes confirmed checked")
        return True

    # Step 6: Fallback — Playwright mouse.click at exact center coordinates
    if event_callback:
        await event_callback("Checkbox", "info", "click_filter didn't work, trying coordinate click...")

    try:
        cbs = page.locator('[role="checkbox"][aria-checked="false"]')
        count = await cbs.count()
        for i in range(count):
            cb = cbs.nth(i)
            if await cb.is_visible(timeout=1000):
                # Try clicking the click_filter child specifically via Playwright mouse.click
                filter_div = page.locator('[role="checkbox"][aria-checked="false"]').nth(i).locator('[data-automation-id="click_filter"]')
                try:
                    if await filter_div.is_visible(timeout=500):
                        box = await filter_div.bounding_box()
                        if box:
                            cx = box['x'] + box['width'] / 2
                            cy = box['y'] + box['height'] / 2
                            await page.mouse.click(cx, cy)
                            await asyncio.sleep(1)
                            state = await cb.get_attribute("aria-checked")
                            if state == "true":
                                if event_callback:
                                    await event_callback("Checkbox", "success", f"Checked via click_filter mouse.click({cx:.0f},{cy:.0f})")
                                continue
                except Exception:
                    pass

                # Fallback: click checkbox itself
                box = await cb.bounding_box()
                if box:
                    cx = box['x'] + box['width'] / 2
                    cy = box['y'] + box['height'] / 2
                    await page.mouse.click(cx, cy)
                    await asyncio.sleep(1)
                    state = await cb.get_attribute("aria-checked")
                    if state == "true":
                        if event_callback:
                            await event_callback("Checkbox", "success", f"Checked via mouse.click({cx:.0f},{cy:.0f})")
    except Exception as e:
        if event_callback:
            await event_callback("Checkbox", "info", f"Coordinate click error: {e}")

    # Step 7: Final check
    await asyncio.sleep(0.5)
    still_unchecked = await page.evaluate("""() =>
        Array.from(document.querySelectorAll('[role="checkbox"][aria-checked="false"]'))
            .filter(el => el.offsetParent !== null).length
    """)

    if still_unchecked == 0:
        if event_callback:
            await event_callback("Checkbox", "success", "All Workday checkboxes checked")
        return True
    else:
        if event_callback:
            await event_callback("Checkbox", "error",
                f"{still_unchecked} checkbox(es) still unchecked. Please check manually, then click Continue.")
        return False


# Keep old name as alias for backwards compatibility
check_workday_checkbox = check_workday_consent


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
    already = await page.evaluate("""() => {
        for (const sel of ['.filename','.file-name','[class*="upload-success"]','[data-automation-id="file-upload-item"]','[class*="attachment"]']) {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null && el.innerText.trim()) return el.innerText.trim();
        }
        for (const fi of document.querySelectorAll('input[type="file"]')) {
            if (fi.files && fi.files.length > 0) return fi.files[0].name;
        }
        return '';
    }""")
    if already:
        if event_callback:
            await event_callback("Upload", "info", f"Already uploaded: {already}")
        return True

    # STRATEGY 1: Make hidden file inputs visible, set_input_files, dispatch change
    try:
        count = await page.evaluate("""() => {
            const inputs = document.querySelectorAll('input[type="file"]');
            for (const inp of inputs) {
                inp.style.cssText = 'display:block!important;visibility:visible!important;opacity:1!important;position:relative!important;width:200px!important;height:30px!important;z-index:99999!important;';
            }
            return inputs.length;
        }""")
        if count > 0:
            await asyncio.sleep(0.5)
            fi = page.locator('input[type="file"]').first
            await fi.set_input_files(abs_path, timeout=10000)
            await asyncio.sleep(1)
            await page.evaluate("""() => {
                for (const inp of document.querySelectorAll('input[type="file"]')) {
                    if (inp.files && inp.files.length > 0) {
                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                        inp.dispatchEvent(new Event('input', {bubbles: true}));
                    }
                }
            }""")
            await asyncio.sleep(3)
            has = await page.evaluate("() => { for (const i of document.querySelectorAll('input[type=\"file\"]')) { if (i.files && i.files.length > 0) return i.files[0].name; } return ''; }")
            if has:
                # Final verification: check for upload confirmation UI
                verify = await page.evaluate("""() => {
                    for (const sel of ['.filename', '.file-name', '.resume-filename', '.attachment-filename', '[data-automation-id="file-upload-item"]', '[class*="upload-success"]', '[class*="attachment"]']) {
                        const el = document.querySelector(sel);
                        if (el && el.offsetParent !== null && el.innerText.trim()) return el.innerText.trim();
                    }
                    return '';
                }""")
                if event_callback:
                    await event_callback("Upload", "success", f"Uploaded via file input: {has}" + (f" (confirmed: {verify})" if verify else ""))
                return True
    except Exception as e:
        if event_callback:
            await event_callback("Upload", "info", f"Strategy 1 failed: {e}")

    # STRATEGY 2: Click "Select files" / "Attach" link + file chooser
    for sel in [
        '[data-automation-id="file-upload-drop-zone"] a',
        'a:has-text("Select files")', 'button:has-text("Select files")',
        'a:has-text("Attach")', 'button:has-text("Attach")',
        'a:has-text("Upload")', 'button:has-text("Upload")',
        'a:has-text("Choose File")', 'label:has-text("Attach")',
        'a.attachment-link',
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
            # Final verification: check for upload confirmation UI
            verify = await page.evaluate("""() => {
                for (const s of ['.filename', '.file-name', '.resume-filename', '.attachment-filename', '[data-automation-id="file-upload-item"]', '[class*="upload-success"]', '[class*="attachment"]']) {
                    const e = document.querySelector(s);
                    if (e && e.offsetParent !== null && e.innerText.trim()) return e.innerText.trim();
                }
                return '';
            }""")
            if event_callback:
                await event_callback("Upload", "success", f"Uploaded via file chooser: {sel[:40]}" + (f" (confirmed: {verify})" if verify else ""))
            return True
        except Exception:
            continue

    # STRATEGY 3: Click drop zone + file chooser
    for sel in ['[data-automation-id="file-upload-drop-zone"]', '[class*="dropzone"]', '[class*="drop-zone"]']:
        try:
            zone = page.locator(sel).first
            if not await zone.is_visible(timeout=1500):
                continue
            async with page.expect_file_chooser(timeout=8000) as fc:
                await zone.click(force=True, timeout=5000)
            chooser = await fc.value
            await chooser.set_files(abs_path)
            await asyncio.sleep(5)
            # Final verification: check for upload confirmation UI
            verify = await page.evaluate("""() => {
                for (const s of ['.filename', '.file-name', '.resume-filename', '.attachment-filename', '[data-automation-id="file-upload-item"]', '[class*="upload-success"]', '[class*="attachment"]']) {
                    const e = document.querySelector(s);
                    if (e && e.offsetParent !== null && e.innerText.trim()) return e.innerText.trim();
                }
                return '';
            }""")
            if event_callback:
                await event_callback("Upload", "success", f"Uploaded via drop zone: {sel[:40]}" + (f" (confirmed: {verify})" if verify else ""))
            return True
        except Exception:
            continue

    # STRATEGY 4: Programmatic drag-and-drop
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
            const file = new File([bytes], name, {type: mimeMap[ext] || 'application/octet-stream'});
            const dt = new DataTransfer();
            dt.items.add(file);
            const fi = document.querySelector('input[type="file"]');
            if (fi) { fi.files = dt.files; fi.dispatchEvent(new Event('change', {bubbles: true})); }
            const zone = document.querySelector('[data-automation-id="file-upload-drop-zone"], [class*="dropzone"]');
            if (zone) {
                zone.dispatchEvent(new DragEvent('dragenter', {dataTransfer: dt, bubbles: true}));
                zone.dispatchEvent(new DragEvent('dragover', {dataTransfer: dt, bubbles: true}));
                zone.dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true}));
            }
            return fi || zone ? 'ok' : 'no target';
        }""", [b64, fname])
        if dropped == "ok":
            await asyncio.sleep(5)
            result = await page.evaluate("() => { for (const s of ['.filename','.file-name','.resume-filename','.attachment-filename','[data-automation-id=\"file-upload-item\"]','[class*=\"upload-success\"]','[class*=\"attachment\"]']) { const e = document.querySelector(s); if (e && e.offsetParent !== null && e.innerText.trim()) return e.innerText.trim(); } return ''; }")
            if result:
                if event_callback:
                    await event_callback("Upload", "success", f"Drag-drop worked: {result}")
                return True
    except Exception as e:
        if event_callback:
            await event_callback("Upload", "info", f"Drag-drop failed: {e}")

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
                await page.wait_for_timeout(500)
                try:
                    await btn.click(timeout=5000)
                except Exception:
                    await page.evaluate("""(s) => {
                        const el = document.querySelector(s);
                        if (el) el.click();
                    }""", sel)
                await page.wait_for_timeout(3000)
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
            await page.wait_for_timeout(3000)

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
                        await page.wait_for_timeout(1500)
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
                        await page.wait_for_timeout(1000)
                        # Type to filter
                        await page.keyboard.type("Bachelor", delay=50)
                        await page.wait_for_timeout(800)
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
                        await page.wait_for_timeout(2000)
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
                await page.wait_for_timeout(2000)
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
                            await page.wait_for_timeout(1000)
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
    info = _load_personal_info()
    filled = 0

    if event_callback:
        await event_callback("Fill Form", "info", "Workday: EEO/Disclosures page")

    # Try generic form filler first
    try:
        from applicator.form_filler import JS_EXTRACT_FIELDS, map_fields_to_profile, fill_form
        fields = await page.evaluate(JS_EXTRACT_FIELDS)
        if fields:
            mappings = map_fields_to_profile(fields, "", "", "")
            result = await fill_form(page, mappings, "", event_callback=event_callback, screenshot_page=page)
            filled = result.get("filled", 0)
    except Exception:
        pass

    # Also try clicking specific EEO radio/dropdown options
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
        await page.wait_for_timeout(2000)

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
            from applicator.form_filler import JS_EXTRACT_FIELDS, map_fields_to_profile, fill_form
            fields = await page.evaluate(JS_EXTRACT_FIELDS)
            if fields:
                mappings = map_fields_to_profile(fields, job_description, company, role)
                result = await fill_form(page, mappings, resume_path, event_callback=event_callback, screenshot_page=page)

        elif "experience" in current_step.lower():
            result = await handle_my_experience(page, resume_path, event_callback, screenshot_callback)

        elif "question" in current_step.lower():
            from applicator.form_filler import JS_EXTRACT_FIELDS, map_fields_to_profile, fill_form
            for i in range(5):
                await page.evaluate(f"window.scrollTo(0, {i * 500})")
                await page.wait_for_timeout(200)
            await page.evaluate("window.scrollTo(0, 0)")
            fields = await page.evaluate(JS_EXTRACT_FIELDS)
            if fields:
                mappings = map_fields_to_profile(fields, job_description, company, role)
                result = await fill_form(page, mappings, resume_path, event_callback=event_callback, screenshot_page=page)

        elif "disclos" in current_step.lower() or "identify" in current_step.lower():
            result = await handle_eeo_page(page, event_callback, screenshot_callback)

        else:
            # Unknown — try generic
            if event_callback:
                await event_callback("Fill Form", "info", f"Unknown step '{current_step}', trying generic filler")
            from applicator.form_filler import JS_EXTRACT_FIELDS, map_fields_to_profile, fill_form
            fields = await page.evaluate(JS_EXTRACT_FIELDS)
            if fields:
                mappings = map_fields_to_profile(fields, job_description, company, role)
                result = await fill_form(page, mappings, resume_path, event_callback=event_callback, screenshot_page=page)

        total_filled += result.get("filled", 0)
        total_failed += result.get("failed", 0)
        all_errors.extend(result.get("errors", []))

        # Click Next
        if not await click_next(page, event_callback):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)
            if not await click_next(page, event_callback):
                all_errors.append(f"Stuck on step: {current_step}")
                break

        await page.wait_for_timeout(3000)

    return {"filled": total_filled, "failed": total_failed, "skipped": 0, "errors": all_errors}

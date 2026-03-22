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


async def check_workday_checkbox(page: Page, event_callback=None) -> bool:
    """Check ALL unchecked Workday checkboxes (privacy, terms, consent).
    Handles custom components with click_filter overlays."""
    checked_any = False

    # STRATEGY 1: role="checkbox" with aria-checked="false" — set directly + click
    try:
        result = await page.evaluate("""() => {
            const checked = [];
            document.querySelectorAll('[role="checkbox"][aria-checked="false"]').forEach(el => {
                if (el.offsetParent !== null) {
                    el.setAttribute('aria-checked', 'true');
                    el.click();
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    checked.push(el.getAttribute('data-automation-id') || 'role-cb');
                }
            });
            return checked;
        }""")
        if result:
            checked_any = True
            if event_callback:
                await event_callback("Fill Form", "info", f"Checked role=checkbox: {result}")
    except Exception:
        pass

    # STRATEGY 2: Hidden input[type=checkbox] — set .checked = true
    try:
        result2 = await page.evaluate("""() => {
            const checked = [];
            document.querySelectorAll('input[type="checkbox"]').forEach(el => {
                if (!el.checked) {
                    el.checked = true;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('click', {bubbles: true}));
                    checked.push(el.name || el.id || 'input-cb');
                }
            });
            return checked;
        }""")
        if result2:
            checked_any = True
            if event_callback:
                await event_callback("Fill Form", "info", f"Checked input[checkbox]: {result2}")
    except Exception:
        pass

    # STRATEGY 3: Click container label (not the overlay)
    if not checked_any:
        try:
            result3 = await page.evaluate("""() => {
                const checked = [];
                const sels = '[data-automation-id*="checkbox"], [data-automation-id*="Checkbox"], ' +
                    '[data-automation-id*="agreement"], [data-automation-id*="consent"], ' +
                    '[data-automation-id*="acknowledge"], [data-automation-id*="privacy"]';
                document.querySelectorAll(sels).forEach(container => {
                    if (container.offsetParent === null) return;
                    const label = container.querySelector('label, span:not([data-automation-id="click_filter"])');
                    if (label && label.offsetParent !== null) {
                        label.click();
                        checked.push(container.getAttribute('data-automation-id') || 'label-click');
                    }
                });
                return checked;
            }""")
            if result3:
                checked_any = True
                if event_callback:
                    await event_callback("Fill Form", "info", f"Checked via label: {result3}")
        except Exception:
            pass

    # STRATEGY 4: Playwright force click (bypasses overlays)
    if not checked_any:
        try:
            cbs = page.locator('[role="checkbox"], [data-automation-id*="checkbox" i], [data-automation-id*="agreement" i]')
            count = await cbs.count()
            for i in range(count):
                cb = cbs.nth(i)
                if await cb.is_visible(timeout=1000):
                    aria = await cb.get_attribute("aria-checked")
                    if aria == "false" or aria is None:
                        await cb.click(force=True, timeout=3000)
                        checked_any = True
                        if event_callback:
                            await event_callback("Fill Form", "info", f"Force-clicked checkbox {i}")
        except Exception:
            pass

    # STRATEGY 5: Click any element with privacy/agree text
    if not checked_any:
        try:
            result5 = await page.evaluate("""() => {
                for (const el of document.querySelectorAll('div, label, span')) {
                    const t = el.innerText.toLowerCase();
                    if ((t.includes('privacy') || t.includes('agree') || t.includes('acknowledge') || t.includes('reviewed'))
                        && el.offsetParent !== null && el.getBoundingClientRect().height < 100) {
                        el.click();
                        return 'clicked: ' + t.substring(0, 60);
                    }
                }
                return '';
            }""")
            if result5:
                checked_any = True
                if event_callback:
                    await event_callback("Fill Form", "info", f"Privacy click: {result5}")
        except Exception:
            pass

    # STRATEGY 6: Focus + Space key
    if not checked_any:
        try:
            await page.evaluate("""() => {
                const cb = document.querySelector('[role="checkbox"], [data-automation-id*="checkbox" i]');
                if (cb) cb.focus();
            }""")
            await page.keyboard.press("Space")
            await page.wait_for_timeout(500)
            is_checked = await page.evaluate("""() => {
                const cb = document.querySelector('[role="checkbox"]');
                return cb ? cb.getAttribute('aria-checked') : 'none';
            }""")
            if is_checked == "true":
                checked_any = True
                if event_callback:
                    await event_callback("Fill Form", "info", "Checked via Space key")
        except Exception:
            pass

    if not checked_any and event_callback:
        await event_callback("Fill Form", "warning", "Could not check any checkbox — may need manual click")

    return checked_any


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
        uploaded = False

        # Strategy 1: Make hidden file input visible
        try:
            count = await page.evaluate("""() => {
                const inputs = document.querySelectorAll('input[type="file"]');
                for (const inp of inputs) {
                    inp.style.display = 'block'; inp.style.visibility = 'visible';
                    inp.style.opacity = '1'; inp.style.position = 'relative';
                    inp.style.height = '30px'; inp.style.width = '200px'; inp.style.zIndex = '99999';
                }
                return inputs.length;
            }""")
            if count > 0:
                await page.locator('input[type="file"]').first.set_input_files(resume_path, timeout=10000)
                await page.wait_for_timeout(5000)
                uploaded = True
                filled += 1
                if event_callback:
                    await event_callback("Fill Form", "success", "Resume uploaded via file input")
        except Exception as e:
            if event_callback:
                await event_callback("Fill Form", "info", f"File input failed: {e}")

        # Strategy 2: Click "Select files" link + file chooser
        if not uploaded:
            for sel in [
                '[data-automation-id="file-upload-drop-zone"] a',
                'a:has-text("Select files")',
                'button:has-text("Select files")',
                '[data-automation-id="file-upload-drop-zone"]',
            ]:
                try:
                    link = page.locator(sel).first
                    if await link.is_visible(timeout=2000):
                        async with page.expect_file_chooser(timeout=10000) as fc:
                            await link.click(timeout=5000)
                        chooser = await fc.value
                        await chooser.set_files(resume_path)
                        await page.wait_for_timeout(5000)
                        uploaded = True
                        filled += 1
                        if event_callback:
                            await event_callback("Fill Form", "success", f"Resume uploaded via {sel[:40]}")
                        break
                except Exception:
                    continue

        if not uploaded:
            errors.append("Resume upload failed on My Experience")
            if event_callback:
                await event_callback("Fill Form", "error", "All resume upload strategies failed")

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

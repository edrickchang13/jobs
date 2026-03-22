"""
Debug: Navigate to Workday, click Apply, and dump every clickable element at each stage.
"""
import asyncio
import sys
from playwright.async_api import async_playwright


URL = "https://roberthalf.wd1.myworkdayjobs.com/roberthalfcareers/job/SAN-RAMON/Software-Engineer-Virtual-Internship_JR-259698"


async def dump_page(page, stage):
    """Dump all interactive elements and page text."""
    print(f"\n{'='*80}")
    print(f"  STAGE: {stage}")
    print(f"  URL: {page.url}")
    print(f"{'='*80}")

    info = await page.evaluate("""() => {
        const results = {buttons: [], links: [], inputs: [], dataAids: [], roleButtons: []};

        document.querySelectorAll('button').forEach(el => {
            results.buttons.push({
                text: (el.innerText || '').trim().slice(0, 80),
                aid: el.getAttribute('data-automation-id') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                type: el.type || '',
                disabled: el.disabled,
                visible: el.offsetParent !== null,
                tag: 'button',
            });
        });

        document.querySelectorAll('a').forEach(el => {
            results.links.push({
                text: (el.innerText || '').trim().slice(0, 80),
                href: (el.href || '').slice(0, 100),
                aid: el.getAttribute('data-automation-id') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                role: el.getAttribute('role') || '',
                uxiId: el.getAttribute('data-uxi-element-id') || '',
                visible: el.offsetParent !== null,
                tag: 'a',
            });
        });

        document.querySelectorAll('input, textarea, select').forEach(el => {
            results.inputs.push({
                type: el.type || el.tagName.toLowerCase(),
                name: el.name || '',
                aid: el.getAttribute('data-automation-id') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                placeholder: el.placeholder || '',
                visible: el.offsetParent !== null,
                tag: el.tagName.toLowerCase(),
            });
        });

        // ALL data-automation-id elements
        document.querySelectorAll('[data-automation-id]').forEach(el => {
            if (['BUTTON', 'A', 'INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) return;
            results.dataAids.push({
                tag: el.tagName.toLowerCase(),
                aid: el.getAttribute('data-automation-id'),
                text: (el.innerText || '').trim().slice(0, 60),
                role: el.getAttribute('role') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                visible: el.offsetParent !== null,
            });
        });

        // ALL role="button" that aren't actual buttons
        document.querySelectorAll('[role="button"]').forEach(el => {
            if (el.tagName === 'BUTTON') return;
            results.roleButtons.push({
                tag: el.tagName.toLowerCase(),
                text: (el.innerText || '').trim().slice(0, 80),
                aid: el.getAttribute('data-automation-id') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                visible: el.offsetParent !== null,
                classes: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
            });
        });

        return results;
    }""")

    for category, items in info.items():
        visible = [i for i in items if i.get('visible')]
        if visible:
            print(f"\n--- {category.upper()} (visible: {len(visible)}/{len(items)}) ---")
            for el in visible:
                parts = []
                for k, v in el.items():
                    if k in ('visible',) or not v:
                        continue
                    if k == 'disabled' and not v:
                        continue
                    parts.append(f'{k}="{v}"')
                print(f"  {' | '.join(parts)}")

    text = await page.evaluate("document.body.innerText")
    print(f"\n--- PAGE TEXT (500 chars) ---")
    print(text[:500])


async def try_click(page, description, methods):
    """Try multiple click methods, return which one worked."""
    for name, fn in methods:
        try:
            result = await fn()
            if result is not False:
                print(f"  >>> CLICKED [{description}] via: {name}")
                return True
        except Exception as e:
            print(f"  ... {name}: {str(e)[:80]}")
    print(f"  >>> FAILED to click [{description}]")
    return False


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print(f"Navigating to: {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)

        await dump_page(page, "1. JOB POSTING")

        # Try clicking Apply
        print("\n>>> TRYING TO CLICK APPLY...")
        await try_click(page, "Apply", [
            ("PW: data-uxi-element-id", lambda: page.locator('a[data-uxi-element-id="Apply_adventureButton"]').first.click(timeout=3000)),
            ("PW: adventureButton", lambda: page.locator('[data-automation-id="adventureButton"]').first.click(timeout=3000)),
            ("PW: jobPostingApplyButton", lambda: page.locator('[data-automation-id="jobPostingApplyButton"]').first.click(timeout=3000)),
            ("PW: a role=button Apply", lambda: page.locator('a[role="button"]:has-text("Apply")').first.click(timeout=3000)),
            ("PW: button Apply", lambda: page.locator('button:has-text("Apply")').first.click(timeout=3000)),
            ("PW: a Apply", lambda: page.locator('a:has-text("Apply")').first.click(timeout=3000)),
            ("JS: text match", lambda: page.evaluate("""() => {
                const els = [...document.querySelectorAll('a, button, div[role="button"], [data-automation-id]')];
                for (const el of els) {
                    if (el.innerText && el.innerText.trim().match(/^Apply$/i) && el.offsetParent !== null) {
                        el.click(); return true;
                    }
                }
                return false;
            }""")),
            ("JS: mousedown+mouseup", lambda: page.evaluate("""() => {
                const els = [...document.querySelectorAll('a, button, div[role="button"]')];
                for (const el of els) {
                    if (el.innerText && el.innerText.trim().match(/^Apply/i) && el.offsetParent !== null) {
                        el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                        el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                        el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                        return true;
                    }
                }
                return false;
            }""")),
            ("PW: force click text", lambda: page.get_by_text("Apply", exact=True).first.click(force=True, timeout=3000)),
        ])
        await page.wait_for_timeout(5000)

        # Check if a new page opened
        pages = context.pages
        if len(pages) > 1:
            page = pages[-1]
            print(f"  >>> New page opened: {page.url}")
            await page.wait_for_timeout(3000)

        await dump_page(page, "2. AFTER APPLY CLICK")

        # Try clicking Apply Manually (if present)
        print("\n>>> TRYING TO CLICK APPLY MANUALLY...")
        await try_click(page, "Apply Manually", [
            ("PW: applyManually aid", lambda: page.locator('[data-automation-id="applyManually"]').first.click(timeout=3000)),
            ("PW: text", lambda: page.locator('a:has-text("Apply Manually")').first.click(timeout=3000)),
            ("PW: button text", lambda: page.locator('button:has-text("Apply Manually")').first.click(timeout=3000)),
        ])
        await page.wait_for_timeout(5000)

        await dump_page(page, "3. SIGN IN / CREATE ACCOUNT PAGE")

        # Try Sign In button
        print("\n>>> TRYING TO CLICK SIGN IN...")
        await try_click(page, "Sign In", [
            ("PW: click_filter+aria Sign In", lambda: page.locator('div[role="button"][aria-label="Sign In"][data-automation-id="click_filter"]').first.click(timeout=3000)),
            ("PW: aria-label Sign In", lambda: page.locator('[aria-label="Sign In"]').first.click(timeout=3000)),
            ("PW: div role=button Sign In", lambda: page.locator('div[role="button"]:has-text("Sign In")').first.click(timeout=3000)),
            ("PW: button signInSubmitButton", lambda: page.locator('button[data-automation-id="signInSubmitButton"]').first.click(timeout=3000)),
            ("PW: button text", lambda: page.locator('button:has-text("Sign In")').first.click(timeout=3000)),
            ("PW: utilityButtonSignIn", lambda: page.locator('[data-automation-id="utilityButtonSignIn"]').first.click(timeout=3000)),
            ("PW: get_by_role", lambda: page.get_by_role("button", name="Sign In").first.click(timeout=3000)),
            ("PW: get_by_text", lambda: page.get_by_text("Sign In", exact=True).first.click(timeout=3000)),
            ("PW: force click text", lambda: page.get_by_text("Sign In", exact=True).first.click(force=True, timeout=3000)),
            ("JS: aria-label", lambda: page.evaluate("""() => {
                const el = document.querySelector('[aria-label="Sign In"]');
                if (el && el.offsetParent !== null) { el.click(); return true; }
                return false;
            }""")),
            ("JS: text match", lambda: page.evaluate("""() => {
                const els = document.querySelectorAll('button, div[role="button"], a[role="button"], [data-automation-id]');
                for (const el of els) {
                    if (el.innerText && el.innerText.trim() === 'Sign In' && el.offsetParent !== null) {
                        el.click(); return true;
                    }
                }
                return false;
            }""")),
            ("JS: mousedown+click", lambda: page.evaluate("""() => {
                const els = document.querySelectorAll('button, div[role="button"], a, [data-automation-id="click_filter"]');
                for (const el of els) {
                    if (el.innerText && el.innerText.trim() === 'Sign In' && el.offsetParent !== null) {
                        el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                        el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                        el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        return true;
                    }
                }
                return false;
            }""")),
            ("JS: pointer events", lambda: page.evaluate("""() => {
                const els = document.querySelectorAll('button, div[role="button"], a, [data-automation-id="click_filter"]');
                for (const el of els) {
                    if (el.innerText && el.innerText.trim() === 'Sign In' && el.offsetParent !== null) {
                        el.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, cancelable: true}));
                        el.dispatchEvent(new PointerEvent('pointerup', {bubbles: true, cancelable: true}));
                        el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        return true;
                    }
                }
                return false;
            }""")),
        ])
        await page.wait_for_timeout(3000)

        # Try Create Account button
        print("\n>>> TRYING TO CLICK CREATE ACCOUNT...")
        await try_click(page, "Create Account", [
            ("PW: createAccountLink aid", lambda: page.locator('[data-automation-id="createAccountLink"]').first.click(timeout=3000)),
            ("PW: button createAccountLink", lambda: page.locator('button[data-automation-id="createAccountLink"]').first.click(timeout=3000)),
            ("PW: a text", lambda: page.locator('a:has-text("Create Account")').first.click(timeout=3000)),
            ("PW: button text", lambda: page.locator('button:has-text("Create Account")').first.click(timeout=3000)),
            ("PW: div role=button text", lambda: page.locator('div[role="button"]:has-text("Create Account")').first.click(timeout=3000)),
            ("PW: get_by_role", lambda: page.get_by_role("button", name="Create Account").first.click(timeout=3000)),
            ("PW: get_by_text", lambda: page.get_by_text("Create Account", exact=True).first.click(timeout=3000)),
            ("PW: force click", lambda: page.get_by_text("Create Account", exact=True).first.click(force=True, timeout=3000)),
            ("JS: text match", lambda: page.evaluate("""() => {
                const els = document.querySelectorAll('button, div[role="button"], a, [data-automation-id]');
                for (const el of els) {
                    const t = el.innerText && el.innerText.trim();
                    if (t && (t === 'Create Account' || t === 'Create an Account') && el.offsetParent !== null) {
                        el.click(); return true;
                    }
                }
                return false;
            }""")),
            ("JS: mousedown+click", lambda: page.evaluate("""() => {
                const els = document.querySelectorAll('button, div[role="button"], a, [data-automation-id]');
                for (const el of els) {
                    const t = el.innerText && el.innerText.trim();
                    if (t && (t === 'Create Account' || t === 'Create an Account') && el.offsetParent !== null) {
                        el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                        el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                        el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        return true;
                    }
                }
                return false;
            }""")),
        ])
        await page.wait_for_timeout(3000)

        await dump_page(page, "4. FINAL STATE")

        print("\n\nDone! Browser open for 120s for inspection. Press Ctrl+C to exit.")
        try:
            await asyncio.sleep(120)
        except KeyboardInterrupt:
            pass
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

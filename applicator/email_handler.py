"""
Handles email verification by opening Gmail in a new tab,
finding the verification email, and extracting the code or link.
"""
import asyncio
import re
import yaml
from pathlib import Path
from playwright.async_api import Page, BrowserContext


def _load_email_credentials() -> dict:
    creds_path = Path(__file__).parent.parent / "credentials.yaml"
    if creds_path.exists():
        with open(creds_path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("gmail", data.get("email", {}))
    return {}


async def handle_email_verification(
    context: BrowserContext,
    original_page: Page,
    company_name: str = "",
    event_callback=None,
    screenshot_callback=None,
) -> dict:
    """
    Open Gmail in a new tab, find verification email, extract code/link.
    Returns {"success": bool, "code": str|None, "link": str|None, "method": str}
    """
    creds = _load_email_credentials()
    email = creds.get("email", "")
    password = creds.get("password", "")

    if not email or not password:
        if event_callback:
            await event_callback("Email Verify", "warning",
                "No Gmail credentials in credentials.yaml. Add gmail: section with email and app password.")
        return {"success": False, "code": None, "link": None, "method": "none"}

    if event_callback:
        await event_callback("Email Verify", "info", "Opening Gmail to find verification email...")

    gmail_page = await context.new_page()

    try:
        await gmail_page.goto("https://mail.google.com", wait_until="domcontentloaded", timeout=30000)
        await gmail_page.wait_for_timeout(3000)

        # Sign in if needed
        if "accounts.google.com" in gmail_page.url.lower():
            if event_callback:
                await event_callback("Email Verify", "info", "Signing into Gmail...")
            try:
                email_input = gmail_page.locator('input[type="email"]')
                if await email_input.is_visible(timeout=5000):
                    await email_input.fill(email)
                    await gmail_page.locator('#identifierNext, button:has-text("Next")').first.click()
                    await gmail_page.wait_for_timeout(3000)
                pass_input = gmail_page.locator('input[type="password"]')
                if await pass_input.is_visible(timeout=5000):
                    await pass_input.fill(password)
                    await gmail_page.locator('#passwordNext, button:has-text("Next")').first.click()
                    await gmail_page.wait_for_timeout(5000)
            except Exception as e:
                if event_callback:
                    await event_callback("Email Verify", "warning", f"Gmail sign-in issue: {e}")

        await gmail_page.wait_for_timeout(5000)

        if screenshot_callback:
            try:
                ss = await gmail_page.screenshot(type="png")
                await screenshot_callback(ss)
            except:
                pass

        if event_callback:
            await event_callback("Email Verify", "info", "Searching for verification email...")

        # Search Gmail for recent verification emails
        try:
            search_box = gmail_page.locator('input[aria-label="Search mail"], input[name="q"]').first
            if await search_box.is_visible(timeout=5000):
                query_parts = ["is:unread", "newer_than:1h"]
                if company_name:
                    query_parts.append(f"({company_name} OR verify OR confirm OR activate)")
                else:
                    query_parts.append("(verify OR confirm OR activate OR registration)")
                await search_box.fill(" ".join(query_parts))
                await gmail_page.keyboard.press("Enter")
                await gmail_page.wait_for_timeout(3000)
        except Exception as e:
            if event_callback:
                await event_callback("Email Verify", "info", f"Search issue, scanning inbox: {e}")

        # Click first matching email
        try:
            email_row = gmail_page.locator('tr.zA, tr.zE').first
            if await email_row.is_visible(timeout=5000):
                await email_row.click()
                await gmail_page.wait_for_timeout(3000)
                if event_callback:
                    await event_callback("Email Verify", "info", "Opened verification email")
            else:
                if event_callback:
                    await event_callback("Email Verify", "warning", "No matching emails found")
                await gmail_page.close()
                return {"success": False, "code": None, "link": None, "method": "none"}
        except Exception as e:
            if event_callback:
                await event_callback("Email Verify", "warning", f"Could not open email: {e}")
            await gmail_page.close()
            return {"success": False, "code": None, "link": None, "method": "none"}

        if screenshot_callback:
            try:
                ss = await gmail_page.screenshot(type="png")
                await screenshot_callback(ss)
            except:
                pass

        # Extract email body
        email_body = await gmail_page.evaluate("""() => {
            const selectors = [
                'div[data-message-id] div.a3s',
                'div.ii.gt div',
                'div[role="listitem"] div.a3s',
                'div.maincontent',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim().length > 20) {
                    return { text: el.innerText.trim().substring(0, 3000), html: el.innerHTML.substring(0, 5000) };
                }
            }
            return { text: document.body.innerText.substring(0, 3000), html: '' };
        }""")

        email_text = email_body.get("text", "")
        email_html = email_body.get("html", "")

        if event_callback:
            await event_callback("Email Verify", "info", f"Email body preview: {email_text[:120]}...")

        # STRATEGY A: Extract verification code (4-8 digits)
        code_patterns = [
            r'code[:\s]+(\d{4,8})', r'code is[:\s]+(\d{4,8})',
            r'verification[:\s]+(\d{4,8})', r'\b(\d{6})\b', r'\b(\d{4})\b',
        ]
        for pattern in code_patterns:
            match = re.search(pattern, email_text, re.IGNORECASE)
            if match:
                code = match.group(1)
                if event_callback:
                    await event_callback("Email Verify", "success", f"Found verification code: {code}")
                await gmail_page.close()
                return {"success": True, "code": code, "link": None, "method": "code"}

        # STRATEGY B: Find verification link
        verification_link = None
        if email_html:
            for pattern in [
                r'href="(https?://[^"]*(?:verify|confirm|activate|registration|token|validate)[^"]*)"',
                r'href="(https?://[^"]*(?:email|account)[^"]*(?:verify|confirm)[^"]*)"',
            ]:
                match = re.search(pattern, email_html, re.IGNORECASE)
                if match:
                    verification_link = match.group(1)
                    break

        if not verification_link:
            for pattern in [r'(https?://\S*(?:verify|confirm|activate|token|validate)\S*)']:
                match = re.search(pattern, email_text, re.IGNORECASE)
                if match:
                    verification_link = match.group(1)
                    break

        # STRATEGY C: Click verify button in email
        if not verification_link:
            try:
                btns = gmail_page.locator('a:has-text("Verify"), a:has-text("Confirm"), a:has-text("Activate"), a:has-text("Click here")')
                count = await btns.count()
                for i in range(count):
                    href = await btns.nth(i).get_attribute("href")
                    if href and "http" in href and "google.com/support" not in href:
                        verification_link = href
                        break
            except:
                pass

        if verification_link:
            if event_callback:
                await event_callback("Email Verify", "success", f"Found verification link: {verification_link[:80]}")
            await gmail_page.close()

            # Open verification link in new tab
            verify_page = await context.new_page()
            try:
                await verify_page.goto(verification_link, wait_until="domcontentloaded", timeout=30000)
                await verify_page.wait_for_timeout(5000)
                if screenshot_callback:
                    ss = await verify_page.screenshot(type="png")
                    await screenshot_callback(ss)
                if event_callback:
                    await event_callback("Email Verify", "success", "Clicked verification link successfully")
                await verify_page.close()
            except Exception as e:
                if event_callback:
                    await event_callback("Email Verify", "warning", f"Verification link error: {e}")
                try:
                    await verify_page.close()
                except:
                    pass

            return {"success": True, "code": None, "link": verification_link, "method": "link"}

        if event_callback:
            await event_callback("Email Verify", "warning",
                f"No code or link found. Email text: {email_text[:200]}")
        await gmail_page.close()
        return {"success": False, "code": None, "link": None, "method": "none"}

    except Exception as e:
        if event_callback:
            await event_callback("Email Verify", "error", f"Email handler error: {e}")
        try:
            await gmail_page.close()
        except:
            pass
        return {"success": False, "code": None, "link": None, "method": "none"}


async def enter_verification_code(page: Page, code: str, event_callback=None) -> bool:
    """Enter a verification code on the current page."""
    selectors = [
        'input[type="text"][name*="code"]', 'input[type="text"][name*="verify"]',
        'input[type="text"][name*="token"]', 'input[type="number"]',
        'input[placeholder*="code" i]', 'input[placeholder*="Code"]',
        'input[aria-label*="code" i]', 'input[data-automation-id*="verif"]',
        'input.otp-input', 'input[maxlength="1"]',
        'input[type="text"]:not([name*="email"]):not([name*="password"])',
    ]

    for selector in selectors:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=2000):
                maxlen = await el.get_attribute("maxlength")
                if maxlen == "1":
                    # OTP boxes
                    otp_inputs = page.locator(selector)
                    count = await otp_inputs.count()
                    for i, digit in enumerate(code):
                        if i < count:
                            await otp_inputs.nth(i).fill(digit)
                else:
                    await el.fill(code)

                if event_callback:
                    await event_callback("Email Verify", "info", f"Entered code: {code}")

                # Submit
                for ss in ['button:has-text("Verify")', 'button:has-text("Confirm")',
                           'button:has-text("Submit")', 'button:has-text("Continue")',
                           'button[type="submit"]']:
                    try:
                        btn = page.locator(ss).first
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                            await asyncio.sleep(5)
                            return True
                    except:
                        continue

                await page.keyboard.press("Enter")
                await asyncio.sleep(5)
                return True
        except:
            continue

    if event_callback:
        await event_callback("Email Verify", "warning", "Could not find code input field")
    return False

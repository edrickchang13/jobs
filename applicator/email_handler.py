"""
Handles email verification by checking Gmail via IMAP (primary)
or opening Gmail in a browser tab (fallback).
Extracts verification codes and enters them on the application page.
"""
import asyncio
import imaplib
import email as email_lib
import re
import yaml
import time
from pathlib import Path
from email.header import decode_header


def _load_email_credentials() -> dict:
    """Load Gmail credentials from credentials.yaml or .env"""
    creds_path = Path(__file__).parent.parent / "credentials.yaml"
    if creds_path.exists():
        with open(creds_path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("gmail", data.get("email", {}))

    # Fallback: try .env
    import os
    from dotenv import load_dotenv
    load_dotenv()
    email_addr = os.getenv("GMAIL_EMAIL", "")
    app_password = os.getenv("GMAIL_APP_PASSWORD", "")
    if email_addr and app_password:
        return {"email": email_addr, "password": app_password}
    return {}


def _decode_mime_header(header_val):
    """Decode a MIME-encoded header value to a plain string."""
    if not header_val:
        return ""
    decoded_parts = decode_header(header_val)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)


def _extract_email_body(msg) -> str:
    """Extract plain text body from an email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disposition:
                continue
            if content_type == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body += payload.decode(charset, errors="replace")
                except Exception:
                    pass
            elif content_type == "text/html" and not body:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        html_text = payload.decode(charset, errors="replace")
                        # Strip HTML tags for basic text extraction
                        clean = re.sub(r'<[^>]+>', ' ', html_text)
                        clean = re.sub(r'\s+', ' ', clean).strip()
                        body += clean
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace")
                if msg.get_content_type() == "text/html":
                    body = re.sub(r'<[^>]+>', ' ', body)
                    body = re.sub(r'\s+', ' ', body).strip()
        except Exception:
            pass
    return body[:5000]


def _extract_code_from_text(text: str) -> str | None:
    """Extract a verification code from email text."""
    # Greenhouse-specific patterns first
    code_patterns = [
        r'(?:security|verification|confirm)\s*code\s*(?:is|:)\s*(\d{4,8})',
        r'(?:your|the)\s+code\s+(?:is|:)\s*(\d{4,8})',
        r'code\s*:\s*(\d{4,8})',
        r'code\s+is\s+(\d{4,8})',
        r'(?:enter|use)\s+(?:this\s+)?(?:code\s*:?\s*)?(\d{4,8})',
        # Standalone 6-digit code (most common for verification)
        r'\b(\d{6})\b',
        # 4-digit codes
        r'\b(\d{4})\b',
    ]
    for pattern in code_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            code = match.group(1)
            # Skip common non-code numbers
            if code in ("2024", "2025", "2026", "2027", "2028", "2029", "2030"):
                continue
            if len(code) >= 4:
                return code
    return None


async def fetch_verification_code_imap(
    company_name: str = "",
    event_callback=None,
    max_wait_seconds: int = 60,
    poll_interval: int = 5,
) -> dict:
    """
    Check Gmail via IMAP for a verification code.
    Polls for up to max_wait_seconds, checking every poll_interval seconds.
    Returns {"success": bool, "code": str|None, "method": str}
    """
    creds = _load_email_credentials()
    email_addr = creds.get("email", "")
    app_password = creds.get("password", "")

    if not email_addr or not app_password:
        if event_callback:
            await event_callback("Email IMAP", "warning",
                "No Gmail credentials found. Add to credentials.yaml:\n"
                "gmail:\n  email: your@gmail.com\n  password: your-app-password")
        return {"success": False, "code": None, "method": "none"}

    if event_callback:
        await event_callback("Email IMAP", "info",
            f"Checking Gmail via IMAP for verification code (waiting up to {max_wait_seconds}s)...")

    start_time = time.time()
    attempt = 0

    while time.time() - start_time < max_wait_seconds:
        attempt += 1
        try:
            # Connect to Gmail IMAP
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(email_addr, app_password)
            mail.select("INBOX")

            # Search for recent unread emails (last 1 day to be safe)
            # Use SINCE with today's date
            from datetime import datetime, timedelta
            since_date = (datetime.now() - timedelta(hours=1)).strftime("%d-%b-%Y")
            search_criteria = f'(UNSEEN SINCE "{since_date}")'

            status, msg_ids = mail.search(None, search_criteria)
            if status != "OK" or not msg_ids[0]:
                mail.logout()
                if attempt == 1 and event_callback:
                    await event_callback("Email IMAP", "info", "No unread emails yet, waiting...")
                await asyncio.sleep(poll_interval)
                continue

            # Check most recent emails first (reverse order)
            id_list = msg_ids[0].split()
            id_list.reverse()

            for msg_id in id_list[:10]:  # Check up to 10 recent emails
                status, msg_data = mail.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue

                raw_email = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw_email)

                subject = _decode_mime_header(msg.get("Subject", ""))
                sender = _decode_mime_header(msg.get("From", ""))
                subject_lower = subject.lower()

                # Filter: look for verification/security emails
                is_verification = any(kw in subject_lower for kw in [
                    "verify", "verification", "security code", "confirm",
                    "code", "activate", "one-time", "otp",
                ])

                # Also check if from the company
                if company_name:
                    is_from_company = company_name.lower() in sender.lower() or company_name.lower() in subject_lower
                    is_verification = is_verification or is_from_company

                # Also check for Greenhouse-specific senders
                is_greenhouse = any(kw in sender.lower() for kw in [
                    "greenhouse", "no-reply", "noreply", "do-not-reply",
                    "security", "verify", "notification",
                ])
                is_verification = is_verification or is_greenhouse

                if not is_verification:
                    continue

                if event_callback:
                    await event_callback("Email IMAP", "info",
                        f"Found email: '{subject[:60]}' from {sender[:40]}")

                body = _extract_email_body(msg)
                code = _extract_code_from_text(body)

                if not code:
                    # Also check subject line
                    code = _extract_code_from_text(subject)

                if code:
                    if event_callback:
                        await event_callback("Email IMAP", "success",
                            f"Found verification code: {code}")
                    # Mark as read
                    try:
                        mail.store(msg_id, '+FLAGS', '\\Seen')
                    except Exception:
                        pass
                    mail.logout()
                    return {"success": True, "code": code, "method": "imap"}

                if event_callback:
                    await event_callback("Email IMAP", "info",
                        f"Email matched but no code found. Body preview: {body[:100]}...")

            mail.logout()

            if event_callback and attempt <= 3:
                elapsed = int(time.time() - start_time)
                await event_callback("Email IMAP", "info",
                    f"No code found yet (attempt {attempt}, {elapsed}s elapsed). Waiting {poll_interval}s...")

            await asyncio.sleep(poll_interval)

        except imaplib.IMAP4.error as e:
            error_str = str(e)
            if "AUTHENTICATIONFAILED" in error_str or "Invalid credentials" in error_str:
                if event_callback:
                    await event_callback("Email IMAP", "error",
                        "Gmail authentication failed. Make sure you're using an App Password, "
                        "not your regular Gmail password. Go to myaccount.google.com → Security → "
                        "App passwords to create one.")
                return {"success": False, "code": None, "method": "none"}
            if event_callback:
                await event_callback("Email IMAP", "warning", f"IMAP error: {error_str[:100]}")
            await asyncio.sleep(poll_interval)

        except Exception as e:
            if event_callback:
                await event_callback("Email IMAP", "warning", f"Error checking email: {str(e)[:100]}")
            await asyncio.sleep(poll_interval)

    if event_callback:
        await event_callback("Email IMAP", "warning",
            f"No verification code found after {max_wait_seconds}s. "
            "You can click 'Get Email Code' to try again or enter the code manually.")
    return {"success": False, "code": None, "method": "none"}


async def handle_email_verification(
    context=None,
    original_page=None,
    company_name: str = "",
    event_callback=None,
    screenshot_callback=None,
) -> dict:
    """
    Primary: Check Gmail via IMAP for verification code.
    Fallback: Open Gmail in browser tab if IMAP fails.
    Returns {"success": bool, "code": str|None, "link": str|None, "method": str}
    """
    # Try IMAP first (faster, more reliable)
    result = await fetch_verification_code_imap(
        company_name=company_name,
        event_callback=event_callback,
        max_wait_seconds=45,
        poll_interval=5,
    )

    if result["success"]:
        return {"success": True, "code": result["code"], "link": None, "method": result["method"]}

    # Fallback: browser-based Gmail (only if we have a browser context)
    if context:
        if event_callback:
            await event_callback("Email Verify", "info", "IMAP failed, trying browser-based Gmail...")
        return await _browser_gmail_fallback(
            context, original_page, company_name, event_callback, screenshot_callback
        )

    return {"success": False, "code": None, "link": None, "method": "none"}


async def _browser_gmail_fallback(
    context,
    original_page,
    company_name: str = "",
    event_callback=None,
    screenshot_callback=None,
) -> dict:
    """Browser-based Gmail fallback using Playwright."""
    from playwright.async_api import Page, BrowserContext

    creds = _load_email_credentials()
    email_addr = creds.get("email", "")
    password = creds.get("password", "")

    if not email_addr or not password:
        return {"success": False, "code": None, "link": None, "method": "none"}

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
                    await email_input.fill(email_addr)
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

        # Search for verification emails
        try:
            search_box = gmail_page.locator('input[aria-label="Search mail"], input[name="q"]').first
            if await search_box.is_visible(timeout=5000):
                query_parts = ["is:unread", "newer_than:1h"]
                if company_name:
                    query_parts.append(f"({company_name} OR verify OR confirm OR code OR security)")
                else:
                    query_parts.append("(verify OR confirm OR code OR security)")
                await search_box.fill(" ".join(query_parts))
                await gmail_page.keyboard.press("Enter")
                await gmail_page.wait_for_timeout(3000)
        except Exception as e:
            if event_callback:
                await event_callback("Email Verify", "info", f"Search issue: {e}")

        # Click first matching email
        try:
            email_row = gmail_page.locator('tr.zA, tr.zE').first
            if await email_row.is_visible(timeout=5000):
                await email_row.click()
                await gmail_page.wait_for_timeout(3000)
            else:
                await gmail_page.close()
                return {"success": False, "code": None, "link": None, "method": "none"}
        except Exception:
            await gmail_page.close()
            return {"success": False, "code": None, "link": None, "method": "none"}

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
                    return el.innerText.trim().substring(0, 3000);
                }
            }
            return document.body.innerText.substring(0, 3000);
        }""")

        code = _extract_code_from_text(email_body)
        if code:
            if event_callback:
                await event_callback("Email Verify", "success", f"Found code (browser): {code}")
            await gmail_page.close()
            return {"success": True, "code": code, "link": None, "method": "browser"}

        await gmail_page.close()
        return {"success": False, "code": None, "link": None, "method": "none"}

    except Exception as e:
        if event_callback:
            await event_callback("Email Verify", "error", f"Browser email error: {e}")
        try:
            await gmail_page.close()
        except:
            pass
        return {"success": False, "code": None, "link": None, "method": "none"}


async def enter_verification_code(page, code: str, event_callback=None) -> bool:
    """Enter a verification code on the current page."""
    selectors = [
        'input[name*="security" i]',
        'input[name*="code" i]',
        'input[name*="verify" i]',
        'input[name*="token" i]',
        'input[placeholder*="code" i]',
        'input[placeholder*="Code"]',
        'input[aria-label*="code" i]',
        'input[aria-label*="security" i]',
        'input[type="number"]',
        'input[data-automation-id*="verif"]',
        'input.otp-input',
        'input[maxlength="1"]',
        'input[autocomplete="one-time-code"]',
        # Generic text input (last resort) — exclude email/name/password
        'input[type="text"]:not([name*="email"]):not([name*="name"]):not([name*="password"]):not([name*="first"]):not([name*="last"])',
    ]

    for selector in selectors:
        try:
            el = page.locator(selector).first
            if await el.is_visible(timeout=2000):
                maxlen = await el.get_attribute("maxlength")
                if maxlen == "1":
                    # OTP boxes — fill each digit individually
                    otp_inputs = page.locator(selector)
                    count = await otp_inputs.count()
                    for i, digit in enumerate(code):
                        if i < count:
                            await otp_inputs.nth(i).fill(digit)
                else:
                    await el.click()
                    await el.fill(code)

                if event_callback:
                    await event_callback("Email Verify", "info", f"Entered code: {code}")

                # Wait a moment for validation
                await asyncio.sleep(1)

                # Try clicking a submit/verify button
                for btn_sel in [
                    'button:has-text("Verify")', 'button:has-text("Confirm")',
                    'button:has-text("Submit")', 'button:has-text("Continue")',
                    'input[type="submit"]', 'button[type="submit"]',
                ]:
                    try:
                        btn = page.locator(btn_sel).first
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                            if event_callback:
                                await event_callback("Email Verify", "success",
                                    f"Clicked submit button after entering code")
                            await asyncio.sleep(5)
                            return True
                    except:
                        continue

                # Fallback: press Enter
                await page.keyboard.press("Enter")
                await asyncio.sleep(5)
                return True
        except:
            continue

    if event_callback:
        await event_callback("Email Verify", "warning", "Could not find code input field on page")
    return False


async def auto_handle_security_code(page, company_name: str = "", event_callback=None) -> bool:
    """
    Automatically detect if the current page needs a security code,
    fetch it from Gmail, and enter it.
    Returns True if code was successfully entered.
    """
    # Check if the page has a security code input
    has_code_input = await page.evaluate("""() => {
        const bodyText = document.body.innerText.toLowerCase();
        const hasVerifyText = ['verify your email', 'verification code', 'security code',
            'check your email', 'check your inbox', 'enter the code', 'enter code',
            'we sent', 'sent a code', 'sent you a code'].some(k => bodyText.includes(k));

        const hasCodeInput = !!document.querySelector(
            'input[name*="security" i], input[name*="code" i], input[name*="verify" i], ' +
            'input[placeholder*="code" i], input[aria-label*="code" i], input[aria-label*="security" i]'
        );

        return hasVerifyText || hasCodeInput;
    }""")

    if not has_code_input:
        return False

    if event_callback:
        await event_callback("Auto Email", "info",
            "Security code page detected — automatically checking Gmail...")

    # Fetch the code via IMAP
    result = await fetch_verification_code_imap(
        company_name=company_name,
        event_callback=event_callback,
        max_wait_seconds=60,
        poll_interval=5,
    )

    if result["success"] and result["code"]:
        entered = await enter_verification_code(page, result["code"], event_callback)
        if entered:
            if event_callback:
                await event_callback("Auto Email", "success",
                    f"Security code {result['code']} entered automatically!")
            return True

    if event_callback:
        await event_callback("Auto Email", "warning",
            "Could not automatically enter security code. Please enter it manually or click 'Get Email Code'.")
    return False

"""
Email verification handler for job application portals (e.g., Workday).

Opens Gmail in a browser tab, finds the verification email, clicks the link.
No IMAP or App Password needed — uses the existing browser session.
"""

import asyncio
import re


async def fetch_verification_link_browser(
    page,
    sender_filter: str = "workday",
    max_wait_seconds: int = 90,
    poll_interval: int = 5,
    event_callback=None,
) -> str | None:
    """
    Open Gmail in a new tab, find the verification email, extract and return the link.

    Args:
        page: The current Playwright page (used to get the browser context)
        sender_filter: Keyword to search for in Gmail
        max_wait_seconds: How long to wait for the email to arrive
        poll_interval: Seconds between refresh/retry
        event_callback: Optional async callback for status updates

    Returns:
        The verification URL, or None if not found.
    """
    context = page.context
    gmail_page = await context.new_page()

    try:
        # Navigate to Gmail search for verification emails
        search_query = f"from:{sender_filter} newer_than:1h (verify OR confirm OR activate)"
        gmail_url = f"https://mail.google.com/mail/u/0/#search/{search_query}"

        if event_callback:
            await event_callback("Email", "info", "Opening Gmail to find verification email...")

        await gmail_page.goto(gmail_url, wait_until="domcontentloaded", timeout=30000)
        await gmail_page.wait_for_timeout(5000)

        elapsed = 0
        while elapsed < max_wait_seconds:
            # Check if we need to sign in (redirect to accounts.google.com)
            if "accounts.google.com" in gmail_page.url:
                if event_callback:
                    await event_callback("Email", "warning",
                        "Not signed into Gmail. Please sign in manually in the browser.")
                # Wait for user to sign in
                try:
                    await gmail_page.wait_for_url("**/mail/**", timeout=60000)
                    await gmail_page.wait_for_timeout(3000)
                except Exception:
                    if event_callback:
                        await event_callback("Email", "error", "Gmail sign-in timed out")
                    return None

            # Look for email rows in the search results
            # Gmail email rows have role="row" or are in a table
            try:
                # Click the first/newest email in search results
                # Gmail uses .zA class for email row items
                email_row = gmail_page.locator('tr.zA').first
                if await email_row.is_visible(timeout=3000):
                    await email_row.click()
                    await gmail_page.wait_for_timeout(3000)

                    # Now we're inside the email. Extract the verification link.
                    # Look for links with verify/confirm/activate text or href
                    link = await gmail_page.evaluate("""() => {
                        const links = document.querySelectorAll('a[href]');
                        for (const a of links) {
                            const href = a.href.toLowerCase();
                            const text = a.innerText.toLowerCase();
                            if (href.includes('verify') || href.includes('confirm') ||
                                href.includes('activate') || href.includes('validate') ||
                                text.includes('verify') || text.includes('confirm') ||
                                text.includes('activate') || text.includes('click here')) {
                                // Skip Google's own links and mailto
                                if (!href.includes('google.com/support') &&
                                    !href.includes('mailto:') &&
                                    !href.includes('unsubscribe')) {
                                    return a.href;
                                }
                            }
                        }
                        // Fallback: look for any link containing workday
                        for (const a of links) {
                            if (a.href.toLowerCase().includes('workday') &&
                                !a.href.toLowerCase().includes('.css') &&
                                !a.href.toLowerCase().includes('.png')) {
                                return a.href;
                            }
                        }
                        return null;
                    }""")

                    if link:
                        if event_callback:
                            await event_callback("Email", "success", "Found verification link in email")
                        return link

                    # If no link found via JS, try clicking the verify button directly
                    verify_btn = gmail_page.locator('a:has-text("Verify"), a:has-text("Confirm"), a:has-text("Activate")')
                    if await verify_btn.first.is_visible(timeout=2000):
                        href = await verify_btn.first.get_attribute("href")
                        if href:
                            if event_callback:
                                await event_callback("Email", "success", "Found verification button in email")
                            return href

                    # Go back to search results and retry
                    await gmail_page.go_back()
                    await gmail_page.wait_for_timeout(2000)

                else:
                    # No emails found yet, refresh search
                    if event_callback:
                        await event_callback("Email", "info",
                            f"No verification email yet, retrying... ({elapsed}s/{max_wait_seconds}s)")
                    await gmail_page.reload(wait_until="domcontentloaded")
                    await gmail_page.wait_for_timeout(poll_interval * 1000)
                    elapsed += poll_interval

            except Exception:
                # Refresh and retry
                await gmail_page.reload(wait_until="domcontentloaded")
                await gmail_page.wait_for_timeout(poll_interval * 1000)
                elapsed += poll_interval

        if event_callback:
            await event_callback("Email", "warning", "Verification email not found within timeout")
        return None

    finally:
        await gmail_page.close()


async def complete_email_verification(page, sender_filter="workday", event_callback=None) -> bool:
    """
    Full flow: find verification link in Gmail and navigate to it.

    Args:
        page: Current Playwright page
        sender_filter: Keyword to search Gmail for
        event_callback: Optional async callback

    Returns:
        True if verification was completed, False otherwise.
    """
    link = await fetch_verification_link_browser(
        page,
        sender_filter=sender_filter,
        event_callback=event_callback,
    )

    if not link:
        return False

    # Open the verification link in a new tab
    context = page.context
    verify_page = await context.new_page()
    try:
        if event_callback:
            await event_callback("Email", "info", "Clicking verification link...")
        await verify_page.goto(link, wait_until="domcontentloaded", timeout=30000)
        await verify_page.wait_for_timeout(5000)

        if event_callback:
            await event_callback("Email", "success", "Email verification completed")
        return True
    except Exception as e:
        if event_callback:
            await event_callback("Email", "error", f"Verification link failed: {e}")
        return False
    finally:
        await verify_page.close()

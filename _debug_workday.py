import asyncio, sys, os
sys.path.insert(0, 'C:/Users/Owner/jobs')
os.chdir('C:/Users/Owner/jobs')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from dotenv import load_dotenv
load_dotenv()

from playwright.async_api import async_playwright

URL = "https://cohesity.wd5.myworkdayjobs.com/Cohesity_Careers/job/Santa-Clara-CA---USA-Office/Software-Engineering-Intern--Summer-2026_R01589-1"

async def main():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 1280, "height": 900})

    # Step 1: Load page
    print("Step 1: Loading job page...")
    await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(5000)
    print(f"  URL: {page.url}")
    print(f"  Title: {await page.title()}")

    # Step 2: Look for Apply button
    print("\nStep 2: Looking for Apply button...")
    apply_selectors = [
        '[data-automation-id="adventureButton"]',
        'a:has-text("Apply")',
        'button:has-text("Apply")',
        '[data-automation-id="jobPostingApplyButton"]',
    ]
    for sel in apply_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                text = await btn.inner_text()
                href = await btn.get_attribute("href") or ""
                tag = await btn.evaluate("el => el.tagName")
                print(f"  Found: {sel} -> <{tag}> text='{text.strip()[:30]}' href='{href[:60]}'")

                if href and href.startswith("http"):
                    await page.goto(href, wait_until="domcontentloaded", timeout=30000)
                else:
                    await btn.click(timeout=5000)
                await page.wait_for_timeout(5000)
                print(f"  After click URL: {page.url}")
                break
        except Exception as e:
            pass

    # Step 3: Dump what we see now (should be sign-in / create account)
    print("\nStep 3: Current page analysis...")
    print(f"  URL: {page.url}")

    structure = await page.evaluate("""() => {
        const info = {
            title: document.title,
            all_data_automation_ids: [],
            visible_buttons: [],
            visible_links: [],
            visible_inputs: [],
            page_text_excerpt: document.body?.innerText?.substring(0, 1000) || '',
        };

        // All data-automation-id elements
        document.querySelectorAll('[data-automation-id]').forEach(el => {
            if (el.offsetParent !== null || el.offsetHeight > 0) {
                info.all_data_automation_ids.push({
                    id: el.getAttribute('data-automation-id'),
                    tag: el.tagName,
                    text: (el.innerText || '').trim().substring(0, 40),
                    type: el.type || '',
                });
            }
        });

        // Visible buttons
        document.querySelectorAll('button, [role="button"]').forEach(el => {
            if (el.offsetParent !== null) {
                info.visible_buttons.push({
                    text: (el.innerText || '').trim().substring(0, 40),
                    dataid: el.getAttribute('data-automation-id') || '',
                    classes: el.className?.substring(0, 50) || '',
                });
            }
        });

        // Visible links
        document.querySelectorAll('a').forEach(el => {
            if (el.offsetParent !== null && el.innerText?.trim()) {
                info.visible_links.push({
                    text: (el.innerText || '').trim().substring(0, 40),
                    href: el.href?.substring(0, 80) || '',
                    dataid: el.getAttribute('data-automation-id') || '',
                });
            }
        });

        // Visible inputs
        document.querySelectorAll('input, textarea, select').forEach(el => {
            if (el.offsetParent !== null || el.type === 'hidden') {
                info.visible_inputs.push({
                    tag: el.tagName,
                    type: el.type,
                    name: el.name?.substring(0, 30) || '',
                    dataid: el.getAttribute('data-automation-id') || '',
                    placeholder: el.placeholder?.substring(0, 30) || '',
                    visible: el.offsetParent !== null,
                });
            }
        });

        return info;
    }""")

    print(f"\n  data-automation-id elements ({len(structure['all_data_automation_ids'])}):")
    for el in structure['all_data_automation_ids']:
        print(f"    {el['id']:50s} <{el['tag']:8s}> type={el['type']:10s} '{el['text'][:30]}'")

    print(f"\n  Visible buttons ({len(structure['visible_buttons'])}):")
    for b in structure['visible_buttons']:
        print(f"    '{b['text'][:30]}' dataid={b['dataid']}")

    print(f"\n  Visible links ({len(structure['visible_links'])}):")
    for l in structure['visible_links'][:15]:
        print(f"    '{l['text'][:30]}' dataid={l['dataid']} href={l['href'][:60]}")

    print(f"\n  Visible inputs ({len(structure['visible_inputs'])}):")
    for i in structure['visible_inputs']:
        vis = 'V' if i['visible'] else 'H'
        print(f"    [{vis}] <{i['tag']}> type={i['type']:15s} dataid={i['dataid']:30s} name={i['name']}")

    print(f"\n  Page text (first 500 chars):")
    print(f"    {structure['page_text_excerpt'][:500]}")

    # Step 4: Try to find and click "Create Account" or "Sign In"
    print("\nStep 4: Looking for Create Account / Sign In...")
    create_selectors = [
        '[data-automation-id="createAccountLink"]',
        'a:has-text("Create Account")',
        'button:has-text("Create Account")',
        '[data-automation-id="signInLink"]',
        'a:has-text("Sign In")',
        'button:has-text("Sign In")',
    ]
    for sel in create_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                text = await el.inner_text()
                print(f"  Found: {sel} -> '{text.strip()[:30]}'")
        except:
            pass

    # Step 5: Try clicking Create Account
    print("\nStep 5: Attempting Create Account flow...")
    try:
        create = page.locator('[data-automation-id="createAccountLink"], a:has-text("Create Account"), button:has-text("Create Account")').first
        if await create.is_visible(timeout=3000):
            await create.click()
            await page.wait_for_timeout(3000)
            print(f"  Clicked Create Account, URL: {page.url}")

            # Dump new page state
            create_structure = await page.evaluate("""() => {
                const inputs = [];
                document.querySelectorAll('input, textarea, select').forEach(el => {
                    if (el.offsetParent !== null) {
                        inputs.push({
                            tag: el.tagName,
                            type: el.type,
                            dataid: el.getAttribute('data-automation-id') || '',
                            name: el.name?.substring(0, 30) || '',
                            placeholder: el.placeholder?.substring(0, 30) || '',
                            label: '',
                        });
                    }
                });
                // Get labels
                document.querySelectorAll('label').forEach(lbl => {
                    if (lbl.offsetParent !== null) {
                        const forId = lbl.getAttribute('for');
                        const text = lbl.innerText?.trim()?.substring(0, 40) || '';
                        if (text) {
                            // Find matching input
                            for (const inp of inputs) {
                                if (forId && inp.dataid === forId) {
                                    inp.label = text;
                                }
                            }
                        }
                    }
                });

                const buttons = [];
                document.querySelectorAll('button, [role="button"], [data-automation-id]').forEach(el => {
                    if (el.offsetParent !== null) {
                        const dataid = el.getAttribute('data-automation-id') || '';
                        if (dataid.includes('submit') || dataid.includes('create') || dataid.includes('button') || el.tagName === 'BUTTON') {
                            buttons.push({
                                text: (el.innerText || '').trim().substring(0, 30),
                                dataid: dataid,
                                tag: el.tagName,
                            });
                        }
                    }
                });

                const checkboxes = [];
                document.querySelectorAll('[data-automation-id*="checkbox"], [data-automation-id*="Checkbox"], input[type="checkbox"]').forEach(el => {
                    if (el.offsetParent !== null) {
                        checkboxes.push({
                            dataid: el.getAttribute('data-automation-id') || '',
                            checked: el.checked || false,
                            label: el.closest('label')?.innerText?.trim()?.substring(0, 50) || '',
                        });
                    }
                });

                return {inputs, buttons, checkboxes, text: document.body?.innerText?.substring(0, 500) || ''};
            }""")

            print(f"\n  Create Account form inputs ({len(create_structure['inputs'])}):")
            for i in create_structure['inputs']:
                print(f"    <{i['tag']}> type={i['type']:15s} dataid={i['dataid']:40s} label='{i['label']}'")

            print(f"\n  Buttons ({len(create_structure['buttons'])}):")
            for b in create_structure['buttons']:
                print(f"    <{b['tag']}> dataid={b['dataid']:40s} '{b['text']}'")

            print(f"\n  Checkboxes ({len(create_structure['checkboxes'])}):")
            for c in create_structure['checkboxes']:
                print(f"    dataid={c['dataid']:40s} checked={c['checked']} '{c['label']}'")

            print(f"\n  Page text: {create_structure['text'][:300]}")
        else:
            print("  Create Account button not found")
    except Exception as e:
        print(f"  Error: {e}")

    await browser.close()
    await pw.stop()
    print("\nDONE")

asyncio.run(main())

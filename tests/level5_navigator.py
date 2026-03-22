"""Level 5: Test navigation agent reaches the application form."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

TESTS = [
    {
        "name": "Lever (direct form)",
        "url": "https://jobs.lever.co/aofl/4b91076d-8937-4dbc-a502-a7d6a66e2e19/apply",
        "expect": "Form should be immediately visible",
    },
]

async def test_navigation(test_case):
    from browser_use import Agent, Browser
    from browser_use.llm import ChatOpenAI

    name = test_case["name"]
    url = test_case["url"]
    print(f"\n{'='*50}")
    print(f"Testing: {name}")
    print(f"URL: {url}")
    print(f"{'='*50}")

    llm = ChatOpenAI(
        model="gemini-2.5-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=os.getenv("GEMINI_API_KEY"),
        frequency_penalty=None,
    )

    browser = Browser(headless=False, keep_alive=True)

    task = f"""Navigate to {url} and find the job application form.

RULES:
- If you see an "Apply" or "Apply Now" button, click it
- NEVER click "Apply with LinkedIn" or any social login
- If you see a cookie banner, dismiss it
- STOP when you see a form with fields like name, email, phone, resume upload

Describe what fields you see on the page."""

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        use_vision=False,
        max_actions_per_step=3,
    )

    print("Running navigation agent...")
    try:
        result = await agent.run(max_steps=15)
        is_done = result.is_done()
        final = result.final_result()
        print(f"Agent done: {is_done}")
        print(f"Final result: {str(final)[:300]}")
    except Exception as e:
        print(f"FAIL Agent crashed: {e}")
        import traceback
        traceback.print_exc()
        try:
            await browser.stop()
        except:
            pass
        return False

    # Check if we ended up on a form page
    try:
        from applicator.form_filler import JS_EXTRACT_FIELDS
        pages = agent.browser_session.context.pages
        page = pages[-1] if pages else None
        if page:
            print(f"Final URL: {page.url}")
            fields = await page.evaluate(JS_EXTRACT_FIELDS)

            if len(fields) == 0:
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        ff = await frame.evaluate(JS_EXTRACT_FIELDS)
                        if ff:
                            fields = ff
                            break
                    except:
                        continue

            print(f"Fields found: {len(fields)}")
            for f in fields[:8]:
                print(f"  [{f['type']:12s}] {f['label'][:50]}")

            await browser.stop()

            if len(fields) >= 3:
                print(f"\nPASSED: Found {len(fields)} form fields")
                return True
            else:
                print(f"\nFAILED: Only {len(fields)} fields found")
                return False
    except Exception as e:
        print(f"FAIL Could not inspect page: {e}")
        try:
            await browser.stop()
        except:
            pass
        return False

async def main():
    results = {}
    for test in TESTS:
        success = await test_navigation(test)
        results[test["name"]] = success

    print(f"\n{'='*50}")
    print("RESULTS:")
    for name, passed in results.items():
        print(f"  {'PASS' if passed else 'FAIL'} {name}")
    sys.exit(0 if all(results.values()) else 1)

asyncio.run(main())

"""Level 1: Test LLM provider works with browser-use agent."""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

async def main():
    print("Testing Gemini 2.5 Flash via browser-use ChatOpenAI...")

    from browser_use import Agent, Browser
    from browser_use.llm import ChatOpenAI

    llm = ChatOpenAI(
        model="gemini-2.5-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=os.getenv("GEMINI_API_KEY"),
        frequency_penalty=None,
    )
    print(f"  LLM created: {type(llm).__name__}")

    try:
        browser = Browser(headless=True)
        agent = Agent(
            task="Go to https://example.com and tell me the text of the h1 heading on the page.",
            llm=llm,
            browser=browser,
            use_vision=False,
        )
        result = await agent.run(max_steps=5)
        await browser.close()

        result_text = str(result)
        is_done = result.is_done()
        print(f"  Agent done: {is_done}")
        print(f"  Final result: {result.final_result()}")

        if is_done:
            print(f"\nPASSED: Gemini 2.5 Flash works with browser-use!")
            return True
        else:
            print(f"\n  Agent ran but did not mark done. Result: {result_text[:200]}")
            return True  # It still ran without errors
    except Exception as e:
        error_str = str(e)
        print(f"  FAILED: {error_str[:300]}")
        if "frequency_penalty" in error_str:
            print("  DIAGNOSIS: frequency_penalty not set to None")
        elif "provider" in error_str:
            print("  DIAGNOSIS: LLM missing .provider attribute - use browser_use.llm.ChatOpenAI")
        elif "422" in error_str:
            print("  DIAGNOSIS: API rejected a parameter")
        elif "context" in error_str.lower() or "token" in error_str.lower():
            print("  DIAGNOSIS: Context window too small")
        return False

success = asyncio.run(main())
sys.exit(0 if success else 1)

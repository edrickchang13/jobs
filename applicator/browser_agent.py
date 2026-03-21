import asyncio
import os
from datetime import datetime

from browser_use import Agent, Browser
from langchain_openai import ChatOpenAI

from config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    CHROME_PROFILE_DIR,
    SIMPLIFY_EXTENSION_PATH,
    SCREENSHOTS_DIR,
    AUTO_SUBMIT,
)
from applicator.field_generator import generate_field_answer


def _get_llm():
    """Create a LangChain ChatOpenAI pointed at Groq's OpenAI-compatible endpoint."""
    return ChatOpenAI(
        model=GROQ_MODEL,
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )


async def apply_to_posting(
    url: str,
    company: str,
    role: str,
    job_description: str,
    resume_pdf_path: str,
) -> dict:
    """
    Use browser-use agent to fill out a job application.

    Returns dict with: {success: bool, screenshot_path: str, answers: dict}
    """
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    # Configure browser args
    browser_args = []
    if SIMPLIFY_EXTENSION_PATH:
        browser_args.extend([
            f"--disable-extensions-except={SIMPLIFY_EXTENSION_PATH}",
            f"--load-extension={SIMPLIFY_EXTENSION_PATH}",
        ])

    # Build Browser with configuration
    browser = Browser(
        headless=False,
        user_data_dir=CHROME_PROFILE_DIR if CHROME_PROFILE_DIR else None,
        args=browser_args if browser_args else None,
    )

    llm = _get_llm()

    # PHASE 1: Navigate and trigger Simplify autofill
    agent_task_phase1 = f"""Navigate to this job application URL: {url}

Your goal is to start the application process. Steps:
1. Go to the URL
2. Look for an "Apply" or "Apply Now" or "Apply for this job" button and click it
3. Wait for the application form to load
4. If the Simplify extension popup appears, click "Autofill" to let it fill standard fields
5. Wait 5 seconds for Simplify to finish
6. After Simplify fills fields, take note of what fields remain EMPTY
7. Report back what empty fields you see (especially file uploads and text areas)

Do NOT submit the application. Just fill what you can and report the state of the form."""

    agent = Agent(
        task=agent_task_phase1,
        llm=llm,
        browser=browser,
    )

    await agent.run(max_steps=20)

    # PHASE 2: Upload resume and fill text fields
    # Pre-generate answers for common questions
    common_questions = [
        "Why do you want to work at this company?",
        "Why are you interested in this role?",
        "Tell us about a project you're proud of.",
        "What makes you a good fit for this position?",
    ]

    pre_generated_answers = {}
    for q in common_questions:
        pre_generated_answers[q] = generate_field_answer(q, company, role, job_description)

    answers_context = "\n".join(
        f'Q: "{q}"\nA: "{a}"' for q, a in pre_generated_answers.items()
    )

    agent_task_phase2 = f"""Continue filling out this job application form. The Simplify extension has already filled
the standard fields (name, email, etc.).

You need to:

1. RESUME UPLOAD: Find the resume/CV file upload field and upload this file: {resume_pdf_path}
   - Look for file input elements, "Upload Resume" buttons, or drag-and-drop areas
   - If there's a choice between "Simplify Resume" and "Upload your own", choose "Upload your own"

2. TEXT FIELDS: For any remaining empty text areas or text fields that ask questions,
   use these pre-generated answers to fill them in. Match the question to the closest answer:
   {answers_context}
   If you encounter a question NOT covered above, write a brief, authentic answer about
   why {company}'s work on the topics in this job description interests you, connecting
   it to the candidate's experience in AI systems and hackathon projects.

3. CHECKBOXES/DROPDOWNS: For any remaining required checkboxes or dropdowns:
   - Work authorization: Yes, authorized to work in the US
   - Sponsorship: Will not require sponsorship
   - If asked for graduation date: June 2028
   - If asked for GPA: 3.78
   - If asked for degree: Bachelor of Science, Computer Science & Engineering

4. After filling everything, DO NOT click Submit. Stop and report what you've filled.

IMPORTANT: Be careful and methodical. Check each field before moving on."""

    agent2 = Agent(
        task=agent_task_phase2,
        llm=llm,
        browser=browser,
    )

    await agent2.run(max_steps=30)

    # Take screenshot of completed form
    screenshot_filename = f"{company}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    screenshot_path = os.path.join(SCREENSHOTS_DIR, screenshot_filename)

    agent_screenshot = Agent(
        task="Take a screenshot of the current page. The completed form should be visible.",
        llm=llm,
        browser=browser,
    )
    await agent_screenshot.run(max_steps=3)

    # PHASE 3: Submit only if AUTO_SUBMIT is enabled
    if AUTO_SUBMIT:
        agent_submit = Agent(
            task="Click the Submit or Send Application button to submit this application.",
            llm=llm,
            browser=browser,
        )
        await agent_submit.run(max_steps=5)

    await browser.close()

    return {
        "success": True,
        "screenshot_path": screenshot_path,
        "answers": pre_generated_answers,
        "auto_submitted": AUTO_SUBMIT,
    }

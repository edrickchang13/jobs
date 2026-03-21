import asyncio
import os
from datetime import datetime

from config import CHECK_INTERVAL_MINUTES, RESUMES_DIR
from database.tracker import (
    init_db,
    add_posting,
    log_application,
    update_posting_status,
)
from scraper.github_scraper import get_new_postings
from scraper.job_description import extract_job_description
from resume.generator import generate_resume
from resume.compiler import compile_resume_to_pdf
from applicator.browser_agent import apply_to_posting
from notifications.notifier import send_notification, send_application_ready, send_error


async def process_posting(posting: dict):
    """Process a single new internship posting through the full pipeline."""
    company = posting["company"]
    role = posting["role"]
    url = posting["url"]
    location = posting.get("location", "")
    date_posted = posting.get("date", "")

    print(f"\n{'='*60}")
    print(f"Processing: {company} - {role}")
    print(f"URL: {url}")
    print(f"{'='*60}")

    # 1. Add to database
    posting_id = add_posting(company, role, location, url, date_posted)
    if not posting_id:
        print("  Skipping (already in database)")
        return

    try:
        # 2. Extract job description (deterministic)
        print("  Extracting job description...")
        job_description = extract_job_description(url)
        print(f"  Job description: {len(job_description)} chars")

        # 3. Generate tailored resume (deterministic - single Claude API call)
        print("  Generating tailored resume...")
        resume_html = generate_resume(company, role, job_description)

        # 4. Compile to PDF (deterministic)
        print("  Compiling resume PDF...")
        resume_path = compile_resume_to_pdf(resume_html, company, role)
        print(f"  Resume saved: {resume_path}")

        # 5. Apply via browser agent (AGENTIC)
        print("  Starting browser agent...")
        result = await apply_to_posting(
            url=url,
            company=company,
            role=role,
            job_description=job_description,
            resume_pdf_path=os.path.abspath(resume_path),
        )

        # 6. Log the application
        log_application(
            posting_id=posting_id,
            resume_path=resume_path,
            answers=result.get("answers", {}),
            screenshot_path=result.get("screenshot_path", ""),
            status="submitted" if result.get("auto_submitted") else "ready_for_review",
        )

        # 7. Send notification
        if result.get("auto_submitted"):
            send_notification(f"Auto-submitted application to {company} for {role}")
        else:
            send_application_ready(company, role, url)

        update_posting_status(posting_id, "applied")
        print("  Application completed successfully!")

    except Exception as e:
        print(f"  ERROR: {str(e)}")
        update_posting_status(posting_id, "error")
        send_error(company, role, str(e))


async def run_pipeline():
    """Run one cycle of the pipeline: check for new postings and process them."""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking for new postings...")

    try:
        new_postings = get_new_postings()
        print(f"  Found {len(new_postings)} new posting(s)")

        for posting in new_postings:
            await process_posting(posting)

            # Add delay between applications to avoid rate limits and detection
            if len(new_postings) > 1:
                delay = 120  # 2 minutes between applications
                print(f"\n  Waiting {delay}s before next application...")
                await asyncio.sleep(delay)

    except Exception as e:
        print(f"  Pipeline error: {str(e)}")
        send_notification(f"Pipeline error: {str(e)}")


async def main():
    """Main entry point. Runs the pipeline on a schedule."""
    print("=" * 60)
    print("  AUTO-APPLY: Internship Application Pipeline")
    print("=" * 60)

    # Initialize database
    init_db()
    os.makedirs(RESUMES_DIR, exist_ok=True)

    # Run pipeline in a loop
    while True:
        await run_pipeline()

        print(f"\n  Next check in {CHECK_INTERVAL_MINUTES} minutes...")
        await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    asyncio.run(main())

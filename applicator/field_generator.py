import subprocess
from config import CANDIDATE_PROFILE, WRITING_STYLE


def generate_field_answer(question: str, company: str, role: str, job_description: str) -> str:
    """
    Generate a tailored answer for an application text field using Claude Code CLI.
    Uses the authenticated claude session - no API key needed.
    """
    prompt = f"""You are helping a computer science student write authentic,
concise job application answers.

CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

{WRITING_STYLE}

Write answers that:
- Reference specific things about the company from the job description
- Connect the candidate's actual experience to the role
- Sound like a real person wrote them, not AI
- Are concise (under 150 words unless the question clearly expects more)
- Do NOT start with "I am writing to..." or other generic openers

Company: {company}
Role: {role}
Job Description (excerpt): {job_description[:2000]}

Application Question: "{question}"

Write a compelling answer. Output ONLY the answer text, nothing else."""

    result = subprocess.run(
        ["claude", "-p", "--model", "sonnet", prompt],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI failed: {result.stderr}")

    return result.stdout.strip()

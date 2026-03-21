from openai import OpenAI
from config import CANDIDATE_PROFILE, WRITING_STYLE
import os

client = OpenAI(
    base_url="https://api.cerebras.ai/v1",
    api_key=os.getenv("CEREBRAS_API_KEY"),
)


def generate_field_answer(question: str, company: str, role: str, job_description: str) -> str:
    """
    Generate a tailored answer for an application text field using Cerebras.
    """
    response = client.chat.completions.create(
        model="qwen-3-235b-a22b-instruct-2507",
        max_tokens=500,
        messages=[
            {
                "role": "system",
                "content": f"""You are helping a computer science student write authentic,
concise job application answers.

CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

{WRITING_STYLE}

Write answers that:
- Reference specific things about the company from the job description
- Connect the candidate's actual experience to the role
- Sound like a real person wrote them, not AI
- Are concise (under 150 words unless the question clearly expects more)
- Do NOT start with "I am writing to..." or other generic openers"""
            },
            {
                "role": "user",
                "content": f"""Company: {company}
Role: {role}
Job Description (excerpt): {job_description[:2000]}

Application Question: "{question}"

Write a compelling answer."""
            }
        ]
    )

    return response.choices[0].message.content

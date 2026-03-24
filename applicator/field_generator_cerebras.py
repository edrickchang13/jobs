from openai import OpenAI
from config import CANDIDATE_PROFILE, WRITING_STYLE
import os


def _make_client():
    """Return best available LLM client: Gemini → Cerebras fallback."""
    if os.getenv("GEMINI_API_KEY"):
        try:
            return OpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=os.getenv("GEMINI_API_KEY"),
            ), "gemini-2.0-flash", {}
        except Exception:
            pass
    return (
        OpenAI(
            base_url="https://api.cerebras.ai/v1",
            api_key=os.getenv("CEREBRAS_API_KEY"),
        ),
        "qwen-3-235b-a22b-instruct-2507",
        {"frequency_penalty": None},
    )


_SYSTEM_PROMPT = f"""You are helping a computer science student write authentic,
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


def generate_field_answer(question: str, company: str, role: str, job_description: str) -> str:
    """Generate a tailored answer for an application text field.

    Tries Gemini first; falls back to Cerebras if Gemini key is missing/expired.
    """
    providers = []
    # Build priority list
    if os.getenv("GEMINI_API_KEY"):
        providers.append((
            OpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=os.getenv("GEMINI_API_KEY"),
            ),
            "gemini-2.0-flash",
            {},
        ))
    if os.getenv("CEREBRAS_API_KEY"):
        providers.append((
            OpenAI(
                base_url="https://api.cerebras.ai/v1",
                api_key=os.getenv("CEREBRAS_API_KEY"),
            ),
            "qwen-3-235b-a22b-instruct-2507",
            {},
        ))
    if os.getenv("GROQ_API_KEY"):
        providers.append((
            OpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=os.getenv("GROQ_API_KEY"),
            ),
            "llama-3.3-70b-versatile",
            {},
        ))

    last_err = None
    for client, model, extra_kwargs in providers:
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=2000,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Company: {company}\n"
                            f"Role: {role}\n"
                            f"Job Description (excerpt): {job_description[:2000]}\n\n"
                            f'Application Question: "{question}"\n\n'
                            "Write a compelling answer."
                        ),
                    },
                ],
                **extra_kwargs,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"All LLM providers failed. Last error: {last_err}")

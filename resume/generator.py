import re
import os
import subprocess
from openai import OpenAI
from config import CANDIDATE_PROFILE, WRITING_STYLE

# ── LLM clients ───────────────────────────────────────────────────────────────
_CEREBRAS_KEY = os.getenv("CEREBRAS_API_KEY", "")
_CEREBRAS_MODEL = "qwen-3-235b-a22b-instruct-2507"
_cerebras_client = OpenAI(
    base_url="https://api.cerebras.ai/v1",
    api_key=_CEREBRAS_KEY,
) if _CEREBRAS_KEY else None

_GROQ_KEY = os.getenv("GROQ_API_KEY", "")
_GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
_groq_client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=_GROQ_KEY,
) if _GROQ_KEY else None


def _llm_client():
    """Return (client, model) — Cerebras first, Groq as fallback."""
    if _cerebras_client:
        return _cerebras_client, _CEREBRAS_MODEL
    if _groq_client:
        return _groq_client, _GROQ_MODEL
    raise RuntimeError("No LLM API key configured (need CEREBRAS_API_KEY or GROQ_API_KEY)")


# ── LaTeX source helpers ──────────────────────────────────────────────────────

def read_tex_source(tex_path: str) -> str:
    """Read the raw LaTeX source from the uploaded .tex file."""
    with open(tex_path, "r", encoding="utf-8") as f:
        return f.read()


def split_tex(tex_source: str) -> tuple[str, str]:
    """
    Split a .tex file into (preamble_with_begin_doc, body).
    preamble includes everything up to and including \\begin{document}.
    body is everything between \\begin{document} and \\end{document}.
    """
    begin = tex_source.find(r"\begin{document}")
    end   = tex_source.rfind(r"\end{document}")
    if begin == -1 or end == -1:
        raise ValueError("Could not find \\begin{document} / \\end{document} in .tex file")
    preamble = tex_source[:begin + len(r"\begin{document}")]
    body     = tex_source[begin + len(r"\begin{document}"):end].strip()
    return preamble, body


def extract_resume_text(pdf_path: str) -> str:
    """Extract plain text from the uploaded resume PDF using pdfplumber (fallback)."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        return "\n".join(pages).strip()
    except Exception as e:
        print(f"[resume] PDF extract failed ({e}), falling back to CANDIDATE_PROFILE")
        return ""


# ── Spacing presets (used only when no .tex preamble is available) ────────────
SPACING_PRESETS = [
    {"margin": "0.55in", "itemsep": "1pt",  "topsep": "1pt",  "subheading_vspace": "0pt",  "subheading_after_vspace": "-4pt",  "itemlist_after_vspace": "1pt",  "section_before_vspace": "-4pt", "section_after_vspace": "-7pt", "skills_itemsep": "0pt"},
    {"margin": "0.5in",  "itemsep": "1pt",  "topsep": "1pt",  "subheading_vspace": "0pt",  "subheading_after_vspace": "-4pt",  "itemlist_after_vspace": "1pt",  "section_before_vspace": "-6pt", "section_after_vspace": "-6pt", "skills_itemsep": "1pt"},
    {"margin": "0.45in", "itemsep": "0pt",  "topsep": "0pt",  "subheading_vspace": "0pt",  "subheading_after_vspace": "-4pt",  "itemlist_after_vspace": "0pt",  "section_before_vspace": "-6pt", "section_after_vspace": "-6pt", "skills_itemsep": "0pt"},
    {"margin": "0.4in",  "itemsep": "0pt",  "topsep": "0pt",  "subheading_vspace": "-2pt", "subheading_after_vspace": "-6pt",  "itemlist_after_vspace": "0pt",  "section_before_vspace": "-6pt", "section_after_vspace": "-6pt", "skills_itemsep": "0pt"},
]

LATEX_FOOTER = r"""
\end{document}"""

RESUME_SYSTEM_PROMPT = r"""You are an expert ATS resume writer. You are given a candidate's LaTeX resume body and a job description. Your job is to rewrite the resume body so it passes ATS screening for that specific role.

═══════════════════════════════════════════════
ATS KEYWORD INJECTION — THIS IS THE #1 PRIORITY
═══════════════════════════════════════════════
1. Extract every significant technical term, tool, language, framework, skill, and methodology from the job description.
2. Rewrite the candidate's existing bullets to include those exact keywords verbatim — do NOT invent new experiences.
3. Wrap 2-3 of the most important ATS keywords per bullet in \textbf{} so they stand out.
4. Reorder the Technical Skills section categories so the most JD-relevant skills appear FIRST.
5. Mirror the company's own language and values in how you frame bullets.
6. If the JD mentions a technology the candidate has used under a different name, use the JD's exact terminology.

STRICT RULES:
- Output ONLY the LaTeX body content — no \documentclass, no \usepackage, no \begin{document}, no \end{document}
- Keep ALL the same LaTeX commands (\resumeItem, \resumeSubheading, \resumeSubHeadingListStart, etc.) — do NOT change command names
- Keep the exact same sections and structure as the original — do NOT add or remove sections
- Keep Heading and Education exactly as-is — never change the candidate's name, email, GPA, dates, or school
- NEVER use markdown **bold** — always use \textbf{}
- NEVER use em dashes (—) — use -- instead
- Use \& for ampersands, \# for hash, \% for percent
- ONE PAGE ONLY. Keep bullets tight (max 115 characters each, single line)
- Every bullet must start with a strong action verb"""


def generate_resume(company: str, role: str, job_description: str,
                    resume_pdf_path: str = "",
                    resume_tex_path: str = "") -> str:
    """
    Generate an ATS-optimized LaTeX resume tailored to a specific role.

    Priority order for source:
    1. resume_tex_path — uses the exact preamble + body from the uploaded .tex file
    2. resume_pdf_path — extracts text via pdfplumber, uses fallback preamble
    3. CANDIDATE_PROFILE — hardcoded fallback

    Returns complete LaTeX source code ready for compilation.
    """
    client, model = _llm_client()

    # ── 1. Determine source ───────────────────────────────────────────────────
    tex_preamble = None
    original_body = None

    if resume_tex_path and os.path.exists(resume_tex_path):
        try:
            tex_source = read_tex_source(resume_tex_path)
            tex_preamble, original_body = split_tex(tex_source)
            print(f"[resume] Using uploaded LaTeX source: {resume_tex_path}")
        except Exception as e:
            print(f"[resume] Failed to parse .tex file ({e}), falling back...")
            tex_preamble = None
            original_body = None

    if original_body is None:
        # Fall back to PDF text or hardcoded profile
        if resume_pdf_path and os.path.exists(resume_pdf_path):
            original_body = extract_resume_text(resume_pdf_path)
        if not original_body:
            original_body = CANDIDATE_PROFILE

    # ── 2. Call LLM to rewrite body with JD keywords ─────────────────────────
    if tex_preamble:
        # We have the real LaTeX body — ask LLM to rewrite it preserving commands
        user_msg = f"""TARGET JOB:
Company: {company}
Role: {role}

JOB DESCRIPTION (extract every keyword, tool, skill, and methodology from this):
{job_description}

CANDIDATE'S CURRENT RESUME BODY (LaTeX — rewrite this with JD keywords injected):
{original_body}

TASK:
Rewrite the resume body above to pass ATS for this {company} {role} role.
- Inject JD keywords verbatim into existing bullets using \textbf{{}}
- Reorder Technical Skills so the most relevant to this JD appear first
- Keep ALL LaTeX commands and section structure identical
- Output ONLY the rewritten LaTeX body (no preamble, no \\begin{{document}}, no \\end{{document}})"""
    else:
        # No LaTeX source — ask LLM to generate from profile text
        user_msg = f"""TARGET JOB:
Company: {company}
Role: {role}

JOB DESCRIPTION (extract every keyword/tool/skill from this):
{job_description}

CANDIDATE PROFILE:
{original_body}

TASK: Generate an ATS-optimized LaTeX resume body for this role. Inject JD keywords into bullets using \\textbf{{}}. Output ONLY LaTeX body content."""

    response = client.chat.completions.create(
        model=model,
        max_tokens=4000,
        messages=[
            {"role": "system", "content": RESUME_SYSTEM_PROMPT + "\n\n" + WRITING_STYLE},
            {"role": "user", "content": user_msg},
        ],
    )

    new_body = _postprocess_latex(response.choices[0].message.content)

    # ── 3. Assemble final LaTeX ───────────────────────────────────────────────
    if tex_preamble:
        # Use the user's exact preamble verbatim — their formatting, their packages
        full_latex = tex_preamble + "\n\n" + new_body + "\n" + LATEX_FOOTER
        # Check page count; if over 1 page tighten spacing in the preamble
        pages = _check_page_count(full_latex)
        if pages and pages > 1:
            # Nudge margin tighter and recheck
            tighter = full_latex.replace(
                r"\usepackage[letterpaper, margin=0.55in]{geometry}",
                r"\usepackage[letterpaper, margin=0.45in]{geometry}"
            ).replace(
                r"itemsep=1pt, topsep=1pt",
                r"itemsep=0pt, topsep=0pt"
            )
            pages2 = _check_page_count(tighter)
            if pages2 and pages2 <= 1:
                return tighter
        return full_latex
    else:
        # Fallback preamble (no uploaded .tex)
        for preset in SPACING_PRESETS:
            preamble = _make_fallback_preamble(**preset)
            full_latex = preamble + new_body + LATEX_FOOTER
            pages = _check_page_count(full_latex)
            if pages is not None and pages <= 1:
                return full_latex
        return _make_fallback_preamble(**SPACING_PRESETS[-1]) + new_body + LATEX_FOOTER


def _make_fallback_preamble(margin="0.55in", itemsep="1pt", topsep="1pt",
                             subheading_vspace="0pt", subheading_after_vspace="-4pt",
                             itemlist_after_vspace="1pt", section_before_vspace="-4pt",
                             section_after_vspace="-7pt", skills_itemsep="0pt"):
    """Fallback preamble matching the user's uploaded .tex style (used only if no .tex file)."""
    return rf"""\documentclass[letterpaper,11pt]{{article}}
\usepackage[letterpaper, margin={margin}]{{geometry}}
\usepackage{{titlesec}}
\usepackage{{enumitem}}
\usepackage[hidelinks]{{hyperref}}
\usepackage{{fancyhdr}}
\usepackage{{tabularx}}
\usepackage{{fontawesome5}}
\usepackage{{multicol}}
\pagestyle{{fancy}}
\fancyhf{{}}
\renewcommand{{\headrulewidth}}{{0pt}}
\renewcommand{{\footrulewidth}}{{0pt}}
\raggedbottom
\urlstyle{{same}}
\setlength{{\tabcolsep}}{{0in}}
\titleformat{{\section}}{{\vspace{{{section_before_vspace}}}\scshape\raggedright\Large\bfseries}}{{}}{{0em}}{{}}[\titlerule \vspace{{{section_after_vspace}}}]
\newcommand{{\resumeItem}}[1]{{\item\small{{#1}}\vspace{{-1pt}}}}
\newcommand{{\resumeSubheading}}[4]{{\vspace{{0pt}}\item\begin{{tabular*}}{{\textwidth}}[t]{{l@{{\extracolsep{{\fill}}}}r}}\textbf{{#1}} & \textit{{\small #2}} \\\textit{{\small #3}} & \textit{{\small #4}} \\\end{{tabular*}}\vspace{{-4pt}}}}
\newcommand{{\resumeSubHeadingListStart}}{{\begin{{itemize}}[leftmargin=0.0in, label={{}}, itemsep={itemsep}, topsep={topsep}]}}
\newcommand{{\resumeSubHeadingListEnd}}{{\end{{itemize}}}}
\newcommand{{\resumeItemListStart}}{{\begin{{itemize}}[leftmargin=0.15in, itemsep={itemsep}, topsep=0pt]}}
\newcommand{{\resumeItemListEnd}}{{\end{{itemize}}}}
\begin{{document}}
"""


def _check_page_count(latex_content: str) -> int | None:
    """Compile LaTeX in a temp dir and return page count, or None on failure."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "check.tex")
        with open(tex_path, "w") as f:
            f.write(latex_content)

        result = subprocess.run(
            ["tectonic", "-X", "compile", tex_path],
            capture_output=True, text=True, cwd=tmpdir, timeout=60,
        )
        if result.returncode != 0:
            return None

        pdf_path = os.path.join(tmpdir, "check.pdf")
        if not os.path.exists(pdf_path):
            return None

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        import re as re_mod
        pages = len(re_mod.findall(rb"/Type\s*/Page(?!s)", pdf_bytes))
        return max(pages, 1)


def _postprocess_latex(content: str) -> str:
    """Fix common LLM mistakes in generated LaTeX."""
    # Strip markdown code fences
    if "```" in content:
        lines = content.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        content = "\n".join(lines)

    # Remove any preamble the LLM accidentally includes
    content = re.sub(r"\\documentclass.*?\n", "", content)
    content = re.sub(r"\\usepackage.*?\n", "", content)
    content = re.sub(r"\\input\{.*?\}", "", content)
    content = re.sub(r"\\pagestyle\{.*?\}\n?", "", content)
    content = re.sub(r"\\fancyhf\{.*?\}\n?", "", content)
    content = re.sub(r"\\renewcommand.*?\n", "", content)
    content = re.sub(r"\\urlstyle.*?\n", "", content)
    content = re.sub(r"\\setlength.*?\n", "", content)
    content = re.sub(r"\\titleformat.*?\n", "", content)
    content = re.sub(r"\\newcommand.*?\n", "", content)
    content = content.replace(r"\begin{document}", "")
    content = content.replace(r"\end{document}", "")

    # Convert stray markdown **bold** to \textbf{}
    content = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", content)

    # Fix em-dash to --
    content = content.replace("\u2014", "--")
    content = content.replace("\u2013", "--")

    return content.strip()

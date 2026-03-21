import re
import subprocess
import os
from groq import Groq
from config import GROQ_API_KEY, GROQ_MODEL, CANDIDATE_PROFILE, WRITING_STYLE

client = Groq(api_key=GROQ_API_KEY)


def _make_preamble(margin="0.45in", itemsep="0pt", topsep="0pt", subheading_vspace="0pt", subheading_after_vspace="-4pt", itemlist_after_vspace="0pt", section_before_vspace="-6pt", section_after_vspace="-6pt", skills_itemsep="1pt"):
    """Generate LaTeX preamble with configurable spacing."""
    return rf"""\documentclass[letterpaper,10pt]{{article}}
\usepackage[letterpaper, margin={margin}]{{geometry}}
\usepackage{{titlesec}}
\usepackage{{enumitem}}
\usepackage[hidelinks]{{hyperref}}
\usepackage{{fancyhdr}}
\usepackage{{tabularx}}
\pagestyle{{fancy}}
\fancyhf{{}}
\renewcommand{{\headrulewidth}}{{0pt}}
\renewcommand{{\footrulewidth}}{{0pt}}
\urlstyle{{same}}
\setlength{{\tabcolsep}}{{0in}}
\titleformat{{\section}}{{
  \vspace{{{section_before_vspace}}}\scshape\raggedright\large\bfseries
}}{{}}{{0em}}{{}}[\titlerule \vspace{{{section_after_vspace}}}]
\newcommand{{\resumeItem}}[1]{{\item\small{{#1}}}}
\newcommand{{\resumeSubheading}}[4]{{
  \vspace{{{subheading_vspace}}}\item
  \begin{{tabular*}}{{0.97\textwidth}}[t]{{l@{{\extracolsep{{\fill}}}}r}}
    \textbf{{#1}} & \textit{{\small #2}} \\
    \textit{{\small #3}} & \textit{{\small #4}} \\
  \end{{tabular*}}\vspace{{{subheading_after_vspace}}}
}}
\newcommand{{\resumeSubHeadingListStart}}{{\begin{{itemize}}[leftmargin=0.15in, label={{}}, itemsep={itemsep}]}}
\newcommand{{\resumeSubHeadingListEnd}}{{\end{{itemize}}}}
\newcommand{{\resumeItemListStart}}{{\begin{{itemize}}[itemsep={itemsep}, topsep={topsep}]}}
\newcommand{{\resumeItemListEnd}}{{\end{{itemize}}\vspace{{{itemlist_after_vspace}}}}}
\begin{{document}}
"""


LATEX_FOOTER = r"""
\end{document}"""

# Spacing presets ordered from most spacious to tightest
SPACING_PRESETS = [
    {"margin": "0.5in", "itemsep": "1pt", "topsep": "1pt", "subheading_vspace": "0pt", "subheading_after_vspace": "-4pt", "itemlist_after_vspace": "1pt", "section_before_vspace": "-6pt", "section_after_vspace": "-6pt", "skills_itemsep": "1pt"},
    {"margin": "0.45in", "itemsep": "1pt", "topsep": "1pt", "subheading_vspace": "0pt", "subheading_after_vspace": "-4pt", "itemlist_after_vspace": "0pt", "section_before_vspace": "-6pt", "section_after_vspace": "-6pt", "skills_itemsep": "1pt"},
    {"margin": "0.45in", "itemsep": "0pt", "topsep": "0pt", "subheading_vspace": "0pt", "subheading_after_vspace": "-4pt", "itemlist_after_vspace": "0pt", "section_before_vspace": "-6pt", "section_after_vspace": "-6pt", "skills_itemsep": "0pt"},
    {"margin": "0.4in", "itemsep": "0pt", "topsep": "0pt", "subheading_vspace": "-2pt", "subheading_after_vspace": "-6pt", "itemlist_after_vspace": "0pt", "section_before_vspace": "-6pt", "section_after_vspace": "-6pt", "skills_itemsep": "0pt"},
]

RESUME_SYSTEM_PROMPT = r"""You are an expert resume writer. You output ONLY the LaTeX body content for a resume (everything between \begin{document} and \end{document}).

CRITICAL FORMATTING RULES:
1. Use \textbf{} for bold (NEVER use markdown **bold**)
2. Use -- for dashes (NEVER use the em dash character)
3. Use \& for ampersands
4. Use \# for hash symbols
5. Use \% for percent signs
6. Bold 2-3 ATS keywords per bullet with \textbf{}
7. Reframe experience to match the target company's language and values
8. ONE PAGE. Keep it tight.
9. Follow the EXACT structure shown in the example below

BULLET POINT RULES:
- Target: 80-110 characters per bullet
- Maximum: 115 characters (avoid wrapping to second line)
- Every bullet MUST fit on a single line
- Major Experience: 3-5 bullets
- Minor Experience: 2-3 bullets
- Projects: 3-4 bullets each
- Total bullets across resume: 15-25
- Formula: Action verb + \textbf{technical skill/tool} + specific task + \textbf{quantified outcome}

HERE IS THE EXACT TEMPLATE FORMAT YOU MUST FOLLOW:

%----------HEADING----------
\begin{center}
{\Huge \scshape Edrick Chang} \\ \vspace{2pt}
\small \href{mailto:eachang@scu.edu}{eachang@scu.edu} $|$
\href{https://linkedin.com/in/edrickchang}{linkedin.com/in/edrickchang} $|$
(408) 806-6495
\end{center}

%-----------EDUCATION-----------
\section{Education}
\resumeSubHeadingListStart
  \resumeSubheading
    {Santa Clara University}{Santa Clara, CA}
    {B.S. Computer Science \& Engineering; GPA: 3.78/4.0}{Expected June 2028}
\resumeSubHeadingListEnd

%-----------TECHNICAL SKILLS-----------
\section{Technical Skills}
\begin{itemize}[leftmargin=0.15in, label={}, itemsep=1pt]
\small{\item{
\textbf{Programming:} C++, Python, C, Java, MATLAB, Swift \\
\textbf{Systems \& Tools:} ROS2, Git, AWS, CI/CD, Serverless Architectures \\
\textbf{AI/ML:} Real-Time Inference, RAG Pipelines, Decision Support Systems \\
\textbf{Data \& Research:} Data Pipelines, Logging \& Metrics Analysis, Dataset Curation
}}
\end{itemize}

%-----------EXPERIENCE-----------
\section{Experience}
\resumeSubHeadingListStart

\resumeSubheading
{Real-Time AI Lawyer (Power2ThePeople)}{Feb 2026}
{NVIDIA Spark Hackathon -- \textbf{1st Place} / 75 Teams}{Santa Clara, CA}
\resumeItemListStart
  \resumeItem{Developed a \textbf{real-time AI system} assisting users during high-stress legal encounters using live video and audio}
  \resumeItem{Designed \textbf{perception and reasoning pipelines} to evaluate legality and detect missed procedural safeguards}
  \resumeItem{Applied \textbf{retrieval-augmented generation (RAG)} over public misconduct datasets to contextualize encounters}
  \resumeItem{Optimized \textbf{low-latency inference} workflows on NVIDIA DGX Spark for real-time decision support}
\resumeItemListEnd

\resumeSubheading
{Agentic System for Incident Response (DevAngel)}{Oct 2025}
{AWS x INRIX Hackathon -- \textbf{2nd Place} / 40 Teams}{San Jose, CA}
\resumeItemListStart
  \resumeItem{Built an \textbf{intelligent decision-support system} to diagnose and respond to complex system failures in real time}
  \resumeItem{Designed visual analytics to surface temporal patterns, system states, and \textbf{impacted components}}
  \resumeItem{Implemented automated \textbf{data pipelines} using \textbf{AWS Lambda}, Step Functions, and CloudWatch Logs Insights}
  \resumeItem{Deployed scalable, \textbf{serverless infrastructure} with continuous integration and live updates}
\resumeItemListEnd

\resumeSubHeadingListEnd

%-----------PROJECTS-----------
\section{Projects}
\resumeSubHeadingListStart

\resumeSubheading
{AI-Powered STEM Tutoring Platform (Equity.edu)}{Mar 2026}
{Hack for Humanity 2026}{Santa Clara, CA}
\resumeItemListStart
  \resumeItem{Developed an accessible \textbf{AI-powered STEM tutoring platform} for contextual feedback and guided hints}
  \resumeItem{Designed real-time workspace awareness using a \textbf{tldraw canvas} with voice-enabled Socratic guidance}
  \resumeItem{Built backend \textbf{reasoning pipelines} combining vision-language models with fallback generative services}
\resumeItemListEnd

\resumeSubheading
{AI Calorie Tracker (MealSense)}{Feb 2025}
{Hack for Humanity 2025}{Santa Clara, CA}
\resumeItemListStart
  \resumeItem{Developed a personalized \textbf{nutrition assistance system} aligned with user health goals}
  \resumeItem{Built mobile interface using \textbf{React Native} with backend services in \textbf{FastAPI} and Firebase}
  \resumeItem{Modeled food intake data and \textbf{macronutrient profiles} to support behavior-aware recommendations}
\resumeItemListEnd

\resumeSubHeadingListEnd

END OF TEMPLATE.

YOUR TASK: Using the candidate profile and job description provided, generate a resume body that follows this EXACT structure. You MUST:
- Keep the Heading and Education sections exactly as shown (do not change the candidate's info)
- Reorder and reframe the Technical Skills to emphasize skills relevant to the target role
- Reframe the Experience and Projects bullet points to match the target company's language
- Add/remove/reorder sections as needed to best match the role
- Bold ATS keywords from the job description using \textbf{}

Output ONLY the LaTeX body content. Do NOT include \documentclass, \usepackage, \begin{document}, or \end{document}."""


def generate_resume(company: str, role: str, job_description: str) -> str:
    """
    Generate an ATS-optimized resume tailored to a specific role.
    Returns complete LaTeX source code ready for compilation.
    Automatically adjusts spacing to fit exactly one page.
    """
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=4000,
        messages=[
            {"role": "system", "content": RESUME_SYSTEM_PROMPT + "\n\n" + WRITING_STYLE},
            {
                "role": "user",
                "content": f"""CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

TARGET ROLE:
Company: {company}
Role: {role}

Job Description:
{job_description}

Generate the LaTeX resume body. Follow the template structure EXACTLY. Reframe bullets for {company}. Output ONLY LaTeX body content.""",
            },
        ],
    )

    body = _postprocess_latex(response.choices[0].message.content)

    # Try spacing presets from standard to tightest until it fits on one page
    for i, preset in enumerate(SPACING_PRESETS):
        preamble = _make_preamble(**preset)
        full_latex = preamble + body + LATEX_FOOTER
        pages = _check_page_count(full_latex)
        if pages is not None and pages <= 1:
            return full_latex
        if pages is not None:
            print(f"  Spacing preset {i} ({preset['margin']} margin): {pages} pages, trying tighter...")

    # Fallback: use tightest preset regardless
    preamble = _make_preamble(**SPACING_PRESETS[-1])
    return preamble + body + LATEX_FOOTER


def _check_page_count(latex_content: str) -> int | None:
    """Compile LaTeX in a temp dir and return page count, or None on failure."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "check.tex")
        with open(tex_path, "w") as f:
            f.write(latex_content)

        result = subprocess.run(
            ["tectonic", "-X", "compile", tex_path],
            capture_output=True,
            text=True,
            cwd=tmpdir,
            timeout=60,
        )

        if result.returncode != 0:
            return None

        # Check PDF page count using python
        pdf_path = os.path.join(tmpdir, "check.pdf")
        if not os.path.exists(pdf_path):
            return None

        # Read PDF and count pages by looking for /Type /Page entries
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        # Simple heuristic: count /Type /Page (not /Pages)
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

    # Convert markdown **bold** to LaTeX \textbf{bold}
    content = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", content)

    # Fix em-dash to LaTeX --
    content = content.replace("\u2014", "--")
    content = content.replace("\u2013", "--")

    return content.strip()

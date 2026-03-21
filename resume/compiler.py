import os
import subprocess
import tempfile
from config import RESUMES_DIR


def compile_resume_to_pdf(latex_content: str, company: str, role: str) -> str:
    """
    Compile LaTeX resume content to PDF using tectonic.
    Returns the file path of the generated PDF.
    """
    os.makedirs(RESUMES_DIR, exist_ok=True)

    # Clean filename
    safe_company = "".join(c for c in company if c.isalnum() or c in "_ -").strip()
    safe_role = "".join(c for c in role if c.isalnum() or c in "_ -").strip()
    filename = f"{safe_company}_{safe_role}"
    filepath = os.path.join(RESUMES_DIR, f"{filename}.pdf")

    # Write LaTeX to a temp file and compile
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "resume.tex")
        with open(tex_path, "w") as f:
            f.write(latex_content)

        # Compile with tectonic (self-contained LaTeX engine)
        result = subprocess.run(
            ["tectonic", "-X", "compile", tex_path],
            capture_output=True,
            text=True,
            cwd=tmpdir,
            timeout=60,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"LaTeX compilation failed:\n{result.stderr}\n{result.stdout}"
            )

        # Move PDF to resumes directory
        compiled_pdf = os.path.join(tmpdir, "resume.pdf")
        if not os.path.exists(compiled_pdf):
            raise RuntimeError("PDF was not generated")

        # Copy to final destination
        import shutil
        shutil.copy2(compiled_pdf, filepath)

    return filepath

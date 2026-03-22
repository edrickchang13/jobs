"""Level 0: Verify environment, imports, API keys, and file paths."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

errors = []
warnings = []

print(f"Python: {sys.version}")

# Required packages
required = ["browser_use", "playwright", "openai", "requests", "bs4", "fastapi", "yaml"]
for pkg in required:
    try:
        __import__(pkg)
        print(f"  OK {pkg}")
    except ImportError as e:
        errors.append(f"Missing package: {pkg} ({e})")
        print(f"  FAIL {pkg}: {e}")

# Playwright browsers
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        browser.close()
        print("  OK Playwright Chromium installed")
except Exception as e:
    errors.append(f"Playwright Chromium not installed: {e}")

# API keys
api_keys = {
    "CEREBRAS_API_KEY": os.getenv("CEREBRAS_API_KEY"),
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
}
for name, val in api_keys.items():
    if val:
        print(f"  OK {name} is set")
    else:
        print(f"  -- {name} not set")

if not any(api_keys.values()):
    errors.append("No LLM API key set")

# Required files
from pathlib import Path
root = Path(__file__).parent.parent

for name, path in {
    "personal_info.yaml": root / "personal_info.yaml",
    "credentials.yaml": root / "credentials.yaml",
    "config.py": root / "config.py",
    "applicator/form_filler.py": root / "applicator" / "form_filler.py",
    "dashboard/app.py": root / "dashboard" / "app.py",
}.items():
    if path.exists():
        print(f"  OK {name}")
    else:
        errors.append(f"Missing file: {name}")

# personal_info.yaml content
pi_path = root / "personal_info.yaml"
if pi_path.exists():
    import yaml
    with open(pi_path) as f:
        pi = yaml.safe_load(f) or {}
    critical = ["first_name", "last_name", "email", "phone", "country", "how_did_you_hear",
                "street_address", "city", "state", "zip_code", "school", "degree", "gpa",
                "authorized_to_work", "sponsorship_needed", "gender", "race_ethnicity",
                "veteran_status", "disability_status"]
    for field in critical:
        if pi.get(field):
            print(f"  OK personal_info.{field} = {str(pi[field])[:30]}")
        else:
            warnings.append(f"personal_info.yaml missing: {field}")
            print(f"  WARN personal_info.{field} is missing")

# Resume file
resume_found = False
for rp in [root / "uploads" / "EdrickChang_Resume.pdf",
           Path(os.path.expanduser("~/Downloads/EdrickChang_Resume.pdf")),
           Path(r"C:\Users\Owner\Downloads\EdrickChang.pdf")]:
    if rp.exists():
        print(f"  OK Resume found: {rp}")
        resume_found = True
        break
if not resume_found:
    warnings.append("No resume PDF found")

# Test Gemini API
if os.getenv("GEMINI_API_KEY"):
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=os.getenv("GEMINI_API_KEY"),
        )
        response = client.chat.completions.create(
            model="gemini-2.5-flash",
            max_tokens=20,
            messages=[{"role": "user", "content": "Say OK"}],
        )
        content = response.choices[0].message.content or ""
        print(f"  OK Gemini API works: {content[:30]}")
    except Exception as e:
        errors.append(f"Gemini API failed: {e}")
        print(f"  FAIL Gemini API: {e}")

# Summary
print(f"\n{'='*50}")
if errors:
    print(f"FAILED: {len(errors)} error(s)")
    for e in errors:
        print(f"  FAIL {e}")
else:
    print("PASSED: Environment OK")
if warnings:
    print(f"WARNINGS: {len(warnings)}")
    for w in warnings:
        print(f"  WARN {w}")

sys.exit(1 if errors else 0)

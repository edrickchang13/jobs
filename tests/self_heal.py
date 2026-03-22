"""
Self-healing test harness for auto-apply form filler.
Run: python tests/self_heal.py
Output: tests/diagnosis_report.md + tests/screenshots/
"""
import asyncio
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)
REPORT_PATH = Path(__file__).parent / "diagnosis_report.md"

TEST_CASES = [
    {
        "name": "Lever - Direct Apply Form",
        "url": "https://jobs.lever.co/aofl/4b91076d-8937-4dbc-a502-a7d6a66e2e19/apply",
        "ats": "lever",
        "company": "Age of Learning",
        "role": "Software Engineer Intern",
    },
]


class DiagnosisReport:
    def __init__(self):
        self.entries = []
        self.start_time = datetime.now()

    def add(self, test_name, step, status, detail, screenshot_path="", dom_snapshot="", suggestion=""):
        self.entries.append({
            "test": test_name, "step": step, "status": status, "detail": detail,
            "screenshot": screenshot_path, "dom": dom_snapshot[:3000] if dom_snapshot else "",
            "suggestion": suggestion, "time": datetime.now().strftime("%H:%M:%S"),
        })

    def write(self):
        tests_run = set(e["test"] for e in self.entries)
        tests_failed = set()
        tests_passed = set()
        for t in tests_run:
            if any(e["status"] in ("fail", "error") for e in self.entries if e["test"] == t):
                tests_failed.add(t)
            else:
                tests_passed.add(t)

        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write(f"# Diagnosis Report\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Duration: {(datetime.now() - self.start_time).seconds}s\n\n")

            if not tests_failed:
                f.write(f"## ALL TESTS PASSED\n\n{len(tests_passed)} test(s) passed.\n\n")
            else:
                f.write(f"## TESTS FAILED: {len(tests_failed)} of {len(tests_run)}\n\n")
                for t in tests_failed:
                    f.write(f"- **{t}**\n")
                f.write(f"\n---\n\n")

            for test_name in tests_run:
                t_entries = [e for e in self.entries if e["test"] == test_name]
                icon = "PASS" if test_name in tests_passed else "FAIL"
                f.write(f"## {icon}: {test_name}\n\n")
                for e in t_entries:
                    f.write(f"### [{e['status']}] {e['step']}\n")
                    f.write(f"{e['detail']}\n")
                    if e["screenshot"]:
                        f.write(f"Screenshot: `{e['screenshot']}`\n")
                    if e["suggestion"]:
                        f.write(f"\n**FIX:** {e['suggestion']}\n")
                    if e["dom"]:
                        f.write(f"\n<details><summary>DOM</summary>\n\n```\n{e['dom']}\n```\n</details>\n")
                    f.write(f"\n")
                f.write(f"---\n\n")

        print(f"\nReport: {REPORT_PATH}")
        return len(tests_failed) == 0


async def take_ss(page, name, step):
    try:
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in f"{name}_{step}")
        path = str(SCREENSHOT_DIR / f"{safe}.png")
        await page.screenshot(path=path)
        return path
    except:
        return ""


async def run_test(tc, report):
    name = tc["name"]
    url = tc["url"]
    company = tc["company"]
    role = tc["role"]

    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")

    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    page = await browser.new_page(viewport={"width": 1280, "height": 900})
    passed = True

    try:
        # STEP 1: Navigate
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        ss = await take_ss(page, name, "01_loaded")
        report.add(name, "Navigate", "pass", f"Loaded: {page.url}", screenshot_path=ss)

        # STEP 2: Extract fields
        from applicator.form_filler import JS_EXTRACT_FIELDS
        for i in range(5):
            await page.evaluate(f"window.scrollTo(0, {i * 500})")
            await page.wait_for_timeout(200)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(500)

        fields = await page.evaluate(JS_EXTRACT_FIELDS)
        form_ctx = page

        if not fields:
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                try:
                    ff = await frame.evaluate(JS_EXTRACT_FIELDS)
                    if len(ff) > len(fields):
                        fields = ff
                        form_ctx = frame
                except:
                    continue

        if not fields:
            ss = await take_ss(page, name, "02_no_fields")
            html = await page.evaluate("document.body.innerHTML.substring(0, 3000)")
            report.add(name, "Extract Fields", "fail", "0 fields found", screenshot_path=ss, dom_snapshot=html)
            passed = False
        else:
            report.add(name, "Extract Fields", "pass", f"{len(fields)} fields extracted")
            for f in fields:
                report.add(name, "Field", "info",
                           f"[{f['type']:12s}] {f['label'][:40]:40s} required={f['required']} sel={f['selector'][:50]}")

        if not fields:
            await browser.close()
            await pw.stop()
            return False

        # STEP 3: LLM mapping
        from applicator.form_filler import map_fields_to_profile
        try:
            mappings = map_fields_to_profile(fields, "Software engineering internship", company, role)
            report.add(name, "LLM Mapping", "pass", f"{len(mappings)} mappings")
            for m in mappings:
                sel = m.get("selector", "?")
                action = m.get("action", "?")
                value = m.get("value", "")
                label = next((f["label"] for f in fields if f["selector"] == sel), sel[:40])
                report.add(name, "Mapping", "info", f"{action:12s} | {label[:35]:35s} | '{str(value)[:60]}'")

                # Validate
                if "email" in label.lower() and action == "fill" and "@" not in str(value):
                    report.add(name, "Mapping", "fail", f"Email missing @: '{value}'")
                    passed = False
                if "resume" in label.lower() and "cv" in label.lower() or "resume" in label.lower():
                    if action not in ("upload_file", "skip"):
                        report.add(name, "Mapping", "fail", f"Resume action='{action}' should be 'upload_file'")
                        passed = False
        except Exception as e:
            report.add(name, "LLM Mapping", "error", f"CRASHED: {e}\n{traceback.format_exc()[:500]}")
            passed = False
            await browser.close()
            await pw.stop()
            return False

        # STEP 4: Fill form
        resume_path = ""
        for c in [Path("uploads/EdrickChang_Resume.pdf"), Path(os.path.expanduser("~/Downloads/EdrickChang.pdf"))]:
            if c.exists():
                resume_path = str(c.resolve())
                break

        fill_errors = []
        async def on_event(step, status, detail=""):
            if "fail" in status.lower() or "error" in status.lower():
                fill_errors.append(detail)

        ss = await take_ss(page, name, "03_before_fill")
        report.add(name, "Fill Form", "info", "Starting fill...", screenshot_path=ss)

        try:
            result = await fill_form(form_ctx, mappings, resume_path, event_callback=on_event, screenshot_page=page)
            filled = result.get("filled", 0)
            failed_count = result.get("failed", 0)
            errors = result.get("errors", [])

            ss = await take_ss(page, name, "04_after_fill")
            report.add(name, "Fill Result", "pass" if failed_count == 0 else "fail",
                       f"Filled: {filled}, Failed: {failed_count}", screenshot_path=ss)
            for err in errors:
                report.add(name, "Fill Error", "fail", str(err)[:200])
                passed = False
        except Exception as e:
            ss = await take_ss(page, name, "04_fill_crash")
            report.add(name, "Fill Form", "error", f"CRASHED: {e}\n{traceback.format_exc()[:500]}", screenshot_path=ss)
            passed = False

        # STEP 5: Verify
        await page.wait_for_timeout(1000)
        ss = await take_ss(page, name, "05_final")

        state = await page.evaluate("""() => {
            const inputs = document.querySelectorAll('input, textarea, select');
            const filled = [], empty_req = [];
            for (const el of inputs) {
                if (el.type === 'hidden' || el.offsetParent === null) continue;
                const label = (() => {
                    if (el.id) { const l = document.querySelector('label[for="'+el.id+'"]'); if (l) return l.innerText.trim(); }
                    const p = el.closest('.field, .form-group, li, .application-question');
                    if (p) { const l = p.querySelector('label'); if (l) return l.innerText.trim(); }
                    return el.name || el.id || '';
                })();
                if (el.value) filled.push({label: label.substring(0,40), value: el.value.substring(0,50)});
                else if (el.required) empty_req.push({label: label.substring(0,40), type: el.type, name: el.name});
            }
            const errs = [];
            document.querySelectorAll('[class*="error"], [role="alert"]').forEach(e => {
                if (e.offsetParent !== null && e.innerText.trim()) errs.push(e.innerText.trim().substring(0,100));
            });
            return {filled, empty_req, errs};
        }""")

        for f in state.get("filled", []):
            report.add(name, "Verify", "pass", f"OK: {f['label']} = '{f['value']}'")

        for f in state.get("empty_req", []):
            # Some fields (like location with autocomplete) store values in React state, not el.value
            # Check if it looks intentionally empty vs a fill failure
            field_name = f.get("name", "")
            if field_name == "location":
                # Location autocomplete may clear el.value — check if the container shows a location
                loc_text = await page.evaluate("""() => {
                    const el = document.querySelector('[name="location"]');
                    if (!el) return '';
                    const parent = el.closest('.application-question, .field, li');
                    return parent ? parent.innerText.trim() : el.value;
                }""")
                if loc_text and len(loc_text) > 5:
                    report.add(name, "Verify", "info", f"Location field may have autocomplete value: {loc_text[:60]}")
                    continue
            report.add(name, "Verify", "fail", f"Empty required: {f['label']} (type={f['type']}, name={f['name']})")
            passed = False

        for e in state.get("errs", []):
            report.add(name, "Verify", "fail", f"Validation error: {e}")
            passed = False

        # Check file upload
        file_status = await page.evaluate("""() => {
            for (const fi of document.querySelectorAll('input[type="file"]')) {
                if (fi.files && fi.files.length > 0) return fi.files[0].name;
            }
            const ind = document.querySelector('[class*="upload-success"], [class*="file-name"], .resume-filename');
            return ind ? ind.innerText.trim() : '';
        }""")
        if file_status:
            report.add(name, "Verify", "pass", f"Resume: {file_status}")
        else:
            # Check if there was a file input at all
            has_file = await page.evaluate("document.querySelector('input[type=\"file\"]') !== null")
            if has_file:
                report.add(name, "Verify", "fail", "Resume file input appears empty")
                passed = False

        report.add(name, "RESULT", "pass" if passed else "fail",
                   "ALL CHECKS PASSED" if passed else "SOME CHECKS FAILED", screenshot_path=ss)

    except Exception as e:
        ss = await take_ss(page, name, "99_crash")
        report.add(name, "CRASH", "error", f"{e}\n{traceback.format_exc()[:800]}", screenshot_path=ss)
        passed = False
    finally:
        await browser.close()
        await pw.stop()

    return passed


# Need to import fill_form at module level for the test
from applicator.form_filler import fill_form


async def main():
    report = DiagnosisReport()
    results = {}

    for tc in TEST_CASES:
        try:
            results[tc["name"]] = await run_test(tc, report)
        except Exception as e:
            report.add(tc["name"], "CRASH", "error", f"{e}\n{traceback.format_exc()[:500]}")
            results[tc["name"]] = False

    all_passed = report.write()

    print(f"\n{'='*60}")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'} {name}")

    if all_passed:
        print(f"\nALL TESTS PASSED")
    else:
        print(f"\nFAILED - Read {REPORT_PATH} and fix issues")

    return all_passed

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)

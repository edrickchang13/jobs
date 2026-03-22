"""Level 3: Test LLM field mapping produces correct values."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

FAKE_LEVER_FIELDS = [
    {"selector": '[name="name"]', "tag": "input", "type": "text", "label": "Full name", "required": True, "options": [], "placeholder": ""},
    {"selector": '[name="email"]', "tag": "input", "type": "email", "label": "Email", "required": True, "options": [], "placeholder": ""},
    {"selector": '[name="phone"]', "tag": "input", "type": "tel", "label": "Phone", "required": False, "options": [], "placeholder": ""},
    {"selector": '[name="org"]', "tag": "input", "type": "text", "label": "Current company", "required": False, "options": [], "placeholder": ""},
    {"selector": '[name="urls[LinkedIn]"]', "tag": "input", "type": "text", "label": "LinkedIn URL", "required": False, "options": [], "placeholder": ""},
    {"selector": '[name="urls[GitHub]"]', "tag": "input", "type": "text", "label": "GitHub URL", "required": False, "options": [], "placeholder": ""},
    {"selector": 'input[type="file"]', "tag": "input", "type": "file", "label": "Resume/CV", "required": True, "options": [], "placeholder": ""},
    {"selector": '[name="cards[q1]"]', "tag": "textarea", "type": "textarea", "label": "Why are you interested in this role?", "required": True, "options": [], "placeholder": ""},
    {"selector": '[name="cards[q2]"]', "tag": "select", "type": "select-one", "label": "How did you hear about us?", "required": True, "options": [
        {"value": "", "text": "Select..."},
        {"value": "job_board", "text": "Job Board"},
        {"value": "linkedin", "text": "LinkedIn"},
        {"value": "referral", "text": "Employee Referral"},
        {"value": "other", "text": "Other"},
    ], "placeholder": ""},
    {"selector": '[name="cards[q3]"]', "tag": "select", "type": "select-one", "label": "Are you authorized to work in the US?", "required": True, "options": [
        {"value": "", "text": "Select..."},
        {"value": "yes", "text": "Yes"},
        {"value": "no", "text": "No"},
    ], "placeholder": ""},
    {"selector": '[name="cards[q4]"]', "tag": "select", "type": "select-one", "label": "Will you require sponsorship?", "required": True, "options": [
        {"value": "", "text": "Select..."},
        {"value": "yes", "text": "Yes"},
        {"value": "no", "text": "No"},
    ], "placeholder": ""},
]

def test_mapping():
    from applicator.form_filler import map_fields_to_profile

    print("Testing LLM field mapping...")
    print(f"Input: {len(FAKE_LEVER_FIELDS)} fields\n")

    try:
        mappings = map_fields_to_profile(
            FAKE_LEVER_FIELDS,
            job_description="Software engineering internship building educational products for children.",
            company="Age of Learning",
            role="Software Engineer Intern",
        )
    except Exception as e:
        print(f"FAIL map_fields_to_profile() CRASHED: {e}")
        import traceback
        traceback.print_exc()
        return False

    print(f"Output: {len(mappings)} mappings\n")

    errors = []
    for m in mappings:
        selector = m.get("selector", "?")
        action = m.get("action", "?")
        value = m.get("value", "")
        field_label = next((f["label"] for f in FAKE_LEVER_FIELDS if f["selector"] == selector), selector)

        print(f"  {action:12s} | {field_label[:35]:35s} | {str(value)[:60]}")

        if "name" in field_label.lower() and "full" in field_label.lower():
            if action != "fill" or "edrick" not in str(value).lower():
                errors.append(f"Full name wrong: action={action} value='{value}'")
        if "email" == field_label.lower():
            if action != "fill" or "eachang" not in str(value).lower():
                errors.append(f"Email wrong: value='{value}'")
        if field_label == "Resume/CV":
            if action != "upload_file":
                errors.append(f"Resume should be upload_file, got '{action}'")
        if "how did you hear" in field_label.lower():
            if action != "select":
                errors.append(f"'How did you hear' should be select, got '{action}'")
        if "authorized" in field_label.lower():
            if "yes" not in str(value).lower():
                errors.append(f"Work auth should be 'Yes', got '{value}'")
        if "sponsorship" in field_label.lower():
            if "no" not in str(value).lower():
                errors.append(f"Sponsorship should be 'No', got '{value}'")
        if "why" in field_label.lower() and "interested" in field_label.lower():
            if action != "fill" or len(str(value)) < 30:
                errors.append(f"'Why interested' too short: {len(str(value))} chars")

    print()
    if errors:
        print(f"FAILED: {len(errors)} error(s)")
        for e in errors:
            print(f"  - {e}")
        return False
    else:
        print("PASSED: All mappings correct!")
        return True

success = test_mapping()
sys.exit(0 if success else 1)

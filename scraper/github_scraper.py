import re
import requests
from bs4 import BeautifulSoup
from config import GITHUB_REPO_URL
from applicator.ats_profiles import detect_ats


def fetch_readme() -> str:
    """Fetch raw README content from GitHub."""
    response = requests.get(GITHUB_REPO_URL)
    response.raise_for_status()
    return response.text


def parse_internship_table(readme_text: str) -> list[dict]:
    """
    Parse the HTML tables from the SimplifyJobs README.

    The README contains HTML <table> elements (not markdown tables).
    Each row has: Company | Role | Location | Application (with Apply link) | Age

    Returns list of dicts with keys: company, role, location, url, date
    """
    soup = BeautifulSoup(readme_text, "html.parser")
    postings = []

    # Track current company for sub-listings that use "↳" instead of company name
    current_company = ""

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            # --- Company ---
            company_cell = cells[0]
            company_link = company_cell.find("a")
            if company_link:
                current_company = company_link.get_text(strip=True)
            elif "↳" in company_cell.get_text():
                # Sub-listing under the same company
                pass
            else:
                continue

            company = current_company

            # --- Role ---
            role_text = cells[1].get_text(strip=True)
            # Remove emoji markers from role
            role = re.sub(r"[🛂🇺🇸🎓🔒🔥]", "", role_text).strip()

            # Skip closed listings (🔒 emoji)
            if "🔒" in role_text:
                continue

            # --- Location ---
            # Locations can be separated by <br> tags
            location = cells[2].get_text(separator=", ", strip=True)

            # --- Application URL ---
            app_cell = cells[3]
            # The first <a> with an <img> is the Apply button
            apply_link = None
            for a_tag in app_cell.find_all("a"):
                img = a_tag.find("img")
                if img and "apply" in (img.get("alt", "")).lower():
                    apply_link = a_tag.get("href", "")
                    break

            if not apply_link:
                # Fallback: grab the first link in the cell
                first_link = app_cell.find("a")
                if first_link:
                    apply_link = first_link.get("href", "")

            if not apply_link:
                continue

            url = apply_link

            # --- Age ---
            age = cells[4].get_text(strip=True)

            postings.append({
                "company": company,
                "role": role,
                "location": location,
                "url": url,
                "date": age,
                "ats": detect_ats(url) or "unknown",
            })

    # Deduplicate: prefer first occurrence, dedupe by (company, role) keeping
    # the one with the most specific URL (non-Simplify redirect preferred).
    seen_keys: set = set()
    seen_urls: set = set()
    deduped = []
    for p in postings:
        # Strip UTM params for URL-level dedup
        clean_url = re.sub(r'[?&](utm_[^&]*|ref=[^&]*|source=[^&]*)', '', p["url"]).rstrip('?&')
        key = (p["company"].lower(), p["role"].lower())
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(p)

    return deduped


def get_new_postings() -> list[dict]:
    """Fetch README, parse tables, and return only unseen postings."""
    from database.tracker import is_posting_seen

    readme = fetch_readme()
    all_postings = parse_internship_table(readme)
    new_postings = [p for p in all_postings if not is_posting_seen(p["url"])]
    return new_postings

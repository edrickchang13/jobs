import requests
from bs4 import BeautifulSoup


def extract_job_description(url: str) -> str:
    """
    Attempt to extract job description text from a posting URL.
    Uses requests first (fast), falls back gracefully if page requires JS.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Remove script and style elements
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()

        # Common job description containers across ATS platforms
        selectors = [
            "div.content",             # Greenhouse
            "div.posting-page",        # Lever
            "div[data-automation-id]",  # Workday
            "div.job-description",     # Generic
            "div.description",         # Generic
            "article",                 # Generic
            "main",                    # Generic fallback
        ]

        for selector in selectors:
            element = soup.select_one(selector)
            if element and len(element.get_text(strip=True)) > 200:
                return element.get_text(separator="\n", strip=True)[:5000]

        # Fallback: get all text from body
        body = soup.find("body")
        if body:
            return body.get_text(separator="\n", strip=True)[:5000]

        return "Job description could not be extracted."

    except Exception as e:
        return f"Failed to fetch job description: {str(e)}"

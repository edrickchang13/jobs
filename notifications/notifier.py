import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def send_notification(message: str):
    """Send a notification via Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[NOTIFICATION] {message}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }

    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[NOTIFICATION FAILED] {e}")
        print(f"[MESSAGE] {message}")


def send_application_ready(company: str, role: str, url: str):
    """Notify that an application is ready for review."""
    message = (
        f"*Application Ready for Review*\n\n"
        f"Company: {company}\n"
        f"Role: {role}\n"
        f"URL: {url}\n\n"
        f"The form has been filled out. Please review and submit manually."
    )
    send_notification(message)


def send_error(company: str, role: str, error: str):
    """Notify of an application error."""
    message = (
        f"*Application Error*\n\n"
        f"Company: {company}\n"
        f"Role: {role}\n"
        f"Error: {error}"
    )
    send_notification(message)

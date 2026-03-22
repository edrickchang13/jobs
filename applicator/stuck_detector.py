"""
Detects when the automation is stuck in a loop doing the same thing.
Tracks page URLs, page content hashes, and action history.
"""
import hashlib
from collections import deque


class StuckDetector:
    def __init__(self, max_repeats: int = 3, history_size: int = 10):
        self.max_repeats = max_repeats
        self.history = deque(maxlen=history_size)
        self.url_history = deque(maxlen=history_size)
        self.action_history = deque(maxlen=history_size)
        self.stuck_reason = ""

    def _hash_state(self, url: str, page_text_snippet: str) -> str:
        content = f"{url}|{page_text_snippet[:500]}"
        return hashlib.md5(content.encode()).hexdigest()

    def check(self, url: str, page_text_snippet: str, action_description: str = "") -> bool:
        state_hash = self._hash_state(url, page_text_snippet)
        self.history.append(state_hash)
        self.url_history.append(url)
        if action_description:
            self.action_history.append(action_description)

        if self.history.count(state_hash) >= self.max_repeats:
            self.stuck_reason = (
                f"Same page state seen {self.history.count(state_hash)} times. "
                f"URL: {url[:80]}. Recent actions: {list(self.action_history)[-5:]}. "
                f"Page starts with: {page_text_snippet[:200]}"
            )
            return True

        recent_urls = list(self.url_history)[-self.max_repeats:]
        if len(recent_urls) >= self.max_repeats and len(set(recent_urls)) == 1:
            recent_hashes = list(self.history)[-self.max_repeats:]
            if len(set(recent_hashes)) == 1:
                self.stuck_reason = (
                    f"URL unchanged for {self.max_repeats} steps: {url[:80]}. "
                    f"Recent actions: {list(self.action_history)[-5:]}"
                )
                return True

        verification_keywords = [
            "verify your email", "check your email", "verification code",
            "confirm your email", "we sent you", "check your inbox",
            "verification link", "verify account", "confirm account",
            "enter the code", "enter code", "verification email",
        ]
        text_lower = page_text_snippet.lower()
        if any(kw in text_lower for kw in verification_keywords):
            if self.history.count(state_hash) >= 2:
                self.stuck_reason = (
                    f"STUCK ON EMAIL VERIFICATION PAGE. URL: {url[:80]}. "
                    f"Need to check email for verification code/link."
                )
                return True

        return False

    def is_verification_page(self, page_text: str) -> bool:
        keywords = [
            "verify your email", "check your email", "verification code",
            "confirm your email", "we sent you", "check your inbox",
            "verification link", "verify account", "enter the code",
        ]
        text_lower = page_text.lower()
        return any(kw in text_lower for kw in keywords)

    def reset(self):
        self.history.clear()
        self.url_history.clear()
        self.action_history.clear()
        self.stuck_reason = ""

import sqlite3
import json
from datetime import datetime
from config import DB_PATH


def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS seen_postings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            location TEXT,
            url TEXT UNIQUE NOT NULL,
            date_posted TEXT,
            date_seen TEXT NOT NULL,
            status TEXT DEFAULT 'new'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            posting_id INTEGER NOT NULL,
            resume_path TEXT,
            answers_json TEXT,
            screenshot_path TEXT,
            submitted_at TEXT,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (posting_id) REFERENCES seen_postings(id)
        )
    """)
    conn.commit()
    conn.close()


def is_posting_seen(url: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM seen_postings WHERE url = ?", (url,))
    result = c.fetchone()
    conn.close()
    return result is not None


def add_posting(company, role, location, url, date_posted):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        INSERT OR IGNORE INTO seen_postings (company, role, location, url, date_posted, date_seen)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (company, role, location, url, date_posted, datetime.now().isoformat()),
    )
    conn.commit()
    posting_id = c.lastrowid
    conn.close()
    return posting_id


def log_application(posting_id, resume_path, answers, screenshot_path, status="pending"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO applications (posting_id, resume_path, answers_json, screenshot_path, submitted_at, status)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (
            posting_id,
            resume_path,
            json.dumps(answers),
            screenshot_path,
            datetime.now().isoformat(),
            status,
        ),
    )
    conn.commit()
    conn.close()


def update_posting_status(posting_id, status):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE seen_postings SET status = ? WHERE id = ?", (status, posting_id))
    conn.commit()
    conn.close()


def mark_applied(url: str, company: str = "", role: str = ""):
    """Mark a job URL as applied."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS applied_jobs (
            url TEXT PRIMARY KEY,
            company TEXT,
            role TEXT,
            applied_at TEXT NOT NULL
        )
    """)
    c.execute(
        "INSERT OR REPLACE INTO applied_jobs (url, company, role, applied_at) VALUES (?, ?, ?, ?)",
        (url, company, role, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def unmark_applied(url: str):
    """Remove a job URL from applied list."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM applied_jobs WHERE url = ?", (url,))
    conn.commit()
    conn.close()


def get_applied_urls() -> set:
    """Return set of all applied job URLs."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS applied_jobs (
            url TEXT PRIMARY KEY,
            company TEXT,
            role TEXT,
            applied_at TEXT NOT NULL
        )
    """)
    c.execute("SELECT url FROM applied_jobs")
    urls = {row[0] for row in c.fetchall()}
    conn.close()
    return urls


# ── Starred jobs ────────────────────────────────────────────────────────────

def _ensure_starred_table(c):
    c.execute("""
        CREATE TABLE IF NOT EXISTS starred_jobs (
            url TEXT PRIMARY KEY,
            company TEXT,
            role TEXT,
            starred_at TEXT NOT NULL,
            resume_path TEXT DEFAULT NULL,
            resume_status TEXT DEFAULT 'pending'
        )
    """)


def star_job(url: str, company: str, role: str):
    """Add a job to the starred list."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    _ensure_starred_table(c)
    c.execute(
        "INSERT OR IGNORE INTO starred_jobs (url, company, role, starred_at) VALUES (?, ?, ?, ?)",
        (url, company, role, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def unstar_job(url: str):
    """Remove a job from the starred list."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    _ensure_starred_table(c)
    c.execute("DELETE FROM starred_jobs WHERE url = ?", (url,))
    conn.commit()
    conn.close()


def get_starred_urls() -> set:
    """Return set of all starred job URLs."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    _ensure_starred_table(c)
    c.execute("SELECT url FROM starred_jobs")
    urls = {row[0] for row in c.fetchall()}
    conn.close()
    return urls


def update_star_resume(url: str, resume_path: str, status: str = "done"):
    """Record the generated resume path for a starred job."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    _ensure_starred_table(c)
    c.execute(
        "UPDATE starred_jobs SET resume_path = ?, resume_status = ? WHERE url = ?",
        (resume_path, status, url),
    )
    conn.commit()
    conn.close()


def get_starred_jobs() -> list:
    """Return list of all starred jobs with their resume status."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    _ensure_starred_table(c)
    c.execute("SELECT url, company, role, starred_at, resume_path, resume_status FROM starred_jobs ORDER BY starred_at DESC")
    rows = c.fetchall()
    conn.close()
    return [
        {"url": r[0], "company": r[1], "role": r[2],
         "starred_at": r[3], "resume_path": r[4], "resume_status": r[5]}
        for r in rows
    ]

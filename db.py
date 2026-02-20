# db.py
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path("data/jobs.db")

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)

def init_db():
    with connect() as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_job_id TEXT,
            url TEXT,
            title TEXT,
            company TEXT,
            location TEXT,
            contract TEXT,
            seniority TEXT,
            remote TEXT,
            published_at TEXT,
            description TEXT,
            apply_email TEXT,
            hash TEXT UNIQUE
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_hash TEXT NOT NULL,
            status TEXT NOT NULL, -- drafted/sent/skipped
            sent_at TEXT,
            to_email TEXT,
            subject TEXT,
            body TEXT
        );
        """)
        con.commit()

def upsert_job(job: dict):
    # job must include 'hash'
    with connect() as con:
        cur = con.cursor()
        cur.execute("""
        INSERT OR IGNORE INTO jobs(
          source, source_job_id, url, title, company, location, contract,
          seniority, remote, published_at, description, apply_email, hash
        ) VALUES (
          :source, :source_job_id, :url, :title, :company, :location, :contract,
          :seniority, :remote, :published_at, :description, :apply_email, :hash
        );
        """, job)
        con.commit()

def list_jobs(filters: dict):
    q = "SELECT * FROM jobs WHERE 1=1"
    params = {}

    if filters.get("query"):
        q += " AND (lower(title) LIKE :q OR lower(description) LIKE :q OR lower(company) LIKE :q)"
        params["q"] = f"%{filters['query'].lower()}%"

    if filters.get("location"):
        q += " AND lower(location) LIKE :loc"
        params["loc"] = f"%{filters['location'].lower()}%"

    if filters.get("remote") and filters["remote"] != "Tous":
        q += " AND remote = :remote"
        params["remote"] = filters["remote"]

    if filters.get("not_applied"):
        q += """ AND hash NOT IN (SELECT job_hash FROM applications WHERE status='sent') """

    q += " ORDER BY published_at DESC"

    with connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(q, params)
        return [dict(r) for r in cur.fetchall()]

def get_job_by_hash(h: str):
    with connect() as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT * FROM jobs WHERE hash = ?", (h,))
        row = cur.fetchone()
        return dict(row) if row else None

def log_application(job_hash: str, status: str, to_email: str, subject: str, body: str):
    with connect() as con:
        cur = con.cursor()
        cur.execute("""
        INSERT INTO applications(job_hash, status, sent_at, to_email, subject, body)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (job_hash, status, datetime.utcnow().isoformat(), to_email, subject, body))
        con.commit()

def count_sent_today():
    # naive UTC day
    from datetime import date
    today = date.today().isoformat()
    with connect() as con:
        cur = con.cursor()
        cur.execute("""
        SELECT COUNT(*) FROM applications
        WHERE status='sent' AND substr(sent_at,1,10)=?
        """, (today,))
        return cur.fetchone()[0]

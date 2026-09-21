"""SQLite storage. Single file, WAL mode, safe for a scraper + dashboard running concurrently."""
import sqlite3, json, threading
from .config import DB_PATH, ensure_dirs
from .util import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  domain TEXT,
  category TEXT,
  relevance INTEGER DEFAULT 50,
  dynamic_score REAL DEFAULT 0,
  careers_url TEXT,
  ats_provider TEXT,
  ats_token TEXT,
  ats_extra TEXT,
  linkedin_company_id TEXT,
  hq TEXT,
  notes TEXT,
  active INTEGER DEFAULT 1,
  source TEXT DEFAULT 'seed',
  discovered_hits INTEGER DEFAULT 0,
  added_at TEXT,
  last_scraped_at TEXT,
  last_status TEXT,
  last_error TEXT,
  jobs_total INTEGER DEFAULT 0,
  jobs_relevant INTEGER DEFAULT 0,
  jobs_intern INTEGER DEFAULT 0,
  consecutive_failures INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  ext_id TEXT NOT NULL,
  source TEXT,
  title TEXT,
  location TEXT,
  url TEXT,
  apply_url TEXT,
  department TEXT,
  employment_type TEXT,
  posted_at TEXT,
  updated_at TEXT,
  description TEXT,
  remote INTEGER DEFAULT 0,
  is_intern INTEGER DEFAULT 0,
  is_newgrad INTEGER DEFAULT 0,
  relevance INTEGER DEFAULT 0,
  keywords TEXT,
  first_seen TEXT,
  last_seen TEXT,
  status TEXT DEFAULT 'open',
  closed_at TEXT,
  reopened_count INTEGER DEFAULT 0,
  link_ok INTEGER,
  link_status INTEGER,
  link_checked_at TEXT,
  user_status TEXT DEFAULT 'none',
  applied_at TEXT,
  user_notes TEXT,
  starred INTEGER DEFAULT 0,
  is_us INTEGER,
  degree TEXT DEFAULT 'any',
  UNIQUE(company_id, ext_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, is_intern, relevance);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT, finished_at TEXT, kind TEXT,
  companies_total INTEGER DEFAULT 0, companies_ok INTEGER DEFAULT 0, companies_err INTEGER DEFAULT 0,
  jobs_seen INTEGER DEFAULT 0, jobs_new INTEGER DEFAULT 0, jobs_closed INTEGER DEFAULT 0, jobs_reopened INTEGER DEFAULT 0,
  links_checked INTEGER DEFAULT 0, links_broken INTEGER DEFAULT 0,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  ts TEXT, run_id INTEGER, job_id INTEGER, company_id INTEGER, kind TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  name TEXT,
  title TEXT,
  role_type TEXT,            -- recruiter | university_recruiter | hiring_manager | engineering_lead | inbox
  email TEXT,
  email_confidence TEXT,     -- found | pattern | guess | none
  linkedin_url TEXT,
  source TEXT,               -- ddg:linkedin | careers_page | job_posting | contact_page | pattern
  source_url TEXT,
  found_at TEXT,
  last_seen TEXT,
  contacted INTEGER DEFAULT 0,
  contacted_at TEXT,
  user_notes TEXT,
  hidden INTEGER DEFAULT 0,
  UNIQUE(company_id, linkedin_url),
  UNIQUE(company_id, email)
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);
"""

_lock = threading.RLock()

def connect(path=None):
    ensure_dirs()
    conn = sqlite3.connect(str(path or DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn

def _migrate(conn):
    have = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    for col, ddl in (("is_us", "INTEGER"), ("degree", "TEXT DEFAULT 'any'")):
        if col not in have: conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
    have = {r[1] for r in conn.execute("PRAGMA table_info(companies)")}
    for col, ddl in (("is_us", "INTEGER DEFAULT 1"), ("contacts_checked_at", "TEXT"), ("email_pattern", "TEXT"), ("contacts_n", "INTEGER DEFAULT 0")):
        if col not in have: conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {ddl}")
    conn.commit()

def rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]

def one(conn, sql, args=()):
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None

def get_setting(conn, key, default=None):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default

def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value)))
    conn.commit()

def upsert_company(conn, c):
    """c: dict with slug,name,... Returns id. Does not overwrite user-tunable fields on update except when provided."""
    cols = ["slug","name","domain","category","relevance","careers_url","ats_provider","ats_token",
            "ats_extra","linkedin_company_id","hq","notes","active","source","is_us"]
    vals = {k: c.get(k) for k in cols}
    if isinstance(vals["ats_extra"], (dict, list)): vals["ats_extra"] = json.dumps(vals["ats_extra"])
    if vals["active"] is None: vals["active"] = 1
    if vals["relevance"] is None: vals["relevance"] = 50
    if vals["source"] is None: vals["source"] = "seed"
    if vals["is_us"] is None: vals["is_us"] = 1
    existing = one(conn, "SELECT id FROM companies WHERE slug=?", (vals["slug"],))
    if existing:
        sets = ", ".join(f"{k}=?" for k in cols if k != "slug")
        conn.execute(f"UPDATE companies SET {sets} WHERE slug=?", [vals[k] for k in cols if k != "slug"] + [vals["slug"]])
        return existing["id"]
    vals["added_at"] = now_iso()
    keys = list(vals.keys())
    conn.execute(f"INSERT INTO companies({','.join(keys)}) VALUES({','.join('?'*len(keys))})", [vals[k] for k in keys])
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

def log_event(conn, kind, detail="", run_id=None, job_id=None, company_id=None):
    conn.execute("INSERT INTO events(ts,run_id,job_id,company_id,kind,detail) VALUES(?,?,?,?,?,?)",
                 (now_iso(), run_id, job_id, company_id, kind, detail))

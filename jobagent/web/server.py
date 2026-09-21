"""Dashboard: stdlib HTTP server + JSON API. No external deps."""
import json, subprocess, sys, threading, datetime as dt
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from ..db import connect, rows, one, log_event
from ..config import settings, DATA, ROOT
from ..util import now_iso

HERE = Path(__file__).parent
_lock = threading.Lock()
_scrape_proc = {"p": None, "started": None}

def _conn():
    return connect()

def api_summary(conn, q):
    st = settings()
    kp = {}
    kp["open_intern"] = one(conn, "SELECT COUNT(*) n FROM jobs WHERE status='open' AND is_intern=1")["n"]
    kp["open_phd"] = one(conn, "SELECT COUNT(*) n FROM jobs WHERE status='open' AND is_intern=1 AND degree LIKE '%phd%'")["n"]
    kp["open_bs"] = one(conn, "SELECT COUNT(*) n FROM jobs WHERE status='open' AND is_intern=1 AND (degree LIKE '%bs%' OR degree='any')")["n"]
    kp["open_fulltime"] = one(conn, "SELECT COUNT(*) n FROM jobs WHERE status='open' AND is_intern=0 AND is_newgrad=0")["n"]
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=36)).isoformat()
    kp["new_36h"] = one(conn, "SELECT COUNT(*) n FROM jobs WHERE status='open' AND first_seen>=? AND (is_intern=1 OR is_newgrad=1)", (since,))["n"]
    kp["applied"] = one(conn, "SELECT COUNT(*) n FROM jobs WHERE user_status IN ('applied','interviewing','offer')")["n"]
    kp["interested"] = one(conn, "SELECT COUNT(*) n FROM jobs WHERE user_status='interested' OR starred=1")["n"]
    kp["broken_links"] = one(conn, "SELECT COUNT(*) n FROM jobs WHERE status='open' AND link_ok=0")["n"]
    kp["closed_tracked"] = one(conn, "SELECT COUNT(*) n FROM jobs WHERE status='closed' AND user_status IN ('applied','interviewing','interested')")["n"]
    kp["companies_active"] = one(conn, "SELECT COUNT(*) n FROM companies WHERE active=1")["n"]
    kp["companies_with_source"] = one(conn, "SELECT COUNT(*) n FROM companies WHERE active=1 AND ats_provider!='' AND ats_provider IS NOT NULL")["n"]
    kp["companies_error"] = one(conn, "SELECT COUNT(*) n FROM companies WHERE active=1 AND last_status='error'")["n"]
    runs = rows(conn, "SELECT * FROM runs ORDER BY id DESC LIMIT 30")
    last = runs[0] if runs else None
    return {"kpi": kp, "last_run": last, "runs": list(reversed(runs)), "scraping": _scrape_proc["p"] is not None and _scrape_proc["p"].poll() is None,
            "settings": {k: st[k] for k in ("active_company_limit", "store_min_relevance_intern", "store_min_relevance_fulltime")}, "now": now_iso()}

def api_jobs(conn, q):
    g = lambda k, d=None: (q.get(k) or [d])[0]
    where, args = [], []
    status = g("status", "open")
    if status == "open": where.append("j.status='open'")
    elif status == "closed": where.append("j.status='closed'")
    elif status == "all": where.append("j.status NOT IN ('duplicate','filtered')")
    kind = g("kind", "intern")
    deg = g("degree")
    if deg == "bs": where.append("(j.degree LIKE '%bs%' OR j.degree='any')")
    elif deg == "ms": where.append("(j.degree LIKE '%ms%' OR j.degree='any')")
    elif deg == "phd": where.append("(j.degree LIKE '%phd%' OR j.degree='any')")
    elif deg == "any": where.append("j.degree='any'")
    if kind == "intern": where.append("j.is_intern=1")
    elif kind == "newgrad": where.append("(j.is_newgrad=1 OR j.is_intern=1)")
    elif kind == "fulltime": where.append("j.is_intern=0 AND j.is_newgrad=0")
    us = g("user_status")
    if us == "applied": where.append("j.user_status IN ('applied','interviewing','offer','rejected')")
    elif us == "not_applied": where.append("j.user_status IN ('none','interested')")
    elif us == "starred": where.append("(j.starred=1 OR j.user_status='interested')")
    elif us and us != "any": where.append("j.user_status=?"); args.append(us)
    if g("company"): where.append("j.company_id=?"); args.append(int(g("company")))
    if g("category"): where.append("c.category=?"); args.append(g("category"))
    if g("source"): where.append("j.source=?"); args.append(g("source"))
    if g("min_rel"): where.append("j.relevance>=?"); args.append(int(g("min_rel")))
    if g("since"): where.append("j.first_seen>=?"); args.append(g("since"))
    if g("broken") == "1": where.append("j.link_ok=0")
    if g("q"):
        for tok in g("q").split():
            where.append("(j.title LIKE ? OR c.name LIKE ? OR j.location LIKE ? OR j.description LIKE ?)"); args += [f"%{tok}%"] * 4
    if g("loc"):
        where.append("j.location LIKE ?"); args.append(f"%{g('loc')}%")
    sort = {"relevance": "j.relevance DESC, j.first_seen DESC", "newest": "j.first_seen DESC, j.relevance DESC", "posted": "j.posted_at DESC, j.relevance DESC",
            "company": "c.name ASC, j.relevance DESC", "title": "j.title ASC",
            "degree": "CASE j.degree WHEN 'bs' THEN 0 WHEN 'bs/ms' THEN 1 WHEN 'bs/ms/phd' THEN 2 WHEN 'any' THEN 3 WHEN 'ms' THEN 4 WHEN 'ms/phd' THEN 5 WHEN 'phd' THEN 6 ELSE 7 END, j.relevance DESC"}.get(g("sort", "relevance"), "j.relevance DESC")
    limit = min(int(g("limit", 200)), 2000); offset = int(g("offset", 0))
    sql = f"""SELECT j.id, j.company_id, c.name AS company, c.category, j.title, j.location, j.url, j.apply_url, j.department, j.employment_type, j.posted_at,
              j.first_seen, j.last_seen, j.status, j.closed_at, j.is_intern, j.is_newgrad, j.relevance, j.keywords, j.link_ok, j.link_status, j.link_checked_at,
              j.user_status, j.applied_at, j.user_notes, j.starred, j.source, j.remote, j.is_us, j.degree, substr(j.description,1,600) AS snippet
              FROM jobs j JOIN companies c ON c.id=j.company_id {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY {sort} LIMIT ? OFFSET ?"""
    items = rows(conn, sql, args + [limit, offset])
    total = one(conn, f"SELECT COUNT(*) n FROM jobs j JOIN companies c ON c.id=j.company_id {('WHERE ' + ' AND '.join(where)) if where else ''}", args)["n"]
    return {"items": items, "total": total}

def api_job(conn, jid):
    j = one(conn, "SELECT j.*, c.name AS company FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id=?", (jid,))
    if j: j["events"] = rows(conn, "SELECT ts, kind, detail FROM events WHERE job_id=? ORDER BY id DESC LIMIT 20", (jid,))
    return j

def api_update_job(conn, jid, body):
    sets, args = [], []
    if "user_status" in body:
        sets.append("user_status=?"); args.append(body["user_status"])
        if body["user_status"] == "applied": sets.append("applied_at=COALESCE(applied_at, ?)"); args.append(now_iso())
    if "user_notes" in body: sets.append("user_notes=?"); args.append(body["user_notes"])
    if "starred" in body: sets.append("starred=?"); args.append(1 if body["starred"] else 0)
    if not sets: return {"ok": False}
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", args + [jid]); conn.commit()
    log_event(conn, "user_update", json.dumps(body), job_id=jid); conn.commit()
    return {"ok": True, "job": api_job(conn, jid)}

def api_companies(conn, q):
    g = lambda k, d=None: (q.get(k) or [d])[0]
    where, args = [], []
    if g("active", "1") == "1": where.append("active=1")
    elif g("active") == "0": where.append("active=0")
    if g("q"): where.append("(name LIKE ? OR category LIKE ? OR ats_provider LIKE ?)"); args += [f"%{g('q')}%"] * 3
    if g("status") == "error": where.append("last_status='error'")
    elif g("status") == "nosource": where.append("(ats_provider IS NULL OR ats_provider='')")
    sql = f"""SELECT c.*, (SELECT COUNT(*) FROM jobs WHERE company_id=c.id AND status='open' AND is_intern=1) AS open_intern,
              (SELECT COUNT(*) FROM jobs WHERE company_id=c.id AND status='open') AS open_all,
              (SELECT COUNT(*) FROM jobs WHERE company_id=c.id AND user_status IN ('applied','interviewing','offer')) AS applied
              FROM companies c {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY active DESC, dynamic_score DESC, relevance DESC"""
    return {"items": rows(conn, sql, args), "categories": [r["category"] for r in rows(conn, "SELECT DISTINCT category FROM companies WHERE category IS NOT NULL ORDER BY 1")]}

def api_update_company(conn, cid, body):
    sets, args = [], []
    for k in ("active", "relevance", "notes", "careers_url"):
        if k in body: sets.append(f"{k}=?"); args.append(body[k])
    if sets:
        conn.execute(f"UPDATE companies SET {', '.join(sets)} WHERE id=?", args + [cid]); conn.commit()
    return {"ok": True}

def api_events(conn, q):
    lim = int((q.get("limit") or [100])[0])
    return {"items": rows(conn, """SELECT e.ts, e.kind, e.detail, e.job_id, j.title, j.url, c.name AS company FROM events e LEFT JOIN jobs j ON j.id=e.job_id
                                   LEFT JOIN companies c ON c.id=COALESCE(e.company_id, j.company_id) WHERE e.kind!='user_update' ORDER BY e.id DESC LIMIT ?""", (lim,))}

def api_contacts(conn, q):
    g = lambda k, d=None: (q.get(k) or [d])[0]
    where, args = ["ct.hidden=0"], []
    if g("company"): where.append("ct.company_id=?"); args.append(int(g("company")))
    if g("role"): where.append("ct.role_type=?"); args.append(g("role"))
    if g("has_email") == "1": where.append("ct.email IS NOT NULL AND ct.email!=''")
    if g("contacted") == "1": where.append("ct.contacted=1")
    elif g("contacted") == "0": where.append("ct.contacted=0")
    if g("q"):
        for tok in g("q").split():
            where.append("(ct.name LIKE ? OR ct.title LIKE ? OR ct.email LIKE ? OR c.name LIKE ?)"); args += [f"%{tok}%"] * 4
    if g("with_interns") == "1": where.append("(SELECT COUNT(*) FROM jobs j WHERE j.company_id=c.id AND j.status='open' AND j.is_intern=1) > 0")
    limit = min(int(g("limit", 300)), 3000)
    items = rows(conn, f"""SELECT ct.*, c.name AS company, c.email_pattern, c.contacts_checked_at,
                           (SELECT COUNT(*) FROM jobs j WHERE j.company_id=c.id AND j.status='open' AND j.is_intern=1) AS open_intern
                           FROM contacts ct JOIN companies c ON c.id=ct.company_id WHERE {' AND '.join(where)}
                           ORDER BY open_intern DESC, c.name ASC, CASE ct.role_type WHEN 'university_recruiter' THEN 0 WHEN 'recruiter' THEN 1 WHEN 'inbox' THEN 2 WHEN 'hiring_manager' THEN 3 ELSE 4 END,
                           CASE ct.email_confidence WHEN 'found' THEN 0 WHEN 'pattern' THEN 1 WHEN 'guess' THEN 2 ELSE 3 END LIMIT ?""", args + [limit])
    total = one(conn, f"SELECT COUNT(*) n FROM contacts ct JOIN companies c ON c.id=ct.company_id WHERE {' AND '.join(where)}", args)["n"]
    covered = one(conn, "SELECT COUNT(*) n FROM companies WHERE active=1 AND contacts_checked_at IS NOT NULL")["n"]
    return {"items": items, "total": total, "companies_checked": covered}

def api_update_contact(conn, cid, body):
    sets, args = [], []
    if "contacted" in body:
        sets.append("contacted=?"); args.append(1 if body["contacted"] else 0)
        if body["contacted"]: sets.append("contacted_at=COALESCE(contacted_at, ?)"); args.append(now_iso())
    for k in ("user_notes", "email", "hidden", "name", "title"):
        if k in body: sets.append(f"{k}=?"); args.append(body[k])
    if "email" in body and body.get("email"): sets.append("email_confidence='found'")
    if sets: conn.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id=?", args + [cid]); conn.commit()
    return {"ok": True}

def start_contacts(company_id=None):
    if _scrape_proc["p"] is not None and _scrape_proc["p"].poll() is None: return {"ok": False, "msg": "a job is already running"}
    log = open(DATA.parent / "logs" / "contacts_dashboard.log", "ab")
    cmd = [sys.executable, "-m", "jobagent", "contacts", "--limit", "40"]
    if company_id:
        slug = one(_conn(), "SELECT slug FROM companies WHERE id=?", (company_id,))["slug"]; cmd = [sys.executable, "-m", "jobagent", "contacts", "--only", slug, "--force"]
    _scrape_proc["p"] = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT); _scrape_proc["started"] = now_iso()
    return {"ok": True}

def api_categories(conn):
    return {"items": rows(conn, """SELECT c.category, COUNT(DISTINCT c.id) companies, SUM(CASE WHEN j.status='open' AND j.is_intern=1 THEN 1 ELSE 0 END) open_intern
                                   FROM companies c LEFT JOIN jobs j ON j.company_id=c.id WHERE c.active=1 GROUP BY c.category ORDER BY open_intern DESC""")}

def start_scrape(kind="dashboard"):
    if _scrape_proc["p"] is not None and _scrape_proc["p"].poll() is None: return {"ok": False, "msg": "already running"}
    log = open(DATA.parent / "logs" / "scrape_dashboard.log", "ab")
    _scrape_proc["p"] = subprocess.Popen([sys.executable, "-m", "jobagent", "scrape", "--kind", kind], cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
    _scrape_proc["started"] = now_iso()
    return {"ok": True}

def recheck_link(conn, jid):
    from ..linkcheck import run_linkcheck
    from ..http import default_http
    j = one(conn, "SELECT id, url, link_ok, link_status, status, company_id FROM jobs WHERE id=?", (jid,))
    if not j: return {"ok": False}
    run_linkcheck(default_http(), conn, [j], workers=1)
    return {"ok": True, "job": api_job(conn, jid)}

class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):  # quieter
        if "/api/" not in (a[0] if a else ""): pass
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code); self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))
        self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query); p = u.path.rstrip("/") or "/"
        try:
            if p == "/": return self._send(200, (HERE / "index.html").read_bytes(), "text/html")
            if p == "/api/summary": return self._send(200, api_summary(_conn(), q))
            if p == "/api/jobs": return self._send(200, api_jobs(_conn(), q))
            if p.startswith("/api/jobs/"): return self._send(200, api_job(_conn(), int(p.split("/")[3])) or {})
            if p == "/api/companies": return self._send(200, api_companies(_conn(), q))
            if p == "/api/events": return self._send(200, api_events(_conn(), q))
            if p == "/api/categories": return self._send(200, api_categories(_conn()))
            if p == "/api/contacts": return self._send(200, api_contacts(_conn(), q))
            if p == "/api/contacts.csv":
                import csv, io
                items = api_contacts(_conn(), {**q, "limit": ["3000"]})["items"]; buf = io.StringIO(); w = csv.writer(buf)
                w.writerow(["company", "name", "title", "role", "email", "email_confidence", "linkedin", "source", "contacted", "notes"])
                for c in items: w.writerow([c["company"], c["name"], c["title"], c["role_type"], c["email"], c["email_confidence"], c["linkedin_url"], c["source"], c["contacted"], c["user_notes"] or ""])
                return self._send(200, buf.getvalue().encode(), "text/csv")
            if p == "/api/digest":
                f = DATA / "digest_latest.md"; return self._send(200, {"text": f.read_text() if f.exists() else ""})
            if p == "/api/export.csv":
                import csv, io
                items = api_jobs(_conn(), {**q, "limit": ["5000"]})["items"]; buf = io.StringIO(); w = csv.writer(buf)
                w.writerow(["company", "title", "location", "degree", "url", "posted", "first_seen", "status", "relevance", "user_status", "notes"])
                for j in items: w.writerow([j["company"], j["title"], j["location"], j["degree"], j["url"], j["posted_at"], j["first_seen"], j["status"], j["relevance"], j["user_status"], j["user_notes"] or ""])
                return self._send(200, buf.getvalue().encode(), "text/csv")
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})
    def do_POST(self):
        u = urlparse(self.path); p = u.path.rstrip("/")
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        try:
            with _lock:
                if p.startswith("/api/jobs/") and p.endswith("/recheck"): return self._send(200, recheck_link(_conn(), int(p.split("/")[3])))
                if p.startswith("/api/jobs/"): return self._send(200, api_update_job(_conn(), int(p.split("/")[3]), body))
                if p.startswith("/api/companies/"): return self._send(200, api_update_company(_conn(), int(p.split("/")[3]), body))
                if p == "/api/scrape": return self._send(200, start_scrape())
                if p == "/api/contacts/run": return self._send(200, start_contacts(body.get("company_id")))
                if p.startswith("/api/contacts/"): return self._send(200, api_update_contact(_conn(), int(p.split("/")[3]), body))
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

def serve(host="127.0.0.1", port=8765):
    srv = ThreadingHTTPServer((host, port), H); srv.daemon_threads = True
    print(f"Job Agent dashboard: http://{host}:{port}", flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass

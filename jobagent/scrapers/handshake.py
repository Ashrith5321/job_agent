"""Handshake (app.joinhandshake.com) — requires a logged-in student session.
Set settings.handshake_cookie to the full Cookie header value copied from your browser
(DevTools → Network → any app.joinhandshake.com request → Request Headers → cookie).
We try the classic postings JSON endpoint first, then the HTML listing as a fallback."""
import re, json, html as H
from .base import Scraper, Job
from ..util import to_iso, strip_html
from ..http import HttpError

BASE = "https://app.joinhandshake.com"
JOB_RX = re.compile(r'href="(/(?:stu/)?jobs/(\d+)[^"]*)"[^>]*>(.*?)</a>', re.S)

class AuthRequired(Exception): pass

def _hdr(cookie, accept="application/json"):
    return {"Cookie": cookie, "Accept": accept, "X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/stu/postings"}

def _check_auth(r):
    if "/access" in r.url or "/login" in r.url or r.status_code in (401, 403): raise AuthRequired("handshake cookie invalid/expired")

def search_json(http, cookie, query, page):
    # classic API: job type 3 = Internship; per_page max ~50
    u = f"{BASE}/stu/postings?ajax=true&page={page}&per_page=50&query={query.replace(' ', '%20')}&job.job_types[]=3&sort=posted_date_desc"
    r = http.get(u, headers=_hdr(cookie)); _check_auth(r)
    if r.status_code != 200: raise HttpError(f"HTTP {r.status_code}", status=r.status_code, url=u)
    try: return r.json()
    except ValueError: return None

def search_html(http, cookie, query, page):
    u = f"{BASE}/stu/postings?page={page}&per_page=50&query={query.replace(' ', '%20')}&job.job_types[]=3"
    r = http.get(u, headers=_hdr(cookie, "text/html")); _check_auth(r)
    out = []
    for path, jid, inner in JOB_RX.findall(r.text):
        title = strip_html(inner)[:200]
        if title and jid not in {o["id"] for o in out}: out.append({"id": jid, "title": title, "url": BASE + path.split("?")[0]})
    return out

def detail(http, cookie, jid):
    r = http.get(f"{BASE}/stu/jobs/{jid}", headers=_hdr(cookie, "text/html")); _check_auth(r)
    if r.status_code != 200: return {}
    t = r.text
    g = lambda rx: (re.search(rx, t, re.S) or [None, ""])[1]
    emp = strip_html(g(r'class="[^"]*employer-name[^"]*"[^>]*>(.*?)</') or g(r'"employer_name"\s*:\s*"([^"]+)"'))
    loc = strip_html(g(r'class="[^"]*job-location[^"]*"[^>]*>(.*?)</') or g(r'"location_name"\s*:\s*"([^"]+)"'))
    desc = strip_html(g(r'class="[^"]*job-description[^"]*"[^>]*>(.*?)</section>') or g(r'id="job-description"[^>]*>(.*?)</div>'))
    apply_ = g(r'href="(https?://[^"]+)"[^>]*>\s*Apply Externally') or ""
    return {"employer": emp, "location": loc, "description": desc, "apply_url": H.unescape(apply_)}

class Handshake(Scraper):
    provider = "handshake"
    complete_listing = False

    def fetch(self, http, company, known_ext_ids=None):
        from ..config import settings
        st = settings(); cookie = (st.get("handshake_cookie") or "").strip()
        if not cookie: raise HttpError("handshake: no cookie configured (settings.handshake_cookie)")
        known = known_ext_ids or set(); seen = {}; budget = 60
        for q in st["handshake_queries"]:
            for page in range(1, 4):
                try:
                    j = search_json(http, cookie, q, page)
                except AuthRequired as e:
                    raise HttpError(str(e))
                items = []
                if isinstance(j, dict):
                    items = j.get("results") or j.get("postings") or j.get("jobs") or []
                if not items:
                    try: items = search_html(http, cookie, q, page)
                    except AuthRequired as e: raise HttpError(str(e))
                    if not items: break
                for it in items:
                    if isinstance(it, dict) and "job" in it: it = {**it["job"], **{k: v for k, v in it.items() if k != "job"}}
                    jid = str(it.get("id") or it.get("job_id") or "")
                    if not jid or jid in seen: continue
                    emp = it.get("employer_name") or (it.get("employer") or {}).get("name", "") if isinstance(it.get("employer"), dict) else it.get("employer_name", "")
                    seen[jid] = {"id": jid, "title": it.get("title", ""), "employer": emp or "", "location": it.get("location_name") or (it.get("location") or {}).get("name", "") if isinstance(it.get("location"), dict) else (it.get("location") or ""),
                                 "posted": it.get("created_at") or it.get("posted_date") or "", "description": it.get("description") or "", "url": it.get("url") or f"{BASE}/stu/jobs/{jid}", "apply_url": it.get("apply_url") or ""}
                if len(items) < 50: break
        jobs = []
        for jid, it in seen.items():
            desc = strip_html(it["description"]) if it["description"] else None
            if (jid not in known) and (not it["employer"] or desc is None) and budget > 0:
                budget -= 1
                try:
                    d = detail(http, cookie, jid)
                    it["employer"] = it["employer"] or d.get("employer", ""); it["location"] = it["location"] or d.get("location", "")
                    desc = d.get("description") or desc or ""; it["apply_url"] = it["apply_url"] or d.get("apply_url", "")
                except (AuthRequired, HttpError): desc = desc or ""
            jobs.append(Job(ext_id=jid, title=it["title"], url=it["url"], apply_url=it["apply_url"], location=it["location"], department=it["employer"],
                            employment_type="Internship", posted_at=to_iso(it["posted"]), description=desc, source="handshake"))
        return jobs

    @classmethod
    def probe(cls, http, token, hint=None):
        return True, None, {}

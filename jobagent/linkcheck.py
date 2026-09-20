"""Verify job URLs still resolve. Conservative: only 404/410 (or a redirect to a generic jobs index) count as broken."""
import re, concurrent.futures as cf
from urllib.parse import urlparse
from .http import HttpError
from .util import now_iso

DEAD_TEXT = re.compile(r"(job (?:is )?no longer (?:available|open|accepting)|this job has (?:closed|expired)|position has been filled|"
                       r"posting (?:has )?(?:expired|closed|been removed)|job not found|this job is no longer active|"
                       r"the job you are looking for (?:is no longer|could not be found)|we couldn'?t find (?:that|this) job|"
                       r"job (?:posting )?(?:has been )?(?:removed|filled|closed)|no longer accepting applications)", re.I)

def check_one(http, url):
    """Returns (ok: True/False/None, status:int|None). None = unknown (blocked/timeout), don't change state."""
    if not url or not url.startswith("http"): return False, 0
    host = urlparse(url).netloc
    try:
        r = http.get(url, headers={"Accept": "text/html,*/*"}, timeout=20)
    except HttpError:
        return None, None
    st = r.status_code
    if st in (404, 410): return False, st
    if st in (401, 403, 405, 429, 503) or st >= 500: return None, st
    if not (200 <= st < 300): return None, st
    final = r.url or url
    # redirected from a specific job to a bare listing page => job gone
    if "linkedin.com" in host:
        if "/jobs/view/" not in final and "linkedin.com/jobs/view" in url: return False, st
        body = r.text[:200000]
        if re.search(r'class="[^"]*closed-job[^"]*"', body) or "This job is no longer accepting applications" in body: return False, st
        return True, st
    if "myworkdayjobs.com" in host:
        body = r.text[:200000]
        # Workday returns 200 shell; a dead job shows "The job you're looking for is no longer available"
        if "no longer available" in body.lower(): return False, st
        return True, st
    body = r.text[:150000]
    if DEAD_TEXT.search(body): return False, st
    pu, pf = urlparse(url), urlparse(final)
    generic = pf.path.rstrip("/") in ("", "/jobs", "/careers", "/careers/jobs", "/openings", "/job-openings", "/careers/search", "/jobs/search")
    moved = pf.path.rstrip("/") != pu.path.rstrip("/") or (pu.query and not pf.query)
    if generic and moved:  # a specific job URL was redirected to the bare listing => posting is gone
        return False, st
    return True, st

def run_linkcheck(http, conn, job_rows, workers=8, run_id=None):
    """job_rows: list of dict(id,url,link_ok,link_status). Updates DB; returns (checked, broken, newly_closed)."""
    from .db import log_event
    results = {}
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(check_one, http, j["url"]): j for j in job_rows}
        for f in cf.as_completed(futs):
            j = futs[f]
            try: results[j["id"]] = f.result()
            except Exception: results[j["id"]] = (None, None)
    ts = now_iso(); broken = 0; closed = 0
    for j in job_rows:
        ok, st = results.get(j["id"], (None, None))
        if ok is None:
            conn.execute("UPDATE jobs SET link_checked_at=?, link_status=? WHERE id=?", (ts, st, j["id"]))
            continue
        prev_ok = j.get("link_ok")
        conn.execute("UPDATE jobs SET link_ok=?, link_status=?, link_checked_at=? WHERE id=?", (1 if ok else 0, st, ts, j["id"]))
        if not ok:
            broken += 1
            if prev_ok == 0 and j.get("status") == "open":
                # broken on two consecutive checks -> treat as closed
                conn.execute("UPDATE jobs SET status='closed', closed_at=? WHERE id=?", (ts, j["id"]))
                log_event(conn, "closed", f"link dead (HTTP {st}) on two checks", run_id, j["id"], j.get("company_id")); closed += 1
            elif prev_ok in (1, None):
                log_event(conn, "link_broken", f"HTTP {st}", run_id, j["id"], j.get("company_id"))
        elif prev_ok == 0:
            log_event(conn, "link_fixed", f"HTTP {st}", run_id, j["id"], j.get("company_id"))
    conn.commit()
    return len(job_rows), broken, closed

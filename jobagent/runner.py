"""Orchestrates a scrape run: fetch all active companies in parallel, reconcile with DB, check links, notify, rebalance."""
import json, time, math, traceback, datetime as dt, concurrent.futures as cf
from .config import settings
from .db import connect, rows, one, log_event, upsert_company
from .http import default_http, HttpError
from .scrapers import get as get_scraper
from .scoring import classify, degree_level, profile_adjust
from .geo import us_status
import re as _re
FOREIGN_TITLE = _re.compile(r"praktik|werkstudent|stagiaire|\bstage\b|abschlussarbeit|masterarbeit|bachelorarbeit|\(m/[wf]/[dx]\)|\(f/m/[dx]\)|\(w/m/d\)|\bpraktikant|\bduales? studium|\balternance\b|\bbecario|\bpasant[ií]a|\btirocin|\bestágio\b", _re.I)
from .util import now_iso, norm_text, slugify
from .linkcheck import run_linkcheck

LINKEDIN_SEARCH_SLUG = "linkedin-search"

def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def _company_index(conn):
    """name -> company row (active + inactive) for matching LinkedIn card company names."""
    idx = {}
    for c in rows(conn, "SELECT id, slug, name, active FROM companies"):
        idx[norm_text(c["name"])] = c
        idx[c["slug"]] = c
        base = norm_text(c["name"]).replace(" inc", "").replace(" ai", "").replace(" robotics", "").replace(",", "").strip()
        idx.setdefault(base, c)
    return idx

def _match_company(idx, name):
    n = norm_text(name)
    if not n: return None
    for k in (n, n.replace(",", ""), n.replace(" inc.", "").replace(" inc", "").strip(), n.replace(" ai", "").strip(), n.replace(" robotics", "").strip(), slugify(n)):
        if k in idx: return idx[k]
    return None

def fetch_company(http, company, known):
    sc = get_scraper(company["ats_provider"])
    if sc is None: return {"error": f"no scraper for provider {company['ats_provider']!r}", "jobs": None}
    t0 = time.time()
    try:
        jobs = sc.fetch(http, company, known)
        return {"jobs": jobs, "complete": sc.complete_listing, "secs": time.time() - t0}
    except HttpError as e:
        return {"error": str(e), "jobs": None, "secs": time.time() - t0}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:200]}", "jobs": None, "secs": time.time() - t0, "trace": traceback.format_exc()[-800:]}

def reconcile(conn, company, result, run_id, st, cidx):
    """Apply one company's fetch result to the DB. Returns stats dict."""
    ts = now_iso(); cid = company["id"]
    stats = {"seen": 0, "new": 0, "reopened": 0, "closed": 0, "err": 0, "kept": 0}
    if result.get("jobs") is None:
        conn.execute("UPDATE companies SET last_scraped_at=?, last_status='error', last_error=?, consecutive_failures=consecutive_failures+1 WHERE id=?",
                     (ts, result.get("error", "")[:500], cid))
        stats["err"] = 1; return stats
    jobs = result["jobs"]; stats["seen"] = len(jobs)
    existing = {r["ext_id"]: r for r in rows(conn, "SELECT id, ext_id, status, description, relevance, is_intern, is_newgrad, company_id FROM jobs WHERE company_id=?", (cid,))}
    seen_ext = set()
    li_source = company["ats_provider"] in ("linkedin_search", "handshake")
    for j in jobs:
        ex = existing.get(j.ext_id)
        desc = j.description if j.description is not None else (ex["description"] if ex else "")
        cls = classify(j.title, desc or "", j.department if not li_source else "", j.employment_type, j.extra_flags)
        geo = us_status(j.location)
        if geo != "us" and FOREIGN_TITLE.search(j.title or ""): geo = "non_us"   # German/French/etc. internship titles with no US location
        is_us = 1 if geo == "us" else 0 if geo == "non_us" else None
        # unknown location: trust the company's HQ (job may say "Remote" / nothing); LinkedIn search is already US-filtered
        us_ok = (is_us == 1) or (is_us is None and (company.get("is_us", 1) or li_source))
        degree = degree_level(j.title, desc or "")
        if not cls["excluded"]:
            cls["relevance"] = profile_adjust(cls["relevance"], j.title, desc or "", degree, company.get("category") if not li_source else None)
        keep = (not cls["excluded"]) and (not st["us_only"] or us_ok) and (
            (cls["is_intern"] and cls["relevance"] >= st["store_min_relevance_intern"]) or
            (st["track_newgrad"] and cls["is_newgrad"] and cls["relevance"] >= st["store_min_relevance_intern"]) or
            (st["track_fulltime"] and cls["relevance"] >= st["store_min_relevance_fulltime"]))
        target_cid = cid
        if li_source:
            # attribute LinkedIn search hits to a tracked company when the name matches
            m = _match_company(cidx, j.department)
            if m: target_cid = m["id"]
            elif keep and cls["is_intern"] and cls["relevance"] >= 30:
                _record_discovered(conn, j.department, run_id)
        if target_cid != cid:
            ex = one(conn, "SELECT id, ext_id, status, description, relevance, is_intern, is_newgrad, company_id FROM jobs WHERE company_id=? AND ext_id=?", (target_cid, j.ext_id))
        if not keep:
            if ex and ex["status"] == "open":
                # previously stored but now fails the filters (e.g. moved to non-US, or filters tightened): retire it quietly
                conn.execute("UPDATE jobs SET status='filtered', closed_at=? WHERE id=?", (ts, ex["id"]))
            continue
        seen_ext.add(j.ext_id); stats["kept"] += 1
        kws = json.dumps(cls["keywords"])
        if ex:
            reopened = ex["status"] == "closed"
            conn.execute("""UPDATE jobs SET title=?, location=?, url=?, apply_url=?, department=?, employment_type=?, posted_at=COALESCE(NULLIF(?, ''), posted_at),
                            updated_at=?, description=?, remote=?, is_intern=?, is_newgrad=?, relevance=?, keywords=?, last_seen=?, source=?, is_us=?, degree=?,
                            status='open', closed_at=NULL, reopened_count=reopened_count+? WHERE id=?""",
                         (j.title, j.location, j.url, j.apply_url, j.department, j.employment_type, j.posted_at, j.updated_at, desc, int(j.remote),
                          int(cls["is_intern"]), int(cls["is_newgrad"]), cls["relevance"], kws, ts, j.source, is_us, degree, int(reopened), ex["id"]))
            if reopened:
                stats["reopened"] += 1; log_event(conn, "reopened", j.title, run_id, ex["id"], target_cid)
        else:
            conn.execute("""INSERT INTO jobs(company_id, ext_id, source, title, location, url, apply_url, department, employment_type, posted_at, updated_at,
                            description, remote, is_intern, is_newgrad, relevance, keywords, first_seen, last_seen, is_us, degree, status)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open')""",
                         (target_cid, j.ext_id, j.source, j.title, j.location, j.url, j.apply_url, j.department, j.employment_type, j.posted_at, j.updated_at,
                          desc, int(j.remote), int(cls["is_intern"]), int(cls["is_newgrad"]), cls["relevance"], kws, ts, ts, is_us, degree))
            jid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            stats["new"] += 1; log_event(conn, "new", j.title, run_id, jid, target_cid)
    # close jobs that disappeared (only when the provider gives a complete listing)
    if result.get("complete", True):
        for ext, ex in existing.items():
            if ext not in seen_ext and ex["status"] == "open":
                conn.execute("UPDATE jobs SET status='closed', closed_at=? WHERE id=?", (ts, ex["id"]))
                stats["closed"] += 1; log_event(conn, "closed", "no longer listed", run_id, ex["id"], cid)
    conn.execute("""UPDATE companies SET last_scraped_at=?, last_status='ok', last_error=NULL, consecutive_failures=0, jobs_total=?,
                    jobs_relevant=(SELECT COUNT(*) FROM jobs WHERE company_id=? AND status='open' AND relevance>=40),
                    jobs_intern=(SELECT COUNT(*) FROM jobs WHERE company_id=? AND status='open' AND is_intern=1 AND relevance>=?) WHERE id=?""",
                 (ts, len(jobs), cid, cid, st["store_min_relevance_intern"], cid))
    return stats

def _record_discovered(conn, name, run_id):
    slug = slugify(name)
    if not slug or len(slug) < 2: return
    ex = one(conn, "SELECT id, active FROM companies WHERE slug=?", (slug,))
    if ex:
        conn.execute("UPDATE companies SET discovered_hits=discovered_hits+1 WHERE id=?", (ex["id"],))
    else:
        conn.execute("INSERT INTO companies(slug, name, category, relevance, active, source, discovered_hits, added_at, notes) VALUES(?,?,?,?,0,'discovered',1,?,?)",
                     (slug, name.strip(), "discovered", 40, now_iso(), "auto-discovered from LinkedIn search hits"))
        log_event(conn, "company_discovered", name, run_id)

def close_stale_search_jobs(conn, run_id, days=5):
    """Jobs from search-style providers (LinkedIn/Amazon/Apple/Workday) that haven't been seen in N days."""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).replace(microsecond=0).isoformat()
    stale = rows(conn, "SELECT id, company_id, title FROM jobs WHERE status='open' AND source IN ('linkedin','linkedin_search','amazon','apple','workday','handshake') AND last_seen < ?", (cutoff,))
    ts = now_iso()
    for j in stale:
        conn.execute("UPDATE jobs SET status='closed', closed_at=? WHERE id=?", (ts, j["id"]))
        log_event(conn, "closed", f"not seen in {days} days", run_id, j["id"], j["company_id"])
    return len(stale)

def dedupe_linkedin(conn):
    """Hide LinkedIn copies of jobs we already track from the company's own ATS."""
    li = rows(conn, "SELECT id, company_id, title FROM jobs WHERE status='open' AND source IN ('linkedin','linkedin_search')")
    n = 0
    for j in li:
        dup = one(conn, "SELECT id FROM jobs WHERE company_id=? AND status='open' AND source NOT IN ('linkedin','linkedin_search') AND lower(trim(title))=lower(trim(?)) LIMIT 1", (j["company_id"], j["title"]))
        if dup:
            conn.execute("UPDATE jobs SET status='duplicate' WHERE id=?", (j["id"],)); n += 1
    return n

def dedupe_shared_boards(conn, run_id=None):
    """Two seed entries pointing at the same ATS board (e.g. 'Bosch' and 'Bosch Research') would double every posting; keep the higher-relevance one."""
    n = 0
    for d in rows(conn, "SELECT ats_provider, ats_token, ats_extra, GROUP_CONCAT(id) ids FROM companies WHERE active=1 AND ((ats_token!='' AND ats_token IS NOT NULL) OR ats_provider='workday') GROUP BY ats_provider, ats_token, ats_extra HAVING COUNT(*)>1"):
        ids = [int(x) for x in d["ids"].split(",")]
        keep = one(conn, f"SELECT id, name FROM companies WHERE id IN ({','.join('?'*len(ids))}) ORDER BY relevance DESC, id ASC LIMIT 1", ids)
        for i in ids:
            if i != keep["id"]:
                conn.execute("UPDATE companies SET active=0, notes='duplicate board of '||? WHERE id=?", (keep["name"], i))
                conn.execute("DELETE FROM jobs WHERE company_id=? AND user_status='none' AND starred=0", (i,))
                conn.execute("UPDATE jobs SET status='duplicate' WHERE company_id=?", (i,))
                log_event(conn, "company_deduped", f"#{i} shares a board with {keep['name']}", run_id, company_id=i); n += 1
    conn.commit(); return n

def rebalance(conn, run_id, st):
    """Keep the active set at the configured size; swap in strong discovered/candidate companies for dead weight."""
    if not st.get("auto_swap_companies"): return 0
    for c in rows(conn, "SELECT id, relevance, jobs_relevant, jobs_intern, discovered_hits, active FROM companies"):
        observed = min(100, 30 * (c["jobs_intern"] or 0) + 6 * (c["jobs_relevant"] or 0)) if c["active"] else min(100, 20 * (c["discovered_hits"] or 0))
        conn.execute("UPDATE companies SET dynamic_score=? WHERE id=?", (round(0.55 * (c["relevance"] or 0) + 0.45 * observed, 1), c["id"]))
    limit = st["active_company_limit"]; swaps = 0
    active = rows(conn, "SELECT id, name, dynamic_score, consecutive_failures, jobs_relevant FROM companies WHERE active=1 ORDER BY dynamic_score ASC")
    cands = rows(conn, "SELECT id, name, dynamic_score FROM companies WHERE active=0 AND ats_provider IS NOT NULL AND ats_provider!='' AND (discovered_hits>=3 OR relevance>=60) ORDER BY dynamic_score DESC")
    for cand in cands:
        if swaps >= st["max_swaps_per_run"]: break
        if len(active) < limit:
            conn.execute("UPDATE companies SET active=1 WHERE id=?", (cand["id"],)); active.append(cand); swaps += 1
            log_event(conn, "company_added", cand["name"], run_id, company_id=cand["id"]); continue
        worst = active[0]
        if cand["dynamic_score"] > worst["dynamic_score"] + 5 and (worst["consecutive_failures"] >= 3 or (worst["jobs_relevant"] or 0) == 0):
            conn.execute("UPDATE companies SET active=0, notes=COALESCE(notes,'')||' [swapped out '||?||']' WHERE id=?", (now_iso()[:10], worst["id"]))
            conn.execute("UPDATE companies SET active=1 WHERE id=?", (cand["id"],))
            log_event(conn, "company_swapped", f"{cand['name']} replaced {worst['name']}", run_id, company_id=cand["id"])
            active = active[1:] + [cand]; active.sort(key=lambda x: x["dynamic_score"]); swaps += 1
        else:
            break
    conn.commit(); return swaps

def run(kind="scheduled", limit=None, only=None, linkcheck=True, notify=True, workers=None, discover=True):
    st = settings(); http = default_http(); conn = connect()
    ensure_pseudo_companies(conn)
    conn.execute("INSERT INTO runs(started_at, kind) VALUES(?,?)", (now_iso(), kind)); run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]; conn.commit()
    q = "SELECT * FROM companies WHERE active=1 AND ats_provider IS NOT NULL AND ats_provider!=''"
    args = []
    if only: q += " AND (slug=? OR name LIKE ?)"; args += [only, f"%{only}%"]; discover = False
    q += " ORDER BY relevance DESC"
    companies = rows(conn, q, args)
    if limit: companies = companies[:limit]
    known = {}
    # "known" = jobs whose description we already captured; jobs stored without one get their details fetched on later runs
    for r in rows(conn, "SELECT company_id, ext_id FROM jobs WHERE description IS NOT NULL AND description!=''"): known.setdefault(r["company_id"], set()).add(r["ext_id"])
    cidx = _company_index(conn)
    _log(f"run #{run_id}: scraping {len(companies)} companies with {workers or st['scrape_workers']} workers")
    tot = {"ok": 0, "err": 0, "seen": 0, "new": 0, "closed": 0, "reopened": 0}
    with cf.ThreadPoolExecutor(workers or st["scrape_workers"]) as ex:
        futs = {ex.submit(fetch_company, http, c, known.get(c["id"], set())): c for c in companies}
        for i, f in enumerate(cf.as_completed(futs), 1):
            c = futs[f]
            try: res = f.result()
            except Exception as e: res = {"error": f"{type(e).__name__}: {e}", "jobs": None}
            s = reconcile(conn, c, res, run_id, st, cidx); conn.commit()
            if s["err"]:
                tot["err"] += 1; _log(f"  [{i}/{len(companies)}] ERR {c['name']}: {res.get('error','')[:120]}")
            else:
                tot["ok"] += 1
                for k in ("seen", "new", "closed", "reopened"): tot[k] += s[k]
                if s["new"] or s["closed"] or s["reopened"]:
                    _log(f"  [{i}/{len(companies)}] {c['name']}: {s['seen']} listed, kept {s['kept']}, +{s['new']} new, -{s['closed']} closed, {s['reopened']} reopened ({res.get('secs',0):.0f}s)")
    tot["closed"] += close_stale_search_jobs(conn, run_id)
    nd = dedupe_linkedin(conn); conn.commit()
    nb = dedupe_shared_boards(conn, run_id)
    if nb: _log(f"deduped {nb} companies sharing a job board")
    _log(f"scrape done: ok={tot['ok']} err={tot['err']} seen={tot['seen']} new={tot['new']} closed={tot['closed']} reopened={tot['reopened']} li-dupes={nd}")
    checked = broken = 0
    if linkcheck:
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=st["linkcheck_max_age_days"])).replace(microsecond=0).isoformat()
        todo = rows(conn, """SELECT id, url, link_ok, link_status, status, company_id FROM jobs WHERE status='open' AND (is_intern=1 OR is_newgrad=1 OR relevance>=50 OR user_status!='none' OR starred=1)
                             AND (link_checked_at IS NULL OR link_checked_at < ?) ORDER BY (user_status!='none') DESC, is_intern DESC, relevance DESC LIMIT 800""", (cutoff,))
        _log(f"link check: {len(todo)} urls")
        checked, broken, closed2 = run_linkcheck(http, conn, todo, st["linkcheck_workers"], run_id)
        tot["closed"] += closed2
        _log(f"link check done: {checked} checked, {broken} broken, {closed2} closed as dead")
    swaps = rebalance(conn, run_id, st)
    if swaps: _log(f"rebalance: {swaps} company swaps/additions")
    if discover and st.get("auto_discover", True):
        try:
            from .discover import run_discovery, promote_candidates
            conn.commit()
            run_discovery(max_homepage_fetches=st.get("discover_homepage_fetches", 40), log=_log, include_vc=(kind == "scheduled" or kind == "discover"))
            swaps += promote_candidates(max_probe=st.get("discover_probe_per_run", 25), log=_log, run_id=run_id)
        except Exception as e:
            _log(f"discovery failed: {type(e).__name__}: {e}")
    if st.get("contacts_per_run", 0) and kind in ("scheduled", "dashboard", "manual"):
        try:
            from .contacts import run_contacts, pick_companies
            cs = pick_companies(conn, st["contacts_per_run"])
            if cs:
                _log(f"contacts: {len(cs)} companies")
                cst = run_contacts(conn, http, cs, log=_log, run_id=run_id, delay=st.get("contacts_search_delay", 2.5))
                _log(f"contacts done: +{cst['new']} new across {cst['companies']} companies")
        except Exception as e:
            _log(f"contacts failed: {type(e).__name__}: {e}")
    conn.execute("""UPDATE runs SET finished_at=?, companies_total=?, companies_ok=?, companies_err=?, jobs_seen=?, jobs_new=?, jobs_closed=?, jobs_reopened=?,
                    links_checked=?, links_broken=?, notes=? WHERE id=?""",
                 (now_iso(), len(companies), tot["ok"], tot["err"], tot["seen"], tot["new"], tot["closed"], tot["reopened"], checked, broken,
                  json.dumps({"http": http.stats, "swaps": swaps}), run_id))
    conn.commit()
    if notify:
        from .notify import send_all
        _log("digest: " + send_all(conn, run_id))
    conn.close()
    return run_id, tot

def ensure_pseudo_companies(conn):
    for slug, name, prov, rel in ((LINKEDIN_SEARCH_SLUG, "LinkedIn Search (all companies)", "linkedin_search", 100),
                                  ("handshake-search", "Handshake Search (all companies)", "handshake", 100),
                                  ("amazon", "Amazon (incl. Amazon Robotics, Zoox parent, FAR Lab)", "amazon", 85),
                                  ("apple", "Apple", "apple", 75)):
        if not one(conn, "SELECT id FROM companies WHERE slug=?", (slug,)):
            upsert_company(conn, {"slug": slug, "name": name, "category": "aggregator" if prov == "linkedin_search" else "bigtech",
                                  "relevance": rel, "ats_provider": prov, "ats_token": "", "active": 1, "source": "seed",
                                  "careers_url": {"linkedin_search": "https://www.linkedin.com/jobs/", "handshake": "https://app.joinhandshake.com/job-search/", "amazon": "https://www.amazon.jobs/", "apple": "https://jobs.apple.com/"}[prov]})
    conn.commit()

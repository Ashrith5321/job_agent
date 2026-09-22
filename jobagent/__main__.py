import sys, json, argparse, time
from .config import settings, ensure_dirs, COMPANIES_PATH, DATA
from .db import connect, rows, one, upsert_company, log_event
from .util import now_iso, slugify

def cmd_init(a):
    """Load seed files + registry into the DB (registry wins over seed for ATS info)."""
    from .seed import load_seed_files, load_registry, save_registry
    conn = connect(); seed = load_seed_files(); reg = {c["slug"]: c for c in load_registry()}
    merged = []
    for c in seed:
        r = reg.get(c["slug"], {})
        for k in ("ats_provider", "ats_token", "ats_extra", "careers_url", "linkedin_company_id", "probe_note"):
            if r.get(k) and not c.get(k): c[k] = r[k]
        if r.get("ats_provider") and r.get("probe_note", "").startswith(("careers page", "slug guess", "hint ok", "manual")): 
            c["ats_provider"], c["ats_token"], c["ats_extra"] = r["ats_provider"], r.get("ats_token", ""), r.get("ats_extra", {})
        if c.get("linkedin_company_id") and not c.get("ats_provider"): c["ats_provider"] = "linkedin"
        merged.append(c)
    if not getattr(a, "prune", False):
        for slug, r in reg.items():
            if slug not in {c["slug"] for c in merged}: merged.append(r)
    st = settings(); limit = st["active_company_limit"]
    merged.sort(key=lambda c: -int(c.get("relevance") or 0))
    n = 0
    for i, c in enumerate(merged):
        c = dict(c); c.setdefault("active", 1 if i < limit else 0)
        existing = one(conn, "SELECT active, ats_provider FROM companies WHERE slug=?", (c["slug"],))
        if existing is not None: c["active"] = existing["active"]
        upsert_company(conn, c); n += 1
    conn.commit(); save_registry(merged)
    from .runner import ensure_pseudo_companies; ensure_pseudo_companies(conn)
    if getattr(a, "prune", False):
        keep = {c["slug"] for c in merged} | {"linkedin-search", "handshake-search", "amazon", "apple"}
        gone = [r for r in rows(conn, "SELECT id, slug, name, source FROM companies WHERE source='seed'") if r["slug"] not in keep]
        for r in gone: conn.execute("DELETE FROM companies WHERE id=?", (r["id"],))
        conn.commit(); print(f"pruned {len(gone)} seed companies no longer in the seed files")
    print(f"loaded {n} companies ({sum(1 for c in merged if c.get('ats_provider'))} with an ATS/source), registry -> {COMPANIES_PATH}")

def cmd_probe(a):
    from .prober import probe_many
    from .seed import load_registry, save_registry
    conn = connect()
    q = "SELECT * FROM companies WHERE ats_provider NOT IN ('amazon','apple','linkedin_search')"
    if a.only: q += f" AND (slug='{a.only}' OR name LIKE '%{a.only}%')"
    elif not a.all: q += " AND (ats_provider IS NULL OR ats_provider='' OR ats_provider='linkedin' OR consecutive_failures>=3)"
    cs = rows(conn, q)
    if a.limit: cs = cs[:a.limit]
    print(f"probing {len(cs)} companies with {a.workers} workers …", flush=True); t0 = time.time()
    reg = {c["slug"]: c for c in load_registry()}
    def persist(c, r):
        if r["ats_provider"]:
            conn.execute("UPDATE companies SET ats_provider=?, ats_token=?, ats_extra=?, careers_url=COALESCE(NULLIF(?,''), careers_url), notes=?, consecutive_failures=0 WHERE id=?",
                         (r["ats_provider"], r["ats_token"], json.dumps(r["ats_extra"] or {}), r["careers_url"], r["probe_note"], c["id"]))
        else:
            prov = "linkedin" if c.get("linkedin_company_id") else ""
            conn.execute("UPDATE companies SET ats_provider=?, careers_url=COALESCE(NULLIF(?,''), careers_url), notes=? WHERE id=?", (prov, r["careers_url"], r["probe_note"], c["id"]))
        conn.commit()
        rc = reg.setdefault(c["slug"], {k: c.get(k) for k in ("slug", "name", "domain", "category", "relevance", "hq", "linkedin_company_id")})
        rc.update({"ats_provider": r["ats_provider"] or rc.get("ats_provider", ""), "ats_token": r["ats_token"], "ats_extra": r["ats_extra"] or {}, "careers_url": r["careers_url"] or rc.get("careers_url", ""), "probe_note": r["probe_note"]})
    res = probe_many(cs, workers=a.workers, log=(print if a.verbose else None), on_result=persist)
    save_registry(list(reg.values()))
    found = sum(1 for r in res.values() if r["ats_provider"])
    print(f"done in {time.time()-t0:.0f}s: {found}/{len(cs)} resolved. Registry saved.")
    for r in rows(conn, "SELECT ats_provider, COUNT(*) n FROM companies GROUP BY ats_provider ORDER BY n DESC"): print(f"  {r['ats_provider'] or '(none)':16} {r['n']}")

def cmd_scrape(a):
    from .runner import run
    run(kind=a.kind, limit=a.limit, only=a.only, linkcheck=not a.no_linkcheck, notify=not a.no_notify, workers=a.workers, discover=not a.no_discover)

def cmd_rescore(a):
    """Recompute relevance/degree for every stored job using the current scoring rules + config/profile.json."""
    from .scoring import classify, degree_level, profile_adjust
    conn = connect(); n = 0; changed = 0
    for j in rows(conn, "SELECT j.id, j.title, j.description, j.department, j.employment_type, j.relevance, j.source, c.category FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.status IN ('open','closed')"):
        cls = classify(j["title"], j["description"] or "", j["department"] if j["source"] not in ("linkedin", "linkedin_search", "handshake") else "", j["employment_type"])
        deg = degree_level(j["title"], j["description"] or "")
        rel = 0 if cls["excluded"] else profile_adjust(cls["relevance"], j["title"], j["description"] or "", deg, j["category"] if j["source"] not in ("linkedin_search", "handshake") else None)
        conn.execute("UPDATE jobs SET relevance=?, degree=?, keywords=? WHERE id=?", (rel, deg, json.dumps(cls["keywords"]), j["id"])); n += 1
        if rel != j["relevance"]: changed += 1
    conn.commit(); print(f"rescored {n} jobs ({changed} changed)")
    for r in rows(conn, "SELECT CASE WHEN relevance>=70 THEN '70+' WHEN relevance>=50 THEN '50-69' WHEN relevance>=30 THEN '30-49' ELSE '<30' END b, COUNT(*) n FROM jobs WHERE status='open' GROUP BY b ORDER BY b DESC"): print(f"  {r['b']:6} {r['n']}")

def cmd_contacts(a):
    from .contacts import run_contacts, pick_companies
    from .http import default_http
    conn = connect()
    if a.only: cs = rows(conn, "SELECT * FROM companies WHERE slug=? OR name LIKE ?", (a.only, f"%{a.only}%"))
    else: cs = pick_companies(conn, a.limit, stale_days=0 if a.force else 30)
    print(f"finding recruiters/hiring managers for {len(cs)} companies …")
    print(run_contacts(conn, default_http(), cs, delay=a.delay))

def cmd_discover(a):
    from .discover import run_discovery, promote_candidates
    if not a.promote_only: run_discovery(max_homepage_fetches=a.homepages, include_vc=not a.no_vc)
    if not a.no_promote: print("activated:", promote_candidates(max_probe=a.probe))

def cmd_linkcheck(a):
    from .linkcheck import run_linkcheck
    from .http import default_http
    conn = connect(); st = settings()
    todo = rows(conn, "SELECT id, url, link_ok, link_status, status, company_id FROM jobs WHERE status='open' ORDER BY (user_status!='none') DESC, is_intern DESC, relevance DESC LIMIT ?", (a.limit,))
    print(f"checking {len(todo)} links …"); print(run_linkcheck(default_http(), conn, todo, st["linkcheck_workers"]))

def cmd_serve(a):
    from .web.server import serve
    serve(host=a.host or settings()["dashboard_host"], port=a.port or settings()["dashboard_port"])

def cmd_digest(a):
    p = DATA / "digest_latest.md"; print(p.read_text() if p.exists() else "no digest yet")

def cmd_stats(a):
    conn = connect()
    print("companies:", one(conn, "SELECT COUNT(*) n FROM companies WHERE active=1")["n"], "active /", one(conn, "SELECT COUNT(*) n FROM companies")["n"], "total")
    for r in rows(conn, "SELECT ats_provider, COUNT(*) n, SUM(active) act FROM companies GROUP BY ats_provider ORDER BY n DESC"): print(f"  {r['ats_provider'] or '(none)':16} {r['n']:4} ({r['act'] or 0} active)")
    print("jobs open:", one(conn, "SELECT COUNT(*) n FROM jobs WHERE status='open'")["n"], "| interns:", one(conn, "SELECT COUNT(*) n FROM jobs WHERE status='open' AND is_intern=1")["n"],
          "| applied:", one(conn, "SELECT COUNT(*) n FROM jobs WHERE user_status='applied'")["n"])
    for r in rows(conn, "SELECT * FROM runs ORDER BY id DESC LIMIT 5"): print("  run", r["id"], r["kind"], r["started_at"], "->", r["finished_at"], f"ok={r['companies_ok']} err={r['companies_err']} new={r['jobs_new']} closed={r['jobs_closed']}")

def cmd_add(a):
    from .prober import probe_many
    conn = connect()
    c = {"slug": slugify(a.name), "name": a.name, "domain": a.domain, "category": a.category, "relevance": a.relevance, "active": 1, "source": "manual", "ats_provider": "", "ats_token": ""}
    cid = upsert_company(conn, c); conn.commit()
    r = probe_many([dict(c, id=cid)], workers=1, log=print)[c["slug"]]
    conn.execute("UPDATE companies SET ats_provider=?, ats_token=?, ats_extra=?, careers_url=?, notes=? WHERE id=?",
                 (r["ats_provider"], r["ats_token"], json.dumps(r["ats_extra"] or {}), r["careers_url"], r["probe_note"], cid)); conn.commit()
    print("added:", c["name"], "->", r["ats_provider"] or "no ATS found (link-only)")

def cmd_rebalance(a):
    from .runner import rebalance
    conn = connect(); print("swaps:", rebalance(conn, None, settings()))

def cmd_export(a):
    conn = connect(); from .seed import save_registry
    cs = rows(conn, "SELECT slug,name,domain,category,relevance,hq,careers_url,ats_provider,ats_token,ats_extra,linkedin_company_id,active,notes FROM companies WHERE slug NOT IN ('linkedin-search') ORDER BY relevance DESC")
    for c in cs:
        try: c["ats_extra"] = json.loads(c["ats_extra"] or "{}")
        except Exception: c["ats_extra"] = {}
        c["probe_note"] = c.pop("notes") or ""
    save_registry(cs); print(f"exported {len(cs)} companies -> {COMPANIES_PATH}")

def main(argv=None):
    ensure_dirs()
    p = argparse.ArgumentParser(prog="jobagent", description="Robotics/ML internship tracker")
    sp = p.add_subparsers(dest="cmd", required=True)
    x = sp.add_parser("init", help="load seed + registry into DB"); x.add_argument("--prune", action="store_true", help="delete seed companies not in seed files"); x.set_defaults(f=cmd_init)
    x = sp.add_parser("probe", help="discover ATS boards"); x.add_argument("--all", action="store_true"); x.add_argument("--only"); x.add_argument("--limit", type=int); x.add_argument("--workers", type=int, default=12); x.add_argument("-v", "--verbose", action="store_true"); x.set_defaults(f=cmd_probe)
    x = sp.add_parser("scrape", help="run a scrape"); x.add_argument("--limit", type=int); x.add_argument("--only"); x.add_argument("--workers", type=int); x.add_argument("--no-linkcheck", action="store_true"); x.add_argument("--no-notify", action="store_true"); x.add_argument("--kind", default="manual"); x.add_argument("--no-discover", action="store_true"); x.set_defaults(f=cmd_scrape)
    sp.add_parser("rescore", help="re-apply scoring rules + profile.json to stored jobs").set_defaults(f=cmd_rescore)
    x = sp.add_parser("contacts", help="find recruiter / hiring-manager contacts (public sources)"); x.add_argument("--only"); x.add_argument("--limit", type=int, default=30); x.add_argument("--force", action="store_true"); x.add_argument("--delay", type=float, default=2.0); x.set_defaults(f=cmd_contacts)
    x = sp.add_parser("discover", help="find new companies (YC, VC portfolios) and activate promising ones"); x.add_argument("--homepages", type=int, default=60); x.add_argument("--probe", type=int, default=40); x.add_argument("--no-vc", action="store_true"); x.add_argument("--no-promote", action="store_true"); x.add_argument("--promote-only", action="store_true"); x.set_defaults(f=cmd_discover)
    x = sp.add_parser("linkcheck"); x.add_argument("--limit", type=int, default=500); x.set_defaults(f=cmd_linkcheck)
    x = sp.add_parser("serve", help="run the dashboard"); x.add_argument("--host"); x.add_argument("--port", type=int); x.set_defaults(f=cmd_serve)
    sp.add_parser("digest").set_defaults(f=cmd_digest)
    sp.add_parser("stats").set_defaults(f=cmd_stats)
    x = sp.add_parser("add", help="add a company and probe it"); x.add_argument("name"); x.add_argument("domain"); x.add_argument("--relevance", type=int, default=60); x.add_argument("--category", default="manual"); x.set_defaults(f=cmd_add)
    sp.add_parser("rebalance").set_defaults(f=cmd_rebalance)
    sp.add_parser("export").set_defaults(f=cmd_export)
    a = p.parse_args(argv); a.f(a)

if __name__ == "__main__":
    main()

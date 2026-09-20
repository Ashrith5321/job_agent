"""Company discovery: YC directory (public mirror), VC portfolio pages, and LinkedIn/Handshake hits.
New companies land as inactive candidates; `promote_candidates` probes them for a job board and activates the ones that
(a) have a working board and (b) look robotics/AV/perception-relevant. Bounded per run so scheduled scrapes stay fast."""
import re, json, time, concurrent.futures as cf
from urllib.parse import urlparse
from .config import settings
from .db import connect, rows, one, upsert_company, log_event
from .http import Http, HttpError
from .util import slugify, norm_text, now_iso, strip_html
from .geo import us_status

YC_ALL = "https://yc-oss.github.io/api/companies/all.json"
YC_TAGS = {"robotics", "autonomous vehicles", "drones", "computer vision", "hard tech", "hardware", "aerospace", "self-driving", "autonomous",
           "manufacturing", "automation", "ai", "artificial intelligence", "machine learning", "deep learning", "3d", "lidar", "space", "defense"}
STRONG = re.compile(r"robot|autonom|self[- ]driving|drone|uav|perception|computer vision|lidar|humanoid|manipulat|slam|motion planning|embodied|physical ai|"
                    r"\bav\b|autonomous vehicle|warehouse automation|surgical robot|agricultur\w+ robot|aerial|rover|teleop", re.I)
WEAK = re.compile(r"machine learning|deep learning|simulation|3d|spatial|sensor|vision|mapping|localization|navigation|control|optimization|hardware|manufactur", re.I)

VC_PAGES = [
    ("khosla", "https://www.khoslaventures.com/portfolio/"), ("dcvc", "https://www.dcvc.com/companies/"), ("playground", "https://playground.vc/portfolio/"),
    ("sequoia", "https://www.sequoiacap.com/our-companies/"), ("lux", "https://www.luxcapital.com/companies"), ("eclipse", "https://eclipse.vc/portfolio/"),
    ("founders_fund", "https://foundersfund.com/portfolio/"), ("a16z", "https://a16z.com/portfolio/"), ("nvidia_inception", "https://www.nvidia.com/en-us/startups/"),
    ("bloomberg_beta", "https://github.com/Bloomberg-Beta/Manual"), ("root_ventures", "https://root.vc/portfolio"), ("lemnos", "https://lemnos.vc/portfolio"),
    ("grep_vc", "https://www.grep.vc/portfolio"), ("innovation_endeavors", "https://www.innovationendeavors.com/portfolio"), ("prime_movers", "https://www.primemovers.vc/portfolio"),
    ("bold_capital", "https://boldcapitalpartners.com/portfolio/"), ("ff_venture", "https://ffvc.com/portfolio/"), ("amplify", "https://amplifypartners.com/portfolio"),
    ("pear_vc", "https://pear.vc/portfolio/"), ("pathbreaker", "https://pathbreaker.vc/portfolio"),
]
SKIP_HOSTS = ("twitter.com", "x.com", "linkedin.com", "facebook.com", "instagram.com", "youtube.com", "medium.com", "github.com", "apple.com", "google.com",
              "crunchbase.com", "wikipedia.org", "techcrunch.com", "forbes.com", "bloomberg.com", "substack.com", "notion.site", "vimeo.com", "tiktok.com",
              "wsj.com", "nytimes.com", "reuters.com", "pitchbook.com", "angel.co", "wellfound.com", "producthunt.com", "amazon.com", "microsoft.com", "spotify.com")

def _relevance_from_text(text):
    t = text or ""
    s = len(set(m.lower() for m in STRONG.findall(t))) * 25 + len(set(m.lower() for m in WEAK.findall(t))) * 6
    return min(100, s)

def yc_candidates(http, log=print):
    data = http.get_json(YC_ALL)
    out = []
    for c in data:
        if (c.get("status") or "").lower() not in ("active", "public", "acquired"): continue
        if c.get("status", "").lower() == "acquired": continue
        regions = " ".join(c.get("regions") or []).lower(); locs = (c.get("all_locations") or "").lower()
        if not ("united states" in regions or "america" in regions or us_status(c.get("all_locations") or "") == "us"): continue
        tags = {t.lower() for t in (c.get("tags") or []) + (c.get("industries") or [])}
        blob = " ".join([c.get("one_liner") or "", c.get("long_description") or "", " ".join(tags)])
        strong = bool(STRONG.search(blob)) or bool(tags & {"robotics", "autonomous vehicles", "drones", "computer vision", "self-driving"})
        if not strong: continue
        rel = _relevance_from_text(blob)
        if rel < 25: continue
        dom = urlparse(c.get("website") or "").netloc.replace("www.", "")
        if not dom: continue
        out.append({"slug": slugify(c["name"]), "name": c["name"], "domain": dom, "category": "yc", "relevance": min(90, 40 + rel // 2),
                    "hq": c.get("all_locations") or "", "source": "yc", "notes": f"YC {c.get('batch','')}: {(c.get('one_liner') or '')[:120]}"})
    log(f"  YC: {len(out)} US robotics-relevant companies")
    return out

LINK_RX = re.compile(r'<a[^>]+href="(https?://[^"#?]+)"[^>]*>(.*?)</a>', re.S)
def vc_candidates(http, log=print, max_pages=None):
    seen = {}
    for name, url in VC_PAGES[:max_pages] if max_pages else VC_PAGES:
        try:
            r = http.get(url, headers={"Accept": "text/html"}, timeout=25)
            if r.status_code != 200: continue
        except HttpError: continue
        n = 0
        for href, inner in LINK_RX.findall(r.text):
            host = urlparse(href).netloc.lower().replace("www.", "")
            if not host or any(host.endswith(s) for s in SKIP_HOSTS) or host.endswith(urlparse(url).netloc.replace("www.", "")): continue
            if host.count(".") > 2 or len(host) > 40: continue
            label = strip_html(inner)[:80].strip()
            if host not in seen:
                seen[host] = {"domain": host, "name": label or host.split(".")[0].title(), "vc": name}; n += 1
        log(f"  {name}: {n} portfolio links")
    return list(seen.values())

META_RX = re.compile(r'<meta[^>]+(?:name|property)="(?:description|og:description|og:title|twitter:description)"[^>]+content="([^"]*)"', re.I)
TITLE_RX = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
def score_homepage(http, domain):
    """Returns (relevance, name_guess, hq_guess) from a company homepage's title/meta/visible text."""
    try:
        r = http.get(f"https://{domain}/", headers={"Accept": "text/html"}, timeout=15)
        if r.status_code != 200: return 0, "", ""
    except HttpError:
        return 0, "", ""
    t = r.text[:300000]
    title = strip_html((TITLE_RX.search(t) or [None, ""])[1])[:120]
    metas = " ".join(META_RX.findall(t))
    body = strip_html(re.sub(r"<(script|style|svg|noscript)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I))[:6000]
    rel = _relevance_from_text(title + " " + metas) * 1.0 + _relevance_from_text(body) * 0.5
    name = re.split(r"[|\-–—:·]", title)[0].strip() if title else ""
    return int(min(100, rel)), name, ""

def add_candidates(conn, cands, log=print):
    added = 0
    for c in cands:
        if not c.get("slug"): c["slug"] = slugify(c["name"])
        if not c["slug"] or len(c["slug"]) < 2: continue
        if one(conn, "SELECT id FROM companies WHERE slug=? OR (domain=? AND domain!='')", (c["slug"], c.get("domain", ""))): continue
        c.setdefault("active", 0); c.setdefault("ats_provider", ""); c.setdefault("ats_token", "")
        upsert_company(conn, c); added += 1
    conn.commit(); log(f"  {added} new candidates added"); return added

def run_discovery(max_homepage_fetches=40, log=print, include_vc=True):
    st = settings(); http = Http(timeout=20, default_interval=0.05, retries=1); conn = connect()
    log("discovery: YC directory")
    try: add_candidates(conn, yc_candidates(http, log), log)
    except Exception as e: log(f"  YC failed: {e}")
    if include_vc:
        log("discovery: VC portfolio pages")
        try:
            vc = vc_candidates(http, log)
            known_domains = {r["domain"] for r in rows(conn, "SELECT domain FROM companies WHERE domain IS NOT NULL")}
            _row = one(conn, "SELECT value FROM settings WHERE key='vc_domains_checked'")
            checked_domains = set(json.loads(_row["value"]) if _row else [])
            todo = [v for v in vc if v["domain"] not in known_domains and v["domain"] not in checked_domains][:max_homepage_fetches]
            log(f"  {len(vc)} portfolio domains, {len(todo)} to evaluate this run")
            new = []
            with cf.ThreadPoolExecutor(8) as ex:
                for v, (rel, name, hq) in zip(todo, ex.map(lambda v: score_homepage(http, v["domain"]), todo)):
                    checked_domains.add(v["domain"])
                    if rel >= 40:
                        nm = v["name"] if v["name"] and v["name"].lower() != v["domain"].split(".")[0] else (name or v["name"])
                        new.append({"slug": slugify(nm), "name": nm, "domain": v["domain"], "category": "vc", "relevance": min(85, 35 + rel // 2), "hq": hq,
                                    "source": "vc:" + v["vc"], "notes": f"from {v['vc']} portfolio (homepage score {rel})"})
            add_candidates(conn, new, log)
            conn.execute("INSERT INTO settings(key,value) VALUES('vc_domains_checked',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(sorted(checked_domains)[-5000:]),)); conn.commit()
        except Exception as e: log(f"  VC discovery failed: {e}")
    conn.close()

def promote_candidates(max_probe=25, log=print, run_id=None):
    """Probe inactive candidates that have no board yet; activate those with a working board + relevant intern postings (or high seed relevance).
    Respects active_company_limit by swapping out the weakest active company."""
    from .prober import probe_many
    from .scrapers import get as get_scraper
    from .scoring import classify
    st = settings(); conn = connect(); http = Http(timeout=20, default_interval=0.1, retries=1)
    cands = rows(conn, """SELECT * FROM companies WHERE active=0 AND (ats_provider IS NULL OR ats_provider='') AND (notes IS NULL OR notes NOT LIKE 'no ATS%')
                          AND source!='seed' ORDER BY discovered_hits DESC, relevance DESC LIMIT ?""", (max_probe,))
    if not cands: log("promote: no candidates to probe"); conn.close(); return 0
    log(f"promote: probing {len(cands)} candidates")
    res = probe_many(cands, workers=8, log=None)
    activated = 0
    for c in cands:
        r = res.get(c["slug"]) or {}
        if not r.get("ats_provider"):
            conn.execute("UPDATE companies SET notes=?, careers_url=COALESCE(NULLIF(?,''), careers_url) WHERE id=?", (f"no ATS found {now_iso()[:10]}; " + (c["notes"] or "")[:200], r.get("careers_url", ""), c["id"])); continue
        conn.execute("UPDATE companies SET ats_provider=?, ats_token=?, ats_extra=?, careers_url=COALESCE(NULLIF(?,''), careers_url) WHERE id=?",
                     (r["ats_provider"], r["ats_token"], json.dumps(r["ats_extra"] or {}), r.get("careers_url", ""), c["id"]))
        # quick relevance check on the live board
        try:
            jobs = get_scraper(r["ats_provider"]).fetch(http, {**c, "ats_provider": r["ats_provider"], "ats_token": r["ats_token"], "ats_extra": json.dumps(r["ats_extra"] or {})}, known_ext_ids=set())
        except Exception:
            jobs = []
        rel_intern = sum(1 for j in jobs if (cl := classify(j.title, j.description or "", j.department, j.employment_type, j.extra_flags))["is_intern"] and cl["relevance"] >= 30 and us_status(j.location) != "non_us")
        rel_any = sum(1 for j in jobs if classify(j.title, j.description or "", j.department, j.employment_type, j.extra_flags)["relevance"] >= 50)
        hq_us = us_status(c.get("hq") or "") != "non_us"
        worthy = hq_us and (rel_intern > 0 or (rel_any >= 3 and (c["relevance"] or 0) >= 55) or (c["discovered_hits"] or 0) >= 2)
        if not worthy:
            conn.execute("UPDATE companies SET notes=? WHERE id=?", (f"board found ({len(jobs)} jobs, {rel_intern} rel. interns) – not activated; " + (c["notes"] or "")[:160], c["id"])); continue
        n_active = one(conn, "SELECT COUNT(*) n FROM companies WHERE active=1")["n"]
        if n_active >= st["active_company_limit"]:
            worst = one(conn, "SELECT id, name, dynamic_score FROM companies WHERE active=1 AND source='seed' AND jobs_intern=0 AND last_status IS NOT NULL ORDER BY dynamic_score ASC, relevance ASC LIMIT 1")
            if not worst or (worst["dynamic_score"] or 0) > 60: continue
            conn.execute("UPDATE companies SET active=0, notes=COALESCE(notes,'')||' [swapped out for '||?||']' WHERE id=?", (c["name"], worst["id"]))
            log_event(conn, "company_swapped", f"{c['name']} replaced {worst['name']}", run_id, company_id=c["id"])
        conn.execute("UPDATE companies SET active=1, jobs_intern=?, notes=? WHERE id=?", (rel_intern, f"auto-activated {now_iso()[:10]}: {rel_intern} relevant intern postings; " + (c["notes"] or "")[:160], c["id"]))
        log_event(conn, "company_added", f"{c['name']} ({r['ats_provider']}, {rel_intern} relevant interns)", run_id, company_id=c["id"])
        activated += 1; log(f"  + activated {c['name']} ({r['ats_provider']}:{r['ats_token']}, {rel_intern} relevant interns)")
    conn.commit(); conn.close(); return activated

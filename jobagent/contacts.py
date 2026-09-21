"""Recruiter / hiring-manager discovery from PUBLIC sources only:
  1. emails already sitting in stored job postings (apply/contact addresses)
  2. the company's careers / contact / about pages (mailto: + plain emails)
  3. DuckDuckGo HTML search for indexed LinkedIn profile titles ("Name - Technical Recruiter at Acme | LinkedIn")
  4. company email pattern inferred from found person emails; used to *guess* addresses for named contacts (flagged)
No LinkedIn login, no SMTP verification. Everything found is tagged with source + confidence."""
import re, html as H, time, json, itertools, datetime as dt
from urllib.parse import urlparse, quote_plus
from .db import rows, one, log_event
from .http import HttpError
from .util import now_iso, strip_html, norm_text

EMAIL_RX = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
MAILTO_RX = re.compile(r'mailto:([^"\'?> ]+)', re.I)
DDG_RESULT = re.compile(r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', re.S)
DDG_SNIP = re.compile(r'<a class="result__snippet"[^>]*>(.*?)</a>', re.S)
LI_TITLE = re.compile(r"^(?P<name>[^|–\-]{2,60}?)\s*[–\-]\s*(?P<title>.{2,120}?)\s*(?:[–\-]\s*(?P<co>[^|–\-]{1,60}?)\s*)?\|\s*LinkedIn\s*$", re.I)
LI_TITLE2 = re.compile(r"^(?P<name>[^|–\-]{2,60}?)\s*[–\-]\s*(?P<title>.{2,140}?)\s*\|\s*LinkedIn\s*$", re.I)
AT_CO = re.compile(r"^(?P<title>.+?)\s+(?:at|@)\s+(?P<co>.+)$", re.I)

INBOX_KIND = [("university_recruiter", re.compile(r"universit|campus|intern|earlycareer|early[-_.]?career|students?|newgrad|college", re.I)),
              ("inbox", re.compile(r"recruit|careers?|jobs?|talent|hiring|hr\b|people|apply|employment|staffing|ta@", re.I))]
GENERIC_LOCAL = re.compile(r"^(info|press|media|support|help|sales|legal|privacy|security|abuse|noreply|no-reply|donotreply|webmaster|admin|billing|marketing|partnerships?|investors?|ir|contact|hello|team|office|accessibility|accommodations?|dei|ethics|compliance)$", re.I)
ROLE_KIND = [("university_recruiter", re.compile(r"universit|campus|early[- ]career|intern(ship)? program|student|new grad|emerging talent|college", re.I)),
             ("recruiter", re.compile(r"recruit|talent|sourc|people partner|hr business|technical staffing|talent acquisition|\bta\b", re.I)),
             ("hiring_manager", re.compile(r"hiring manager|engineering manager|\bmanager\b.*(robot|perception|autonom|planning|ml|machine learning|software)|(robot|perception|autonom|planning|ml|machine learning|software).*\bmanager\b|director of|head of|\bvp\b|vice president|lead\b|principal|staff|chief", re.I))]

def _kind_for_email(local):
    for k, rx in INBOX_KIND:
        if rx.search(local): return k
    return None

def _role_for_title(title):
    for k, rx in ROLE_KIND:
        if rx.search(title or ""): return k
    return "engineering_lead"

def _company_domains(company):
    d = (company.get("domain") or "").lower().replace("www.", "").split("/")[0]
    out = {d} if d else set()
    cu = company.get("careers_url") or ""
    if cu:
        h = urlparse(cu).netloc.lower().replace("www.", "")
        parts = h.split(".")
        if len(parts) >= 2 and not any(x in h for x in ("greenhouse", "lever", "ashby", "workday", "smartrecruiters", "bamboohr", "rippling", "workable", "recruitee", "linkedin")):
            out.add(".".join(parts[-2:]))
    return {x for x in out if x and "." in x}

def _is_company_email(dom, domains):
    dom = dom.lower()
    return any(dom == d or dom.endswith("." + d) for d in domains)

# ---------- 1. job postings ----------
def harvest_from_jobs(conn, company, domains):
    found = []
    for j in rows(conn, "SELECT id, url, description FROM jobs WHERE company_id=? AND status='open'", (company["id"],)):
        for local, dom in EMAIL_RX.findall(j["description"] or ""):
            if _is_company_email(dom, domains) and not GENERIC_LOCAL.match(local):
                found.append({"email": f"{local}@{dom}".lower(), "source": "job_posting", "source_url": j["url"]})
    return found

# ---------- 2. company pages ----------
PAGES = ["", "/careers", "/careers/", "/jobs", "/contact", "/contact-us", "/about", "/company", "/join", "/join-us", "/careers/internships", "/internships", "/students", "/university"]
def harvest_from_site(http, company, domains, max_pages=8):
    found, seen_html = [], 0
    urls = []
    if company.get("careers_url"): urls.append(company["careers_url"])
    dom = (company.get("domain") or "").replace("www.", "")
    for p in PAGES: urls += [f"https://{dom}{p}", f"https://www.{dom}{p}"]
    tried = set()
    for u in urls:
        if len(tried) >= max_pages * 2: break
        if u in tried: continue
        tried.add(u)
        try:
            r = http.get(u, headers={"Accept": "text/html"}, timeout=15)
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"): continue
        except HttpError:
            continue
        seen_html += 1
        t = H.unescape(r.text[:600000])
        for m in MAILTO_RX.findall(t):
            mm = EMAIL_RX.search(m)
            if mm: found.append({"email": mm.group(0).lower(), "source": "careers_page" if "career" in u or "job" in u else "contact_page", "source_url": r.url})
        for local, d in EMAIL_RX.findall(t):
            if _is_company_email(d, domains) and not local.lower().endswith((".png", ".jpg", ".svg", ".gif")):
                found.append({"email": f"{local}@{d}".lower(), "source": "careers_page" if "career" in u or "job" in u else "contact_page", "source_url": r.url})
        if seen_html >= max_pages: break
    return found

# ---------- 3. DuckDuckGo → LinkedIn profiles ----------
class SearchBlocked(Exception): pass

_A = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_blocked = {}   # engine -> until epoch

def _decode_wrapped(u):
    import base64
    from urllib.parse import unquote
    u = H.unescape(u)
    b = re.search(r"[?&]u=a1([A-Za-z0-9_\-]+)", u)
    if b:
        x = b.group(1); x += "=" * (-len(x) % 4)
        try: return base64.urlsafe_b64decode(x).decode(errors="ignore")
        except Exception: return u
    ru = re.search(r"/RU=([^/]+)/", u)
    if ru: return unquote(ru.group(1))
    m = re.search(r"uddg=([^&]+)", u)
    if m: return unquote(m.group(1))
    return H.unescape(u)

def _clean_title(t):
    t = H.unescape(strip_html(t)).strip()
    t = re.sub(r"^.*?linkedin\.com\s*›\s*in\s*›\s*\S+\s*", "", t)   # yahoo breadcrumb prefix
    return t.strip()

def _engine_ddg(http, query):
    r = http.post("https://html.duckduckgo.com/html/", data={"q": query}, headers={"Accept": "text/html", "Content-Type": "application/x-www-form-urlencoded"}, timeout=25)
    if r.status_code in (202, 429, 403) or "anomaly" in r.text[:4000].lower(): raise SearchBlocked(f"ddg {r.status_code}")
    if r.status_code != 200: return []
    snips = DDG_SNIP.findall(r.text); out = []
    for i, (u, ti) in enumerate(DDG_RESULT.findall(r.text)):
        out.append({"url": _decode_wrapped(u), "title": _clean_title(ti), "snippet": H.unescape(strip_html(snips[i])).strip() if i < len(snips) else ""})
    return out

def _engine_yahoo(http, query):
    r = http.get("https://search.yahoo.com/search", params={"p": query, "n": 20}, headers={"Accept": "text/html"}, timeout=25)
    if r.status_code in (429, 403, 503): raise SearchBlocked(f"yahoo {r.status_code}")
    if r.status_code != 200: return []
    out, seen = [], set()
    for u, inner in _A.findall(r.text):
        real = _decode_wrapped(u)
        if "linkedin.com/in/" not in real or real in seen: continue
        ti = _clean_title(inner)
        if len(ti) < 8: continue
        seen.add(real); out.append({"url": real, "title": ti, "snippet": ""})
    return out

def _engine_bing(http, query):
    r = http.get("https://www.bing.com/search", params={"q": query, "count": 20}, headers={"Accept": "text/html"}, timeout=25)
    if r.status_code in (429, 403): raise SearchBlocked(f"bing {r.status_code}")
    if r.status_code != 200: return []
    out, seen = [], set()
    for u, inner in _A.findall(r.text):
        real = _decode_wrapped(u)
        if "linkedin.com/in/" not in real or real in seen: continue
        ti = _clean_title(inner)
        if len(ti) < 8: continue
        seen.add(real); out.append({"url": real, "title": ti, "snippet": ""})
    return out

ENGINES = [("ddg", _engine_ddg), ("yahoo", _engine_yahoo), ("bing", _engine_bing)]

def ddg(http, query):
    """Search across engines in order, skipping ones that recently blocked us. Raises SearchBlocked only if all are blocked."""
    now = time.time(); errors = []
    for name, fn in ENGINES:
        if _blocked.get(name, 0) > now: continue
        try:
            res = fn(http, query)
            if not res and name == "yahoo":
                time.sleep(3); res = fn(http, query)
            if res: return res
        except SearchBlocked as e:
            _blocked[name] = now + 3600; errors.append(str(e))
        except HttpError as e:
            errors.append(str(e))
    if errors and all(_blocked.get(n, 0) > now for n, _ in ENGINES): raise SearchBlocked("; ".join(errors))
    return []

def _name_ok(name):
    n = name.strip()
    return 4 <= len(n) <= 60 and " " in n and not re.search(r"\d|linkedin|profile|jobs|careers|hiring|recruit", n, re.I)

def _parse_li(title, company_name):
    t = title.replace("—", "-").replace("–", "-").strip()
    m = LI_TITLE.match(t) or LI_TITLE2.match(t)
    if not m: return None
    name = m.group("name").strip(); role = m.group("title").strip(); co = (m.groupdict().get("co") or "").strip()
    m2 = AT_CO.match(role)
    if m2 and not co: role, co = m2.group("title").strip(), m2.group("co").strip()
    if not _name_ok(name): return None
    # must reference the company
    cn = norm_text(company_name); short = cn.split(" ")[0]
    blob = norm_text(role + " " + co)
    if cn not in blob and (len(short) < 4 or short not in blob): return None
    return {"name": name, "title": role[:140], "co": co}

def search_people(http, company, log=None, max_queries=4):
    name = company["name"].split("(")[0].strip()
    q = [f'site:linkedin.com/in "{name}" recruiter', f'site:linkedin.com/in "{name}" "university recruiter" OR "campus recruiter" OR "early career"',
         f'site:linkedin.com/in "{name}" "technical recruiter" OR "talent acquisition"',
         f'site:linkedin.com/in "{name}" "engineering manager" OR "head of" OR "director" robotics OR perception OR autonomy OR "machine learning"']
    people = {}
    for query in q[:max_queries]:
        try: res = ddg(http, query)
        except SearchBlocked as e:
            if log: log(f"    search blocked: {e}")
            break
        except HttpError: continue
        for r in res:
            u = r["url"]
            if "linkedin.com/in/" not in u: continue
            u = re.sub(r"\?.*$", "", u).replace("http://", "https://")
            u = re.sub(r"https://[a-z]{2,3}\.linkedin\.com", "https://www.linkedin.com", u)
            p = _parse_li(r["title"], name)
            if not p: continue
            if u not in people:
                people[u] = {"name": p["name"], "title": p["title"], "linkedin_url": u, "role_type": _role_for_title(p["title"]), "source": "ddg:linkedin", "source_url": u, "snippet": r["snippet"][:300]}
        time.sleep(0.5)
    return list(people.values())

# ---------- 4. email pattern ----------
PATTERNS = ["first.last", "first", "flast", "firstlast", "first_last", "firstl", "f.last", "last", "lastf", "last.first"]
def _apply(pattern, first, last):
    f, l = re.sub(r"[^a-z]", "", first.lower()), re.sub(r"[^a-z]", "", last.lower())
    if not f or not l: return None
    return {"first.last": f"{f}.{l}", "first": f, "flast": f"{f[0]}{l}", "firstlast": f"{f}{l}", "first_last": f"{f}_{l}", "firstl": f"{f}{l[0]}",
            "f.last": f"{f[0]}.{l}", "last": l, "lastf": f"{l}{f[0]}", "last.first": f"{l}.{f}"}[pattern]

def infer_pattern(person_emails, names_by_email=None):
    """person_emails: list of local parts that look like people (contain letters, not generic). Returns best pattern or None."""
    votes = {}
    for local in person_emails:
        l = local.lower()
        if GENERIC_LOCAL.match(l) or _kind_for_email(l): continue
        if re.match(r"^[a-z]+\.[a-z]+$", l): votes["first.last"] = votes.get("first.last", 0) + 1
        elif re.match(r"^[a-z]+_[a-z]+$", l): votes["first_last"] = votes.get("first_last", 0) + 1
        elif re.match(r"^[a-z]\.[a-z]+$", l): votes["f.last"] = votes.get("f.last", 0) + 1
        elif re.match(r"^[a-z]{3,10}$", l): votes["first"] = votes.get("first", 0) + 1
        elif re.match(r"^[a-z]{5,}$", l): votes["flast"] = votes.get("flast", 0) + 1
    return max(votes, key=votes.get) if votes else None

def guess_email(pattern, name, domain):
    parts = [p for p in re.sub(r"[(),.]|\b(?:PhD|MBA|Jr|Sr|II|III)\b", " ", name).strip().split() if p]
    if len(parts) < 2: return None
    local = _apply(pattern, parts[0], parts[-1])
    return f"{local}@{domain}" if local else None

# ---------- orchestration ----------
def _upsert_contact(conn, cid, c):
    ts = now_iso()
    ex = None
    if c.get("linkedin_url"): ex = one(conn, "SELECT id, email, email_confidence FROM contacts WHERE company_id=? AND linkedin_url=?", (cid, c["linkedin_url"]))
    if not ex and c.get("email"): ex = one(conn, "SELECT id, email, email_confidence FROM contacts WHERE company_id=? AND email=?", (cid, c["email"]))
    if ex:
        # only upgrade email confidence (found > pattern > guess)
        rank = {"found": 3, "pattern": 2, "guess": 1, "none": 0, None: 0}
        sets, args = ["last_seen=?"], [ts]
        if c.get("email") and rank.get(c.get("email_confidence")) > rank.get(ex["email_confidence"]):
            sets += ["email=?", "email_confidence=?"]; args += [c["email"], c["email_confidence"]]
        for k in ("title", "role_type", "source_url", "name"):
            if c.get(k): sets.append(f"{k}=COALESCE(NULLIF({k},''), ?)"); args.append(c[k])
        conn.execute(f"UPDATE contacts SET {', '.join(sets)} WHERE id=?", args + [ex["id"]]); return False
    conn.execute("""INSERT OR IGNORE INTO contacts(company_id,name,title,role_type,email,email_confidence,linkedin_url,source,source_url,found_at,last_seen)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (cid, c.get("name"), c.get("title"), c.get("role_type"), c.get("email"), c.get("email_confidence") or ("found" if c.get("email") else "none"),
                                                     c.get("linkedin_url"), c.get("source"), c.get("source_url"), ts, ts))
    return True

def find_contacts_for(conn, http, company, log=None, do_search=True):
    """Run all harvesters for one company; store results. Returns (n_new, n_total, pattern)."""
    cid = company["id"]; domains = _company_domains(company)
    primary = (company.get("domain") or "").replace("www.", "") or (sorted(domains)[0] if domains else "")
    new = 0
    emails = []
    try: emails += harvest_from_jobs(conn, company, domains)
    except Exception as e: log and log(f"    jobs harvest failed: {e}")
    try: emails += harvest_from_site(http, company, domains)
    except Exception as e: log and log(f"    site harvest failed: {e}")
    person_locals = []
    seen_e = set()
    for e in emails:
        em = e["email"]
        if em in seen_e: continue
        seen_e.add(em)
        local = em.split("@")[0]
        kind = _kind_for_email(local)
        if kind:
            new += _upsert_contact(conn, cid, {"name": "", "title": f"{'University/intern' if kind=='university_recruiter' else 'Recruiting'} inbox", "role_type": kind, "email": em, "email_confidence": "found", "source": e["source"], "source_url": e["source_url"]})
        elif not GENERIC_LOCAL.match(local) and re.search(r"[a-z]", local, re.I):
            person_locals.append(local)
            nm = local.replace(".", " ").replace("_", " ").title() if re.match(r"^[a-z]+[._][a-z]+$", local, re.I) else ""
            new += _upsert_contact(conn, cid, {"name": nm, "title": "Contact found on company page/posting", "role_type": "recruiter" if "recruit" in (e["source_url"] or "") else "engineering_lead", "email": em, "email_confidence": "found", "source": e["source"], "source_url": e["source_url"]})
    pattern = infer_pattern(person_locals) or company.get("email_pattern")
    people = []
    if do_search:
        try: people = search_people(http, company, log)
        except Exception as e: log and log(f"    people search failed: {e}")
    for p in people:
        em = guess_email(pattern or "first.last", p["name"], primary) if primary else None
        p.update({"email": em, "email_confidence": ("pattern" if pattern else "guess") if em else "none"})
        try: new += _upsert_contact(conn, cid, p)
        except Exception: pass
    total = one(conn, "SELECT COUNT(*) n FROM contacts WHERE company_id=? AND hidden=0", (cid,))["n"]
    conn.execute("UPDATE companies SET contacts_checked_at=?, email_pattern=COALESCE(?, email_pattern), contacts_n=? WHERE id=?", (now_iso(), pattern, total, cid))
    conn.commit()
    return new, total, pattern

def run_contacts(conn, http, companies, log=print, run_id=None, delay=2.0):
    stats = {"companies": 0, "new": 0, "blocked": False}
    for c in companies:
        t0 = time.time()
        try:
            n, tot, pat = find_contacts_for(conn, http, c, log)
            stats["companies"] += 1; stats["new"] += n
            log(f"  {c['name']}: +{n} new, {tot} total contacts, pattern={pat or '?'} ({time.time()-t0:.0f}s)")
            if n: log_event(conn, "contacts_found", f"{n} new contacts", run_id, company_id=c["id"]); conn.commit()
        except SearchBlocked:
            log("  search engine rate-limited; stopping contact discovery for this run"); stats["blocked"] = True; break
        except Exception as e:
            import traceback; log(f"  {c['name']}: failed {type(e).__name__}: {e}\n" + traceback.format_exc()[-600:])
        time.sleep(delay)
    return stats

def pick_companies(conn, limit, stale_days=30):
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=stale_days)).replace(microsecond=0).isoformat()
    return rows(conn, """SELECT c.* FROM companies c WHERE c.active=1 AND c.domain IS NOT NULL AND c.domain!='' AND c.ats_provider NOT IN ('linkedin_search','handshake')
                         AND (c.contacts_checked_at IS NULL OR c.contacts_checked_at < ?)
                         ORDER BY (SELECT COUNT(*) FROM jobs j WHERE j.company_id=c.id AND j.status='open' AND j.is_intern=1) DESC,
                                  (SELECT COUNT(*) FROM jobs j WHERE j.company_id=c.id AND j.user_status IN ('applied','interested','interviewing')) DESC,
                                  c.relevance DESC LIMIT ?""", (cutoff, limit))

"""ATS discovery. For each company: fetch its careers page(s), regex for known ATS board links, verify via the
provider's probe(); fall back to slug guessing with an identity check so we don't attach the wrong company's board."""
import re, json, concurrent.futures as cf
from urllib.parse import urljoin, urlparse
from .http import Http, HttpError
from .scrapers import REGISTRY, PROBEABLE
from .util import slugify, norm_text

ATS_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board(?:/js)?\?(?:[^\"'&]*&)?for=)?([A-Za-z0-9_-]+)")),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)")),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)")),
    ("lever", re.compile(r"api\.lever\.co/v0/postings/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.-]+)")),
    ("workday", re.compile(r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")),
    ("smartrecruiters", re.compile(r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_-]+)")),
    ("workable", re.compile(r"apply\.workable\.com/(?:api/v\d/widget/accounts/)?([a-z0-9_-]+)")),
    ("workable", re.compile(r"https?://([a-z0-9-]+)\.workable\.com")),
    ("recruitee", re.compile(r"https?://([a-z0-9-]+)\.recruitee\.com")),
    ("bamboohr", re.compile(r"https?://([a-z0-9-]+)\.bamboohr\.com/(?:careers|jobs)")),
    ("rippling", re.compile(r"ats\.rippling\.com/([A-Za-z0-9_-]+)")),
    ("personio", re.compile(r"https?://([a-z0-9-]+)\.jobs\.personio\.(?:com|de)")),
    ("teamtailor", re.compile(r"https?://([a-z0-9-]+)\.teamtailor\.com")),
]
GENERIC_SLUGS = {"advanced","automation","standard","open","deep","brain","bolt","amazon","aurora","elephant","carnegie","monarch","schneider","apple","google","field","path","wing","plus","bot","robot","robotics","ai","labs","tech","systems","vision","auto","drive","space","motion","energy","dynamics","sunday","figure","clone","kind","persona","humanoid","reflex","weave","prosper","booster","engine","galaxy","apex","impulse","relativity","vast","stoke","planet","true","orbit","starfish","lunar","blue","red","cat","near","earth","merlin","reliable","pyka","matternet","percepto","exyn","vermeer","boeing","draper","sandia","overland","mach","chaos","divergent","firestorm","hermeus","rebellion","picogrid","delian","scale","cruise","ghost","comma","phantom","vay","oxa","momenta","pony","baidu","horizon","luminar","ouster","hesai","innoviz","seyond","cepton","serve","starship","coco","kiwibot","cartken","avride","tensor","nauto","netradyne","outrider","forterra","oshkosh","pronto","safeai","polymath","teleo","built","bear","isee","fernride","cyngn","cognata","foretellix","parallel","mapless","zipline","skydio","anduril","shield","epirus","saronic","neros","auterion","wisk","joby","archer","beta","elroy","flytrex","exotec","autostore","fabric","magazino","zebra","otto","seegrid","vecna","invia","dexory","gather","verity","corvus","pickle","mujin","osaro","nimble","fizyr","photoneo","pickit","rapyuta","fox","symbotic","locus","ocado","attabotics","geek","hai","quicktron","youibot","syrius","twinny","neubility","woowa","robotis","intuitive","medtronic","stryker","globus","procept","cmr","moon","vicarious","asensus","neocis","noah","galen","brainlab","activ","auris","microbot","monogram","capstan","mendaera","zeta","irobot","roborock","ecovacs","dreame","matic","dyson","hello","labrador","enchanted","piaggio","segway","husqvarna","yarbo","richtech","pudu","keenon","relay","cobalt","knightscope","ava","double","samsung","lg","sony","panasonic","ecoflow","perceptin","kognic","deepen","encord","v7","roboflow","voxel51","landing","overview","datature","niantic","magic","varjo","orbbec","realsense","zivid","sick","keyence","cognex","basler","teledyne","hexagon","trimble","faro","navvis","matterport","stereolabs","lumotive","prophesee","arbe","uhnder","echodyne","zendar","spartan","omnivision","hailo","recogni","axelera","blaize","tenstorrent","groq","cerebras","sambanova","etched","lightmatter","sima","ceva","kinara","tangram","rerun","foxglove","formant","viam","inorbit","roboto","duality","rendered","synthesis","picknik","robotec","cyberbotics","agilex","clearpath","husarion","robotnik","pal","roboception","dspace","ipg","rfpro","saildrone","ocean","bedrock","terradepth","gecko","anybotics","energy","levita","voliro","skyfish","freefly","aerodyne","sphere","cobot","standard","electric","scythe","greenzie","zauberzeug","ecorobotix","ronovo","tutor","vayu","daxbot","ottonomy","refraction","tortoise","rivr","cartesian","bimanual","rhoban","menlo"}
GENERIC_TOKENS = {"embed", "job_board", "api", "www", "jobs", "careers", "boards", "static", "cdn", "assets", "app", "apply", "widget", "en", "us", "s"}
CAREER_LINK = re.compile(r'href=["\']([^"\']*(?:career|jobs|join[-_ ]?us|join|work[-_ ]with[-_ ]us|opportunit|openings|positions|hiring)[^"\']*)["\']', re.I)

def _careers_urls(domain):
    d = domain.strip().lower().replace("https://", "").replace("http://", "").strip("/")
    return [f"https://{d}/", f"https://www.{d}/", f"https://{d}/careers", f"https://www.{d}/careers", f"https://{d}/jobs",
            f"https://www.{d}/jobs", f"https://careers.{d}/", f"https://jobs.{d}/", f"https://{d}/join", f"https://www.{d}/join-us",
            f"https://{d}/company/careers", f"https://www.{d}/about/careers", f"https://{d}/en/careers", f"https://www.{d}/careers/"]

def _find_in_html(html, base):
    found = []
    for prov, rx in ATS_PATTERNS:
        for m in rx.finditer(html):
            if prov == "workday":
                tenant, wd, site = m.group(1), m.group(2), m.group(3)
                if site.lower() in GENERIC_TOKENS or site.lower().startswith("wday"): continue
                found.append((prov, f"{tenant}.{wd}.myworkdayjobs.com|{tenant}|{site}"))
            else:
                tok = m.group(1)
                if tok.lower() in GENERIC_TOKENS or len(tok) < 2: continue
                found.append((prov, tok))
    # dedupe preserving order
    out, seen = [], set()
    for f in found:
        if f not in seen: seen.add(f); out.append(f)
    return out

def _identity_ok(http, prov, token, name, domain):
    """Light check that the board belongs to this company (guards slug-guess collisions)."""
    words = [w for w in re.split(r"[^a-z0-9]+", norm_text(name)) if len(w) >= 4 and w not in ("robotics", "labs", "technologies", "systems", "inc", "corp", "group", "company", "autonomous", "automation", "dynamics", "industries", "research", "institute", "surgical", "medical", "space", "motors", "energy", "solutions")]
    dom = (domain or "").lower().split(".")[0]
    needles = set(words + ([dom] if len(dom) >= 4 else []))
    if not needles: return True
    try:
        if prov == "greenhouse":
            j = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
            blob = " ".join((x.get("absolute_url", "") + " " + (x.get("content", "") or "")[:3000] + " " + (x.get("company_name") or "")) for x in j.get("jobs", [])[:6]).lower()
        elif prov == "lever":
            j = http.get_json(f"https://api.lever.co/v0/postings/{token}?mode=json&limit=6")
            blob = " ".join((x.get("hostedUrl", "") + " " + (x.get("descriptionPlain", "") or "")[:3000] + " " + (x.get("additionalPlain") or "")[:1500]) for x in j).lower()
        elif prov == "ashby":
            j = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
            blob = " ".join((x.get("jobUrl", "") + " " + (x.get("descriptionPlain", "") or "")[:3000]) for x in j.get("jobs", [])[:6]).lower()
        elif prov == "smartrecruiters":
            j = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=3")
            blob = " ".join(((x.get("company") or {}).get("name", "") + " " + x.get("name", "")) for x in j.get("content", [])).lower() + " " + token.lower()
        elif prov == "workable":
            j = http.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=false")
            blob = (json.dumps(j)[:6000]).lower()
        elif prov == "workday":
            blob = token.lower()
        elif prov in ("personio", "bamboohr", "rippling", "recruitee", "teamtailor"):
            # fetch a few postings and require the company name in their text/urls
            from .scrapers import get as _get
            jobs = _get(prov).fetch(http, {"ats_token": token, "ats_extra": "{}"}, known_ext_ids=set())[:5]
            # descriptions only: URLs always contain the token, which proves nothing
            blob = " ".join((j.description or "")[:3000] + " " + (j.title or "") for j in jobs).lower()
            if not blob.strip() or not any(n in blob for n in needles): return False
            return True
        else:
            blob = token.lower()
    except Exception:
        return False
    blob += " " + token.lower()
    # a token that is just one common English word is too weak on its own
    generic = token.lower() in GENERIC_SLUGS
    if generic and not any(n in blob.replace(token.lower(), "", 1) for n in needles): return False
    return any(n in blob for n in needles)

def _slug_guesses(name, domain):
    n = norm_text(name)
    base = re.sub(r"\b(inc|corp|corporation|llc|ltd|co|company|the)\b", "", n)
    base = re.sub(r"[^a-z0-9 ]", " ", base).strip()
    words = base.split()
    cands = []
    if words:
        cands += ["".join(words), "-".join(words)]
        stripped = [w for w in words if w not in ("ai", "robotics", "labs", "technologies", "tech", "systems", "inc")]
        if stripped and stripped != words: cands += ["".join(stripped), "-".join(stripped)]
        # single first-word guesses ("general", "blue", "intuitive") collide across companies; only allow when it is the domain base
        cands += ["".join(words) + "ai", "".join(words) + "robotics"]
    if domain:
        d = domain.lower().replace("www.", "").split("/")[0].split(".")[0]
        if d: cands += [d, d.replace("-", "")]
    out, seen = [], set()
    for c in cands:
        c = c.strip("-")
        if c and len(c) >= 3 and c not in seen: seen.add(c); out.append(c)
    return out[:8]

def probe_company(http, company, log=None):
    """Returns dict(ats_provider, ats_token, ats_extra, careers_url, probe_note) or None if nothing found."""
    name, domain = company["name"], company.get("domain") or ""
    hint_prov, hint_tok = company.get("ats_provider"), company.get("ats_token")
    def _log(m):
        if log: log(f"  {name}: {m}")
    # 0) verify an existing hint first
    if hint_prov and (hint_tok or hint_prov in ("amazon", "apple", "linkedin_search", "linkedin")):
        cls = REGISTRY.get(hint_prov)
        if cls:
            if hint_prov in ("amazon", "apple", "linkedin_search"): return None  # nothing to probe
            tok = hint_tok if hint_prov != "linkedin" else (company.get("linkedin_company_id") or hint_tok)
            ok, n, ex = cls.probe(http, tok)
            if ok:
                _log(f"hint verified {hint_prov}:{tok} ({n} jobs)")
                return {"ats_provider": hint_prov, "ats_token": hint_tok if hint_prov != "workday" else "", "ats_extra": ex, "careers_url": company.get("careers_url") or "", "probe_note": f"hint ok ({n})"}
            _log(f"hint FAILED {hint_prov}:{tok}, re-probing")
    # 1) careers pages
    candidates, careers_url, html_seen = [], company.get("careers_url") or "", 0
    urls = ([careers_url] if careers_url else []) + (_careers_urls(domain) if domain else [])
    tried = set(); extra_links = []
    for u in urls + extra_links:
        if u in tried or len(tried) >= 10: continue
        tried.add(u)
        try:
            r = http.get(u, headers={"Accept": "text/html"}, timeout=15)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", "html"): continue
            html_seen += 1
            found = _find_in_html(r.text, u)
            if found:
                candidates += found
                if not careers_url: careers_url = r.url
                break
            # harvest careers-ish links from the root/landing page
            if len(extra_links) < 4:
                for m in CAREER_LINK.finditer(r.text[:400000]):
                    link = urljoin(r.url, m.group(1))
                    if urlparse(link).netloc.endswith(domain.split("/")[0].replace("www.", "")) or any(k in link for k in ("greenhouse", "lever.co", "ashbyhq", "workday", "smartrecruiters", "workable", "bamboohr", "rippling", "recruitee")):
                        if link not in tried and link not in extra_links: extra_links.append(link)
                    if len(extra_links) >= 4: break
                if not careers_url and re.search(r"career|jobs", r.url, re.I): careers_url = r.url
        except HttpError:
            continue
    for prov, tok in candidates:
        cls = REGISTRY.get(prov)
        if not cls: continue
        ok, n, ex = cls.probe(http, tok)
        if ok and (n is None or n > 0):
            _log(f"found on careers page: {prov}:{tok} ({n} jobs)")
            return {"ats_provider": prov, "ats_token": tok if prov != "workday" else "", "ats_extra": ex, "careers_url": careers_url, "probe_note": f"careers page ({n})"}
    # 2) slug guessing with identity check
    # slug guessing only on providers whose content lets us verify identity; the rest must be linked from the careers page
    for tok in _slug_guesses(name, domain):
        for prov in ("greenhouse", "lever", "ashby", "smartrecruiters"):
            cls = REGISTRY[prov]
            try: ok, n, ex = cls.probe(http, tok)
            except Exception: ok = False
            if ok and n and n > 0 and _identity_ok(http, prov, tok, name, domain):
                _log(f"found by slug guess: {prov}:{tok} ({n} jobs)")
                return {"ats_provider": prov, "ats_token": tok, "ats_extra": ex, "careers_url": careers_url, "probe_note": f"slug guess ({n})"}
    _log(f"no ATS found (fetched {html_seen} pages)")
    return {"ats_provider": "", "ats_token": "", "ats_extra": {}, "careers_url": careers_url, "probe_note": "not found"}

def probe_many(companies, workers=12, log=print, on_result=None):
    """on_result(company, result) is called as each company finishes (use it to persist incrementally)."""
    http = Http(timeout=15, default_interval=0.05, host_intervals={"greenhouse.io": 0.1, "lever.co": 0.1, "ashbyhq.com": 0.1}, retries=1)
    flog = (lambda m: log(m, flush=True)) if log is print else log
    out = {}; done = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(probe_company, http, c, flog): c for c in companies}
        for f in cf.as_completed(futs):
            c = futs[f]; done += 1
            try: r = f.result()
            except Exception as e: r = {"ats_provider": "", "ats_token": "", "ats_extra": {}, "careers_url": "", "probe_note": f"error {type(e).__name__}"}
            out[c["slug"]] = r
            if on_result:
                try: on_result(c, r)
                except Exception as e: print(f"  persist failed for {c['name']}: {e}", flush=True)
            if done % 25 == 0: print(f"  … {done}/{len(companies)} probed, {sum(1 for x in out.values() if x['ats_provider'])} resolved", flush=True)
    return out

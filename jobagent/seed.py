"""Seed file format (config/companies_seed.txt), pipe-separated:
name|domain|category|relevance|hq|hints
hints (space separated): gh:token lever:token ashby:token sr:token wd:host;tenant;site workable:token li:linkedin_company_id url:careers_url
"""
import json
from .config import CONFIG, COMPANIES_PATH
from .util import slugify

HINT_MAP = {"gh": "greenhouse", "lever": "lever", "ashby": "ashby", "sr": "smartrecruiters", "wd": "workday", "workable": "workable",
            "recruitee": "recruitee", "bamboo": "bamboohr", "rippling": "rippling", "personio": "personio", "teamtailor": "teamtailor"}

def parse_seed(text):
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"): continue
        parts = [p.strip() for p in ln.split("|")]
        while len(parts) < 6: parts.append("")
        name, domain, cat, rel, hq, hints = parts[:6]
        c = {"slug": slugify(name), "name": name, "domain": domain, "category": cat, "relevance": int(rel or 50), "hq": hq,
             "ats_provider": "", "ats_token": "", "ats_extra": {}, "linkedin_company_id": "", "careers_url": ""}
        for h in hints.split():
            if ":" not in h: continue
            k, v = h.split(":", 1)
            if k == "li": c["linkedin_company_id"] = v
            elif k == "url": c["careers_url"] = v
            elif k == "wd":
                host, tenant, site = v.split(";"); c["ats_provider"] = "workday"; c["ats_extra"] = {"host": host, "tenant": tenant, "site": site}
            elif k in HINT_MAP: c["ats_provider"] = HINT_MAP[k]; c["ats_token"] = v
        if c["linkedin_company_id"] and not c["ats_provider"]: c["ats_provider"] = "linkedin"
        out.append(c)
    return out

def load_seed_files():
    cs = []
    for p in sorted(CONFIG.glob("companies_seed*.txt")):
        cs += parse_seed(p.read_text())
    # dedupe by slug, keep first
    seen, out = set(), []
    for c in cs:
        if c["slug"] in seen: continue
        seen.add(c["slug"]); out.append(c)
    return out

def load_registry():
    if COMPANIES_PATH.exists():
        try: return json.loads(COMPANIES_PATH.read_text())
        except Exception: pass
    return []

def save_registry(companies):
    COMPANIES_PATH.write_text(json.dumps(companies, indent=1, ensure_ascii=False))

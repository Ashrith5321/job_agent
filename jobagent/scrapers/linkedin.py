"""LinkedIn public guest job search (no login). Used two ways:
 - provider 'linkedin': per-company via linkedin_company_id (f_C) for companies whose sites block scrapers
 - provider 'linkedin_search': global keyword aggregator, attributed to the pseudo-company 'LinkedIn Search'
Be gentle: ~2.5s between requests, stop on 429."""
import re, html as H
from .base import Scraper, Job, extra
from ..util import to_iso, strip_html
from ..http import HttpError

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{params}&start={start}"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"

CARD = re.compile(r'<li>(.*?)</li>', re.S)
RX = {
    "id": re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"'),
    "title": re.compile(r'<h3 class="base-search-card__title">\s*(.+?)\s*</h3>', re.S),
    "company": re.compile(r'<h4 class="base-search-card__subtitle">\s*<a[^>]*>\s*(.+?)\s*</a>', re.S),
    "company2": re.compile(r'<h4 class="base-search-card__subtitle">\s*(.+?)\s*</h4>', re.S),
    "loc": re.compile(r'<span class="job-search-card__location">\s*(.+?)\s*</span>', re.S),
    "date": re.compile(r'<time[^>]*datetime="([^"]+)"'),
    "link": re.compile(r'href="(https://www\.linkedin\.com/jobs/view/[^"?]+)'),
}

class RateLimited(Exception): pass

def parse_cards(html):
    out = []
    for c in CARD.findall(html):
        m = RX["id"].search(c)
        if not m: continue
        t = RX["title"].search(c); co = RX["company"].search(c) or RX["company2"].search(c)
        lo = RX["loc"].search(c); d = RX["date"].search(c); ln = RX["link"].search(c)
        out.append({"id": m.group(1), "title": H.unescape(t.group(1)).strip() if t else "",
                    "company": H.unescape(strip_html(co.group(1))).strip() if co else "",
                    "location": H.unescape(lo.group(1)).strip() if lo else "", "date": d.group(1) if d else "",
                    "url": ln.group(1) if ln else f"https://www.linkedin.com/jobs/view/{m.group(1)}"})
    return out

def search(http, params, pages):
    cards = []
    for p in range(pages):
        r = http.get(SEARCH.format(params=params, start=p * 10), headers={"Accept": "text/html"})
        if r.status_code == 429: raise RateLimited("linkedin 429")
        if r.status_code != 200: break
        got = parse_cards(r.text)
        cards.extend(got)
        if len(got) < 10: break
    return cards

def fetch_description(http, job_id):
    r = http.get(DETAIL.format(id=job_id), headers={"Accept": "text/html"})
    if r.status_code == 429: raise RateLimited("linkedin 429")
    if r.status_code != 200: return ""
    m = re.search(r'<div class="show-more-less-html__markup[^"]*">(.*?)</div>\s*</div>', r.text, re.S)
    return strip_html(m.group(1)) if m else ""

def _to_jobs(cards, http, known, budget_ref, source):
    jobs = []
    for c in cards:
        desc = None
        if c["id"] not in known and budget_ref[0] > 0:
            budget_ref[0] -= 1
            try: desc = fetch_description(http, c["id"])
            except RateLimited: budget_ref[0] = 0; desc = ""
            except HttpError: desc = ""
        jobs.append(Job(ext_id=c["id"], title=c["title"], url=c["url"], location=c["location"],
                        department=c["company"], posted_at=to_iso(c["date"]), updated_at=to_iso(c["date"]),
                        description=desc, source=source))
    return jobs

class LinkedInCompany(Scraper):
    provider = "linkedin"
    complete_listing = False

    def fetch(self, http, company, known_ext_ids=None):
        from ..config import settings
        st = settings()
        if not st["linkedin_enabled"]: return []
        cid = company.get("linkedin_company_id") or extra(company).get("company_id")
        if not cid: raise HttpError("linkedin: missing linkedin_company_id")
        known = known_ext_ids or set(); budget = [st["linkedin_details_per_run"] // 3]
        seen = {}
        try:
            for kw in ("intern", "robotics", "perception", "machine learning", "autonomy"):
                for c in search(http, f"keywords={kw.replace(' ', '%20')}&f_C={cid}&sortBy=DD", st["linkedin_company_pages"]):
                    seen.setdefault(c["id"], c)
        except RateLimited:
            if not seen: raise HttpError("linkedin rate-limited")
        return _to_jobs(list(seen.values()), http, known, budget, "linkedin")

    @classmethod
    def probe(cls, http, token, hint=None):
        try:
            cards = search(http, f"keywords=intern&f_C={token}&sortBy=DD", 1)
            return bool(cards), len(cards), {}
        except Exception:
            return False, None, {}

class LinkedInSearch(Scraper):
    provider = "linkedin_search"
    complete_listing = False

    def fetch(self, http, company, known_ext_ids=None):
        from ..config import settings
        st = settings()
        if not st["linkedin_enabled"]: return []
        known = known_ext_ids or set(); budget = [st["linkedin_details_per_run"]]
        seen = {}
        try:
            for loc in st["linkedin_search_locations"]:
                for q in st["linkedin_search_queries"]:
                    params = f"keywords={q.replace(' ', '%20')}&location={loc.replace(' ', '%20')}&f_E=1%2C2&f_TPR=r2592000&sortBy=DD"
                    for c in search(http, params, st["linkedin_pages_per_query"]):
                        seen.setdefault(c["id"], c)
        except RateLimited:
            if not seen: raise HttpError("linkedin rate-limited")
        return _to_jobs(list(seen.values()), http, known, budget, "linkedin_search")

    @classmethod
    def probe(cls, http, token, hint=None):
        return True, None, {}

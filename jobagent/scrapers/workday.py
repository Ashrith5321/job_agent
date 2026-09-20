"""Workday CXS API. ats_extra = {"host": "nvidia.wd5.myworkdayjobs.com", "tenant": "nvidia", "site": "NVIDIAExternalCareerSite"}"""
from .base import Scraper, Job, extra, PartialResult
from ..util import to_iso, strip_html
from ..scoring import quick_title_interest
from ..http import HttpError

class Workday(Scraper):
    provider = "workday"
    complete_listing = False  # we search by keywords, not full listing

    @staticmethod
    def _parts(company):
        e = extra(company)
        host, tenant, site = e.get("host"), e.get("tenant"), e.get("site")
        if not (host and tenant and site):
            tok = company.get("ats_token") or ""
            # allow "host|tenant|site" packed token
            if tok.count("|") == 2: host, tenant, site = tok.split("|")
        return host, tenant, site

    def fetch(self, http, company, known_ext_ids=None):
        from ..config import settings
        host, tenant, site = self._parts(company)
        if not (host and tenant and site): raise HttpError("workday: missing host/tenant/site")
        known = known_ext_ids or set()
        base = f"https://{host}/wday/cxs/{tenant}/{site}"
        hdr = {"Accept": "application/json", "Accept-Language": "en-US", "Referer": f"https://{host}/{site}"}
        try: http.get(f"https://{host}/{site}", headers={"Accept": "text/html"})  # prime cookies
        except HttpError: pass
        seen, jobs, budget, errors = {}, [], 150, 0
        for q in settings()["workday_queries"]:
            off = 0
            while off < 200:
                try:
                    data = http.post_json(f"{base}/jobs", {"appliedFacets": {}, "limit": 20, "offset": off, "searchText": q}, headers=hdr)
                except HttpError:
                    errors += 1; break
                posts = data.get("jobPostings", [])
                for p in posts:
                    path = p.get("externalPath") or ""
                    if not path or path in seen: continue
                    seen[path] = p
                off += 20
                if not posts or off >= int(data.get("total") or 0): break
        for path, p in seen.items():
            ext = path.rsplit("/", 1)[-1]
            title = p.get("title", "")
            desc = None; loc = p.get("locationsText", ""); etype = ""; posted = ""
            if ext not in known and budget > 0 and quick_title_interest(title):
                budget -= 1
                try:
                    d = http.get_json(f"{base}{path}", headers=hdr)
                    info = d.get("jobPostingInfo") or {}
                    desc = strip_html(info.get("jobDescription", ""))
                    loc = "; ".join(x for x in [info.get("location")] + (info.get("additionalLocations") or [])[:3] if x) or loc
                    etype = info.get("timeType") or ""
                    posted = to_iso(info.get("startDate"))
                except HttpError:
                    desc = ""
            jobs.append(Job(ext_id=ext, title=title, url=f"https://{host}/{site}{path}", location=loc,
                            employment_type=etype, posted_at=posted, updated_at="", description=desc,
                            department="", source="workday"))
        if errors and not jobs: raise HttpError("workday: all searches failed")
        return jobs

    @classmethod
    def probe(cls, http, token, hint=None):
        """token = 'host|tenant|site'."""
        try:
            host, tenant, site = token.split("|")
            r = http.post(f"https://{host}/wday/cxs/{tenant}/{site}/jobs", json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                          headers={"Accept": "application/json", "Content-Type": "application/json"})
            if r.status_code != 200: return False, None, {}
            j = r.json()
            return True, int(j.get("total") or 0), {"host": host, "tenant": tenant, "site": site}
        except Exception:
            return False, None, {}

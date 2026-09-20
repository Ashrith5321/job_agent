from .base import Scraper, Job
from ..util import to_iso, strip_html

API = "https://api.ashbyhq.com/posting-api/job-board/{t}?includeCompensation=true"

class Ashby(Scraper):
    provider = "ashby"

    def fetch(self, http, company, known_ext_ids=None):
        data = http.get_json(API.format(t=company["ats_token"]))
        jobs = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False: continue
            loc = j.get("location") or ""
            sec = [s.get("location") for s in (j.get("secondaryLocations") or []) if s.get("location")]
            if sec: loc = "; ".join([loc] + sec[:3])
            jobs.append(Job(ext_id=j["id"], title=j.get("title", ""), url=j.get("jobUrl", ""), apply_url=j.get("applyUrl", ""),
                            location=loc, department=", ".join(x for x in (j.get("team"), j.get("department")) if x and x != j.get("team")) or (j.get("department") or ""),
                            employment_type=j.get("employmentType") or "", posted_at=to_iso(j.get("publishedAt")),
                            updated_at=to_iso(j.get("publishedAt")), description=strip_html(j.get("descriptionPlain") or j.get("descriptionHtml") or ""),
                            remote=bool(j.get("isRemote")), source="ashby"))
        return jobs

    @classmethod
    def probe(cls, http, token, hint=None):
        try:
            r = http.get(API.format(t=token))
            if r.status_code != 200: return False, None, {}
            j = r.json(); return True, len(j.get("jobs", [])), {}
        except Exception:
            return False, None, {}

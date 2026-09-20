from .base import Scraper, Job, ScrapeError
from ..util import strip_html, to_iso
from ..scoring import quick_title_interest
from ..http import HttpError

API = "https://boards-api.greenhouse.io/v1/boards/{t}/jobs"

class Greenhouse(Scraper):
    provider = "greenhouse"

    def fetch(self, http, company, known_ext_ids=None):
        from ..config import settings
        cap = settings()["greenhouse_detail_cap"]
        t = company["ats_token"]
        known = known_ext_ids or set()
        data = http.get_json(API.format(t=t))
        jobs = []
        detail_budget = cap
        for j in data.get("jobs", []):
            ext = str(j["id"])
            title = j.get("title", "")
            loc = (j.get("location") or {}).get("name", "").strip()
            depts = ", ".join(d.get("name", "") for d in (j.get("departments") or []) if d.get("name"))
            flags = []
            for m in j.get("metadata") or []:
                n = (m.get("name") or "").lower(); v = m.get("value")
                if v and any(k in n for k in ("level", "type", "employment", "category", "job type", "worker")):
                    vs = ", ".join(v) if isinstance(v, list) else str(v)
                    flags.append(vs)
            desc = None
            if quick_title_interest(title, depts, " ".join(flags)) and ext not in known and detail_budget > 0:
                detail_budget -= 1
                try:
                    d = http.get_json(API.format(t=t) + f"/{j['id']}")
                    desc = strip_html(d.get("content", ""))
                except HttpError:
                    desc = ""
            jobs.append(Job(ext_id=ext, title=title, url=j.get("absolute_url", ""), location=loc,
                            department=depts, employment_type="", posted_at=to_iso(j.get("first_published")),
                            updated_at=to_iso(j.get("updated_at")), description=desc, extra_flags=flags,
                            source="greenhouse"))
        return jobs

    @classmethod
    def probe(cls, http, token, hint=None):
        try:
            r = http.get(API.format(t=token))
            if r.status_code != 200: return False, None, {}
            j = r.json(); return True, len(j.get("jobs", [])), {}
        except Exception:
            return False, None, {}

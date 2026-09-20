from .base import Scraper, Job
from ..util import to_iso, strip_html
from ..http import HttpError

API = "https://www.amazon.jobs/en/search.json?base_query={q}&result_limit=100&offset={off}&sort=recent"

class Amazon(Scraper):
    provider = "amazon"
    complete_listing = False

    def fetch(self, http, company, known_ext_ids=None):
        from ..config import settings
        seen, jobs, errs = {}, [], 0
        for q in settings()["amazon_queries"]:
            for off in range(0, 500, 100):
                try:
                    data = http.get_json(API.format(q=q.replace(" ", "+"), off=off))
                except HttpError:
                    errs += 1; break
                js = data.get("jobs", [])
                for j in js:
                    ext = str(j.get("id_icims") or j.get("id"))
                    if ext in seen: continue
                    seen[ext] = j
                if len(js) < 100: break
        for ext, j in seen.items():
            desc = strip_html("\n".join(x for x in (j.get("description"), j.get("basic_qualifications"), j.get("preferred_qualifications")) if x))
            jobs.append(Job(ext_id=ext, title=j.get("title", ""), url="https://www.amazon.jobs" + (j.get("job_path") or ""),
                            location=j.get("location") or j.get("normalized_location") or "", department=j.get("job_category") or j.get("business_category") or "",
                            employment_type=j.get("job_schedule_type") or "", posted_at=to_iso(j.get("posted_date")),
                            updated_at=to_iso(j.get("updated_time") or j.get("posted_date")), description=desc, source="amazon"))
        if errs and not jobs: raise HttpError("amazon: all queries failed")
        return jobs

    @classmethod
    def probe(cls, http, token, hint=None):
        return True, None, {}

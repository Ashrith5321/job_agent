from .base import Scraper, Job, extra
from ..util import to_iso, strip_html
from ..scoring import quick_title_interest
from ..http import HttpError

API = "https://api.smartrecruiters.com/v1/companies/{t}/postings?limit=100&offset={off}"

class SmartRecruiters(Scraper):
    provider = "smartrecruiters"

    def fetch(self, http, company, known_ext_ids=None):
        t = company["ats_token"]; known = known_ext_ids or set()
        jobs, off, budget = [], 0, 200
        while True:
            data = http.get_json(API.format(t=t, off=off))
            content = data.get("content", [])
            for j in content:
                ext = str(j["id"])
                loc = j.get("location") or {}
                locs = ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
                dept = (j.get("department") or {}).get("label", "") or (j.get("function") or {}).get("label", "")
                etype = (j.get("typeOfEmployment") or {}).get("label", "")
                lvl = (j.get("experienceLevel") or {}).get("label", "")
                title = j.get("name", "")
                desc = None
                if ext not in known and budget > 0 and quick_title_interest(title, dept, f"{etype} {lvl}"):
                    budget -= 1
                    try:
                        d = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{t}/postings/{ext}")
                        secs = (d.get("jobAd") or {}).get("sections") or {}
                        desc = strip_html("\n".join((secs.get(k) or {}).get("text", "") for k in ("jobDescription", "qualifications", "additionalInformation")))
                    except HttpError:
                        desc = ""
                jobs.append(Job(ext_id=ext, title=title, url=f"https://jobs.smartrecruiters.com/{t}/{ext}",
                                location=locs, department=dept, employment_type=f"{etype} {lvl}".strip(),
                                posted_at=to_iso(j.get("releasedDate")), updated_at=to_iso(j.get("releasedDate")),
                                description=desc, remote=bool(loc.get("remote")), source="smartrecruiters"))
            off += 100
            if off >= int(data.get("totalFound") or 0) or not content: break
        return jobs

    @classmethod
    def probe(cls, http, token, hint=None):
        try:
            r = http.get(API.format(t=token, off=0))
            if r.status_code != 200: return False, None, {}
            j = r.json(); n = int(j.get("totalFound") or 0)
            return (n > 0), n, {}
        except Exception:
            return False, None, {}

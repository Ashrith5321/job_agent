from .base import Scraper, Job
from ..util import to_iso

API = "https://api.lever.co/v0/postings/{t}?mode=json&limit=500&skip={skip}"

class Lever(Scraper):
    provider = "lever"

    def fetch(self, http, company, known_ext_ids=None):
        t = company["ats_token"]
        jobs, skip = [], 0
        while True:
            data = http.get_json(API.format(t=t, skip=skip))
            if not isinstance(data, list): break
            for j in data:
                cat = j.get("categories") or {}
                loc = cat.get("location") or ""
                alls = cat.get("allLocations") or []
                if alls and len(alls) > 1: loc = "; ".join(alls[:4])
                desc = (j.get("descriptionPlain") or "")
                for l in j.get("lists") or []:
                    desc += "\n" + (l.get("text") or "") + "\n" + (l.get("content") or "")
                from ..util import strip_html
                jobs.append(Job(ext_id=j["id"], title=j.get("text", ""), url=j.get("hostedUrl", ""),
                                apply_url=j.get("applyUrl", ""), location=loc,
                                department=", ".join(x for x in (cat.get("team"), cat.get("department")) if x),
                                employment_type=cat.get("commitment") or "",
                                posted_at=to_iso(j.get("createdAt")), updated_at=to_iso(j.get("updatedAt") or j.get("createdAt")),
                                description=strip_html(desc), remote=(j.get("workplaceType") == "remote"),
                                source="lever"))
            if len(data) < 500: break
            skip += 500
        return jobs

    @classmethod
    def probe(cls, http, token, hint=None):
        try:
            r = http.get(f"https://api.lever.co/v0/postings/{token}?mode=json&limit=1")
            if r.status_code != 200: return False, None, {}
            j = r.json()
            if not isinstance(j, list): return False, None, {}
            r2 = http.get(f"https://api.lever.co/v0/postings/{token}?mode=json&limit=500")
            return True, len(r2.json()) if r2.status_code == 200 else len(j), {}
        except Exception:
            return False, None, {}

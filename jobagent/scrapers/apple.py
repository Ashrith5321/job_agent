import re, json
from .base import Scraper, Job
from ..util import to_iso, strip_html
from ..http import HttpError

URL = "https://jobs.apple.com/en-us/search?search={q}&sort=newest&page={p}"
HYD = re.compile(r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("(.+?)"\);', re.S)

def _hydration(html):
    m = HYD.search(html)
    if not m: return None
    raw = json.loads('"' + m.group(1) + '"')  # unescape JS string literal
    return json.loads(raw)

class Apple(Scraper):
    provider = "apple"
    complete_listing = False

    def fetch(self, http, company, known_ext_ids=None):
        from ..config import settings
        seen, jobs, errs = {}, [], 0
        for q in settings()["apple_queries"]:
            for p in range(1, 6):
                try:
                    r = http.get(URL.format(q=q.replace(" ", "%20"), p=p), headers={"Accept": "text/html"})
                    if r.status_code != 200: errs += 1; break
                    data = _hydration(r.text)
                    if not data: errs += 1; break
                    search = (data.get("loaderData") or {}).get("search") or {}
                    res = search.get("searchResults") or []
                except Exception:
                    errs += 1; break
                for j in res:
                    ext = str(j.get("positionId") or j.get("id"))
                    if ext not in seen: seen[ext] = j
                if len(res) < 20: break
        for ext, j in seen.items():
            locs = "; ".join((l.get("name") or "") for l in (j.get("locations") or [])[:3])
            slug = j.get("transformedPostingTitle") or ""
            jobs.append(Job(ext_id=ext, title=j.get("postingTitle", ""), url=f"https://jobs.apple.com/en-us/details/{ext}/{slug}".rstrip("/"),
                            location=locs, department=(j.get("team") or {}).get("teamName", ""), employment_type=j.get("type") or "",
                            posted_at=to_iso(j.get("postDateInGMT") or j.get("postingDate")), updated_at="",
                            description=strip_html(j.get("jobSummary") or ""), remote=bool(j.get("homeOffice")), source="apple"))
        if errs and not jobs: raise HttpError("apple: all queries failed")
        return jobs

    @classmethod
    def probe(cls, http, token, hint=None):
        return True, None, {}

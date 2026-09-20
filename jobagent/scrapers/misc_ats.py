"""Smaller ATS providers: Workable, Recruitee, BambooHR, Rippling, Personio, Jobvite(JSON), Teamtailor(public JSON)."""
import re, json
from .base import Scraper, Job
from ..util import to_iso, strip_html
from ..http import HttpError

class Workable(Scraper):
    provider = "workable"
    def fetch(self, http, company, known_ext_ids=None):
        t = company["ats_token"]
        data = http.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{t}?details=true")
        jobs = []
        for j in data.get("jobs", []):
            loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
            jobs.append(Job(ext_id=str(j.get("shortcode") or j.get("id")), title=j.get("title", ""), url=j.get("url") or j.get("shortlink", ""),
                            apply_url=j.get("application_url", ""), location=loc, department=j.get("department", ""),
                            employment_type=j.get("employment_type", ""), posted_at=to_iso(j.get("published_on") or j.get("created_at")),
                            updated_at="", description=strip_html((j.get("description") or "") + "\n" + (j.get("requirements") or "")),
                            remote=bool(j.get("telecommuting")), source="workable"))
        return jobs
    @classmethod
    def probe(cls, http, token, hint=None):
        try:
            r = http.get(f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=false")
            if r.status_code != 200: return False, None, {}
            j = r.json(); return "jobs" in j, len(j.get("jobs", [])), {}
        except Exception: return False, None, {}

class Recruitee(Scraper):
    provider = "recruitee"
    def fetch(self, http, company, known_ext_ids=None):
        t = company["ats_token"]
        data = http.get_json(f"https://{t}.recruitee.com/api/offers/")
        jobs = []
        for j in data.get("offers", []):
            jobs.append(Job(ext_id=str(j.get("id")), title=j.get("title", ""), url=j.get("careers_url", ""), apply_url=j.get("careers_apply_url", ""),
                            location=j.get("location") or ", ".join(x for x in (j.get("city"), j.get("country")) if x),
                            department=j.get("department", ""), employment_type=j.get("employment_type_code", ""),
                            posted_at=to_iso(j.get("published_at") or j.get("created_at")), updated_at="",
                            description=strip_html((j.get("description") or "") + "\n" + (j.get("requirements") or "")),
                            remote=bool(j.get("remote")), source="recruitee"))
        return jobs
    @classmethod
    def probe(cls, http, token, hint=None):
        try:
            r = http.get(f"https://{token}.recruitee.com/api/offers/")
            if r.status_code != 200: return False, None, {}
            j = r.json(); return "offers" in j, len(j.get("offers", [])), {}
        except Exception: return False, None, {}

class BambooHR(Scraper):
    provider = "bamboohr"
    def fetch(self, http, company, known_ext_ids=None):
        t = company["ats_token"]; known = known_ext_ids or set()
        data = http.get_json(f"https://{t}.bamboohr.com/careers/list", headers={"Accept": "application/json"})
        jobs = []
        for j in data.get("result", []):
            ext = str(j.get("id")); loc = j.get("location") or {}
            locs = ", ".join(x for x in (loc.get("city"), loc.get("state"), loc.get("country")) if x) if isinstance(loc, dict) else str(loc)
            desc = None
            if ext not in known:
                try:
                    d = http.get_json(f"https://{t}.bamboohr.com/careers/{ext}/detail", headers={"Accept": "application/json"})
                    desc = strip_html(((d.get("result") or {}).get("jobOpening") or {}).get("description", ""))
                except HttpError: desc = ""
            jobs.append(Job(ext_id=ext, title=j.get("jobOpeningName", ""), url=f"https://{t}.bamboohr.com/careers/{ext}",
                            location=locs, department=j.get("departmentLabel", ""), employment_type=j.get("employmentStatusLabel", ""),
                            posted_at=to_iso(j.get("datePosted")), description=desc, remote=bool(j.get("isRemote")), source="bamboohr"))
        return jobs
    @classmethod
    def probe(cls, http, token, hint=None):
        try:
            r = http.get(f"https://{token}.bamboohr.com/careers/list", headers={"Accept": "application/json"})
            if r.status_code != 200: return False, None, {}
            j = r.json(); return "result" in j, len(j.get("result", [])), {}
        except Exception: return False, None, {}

class Rippling(Scraper):
    provider = "rippling"
    def fetch(self, http, company, known_ext_ids=None):
        t = company["ats_token"]
        data = http.get_json(f"https://api.rippling.com/platform/api/ats/v1/board/{t}/jobs")
        jobs = []
        items = data if isinstance(data, list) else data.get("items") or data.get("jobs") or []
        for j in items:
            locs = "; ".join((l.get("name") or l.get("label") or "") for l in (j.get("workLocation") and [j["workLocation"]] or j.get("locations") or []) if isinstance(l, dict))[:200]
            jobs.append(Job(ext_id=str(j.get("id") or j.get("uuid")), title=j.get("name") or j.get("title", ""), url=j.get("url") or j.get("hostedUrl") or f"https://ats.rippling.com/{t}/jobs/{j.get('id')}",
                            location=locs or (j.get("location") or ""), department=(j.get("department") or {}).get("name", "") if isinstance(j.get("department"), dict) else (j.get("department") or ""),
                            employment_type=j.get("employmentType") or "", posted_at=to_iso(j.get("createdAt") or j.get("publishedAt")),
                            description=strip_html(j.get("description") or j.get("descriptionPlain") or ""), source="rippling"))
        return jobs
    @classmethod
    def probe(cls, http, token, hint=None):
        try:
            r = http.get(f"https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs")
            if r.status_code != 200: return False, None, {}
            j = r.json(); items = j if isinstance(j, list) else j.get("items") or j.get("jobs") or []
            return isinstance(items, list), len(items), {}
        except Exception: return False, None, {}

class Personio(Scraper):
    provider = "personio"
    def fetch(self, http, company, known_ext_ids=None):
        import xml.etree.ElementTree as ET
        t = company["ats_token"]
        r = http.get(f"https://{t}.jobs.personio.com/xml?language=en", headers={"Accept": "application/xml"})
        if r.status_code != 200: raise HttpError(f"HTTP {r.status_code}")
        root = ET.fromstring(r.content); jobs = []
        for pos in root.iter("position"):
            g = lambda k: (pos.findtext(k) or "").strip()
            desc = "\n".join(strip_html(d.findtext("value") or "") for d in pos.iter("jobDescription"))
            jobs.append(Job(ext_id=g("id"), title=g("name"), url=f"https://{t}.jobs.personio.com/job/{g('id')}", location=g("office"),
                            department=g("department"), employment_type=f"{g('employmentType')} {g('seniority')}".strip(),
                            posted_at=to_iso(g("createdAt")), description=desc, source="personio"))
        return jobs
    @classmethod
    def probe(cls, http, token, hint=None):
        try:
            r = http.get(f"https://{token}.jobs.personio.com/xml?language=en", headers={"Accept": "application/xml"})
            return (r.status_code == 200 and b"<position" in r.content), r.content.count(b"<position>"), {}
        except Exception: return False, None, {}

class Teamtailor(Scraper):
    provider = "teamtailor"
    def fetch(self, http, company, known_ext_ids=None):
        t = company["ats_token"]
        r = http.get(f"https://{t}.teamtailor.com/jobs.json" , headers={"Accept": "application/json"})
        if r.status_code != 200:
            # fallback: parse public jobs page
            r = http.get(f"https://{t}.teamtailor.com/jobs", headers={"Accept": "text/html"})
            if r.status_code != 200: raise HttpError(f"HTTP {r.status_code}")
            jobs = []
            for m in re.finditer(r'<a[^>]+href="(https://[^"]+/jobs/(\d+)[^"]*)"[^>]*>\s*<span[^>]*>(.+?)</span>', r.text, re.S):
                jobs.append(Job(ext_id=m.group(2), title=strip_html(m.group(3)), url=m.group(1), source="teamtailor"))
            return jobs
        data = r.json(); jobs = []
        for j in data.get("jobs", data if isinstance(data, list) else []):
            jobs.append(Job(ext_id=str(j.get("id")), title=j.get("title", ""), url=j.get("url") or j.get("links", {}).get("careersite-job-url", ""),
                            location=j.get("location", ""), department=j.get("department", ""), description=strip_html(j.get("body") or ""), source="teamtailor"))
        return jobs
    @classmethod
    def probe(cls, http, token, hint=None):
        try:
            r = http.get(f"https://{token}.teamtailor.com/jobs", headers={"Accept": "text/html"})
            return (r.status_code == 200 and "/jobs/" in r.text), None, {}
        except Exception: return False, None, {}

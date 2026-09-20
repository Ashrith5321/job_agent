"""Scraper interface. Each scraper turns a company row into a list of Job objects."""
from dataclasses import dataclass, field
import json

@dataclass
class Job:
    ext_id: str
    title: str
    url: str
    location: str = ""
    apply_url: str = ""
    department: str = ""
    employment_type: str = ""
    posted_at: str = ""
    updated_at: str = ""
    description: str | None = None   # None => keep whatever is already stored (details not re-fetched)
    remote: bool = False
    extra_flags: list = field(default_factory=list)  # e.g. ["intern"] from ATS metadata
    source: str = ""

class ScrapeError(Exception):
    pass

class PartialResult(Exception):
    """Raised when we got some jobs but the listing is known-incomplete (don't close missing jobs)."""
    def __init__(self, jobs, msg):
        super().__init__(msg); self.jobs = jobs

def extra(company):
    e = company.get("ats_extra")
    if not e: return {}
    if isinstance(e, dict): return e
    try: return json.loads(e)
    except Exception: return {}

class Scraper:
    provider = "base"
    complete_listing = True   # if False, absent jobs are not auto-closed after one run

    def fetch(self, http, company, known_ext_ids=None):
        raise NotImplementedError

    # Used by the prober: return (ok:bool, n_jobs:int|None, extra:dict)
    @classmethod
    def probe(cls, http, token, hint=None):
        raise NotImplementedError

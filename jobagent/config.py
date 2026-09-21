import json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = ROOT / "config"
LOGS = ROOT / "logs"
DB_PATH = DATA / "jobs.db"
SETTINGS_PATH = CONFIG / "settings.json"
COMPANIES_PATH = CONFIG / "companies.json"

DEFAULTS = {
    "active_company_limit": 500,
    "store_min_relevance_intern": 15,
    "store_min_relevance_fulltime": 50,
    "track_fulltime": False,
    "track_newgrad": False,
    "us_only": True,
    "handshake_cookie": "",
    "handshake_queries": ["robotics intern", "perception intern", "computer vision intern", "autonomy intern", "motion planning intern",
                          "machine learning intern robotics", "autonomous vehicles intern", "simulation intern robotics", "SLAM intern", "controls intern robotics"],
    "scrape_workers": 10,
    "linkcheck_workers": 8,
    "linkcheck_max_age_days": 3,
    "http_timeout": 25,
    "linkedin_enabled": True,
    "linkedin_min_interval_sec": 2.5,
    "linkedin_search_queries": [
        "robotics intern", "perception intern", "autonomy intern", "motion planning intern",
        "computer vision intern", "machine learning robotics intern", "SLAM intern",
        "robotics simulation intern", "autonomous vehicles intern", "3D perception intern",
        "reinforcement learning robotics intern", "controls engineering intern robotics"
    ],
    "linkedin_search_locations": ["United States"],
    "linkedin_pages_per_query": 3,
    "linkedin_company_pages": 3,
    "linkedin_details_per_run": 60,
    "amazon_queries": ["robotics intern", "perception intern", "computer vision intern",
                       "machine learning intern", "applied scientist intern", "autonomous intern",
                       "motion planning", "simulation intern", "3D vision"],
    "apple_queries": ["robotics", "computer vision intern", "machine learning intern",
                      "perception", "autonomous systems", "simulation intern", "3D"],
    "workday_queries": ["intern", "internship", "co-op", "robotics", "perception", "autonomy",
                        "computer vision", "machine learning", "motion planning", "simulation"],
    "greenhouse_detail_cap": 250,
    "notify_desktop": True,
    "notify_webhook_url": "",
    "notify_email": {"enabled": False, "smtp_host": "", "smtp_port": 587, "username": "", "password": "", "to": ""},
    "dashboard_host": "127.0.0.1",
    "dashboard_port": 8765,
    "auto_swap_companies": True,
    "auto_discover": True,
    "contacts_per_run": 25,
    "contacts_search_delay": 2.5,
    "discover_homepage_fetches": 40,
    "discover_probe_per_run": 25,
    "max_swaps_per_run": 5,
}

_settings = None
def settings():
    global _settings
    if _settings is None:
        s = dict(DEFAULTS)
        if SETTINGS_PATH.exists():
            try:
                s.update(json.loads(SETTINGS_PATH.read_text()))
            except Exception as e:
                print(f"[config] failed to parse {SETTINGS_PATH}: {e}")
        _settings = s
    return _settings

def ensure_dirs():
    for p in (DATA, CONFIG, LOGS):
        p.mkdir(parents=True, exist_ok=True)

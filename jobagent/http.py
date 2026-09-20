"""Thin HTTP client: per-host throttling, retries, browser-like headers."""
import time, threading, random
from urllib.parse import urlparse
import requests
from requests.adapters import HTTPAdapter

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

class HttpError(Exception):
    def __init__(self, msg, status=None, url=None):
        super().__init__(msg); self.status = status; self.url = url

class Http:
    def __init__(self, timeout=25, default_interval=0.25, host_intervals=None, retries=2):
        self.timeout = timeout
        self.default_interval = default_interval
        self.host_intervals = dict(host_intervals or {})
        self.retries = retries
        self.s = requests.Session()
        self.s.headers.update(BASE_HEADERS)
        ad = HTTPAdapter(pool_connections=64, pool_maxsize=64)
        self.s.mount("https://", ad); self.s.mount("http://", ad)
        self._last = {}
        self._lock = threading.Lock()
        self.stats = {"requests": 0, "errors": 0}

    def _interval_for(self, host):
        for h, iv in self.host_intervals.items():
            if host.endswith(h): return iv
        return self.default_interval

    def _throttle(self, url):
        host = urlparse(url).netloc
        iv = self._interval_for(host)
        if iv <= 0: return
        with self._lock:
            last = self._last.get(host, 0)
            wait = last + iv - time.time()
            if wait > 0:
                # reserve the slot before sleeping so other threads queue behind us
                self._last[host] = time.time() + wait
            else:
                self._last[host] = time.time()
        if wait > 0: time.sleep(wait + random.uniform(0, 0.1))

    def request(self, method, url, **kw):
        kw.setdefault("timeout", self.timeout)
        kw.setdefault("allow_redirects", True)
        last_exc = None
        for attempt in range(self.retries + 1):
            self._throttle(url)
            try:
                self.stats["requests"] += 1
                r = self.s.request(method, url, **kw)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    if attempt < self.retries:
                        ra = r.headers.get("Retry-After")
                        delay = min(float(ra), 30) if (ra and ra.isdigit()) else (2 ** attempt) * 1.5
                        time.sleep(delay); continue
                return r
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                if attempt < self.retries: time.sleep((2 ** attempt) * 1.0); continue
        self.stats["errors"] += 1
        raise HttpError(f"{type(last_exc).__name__}: {str(last_exc)[:160]}", url=url)

    def get(self, url, **kw): return self.request("GET", url, **kw)
    def post(self, url, **kw): return self.request("POST", url, **kw)
    def head(self, url, **kw): return self.request("HEAD", url, **kw)

    def get_json(self, url, ok=(200,), **kw):
        r = self.get(url, **kw)
        if r.status_code not in ok:
            raise HttpError(f"HTTP {r.status_code}", status=r.status_code, url=url)
        try:
            return r.json()
        except ValueError:
            raise HttpError("non-JSON response", status=r.status_code, url=url)

    def post_json(self, url, payload, ok=(200,), headers=None, **kw):
        h = {"Content-Type": "application/json"}
        if headers: h.update(headers)
        r = self.post(url, json=payload, headers=h, **kw)
        if r.status_code not in ok:
            raise HttpError(f"HTTP {r.status_code}", status=r.status_code, url=url)
        try:
            return r.json()
        except ValueError:
            raise HttpError("non-JSON response", status=r.status_code, url=url)

_default = None
def default_http():
    global _default
    if _default is None:
        from .config import settings
        st = settings()
        _default = Http(timeout=st["http_timeout"],
                        host_intervals={"linkedin.com": st["linkedin_min_interval_sec"],
                                        "amazon.jobs": 0.6, "apple.com": 0.8,
                                        "myworkdayjobs.com": 0.4, "greenhouse.io": 0.15,
                                        "lever.co": 0.15, "ashbyhq.com": 0.15})
    return _default

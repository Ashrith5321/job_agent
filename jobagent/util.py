import re, html as _html, datetime as _dt

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")
NL_RE = re.compile(r"\n{3,}")

def strip_html(s, limit=4000):
    if not s: return ""
    s = _html.unescape(s)
    if "<" in s and ">" in s:
        s = _html.unescape(s)  # greenhouse double-escapes
        s = re.sub(r"</(p|div|li|h\d|tr|br)\s*>|<br\s*/?>", "\n", s, flags=re.I)
        s = TAG_RE.sub(" ", s)
    s = WS_RE.sub(" ", s)
    s = NL_RE.sub("\n\n", s).strip()
    return s[:limit]

def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()

def to_iso(v):
    """Best-effort normalize dates (epoch ms/s, ISO strings, 'Sep 20, 2026', 'September 15, 2026')."""
    if v is None or v == "": return ""
    try:
        if isinstance(v, (int, float)):
            if v > 1e12: v = v / 1000.0
            return _dt.datetime.fromtimestamp(v, _dt.timezone.utc).date().isoformat()
        s = str(v).strip()
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
        if m: return m.group(1)
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y"):
            try: return _dt.datetime.strptime(s, fmt).date().isoformat()
            except ValueError: pass
    except Exception:
        pass
    return str(v)[:32]

def norm_text(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")

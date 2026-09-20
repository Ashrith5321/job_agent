"""Digest + notifications: markdown digest file, desktop notify-send, optional webhook / email."""
import json, shutil, subprocess, smtplib, os
from email.mime.text import MIMEText
from .config import DATA, settings
from .db import rows
from .util import now_iso

def build_digest(conn, run_id):
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    new = rows(conn, """SELECT j.*, c.name AS company FROM events e JOIN jobs j ON j.id=e.job_id JOIN companies c ON c.id=j.company_id
                        WHERE e.run_id=? AND e.kind IN ('new','reopened') AND j.status='open' ORDER BY j.is_intern DESC, j.relevance DESC""", (run_id,))
    closed = rows(conn, """SELECT j.*, c.name AS company FROM events e JOIN jobs j ON j.id=e.job_id JOIN companies c ON c.id=j.company_id
                           WHERE e.run_id=? AND e.kind='closed' AND (j.user_status IN ('applied','interviewing','interested') OR j.starred=1)""", (run_id,))
    broken = rows(conn, """SELECT j.*, c.name AS company FROM events e JOIN jobs j ON j.id=e.job_id JOIN companies c ON c.id=j.company_id
                           WHERE e.run_id=? AND e.kind='link_broken' AND j.user_status IN ('applied','interviewing','interested')""", (run_id,))
    interns = [j for j in new if j["is_intern"]]; ng = [j for j in new if j["is_newgrad"] and not j["is_intern"]]; ft = [j for j in new if not j["is_intern"] and not j["is_newgrad"]]
    st = settings(); url = f"http://{st['dashboard_host']}:{st['dashboard_port']}"
    L = [f"# Job Agent digest — {run['finished_at'] or now_iso()}", "",
         f"Scraped {run['companies_ok']}/{run['companies_total']} companies ({run['companies_err']} errors). "
         f"Seen {run['jobs_seen']} listings; **{run['jobs_new']} new**, {run['jobs_closed']} closed, {run['jobs_reopened']} reopened. "
         f"Links checked: {run['links_checked']} ({run['links_broken']} broken).", "", f"Dashboard: {url}", ""]
    def sec(title, items, cap=60):
        if not items: return
        L.append(f"## {title} ({len(items)})"); L.append("")
        for j in items[:cap]:
            L.append(f"- **[{j['relevance']}]** {j['company']} — [{j['title']}]({j['url']}) · {j['location'] or '—'} · posted {j['posted_at'] or '?'}")
        if len(items) > cap: L.append(f"- … and {len(items)-cap} more on the dashboard")
        L.append("")
    sec("New internships / co-ops", interns); sec("New new-grad / early-career", ng); sec("New relevant full-time roles", ft, 30)
    sec("Closed — roles you starred / applied to", closed); sec("Broken links on roles you're tracking", broken)
    text = "\n".join(L)
    (DATA / "digests").mkdir(exist_ok=True)
    (DATA / "digest_latest.md").write_text(text)
    (DATA / "digests" / f"{(run['finished_at'] or now_iso()).replace(':','-')}.md").write_text(text)
    return text, {"interns": len(interns), "newgrad": len(ng), "fulltime": len(ft), "closed_tracked": len(closed), "broken_tracked": len(broken)}

def notify_desktop(title, body):
    if not shutil.which("notify-send"): return False
    env = dict(os.environ)
    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{os.getuid()}/bus"
    if "DISPLAY" not in env: env["DISPLAY"] = ":0"
    try:
        subprocess.run(["notify-send", "-a", "Job Agent", "-i", "system-search", title, body], env=env, timeout=10)
        return True
    except Exception:
        return False

def notify_webhook(url, text):
    if not url: return False
    import requests
    try:
        requests.post(url, json={"text": text[:3800], "content": text[:1900]}, timeout=15); return True
    except Exception:
        return False

def notify_email(cfg, subject, text):
    if not cfg.get("enabled"): return False
    try:
        msg = MIMEText(text, "plain"); msg["Subject"] = subject; msg["From"] = cfg["username"]; msg["To"] = cfg["to"]
        with smtplib.SMTP(cfg["smtp_host"], int(cfg.get("smtp_port", 587)), timeout=30) as s:
            s.starttls(); s.login(cfg["username"], cfg["password"]); s.sendmail(cfg["username"], [cfg["to"]], msg.as_string())
        return True
    except Exception as e:
        print(f"[notify] email failed: {e}"); return False

def send_all(conn, run_id):
    st = settings()
    text, counts = build_digest(conn, run_id)
    summary = (f"{counts['interns']} new internships, {counts['newgrad']} new-grad, {counts['fulltime']} full-time. "
               f"{counts['closed_tracked']} tracked roles closed.")
    if st.get("notify_desktop"): notify_desktop("Job Agent: scrape finished", summary)
    if st.get("notify_webhook_url"): notify_webhook(st["notify_webhook_url"], text)
    notify_email(st.get("notify_email") or {}, f"[Job Agent] {summary}", text)
    return summary

# Robotics Internship Radar (job_agent)

A local bot that watches ~400 US robotics / autonomy / perception / ML companies twice a day (06:00 and 18:00),
tracks which **US internships** are open (classified BS / MS / PhD), verifies links, lets you mark what you applied to,
discovers new companies (YC directory, VC portfolios, LinkedIn/Handshake hits), and shows it all on a localhost dashboard.

Zero dependencies beyond system Python 3 + `requests` (already installed on this machine).

## Quick start

```bash
cd ~/Documents/job_agent
python3 -m jobagent init        # load the seed company list into the DB (config/companies_seed_*.txt)
python3 -m jobagent probe       # auto-discover each company's job board (Greenhouse/Lever/Ashby/Workday/…)
python3 -m jobagent scrape      # scrape everything, check links, write a digest, send a desktop notification
python3 -m jobagent serve       # dashboard at http://127.0.0.1:8765
./scripts/install_schedule.sh   # systemd --user timers for 06:00 + 18:00 and the dashboard as a service
```

## How it works

* **Sources.** Companies are scraped straight from their applicant-tracking-system APIs (Greenhouse, Lever, Ashby,
  Workday, SmartRecruiters, Workable, Recruitee, BambooHR, Rippling, Personio, Teamtailor) plus custom scrapers for
  Amazon and Apple. Companies whose career sites block bots (Google, Microsoft, Uber, Tesla, Meta, …) are covered
  through LinkedIn's public guest job search filtered by company ID. A global LinkedIn keyword search
  ("robotics intern", "perception intern", …) catches postings from companies not yet in the list.
* **Discovery (`probe`).** For each company the prober fetches its careers page, looks for board links, verifies
  the board via the provider's API, and falls back to slug guessing with an identity check.
* **Scoring.** Every listing is classified (intern / new-grad / full-time) and scored 0–100 for relevance to
  robotics, planning, perception/CV/3D, ML, controls/optimization, simulation. Non-technical roles are dropped.
  See `jobagent/scoring.py` — tune the keyword groups there.
* **State.** SQLite at `data/jobs.db`. Jobs get `open` → `closed` when they disappear from a full listing, or when
  the link dies on two consecutive checks. Reopened jobs are flagged. Your `applied / interested / …` status and
  notes live on the job row and survive re-scrapes.
* **Link checks.** Open relevant jobs are re-verified every 3 days (404/410 or "no longer available" pages → broken).
* **Company churn.** `active_company_limit` (500) caps the tracked set. Companies that show up in LinkedIn search
  results with relevant intern postings are recorded as candidates; once they have a working board and ≥3 hits
  they can swap in for the weakest tracked company (no relevant jobs, or repeatedly failing). Max 5 swaps/run.
* **US only / interns only.** `us_only` drops any posting whose location is outside the US (`jobagent/geo.py`);
  postings with no location are kept only for US-headquartered companies. `track_fulltime`/`track_newgrad` are off.
* **Degree level.** Titles/descriptions are parsed for BS / MS / PhD (`degree_level` in `scoring.py`); filter and sort
  by it on the dashboard.
* **Discovery.** Each scheduled run pulls the YC directory (public mirror) and a set of VC portfolio pages, keyword-scores
  candidates for robotics/AV/perception, probes them for a job board, and activates the ones with relevant US intern
  postings. Companies that show up in LinkedIn/Handshake search hits with a relevant intern posting are added the same
  way. `python3 -m jobagent discover` runs it by hand.
* **Handshake.** Requires your university login. In your browser, log into app.joinhandshake.com, open DevTools →
  Network → click any request to app.joinhandshake.com → copy the full `cookie` request header value into
  `config/settings.json` → `"handshake_cookie"`. Sessions expire every few weeks; the run log says when it needs refreshing.
* **Recruiters / hiring managers (Recruiters tab).** For each tracked company the bot collects contacts from
  *public* sources only: emails on the company's careers/contact pages, emails embedded in job postings, and
  search-engine-indexed LinkedIn profile titles ("Jane Doe – University Recruiter at Acme"). No LinkedIn login is used
  (logged-in scraping gets accounts banned). Every email carries a confidence: `found` (seen verbatim on a public
  page), `pattern` (built from the company's observed email format), `guess` (first.last@domain, unverified).
  25 companies are processed per scheduled run (`contacts_per_run`), prioritising companies with open internships;
  `python3 -m jobagent contacts --only <slug> --force` does one on demand. Mark contacted / add notes / correct emails
  on the dashboard; CSV export available.
* **Notifications.** After each run: `data/digest_latest.md` (+ dated copy in `data/digests/`), a desktop
  notification, and optionally a Slack/Discord-style webhook or email (see `config/settings.json`).

## CLI

| command | what |
|---|---|
| `init` | load `config/companies_seed_*.txt` + `config/companies.json` into the DB |
| `probe [--all] [--only slug] [-v]` | discover / re-verify job boards |
| `scrape [--limit N] [--only slug] [--no-linkcheck] [--no-notify]` | run a scrape |
| `serve` | dashboard |
| `add "Company Name" domain.com [--relevance 80] [--category humanoid]` | add + probe one company |
| `contacts [--only slug] [--limit N] [--force]` | find recruiter / hiring-manager contacts |
| `discover [--no-vc] [--no-promote]` | find new companies (YC, VC portfolios) and activate promising ones |
| `linkcheck`, `digest`, `stats`, `rebalance`, `export` | utilities |

## Files

```
config/settings.json          your settings (git-ignored: holds the Handshake cookie); copy from settings.example.json
config/companies_seed_*.txt   the curated company list (name|domain|category|relevance|hq|hints)
config/companies.json         resolved registry (written by init/probe/export) – edit ATS tokens here
data/jobs.db                  SQLite database
data/digest_latest.md         latest run digest
logs/                         scrape.log, probe.log, dashboard.log
```

## Adding a company by hand

`python3 -m jobagent add "Acme Robotics" acmerobotics.com --relevance 85 --category humanoid`
or add a line to a seed file and run `init` + `probe --only acme-robotics`.
If the prober can't find a board, set it manually in `config/companies.json`
(`"ats_provider": "greenhouse", "ats_token": "acme"`) and run `init`.

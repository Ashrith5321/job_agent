#!/usr/bin/env bash
# Installs systemd --user timers: scrape at 06:00 and 18:00 local time, plus the dashboard as an always-on service.
# Falls back to cron if systemd --user is unavailable. Re-runnable.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$(command -v python3)"
if systemctl --user status >/dev/null 2>&1; then
  mkdir -p ~/.config/systemd/user
  cat > ~/.config/systemd/user/jobagent-scrape.service <<UNIT
[Unit]
Description=Job Agent scrape (robotics internships)
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=$PY -m jobagent scrape --kind scheduled
StandardOutput=append:$ROOT/logs/scrape.log
StandardError=append:$ROOT/logs/scrape.log
TimeoutStartSec=3h
UNIT
  cat > ~/.config/systemd/user/jobagent-scrape.timer <<UNIT
[Unit]
Description=Run Job Agent scrape at 06:00 and 18:00
[Timer]
OnCalendar=*-*-* 06:00:00
OnCalendar=*-*-* 18:00:00
Persistent=true
RandomizedDelaySec=120
[Install]
WantedBy=timers.target
UNIT
  cat > ~/.config/systemd/user/jobagent-dashboard.service <<UNIT
[Unit]
Description=Job Agent dashboard (http://127.0.0.1:8765)
After=network.target
[Service]
WorkingDirectory=$ROOT
ExecStart=$PY -m jobagent serve
Restart=on-failure
RestartSec=5
StandardOutput=append:$ROOT/logs/dashboard.log
StandardError=append:$ROOT/logs/dashboard.log
[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now jobagent-scrape.timer jobagent-dashboard.service
  # keep user services running when not logged in (may prompt for password; harmless if it fails)
  loginctl enable-linger "$USER" 2>/dev/null || true
  echo "Installed. Next runs:"; systemctl --user list-timers jobagent-scrape.timer --no-pager
  echo "Dashboard: systemctl --user status jobagent-dashboard.service"
else
  LINE1="0 6 * * * cd $ROOT && $PY -m jobagent scrape --kind scheduled >> $ROOT/logs/scrape.log 2>&1"
  LINE2="0 18 * * * cd $ROOT && $PY -m jobagent scrape --kind scheduled >> $ROOT/logs/scrape.log 2>&1"
  LINE3="@reboot cd $ROOT && $PY -m jobagent serve >> $ROOT/logs/dashboard.log 2>&1"
  ( crontab -l 2>/dev/null | grep -v "jobagent" ; echo "$LINE1"; echo "$LINE2"; echo "$LINE3" ) | crontab -
  echo "Installed cron entries:"; crontab -l | grep jobagent
fi

# eBay Watch Analyzer MVP

This MVP searches eBay US for wristwatch listings likely suited for flipping (non-working/for-parts) using the official eBay Buy Browse API. It scores candidates using seller trust signals and watch condition keywords, then outputs a ranked CSV for manual review.

> **Important note:** This tool provides candidate discovery and risk scoring only. Pricing/ROI requires comps and manual analysis in this MVP. Use official eBay APIs only to avoid policy violations.

## Features
- Uses eBay Buy Browse API search + getItem (no HTML scraping).
- Scores listings with seller feedback thresholds, return policy signals, condition IDs, and keyword matches.
- Persists seen items in SQLite to avoid reprocessing.
- Outputs `data/candidates.csv` plus `data/raw.jsonl` for debugging.

## Setup

### 1) Create a virtual environment
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### 2) Install dependencies
```bash
pip install -r requirements.txt
```

### 3) Configure environment
Copy `.env.example` to `.env` and add your eBay API credentials.
```bash
cp .env.example .env
```

`.env` variables:
- `EBAY_CLIENT_ID`
- `EBAY_CLIENT_SECRET`
- `EBAY_MARKETPLACE_ID` (default `EBAY_US`)
- `MAX_PRICE` (default `300`)
- `MIN_FEEDBACK_PCT` (default `97.5`)
- `MIN_FEEDBACK_SCORE` (default `50`)
- `RUN_QUERIES` (optional comma-separated list)

### 4) Run
```bash
python -m src.app
```

Outputs:
- `data/candidates.csv`
- `data/raw.jsonl`
- `data/run.log`

## Scheduling

### macOS launchd (every 15 minutes)
Create `~/Library/LaunchAgents/com.ebay.watchanalyzer.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.ebay.watchanalyzer</string>
    <key>ProgramArguments</key>
    <array>
      <string>/bin/bash</string>
      <string>-lc</string>
      <string>cd /path/to/ebay_watch_analyzer && source .venv/bin/activate && python -m src.app</string>
    </array>
    <key>StartInterval</key>
    <integer>900</integer>
    <key>StandardOutPath</key>
    <string>/path/to/ebay_watch_analyzer/data/launchd.out</string>
    <key>StandardErrorPath</key>
    <string>/path/to/ebay_watch_analyzer/data/launchd.err</string>
    <key>RunAtLoad</key>
    <true/>
  </dict>
</plist>
```
Load it:
```bash
launchctl load ~/Library/LaunchAgents/com.ebay.watchanalyzer.plist
```

### Linux cron (every 15 minutes)
```bash
crontab -e
```
Add:
```bash
*/15 * * * * cd /path/to/ebay_watch_analyzer && . .venv/bin/activate && python -m src.app >> data/cron.log 2>&1
```

### Optional systemd service + timer
`/etc/systemd/system/ebay-watch-analyzer.service`:
```ini
[Unit]
Description=eBay Watch Analyzer

[Service]
Type=oneshot
WorkingDirectory=/path/to/ebay_watch_analyzer
EnvironmentFile=/path/to/ebay_watch_analyzer/.env
ExecStart=/path/to/ebay_watch_analyzer/.venv/bin/python -m src.app
```

`/etc/systemd/system/ebay-watch-analyzer.timer`:
```ini
[Unit]
Description=Run eBay Watch Analyzer every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
Unit=ebay-watch-analyzer.service

[Install]
WantedBy=timers.target
```
Enable:
```bash
sudo systemctl enable --now ebay-watch-analyzer.timer
```

## Notes
- eBay rate limits (HTTP 429) are handled with exponential backoff.
- Tokens are cached until near expiry to reduce auth requests.
- Missing data fields are tolerated in scoring and output.

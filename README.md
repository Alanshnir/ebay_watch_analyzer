# eBay Watch Analyzer MVP

This MVP searches eBay US for wristwatch listings likely suited for flipping (non-working/for-parts) using the official eBay Buy Browse API. It scores candidates using seller trust signals and watch condition keywords, then runs an optional AI analysis step (OpenAI or Gemini) to estimate equivalent sale price, sellability, and likely replacement parts.

> **Important note:** This tool provides candidate discovery and risk scoring only. AI outputs are estimates, not guarantees. Pricing/ROI still requires manual comp validation.

## Features
- Uses eBay Buy Browse API search + getItem (no HTML scraping).
- Scores listings with seller feedback thresholds, return policy signals, condition IDs, and keyword matches.
- Optional AI step to evaluate each listing + pictures and estimate:
  - whether it is a flip candidate
  - equivalent selling price for a working equivalent watch
  - ease of sale (`high|medium|low`)
  - likely parts to replace + parts cost estimate
  - estimated profit (`equivalent_sale_price - all_in_cost - parts_cost`)
- Persists seen items in SQLite to avoid reprocessing.
- Outputs only top profitable flips (`TOP_N_RESULTS`, default 5) to `data/candidates.csv`.

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

Required eBay env vars:
- `EBAY_CLIENT_ID`
- `EBAY_CLIENT_SECRET`
- `EBAY_MARKETPLACE_ID` (default `EBAY_US`)
- `MAX_PRICE` (default `300`)
- `MIN_FEEDBACK_PCT` (default `97.5`)
- `MIN_FEEDBACK_SCORE` (default `50`)
- `RUN_QUERIES` (optional comma-separated list)
- `TOP_N_RESULTS` (default `5`)

Optional AI Step 2 env vars:
- `AI_PROVIDER` (`openai` or `gemini`; leave empty to disable AI step)
- OpenAI:
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL` (default `gpt-4.1-mini`)
- Gemini:
  - `GEMINI_API_KEY`
  - `GEMINI_MODEL` (default `gemini-1.5-flash`)

### 4) Run
```bash
python -m src.app
```

If you receive a 401 `invalid_client` error, confirm you're using the production app credentials from your eBay developer account (not sandbox credentials), and that values in `.env` match exactly.

Outputs:
- `data/candidates.csv` (top N profitable flips)
- `data/raw.jsonl`
- `data/run.log`

## CSV Columns (minimum + AI step)
Base columns:
- `run_timestamp`, `itemId`, `title`, `itemWebUrl`, `price_value`, `shipping_value`, `all_in_cost`, `currency`, `condition`, `conditionId`, `listingType`, `buyingOptions`, `image_url`, `seller_username`, `seller_feedback_pct`, `seller_feedback_score`, `returns_accepted`, `score_total`, `score_reasons`

AI columns:
- `ai_provider`, `ai_model`, `ai_flip_candidate`, `ai_equivalent_sale_price`, `ai_sell_ease`, `ai_needed_parts`, `ai_parts_cost_estimate`, `ai_confidence`, `ai_summary`, `ai_estimated_profit`, `ai_error`

When AI is enabled and returns estimates, results are sorted by `ai_estimated_profit DESC` then `score_total DESC`; otherwise they fallback to `score_total DESC` + `all_in_cost ASC`.

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
- Token caching is implemented until near expiry.
- Missing fields are tolerated in both scoring and AI enrichment.
- Use official eBay APIs only.

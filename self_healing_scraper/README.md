# Self-Healing Web Scraper with Automated Login Session Maintenance

A config-driven scraping pipeline that logs into authenticated sites via
Playwright, caches and refreshes session cookies automatically, scrapes
with a lightweight `requests` + `BeautifulSoup` core for speed, and
self-heals when a page's structure changes — capturing a screenshot and
alerting an admin via Telegram/email instead of failing silently.

## Features

- **Config-driven targets** — add a new site by editing `config/config.json`, no new code required.
- **Automated login + cookie persistence** — Playwright handles the login form once; cookies are reused until they go stale, then refreshed automatically.
- **Fast scraping core** — cookies are handed off to a plain `requests.Session()` for speed; Playwright is only used for login and failure snapshots.
- **Stealth basics** — rotating User-Agent strings and randomized jitter between requests.
- **Self-healing on failure**:
  - Auth failures (`401`/`403`) trigger an automatic re-login and one retry.
  - Structural failures (selectors return nothing — DOM likely changed) trigger a Playwright screenshot and a Telegram/email alert with the exact failure context.
- **Alert cooldown** — repeated failures on the same target won't spam the admin.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # then fill in your real credentials/tokens
```

## Configure a target

Edit `config/config.json`. Each target needs a `url`, `login_required` flag,
CSS `selectors` for the fields you want, and an `output` block. If
`login_required` is `true`, also fill in `login_url` and `login_selectors`.

## Run

```bash
python main.py                              # run all targets
python main.py --target example_public_listing   # run just one
```

## Project layout

```
self_healing_scraper/
├── config/config.json        # target sites, selectors, output settings
├── core/
│   ├── session_manager.py    # Playwright login + cookie cache/refresh
│   ├── scraper_engine.py     # requests + BeautifulSoup scraping core
│   └── healer.py             # screenshot capture + Telegram/email alerts
├── storage/                  # cookies.json, screenshots/, scrape output (gitignored)
├── .env.example               # copy to .env, never commit the real one
├── requirements.txt
└── main.py                   # orchestration entry point
```

## Notes

- Only scrape sites you have permission to scrape and in compliance with their terms of service and applicable law.
- `storage/cookies.json` and `storage/screenshots/` are gitignored by default — they contain session data and shouldn't be committed.

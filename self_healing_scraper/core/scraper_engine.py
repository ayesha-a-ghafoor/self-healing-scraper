"""
scraper_engine.py
------------------
The fast production scraping core. Takes cookies from session_manager
(if any), injects them into a lightweight requests.Session(), and pulls
+ parses pages with BeautifulSoup.

Includes basic anti-blocking hygiene:
- Random User-Agent rotation
- Randomized jitter between requests
- Retry-with-backoff on transient errors (429/5xx)

Raises specific exceptions so main.py can route failures to healer.py.
"""

import time
import random
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

try:
    _ua = UserAgent()
except Exception:
    _ua = None

_FALLBACK_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]


class AuthenticationExpiredError(Exception):
    """Raised when a request comes back 401/403 — signals session_manager
    should force a re-login."""
    pass


class StructuralParseError(Exception):
    """Raised when expected selectors return nothing — signals the site's
    DOM has likely changed and healer.py should investigate."""
    pass


class ScraperEngine:
    def __init__(self, target_config: dict, request_settings: dict, logger=None):
        self.target = target_config
        self.settings = request_settings
        self.logger = logger
        self.session = requests.Session()

    # ------------------------------------------------------------------
    def load_cookies(self, cookies: list):
        """Accepts Playwright-format cookie dicts and injects them into
        the requests.Session cookie jar."""
        for c in cookies:
            self.session.cookies.set(
                c.get("name"), c.get("value"), domain=c.get("domain", "")
            )

    # ------------------------------------------------------------------
    def scrape(self) -> list:
        """
        Runs the full scrape for this target, following pagination if
        configured. Returns a list of extracted record dicts.
        """
        all_records = []
        url = self.target["url"]
        pagination = self.target.get("pagination", {})
        max_pages = pagination.get("max_pages", 1) if pagination.get("enabled") else 1

        for page_num in range(1, max_pages + 1):
            self._log(f"[{self.target['name']}] Fetching page {page_num}: {url}")
            html = self._fetch_with_retries(url)
            records, next_url = self._parse(html)
            all_records.extend(records)

            self._jitter_pause()

            if not pagination.get("enabled") or not next_url:
                break
            url = next_url

        return all_records

    # ------------------------------------------------------------------
    def _fetch_with_retries(self, url: str) -> str:
        max_retries = self.settings.get("max_retries", 3)
        timeout = self.settings.get("timeout_seconds", 15)

        last_error = None
        for attempt in range(1, max_retries + 1):
            headers = {"User-Agent": self._get_user_agent()}
            try:
                resp = self.session.get(url, headers=headers, timeout=timeout)
            except requests.RequestException as e:
                last_error = e
                self._log(f"Network error on attempt {attempt}: {e}")
                self._backoff(attempt)
                continue

            if resp.status_code in (401, 403):
                raise AuthenticationExpiredError(
                    f"Received {resp.status_code} for {url} — session likely expired."
                )

            if resp.status_code == 429 or resp.status_code >= 500:
                self._log(
                    f"[{self.target['name']}] Got {resp.status_code}, "
                    f"backing off (attempt {attempt}/{max_retries})."
                )
                self._backoff(attempt)
                continue

            resp.raise_for_status()
            return resp.text

        raise RuntimeError(
            f"Failed to fetch {url} after {max_retries} attempts. Last error: {last_error}"
        )

    def _parse(self, html: str):
        soup = BeautifulSoup(html, "html.parser")
        selectors = self.target["selectors"]

        containers = soup.select(selectors["container"])
        if not containers:
            raise StructuralParseError(
                f"No elements matched container selector "
                f"'{selectors['container']}' — page structure may have changed."
            )

        records = []
        for c in containers:
            record = {}
            # Dynamic field parsing based on config.json keys
            for field, sel in selectors.items():
                if field == "container" or not sel:
                    continue
                el = c.select_one(sel)
                record[field] = el.get_text(strip=True) if el else None

            records.append(record)

        next_url = None
        pagination = self.target.get("pagination", {})
        if pagination.get("enabled"):
            next_btn = soup.select_one(pagination.get("next_button_selector", ""))
            if next_btn and next_btn.get("href"):
                raw_href = next_btn["href"]
                # Converts relative paths like '/page/2/' into full URLs like 'http://quotes.toscrape.com/page/2/'
                next_url = urljoin(self.target["url"], raw_href)

        return records, next_url

    # ------------------------------------------------------------------
    def _get_user_agent(self) -> str:
        if _ua:
            try:
                return _ua.random
            except Exception:
                pass
        return random.choice(_FALLBACK_AGENTS)

    def _jitter_pause(self):
        low = self.settings.get("min_delay_seconds", 1.0)
        high = self.settings.get("max_delay_seconds", 3.0)
        delay = random.uniform(low, high)
        time.sleep(delay)

    def _backoff(self, attempt: int):
        base = min(2 ** attempt, 30)
        time.sleep(base + random.uniform(0, 1))

    def _log(self, msg: str):
        if self.logger:
            self.logger(msg)
        else:
            print(msg)
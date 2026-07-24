"""
session_manager.py
-------------------
Handles authentication and session/cookie lifecycle.

Responsibilities:
- Launch a headless Playwright browser and perform login when a target
  requires authentication.
- Persist session cookies to storage/cookies.json so subsequent runs
  can skip the login step.
- Detect stale/expired sessions and trigger a fresh login automatically
  (the "self-healing" part of session handling).
"""

import os
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

COOKIES_PATH = Path(__file__).resolve().parent.parent / "storage" / "cookies.json"
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 6  # treat cookies older than 6 hours as stale


class SessionManager:
    def __init__(self, target_config: dict, logger=None):
        self.target = target_config
        self.logger = logger
        self.name = target_config["name"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def get_valid_cookies(self) -> list:
        """
        Returns a list of cookie dicts usable by requests.Session().
        Reuses cached cookies if they are still fresh; otherwise logs in
        again via Playwright.
        """
        cached = self._load_cached_cookies()
        if cached and self._cookies_are_fresh(cached):
            self._log(f"[{self.name}] Reusing cached session cookies.")
            return cached["cookies"]

        self._log(f"[{self.name}] No fresh cookies found — logging in via Playwright.")
        return self._login_and_capture_cookies()

    def force_relogin(self) -> list:
        """
        Called by the healer when a downstream request fails auth
        (e.g. HTTP 401/403) even though cached cookies looked fresh.
        """
        self._log(f"[{self.name}] Forcing re-authentication after auth failure.")
        return self._login_and_capture_cookies()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _login_and_capture_cookies(self) -> list:
        # Flexible lookup: checks both 'login_selectors' block and root 'selectors' dictionary
        login_cfg = self.target.get("login_selectors") or self.target.get("selectors", {})
        login_url = self.target.get("login_url")
        
        # Verify required keys exist before attempting login
        required_keys = ["username_field", "password_field", "submit_button"]
        has_required_keys = all(k in login_cfg for k in required_keys)

        if not login_url or not has_required_keys:
            raise ValueError(
                f"Target '{self.name}' is marked login_required but is missing "
                f"'login_url' or login selectors in config.json."
            )

        username = os.environ.get("TARGET_USERNAME")
        password = os.environ.get("TARGET_PASSWORD")
        if not username or not password:
            raise EnvironmentError(
                "TARGET_USERNAME / TARGET_PASSWORD not set. Add them to your .env file."
            )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            try:
                page.goto(login_url, wait_until="domcontentloaded", timeout=20000)
                page.fill(login_cfg["username_field"], username)
                page.fill(login_cfg["password_field"], password)
                page.click(login_cfg["submit_button"])

                # Checks for either success_indicator or login_indicator
                success_sel = login_cfg.get("success_indicator") or login_cfg.get("login_indicator")
                if success_sel:
                    page.wait_for_selector(success_sel, timeout=15000)
                else:
                    page.wait_for_load_state("networkidle", timeout=15000)

                self._log(f"[{self.name}] Login successful.")

            except PlaywrightTimeoutError:
                raise RuntimeError(
                    f"Login flow for '{self.name}' timed out — selectors may "
                    f"have changed or credentials are invalid."
                )

            cookies = context.cookies()
            browser.close()

        self._save_cookies(cookies)
        return cookies

    def _load_cached_cookies(self):
        if not COOKIES_PATH.exists():
            return None
        try:
            with open(COOKIES_PATH, "r") as f:
                all_sessions = json.load(f)
            return all_sessions.get(self.name)
        except (json.JSONDecodeError, OSError):
            return None

    def _cookies_are_fresh(self, cached_entry: dict) -> bool:
        saved_at = cached_entry.get("saved_at", 0)
        return (time.time() - saved_at) < COOKIE_MAX_AGE_SECONDS

    def _save_cookies(self, cookies: list):
        COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)

        all_sessions = {}
        if COOKIES_PATH.exists():
            try:
                with open(COOKIES_PATH, "r") as f:
                    all_sessions = json.load(f)
            except (json.JSONDecodeError, OSError):
                all_sessions = {}

        all_sessions[self.name] = {
            "cookies": cookies,
            "saved_at": time.time(),
        }

        with open(COOKIES_PATH, "w") as f:
            json.dump(all_sessions, f, indent=2)

        self._log(f"[{self.name}] Cookies saved to {COOKIES_PATH}")

    def _log(self, msg: str):
        if self.logger:
            self.logger(msg)
        else:
            print(msg)
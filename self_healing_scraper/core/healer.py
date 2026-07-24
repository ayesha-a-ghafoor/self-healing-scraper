
"""
healer.py
---------
The defensive layer. Wraps scraper failures, takes a visual snapshot of
what the page actually looked like when things broke, and sends an
alert (Telegram and/or email) so a human can look at it.

This module intentionally does NOT try to auto-fix selectors — that's
a much harder problem (and easy to get wrong silently). Its job is to
fail loudly and usefully: capture evidence, notify a human fast, and
keep the rest of the pipeline running for other targets.
"""

import os
import time
import requests
from pathlib import Path
from playwright.sync_api import sync_playwright

SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent / "storage" / "screenshots"

# Simple in-process alert throttling so a flapping site doesn't spam
# the admin's phone every retry loop.
_last_alert_sent = {}
ALERT_COOLDOWN_SECONDS = 15 * 60


class Healer:
    def __init__(self, alert_settings: dict, logger=None):
        self.alert_settings = alert_settings
        self.logger = logger

    # ------------------------------------------------------------------
    def handle_failure(self, target_name: str, url: str, error: Exception):
        """
        Main entry point called by main.py when a target fails.
        Captures a screenshot, then routes an alert if not in cooldown.
        """
        self._log(f"[{target_name}] Handling failure: {error}")

        screenshot_path = self._capture_snapshot(target_name, url)

        if self._should_alert(target_name):
            self._send_alert(target_name, url, error, screenshot_path)
            _last_alert_sent[target_name] = time.time()
        else:
            self._log(
                f"[{target_name}] Alert suppressed (cooldown active) — "
                f"failure was still logged and screenshotted."
            )

    # ------------------------------------------------------------------
    def _capture_snapshot(self, target_name: str, url: str) -> str | None:
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{target_name}_{int(time.time())}.png"
        filepath = SCREENSHOTS_DIR / filename

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.screenshot(path=str(filepath), full_page=True)
                browser.close()
            self._log(f"[{target_name}] Snapshot saved: {filepath}")
            return str(filepath)
        except Exception as e:
            self._log(f"[{target_name}] Could not capture snapshot: {e}")
            return None

    def _should_alert(self, target_name: str) -> bool:
        last = _last_alert_sent.get(target_name, 0)
        return (time.time() - last) >= ALERT_COOLDOWN_SECONDS

    def _send_alert(self, target_name: str, url: str, error: Exception, screenshot_path):
        message = (
            f"🚨 Scraper failure: {target_name}\n"
            f"URL: {url}\n"
            f"Error: {type(error).__name__}: {error}\n"
            f"Screenshot: {screenshot_path or 'unavailable'}"
        )

        if self.alert_settings.get("telegram_enabled"):
            self._send_telegram(message, screenshot_path)

        if self.alert_settings.get("email_enabled"):
            self._send_email(message)

    # ------------------------------------------------------------------
    def _send_telegram(self, message: str, screenshot_path: str | None):
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            self._log("Telegram alerting enabled but TELEGRAM_BOT_TOKEN / "
                       "TELEGRAM_CHAT_ID missing from .env — skipping alert.")
            return

        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": message},
                timeout=10,
            )
            if screenshot_path and Path(screenshot_path).exists():
                with open(screenshot_path, "rb") as img:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/sendPhoto",
                        data={"chat_id": chat_id},
                        files={"photo": img},
                        timeout=15,
                    )
        except requests.RequestException as e:
            self._log(f"Failed to send Telegram alert: {e}")

    def _send_email(self, message: str):
        import smtplib
        from email.mime.text import MIMEText

        host = os.environ.get("SMTP_HOST")
        port = int(os.environ.get("SMTP_PORT", 587))
        user = os.environ.get("SMTP_USER")
        password = os.environ.get("SMTP_PASSWORD")
        to_addr = os.environ.get("ALERT_EMAIL_TO")

        if not all([host, user, password, to_addr]):
            self._log("Email alerting enabled but SMTP_* env vars are incomplete "
                       "— skipping alert.")
            return

        msg = MIMEText(message)
        msg["Subject"] = "Self-Healing Scraper Alert"
        msg["From"] = user
        msg["To"] = to_addr

        try:
            with smtplib.SMTP(host, port) as server:
                server.starttls()
                server.login(user, password)
                server.sendmail(user, [to_addr], msg.as_string())
        except Exception as e:
            self._log(f"Failed to send email alert: {e}")

    def _log(self, msg: str):
        if self.logger:
            self.logger(msg)
        else:
            print(msg)

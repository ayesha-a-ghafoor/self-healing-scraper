"""
main.py
-------
Entry point. Loops over targets in config/config.json, orchestrates
login (if needed), scraping, output saving, and failure handling.

Usage:
    python main.py
    python main.py --target example_public_listing
"""

import os
import sys
import json
import csv
import argparse
from pathlib import Path
from datetime import datetime 
from dotenv import load_dotenv

from core.session_manager import SessionManager
from core.scraper_engine import (
    ScraperEngine,
    AuthenticationExpiredError,
    StructuralParseError,
)
from core.healer import Healer

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "config.json"


def log(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def save_output(target_config: dict, records: list):
    output_cfg = target_config["output"]
    out_path = ROOT / output_cfg["path"]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if output_cfg["format"] == "json":
        with open(out_path, "w") as f:
            json.dump(records, f, indent=2)
    elif output_cfg["format"] == "csv":
        if not records:
            log(f"[{target_config['name']}] No records to write.")
            return
        fieldnames = list(records[0].keys())
        with open(out_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
    else:
        raise ValueError(f"Unsupported output format: {output_cfg['format']}")

    log(f"[{target_config['name']}] Saved {len(records)} records to {out_path}")


def run_target(target_config: dict, request_settings: dict, alert_settings: dict):
    name = target_config["name"]
    healer = Healer(alert_settings, logger=log)
    engine = ScraperEngine(target_config, request_settings, logger=log)

    try:
        if target_config.get("login_required"):
            session_mgr = SessionManager(target_config, logger=log)
            cookies = session_mgr.get_valid_cookies()
            engine.load_cookies(cookies)

        try:
            records = engine.scrape()
        except AuthenticationExpiredError:
            # Self-healing: force a fresh login once, then retry the scrape.
            log(f"[{name}] Auth expired mid-scrape — forcing re-login and retrying once.")
            session_mgr = SessionManager(target_config, logger=log)
            cookies = session_mgr.force_relogin()
            engine.load_cookies(cookies)
            records = engine.scrape()

        save_output(target_config, records)
        return True

    except StructuralParseError as e:
        healer.handle_failure(name, target_config["url"], e)
        return False
    except Exception as e:
        healer.handle_failure(name, target_config["url"], e)
        return False


def main():
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description="Self-Healing Web Scraper")
    parser.add_argument(
        "--target",
        help="Run only the target with this name from config.json",
        default=None,
    )
    args = parser.parse_args()

    config = load_config()
    request_settings = config.get("request_settings", {})
    alert_settings = config.get("alerting", {})
    targets = config["targets"]

    if args.target:
        targets = [t for t in targets if t["name"] == args.target]
        if not targets:
            log(f"No target named '{args.target}' found in config.json.")
            sys.exit(1)

    results = {}
    for target_config in targets:
        log(f"=== Starting target: {target_config['name']} ===")
        success = run_target(target_config, request_settings, alert_settings)
        results[target_config["name"]] = "OK" if success else "FAILED"

    log("=== Run summary ===")
    for name, status in results.items():
        log(f"  {name}: {status}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Best-effort visible-page fare candidate collector. Never bypasses access controls."""
from __future__ import annotations
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DB_PATH = ROOT / "data" / "fares.db"
JSON_PATH = ROOT / "site" / "fares.json"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
JSON_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scanned_at TEXT NOT NULL,
  origin TEXT NOT NULL,
  destination TEXT NOT NULL,
  departure_date TEXT NOT NULL,
  return_date TEXT NOT NULL,
  currency TEXT NOT NULL,
  candidate_price INTEGER,
  status TEXT NOT NULL,
  note TEXT,
  source_url TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_route_time ON observations(origin, destination, scanned_at);
CREATE TABLE IF NOT EXISTS scan_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  routes_attempted INTEGER NOT NULL DEFAULT 0,
  note TEXT
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def flight_url(origin: str, destination: str, dep: str, ret: str) -> str:
    query = f"Flights from {origin} to {destination} on {dep} returning {ret}"
    return "https://www.google.com/travel/flights?q=" + quote(query) + "&curr=INR&hl=en&gl=IN"


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def collect_visible_text(page, url: str) -> tuple[str, str]:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_timeout(4500)
        page.locator("body").wait_for(timeout=10000)
        text = page.locator("body").inner_text(timeout=10000)
    except PlaywrightTimeoutError:
        text = ""
    lower = text.lower()
    if any(x in lower for x in ["unusual traffic", "our systems have detected", "captcha", "automated queries"]):
        return text, "blocked_or_challenge"
    if not text.strip():
        return text, "empty_page"
    return text, "page_loaded"


def candidate_inr(text: str) -> int | None:
    """Extract the lowest visible INR-looking amount; it remains unverified."""
    patterns = [
        r"₹\s*([0-9][0-9,]{2,})",
        r"INR\s*([0-9][0-9,]{2,})",
        r"Rs\.?\s*([0-9][0-9,]{2,})",
    ]
    values: list[int] = []
    for pattern in patterns:
        for raw in re.findall(pattern, text, flags=re.IGNORECASE):
            try:
                value = int(raw.replace(",", ""))
                if 1000 <= value <= 2000000:
                    values.append(value)
            except ValueError:
                continue
    return min(values) if values else None


def send_telegram(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or not CONFIG.get("telegram_alerts", False):
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=15,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"Telegram alert failed: {exc}")
        return False


def export_dashboard_data(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    observations = [dict(row) for row in conn.execute(
        "SELECT * FROM observations ORDER BY scanned_at DESC, id DESC LIMIT 3000"
    )]
    runs = [dict(row) for row in conn.execute(
        "SELECT * FROM scan_runs ORDER BY id DESC LIMIT 100"
    )]
    payload = {
        "generated_at": utc_now(),
        "config": CONFIG,
        "observations": observations,
        "runs": runs,
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    started = utc_now()
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    run_cur = conn.execute(
        "INSERT INTO scan_runs(started_at, status, routes_attempted, note) VALUES(?, 'running', 0, '')",
        (started,),
    )
    run_id = run_cur.lastrowid
    conn.commit()
    alerts: list[str] = []
    routes_attempted = 0
    statuses: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata")
            page = context.new_page()
            page.set_default_navigation_timeout(45000)
            for route in CONFIG["routes"]:
                origin, destination = route["origin"], route["destination"]
                dep = route.get("departure_date", CONFIG["departure_date"])
                ret = route.get("return_date", CONFIG["return_date"])
                url = flight_url(origin, destination, dep, ret)
                scanned_at = utc_now()
                price = None
                status = "scan_error"
                note = "No reliable candidate extracted"
                try:
                    text, page_status = collect_visible_text(page, url)
                    if page_status == "blocked_or_challenge":
                        status, note = page_status, "Access challenge detected; no bypass attempted"
                    elif page_status == "empty_page":
                        status, note = page_status, "No visible page text"
                    else:
                        price = candidate_inr(text)
                        if price is not None:
                            status = "needs_review"
                            note = "Lowest visible INR-like amount; verify manually before relying on it"
                            if price <= int(CONFIG.get("target_price_inr", 18000)):
                                alerts.append(f"{origin}→{destination}: candidate ₹{price:,} (target ₹{int(CONFIG['target_price_inr']):,}); verify manually. {url}")
                        else:
                            status, note = "no_inr_price_detected", "Page loaded but no INR-like amount was parsed"
                except Exception as exc:
                    status, note = "scan_error", f"{type(exc).__name__}: {str(exc)[:180]}"
                conn.execute(
                    """INSERT INTO observations(scanned_at,origin,destination,departure_date,return_date,currency,candidate_price,status,note,source_url)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (scanned_at, origin, destination, dep, ret, CONFIG.get("currency", "INR"), price, status, note, url),
                )
                conn.commit()
                routes_attempted += 1
                statuses.append(f"{origin}-{destination}:{status}")
                print(f"{origin}-{destination}: {status}; candidate={price}")
                # Be polite; do not hammer a provider with immediate sequential requests.
                time.sleep(3)
            browser.close()
        final_status = "completed" if not all(s.endswith("scan_error") for s in statuses) else "partial_failure"
        conn.execute(
            "UPDATE scan_runs SET finished_at=?, status=?, routes_attempted=?, note=? WHERE id=?",
            (utc_now(), final_status, routes_attempted, "; ".join(statuses), run_id),
        )
        conn.commit()
        for alert in alerts:
            send_telegram("FareRadar price candidate (unverified)\n" + alert)
    except Exception as exc:
        conn.execute(
            "UPDATE scan_runs SET finished_at=?, status='failed', routes_attempted=?, note=? WHERE id=?",
            (utc_now(), routes_attempted, f"{type(exc).__name__}: {str(exc)[:250]}", run_id),
        )
        conn.commit()
        raise
    finally:
        export_dashboard_data(conn)
        conn.close()


if __name__ == "__main__":
    main()

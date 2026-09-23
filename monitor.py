"""Read-only BookMyShow seat monitor for the Paradise show.

The monitor opens the direct seat-layout page and reads BookMyShow's
accessible seat table. It never clicks a seat or proceeds to checkout.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BOOKING_URL = (
    "https://in.bookmyshow.com/cinemas/hyderabad/allu-cinemas-kokapet/"
    "buytickets/ALUC/20260924"
)
# BookMyShow's direct seat-layout route is the reliable entry point. The
# session id is supplied by the show listing and can be overridden if BMS
# rotates it for a future publication of the show.
SEAT_LAYOUT_URL = os.getenv(
    "BOOKMYSHOW_SEAT_LAYOUT_URL",
    "https://in.bookmyshow.com/movies/hyderabad/seat-layout/"
    "ET00436621/ALUC/5299/20260924",
)
TARGET_TIME = "10:40 PM"
TARGET_DATE = "2026-09-24"
TARGET_VENUE = "ALLU Cinemas: Kokapet"
TARGET_MOVIE = "The Paradise"
TARGET_CATEGORY = os.getenv("TARGET_CATEGORY", "GOLD").strip().upper()
TARGET_SEATS = {
    value.strip().upper()
    for value in os.getenv("TARGET_SEATS", "M15,M16,M17").split(",")
    if value.strip()
}
IST = ZoneInfo("Asia/Kolkata")
SHOW_START = datetime(2026, 9, 24, 22, 40, tzinfo=IST)
STATE_FILE = Path("state/alert_state.json")


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"alert_sent": False, "last_status": "unknown", "last_checked": None}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def send_telegram(message: str) -> None:
    import urllib.parse
    import urllib.request

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}
    ).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8")
    if '"ok":true' not in body:
        raise RuntimeError(f"Telegram API did not confirm success: {body[:500]}")


def seat_statuses(page) -> dict[str, str]:
    """Read statuses from BookMyShow's accessibility table, never selecting."""
    page.get_by_role("button", name="Open accessibility seat selection").click()

    category = page.locator("#category-select")
    category.wait_for(state="visible", timeout=15_000)
    labels = category.locator("option").all_text_contents()
    category_label = next(
        (label for label in labels if label.strip().upper().startswith(TARGET_CATEGORY)),
        None,
    )
    if not category_label:
        raise RuntimeError(
            f"Target seat category {TARGET_CATEGORY!r} not found; options={labels!r}"
        )
    category.select_option(label=category_label)

    row_select = page.locator("#row-select")
    row_select.wait_for(state="visible", timeout=10_000)
    row_labels = row_select.locator("option").all_text_contents()
    row_label = next(
        (label for label in row_labels if re.search(r"\bM\b", label.upper())), None
    )
    if not row_label:
        raise RuntimeError(f"Target row M not found; options={row_labels!r}")
    row_select.select_option(label=row_label)

    table = page.locator('table[aria-label="Seats for Row GOLD-M"]')
    table.wait_for(state="visible", timeout=10_000)
    result = {seat: "unknown" for seat in TARGET_SEATS}
    for row in table.locator("tbody tr").all():
        cells = row.locator("td").all_text_contents()
        if len(cells) < 2:
            continue
        seat = f"M{int(cells[0].strip())}"
        if seat not in result:
            continue
        status = cells[1].strip().lower()
        if status == "available":
            result[seat] = "available"
        elif status in {"booked", "sold", "unavailable"}:
            result[seat] = "unavailable"
        else:
            result[seat] = "unknown"
    return result


def main() -> None:
    state = load_state()
    now = datetime.now(IST)
    result = {
        "checked_at_ist": now.isoformat(),
        "movie": TARGET_MOVIE,
        "venue": TARGET_VENUE,
        "date": TARGET_DATE,
        "time": TARGET_TIME,
        "seat_layout_url": SEAT_LAYOUT_URL,
        "seats": {},
    }

    if now >= SHOW_START:
        result["status"] = "expired"
        result["reason"] = "Target show has already started or passed"
        state.update(last_checked=result["checked_at_ist"], last_status="expired")
        save_state(state)
        print(json.dumps(result, indent=2))
        return

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(locale="en-IN", timezone_id="Asia/Kolkata")
        try:
            page.goto(SEAT_LAYOUT_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3_000)
            body = page.locator("body").inner_text(timeout=10_000)
            for expected in (TARGET_MOVIE, TARGET_VENUE, "24 September, 2026", TARGET_TIME):
                if expected not in body:
                    raise RuntimeError(f"Target show not found in seat-layout page: {expected}")

            result["seats"] = seat_statuses(page)
            result["status"] = "checked"
            result["all_target_seats_available"] = all(
                result["seats"].get(seat) == "available" for seat in TARGET_SEATS
            )

            if result["all_target_seats_available"]:
                if not state.get("alert_sent", False):
                    send_telegram(
                        "🚨 THE PARADISE SEATS AVAILABLE!\n\n"
                        f"{TARGET_VENUE}\n24 Sep 2026 • {TARGET_TIME}\n\n"
                        f"Seats: {', '.join(sorted(TARGET_SEATS))}\n\n"
                        f"Book them now:\n{BOOKING_URL}"
                    )
                    state["alert_sent"] = True
                    state["last_status"] = "alert_sent"
                else:
                    state["last_status"] = "available_already_alerted"
            else:
                # A later all-available event can alert again after any seat
                # becomes unavailable.
                state["alert_sent"] = False
                state["last_status"] = "not_all_available"
            state["last_checked"] = result["checked_at_ist"]
            save_state(state)
            print(json.dumps(result, indent=2))
        except Exception as exc:
            result["status"] = "error"
            result["reason"] = str(exc)
            state.update(last_checked=result["checked_at_ist"], last_status=f"error: {type(exc).__name__}")
            save_state(state)
            Path("artifacts").mkdir(exist_ok=True)
            try:
                page.screenshot(path="artifacts/bookmyshow-error.png", full_page=True)
            except Exception:
                pass
            print(json.dumps(result, indent=2))
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()

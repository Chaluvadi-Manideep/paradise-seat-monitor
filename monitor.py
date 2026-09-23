import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

SHOW_URL = "https://in.bookmyshow.com/cinemas/hyderabad/allu-cinemas-kokapet/buytickets/ALUC/20260924"
TARGET_TIME = "10:40 PM"
TARGET_SEATS = {s.strip().upper() for s in os.getenv("TARGET_SEATS", "G7,G8,G9").split(",") if s.strip()}
IST = ZoneInfo("Asia/Kolkata")
STATE_FILE = Path("state/alert_state.json")


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"alert_sent": False, "last_status": "unknown", "last_checked": None}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def normalize(value):
    return re.sub(r"\s+", " ", value or "").strip().upper()


def send_telegram(message):
    import urllib.parse
    import urllib.request

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode()

    request = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8")
        if '"ok":true' not in body:
            raise RuntimeError(f"Telegram API did not confirm success: {body[:500]}")


def visible_seat_state(page, seat_name):
    # BookMyShow's markup can change. We inspect the seat text plus nearby
    # accessibility/class metadata rather than relying on one CSS class.
    loc = page.get_by_text(seat_name, exact=True)
    count = loc.count()

    if count == 0:
        return "not_found"

    states = []
    for i in range(min(count, 10)):
        try:
            el = loc.nth(i)
            if not el.is_visible():
                continue

            data = page.evaluate(
                """el => {
                    const parts = [];
                    let n = el;
                    for (let i = 0; n && i < 5; i++, n = n.parentElement) {
                      parts.push([
                        n.tagName,
                        n.getAttribute('class') || '',
                        n.getAttribute('aria-label') || '',
                        n.getAttribute('data-testid') || '',
                        n.getAttribute('data-seat') || '',
                        n.getAttribute('data-status') || ''
                      ].join(' '));
                    }
                    return parts.join(' | ');
                }""",
                el,
            )
            s = normalize(data)

            if any(x in s for x in [
                "SOLD", "BOOKED", "UNAVAILABLE", "NOT AVAILABLE",
                "DISABLED", "OCCUPIED", "TAKEN"
            ]):
                states.append("unavailable")
            elif any(x in s for x in [
                "AVAILABLE", "SELECTABLE", "FREE", "VACANT"
            ]):
                states.append("available")
            else:
                # If the element can be clicked, it is a useful availability
                # signal even when BookMyShow changes its class names.
                try:
                    if el.is_enabled():
                        states.append("clickable")
                    else:
                        states.append("unavailable")
                except Exception:
                    states.append("unknown")
        except Exception:
            continue

    if "available" in states or "clickable" in states:
        return "available"
    if "unavailable" in states:
        return "unavailable"
    return "unknown"


def open_show(page):
    page.goto(SHOW_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)

    # Dismiss common overlays without depending on a single selector.
    for text in ["Got it", "Accept", "Continue", "Allow"]:
        try:
            page.get_by_text(text, exact=True).first.click(timeout=1500)
        except Exception:
            pass

    # Select the exact 10:40 PM show.
    show = page.get_by_text(TARGET_TIME, exact=True)
    if show.count() == 0:
        # Fallback: the page may have rendered the show in a button/card.
        show = page.locator("text=" + TARGET_TIME)

    if show.count() == 0:
        raise RuntimeError(f"Could not find {TARGET_TIME} on the BookMyShow listing page.")

    clicked = False
    for i in range(min(show.count(), 5)):
        try:
            candidate = show.nth(i)
            if candidate.is_visible():
                candidate.click(timeout=5000)
                clicked = True
                break
        except Exception:
            continue

    if not clicked:
        raise RuntimeError(f"Found {TARGET_TIME}, but could not click the show.")

    page.wait_for_timeout(7000)


def main():
    state = load_state()
    now = datetime.now(IST)

    result = {
        "checked_at_ist": now.isoformat(),
        "show_url": SHOW_URL,
        "target_show": TARGET_TIME,
        "seats": {},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            open_show(page)

            for seat in sorted(TARGET_SEATS):
                result["seats"][seat] = visible_seat_state(page, seat)

            states = set(result["seats"].values())
            all_available = all(
                result["seats"].get(seat) == "available"
                for seat in TARGET_SEATS
            )

            result["all_three_available"] = all_available

            if all_available and not state.get("alert_sent", False):
                send_telegram(
                    "🚨 THE PARADISE SEATS AVAILABLE!\n\n"
                    "ALLU Cinemas: Kokapet\n"
                    "24 Sep 2026 • 10:40 PM\n"
                    f"Seats: {', '.join(sorted(TARGET_SEATS))}\n\n"
                    "Book them now:\n" + SHOW_URL
                )
                state["alert_sent"] = True
                state["last_status"] = "alert_sent"
            elif all_available:
                state["last_status"] = "available_already_alerted"
            else:
                state["last_status"] = "not_all_available"

            state["last_checked"] = result["checked_at_ist"]
            save_state(state)

            print(json.dumps(result, indent=2))

        except Exception as exc:
            # Save a screenshot to help diagnose BookMyShow markup changes.
            try:
                page.screenshot(path="artifacts/bookmyshow-error.png", full_page=True)
            except Exception:
                pass
            state["last_checked"] = result["checked_at_ist"]
            state["last_status"] = f"error: {type(exc).__name__}"
            save_state(state)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()

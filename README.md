Paradise seat monitor patch

This patch replaces the blocked cinema-listing scrape with BookMyShow's direct
seat-layout flow. The monitor opens the target seat-layout page, turns on the
site's accessibility seat view, selects the GOLD category and M row, and reads
the structured seat table. It does not click seats and never enters checkout.

Copy `monitor.py` and `requirements.txt` to the repository root, and replace
`.github/workflows/seat-monitor.yml` with `seat-monitor.yml` from this folder.
Keep the existing `telegram-test.yml` unchanged.

The direct URL currently contains session `5299`. If BookMyShow rotates that
session for a republished show, update the `BOOKMYSHOW_SEAT_LAYOUT_URL`
repository variable or edit the default in `monitor.py`.

The monitor reports `available`, `unavailable`, or `unknown`; only three
`available` values trigger Telegram. State is committed to
`state/alert_state.json`, duplicate alerts are suppressed, and the flag resets
after a later check sees a seat unavailable.

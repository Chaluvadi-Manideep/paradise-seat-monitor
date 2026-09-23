# Paradise Seat Monitor

Cloud monitor for:

- Movie: The Paradise
- Cinema: ALLU Cinemas: Kokapet
- Date: 24 September 2026
- Show: 10:40 PM
- Target seats: G7, G8, G9
- Polling: every 5 minutes through GitHub Actions
- Notification: Telegram

## Required GitHub Secrets

Add these repository secrets:

- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

Never put either value directly into source code.

## Important

The monitor does not purchase tickets. It only detects the target seats and sends a Telegram notification.

BookMyShow uses a dynamic seat-selection interface, so the workflow saves a diagnostic screenshot if the page structure changes or the seat map cannot be reached.

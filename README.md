# FareRadar — cloud-hosted flight fare tracker

FareRadar is a starter project for hourly fare observations using GitHub Actions, Playwright, SQLite, a static dashboard, CSV export, and optional Telegram alerts. It runs in the cloud on a schedule, so your PC can be off.

## Default tracking plan
- IXC → SHJ
- IXC → DXB
- ATQ → SHJ
- Departure: 22 December 2026
- Return: 10 January 2027
- Scan cadence: hourly (GitHub Actions may delay scheduled runs)
- Alert threshold: ₹18,000 (edit `config.json`)

## Important limitations
- This uses visible-page browser automation against Google Flights, **not** a paid airfare API. Automated access may be blocked, rate-limited, or disallowed by a provider's terms. FareRadar does not bypass CAPTCHA, login walls, or other access controls. Check the provider's terms and stop scanning if access is denied.
- A detected number is stored as a **candidate**, not a verified bookable fare. Google Flights pages can change, and visible text parsing can mistake taxes, baggage, or unrelated prices for the total. Confirm any candidate manually on the airline or booking page before making decisions.
- GitHub Actions is best-effort: hourly schedules can run late or be skipped during high load. The workflow can be started manually from the Actions tab.
- GitHub Pages on the free plan generally requires a public repository. The dashboard and committed SQLite history will therefore be public. Do not put passwords, API tokens, personal identifiers, or other private information in this repository. If public trip history is unacceptable, use a private paid plan or redesign storage/hosting.
- SQLite is committed back to the repository so history survives ephemeral cloud runners. This is simple and free, but it is not a high-concurrency database.

## Setup
1. Create a **new public GitHub repository** named `FareRadar` (or choose a different name).
2. Upload the contents of this folder to the repository's root, keeping the `.github/workflows/hourly-scan.yml` path.
3. In repository **Settings → Actions → General → Workflow permissions**, enable **Read and write permissions** so the workflow can commit the database and JSON history.
4. In **Settings → Pages**, choose **GitHub Actions** as the build/deployment source.
5. Open **Actions → FareRadar hourly scan → Run workflow** to run the first scan. Scheduled runs use the same workflow every hour at minute 17.
6. After a successful deployment, open the Pages URL shown under **Settings → Pages**.

### Optional Telegram alerts
1. Create a bot with Telegram's official BotFather and obtain a bot token.
2. Get your chat ID using a method you trust; send the bot a message first.
3. In **Settings → Secrets and variables → Actions**, add repository secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Set `telegram_alerts` to `true` in `config.json`, commit, and run the workflow. Never commit the token into the repository.

## Configuration
Edit `config.json` to change the dates, threshold, currency, routes, or alert preference. The current return-date format is a round trip for each route. For different dates per route, extend the route entries with their own `departure_date` and `return_date`.

## Data files
- `data/fares.db`: SQLite database with every scan observation and run status.
- `site/fares.json`: dashboard data generated from SQLite.
- Dashboard: `site/index.html`.

The database stores timestamps in UTC and displays them in the browser's local time zone. `needs_review` means a possible price was found, not that a fare is confirmed.

# vimo-VN

Auto runner for Vietnamese macro reports.

## What it does

- Runs on GitHub Actions every day at 07:30 Asia/Bangkok.
- Tracks 46 macro and market cards based on the 41-indicator `vimovietnam` structure, with extra market context.
- Fetches machine-readable daily values for USD/VND, gold, oil, DXY, US 10Y, S&P 500, and VN-Index, with Vietcap as the primary VN-Index source.
- Adds `VIP` labels to monthly/yearly macro indicators.
- Monitors the five official/free macro sources used by the reference project: PMI, NSO, Customs, VBMA, and VNBA.
- Reads the latest S&P Global Vietnam manufacturing PMI from Viet Nam Government News.
- Parses strict NSO CPI, IIP, retail, and tourism indicators automatically, with API/RSS fallback and transient-network retries.
- Extracts interbank rates, 10-year government-bond yield, and bond issuance from the latest VBMA weekly PDF.
- Preserves the last verified monthly/weekly official values with a `STALE_CACHE` label when a source is temporarily unreachable.
- Keeps official macro indicators in `awaiting_official_source` until a reliable parser/source is added, instead of inventing numbers.
- Generates output files into `output/` and `docs/`.
- Commits changed output back to the repository.
- Sends a Telegram message when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are configured.

## Manual run

Open the repository on GitHub, go to **Actions**, choose **Vimo VN Auto Run**, then click **Run workflow**.

## Telegram setup

Repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Do not commit the bot token into files.

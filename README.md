# vimo-VN

Auto runner for Vietnamese macro reports.

## What it does

- Runs on GitHub Actions every day at 07:30 Asia/Bangkok.
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

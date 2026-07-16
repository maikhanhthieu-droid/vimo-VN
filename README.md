# vimo-VN

Auto runner for Vietnamese macro reports.

## What it does

- Runs on GitHub Actions every day at 07:30 Asia/Bangkok.
- Tracks the original 41-indicator `vimovietnam` structure.
- Fetches machine-readable daily values for USD/VND, gold, oil, DXY, US 10Y, S&P 500, and VN-Index, with Vietcap as the primary VN-Index source.
- Adds `VIP` labels to monthly/yearly macro indicators.
- Monitors the five official/free macro sources used by the reference project: PMI, NSO, Customs, VBMA, and VNBA.
- Reads the latest S&P Global Vietnam manufacturing PMI from Viet Nam Government News.
- Parses strict NSO CPI, IIP, retail, and tourism indicators automatically, with API/RSS fallback and transient-network retries.
- Extracts interbank rates, 10-year government-bond yield, and bond issuance from the latest VBMA weekly PDF.
- Preserves the last observed non-daily values with a `STALE_CACHE` label when a source is temporarily unreachable; daily market values are never frozen as a cache fallback.
- Uses `data/verified_baseline.json` only when neither a live parser nor prior observed value is available; every baseline includes its period, source URL, quality flag, and definition note.
- Shows the data date on every dashboard card so an older verified value is never presented as today's observation.
- Keeps official macro indicators in `awaiting_official_source` until a reliable parser/source is added, instead of inventing numbers.
- Stores indicator state and value-change events in `output/indicator_memory.json`; unchanged monthly/quarterly observations are reused without creating new work.
- Sends only the latest pending event for each changed indicator to Gemini with Google Search. The prompt contains no unchanged cards, batches at most 8 keys, and caps output at 4,096 tokens.
- Builds the 1-3 month market stance with a conservative, transparent local score. Gemini explains individual changes but cannot overwrite the overall stance with promotional language.
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

## Gemini setup

Add `GEMINI_API_KEY` as a repository secret. The optional repository variable `GEMINI_MODEL` can override the default `models/gemini-3-flash-preview`.

When the key is absent or Gemini is unavailable, report generation continues and pending events remain in the memory file for a later run. Older pending events for the same indicator are marked `superseded`, so only the newest observation is analyzed. Gemini output is published to `output/gemini_analysis.json` and `docs/api/gemini_analysis.json`; forecasts are stored as neutral scenarios, not observed facts or investment recommendations.

# vimo-VN

## Hợp đồng dữ liệu dùng chung

- Headline **Lãi suất liên ngân hàng qua đêm** là mức **đóng cửa thứ Sáu của VBMA** (`VBMA Friday close`), thống nhất với `laixuat_tienVN`.
- Không trộn mức đóng cửa VBMA với bình quân tuần SBV trong cùng chuỗi lịch sử.
- Dự báo `LOW / SINGLE_SOURCE` chỉ được hiển thị khi một nguồn API có tối thiểu 60 quan sát/90 ngày.
- Khi API chưa đủ nguồn nhưng chuỗi nội bộ có ít nhất 2 kỳ số cùng đơn vị, hệ thống có thể hiển thị `LOW / MODEL_ESTIMATE`: một kịch bản ngoại suy deterministic có biên bất định và cảnh báo rõ, không phải đồng thuận nguồn hay số liệu chính thức.
- Mỗi khoảng dự báo số có thể kèm 2–3 vùng xác suất liền nhau. Tỷ lệ dùng hỗn hợp phân bố đều–tam giác trên chính biên mô hình và được làm phẳng theo độ tin cậy, tự gộp vùng quá hẹp và luôn cộng thành 100%; đây là trọng số kịch bản chưa hiệu chuẩn, không phải xác suất thống kê thực nghiệm.
- Hai nguồn lệch nhau vượt ngưỡng phải trả `DISAGREEMENT` và giữ consensus là `null`, không lấy trung bình cho có.
- Feed dùng chung chỉ chứa facts tại `docs/api/facts.json`; Gemini nằm ở file riêng và không được ghi đè facts/dự báo.
- Dự báo nằm riêng tại `docs/api/forecasts.json`; đây là đầu ra mô hình, không được nhập vào facts.
- Nguyên nhân hiển thị chính được dựng từ biến động quan sát; bối cảnh Gemini được giữ ở trường `ai_context_unverified`, không thay thế nguyên nhân deterministic.

Auto runner for Vietnamese macro reports.

## What it does

- Runs on GitHub Actions every day at 07:30 Asia/Bangkok.
- Tracks the original 41-indicator `vimovietnam` structure.
- Fetches machine-readable daily values for USD/VND, gold, oil, DXY, US 10Y, S&P 500, and VN-Index, with Vietcap as the primary VN-Index source.
- Builds auditable 1-month/3-month projections from Yahoo Finance and Vietcap histories, optional FRED histories, and optional official EIA STEO oil forecasts.
- Falls back to an explicitly low-confidence `MODEL_ESTIMATE` when external sources are insufficient but at least two comparable observed periods are available; source disagreement is never overridden.
- Shows two or three adjacent model-probability bands for each numeric horizon, with narrow display ranges merged and percentages normalized to 100%.
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

## Forecast API setup

Yahoo Finance and Vietcap inputs require no repository secret. Two optional free
official APIs improve independent confirmation:

- `FRED_API_KEY`: official FRED histories for gold, WTI and US 10Y.
- `EIA_API_KEY`: official EIA Short-Term Energy Outlook forecast for WTI. If
  absent, the pipeline may use EIA's public shared `DEMO_KEY`, explicitly marked
  `shared_demo`; a personal free key is preferred for quota stability.

Missing keys never stop the report. Provider status is written to
`docs/api/forecasts.json`; a key, request URL containing a key, or raw provider
exception is never published. Successful source histories are cached in
`output/forecast_source_cache.json`; daily histories expire after 7 days and an
official monthly EIA forecast expires after 45 days.

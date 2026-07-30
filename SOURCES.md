# vimo-VN Sources

This project runs independently. The `vimovietnam` repository was used only as a reference map for source selection and indicator grouping.

## Core Sources

| Source | Coverage | Frequency | Status |
|---|---|---:|---|
| S&P Global PMI via VGP | PMI headline | Monthly | Parsed |
| NSO/GSO Vietnam | CPI, IIP, FDI, retail, business, tourism | Monthly/Yearly | CPI/IIP/retail/tourism parsed |
| Vietnam Customs | Trade balance, exports, imports, market/commodity split | Monthly | Monitored |
| VBMA | Interbank, government bonds, corporate bonds | Weekly snapshot | Weekly PDF parsed |
| VNBA | Banking, rates, FX, market context | Monthly | Monitored |
| Public market APIs | USD/VND, VN-Index, gold, oil, DXY, US10Y, global equity | Daily | Parsed |
| Yahoo Finance chart API | USD/VND, gold, oil, DXY, US10Y history for model input | Daily | Parsed, no key |
| Vietcap chart API | VN-Index history for model input | Daily | Parsed, no key |
| FRED observations API | Gold, WTI and US10Y independent history | Daily | Optional `FRED_API_KEY` |
| EIA STEO API | Official WTI monthly forecast | Monthly | Shared demo fallback; optional `EIA_API_KEY` preferred |

## VIP Label

`VIP` is applied to monthly or yearly macro indicators. Daily market indicators are useful context but are not tagged VIP.

VIP indicators include CPI, PMI, IIP, trade, FDI, retail, business creation/exit, tourism, credit, rates, and monthly global macro context.

## Quality Rule

The pipeline never invents observed facts. If a source is available but a reliable parser is not yet implemented, the card is marked `awaiting_official_source`. Model scenarios remain in the separate forecast feed and are never copied into facts.

For parsed monthly and weekly official indicators, a temporary network failure reuses the last verified value with a `STALE_CACHE` quality label and its original publication date. Daily market values are never reused this way.

## Forecast quality

- API observations and official forecasts are retained as separate members with
  provider, direct source URL, data date and observation count.
- One mature API history may produce a visible `LOW / SINGLE_SOURCE` scenario,
  always with a warning that it is not consensus.
- If external sources are insufficient, at least two dated observations with
  the same unit may produce a deterministic `LOW / MODEL_ESTIMATE`. It carries
  a wide uncertainty range, identifies the observed input source, and is
  explicitly not a provider forecast or source consensus.
- Numeric horizons may include `probability_bands`: two or three adjacent
  ranges derived from a confidence-weighted uniform/triangular scenario
  distribution over the published low/high envelope. Rounded duplicate or
  very narrow ranges are merged, and displayed weights are corrected to total
  exactly 100%. They are not backtested or calibrated probabilities.
- Two or more agreeing providers produce a median consensus and a range covering
  all member ranges.
- Provider disagreement beyond the per-indicator threshold produces
  `DISAGREEMENT` with `forecast_1m` and `forecast_3m` set to `null`.
- Gemini is not part of numeric forecasting. Forecasts never enter
  `docs/api/facts.json`.

## Parser Roadmap

1. NSO: expand strict regex/parser for FDI, state budget, state investment, and business counts.
2. Customs: parse official or secondary monthly trade release.
3. PMI: expand beyond the headline into sub-indices when S&P exposes stable structured data.
4. VBMA: add auction-period issuance alongside the current YTD government-bond total.
5. VNBA: extract deposit/lending rates only when a stable numeric table is available.

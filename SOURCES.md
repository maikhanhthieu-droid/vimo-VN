# vimo-VN Sources

This project runs independently. The `vimovietnam` repository was used only as a reference map for source selection and indicator grouping.

## Core Sources

| Source | Coverage | Frequency | Status |
|---|---|---:|---|
| S&P Global PMI | PMI headline and sub-indices | Monthly | Monitored |
| NSO/GSO Vietnam | CPI, IIP, FDI, retail, business, tourism | Monthly/Yearly | Partially parsed |
| Vietnam Customs | Trade balance, exports, imports, market/commodity split | Monthly | Monitored |
| VBMA | Interbank, government bonds, corporate bonds | Weekly snapshot | Monitored |
| VNBA | Banking, rates, FX, market context | Monthly | Monitored |
| Public market APIs | USD/VND, gold, oil, DXY, US10Y, global equity | Daily | Parsed |

## VIP Label

`VIP` is applied to monthly or yearly macro indicators. Daily market indicators are useful context but are not tagged VIP.

VIP indicators include CPI, PMI, IIP, trade, FDI, retail, business creation/exit, tourism, credit, rates, and monthly global macro context.

## Quality Rule

The pipeline does not invent values. If a source is available but a reliable parser is not yet implemented, the card is marked `awaiting_official_source`.

## Parser Roadmap

1. NSO: expand strict regex/parser for CPI, FDI, state budget, state investment.
2. Customs: parse official or secondary monthly trade release.
3. PMI: find current S&P press release URL and parse headline PMI.
4. VBMA: fetch weekly PDF and extract interbank, bond yields, issuance.
5. VNBA: fetch monthly PDF and extract monetary/financial tables.

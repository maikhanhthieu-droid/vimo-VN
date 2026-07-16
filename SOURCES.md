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

## VIP Label

`VIP` is applied to monthly or yearly macro indicators. Daily market indicators are useful context but are not tagged VIP.

VIP indicators include CPI, PMI, IIP, trade, FDI, retail, business creation/exit, tourism, credit, rates, and monthly global macro context.

## Quality Rule

The pipeline does not invent values. If a source is available but a reliable parser is not yet implemented, the card is marked `awaiting_official_source`.

## Parser Roadmap

1. NSO: expand strict regex/parser for FDI, state budget, state investment, and business counts.
2. Customs: parse official or secondary monthly trade release.
3. PMI: expand beyond the headline into sub-indices when S&P exposes stable structured data.
4. VBMA: add auction-period issuance alongside the current YTD government-bond total.
5. VNBA: extract deposit/lending rates only when a stable numeric table is available.

from __future__ import annotations

import csv
import html
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"
DOCS_DIR = ROOT / "docs"
HISTORY_FILE = OUTPUT_DIR / "history.json"


@dataclass(frozen=True)
class IndicatorSpec:
    key: str
    name_vi: str
    group: str
    unit: str
    source_primary: str
    definition: str
    why_it_matters: str
    priority: int
    source_url: str


GROUPS = {
    "real_economy": "Kinh tế thực",
    "financial": "Tiền tệ - tài chính",
    "sector": "Ngành - cấu phần",
    "global": "Bối cảnh toàn cầu",
}


SPECS: list[IndicatorSpec] = [
    IndicatorSpec("cpi", "CPI", "real_economy", "% YoY", "NSO", "Chỉ số giá tiêu dùng so với cùng kỳ.", "Lạm phát là biến số nền cho lãi suất, tỷ giá và sức mua.", 1, "https://www.nso.gov.vn/"),
    IndicatorSpec("pmi_manufacturing", "PMI sản xuất", "real_economy", "điểm", "S&P Global", "PMI sản xuất Việt Nam.", "PMI là tín hiệu sớm của chu kỳ đơn hàng và sản xuất.", 2, "https://www.pmi.spglobal.com/"),
    IndicatorSpec("iip", "IIP", "real_economy", "% YoY", "NSO", "Chỉ số sản xuất công nghiệp.", "IIP cho thấy nhịp sản xuất chính thức.", 3, "https://www.nso.gov.vn/"),
    IndicatorSpec("trade_balance", "Cán cân thương mại", "real_economy", "tỷ USD", "Customs", "Xuất khẩu trừ nhập khẩu.", "Cán cân thương mại tác động tới tỷ giá và thanh khoản ngoại tệ.", 4, "https://www.customs.gov.vn/"),
    IndicatorSpec("exports", "Xuất khẩu", "real_economy", "tỷ USD", "Customs", "Kim ngạch xuất khẩu.", "Xuất khẩu là động lực tăng trưởng và dòng USD.", 5, "https://www.customs.gov.vn/"),
    IndicatorSpec("imports", "Nhập khẩu", "real_economy", "tỷ USD", "Customs", "Kim ngạch nhập khẩu.", "Nhập khẩu phản ánh nhu cầu nguyên liệu và đầu tư.", 6, "https://www.customs.gov.vn/"),
    IndicatorSpec("fdi_disbursed", "FDI giải ngân", "real_economy", "tỷ USD", "NSO", "Vốn FDI thực hiện.", "FDI giải ngân cho thấy vốn thật vào nền kinh tế.", 7, "https://www.nso.gov.vn/"),
    IndicatorSpec("fdi_registered", "FDI đăng ký", "real_economy", "tỷ USD", "NSO", "Vốn FDI đăng ký mới và điều chỉnh.", "FDI đăng ký phản ánh kỳ vọng đầu tư tương lai.", 8, "https://www.nso.gov.vn/"),
    IndicatorSpec("retail", "Bán lẻ hàng hóa và dịch vụ", "real_economy", "% YoY", "NSO", "Tổng mức bán lẻ hàng hóa và doanh thu dịch vụ tiêu dùng.", "Bán lẻ là nhiệt kế tiêu dùng nội địa.", 9, "https://www.nso.gov.vn/"),
    IndicatorSpec("business_new", "Doanh nghiệp thành lập mới", "real_economy", "doanh nghiệp", "NSO", "Số doanh nghiệp đăng ký thành lập mới.", "Số doanh nghiệp mới phản ánh khẩu vị mở rộng kinh doanh.", 10, "https://www.nso.gov.vn/"),
    IndicatorSpec("business_exited", "Doanh nghiệp rút lui", "real_economy", "doanh nghiệp", "NSO", "Số doanh nghiệp tạm ngừng hoặc giải thể.", "Doanh nghiệp rút lui đo áp lực vận hành.", 11, "https://www.nso.gov.vn/"),
    IndicatorSpec("international_visitors", "Khách quốc tế", "real_economy", "triệu lượt", "NSO", "Lượt khách quốc tế đến Việt Nam.", "Du lịch là cấu phần quan trọng của dịch vụ và ngoại tệ.", 12, "https://www.nso.gov.vn/"),
    IndicatorSpec("state_investment", "Đầu tư công", "real_economy", "% kế hoạch", "NSO", "Vốn đầu tư thực hiện từ ngân sách.", "Đầu tư công là lực kéo tài khóa.", 13, "https://www.nso.gov.vn/"),
    IndicatorSpec("state_budget", "Ngân sách nhà nước", "real_economy", "nghìn tỷ VND", "MOF/NSO", "Thu chi ngân sách nhà nước.", "Ngân sách cho thấy sức khỏe tài khóa.", 14, "https://www.mof.gov.vn/"),
    IndicatorSpec("interbank_rate", "Lãi suất liên ngân hàng qua đêm", "financial", "%", "VBMA/VNBA", "Lãi suất VND liên ngân hàng kỳ hạn qua đêm.", "Lãi suất liên ngân hàng đo thanh khoản hệ thống.", 1, "https://vbma.org.vn/"),
    IndicatorSpec("fx_central_rate", "Tỷ giá trung tâm", "financial", "VND/USD", "SBV", "Tỷ giá trung tâm USD/VND.", "Tỷ giá trung tâm là neo chính sách ngoại hối.", 2, "https://www.sbv.gov.vn/"),
    IndicatorSpec("fx_market_usd_vnd", "USD/VND thị trường", "financial", "VND/USD", "Open ER API", "Tỷ giá USD sang VND từ API thị trường.", "USD/VND cho thấy áp lực ngoại hối cập nhật hằng ngày.", 3, "https://open.er-api.com/"),
    IndicatorSpec("stock_market", "VN-Index", "financial", "điểm", "Stooq/Yahoo", "Chỉ số VN-Index.", "VN-Index là thước đo sentiment tài sản rủi ro nội địa.", 4, "https://stooq.com/"),
    IndicatorSpec("govt_bond_yield", "Lợi suất TPCP Việt Nam 10Y", "financial", "%", "VBMA", "Lợi suất trái phiếu chính phủ kỳ hạn 10 năm.", "TPCP 10Y là benchmark chi phí vốn dài hạn.", 5, "https://vbma.org.vn/"),
    IndicatorSpec("gold_world", "Vàng thế giới", "financial", "USD/oz", "Metals.live", "Giá vàng giao ngay quốc tế.", "Vàng phản ánh phòng thủ rủi ro và kỳ vọng lãi suất.", 6, "https://api.metals.live/"),
    IndicatorSpec("credit", "Tăng trưởng tín dụng", "financial", "% YTD", "SBV/VNBA", "Tăng trưởng tín dụng toàn hệ thống.", "Tín dụng là dòng vốn chính của nền kinh tế.", 7, "https://www.sbv.gov.vn/"),
    IndicatorSpec("deposit_rate", "Lãi suất huy động", "financial", "%", "VNBA", "Mặt bằng lãi suất huy động.", "Huy động đo chi phí vốn của ngân hàng.", 8, "https://vnba.org.vn/"),
    IndicatorSpec("lending_rate", "Lãi suất cho vay", "financial", "%", "VNBA", "Mặt bằng lãi suất cho vay.", "Lãi vay là chi phí vốn của doanh nghiệp và hộ gia đình.", 9, "https://vnba.org.vn/"),
    IndicatorSpec("dxy", "DXY", "financial", "điểm", "Stooq", "US Dollar Index.", "DXY đo áp lực USD toàn cầu lên tỷ giá.", 10, "https://stooq.com/"),
    IndicatorSpec("corporate_bond_issuance", "Phát hành TPDN", "financial", "nghìn tỷ VND", "VBMA", "Giá trị phát hành trái phiếu doanh nghiệp.", "TPDN phản ánh kênh vốn ngoài ngân hàng.", 11, "https://vbma.org.vn/"),
    IndicatorSpec("govt_bond_issuance", "Phát hành TPCP", "financial", "nghìn tỷ VND", "VBMA", "Giá trị phát hành trái phiếu chính phủ.", "TPCP đo huy động ngân sách và cung trái phiếu.", 12, "https://vbma.org.vn/"),
    IndicatorSpec("pmi_sub_indices", "PMI cấu phần", "sector", "điểm", "S&P Global", "Output, new orders, employment, input costs.", "Cấu phần PMI kể rõ sức khỏe sản xuất bên trong.", 1, "https://www.pmi.spglobal.com/"),
    IndicatorSpec("trade_by_sector", "XNK theo khu vực", "sector", "tỷ USD", "Customs", "XNK khu vực FDI và trong nước.", "Phân rã XNK giúp nhìn động lực FDI/nội địa.", 2, "https://www.customs.gov.vn/"),
    IndicatorSpec("trade_by_market", "XNK theo thị trường", "sector", "tỷ USD", "Customs", "Top thị trường xuất nhập khẩu.", "Thị trường lớn cho thấy rủi ro tập trung thương mại.", 3, "https://www.customs.gov.vn/"),
    IndicatorSpec("trade_by_commodity", "XNK theo mặt hàng", "sector", "tỷ USD", "Customs", "Nhóm hàng xuất nhập khẩu chủ lực.", "Mặt hàng chủ lực cho thấy chu kỳ ngành.", 4, "https://www.customs.gov.vn/"),
    IndicatorSpec("fdi_by_sector", "FDI theo ngành", "sector", "tỷ USD", "NSO", "FDI đăng ký theo ngành.", "FDI theo ngành cho thấy vốn đang chọn khu vực nào.", 5, "https://www.nso.gov.vn/"),
    IndicatorSpec("agriculture_snapshot", "Nông nghiệp", "sector", "% YoY", "NSO", "Tổng quan khu vực nông lâm thủy sản.", "Nông nghiệp ảnh hưởng lương thực, xuất khẩu và CPI.", 6, "https://www.nso.gov.vn/"),
    IndicatorSpec("industrial_exports", "Xuất khẩu công nghiệp", "sector", "tỷ USD", "Customs", "Xuất khẩu nhóm công nghiệp chế biến.", "Nhóm công nghiệp là lõi xuất khẩu.", 7, "https://www.customs.gov.vn/"),
    IndicatorSpec("energy_snapshot", "Năng lượng trong nước", "sector", "% YoY", "NSO/MOIT", "Sản xuất điện, than, dầu khí.", "Năng lượng là nền cho sản xuất và giá đầu vào.", 8, "https://moit.gov.vn/"),
    IndicatorSpec("real_estate_context", "Bất động sản", "sector", "ghi nhận", "NSO/VNBA", "Tín hiệu BĐS, xây dựng và TPDN liên quan.", "BĐS nối giữa tín dụng, trái phiếu và tài sản hộ gia đình.", 9, "https://vnba.org.vn/"),
    IndicatorSpec("banking_liquidity", "Thanh khoản ngân hàng", "sector", "ghi nhận", "VNBA/VBMA", "Tổng hợp LNH, OMO, tín phiếu.", "Thanh khoản ngân hàng dẫn dắt lãi suất ngắn hạn.", 10, "https://vnba.org.vn/"),
    IndicatorSpec("fed_policy", "Fed policy rate", "global", "%", "FRED", "Biên trên lãi suất mục tiêu Fed Funds.", "Fed là neo lãi suất USD và dòng vốn toàn cầu.", 1, "https://fred.stlouisfed.org/"),
    IndicatorSpec("us_economy", "Kinh tế Mỹ", "global", "ghi nhận", "FRED/BEA", "Tăng trưởng, lạm phát, lao động Mỹ.", "Mỹ là thị trường xuất khẩu lớn và neo USD.", 2, "https://fred.stlouisfed.org/"),
    IndicatorSpec("oil_prices", "Giá dầu WTI", "global", "USD/thùng", "Stooq", "Giá dầu WTI.", "Dầu ảnh hưởng chi phí năng lượng và lạm phát.", 3, "https://stooq.com/"),
    IndicatorSpec("geopolitical_risk", "Rủi ro địa chính trị", "global", "ghi nhận", "News", "Các điểm nóng địa chính trị.", "Cú sốc địa chính trị ảnh hưởng dầu, logistics và USD.", 4, "https://www.reuters.com/"),
    IndicatorSpec("us_10y_yield", "US 10Y yield", "global", "%", "Stooq", "Lợi suất trái phiếu chính phủ Mỹ 10 năm.", "US10Y là benchmark định giá tài sản toàn cầu.", 5, "https://stooq.com/"),
    IndicatorSpec("ecb_eurozone", "ECB/Eurozone", "global", "ghi nhận", "ECB", "Chính sách ECB và kinh tế Eurozone.", "EU là đối tác thương mại và nguồn FDI quan trọng.", 6, "https://www.ecb.europa.eu/"),
    IndicatorSpec("boj_japan", "BOJ/Japan", "global", "ghi nhận", "BOJ", "Chính sách BOJ và kinh tế Nhật.", "Nhật là nguồn FDI và đối tác tài chính lớn.", 7, "https://www.boj.or.jp/"),
    IndicatorSpec("china_economy", "Kinh tế Trung Quốc", "global", "ghi nhận", "NBS China", "PMI, tăng trưởng và thương mại Trung Quốc.", "Trung Quốc là đối tác thương mại lớn nhất của Việt Nam.", 8, "https://www.stats.gov.cn/"),
    IndicatorSpec("policy_actions_vn", "Chính sách Việt Nam", "global", "ghi nhận", "Government/SBV", "Chỉ đạo điều hành, thông tư, quyết định mới.", "Chính sách nội địa là biến số trực tiếp cho thị trường.", 9, "https://chinhphu.vn/"),
    IndicatorSpec("global_equities", "Chứng khoán toàn cầu", "global", "điểm", "Stooq", "S&P 500 hoặc chỉ số chứng khoán lớn.", "Thị trường toàn cầu ảnh hưởng khẩu vị rủi ro.", 10, "https://stooq.com/"),
]


def fetch_text(url: str, timeout: int = 20) -> str:
    request = Request(url, headers={"User-Agent": "vimo-VN/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str) -> Any:
    return json.loads(fetch_text(url))


def latest_stooq(symbol: str) -> tuple[float | None, str | None]:
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
    try:
        rows = list(csv.DictReader(fetch_text(url).splitlines()))
        if not rows:
            return None, None
        row = rows[0]
        close = row.get("Close")
        if not close or close == "N/D":
            return None, None
        return float(close), f"{row.get('Date', '')} {row.get('Time', '')}".strip()
    except (ValueError, URLError, TimeoutError, OSError):
        return None, None


def latest_yahoo(symbol: str) -> tuple[float | None, str | None]:
    encoded = symbol.replace("^", "%5E")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d"
    try:
        data = fetch_json(url)
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None, None
        quote = result[0].get("indicators", {}).get("quote", [{}])[0]
        closes = quote.get("close", [])
        timestamps = result[0].get("timestamp", [])
        for idx in range(len(closes) - 1, -1, -1):
            close = closes[idx]
            if isinstance(close, (int, float)):
                stamp = datetime.fromtimestamp(timestamps[idx], tz=timezone.utc).date().isoformat() if idx < len(timestamps) else None
                return float(close), stamp
    except (ValueError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None, None
    return None, None


def live_values() -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}

    try:
        rates = fetch_json("https://open.er-api.com/v6/latest/USD")
        vnd = rates.get("rates", {}).get("VND")
        if isinstance(vnd, (int, float)):
            values["fx_market_usd_vnd"] = {
                "value": round(float(vnd), 2),
                "as_of": rates.get("time_last_update_utc"),
                "source_quality": "AUTO",
                "source_live": "Open ER API",
            }
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        pass

    for key, symbol in {
        "dxy": "dx.f",
        "oil_prices": "cl.f",
        "us_10y_yield": "10usy.b",
        "global_equities": "^spx",
    }.items():
        value, as_of = latest_stooq(symbol)
        if value is not None:
            if key == "us_10y_yield" and value < 1:
                value = value * 10
            values[key] = {"value": round(value, 4), "as_of": as_of, "source_quality": "AUTO", "source_live": "Stooq"}

    for key, symbol in {"gold_world": "xauusd", "fed_policy": "fedfunds"}.items():
        value, as_of = latest_stooq(symbol)
        if value is not None:
            values[key] = {"value": round(value, 4), "as_of": as_of, "source_quality": "AUTO", "source_live": "Stooq"}

    for key, symbol in {
        "gold_world": "GC=F",
        "oil_prices": "CL=F",
        "dxy": "DX-Y.NYB",
        "us_10y_yield": "^TNX",
        "global_equities": "^GSPC",
        "stock_market": "^VNINDEX",
    }.items():
        if key in values:
            continue
        value, as_of = latest_yahoo(symbol)
        if value is not None:
            if key == "us_10y_yield" and value > 20:
                value = value / 10
            values[key] = {"value": round(value, 4), "as_of": as_of, "source_quality": "AUTO", "source_live": "Yahoo Finance"}

    return values


def signal_for(key: str, value: Any) -> str:
    if value is None:
        return "PENDING"
    if key == "pmi_manufacturing":
        return "GREEN" if float(value) >= 50 else "YELLOW"
    if key == "cpi":
        return "GREEN" if float(value) <= 4.5 else "YELLOW"
    if key in {"dxy", "us_10y_yield", "oil_prices"}:
        return "YELLOW"
    return "GREEN"


def build_cards(now: datetime) -> list[dict[str, Any]]:
    values = live_values()
    cards = []
    for spec in SPECS:
        live = values.get(spec.key, {})
        value = live.get("value")
        available = value is not None
        card = {
            "key": spec.key,
            "name_vi": spec.name_vi,
            "group": spec.group,
            "group_name": GROUPS[spec.group],
            "priority": spec.priority,
            "definition": spec.definition,
            "why_it_matters": spec.why_it_matters,
            "value": value,
            "unit": spec.unit,
            "status": "available" if available else "awaiting_official_source",
            "signal": signal_for(spec.key, value),
            "source_primary": live.get("source_live", spec.source_primary),
            "source_url": spec.source_url,
            "source_quality": live.get("source_quality", "SOURCE_MONITOR"),
            "as_of": live.get("as_of", now.date().isoformat()),
            "narrative": (
                f"{spec.name_vi} hiện có dữ liệu tự động từ {spec.source_primary}. "
                f"Giá trị mới nhất là {value} {spec.unit}, dùng để theo dõi {spec.why_it_matters.lower()}"
                if available
                else f"{spec.name_vi} đang chờ bản công bố chính thức từ {spec.source_primary}. "
                "Dashboard vẫn giữ card theo dõi nguồn, nhưng không dựng số thay thế khi chưa có dữ liệu chắc chắn."
            ),
        }
        cards.append(card)
    return sorted(cards, key=lambda c: (c["group"], c["priority"]))


def update_history(cards: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            history = {"series": {}}
    else:
        history = {"series": {}}

    series = history.setdefault("series", {})
    stamp = now.isoformat()
    for card in cards:
        if card["value"] is None:
            continue
        points = series.setdefault(card["key"], [])
        if not points or points[-1].get("value") != card["value"]:
            points.append({"date": stamp, "value": card["value"], "unit": card["unit"]})
        series[card["key"]] = points[-60:]
    return history


def render_html(payload: dict[str, Any]) -> str:
    cards = payload["cards"]
    available = payload["coverage"]["available_cards"]
    total = payload["coverage"]["total_cards"]
    generated = html.escape(payload["generated_at_bkk"])
    tabs = []
    sections = []
    for group_key, group_name in GROUPS.items():
        active = " active" if not tabs else ""
        group_cards = [c for c in cards if c["group"] == group_key]
        tabs.append(f'<button class="tab{active}" data-tab="{group_key}">{html.escape(group_name)} <span>{len(group_cards)}</span></button>')
        card_html = []
        for card in group_cards:
            value = "Chờ nguồn" if card["value"] is None else f'{card["value"]} {html.escape(card["unit"])}'
            status = "pending" if card["value"] is None else "ok"
            card_html.append(
                f"""
        <article class="card {status}">
          <div class="card-top">
            <h3>{html.escape(card["name_vi"])}</h3>
            <span>{html.escape(card["source_quality"])}</span>
          </div>
          <p class="value">{value}</p>
          <p>{html.escape(card["narrative"])}</p>
          <a href="{html.escape(card["source_url"])}">Nguồn: {html.escape(card["source_primary"])}</a>
        </article>"""
            )
        sections.append(f'<section id="{group_key}" class="panel{active}">' + "\n".join(card_html) + "</section>")

    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>vimo-VN Macro Monitor</title>
  <style>
    :root {{ color-scheme: light; --ink:#172033; --muted:#667085; --line:#d8dee8; --ok:#0f8b6f; --warn:#b7791f; --bg:#f6f8fb; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family: Arial, sans-serif; background:var(--bg); color:var(--ink); }}
    header {{ padding:28px 20px 18px; background:#ffffff; border-bottom:1px solid var(--line); }}
    .wrap {{ max-width:1180px; margin:0 auto; }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    .meta {{ color:var(--muted); margin:0; }}
    .summary {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:18px; }}
    .metric {{ background:#f9fbfd; border:1px solid var(--line); border-radius:8px; padding:14px; }}
    .metric strong {{ display:block; font-size:24px; }}
    nav {{ display:flex; gap:8px; flex-wrap:wrap; padding:16px 20px; background:#fff; border-bottom:1px solid var(--line); }}
    .tab {{ border:1px solid var(--line); background:#fff; color:var(--ink); border-radius:6px; padding:9px 12px; cursor:pointer; }}
    .tab.active {{ border-color:#2563eb; color:#1d4ed8; }}
    .tab span {{ color:var(--muted); }}
    main {{ padding:20px; }}
    .panel {{ display:none; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; }}
    .panel.active {{ display:grid; }}
    .card {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:16px; min-height:210px; }}
    .card.ok {{ border-top:4px solid var(--ok); }}
    .card.pending {{ border-top:4px solid var(--warn); }}
    .card-top {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }}
    h3 {{ margin:0; font-size:16px; }}
    .card-top span {{ font-size:12px; color:var(--muted); white-space:nowrap; }}
    .value {{ font-size:24px; font-weight:700; margin:16px 0 10px; }}
    p {{ line-height:1.45; }}
    a {{ color:#1d4ed8; text-decoration:none; font-size:13px; }}
    @media (max-width:720px) {{ .summary {{ grid-template-columns:1fr; }} h1 {{ font-size:23px; }} }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>vimo-VN Macro Monitor</h1>
      <p class="meta">Cập nhật: {generated}. Tự động chạy mỗi sáng qua GitHub Actions.</p>
      <div class="summary">
        <div class="metric"><strong>{available}/{total}</strong><span>card có dữ liệu tự động</span></div>
        <div class="metric"><strong>{payload["coverage"]["source_count"]}</strong><span>nguồn theo dõi</span></div>
        <div class="metric"><strong>{html.escape(payload["status"])}</strong><span>trạng thái pipeline</span></div>
      </div>
    </div>
  </header>
  <nav class="wrap">{"".join(tabs)}</nav>
  <main class="wrap">{"".join(sections)}</main>
  <script>
    const tabs = document.querySelectorAll('.tab');
    const panels = document.querySelectorAll('.panel');
    tabs.forEach(tab => tab.addEventListener('click', () => {{
      tabs.forEach(t => t.classList.remove('active'));
      panels.forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById(tab.dataset.tab).classList.add('active');
    }}));
  </script>
</body>
</html>
"""


def main() -> None:
    now = datetime.now(timezone.utc)
    OUTPUT_DIR.mkdir(exist_ok=True)
    DOCS_DIR.mkdir(exist_ok=True)
    cards = build_cards(now)
    history = update_history(cards, now)
    available = sum(1 for card in cards if card["value"] is not None)
    payload = {
        "project": "vimo-VN",
        "status": "ok",
        "generated_at_utc": now.isoformat(),
        "generated_at_bkk": now.astimezone(ZoneInfo("Asia/Bangkok")).isoformat(),
        "coverage": {
            "available_cards": available,
            "total_cards": len(cards),
            "source_count": len({card["source_primary"] for card in cards}),
            "note": "Macro cards without a reliable machine-readable source are marked awaiting_official_source instead of using guessed values.",
        },
        "cards": cards,
    }

    (OUTPUT_DIR / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DOCS_DIR / "index.html").write_text(render_html(payload), encoding="utf-8")

    summary = f"vimo-VN updated: {available}/{len(cards)} cards have automatic values."
    (OUTPUT_DIR / "telegram_summary.txt").write_text(summary + "\n", encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"vimo-VN pipeline failed: {exc}", file=sys.stderr)
        raise

from __future__ import annotations

import csv
import html
import json
import os
import re
import ssl
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import RemoteDisconnected
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"
DOCS_DIR = ROOT / "docs"
DOCS_API_DIR = DOCS_DIR / "api"
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

VIP_FREQUENCIES = {
    "cpi": "monthly",
    "pmi_manufacturing": "monthly",
    "iip": "monthly",
    "trade_balance": "monthly",
    "exports": "monthly",
    "imports": "monthly",
    "fdi_disbursed": "monthly",
    "fdi_registered": "monthly",
    "retail": "monthly",
    "business_new": "monthly",
    "business_exited": "monthly",
    "international_visitors": "monthly",
    "state_investment": "monthly",
    "state_budget": "monthly",
    "interbank_rate": "weekly_snapshot",
    "fx_central_rate": "daily",
    "fx_market_usd_vnd": "daily",
    "stock_market": "daily",
    "govt_bond_yield": "weekly_snapshot",
    "gold_world": "daily",
    "credit": "monthly",
    "deposit_rate": "monthly",
    "lending_rate": "monthly",
    "dxy": "daily",
    "corporate_bond_issuance": "monthly",
    "govt_bond_issuance": "weekly_snapshot",
    "pmi_sub_indices": "monthly",
    "trade_by_sector": "monthly",
    "trade_by_market": "monthly",
    "trade_by_commodity": "monthly",
    "fdi_by_sector": "monthly",
    "agriculture_snapshot": "monthly",
    "industrial_exports": "monthly",
    "energy_snapshot": "monthly",
    "real_estate_context": "monthly",
    "banking_liquidity": "weekly_snapshot",
    "fed_policy": "meeting",
    "us_economy": "monthly",
    "oil_prices": "daily",
    "geopolitical_risk": "daily_monitor",
    "us_10y_yield": "daily",
    "ecb_eurozone": "meeting",
    "boj_japan": "meeting",
    "china_economy": "monthly",
    "policy_actions_vn": "event",
    "global_equities": "daily",
}

SOURCE_REGISTRY = {
    "pmi": {"name": "S&P Global PMI via VGP", "url": "https://en.baochinhphu.vn/search.htm?keywords=PMI", "role": "PMI sản xuất do S&P Global công bố", "release_lag": "M+1 ngày 1-3"},
    "nso": {"name": "NSO/GSO Việt Nam", "url": "https://www.nso.gov.vn/", "role": "CPI, IIP, FDI, bán lẻ, doanh nghiệp, du lịch", "release_lag": "M+1 ngày 3-7"},
    "customs": {"name": "Tổng cục Hải quan", "url": "https://www.customs.gov.vn/", "role": "Xuất nhập khẩu chính thức", "release_lag": "M+1 ngày 10-15"},
    "vbma": {"name": "VBMA", "url": "https://vbma.org.vn/vi/reports/weekly", "role": "Liên ngân hàng, TPCP, TPDN tuần", "release_lag": "hàng tuần"},
    "vnba": {"name": "VNBA", "url": "https://vnba.org.vn/", "role": "Bản tin tiền tệ tài chính tháng", "release_lag": "M+1 ngày 11-13"},
    "market": {"name": "Public market APIs / Vietcap", "url": "https://trading.vietcap.com.vn/", "role": "VN-Index, tỷ giá, vàng, dầu, DXY, US10Y", "release_lag": "daily"},
}

HISTORY_IMPORTANT_KEYS = {
    "cpi",
    "pmi_manufacturing",
    "iip",
    "trade_balance",
    "exports",
    "imports",
    "fdi_disbursed",
    "retail",
    "interbank_rate",
    "fx_market_usd_vnd",
    "fx_central_rate",
    "stock_market",
    "govt_bond_yield",
    "gold_world",
    "credit",
    "dxy",
    "oil_prices",
    "us_10y_yield",
    "global_equities",
}

HISTORY_LIMIT = 100
TLS_FALLBACK_HOSTS = {"nso.gov.vn", "www.nso.gov.vn", "vbma.org.vn", "www.vbma.org.vn"}
HOST_REFERERS = {
    "nso.gov.vn": "https://www.nso.gov.vn/",
    "www.nso.gov.vn": "https://www.nso.gov.vn/",
    "vbma.org.vn": "https://vbma.org.vn/",
    "www.vbma.org.vn": "https://vbma.org.vn/",
}
NSO_API_URL = (
    "https://www.nso.gov.vn/wp-json/wp/v2/posts?"
    "search=bao%20cao%20tinh%20hinh%20kinh%20te%20xa%20hoi%20thang&per_page=20"
)
NSO_FEED_URL = "https://www.nso.gov.vn/feed/"
PMI_SEARCH_URL = "https://en.baochinhphu.vn/search.htm?keywords=PMI"
VBMA_WEEKLY_URL = "https://vbma.org.vn/vi/reports/weekly"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Connection": "close",
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


def request_headers(url: str) -> dict[str, str]:
    headers = dict(DEFAULT_HEADERS)
    referer = HOST_REFERERS.get(urlparse(url).hostname or "")
    if referer:
        headers["Referer"] = referer
    return headers


def should_retry_without_tls_verification(url: str, exc: BaseException) -> bool:
    host = urlparse(url).hostname
    if host not in TLS_FALLBACK_HOSTS:
        return False
    reason = getattr(exc, "reason", None)
    return (
        isinstance(exc, ssl.SSLCertVerificationError)
        or isinstance(reason, ssl.SSLCertVerificationError)
        or "CERTIFICATE_VERIFY_FAILED" in str(exc)
    )


def is_retryable_network_error(exc: BaseException) -> bool:
    if isinstance(exc, HTTPError):
        return 500 <= exc.code < 600
    reason = getattr(exc, "reason", None)
    if isinstance(reason, (TimeoutError, ConnectionResetError, RemoteDisconnected)):
        return True
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "connection reset",
            "remote end closed connection",
            "temporarily unavailable",
            "timed out",
        )
    )


def read_response_bytes(request: Request, timeout: int, context: ssl.SSLContext | None = None) -> bytes:
    kwargs: dict[str, Any] = {"timeout": timeout}
    if context is not None:
        kwargs["context"] = context
    with urlopen(request, **kwargs) as response:
        return response.read()


def fetch_request_bytes(request: Request, timeout: int = 20, retries: int = 3) -> bytes:
    url = request.full_url
    context: ssl.SSLContext | None = None
    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return read_response_bytes(request, timeout, context)
        except (HTTPError, URLError, ssl.SSLError, TimeoutError, OSError, RemoteDisconnected) as exc:
            last_error = exc
            if context is None and should_retry_without_tls_verification(url, exc):
                context = ssl._create_unverified_context()
                continue
            if attempt >= retries or not is_retryable_network_error(exc):
                raise
            time.sleep(min(2**attempt, 4))
    assert last_error is not None
    raise last_error


def fetch_request_text(request: Request, timeout: int = 20, retries: int = 3) -> str:
    return fetch_request_bytes(request, timeout, retries).decode("utf-8", errors="replace")


def fetch_text(url: str, timeout: int = 20, retries: int = 3) -> str:
    request = Request(url, headers=request_headers(url))
    return fetch_request_text(request, timeout, retries)


def fetch_bytes(url: str, timeout: int = 30, retries: int = 3) -> bytes:
    request = Request(url, headers=request_headers(url))
    return fetch_request_bytes(request, timeout, retries)


def fetch_json(url: str) -> Any:
    return json.loads(fetch_text(url))

def strip_tags(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_vietnamese_number(raw: str) -> float:
    cleaned = re.sub(r"[^0-9,.\-]", "", raw.strip().replace("\xa0", " "))
    if "." in cleaned and "," in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        decimal_digits = len(cleaned) - cleaned.rfind(",") - 1
        cleaned = cleaned.replace(",", ".") if decimal_digits <= 2 else cleaned.replace(",", "")
    elif "." in cleaned:
        decimal_digits = len(cleaned) - cleaned.rfind(".") - 1
        if decimal_digits == 3:
            cleaned = cleaned.replace(".", "")
    return float(cleaned)


def first_percent_after(text: str, markers: list[str]) -> float | None:
    lower = text.lower()
    for marker in markers:
        pos = lower.find(marker.lower())
        if pos == -1:
            continue
        window = text[pos : pos + 650]
        match = re.search(r"(\d{1,3}(?:[,.]\d{1,3})?)\s*%", window)
        if match:
            try:
                return parse_vietnamese_number(match.group(1))
            except ValueError:
                return None
    return None


def first_percent_with_context(text: str, markers: list[str], contexts: list[str]) -> float | None:
    lower = text.lower()
    for marker in markers:
        start = lower.find(marker.lower())
        if start == -1:
            continue
        area = text[start : start + 1800]
        area_l = area.lower()
        for context in contexts:
            pos = area_l.find(context.lower())
            if pos == -1:
                continue
            window = area[max(0, pos - 220) : pos + 280]
            matches = re.findall(r"(\d{1,3}(?:[,.]\d{1,3})?)\s*%", window)
            if matches:
                try:
                    return parse_vietnamese_number(matches[-1])
                except ValueError:
                    return None
    return None


def parse_signed_percent(value_raw: str, direction: str | None = None) -> float:
    value = parse_vietnamese_number(value_raw)
    if direction and direction.lower() == "giảm":
        return -value
    return value


def first_cpi_yoy(text: str) -> float | None:
    lower = text.lower()
    for marker in ["chỉ số giá tiêu dùng", "cpi"]:
        start = lower.find(marker)
        if start == -1:
            continue
        area = text[start : start + 2200]
        sentences = re.split(r"(?<=[.!?])\s+", area)
        for sentence in sentences[:5]:
            sentence_l = sentence.lower()
            if "cùng kỳ" not in sentence_l:
                continue
            if re.search(r"\b(bình quân|tính chung)\b", sentence_l[:120]):
                continue
            patterns = [
                r"(?P<direction>tăng|giảm)\s*(?P<value>\d{1,3}(?:[,.]\d{1,3})?)\s*%\s*so với cùng kỳ(?: năm trước)?",
                r"so với cùng kỳ(?: năm trước)?\s*(?P<direction>tăng|giảm)?\s*(?P<value>\d{1,3}(?:[,.]\d{1,3})?)\s*%",
            ]
            for pattern in patterns:
                match = re.search(pattern, sentence, flags=re.I)
                if match:
                    try:
                        return parse_signed_percent(match.group("value"), match.groupdict().get("direction"))
                    except ValueError:
                        return None
    return None


def first_number_after(text: str, markers: list[str]) -> float | None:
    lower = text.lower()
    for marker in markers:
        pos = lower.find(marker.lower())
        if pos == -1:
            continue
        window = text[pos : pos + 700]
        match = re.search(r"(\d{1,3}(?:[,.]\d{1,3})?)", window)
        if match:
            try:
                return parse_vietnamese_number(match.group(1))
            except ValueError:
                return None
    return None


def fetch_nso_posts() -> list[dict[str, Any]]:
    try:
        posts = fetch_json(NSO_API_URL)
        if isinstance(posts, list):
            return posts
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        pass

    try:
        root = ET.fromstring(fetch_text(NSO_FEED_URL))
    except (HTTPError, URLError, TimeoutError, OSError, ET.ParseError):
        return []

    content_tag = "{http://purl.org/rss/1.0/modules/content/}encoded"
    posts = []
    for item in root.findall(".//item"):
        title = item.findtext("title", default="")
        link = item.findtext("link", default="")
        content = item.findtext(content_tag) or item.findtext("description", default="")
        posts.append(
            {
                "title": {"rendered": title},
                "content": {"rendered": content},
                "date": item.findtext("pubDate"),
                "link": link,
            }
        )
    return posts


def fetch_nso_snapshot() -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    posts = fetch_nso_posts()

    selected = None
    for post in posts:
        title = strip_tags(post.get("title", {}).get("rendered", ""))
        title_l = title.lower()
        if "báo cáo tình hình kinh tế" in title_l and "tháng" in title_l:
            selected = post
            break
    if not selected:
        return values

    text = strip_tags(selected.get("content", {}).get("rendered", ""))
    as_of = selected.get("date_gmt") or selected.get("date")
    link = selected.get("link")
    candidates = {
        "cpi": first_cpi_yoy(text),
        "iip": first_percent_with_context(text, ["Chỉ số sản xuất công nghiệp", "IIP"], ["so với cùng kỳ", "tăng"]),
        "retail": first_percent_with_context(text, ["Tổng mức bán lẻ", "doanh thu dịch vụ tiêu dùng"], ["so với cùng kỳ", "tăng"]),
        "international_visitors": first_number_after(text, ["khách quốc tế đến Việt Nam", "khách quốc tế"]),
    }
    for key, value in candidates.items():
        if value is None:
            continue
        values[key] = {
            "value": round(value, 4),
            "as_of": as_of,
            "source_quality": "AUTO_NSO_PARSE",
            "source_live": "NSO",
            "source_url": link,
        }
    return values


def fetch_pmi_snapshot() -> dict[str, dict[str, Any]]:
    try:
        page = fetch_text(PMI_SEARCH_URL)
    except (HTTPError, URLError, TimeoutError, OSError):
        return {}

    article_pattern = re.compile(
        r'<a[^>]+class="box-stream-link-with-avatar"[^>]+href="(?P<link>[^"]+)"[^>]+title="(?P<title>[^"]*PMI[^"]*)"[^>]*>.*?'
        r'<p[^>]+class="box-stream-sapo"[^>]*>(?P<summary>.*?)</p>',
        flags=re.I | re.S,
    )
    for article in article_pattern.finditer(page):
        text = strip_tags(f'{article.group("title")} {article.group("summary")}')
        value_match = re.search(
            r"PMI.{0,180}?(?:posted|posts|stood at|reached|rose.{0,30}?to|fell.{0,30}?to)\s*(\d{2}(?:[,.]\d+)?)",
            text,
            flags=re.I,
        )
        if not value_match:
            continue
        try:
            value = parse_vietnamese_number(value_match.group(1))
        except ValueError:
            continue
        if not 20 <= value <= 80:
            continue
        link = article.group("link")
        if link.startswith("/"):
            link = f"https://en.baochinhphu.vn{link}"
        date_match = re.search(r"-111(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})\d+\.htm$", link)
        as_of = None
        if date_match:
            as_of = f'20{date_match.group("yy")}-{date_match.group("mm")}-{date_match.group("dd")}'
        return {
            "pmi_manufacturing": {
                "value": round(value, 2),
                "as_of": as_of,
                "source_quality": "AUTO_VGP_SPGLOBAL",
                "source_live": "VGP / S&P Global",
                "source_url": link,
            }
        }
    return {}


def parse_vbma_report(text: str, source_url: str) -> dict[str, dict[str, Any]]:
    date_match = re.search(r"tính đến ngày\s+(\d{1,2})/(\d{1,2})/(20\d{2})", text, flags=re.I)
    if not date_match:
        return {}
    day, month, year = (int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))
    as_of = f"{year:04d}-{month:02d}-{day:02d}"
    metadata = {
        "as_of": as_of,
        "source_quality": "AUTO_VBMA_PDF",
        "source_live": "VBMA",
        "source_url": source_url,
    }
    values: dict[str, dict[str, Any]] = {}

    overnight_match = re.search(
        r"lãi suất qua đêm ON\s+(?:tăng|giảm)\s+\d+\s*đcb\s+(?:lên|xuống) mức\s+(\d+(?:[,.]\d+)?)%",
        text,
        flags=re.I,
    )
    if overnight_match:
        values["interbank_rate"] = {
            "value": round(parse_vietnamese_number(overnight_match.group(1)), 4),
            **metadata,
        }

    yield_section_pos = text.find("BIẾN ĐỘNG LỢI SUẤT PHÒNG GIAO DỊCH VBMA")
    if yield_section_pos != -1:
        yield_section = text[yield_section_pos : yield_section_pos + 2500]
        date_pattern = rf"0?{day}/0?{month}/{year}"
        current_row = re.search(
            date_pattern + r"\s+(?=(?:\d+(?:[,.]\d+)?%\s*){6,})",
            yield_section,
        )
        if current_row:
            yield_values = re.findall(
                r"(\d+(?:[,.]\d+)?)%",
                yield_section[current_row.end() : current_row.end() + 220],
            )
            if len(yield_values) >= 6:
                values["govt_bond_yield"] = {
                    "value": round(parse_vietnamese_number(yield_values[5]), 4),
                    **metadata,
                }

    govt_issuance_match = re.search(r"đã huy động gần\s+([\d,.]+)\s+tỷ đồng", text, flags=re.I)
    if govt_issuance_match:
        values["govt_bond_issuance"] = {
            "value": round(parse_vietnamese_number(govt_issuance_match.group(1)) / 1000, 4),
            **metadata,
        }

    corporate_issuance_match = re.search(r"tổng khối lượng là\s+([\d,.]+)\s+tỷ đồng", text, flags=re.I)
    if corporate_issuance_match:
        values["corporate_bond_issuance"] = {
            "value": round(parse_vietnamese_number(corporate_issuance_match.group(1)) / 1000, 4),
            **metadata,
        }
    return values


def fetch_vbma_snapshot() -> dict[str, dict[str, Any]]:
    try:
        page = fetch_text(VBMA_WEEKLY_URL)
        pdf_match = re.search(r'href=["\'](?P<href>[^"\']+\.pdf)["\']', page, flags=re.I)
        if not pdf_match:
            return {}
        pdf_url = quote(urljoin(VBMA_WEEKLY_URL, html.unescape(pdf_match.group("href"))), safe=":/?=&%")
        reader = PdfReader(BytesIO(fetch_bytes(pdf_url)))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return parse_vbma_report(text, pdf_url)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return {}


def source_health() -> dict[str, Any]:
    health = {}
    for key, source in SOURCE_REGISTRY.items():
        try:
            text = fetch_text(source["url"], timeout=12)
            health[key] = {
                **source,
                "available": True,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "bytes": len(text.encode("utf-8", errors="ignore")),
            }
        except Exception as exc:
            health[key] = {
                **source,
                "available": False,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc)[:180],
            }
    return health


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


def latest_vietcap_index() -> tuple[float | None, str | None]:
    url = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
    payload = {
        "timeFrame": "ONE_DAY",
        "symbols": ["VNINDEX"],
        "to": int(datetime.now(timezone.utc).timestamp()),
        "countBack": 5,
    }
    headers = request_headers(url)
    headers.update(
        {
            "Content-Type": "application/json",
            "Origin": "https://trading.vietcap.com.vn",
            "Referer": "https://trading.vietcap.com.vn/",
        }
    )
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        data = json.loads(fetch_request_text(request))
        if not isinstance(data, list) or not data:
            return None, None
        closes = data[0].get("c", [])
        timestamps = data[0].get("t", [])
        for idx in range(len(closes) - 1, -1, -1):
            close = closes[idx]
            if isinstance(close, (int, float)):
                stamp = None
                if idx < len(timestamps):
                    stamp = datetime.fromtimestamp(int(timestamps[idx]), tz=timezone.utc).date().isoformat()
                return float(close), stamp
    except (ValueError, TypeError, HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None, None
    return None, None


def latest_yahoo(symbol: str) -> tuple[float | None, str | None]:
    encoded = symbol.replace("^", "%5E")
    for host in ["query2.finance.yahoo.com", "query1.finance.yahoo.com"]:
        url = f"https://{host}/v8/finance/chart/{encoded}?range=5d&interval=1d"
        try:
            data = fetch_json(url)
            result = data.get("chart", {}).get("result", [])
            if not result:
                continue
            quote = result[0].get("indicators", {}).get("quote", [{}])[0]
            closes = quote.get("close", [])
            timestamps = result[0].get("timestamp", [])
            for idx in range(len(closes) - 1, -1, -1):
                close = closes[idx]
                if isinstance(close, (int, float)):
                    stamp = datetime.fromtimestamp(timestamps[idx], tz=timezone.utc).date().isoformat() if idx < len(timestamps) else None
                    return float(close), stamp
        except (ValueError, HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            continue
    return None, None


def live_values() -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    values.update(fetch_nso_snapshot())
    values.update(fetch_pmi_snapshot())
    values.update(fetch_vbma_snapshot())

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

    value, as_of = latest_vietcap_index()
    if value is not None:
        values["stock_market"] = {
            "value": round(value, 4),
            "as_of": as_of,
            "source_quality": "AUTO",
            "source_live": "Vietcap",
            "source_url": "https://trading.vietcap.com.vn/",
        }

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

def direction_for(key: str, value: Any) -> str:
    if value is None:
        return "flat"
    if key in {"dxy", "oil_prices", "us_10y_yield", "interbank_rate"}:
        return "up"
    if key in {"cpi"}:
        return "down"
    return "flat"


def card_change_label(card: dict[str, Any]) -> str:
    health = "OK" if card["value"] is not None else "CHECK"
    if card.get("vip"):
        return f'VIP · {card["frequency"]} · {card["source_quality"]} · {health}'
    return f'{card["frequency"]} · {card["source_quality"]} · {health}'


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
            "source_url": live.get("source_url", spec.source_url),
            "source_quality": live.get("source_quality", "SOURCE_MONITOR"),
            "as_of": live.get("as_of", now.date().isoformat()),
            "frequency": VIP_FREQUENCIES.get(spec.key, "monitor"),
            "vip": VIP_FREQUENCIES.get(spec.key) in {"monthly", "yearly"},
            "direction": direction_for(spec.key, value),
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
    active_keys = {card["key"] for card in cards if card["key"] in HISTORY_IMPORTANT_KEYS and card["value"] is not None}
    for key in list(series.keys()):
        if key not in active_keys:
            series.pop(key, None)
    for card in cards:
        if card["key"] not in HISTORY_IMPORTANT_KEYS or card["value"] is None:
            continue
        points = series.setdefault(card["key"], [])
        bucket = history_bucket(card, now)
        points = compact_bucket_points(points, card)
        if points and points[-1].get("bucket") == bucket:
            points[-1] = {"date": stamp, "bucket": bucket, "value": card["value"], "unit": card["unit"]}
        else:
            points.append({"date": stamp, "bucket": bucket, "value": card["value"], "unit": card["unit"]})
        series[card["key"]] = points[-HISTORY_LIMIT:]
    history["policy"] = {
        "retention_max_points": HISTORY_LIMIT,
        "retention_min_target_points": 30,
        "stored_keys": sorted(HISTORY_IMPORTANT_KEYS),
        "frequency_rule": {
            "daily": "one point per day",
            "monthly_or_yearly_vip": "one point per month/period, not one duplicate point every day",
            "weekly_snapshot": "one point per ISO week",
            "event_or_meeting": "one point only when the value changes",
        },
        "note": "Only important indicators keep history. Normal snapshot data is not retained to avoid duplicate/noisy storage.",
    }
    return history


def history_bucket(card: dict[str, Any], now: datetime) -> str:
    frequency = card.get("frequency", "daily")
    if frequency in {"monthly", "yearly"} or card.get("vip"):
        as_of = str(card.get("as_of") or now.date().isoformat())
        match = re.search(r"(20\d{2})[-/](\d{1,2})", as_of)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}"
        return now.strftime("%Y-%m")
    if frequency == "weekly_snapshot":
        year, week, _ = now.isocalendar()
        return f"{year}-W{week:02d}"
    if frequency in {"event", "meeting", "daily_monitor"}:
        return f"value-{card.get('value')}"
    return now.date().isoformat()


def compact_bucket_points(points: list[dict[str, Any]], card: dict[str, Any]) -> list[dict[str, Any]]:
    by_day: dict[str, dict[str, Any]] = {}
    for point in points:
        bucket = bucket_from_existing_point(point, card)
        if not bucket:
            continue
        normalized = dict(point)
        normalized["bucket"] = bucket
        by_day[bucket] = normalized
    return [by_day[day] for day in sorted(by_day)]


def bucket_from_existing_point(point: dict[str, Any], card: dict[str, Any]) -> str:
    date = str(point.get("date", ""))
    frequency = card.get("frequency", "daily")
    if (frequency in {"monthly", "yearly"} or card.get("vip")) and len(date) >= 7:
        return date[:7]
    if frequency == "weekly_snapshot" and len(date) >= 10:
        try:
            parsed = datetime.fromisoformat(date.replace("Z", "+00:00"))
            year, week, _ = parsed.isocalendar()
            return f"{year}-W{week:02d}"
        except ValueError:
            return date[:10]
    if frequency in {"event", "meeting", "daily_monitor"}:
        return f"value-{point.get('value')}"
    return date[:10]

def build_frontend_api(payload: dict[str, Any], history: dict[str, Any]) -> None:
    DOCS_API_DIR.mkdir(exist_ok=True)
    indicators = []
    for card in payload["cards"]:
        if card["value"] is None:
            continue
        indicators.append(
            {
                "id": card["key"],
                "group": card["group"],
                "name": card["name_vi"],
                "value": card["value"],
                "unit": card["unit"],
                "change_label": card_change_label(card),
                "direction": card["direction"],
                "source": card["source_primary"],
                "schedule": card["frequency"],
                "health": "OK",
                "vip": card["vip"],
                "updated_at": card["as_of"],
            }
        )
    (DOCS_API_DIR / "indicators.json").write_text(json.dumps(indicators, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DOCS_API_DIR / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_html(payload: dict[str, Any]) -> str:
    cards = payload["cards"]
    available = payload["coverage"]["available_cards"]
    total = payload["coverage"]["total_cards"]
    generated = html.escape(payload["generated_at_bkk"])
    tabs = []
    sections = []
    history = payload.get("history", {}).get("series", {})
    for group_key, group_name in GROUPS.items():
        active = " active" if not tabs else ""
        group_cards = [c for c in cards if c["group"] == group_key]
        tabs.append(f'<button class="tab{active}" data-tab="{group_key}">{html.escape(group_name)} <span>{len(group_cards)}</span></button>')
        card_html = []
        visible_cards = [c for c in group_cards if c["value"] is not None]
        if not visible_cards:
            card_html.append('<div class="empty">Chưa có dữ liệu tự động chắc chắn cho nhóm này. Nguồn vẫn được kiểm tra mỗi sáng.</div>')
        for card in visible_cards:
            value = "Chờ nguồn" if card["value"] is None else f'{card["value"]} {html.escape(card["unit"])}'
            status = "ok"
            vip = '<b class="vip">VIP</b>' if card.get("vip") else ""
            health = "OK" if card["value"] is not None else "CHECK"
            icon = "↗" if card["direction"] == "up" else "↘" if card["direction"] == "down" else "–"
            has_chart = card["key"] in history and len(history[card["key"]]) >= 2
            chart_attr = f' data-key="{html.escape(card["key"])}"' if has_chart else ""
            chart_class = " chartable" if has_chart else ""
            chart_hint = '<span class="chart-hint">Bấm để xem biểu đồ</span>' if has_chart else '<span class="chart-hint muted">Snapshot</span>'
            card_html.append(
                f"""
        <article class="card {status}{chart_class}"{chart_attr}>
          <div class="card-top">
            <span class="group-label">{html.escape(card["group_name"])}</span>
            <span class="health">{health}</span>
          </div>
          <h3>{html.escape(card["name_vi"])} {vip}</h3>
          <p class="value">{value}</p>
          <p class="change"><span>{icon}</span>{html.escape(card_change_label(card))}</p>
          {chart_hint}
          <p class="source">Nguồn: <a href="{html.escape(card["source_url"])}">{html.escape(card["source_primary"])}</a></p>
        </article>"""
            )
        sections.append(f'<section id="{group_key}" class="panel{active}">' + "\n".join(card_html) + "</section>")

    source_rows = []
    for key, source in payload["source_health"].items():
        badge = "OK" if source["available"] else "CHECK"
        cls = "source-ok" if source["available"] else "source-warn"
        source_rows.append(
            f'<tr><td>{html.escape(source["name"])}</td><td>{html.escape(source["role"])}</td><td>{html.escape(source["release_lag"])}</td><td class="{cls}">{badge}</td></tr>'
        )
    source_table = "<table><thead><tr><th>Nguồn</th><th>Vai trò</th><th>Lịch</th><th>Health</th></tr></thead><tbody>" + "".join(source_rows) + "</tbody></table>"

    chart_data = {
        card["key"]: {
            "name": card["name_vi"],
            "unit": card["unit"],
            "points": history.get(card["key"], []),
        }
        for card in cards
        if history.get(card["key"])
    }
    chart_data_json = json.dumps(chart_data, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>vimo-VN Macro Monitor</title>
  <style>
    :root {{ color-scheme: dark; --ink:#f8fafc; --muted:#94a3b8; --line:rgba(255,255,255,.09); --ok:#34d399; --warn:#f59e0b; --bg:#090b10; --panel:#0f1119; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family: Arial, sans-serif; background:radial-gradient(circle at 20% 0%, rgba(79,70,229,.2), transparent 28%), var(--bg); color:var(--ink); }}
    header {{ padding:30px 20px 10px; }}
    .wrap {{ max-width:1060px; margin:0 auto; }}
    .eyebrow {{ display:inline-flex; gap:8px; align-items:center; margin-bottom:10px; }}
    .pill {{ font-size:11px; font-weight:700; color:#c7d2fe; background:rgba(99,102,241,.16); border:1px solid rgba(129,140,248,.28); border-radius:999px; padding:5px 9px; }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    .meta {{ color:var(--muted); margin:0; }}
    .summary {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:18px; }}
    .metric {{ background:rgba(255,255,255,.035); border:1px solid var(--line); border-radius:8px; padding:14px; }}
    .metric strong {{ display:block; font-size:24px; color:#fff; }}
    nav {{ display:flex; gap:8px; flex-wrap:wrap; padding:16px 20px; }}
    .tab {{ border:1px solid var(--line); background:rgba(255,255,255,.03); color:var(--muted); border-radius:8px; padding:9px 12px; cursor:pointer; }}
    .tab.active {{ border-color:rgba(129,140,248,.65); color:#fff; background:rgba(99,102,241,.16); }}
    .tab span {{ color:var(--muted); }}
    main {{ padding:20px; }}
    .panel {{ display:none; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; }}
    .panel.active {{ display:grid; }}
    .card {{ text-align:left; background:rgba(255,255,255,.035); border:1px solid var(--line); border-radius:8px; padding:18px; min-height:178px; transition:background .2s,border-color .2s; }}
    .card:hover {{ background:rgba(255,255,255,.06); border-color:rgba(255,255,255,.16); }}
    .card.chartable {{ cursor:pointer; }}
    .card-top {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }}
    .group-label {{ font-size:11px; text-transform:uppercase; color:#94a3b8; font-weight:700; }}
    .health {{ color:#34d399; background:rgba(52,211,153,.1); border-radius:999px; padding:2px 7px; font-size:10px; font-weight:700; }}
    h3 {{ margin:14px 0 6px; font-size:15px; color:#cbd5e1; font-weight:600; }}
    .vip {{ display:inline-block; margin-left:6px; color:#fde68a; background:rgba(245,158,11,.12); border:1px solid rgba(245,158,11,.35); border-radius:4px; padding:1px 5px; font-size:10px; vertical-align:middle; }}
    .value {{ font-size:25px; font-weight:700; margin:0 0 8px; color:#fff; }}
    .change {{ display:flex; gap:6px; align-items:flex-start; color:#94a3b8; font-size:12px; line-height:1.35; }}
    .chart-hint {{ display:inline-block; margin-top:10px; color:#a5b4fc; font-size:12px; }}
    .chart-hint.muted {{ color:#64748b; }}
    .source {{ color:#64748b; font-size:12px; margin-top:16px; }}
    p {{ line-height:1.45; }}
    a {{ color:#93c5fd; text-decoration:none; font-size:13px; }}
    .empty {{ grid-column:1/-1; color:#94a3b8; border:1px dashed var(--line); border-radius:8px; padding:18px; background:rgba(255,255,255,.025); }}
    .sources {{ margin-top:22px; background:rgba(255,255,255,.035); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
    .sources h2 {{ margin:0; padding:14px 16px; font-size:18px; border-bottom:1px solid var(--line); }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th,td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
    th {{ color:var(--muted); font-weight:600; background:rgba(255,255,255,.03); }}
    .source-ok {{ color:var(--ok); font-weight:700; }}
    .source-warn {{ color:var(--warn); font-weight:700; }}
    .modal {{ position:fixed; inset:0; display:none; align-items:center; justify-content:center; background:rgba(0,0,0,.72); padding:18px; z-index:50; }}
    .modal.open {{ display:flex; }}
    .modal-box {{ width:min(760px,100%); background:#0f1119; border:1px solid var(--line); border-radius:8px; padding:18px; box-shadow:0 22px 80px rgba(0,0,0,.45); }}
    .modal-head {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:12px; }}
    .modal-title {{ margin:0; font-size:18px; }}
    .modal-close {{ background:rgba(255,255,255,.06); color:#fff; border:1px solid var(--line); border-radius:6px; padding:7px 10px; cursor:pointer; }}
    .chart-svg {{ width:100%; height:300px; display:block; background:rgba(255,255,255,.025); border:1px solid var(--line); border-radius:8px; }}
    .chart-meta {{ color:#94a3b8; font-size:12px; margin-top:10px; }}
    @media (max-width:720px) {{ .summary {{ grid-template-columns:1fr; }} h1 {{ font-size:23px; }} }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <div class="eyebrow"><span class="pill">Báo cáo vĩ mô Việt Nam</span><span class="meta">Cập nhật: {generated}</span></div>
      <h1>Tình hình Kinh tế · Tiền tệ · Tài chính</h1>
      <p class="meta">Tự động quét mỗi sáng qua GitHub Actions. Card chỉ hiện khi có số liệu máy đọc được.</p>
      <div class="summary">
        <div class="metric"><strong>{available}/{total}</strong><span>card có dữ liệu tự động</span></div>
        <div class="metric"><strong>{payload["coverage"]["source_count"]}</strong><span>nguồn theo dõi</span></div>
        <div class="metric"><strong>{html.escape(payload["status"])}</strong><span>trạng thái pipeline</span></div>
      </div>
    </div>
  </header>
  <nav class="wrap">{"".join(tabs)}</nav>
  <main class="wrap">{"".join(sections)}<section class="sources"><h2>Nguồn độc lập đang theo dõi</h2>{source_table}</section></main>
  <div class="modal" id="chartModal" aria-hidden="true">
    <div class="modal-box">
      <div class="modal-head">
        <div>
          <p class="group-label">Lịch sử tối đa {HISTORY_LIMIT} điểm</p>
          <h2 class="modal-title" id="chartTitle">Biểu đồ</h2>
        </div>
        <button class="modal-close" id="chartClose" type="button">Đóng</button>
      </div>
      <svg class="chart-svg" id="chartSvg" viewBox="0 0 720 300" role="img"></svg>
      <p class="chart-meta" id="chartMeta"></p>
    </div>
  </div>
  <script>
    const chartData = {chart_data_json};
    const tabs = document.querySelectorAll('.tab');
    const panels = document.querySelectorAll('.panel');
    tabs.forEach(tab => tab.addEventListener('click', () => {{
      tabs.forEach(t => t.classList.remove('active'));
      panels.forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById(tab.dataset.tab).classList.add('active');
    }}));
    const modal = document.getElementById('chartModal');
    const closeBtn = document.getElementById('chartClose');
    const title = document.getElementById('chartTitle');
    const svg = document.getElementById('chartSvg');
    const meta = document.getElementById('chartMeta');
    function drawChart(key) {{
      const item = chartData[key];
      if (!item || !item.points || item.points.length < 2) return;
      const pts = item.points.slice(-{HISTORY_LIMIT});
      const values = pts.map(p => Number(p.value));
      const min = Math.min(...values);
      const max = Math.max(...values);
      const pad = max === min ? Math.max(1, Math.abs(max) * 0.02) : (max - min) * 0.08;
      const lo = min - pad, hi = max + pad;
      const x = i => 42 + i * (636 / Math.max(1, pts.length - 1));
      const y = v => 252 - ((v - lo) / Math.max(0.000001, hi - lo)) * 204;
      const line = pts.map((p, i) => `${{x(i).toFixed(1)}},${{y(Number(p.value)).toFixed(1)}}`).join(' ');
      const first = pts[0], last = pts[pts.length - 1];
      svg.innerHTML = `
        <line x1="42" y1="252" x2="678" y2="252" stroke="rgba(255,255,255,.16)" />
        <line x1="42" y1="48" x2="42" y2="252" stroke="rgba(255,255,255,.16)" />
        <text x="44" y="38" fill="#94a3b8" font-size="12">${{hi.toFixed(2)}}</text>
        <text x="44" y="276" fill="#94a3b8" font-size="12">${{lo.toFixed(2)}}</text>
        <polyline points="${{line}}" fill="none" stroke="#818cf8" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
        <circle cx="${{x(pts.length - 1).toFixed(1)}}" cy="${{y(Number(last.value)).toFixed(1)}}" r="4" fill="#34d399" />
      `;
      title.textContent = item.name;
      meta.textContent = `${{pts.length}} điểm · mới nhất: ${{last.value}} ${{item.unit || ''}} · từ ${{first.date.slice(0,10)}} đến ${{last.date.slice(0,10)}}`;
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
    }}
    document.querySelectorAll('.card.chartable').forEach(card => card.addEventListener('click', () => drawChart(card.dataset.key)));
    closeBtn.addEventListener('click', () => {{ modal.classList.remove('open'); modal.setAttribute('aria-hidden', 'true'); }});
    modal.addEventListener('click', event => {{ if (event.target === modal) closeBtn.click(); }});
  </script>
</body>
</html>
"""


def main() -> None:
    now = datetime.now(timezone.utc)
    OUTPUT_DIR.mkdir(exist_ok=True)
    DOCS_DIR.mkdir(exist_ok=True)
    cards = build_cards(now)
    sources = source_health()
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
            "vip_cards": sum(1 for card in cards if card.get("vip")),
            "vip_available": sum(1 for card in cards if card.get("vip") and card["value"] is not None),
            "note": "Macro cards without a reliable machine-readable source are marked awaiting_official_source instead of using guessed values.",
        },
        "source_health": sources,
        "history": history,
        "cards": cards,
    }

    (OUTPUT_DIR / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    build_frontend_api(payload, history)
    (DOCS_DIR / "index.html").write_text(render_html(payload), encoding="utf-8")

    vip_available = payload["coverage"]["vip_available"]
    vip_cards = payload["coverage"]["vip_cards"]
    summary = f"vimo-VN updated: {available}/{len(cards)} cards have automatic values; VIP {vip_available}/{vip_cards}."
    (OUTPUT_DIR / "telegram_summary.txt").write_text(summary + "\n", encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"vimo-VN pipeline failed: {exc}", file=sys.stderr)
        raise

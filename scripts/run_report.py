from __future__ import annotations

import csv
import html
import json
import math
import os
import re
import ssl
import sys
import time
import xml.etree.ElementTree as ET
from calendar import monthrange
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
MEMORY_FILE = OUTPUT_DIR / "indicator_memory.json"
GEMINI_ANALYSIS_FILE = OUTPUT_DIR / "gemini_analysis.json"
VERIFIED_BASELINE_FILE = ROOT / "data" / "verified_baseline.json"
VERIFIED_HISTORY_FILE = ROOT / "data" / "verified_history.json"


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
    "fed_policy": "meeting",
    "us_economy": "monthly",
    "oil_prices": "daily",
    "geopolitical_risk": "daily_monitor",
    "us_10y_yield": "daily",
    "ecb_eurozone": "meeting",
    "boj_japan": "meeting",
    "china_economy": "monthly",
    "policy_actions_vn": "event",
}

SOURCE_REGISTRY = {
    "pmi": {"name": "S&P Global PMI via VGP", "url": "https://en.baochinhphu.vn/search.htm?keywords=PMI", "role": "PMI sản xuất do S&P Global công bố", "release_lag": "M+1 ngày 1-3"},
    "nso": {"name": "NSO/GSO Việt Nam", "url": "https://www.nso.gov.vn/", "role": "CPI, IIP, FDI, bán lẻ, doanh nghiệp, du lịch", "release_lag": "M+1 ngày 3-7"},
    "customs": {"name": "Tổng cục Hải quan", "url": "https://www.customs.gov.vn/", "role": "Xuất nhập khẩu chính thức", "release_lag": "M+1 ngày 10-15"},
    "vbma": {"name": "VBMA", "url": "https://vbma.org.vn/vi/reports/weekly", "role": "Liên ngân hàng, TPCP, TPDN tuần", "release_lag": "hàng tuần"},
    "vnba": {"name": "VNBA", "url": "https://vnba.org.vn/", "role": "Bản tin tiền tệ tài chính tháng", "release_lag": "M+1 ngày 11-13"},
    "market": {"name": "Public market APIs / Vietcap", "url": "https://trading.vietcap.com.vn/", "role": "VN-Index, tỷ giá, vàng, dầu, DXY, US10Y", "release_lag": "daily"},
}

HISTORY_LIMIT = 100
MEMORY_EVENT_LIMIT = 500
GEMINI_EVENT_BATCH_LIMIT = 20
NON_CACHEABLE_FREQUENCIES = {"daily", "daily_monitor"}
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
    IndicatorSpec("fed_policy", "Fed policy rate", "global", "%", "FRED", "Biên trên lãi suất mục tiêu Fed Funds.", "Fed là neo lãi suất USD và dòng vốn toàn cầu.", 1, "https://fred.stlouisfed.org/"),
    IndicatorSpec("us_economy", "Kinh tế Mỹ", "global", "ghi nhận", "FRED/BEA", "Tăng trưởng, lạm phát, lao động Mỹ.", "Mỹ là thị trường xuất khẩu lớn và neo USD.", 2, "https://fred.stlouisfed.org/"),
    IndicatorSpec("oil_prices", "Giá dầu WTI", "global", "USD/thùng", "Stooq", "Giá dầu WTI.", "Dầu ảnh hưởng chi phí năng lượng và lạm phát.", 3, "https://stooq.com/"),
    IndicatorSpec("geopolitical_risk", "Rủi ro địa chính trị", "global", "ghi nhận", "News", "Các điểm nóng địa chính trị.", "Cú sốc địa chính trị ảnh hưởng dầu, logistics và USD.", 4, "https://www.reuters.com/"),
    IndicatorSpec("us_10y_yield", "US 10Y yield", "global", "%", "Stooq", "Lợi suất trái phiếu chính phủ Mỹ 10 năm.", "US10Y là benchmark định giá tài sản toàn cầu.", 5, "https://stooq.com/"),
    IndicatorSpec("ecb_eurozone", "ECB/Eurozone", "global", "ghi nhận", "ECB", "Chính sách ECB và kinh tế Eurozone.", "EU là đối tác thương mại và nguồn FDI quan trọng.", 6, "https://www.ecb.europa.eu/"),
    IndicatorSpec("boj_japan", "BOJ/Japan", "global", "ghi nhận", "BOJ", "Chính sách BOJ và kinh tế Nhật.", "Nhật là nguồn FDI và đối tác tài chính lớn.", 7, "https://www.boj.or.jp/"),
    IndicatorSpec("china_economy", "Kinh tế Trung Quốc", "global", "ghi nhận", "NBS China", "PMI, tăng trưởng và thương mại Trung Quốc.", "Trung Quốc là đối tác thương mại lớn nhất của Việt Nam.", 8, "https://www.stats.gov.cn/"),
    IndicatorSpec("policy_actions_vn", "Chính sách Việt Nam", "global", "ghi nhận", "Government/SBV", "Chỉ đạo điều hành, thông tư, quyết định mới.", "Chính sách nội địa là biến số trực tiếp cho thị trường.", 9, "https://chinhphu.vn/"),
    IndicatorSpec("agriculture_snapshot", "Nông nghiệp", "global", "% YoY", "NSO", "Tổng quan khu vực nông lâm thủy sản.", "Nông nghiệp ảnh hưởng lương thực, xuất khẩu và CPI.", 10, "https://www.nso.gov.vn/"),
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


def nso_report_period_end(title: str, published: Any) -> str | None:
    title_l = title.lower()
    year_match = re.search(r"20\d{2}", title_l)
    year = int(year_match.group(0)) if year_match else None
    month = None
    month_words = {
        "một": 1,
        "hai": 2,
        "ba": 3,
        "tư": 4,
        "bốn": 4,
        "năm": 5,
        "sáu": 6,
        "bảy": 7,
        "tám": 8,
        "chín": 9,
        "mười": 10,
        "mười một": 11,
        "mười hai": 12,
    }
    numeric_period = re.search(r"(?:^|\s)(1[0-2]|[1-9])\s*tháng", title_l)
    if numeric_period:
        month = int(numeric_period.group(1))
    else:
        for word, number in sorted(month_words.items(), key=lambda item: len(item[0]), reverse=True):
            if f"{word} tháng" in title_l or f"tháng {word}" in title_l:
                month = number
                break
    if year and month:
        return f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"
    if published:
        published_match = re.search(r"20\d{2}-\d{2}-\d{2}", str(published))
        if published_match:
            return published_match.group(0)
    return None


def first_regex_number(text: str, pattern: str, flags: int = re.I | re.S) -> float | None:
    match = re.search(pattern, text, flags=flags)
    if not match:
        return None
    try:
        return parse_vietnamese_number(match.group(1))
    except (IndexError, ValueError):
        return None


def parse_nso_economic_report(text: str, as_of: str | None, source_url: str | None) -> dict[str, dict[str, Any]]:
    metadata = {
        "as_of": as_of,
        "source_quality": "AUTO_NSO_PARSE",
        "source_live": "NSO",
        "source_url": source_url,
    }
    values: dict[str, dict[str, Any]] = {}

    def add(key: str, value: Any, unit: str | None = None, source_note: str | None = None) -> None:
        if value is None:
            return
        normalized = round(value, 4) if isinstance(value, float) else value
        values[key] = {"value": normalized, **metadata}
        if unit:
            values[key]["unit"] = unit
        if source_note:
            values[key]["source_note"] = source_note

    add("cpi", first_cpi_yoy(text))
    add("iip", first_percent_with_context(text, ["Chỉ số sản xuất công nghiệp", "IIP"], ["so với cùng kỳ", "tăng"]))
    add(
        "retail",
        first_regex_number(
            text,
            r"Tính chung[^.]{0,100}tổng mức bán lẻ hàng hóa và doanh thu dịch vụ tiêu dùng.{0,180}?tăng\s+([\d,.]+)%\s+so với cùng kỳ",
        ),
        "% YoY lũy kế kỳ báo cáo",
    )
    add(
        "international_visitors",
        first_regex_number(
            text,
            r"Khách quốc tế đến Việt Nam[^.]{0,80}?sáu tháng[^.]{0,80}?đạt\s+([\d,.]+)\s+triệu lượt",
        ),
        "triệu lượt (lũy kế kỳ báo cáo)",
    )
    add(
        "exports",
        first_regex_number(text, r"Tính chung[^.]{0,120}kim ngạch xuất khẩu hàng hóa (?:ước )?đạt\s+([\d,.]+)\s+tỷ USD"),
        "tỷ USD (lũy kế kỳ báo cáo)",
    )
    add(
        "imports",
        first_regex_number(text, r"Tính chung[^.]{0,120}kim ngạch nhập khẩu hàng hóa (?:ước )?đạt\s+([\d,.]+)\s+tỷ USD"),
        "tỷ USD (lũy kế kỳ báo cáo)",
    )
    trade_match = re.search(
        r"Tính chung[^.]{0,120}cán cân thương mại hàng hóa (?:ước )?(nhập siêu|xuất siêu)\s+([\d,.]+)\s+tỷ USD",
        text,
        flags=re.I | re.S,
    )
    if trade_match:
        trade_value = parse_vietnamese_number(trade_match.group(2))
        if trade_match.group(1).lower() == "nhập siêu":
            trade_value = -trade_value
        add("trade_balance", trade_value, "tỷ USD (lũy kế kỳ báo cáo)", "Nhập siêu mang dấu âm; xuất siêu mang dấu dương.")

    add(
        "fdi_registered",
        first_regex_number(text, r"Tổng vốn đầu tư nước ngoài đăng ký vào Việt Nam.{0,500}?đạt\s+([\d,.]+)\s+tỷ USD"),
        "tỷ USD (lũy kế kỳ báo cáo)",
    )
    add(
        "fdi_disbursed",
        first_regex_number(text, r"Vốn đầu tư trực tiếp nước ngoài thực hiện tại Việt Nam.{0,220}?(?:ước )?đạt\s+([\d,.]+)\s+tỷ USD"),
        "tỷ USD (lũy kế kỳ báo cáo)",
    )
    business_new = first_regex_number(
        text,
        r"Tính chung[^.]{0,120}cả nước có (?:gần|hơn)?\s*([\d,.]+)\s*nghìn doanh nghiệp đăng ký thành lập mới",
    )
    add("business_new", business_new * 1000 if business_new is not None else None, "doanh nghiệp (lũy kế kỳ báo cáo)")
    business_exited = first_regex_number(
        text,
        r"[Ss]ố doanh nghiệp rút lui khỏi thị trường (?:là|đạt)\s*([\d,.]+)\s*nghìn doanh nghiệp",
    )
    add("business_exited", business_exited * 1000 if business_exited is not None else None, "doanh nghiệp (lũy kế kỳ báo cáo)")
    add(
        "state_investment",
        first_regex_number(text, r"Vốn khu vực Nhà nước đạt.{0,180}?tăng\s+([\d,.]+)%\s+so với cùng kỳ"),
        "% YoY vốn khu vực Nhà nước",
        "Tốc độ tăng vốn đầu tư khu vực Nhà nước, không phải tỷ lệ giải ngân kế hoạch.",
    )
    add(
        "state_budget",
        first_regex_number(text, r"Lũy kế tổng thu ngân sách Nhà nước.{0,100}?(?:ước )?đạt\s+([\d,.]+)\s*nghìn tỷ đồng"),
        "nghìn tỷ VND thu NSNN",
    )
    credit_match = re.search(
        r"Tính đến (?:thời điểm )?(\d{1,2})/(\d{1,2})/(20\d{2}).{0,260}?tăng trưởng tín dụng của nền kinh tế đạt\s+([\d,.]+)%",
        text,
        flags=re.I | re.S,
    )
    if credit_match:
        credit_as_of = f"{int(credit_match.group(3)):04d}-{int(credit_match.group(2)):02d}-{int(credit_match.group(1)):02d}"
        add("credit", parse_vietnamese_number(credit_match.group(4)), "% YTD")
        values["credit"]["as_of"] = credit_as_of
    add(
        "agriculture_snapshot",
        first_regex_number(text, r"GDP sáu tháng.{0,700}?khu vực nông, lâm nghiệp và thủy sản tăng\s+([\d,.]+)%"),
        "% YoY khu vực I",
    )
    fdi_export_match = re.search(
        r"khu vực có vốn đầu tư nước ngoài \(kể cả dầu thô\) đạt\s+[\d,.]+\s+tỷ USD.{0,100}?chiếm\s+([\d,.]+)%",
        text,
        flags=re.I | re.S,
    )
    if fdi_export_match:
        add("trade_by_sector", parse_vietnamese_number(fdi_export_match.group(1)), "% xuất khẩu thuộc khu vực FDI")
    market_match = re.search(
        r"Hoa Kỳ là thị trường xuất khẩu lớn nhất.{0,100}?đạt\s+([\d,.]+)\s+tỷ USD.{0,180}?Trung Quốc là thị trường nhập khẩu lớn nhất.{0,100}?đạt\s+([\d,.]+)\s+tỷ USD",
        text,
        flags=re.I | re.S,
    )
    if market_match:
        us_exports = parse_vietnamese_number(market_match.group(1))
        china_imports = parse_vietnamese_number(market_match.group(2))
        add(
            "trade_by_market",
            f"Mỹ XK {us_exports:g}; Trung Quốc NK {china_imports:g}",
            "tỷ USD (lũy kế kỳ báo cáo)",
        )
    return values


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
    posts = fetch_nso_posts()
    candidates = []
    for post in posts:
        title = strip_tags(post.get("title", {}).get("rendered", ""))
        title_l = title.lower()
        content = post.get("content", {}).get("rendered", "")
        if "tình hình kinh tế" in title_l and "tháng" in title_l and content:
            candidates.append((len(content), title, post))
    if not candidates:
        return {}

    _, title, selected = max(candidates, key=lambda item: item[0])
    text = strip_tags(selected.get("content", {}).get("rendered", ""))
    published = selected.get("date_gmt") or selected.get("date")
    as_of = nso_report_period_end(title, published)
    return parse_nso_economic_report(text, as_of, selected.get("link"))


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


def cached_values_from_payload(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for card in payload.get("cards", []):
        key = card.get("key")
        frequency = card.get("frequency", VIP_FREQUENCIES.get(str(key), "monitor"))
        quality = str(card.get("source_quality") or "")
        if (
            not key
            or card.get("value") is None
            or frequency in NON_CACHEABLE_FREQUENCIES
            or quality == "SOURCE_MONITOR"
        ):
            continue
        if quality.startswith("STALE_CACHE_"):
            quality = quality.removeprefix("STALE_CACHE_")
        values[key] = {
            "value": card["value"],
            "unit": card.get("unit"),
            "as_of": card.get("as_of"),
            "source_quality": f"STALE_CACHE_{quality}",
            "source_live": card.get("source_primary"),
            "source_url": card.get("source_url"),
            "source_note": card.get("source_note"),
        }
    return values


def cached_values_from_memory(memory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for key, state in memory.get("states", {}).items():
        quality = str(state.get("source_quality") or "")
        frequency = VIP_FREQUENCIES.get(str(key), "monitor")
        if (
            state.get("value") is None
            or frequency in NON_CACHEABLE_FREQUENCIES
            or not quality
            or quality == "SOURCE_MONITOR"
        ):
            continue
        if quality.startswith("STALE_CACHE_"):
            quality = quality.removeprefix("STALE_CACHE_")
        values[str(key)] = {
            "value": state["value"],
            "unit": state.get("unit"),
            "as_of": state.get("as_of"),
            "source_quality": f"STALE_CACHE_{quality}",
            "source_live": state.get("source_primary"),
            "source_url": state.get("source_url"),
            "source_note": state.get("source_note"),
        }
    return values


def load_verified_baselines() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(VERIFIED_BASELINE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    active_keys = {spec.key for spec in SPECS}
    return {
        str(key): value
        for key, value in data.items()
        if key in active_keys and isinstance(value, dict) and value.get("value") is not None
    }


def load_cached_official_values() -> dict[str, dict[str, Any]]:
    path = OUTPUT_DIR / "latest.json"
    values: dict[str, dict[str, Any]] = {}
    if path.exists():
        try:
            values.update(cached_values_from_payload(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            pass
    memory = load_indicator_memory()
    for key, cached in cached_values_from_memory(memory).items():
        values.setdefault(key, cached)
    return values


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
    cached_values = load_cached_official_values()
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
    }.items():
        value, as_of = latest_stooq(symbol)
        if value is not None:
            if key == "us_10y_yield" and value < 1:
                value = value * 10
            values[key] = {
                "value": round(value, 4),
                "as_of": as_of,
                "source_quality": "AUTO",
                "source_live": "Stooq",
                "source_url": f"https://stooq.com/q/?s={quote(symbol)}",
            }

    for key, symbol in {"gold_world": "xauusd"}.items():
        value, as_of = latest_stooq(symbol)
        if value is not None:
            values[key] = {
                "value": round(value, 4),
                "as_of": as_of,
                "source_quality": "AUTO",
                "source_live": "Stooq",
                "source_url": f"https://stooq.com/q/?s={quote(symbol)}",
            }

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
        "stock_market": "^VNINDEX",
    }.items():
        if key in values:
            continue
        value, as_of = latest_yahoo(symbol)
        if value is not None:
            if key == "us_10y_yield" and value > 20:
                value = value / 10
            values[key] = {
                "value": round(value, 4),
                "as_of": as_of,
                "source_quality": "AUTO",
                "source_live": "Yahoo Finance",
                "source_url": f"https://finance.yahoo.com/quote/{quote(symbol)}",
            }

    for key, cached in cached_values.items():
        values.setdefault(key, cached)
    for key, baseline in load_verified_baselines().items():
        values.setdefault(key, baseline)
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
        unit = live.get("unit", spec.unit)
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
            "unit": unit,
            "status": "available" if available else "awaiting_official_source",
            "signal": signal_for(spec.key, value),
            "source_primary": live.get("source_live", spec.source_primary),
            "source_url": live.get("source_url", spec.source_url),
            "source_quality": live.get("source_quality", "SOURCE_MONITOR"),
            "source_note": live.get("source_note"),
            "as_of": live.get("as_of", now.date().isoformat()),
            "frequency": VIP_FREQUENCIES.get(spec.key, "monitor"),
            "vip": VIP_FREQUENCIES.get(spec.key) in {"monthly", "yearly"},
            "direction": direction_for(spec.key, value),
            "narrative": (
                f"{spec.name_vi} hiện có dữ liệu tự động từ {spec.source_primary}. "
                f"Giá trị mới nhất là {value} {unit}, dùng để theo dõi {spec.why_it_matters.lower()}"
                if available
                else f"{spec.name_vi} đang chờ bản công bố chính thức từ {spec.source_primary}. "
                "Dashboard vẫn giữ card theo dõi nguồn, nhưng không dựng số thay thế khi chưa có dữ liệu chắc chắn."
            ),
        }
        cards.append(card)
    return sorted(cards, key=lambda c: (c["group"], c["priority"]))


def load_verified_history() -> dict[str, list[dict[str, Any]]]:
    try:
        data = json.loads(VERIFIED_HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    active_keys = {spec.key for spec in SPECS}
    raw_series = data.get("series", {}) if isinstance(data, dict) else {}
    return {
        str(key): [dict(point) for point in points if isinstance(point, dict) and point.get("value") is not None]
        for key, points in raw_series.items()
        if key in active_keys and isinstance(points, list)
    }


def normalized_data_date(raw: Any, fallback: datetime | None = None) -> str:
    value = str(raw or "")
    exact = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", value)
    if exact:
        year, month, day = (int(part) for part in exact.groups())
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            pass
    monthly = re.search(r"(20\d{2})[-/](\d{1,2})", value)
    if monthly:
        year, month = (int(part) for part in monthly.groups())
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"
    return (fallback or datetime.now(timezone.utc)).date().isoformat()


def update_history(cards: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            history = {"series": {}}
    else:
        history = {"series": {}}

    active_keys = {str(card["key"]) for card in cards}
    existing_series = history.get("series", {}) if isinstance(history.get("series"), dict) else {}
    verified_series = load_verified_history()
    series: dict[str, list[dict[str, Any]]] = {}

    for card in cards:
        key = str(card["key"])
        points = [*verified_series.get(key, []), *existing_series.get(key, [])]
        current_date = normalized_data_date(card.get("as_of"), now)
        current_bucket = history_bucket(card, current_date)
        points = compact_bucket_points(points, card)
        points = [point for point in points if str(point.get("bucket", "")) <= current_bucket]

        if card.get("value") is not None:
            points.append(
                {
                    "date": current_date,
                    "bucket": current_bucket,
                    "value": card["value"],
                    "unit": card.get("unit"),
                    "source": card.get("source_primary"),
                    "source_url": card.get("source_url"),
                    "source_quality": card.get("source_quality"),
                    "source_note": card.get("source_note"),
                }
            )
        series[key] = compact_bucket_points(points, card)[-HISTORY_LIMIT:]

    for key in list(series):
        if key not in active_keys:
            series.pop(key, None)
    history["series"] = series
    history["policy"] = {
        "retention_max_points": HISTORY_LIMIT,
        "retention_min_target_points": 30,
        "stored_keys": sorted(active_keys),
        "frequency_rule": {
            "daily": "one point per day",
            "monthly_or_yearly_vip": "one point per month/period, not one duplicate point every day",
            "weekly_snapshot": "one point per ISO week",
            "event_or_meeting": "one point per dated observation",
        },
        "note": "All 41 indicators retain dated observations. Verified older values are merged from data/verified_history.json; no historical value is invented.",
    }
    return history


def history_bucket(card: dict[str, Any], data_date: str | datetime) -> str:
    frequency = card.get("frequency", "daily")
    date = normalized_data_date(data_date, data_date if isinstance(data_date, datetime) else None)
    if frequency in {"monthly", "yearly"} or card.get("vip"):
        return date[:7]
    if frequency == "weekly_snapshot":
        year, week, _ = datetime.fromisoformat(date).isocalendar()
        return f"{year}-W{week:02d}"
    return date


def compact_bucket_points(points: list[dict[str, Any]], card: dict[str, Any]) -> list[dict[str, Any]]:
    by_bucket: dict[str, dict[str, Any]] = {}
    for point in points:
        bucket = bucket_from_existing_point(point, card)
        if not bucket:
            continue
        normalized = dict(point)
        normalized["bucket"] = bucket
        normalized["date"] = normalized_point_date(normalized, card)
        by_bucket[bucket] = normalized
    return sorted(by_bucket.values(), key=lambda point: str(point.get("date", "")))


def normalized_point_date(point: dict[str, Any], card: dict[str, Any]) -> str:
    bucket = str(point.get("bucket") or "")
    if (card.get("frequency") in {"monthly", "yearly"} or card.get("vip")) and re.fullmatch(r"20\d{2}-\d{2}", bucket):
        year, month = (int(part) for part in bucket.split("-"))
        return f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"
    return normalized_data_date(point.get("date"))


def bucket_from_existing_point(point: dict[str, Any], card: dict[str, Any]) -> str:
    stored_bucket = str(point.get("bucket") or "")
    if stored_bucket:
        return stored_bucket
    date = str(point.get("date", ""))
    frequency = card.get("frequency", "daily")
    if (frequency in {"monthly", "yearly"} or card.get("vip")) and len(date) >= 7:
        return date[:7]
    if frequency == "weekly_snapshot" and len(date) >= 10:
        try:
            parsed = datetime.fromisoformat(normalized_data_date(date))
            year, week, _ = parsed.isocalendar()
            return f"{year}-W{week:02d}"
        except ValueError:
            return date[:10]
    return date[:10]


def load_indicator_memory() -> dict[str, Any]:
    if not MEMORY_FILE.exists():
        return {"version": 1, "states": {}, "events": []}
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid memory payload")
        data.setdefault("version", 1)
        data.setdefault("states", {})
        data.setdefault("events", [])
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        return {"version": 1, "states": {}, "events": []}


def values_equal(previous: Any, current: Any) -> bool:
    if (
        isinstance(previous, (int, float))
        and not isinstance(previous, bool)
        and isinstance(current, (int, float))
        and not isinstance(current, bool)
    ):
        return math.isclose(float(previous), float(current), rel_tol=1e-9, abs_tol=1e-9)
    return previous == current


def memory_state_from_card(card: dict[str, Any], now_iso: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    changed_at = now_iso if previous is None else previous.get("last_changed_at", now_iso)
    return {
        "value": card["value"],
        "as_of": card.get("as_of"),
        "unit": card.get("unit"),
        "source_primary": card.get("source_primary"),
        "source_url": card.get("source_url"),
        "source_quality": card.get("source_quality"),
        "source_note": card.get("source_note"),
        "first_seen_at": previous.get("first_seen_at", now_iso) if previous else now_iso,
        "last_seen_at": now_iso,
        "last_changed_at": changed_at,
    }


def update_indicator_memory(
    cards: list[dict[str, Any]],
    now: datetime,
    previous_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    memory = previous_memory if previous_memory is not None else load_indicator_memory()
    states = memory.setdefault("states", {})
    events = memory.setdefault("events", [])
    active_keys = {str(card["key"]) for card in cards}
    for key in list(states):
        if key not in active_keys:
            states.pop(key, None)
    events = [event for event in events if str(event.get("key")) in active_keys]
    memory["events"] = events
    now_iso = now.isoformat()
    bootstrap = not states

    for card in cards:
        if card.get("value") is None:
            continue
        key = str(card["key"])
        previous = states.get(key)
        current = memory_state_from_card(card, now_iso, previous)

        if previous is not None and not values_equal(previous.get("value"), card["value"]):
            current["last_changed_at"] = now_iso
            old_value = previous.get("value")
            new_value = card["value"]
            same_basis = str(previous.get("unit") or "") == str(card.get("unit") or "")
            absolute_change = None
            percent_change = None
            if same_basis and isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)):
                absolute_change = round(float(new_value) - float(old_value), 8)
                if float(old_value) != 0:
                    percent_change = round(absolute_change / abs(float(old_value)) * 100, 6)
            events.append(
                {
                    "id": f"{key}:{now_iso}",
                    "key": key,
                    "name_vi": card.get("name_vi"),
                    "event_type": "change" if same_basis else "measurement_basis_change",
                    "detected_at": now_iso,
                    "previous_value": old_value,
                    "current_value": new_value,
                    "absolute_change": absolute_change,
                    "percent_change": percent_change,
                    "previous_unit": previous.get("unit"),
                    "unit": card.get("unit"),
                    "as_of": card.get("as_of"),
                    "source_primary": card.get("source_primary"),
                    "source_url": card.get("source_url"),
                    "source_quality": card.get("source_quality"),
                    "source_note": card.get("source_note"),
                    "ai_status": "pending",
                }
            )
        elif previous is None and not bootstrap:
            events.append(
                {
                    "id": f"{key}:{now_iso}",
                    "key": key,
                    "name_vi": card.get("name_vi"),
                    "event_type": "new_data",
                    "detected_at": now_iso,
                    "previous_value": None,
                    "current_value": card["value"],
                    "absolute_change": None,
                    "percent_change": None,
                    "unit": card.get("unit"),
                    "as_of": card.get("as_of"),
                    "source_primary": card.get("source_primary"),
                    "source_url": card.get("source_url"),
                    "source_quality": card.get("source_quality"),
                    "source_note": card.get("source_note"),
                    "ai_status": "pending",
                }
            )
        states[key] = current

    memory["events"] = events[-MEMORY_EVENT_LIMIT:]
    memory["updated_at"] = now_iso
    memory["version"] = 1
    return memory


def load_previous_gemini_analysis() -> dict[str, Any] | None:
    if not GEMINI_ANALYSIS_FILE.exists():
        return None
    try:
        data = json.loads(GEMINI_ANALYSIS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def analyze_indicator_changes(
    memory: dict[str, Any],
    cards: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    pending = [event for event in memory.get("events", []) if event.get("ai_status") == "pending"]
    model = os.environ.get("GEMINI_MODEL") or "models/gemini-3-flash-preview"
    if not pending:
        previous = load_previous_gemini_analysis()
        if previous and previous.get("status") == "success":
            return previous
        return {
            "status": "no_change",
            "model": model,
            "generated_at": now.isoformat(),
            "event_count": 0,
            "analysis_vi": "Chua phat hien bien dong moi de phan tich.",
        }

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "status": "waiting_for_api_key",
            "model": model,
            "generated_at": now.isoformat(),
            "event_count": len(pending),
            "analysis_vi": "Da luu bien dong; dang cho GEMINI_API_KEY de phan tich.",
        }

    selected = pending[:GEMINI_EVENT_BATCH_LIMIT]
    context = [
        {
            "key": card["key"],
            "name_vi": card["name_vi"],
            "value": card["value"],
            "unit": card["unit"],
            "as_of": card.get("as_of"),
            "source": card.get("source_primary"),
            "source_url": card.get("source_url"),
            "source_quality": card.get("source_quality"),
            "source_note": card.get("source_note"),
        }
        for card in cards
        if card.get("value") is not None
    ]
    prompt = f"""
Bạn là chuyên gia phân tích kinh tế Việt Nam. Dữ liệu trong CHANGE_EVENTS và CURRENT_CONTEXT là
dữ liệu đầu vào đã được hệ thống thu thập; không được sửa số, tự điền số, hay biến kịch bản thành sự thật.
Hãy dùng Google Search để tìm nguyên nhân từ nguồn chính thống hoặc báo chí uy tín.

Chỉ trả về một JSON object hợp lệ, không dùng Markdown, theo đúng cấu trúc:
{{
  "summary_vi": "tóm tắt tối đa 80 từ",
  "portfolio_note": {{
    "stance": "một trong: TÍCH LŨY TỪNG PHẦN | NẮM GIỮ / TÍCH LŨY CHỌN LỌC | NẮM GIỮ / HẠN CHẾ MUA ĐUỔI | GIẢM TỶ TRỌNG RỦI RO",
    "reason_short": "lý do tối đa 60 từ",
    "base_case": "kịch bản cơ sở 1-3 tháng",
    "bull_case": "kịch bản tích cực 1-3 tháng",
    "bear_case": "kịch bản tiêu cực 1-3 tháng",
    "confidence": "LOW | MEDIUM | HIGH"
  }},
  "indicators": [
    {{
      "key": "đúng key đầu vào",
      "reason_short": "nguyên nhân ngắn; nói chưa đủ bằng chứng nếu cần",
      "forecast_1m": null,
      "forecast_3m": null,
      "unit": "đúng đơn vị đầu vào",
      "confidence": "LOW | MEDIUM | HIGH",
      "sources": ["URL trực tiếp"]
    }}
  ]
}}

forecast_1m và forecast_3m chỉ được là số hoặc null. Mỗi biến động phải nhắc đúng giá trị cũ/mới trong
reason_short, tách suy luận khỏi sự kiện, và có URL nguồn trực tiếp. Không có bằng chứng thì dùng null và
nói rõ "chưa đủ bằng chứng". Đây là phân tích tham khảo chung, không phải lời khuyên đầu tư cá nhân.

CHANGE_EVENTS:
{json.dumps(selected, ensure_ascii=False, indent=2)}

CURRENT_CONTEXT:
{json.dumps(context, ensure_ascii=False, indent=2)}
""".strip()

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        interaction = client.interactions.create(
            model=model,
            input=prompt,
            tools=[{"type": "google_search"}],
            generation_config={
                "temperature": 0.2,
                "max_output_tokens": 8192,
                "top_p": 0.95,
                "thinking_level": "high",
            },
        )
        output_text = getattr(interaction, "output_text", None)
        if not output_text:
            steps = getattr(interaction, "steps", [])
            output_text = str(steps[-1]) if steps else ""
        if not output_text.strip():
            raise ValueError("Gemini returned an empty analysis")
        analysis_data = extract_json_object(output_text)
        selected_ids = {event["id"] for event in selected}
        for event in memory.get("events", []):
            if event.get("id") in selected_ids:
                event["ai_status"] = "analyzed"
                event["ai_analyzed_at"] = now.isoformat()
        return {
            "status": "success",
            "model": model,
            "generated_at": now.isoformat(),
            "event_count": len(selected),
            "event_ids": sorted(selected_ids),
            "analysis_vi": output_text.strip(),
            "analysis_data": analysis_data or {},
        }
    except Exception as exc:
        safe_error = str(exc).replace(api_key, "[REDACTED]")[:500]
        return {
            "status": "error",
            "model": model,
            "generated_at": now.isoformat(),
            "event_count": len(selected),
            "error": safe_error,
            "analysis_vi": "Gemini chua phan tich duoc; bien dong van duoc giu trong hang doi.",
        }


def change_memory_summary(memory: dict[str, Any]) -> dict[str, int]:
    events = memory.get("events", [])
    return {
        "state_count": len(memory.get("states", {})),
        "event_count": len(events),
        "pending_ai_events": sum(1 for event in events if event.get("ai_status") == "pending"),
    }


def numeric_value(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def numeric_history_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for point in points:
        value = numeric_value(point.get("value"))
        if value is None:
            continue
        date = normalized_data_date(point.get("date"))
        by_date[date] = {**point, "date": date, "value": value}
    return [by_date[date] for date in sorted(by_date)]


def add_calendar_months(date: str, months: int) -> str:
    parsed = datetime.fromisoformat(normalized_data_date(date)).date()
    zero_based = parsed.year * 12 + parsed.month - 1 + months
    year, month_index = divmod(zero_based, 12)
    month = month_index + 1
    day = min(parsed.day, monthrange(year, month)[1])
    return parsed.replace(year=year, month=month, day=day).isoformat()


def forecast_precision(value: float) -> int:
    magnitude = abs(value)
    if magnitude >= 1000:
        return 0
    if magnitude >= 100:
        return 1
    return 2


def rounded_forecast(value: float, reference: float) -> float:
    return round(value, forecast_precision(reference))


def trend_forecast(points: list[dict[str, Any]]) -> dict[str, Any] | None:
    numeric_points = numeric_history_points(points)[-6:]
    if len(numeric_points) < 2:
        return None
    first = numeric_points[0]
    last = numeric_points[-1]
    first_date = datetime.fromisoformat(first["date"])
    last_date = datetime.fromisoformat(last["date"])
    elapsed_days = max(1, (last_date - first_date).days)
    current = float(last["value"])
    slope_per_day = (current - float(first["value"])) / elapsed_days
    recent_change = current - float(numeric_points[-2]["value"])
    scale = max(abs(current), abs(recent_change), 1.0)

    one_month_delta = max(-scale * 0.15, min(scale * 0.15, slope_per_day * 30 * 0.50))
    three_month_delta = max(-scale * 0.30, min(scale * 0.30, slope_per_day * 90 * 0.35))
    uncertainty = max(scale * 0.02, abs(recent_change) * 0.35)
    uncertainty = min(uncertainty, scale * 0.12)
    one_center = current + one_month_delta
    three_center = current + three_month_delta
    confidence = "MEDIUM" if len(numeric_points) >= 4 else "LOW"

    return {
        "forecast_1m": {
            "value": rounded_forecast(one_center, current),
            "low": rounded_forecast(one_center - uncertainty, current),
            "high": rounded_forecast(one_center + uncertainty, current),
            "as_of": add_calendar_months(last["date"], 1),
        },
        "forecast_3m": {
            "value": rounded_forecast(three_center, current),
            "low": rounded_forecast(three_center - uncertainty * 1.6, current),
            "high": rounded_forecast(three_center + uncertainty * 1.6, current),
            "as_of": add_calendar_months(last["date"], 3),
        },
        "confidence": confidence,
        "method": f"Ngoại suy xu hướng giảm chấn từ {len(numeric_points)} kỳ; không phải dự báo chính thức.",
        "observations": len(numeric_points),
    }


def gemini_indicator_lookup(gemini_analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    analysis_data = gemini_analysis.get("analysis_data", {})
    indicators = analysis_data.get("indicators", []) if isinstance(analysis_data, dict) else []
    return {
        str(item["key"]): item
        for item in indicators
        if isinstance(item, dict) and item.get("key")
    }


def observed_change_reason(card: dict[str, Any], previous: dict[str, Any] | None) -> str:
    if previous is None:
        note = str(card.get("source_note") or "").strip()
        return note or "Chưa có kỳ trước cùng định nghĩa để xác định biến động."
    old_value = numeric_value(previous.get("value"))
    current_value = numeric_value(card.get("value"))
    if old_value is not None and current_value is not None:
        delta = current_value - old_value
        if math.isclose(delta, 0.0, abs_tol=1e-12):
            movement = "không đổi"
        else:
            movement = f"{'tăng' if delta > 0 else 'giảm'} {abs(delta):g} {card.get('unit') or ''}".strip()
        return (
            f"Giá trị {movement} so với kỳ {previous.get('date', 'trước')}. "
            "Đây là biến động quan sát được; nguyên nhân cụ thể chờ Gemini xác minh nguồn."
        )
    if values_equal(previous.get("value"), card.get("value")):
        return "Nội dung công bố không đổi so với kỳ dữ liệu trước."
    return "Nội dung công bố đã thay đổi; dữ liệu dạng chữ nên không nội suy số."


def build_card_insights(
    cards: list[dict[str, Any]],
    history: dict[str, Any],
    gemini_analysis: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    series = history.get("series", {})
    ai_lookup = gemini_indicator_lookup(gemini_analysis)
    insights: dict[str, dict[str, Any]] = {}

    for card in cards:
        key = str(card["key"])
        points = list(series.get(key, []))
        current_date = normalized_data_date(card.get("as_of"))
        previous_candidates = [point for point in points if normalized_data_date(point.get("date")) < current_date]
        previous = previous_candidates[-1] if previous_candidates else None
        forecast = trend_forecast(points)
        ai_item = ai_lookup.get(key, {})
        reason = str(ai_item.get("reason_short") or "").strip() or observed_change_reason(card, previous)
        method = forecast.get("method") if forecast else "Cần ít nhất 2 kỳ số liệu cùng định nghĩa để dự báo."
        confidence = forecast.get("confidence") if forecast else "WAITING_FOR_DATA"
        forecast_1m = forecast.get("forecast_1m") if forecast else None
        forecast_3m = forecast.get("forecast_3m") if forecast else None

        ai_one = numeric_value(ai_item.get("forecast_1m"))
        ai_three = numeric_value(ai_item.get("forecast_3m"))
        if ai_one is not None or ai_three is not None:
            reference = numeric_value(card.get("value")) or 1.0
            spread = max(abs(reference) * 0.04, 0.01)
            if ai_one is not None:
                forecast_1m = {
                    "value": rounded_forecast(ai_one, reference),
                    "low": rounded_forecast(ai_one - spread, reference),
                    "high": rounded_forecast(ai_one + spread, reference),
                    "as_of": add_calendar_months(current_date, 1),
                }
            if ai_three is not None:
                forecast_3m = {
                    "value": rounded_forecast(ai_three, reference),
                    "low": rounded_forecast(ai_three - spread * 1.6, reference),
                    "high": rounded_forecast(ai_three + spread * 1.6, reference),
                    "as_of": add_calendar_months(current_date, 3),
                }
            confidence = str(ai_item.get("confidence") or "LOW").upper()
            method = "Gemini phân tích kèm Google Search; khoảng số là biên tham khảo của hệ thống."

        change = None
        old_numeric = numeric_value(previous.get("value")) if previous else None
        current_numeric = numeric_value(card.get("value"))
        if old_numeric is not None and current_numeric is not None:
            absolute = current_numeric - old_numeric
            change = {
                "absolute": round(absolute, 8),
                "percent": round(absolute / abs(old_numeric) * 100, 4) if old_numeric else None,
                "direction": "up" if absolute > 0 else "down" if absolute < 0 else "flat",
            }

        insights[key] = {
            "key": key,
            "name_vi": card["name_vi"],
            "current": {
                "value": card.get("value"),
                "unit": card.get("unit"),
                "date": current_date,
                "source": card.get("source_primary"),
                "source_url": card.get("source_url"),
            },
            "previous": previous,
            "change": change,
            "forecast_1m": forecast_1m,
            "forecast_3m": forecast_3m,
            "reason_short": reason,
            "confidence": confidence,
            "method": method,
            "ai_sources": ai_item.get("sources", []) if isinstance(ai_item.get("sources"), list) else [],
            "disclaimer": "Dự báo chỉ mang tính tham khảo, không phải số liệu chính thức hay khuyến nghị đầu tư.",
        }
    return insights


def build_macro_strategy(cards: list[dict[str, Any]], gemini_analysis: dict[str, Any]) -> dict[str, Any]:
    values = {str(card["key"]): numeric_value(card.get("value")) for card in cards}
    positive: list[str] = []
    negative: list[str] = []
    score = 0

    if values.get("pmi_manufacturing") is not None and values["pmi_manufacturing"] >= 50:
        score += 1
        positive.append(f"PMI {values['pmi_manufacturing']:g} trên ngưỡng mở rộng 50")
    if values.get("iip") is not None and values["iip"] > 0:
        score += 1
        positive.append(f"IIP tăng {values['iip']:g}% YoY")
    if values.get("retail") is not None and values["retail"] > 0:
        score += 1
        positive.append(f"Bán lẻ tăng {values['retail']:g}% YoY")
    if values.get("credit") is not None and 4 <= values["credit"] <= 12:
        score += 1
        positive.append(f"Tín dụng tăng {values['credit']:g}% YTD, hỗ trợ thanh khoản kinh tế")
    if values.get("cpi") is not None and values["cpi"] > 4.5:
        score -= 2
        negative.append(f"CPI {values['cpi']:g}% YoY tạo áp lực lãi suất")
    if values.get("trade_balance") is not None and values["trade_balance"] < 0:
        score -= 1
        negative.append(f"Cán cân thương mại âm {abs(values['trade_balance']):g} tỷ USD")
    if values.get("interbank_rate") is not None and values["interbank_rate"] > 6:
        score -= 1
        negative.append(f"Lãi suất liên ngân hàng {values['interbank_rate']:g}% còn cao")
    if values.get("dxy") is not None and values["dxy"] > 105:
        score -= 1
        negative.append(f"DXY {values['dxy']:g} gây áp lực lên tỷ giá")
    if values.get("us_10y_yield") is not None and values["us_10y_yield"] > 4.5:
        score -= 1
        negative.append(f"Lợi suất Mỹ 10Y {values['us_10y_yield']:g}% làm tăng chi phí vốn toàn cầu")
    if values.get("oil_prices") is not None and values["oil_prices"] > 90:
        score -= 1
        negative.append(f"Dầu {values['oil_prices']:g} USD/thùng làm tăng rủi ro chi phí")

    if score >= 3:
        stance = "TÍCH LŨY TỪNG PHẦN"
    elif score >= 0:
        stance = "NẮM GIỮ / TÍCH LŨY CHỌN LỌC"
    elif score > -3:
        stance = "NẮM GIỮ / HẠN CHẾ MUA ĐUỔI"
    else:
        stance = "GIẢM TỶ TRỌNG RỦI RO"

    strategy = {
        "stance": stance,
        "score": score,
        "reason_short": "Tín hiệu tăng trưởng vẫn hiện diện nhưng cần cân bằng với lạm phát, tỷ giá và chi phí vốn.",
        "positive_drivers": positive,
        "risk_drivers": negative,
        "base_case": "Giữ vị thế lõi và tích lũy từng phần ở doanh nghiệp cơ bản tốt; tránh mua đuổi khi thị trường tăng nóng.",
        "bull_case": "Nếu CPI hạ, thanh khoản dịu và PMI tiếp tục trên 50, có thể nâng dần tỷ trọng theo kỷ luật giải ngân.",
        "bear_case": "Nếu lạm phát, dầu hoặc lãi suất tăng lại, ưu tiên tiền mặt và giảm các vị thế nhạy với chi phí vốn.",
        "confidence": "LOW",
        "method": "Thang điểm vĩ mô minh bạch từ 9 chỉ báo; không dùng dữ liệu cá nhân hay định giá từng cổ phiếu.",
        "gemini_status": gemini_analysis.get("status", "unknown"),
        "disclaimer": "Quan điểm chung cho 1-3 tháng, chỉ để tham khảo. Không phải khuyến nghị mua/bán cho cá nhân hoặc mã cổ phiếu cụ thể.",
    }

    analysis_data = gemini_analysis.get("analysis_data", {})
    ai_note = analysis_data.get("portfolio_note") if isinstance(analysis_data, dict) else None
    if isinstance(ai_note, dict) and ai_note.get("stance"):
        for key in ("stance", "reason_short", "base_case", "bull_case", "bear_case", "confidence"):
            if ai_note.get(key):
                strategy[key] = ai_note[key]
        strategy["method"] = "Gemini phân tích biến động với Google Search, kết hợp các tín hiệu vĩ mô đang hiển thị."
    return strategy


def build_frontend_api(
    payload: dict[str, Any],
    history: dict[str, Any],
    memory: dict[str, Any],
    gemini_analysis: dict[str, Any],
    card_insights: dict[str, dict[str, Any]],
) -> None:
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
                "source_url": card.get("source_url"),
                "source_quality": card.get("source_quality"),
                "source_note": card.get("source_note"),
                "schedule": card["frequency"],
                "health": "OK",
                "vip": card["vip"],
                "updated_at": card["as_of"],
            }
        )
    (DOCS_API_DIR / "indicators.json").write_text(json.dumps(indicators, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DOCS_API_DIR / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DOCS_API_DIR / "indicator_memory.json").write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DOCS_API_DIR / "gemini_analysis.json").write_text(json.dumps(gemini_analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DOCS_API_DIR / "card_insights.json").write_text(json.dumps(card_insights, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_html(payload: dict[str, Any]) -> str:
    cards = payload["cards"]
    available = payload["coverage"]["available_cards"]
    total = payload["coverage"]["total_cards"]
    generated = html.escape(payload["generated_at_bkk"])
    tabs = []
    sections = []
    history = payload.get("history", {}).get("series", {})
    insights = payload.get("card_insights", {})
    strategy = payload.get("macro_strategy", {})
    for group_key, group_name in GROUPS.items():
        active = " active" if not tabs else ""
        group_cards = [c for c in cards if c["group"] == group_key]
        tabs.append(f'<button class="tab{active}" data-tab="{group_key}">{html.escape(group_name)} <span>{len(group_cards)}</span></button>')
        card_html = []
        visible_cards = [c for c in group_cards if c["value"] is not None]
        if not visible_cards:
            card_html.append('<div class="empty">Chưa có dữ liệu tự động chắc chắn cho nhóm này. Nguồn vẫn được kiểm tra mỗi sáng.</div>')
        for card in visible_cards:
            value_text = "Chờ nguồn" if card["value"] is None else html.escape(str(card["value"]))
            unit_text = html.escape(str(card.get("unit") or ""))
            value_class = " value-text" if isinstance(card["value"], str) else ""
            data_date = html.escape(str(card.get("as_of") or "không rõ"))
            source_note = html.escape(str(card.get("source_note") or ""))
            source_note_html = f'\n          <p class="source-note">{source_note}</p>' if source_note else ""
            status = "ok"
            vip = '<b class="vip">VIP</b>' if card.get("vip") else ""
            health = "OK" if card["value"] is not None else "CHECK"
            icon = "↗" if card["direction"] == "up" else "↘" if card["direction"] == "down" else "–"
            has_chart = len(numeric_history_points(history.get(card["key"], []))) >= 2
            chart_attr = f' data-key="{html.escape(card["key"])}" tabindex="0" role="button" aria-label="Xem số cũ, dự báo và lý do của {html.escape(card["name_vi"])}"'
            chart_class = " inspectable" + (" has-chart" if has_chart else "")
            chart_hint = '<span class="chart-hint">Bấm xem số cũ, dự báo &amp; lý do</span>'
            card_html.append(
                f"""
        <article class="card {status}{chart_class}"{chart_attr}>
          <div class="card-top">
            <span class="group-label">{html.escape(card["group_name"])}</span>
            <span class="health">{health}</span>
          </div>
          <h3>{html.escape(card["name_vi"])} {vip}</h3>
          <p class="value{value_class}">{value_text}<span class="value-unit">{unit_text}</span></p>
          <p class="change"><span>{icon}</span>{html.escape(card_change_label(card))}</p>
          {chart_hint}
          <p class="data-date">Ngày dữ liệu: <strong>{data_date}</strong></p>
          <p class="source">Nguồn: <a href="{html.escape(card["source_url"])}">{html.escape(card["source_primary"])}</a></p>{source_note_html}
        </article>"""
            )
        sections.append(f'<section id="{group_key}" class="panel{active}">' + "\n".join(card_html) + "</section>")

    tabs.append('<button class="tab" data-tab="strategy">Ghi chú 1–3 tháng</button>')
    positive_items = "".join(f"<li>{html.escape(str(item))}</li>" for item in strategy.get("positive_drivers", []))
    risk_items = "".join(f"<li>{html.escape(str(item))}</li>" for item in strategy.get("risk_drivers", []))
    if not positive_items:
        positive_items = "<li>Chưa có đủ tín hiệu thuận lợi rõ ràng.</li>"
    if not risk_items:
        risk_items = "<li>Chưa có tín hiệu rủi ro vượt ngưỡng của mô hình.</li>"
    gemini_status = html.escape(str(strategy.get("gemini_status") or "unknown"))
    strategy_html = f"""
      <section id="strategy" class="panel strategy-panel">
        <div class="strategy-head">
          <p class="group-label">Quan điểm tham khảo · 1–3 tháng</p>
          <h2>{html.escape(str(strategy.get('stance') or 'CHỜ DỮ LIỆU'))}</h2>
          <p>{html.escape(str(strategy.get('reason_short') or 'Chưa đủ dữ liệu để hình thành quan điểm.'))}</p>
          <div class="strategy-meta"><span>Điểm vĩ mô: {html.escape(str(strategy.get('score', 0)))}</span><span>Độ tin cậy: {html.escape(str(strategy.get('confidence', 'LOW')))}</span><span>Gemini: {gemini_status}</span></div>
        </div>
        <div class="driver-grid">
          <div><h3>Tín hiệu hỗ trợ</h3><ul>{positive_items}</ul></div>
          <div><h3>Rủi ro cần theo dõi</h3><ul>{risk_items}</ul></div>
        </div>
        <div class="scenario-grid">
          <div><span>Cơ sở</span><p>{html.escape(str(strategy.get('base_case') or ''))}</p></div>
          <div><span>Tích cực</span><p>{html.escape(str(strategy.get('bull_case') or ''))}</p></div>
          <div><span>Tiêu cực</span><p>{html.escape(str(strategy.get('bear_case') or ''))}</p></div>
        </div>
        <p class="strategy-method">{html.escape(str(strategy.get('method') or ''))}</p>
        <p class="disclaimer">{html.escape(str(strategy.get('disclaimer') or ''))}</p>
      </section>"""
    sections.append(strategy_html)

    source_rows = []
    for key, source in payload["source_health"].items():
        badge = "OK" if source["available"] else "CHECK"
        cls = "source-ok" if source["available"] else "source-warn"
        source_rows.append(
            f'<tr><td>{html.escape(source["name"])}</td><td>{html.escape(source["role"])}</td><td>{html.escape(source["release_lag"])}</td><td class="{cls}">{badge}</td></tr>'
        )
    source_table = "<table><thead><tr><th>Nguồn</th><th>Vai trò</th><th>Lịch</th><th>Health</th></tr></thead><tbody>" + "".join(source_rows) + "</tbody></table>"

    detail_data = {
        card["key"]: {
            "name": card["name_vi"],
            "unit": card["unit"],
            "points": numeric_history_points(history.get(card["key"], [])),
            "insight": insights.get(card["key"], {}),
        }
        for card in cards
    }
    detail_data_json = json.dumps(detail_data, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>vimo-VN Macro Monitor</title>
  <style>
    :root {{ color-scheme: dark; --ink:#f8fafc; --muted:#94a3b8; --line:rgba(255,255,255,.09); --ok:#34d399; --warn:#f59e0b; --bg:#090b10; --panel:#0f1119; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family: Arial, sans-serif; background:var(--bg); color:var(--ink); }}
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
    .card.inspectable {{ cursor:pointer; }}
    .card.inspectable:focus-visible {{ outline:2px solid #818cf8; outline-offset:3px; }}
    .card-top {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }}
    .group-label {{ font-size:11px; text-transform:uppercase; color:#94a3b8; font-weight:700; }}
    .health {{ color:#34d399; background:rgba(52,211,153,.1); border-radius:999px; padding:2px 7px; font-size:10px; font-weight:700; }}
    h3 {{ margin:14px 0 6px; font-size:15px; color:#cbd5e1; font-weight:600; }}
    .vip {{ display:inline-block; margin-left:6px; color:#fde68a; background:rgba(245,158,11,.12); border:1px solid rgba(245,158,11,.35); border-radius:4px; padding:1px 5px; font-size:10px; vertical-align:middle; }}
    .value {{ font-size:25px; font-weight:700; margin:0 0 8px; color:#fff; }}
    .value-text {{ font-size:18px; line-height:1.35; overflow-wrap:anywhere; }}
    .value-unit {{ display:block; margin-top:4px; color:#94a3b8; font-size:12px; font-weight:500; line-height:1.35; overflow-wrap:anywhere; }}
    .change {{ display:flex; gap:6px; align-items:flex-start; color:#94a3b8; font-size:12px; line-height:1.35; }}
    .chart-hint {{ display:inline-block; margin-top:10px; color:#a5b4fc; font-size:12px; }}
    .data-date {{ color:#94a3b8; font-size:12px; margin:12px 0 0; }}
    .data-date strong {{ color:#cbd5e1; }}
    .source {{ color:#64748b; font-size:12px; margin:6px 0 0; }}
    .source-note {{ color:#64748b; font-size:11px; margin:6px 0 0; }}
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
    .strategy-panel.active {{ display:block; }}
    .strategy-head {{ border-top:1px solid var(--line); border-bottom:1px solid var(--line); padding:24px 0; }}
    .strategy-head h2 {{ margin:8px 0; font-size:28px; color:#fff; }}
    .strategy-head > p:not(.group-label) {{ max-width:760px; color:#cbd5e1; }}
    .strategy-meta {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }}
    .strategy-meta span {{ border:1px solid var(--line); border-radius:6px; padding:7px 9px; color:#a5b4fc; font-size:12px; }}
    .driver-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:28px; padding:24px 0; border-bottom:1px solid var(--line); }}
    .driver-grid h3 {{ margin:0 0 10px; color:#e2e8f0; }}
    .driver-grid ul {{ margin:0; padding-left:20px; color:#cbd5e1; }}
    .driver-grid li {{ margin:8px 0; line-height:1.45; }}
    .scenario-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px; margin:24px 0; background:var(--line); border:1px solid var(--line); }}
    .scenario-grid > div {{ background:var(--bg); padding:16px; }}
    .scenario-grid span {{ color:#a5b4fc; font-size:11px; font-weight:700; text-transform:uppercase; }}
    .scenario-grid p {{ color:#cbd5e1; margin-bottom:0; }}
    .strategy-method {{ color:#94a3b8; font-size:12px; }}
    .disclaimer {{ color:#fbbf24; border-left:3px solid #f59e0b; padding:10px 12px; background:rgba(245,158,11,.06); font-size:12px; }}
    .modal {{ position:fixed; inset:0; display:none; align-items:center; justify-content:center; background:rgba(0,0,0,.72); padding:18px; z-index:50; }}
    .modal.open {{ display:flex; }}
    .modal-box {{ width:min(860px,100%); max-height:calc(100vh - 36px); overflow:auto; background:#0f1119; border:1px solid var(--line); border-radius:8px; padding:18px; box-shadow:0 22px 80px rgba(0,0,0,.45); }}
    .modal-head {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:12px; }}
    .modal-title {{ margin:0; font-size:18px; }}
    .modal-close {{ background:rgba(255,255,255,.06); color:#fff; border:1px solid var(--line); border-radius:6px; padding:7px 10px; cursor:pointer; }}
    .detail-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border:1px solid var(--line); background:var(--line); gap:1px; }}
    .detail-stat {{ background:#0f1119; padding:13px; min-width:0; }}
    .detail-stat span {{ display:block; color:#64748b; font-size:11px; margin-bottom:6px; }}
    .detail-stat strong {{ display:block; color:#fff; font-size:17px; overflow-wrap:anywhere; }}
    .detail-stat small {{ display:block; color:#94a3b8; margin-top:5px; overflow-wrap:anywhere; }}
    .reason-block {{ margin:16px 0; padding:14px 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }}
    .reason-block h3 {{ margin:0 0 7px; color:#e2e8f0; }}
    .reason-block p {{ margin:0; color:#cbd5e1; }}
    .forecast-note {{ color:#fbbf24; font-size:12px; }}
    .chart-wrap[hidden] {{ display:none; }}
    .chart-svg {{ width:100%; height:300px; display:block; background:rgba(255,255,255,.025); border:1px solid var(--line); border-radius:8px; }}
    .chart-meta {{ color:#94a3b8; font-size:12px; margin-top:10px; }}
    @media (max-width:720px) {{ .summary {{ grid-template-columns:1fr; }} h1 {{ font-size:23px; }} .detail-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .driver-grid,.scenario-grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <div class="eyebrow"><span class="pill">Báo cáo vĩ mô Việt Nam</span><span class="meta">Cập nhật: {generated}</span></div>
      <h1>Tình hình Kinh tế · Tiền tệ · Tài chính</h1>
      <p class="meta">Tự động quét mỗi sáng qua GitHub Actions. Khi nguồn mới chưa đọc được, card dùng số gần nhất đã xác minh và ghi rõ ngày dữ liệu.</p>
      <div class="summary">
        <div class="metric"><strong>{available}/{total}</strong><span>card có dữ liệu có nguồn</span></div>
        <div class="metric"><strong>{payload["coverage"]["source_count"]}</strong><span>nguồn theo dõi</span></div>
        <div class="metric"><strong>{html.escape(payload["status"])}</strong><span>trạng thái pipeline</span></div>
      </div>
    </div>
  </header>
  <nav class="wrap">{"".join(tabs)}</nav>
  <main class="wrap">{"".join(sections)}<section class="sources"><h2>Nguồn độc lập đang theo dõi</h2>{source_table}</section></main>
  <div class="modal" id="chartModal" aria-hidden="true">
    <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="chartTitle">
      <div class="modal-head">
        <div>
          <p class="group-label">Số cũ · dự báo tham khảo · lý do</p>
          <h2 class="modal-title" id="chartTitle">Chi tiết chỉ số</h2>
        </div>
        <button class="modal-close" id="chartClose" type="button" title="Đóng" aria-label="Đóng">×</button>
      </div>
      <div class="detail-grid">
        <div class="detail-stat"><span>Hiện tại</span><strong id="detailCurrent">—</strong><small id="detailCurrentDate"></small></div>
        <div class="detail-stat"><span>Kỳ trước</span><strong id="detailPrevious">—</strong><small id="detailPreviousDate"></small></div>
        <div class="detail-stat"><span>Dự báo +1 tháng</span><strong id="detailForecast1">—</strong><small id="detailForecast1Range"></small></div>
        <div class="detail-stat"><span>Dự báo +3 tháng</span><strong id="detailForecast3">—</strong><small id="detailForecast3Range"></small></div>
      </div>
      <div class="reason-block"><h3>Lý do ngắn</h3><p id="detailReason"></p></div>
      <p class="chart-meta" id="detailMethod"></p>
      <p class="forecast-note" id="detailDisclaimer"></p>
      <div class="chart-wrap" id="chartWrap">
        <svg class="chart-svg" id="chartSvg" viewBox="0 0 720 300" role="img"></svg>
        <p class="chart-meta" id="chartMeta"></p>
      </div>
    </div>
  </div>
  <script>
    const detailData = {detail_data_json};
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
    const chartWrap = document.getElementById('chartWrap');
    const currentEl = document.getElementById('detailCurrent');
    const currentDateEl = document.getElementById('detailCurrentDate');
    const previousEl = document.getElementById('detailPrevious');
    const previousDateEl = document.getElementById('detailPreviousDate');
    const forecast1El = document.getElementById('detailForecast1');
    const forecast1RangeEl = document.getElementById('detailForecast1Range');
    const forecast3El = document.getElementById('detailForecast3');
    const forecast3RangeEl = document.getElementById('detailForecast3Range');
    const reasonEl = document.getElementById('detailReason');
    const methodEl = document.getElementById('detailMethod');
    const disclaimerEl = document.getElementById('detailDisclaimer');
    let lastFocused = null;

    function formatValue(value, unit) {{
      if (value === null || value === undefined || value === '') return 'Chưa có';
      const shown = typeof value === 'number'
        ? new Intl.NumberFormat('vi-VN', {{ maximumFractionDigits: 3 }}).format(value)
        : String(value);
      return `${{shown}}${{unit ? ` ${{unit}}` : ''}}`;
    }}

    function forecastRange(forecast, unit) {{
      if (!forecast) return 'Cần ít nhất 2 kỳ số liệu';
      return `${{formatValue(forecast.low, unit)}} – ${{formatValue(forecast.high, unit)}} · đến ${{forecast.as_of}}`;
    }}

    function drawChart(item) {{
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
      meta.textContent = `${{pts.length}} điểm · mới nhất: ${{last.value}} ${{item.unit || ''}} · từ ${{first.date.slice(0,10)}} đến ${{last.date.slice(0,10)}}`;
    }}

    function openDetail(key, trigger) {{
      const item = detailData[key];
      if (!item) return;
      const insight = item.insight || {{}};
      const current = insight.current || {{}};
      const previous = insight.previous;
      title.textContent = item.name;
      currentEl.textContent = formatValue(current.value, current.unit || item.unit);
      currentDateEl.textContent = current.date ? `Ngày dữ liệu: ${{current.date}}` : '';
      previousEl.textContent = previous ? formatValue(previous.value, previous.unit || item.unit) : 'Chưa có kỳ so sánh';
      previousDateEl.textContent = previous && previous.date ? `Ngày dữ liệu: ${{previous.date.slice(0, 10)}}` : 'Không tạo số thay thế';
      forecast1El.textContent = insight.forecast_1m ? formatValue(insight.forecast_1m.value, item.unit) : 'Chưa đủ dữ liệu';
      forecast1RangeEl.textContent = forecastRange(insight.forecast_1m, item.unit);
      forecast3El.textContent = insight.forecast_3m ? formatValue(insight.forecast_3m.value, item.unit) : 'Chưa đủ dữ liệu';
      forecast3RangeEl.textContent = forecastRange(insight.forecast_3m, item.unit);
      reasonEl.textContent = insight.reason_short || 'Chưa có phân tích nguyên nhân.';
      methodEl.textContent = `${{insight.method || ''}} Độ tin cậy: ${{insight.confidence || 'WAITING_FOR_DATA'}}.`;
      disclaimerEl.textContent = insight.disclaimer || 'Dự báo chỉ mang tính tham khảo.';
      if (item.points && item.points.length >= 2) {{
        chartWrap.hidden = false;
        drawChart(item);
      }} else {{
        chartWrap.hidden = true;
        svg.innerHTML = '';
        meta.textContent = '';
      }}
      lastFocused = trigger || document.activeElement;
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      closeBtn.focus();
    }}
    document.querySelectorAll('.card.inspectable').forEach(card => {{
      card.addEventListener('click', event => {{
        if (!event.target.closest('a')) openDetail(card.dataset.key, card);
      }});
      card.addEventListener('keydown', event => {{
        if (event.key === 'Enter' || event.key === ' ') {{
          event.preventDefault();
          openDetail(card.dataset.key, card);
        }}
      }});
    }});
    closeBtn.addEventListener('click', () => {{
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      if (lastFocused) lastFocused.focus();
    }});
    modal.addEventListener('click', event => {{ if (event.target === modal) closeBtn.click(); }});
    document.addEventListener('keydown', event => {{ if (event.key === 'Escape' && modal.classList.contains('open')) closeBtn.click(); }});
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
    memory = update_indicator_memory(cards, now)
    gemini_analysis = analyze_indicator_changes(memory, cards, now)
    card_insights = build_card_insights(cards, history, gemini_analysis)
    macro_strategy = build_macro_strategy(cards, gemini_analysis)
    memory_summary = change_memory_summary(memory)
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
            "note": "Priority is live parser, then dated cache, then sourced verified baseline. Values are never guessed.",
        },
        "change_memory": memory_summary,
        "gemini_analysis": {
            "status": gemini_analysis["status"],
            "model": gemini_analysis["model"],
            "generated_at": gemini_analysis["generated_at"],
            "event_count": gemini_analysis["event_count"],
            "api_url": "api/gemini_analysis.json",
        },
        "card_insights": card_insights,
        "macro_strategy": macro_strategy,
        "source_health": sources,
        "history": history,
        "cards": cards,
    }

    (OUTPUT_DIR / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MEMORY_FILE.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    GEMINI_ANALYSIS_FILE.write_text(json.dumps(gemini_analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    build_frontend_api(payload, history, memory, gemini_analysis, card_insights)
    (DOCS_DIR / "index.html").write_text(render_html(payload), encoding="utf-8")

    vip_available = payload["coverage"]["vip_available"]
    vip_cards = payload["coverage"]["vip_cards"]
    summary = (
        f"vimo-VN updated: {available}/{len(cards)} cards have observed values; "
        f"VIP {vip_available}/{vip_cards}; Gemini {gemini_analysis['status']}; "
        f"pending changes {memory_summary['pending_ai_events']}."
    )
    (OUTPUT_DIR / "telegram_summary.txt").write_text(summary + "\n", encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"vimo-VN pipeline failed: {exc}", file=sys.stderr)
        raise

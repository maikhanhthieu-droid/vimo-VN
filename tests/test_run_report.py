import importlib.util
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_report.py"
SPEC = importlib.util.spec_from_file_location("run_report", MODULE_PATH)
assert SPEC and SPEC.loader
run_report = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run_report
SPEC.loader.exec_module(run_report)


class RunReportTests(unittest.TestCase):
    def test_number_parser_supports_vietnamese_and_international_formats(self) -> None:
        self.assertEqual(run_report.parse_vietnamese_number("4,69"), 4.69)
        self.assertEqual(run_report.parse_vietnamese_number("51.8"), 51.8)
        self.assertEqual(run_report.parse_vietnamese_number("185,401"), 185401.0)
        self.assertEqual(run_report.parse_vietnamese_number("185.401,5"), 185401.5)

    def test_cpi_yoy_prefers_monthly_value(self) -> None:
        text = (
            "Chỉ số giá tiêu dùng (CPI) tháng 6 tăng 0,48% so với tháng trước; "
            "tăng 3,57% so với cùng kỳ năm trước. "
            "Bình quân sáu tháng, CPI tăng 3,25% so với cùng kỳ năm trước."
        )
        self.assertEqual(run_report.first_cpi_yoy(text), 3.57)

    def test_cpi_yoy_preserves_decrease_direction(self) -> None:
        text = "Chỉ số giá tiêu dùng tháng này giảm 0,2% so với cùng kỳ năm trước."
        self.assertEqual(run_report.first_cpi_yoy(text), -0.2)

    def test_tls_fallback_is_limited_to_nso_certificate_errors(self) -> None:
        error = URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        self.assertTrue(run_report.should_retry_without_tls_verification("https://www.nso.gov.vn/", error))
        self.assertFalse(run_report.should_retry_without_tls_verification("https://vnba.org.vn/", error))

    def test_http_500_is_retryable_but_404_is_not(self) -> None:
        error_500 = HTTPError("https://example.com", 500, "server error", {}, None)
        error_404 = HTTPError("https://example.com", 404, "not found", {}, None)
        self.assertTrue(run_report.is_retryable_network_error(error_500))
        self.assertFalse(run_report.is_retryable_network_error(error_404))

    def test_pmi_search_parser_extracts_latest_value(self) -> None:
        page = """
        <a class="box-stream-link-with-avatar"
           href="/viet-nams-manufacturing-pmi-posts-518-in-june-111260703104749914.htm"
           title="Viet Nam's manufacturing PMI posts 51.8 in June"></a>
        <p class="box-stream-sapo">The Manufacturing Purchasing Managers' Index (PMI) posted 51.8 in June.</p>
        """
        original_fetch_text = run_report.fetch_text
        run_report.fetch_text = lambda _url: page
        try:
            result = run_report.fetch_pmi_snapshot()["pmi_manufacturing"]
        finally:
            run_report.fetch_text = original_fetch_text
        self.assertEqual(result["value"], 51.8)
        self.assertEqual(result["as_of"], "2026-07-03")

    def test_vbma_report_parser_extracts_numeric_cards(self) -> None:
        text = """
        Dữ liệu VBMA tổng hợp tính đến ngày 10/7/2026
        Kết tuần, lãi suất qua đêm ON giảm 139 đcb xuống mức 4.28%.
        Từ đầu năm 2026, Kho bạc Nhà nước đã huy động gần 185,401 tỷ đồng.
        Với tổng khối lượng là 100 tỷ đồng đến từ lĩnh vực logistics.
        BIẾN ĐỘNG LỢI SUẤT PHÒNG GIAO DỊCH VBMA
        10/7/2026 3.40% 3.50% 3.57% 4.20% 4.26% 4.40% 4.57% 4.64%
        """
        result = run_report.parse_vbma_report(text, "https://example.com/report.pdf")
        self.assertEqual(result["interbank_rate"]["value"], 4.28)
        self.assertEqual(result["govt_bond_yield"]["value"], 4.4)
        self.assertEqual(result["govt_bond_issuance"]["value"], 185.401)
        self.assertEqual(result["corporate_bond_issuance"]["value"], 0.1)

    def test_cached_official_values_are_marked_stale(self) -> None:
        payload = {
            "cards": [
                {
                    "key": "cpi",
                    "value": 4.69,
                    "as_of": "2026-07-03",
                    "source_primary": "NSO",
                    "source_quality": "AUTO_NSO_PARSE",
                    "source_url": "https://www.nso.gov.vn/report",
                },
                {"key": "stock_market", "value": 1804.24, "source_quality": "AUTO"},
            ]
        }
        result = run_report.cached_values_from_payload(payload)
        self.assertEqual(result["cpi"]["value"], 4.69)
        self.assertEqual(result["cpi"]["source_quality"], "STALE_CACHE_AUTO_NSO_PARSE")
        self.assertNotIn("stock_market", result)


if __name__ == "__main__":
    unittest.main()

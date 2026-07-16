import importlib.util
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from unittest.mock import patch


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
                    "key": "credit",
                    "value": 19.4,
                    "as_of": "2026-07-03",
                    "source_primary": "SBV",
                    "source_quality": "AUTO_OFFICIAL_PARSE",
                    "source_url": "https://www.sbv.gov.vn/report",
                    "frequency": "monthly",
                },
                {"key": "stock_market", "value": 1804.24, "source_quality": "AUTO", "frequency": "daily"},
            ]
        }
        result = run_report.cached_values_from_payload(payload)
        self.assertEqual(result["credit"]["value"], 19.4)
        self.assertEqual(result["credit"]["source_quality"], "STALE_CACHE_AUTO_OFFICIAL_PARSE")
        self.assertNotIn("stock_market", result)

    def test_memory_bootstrap_does_not_create_false_change_events(self) -> None:
        cards = [
            {
                "key": "cpi",
                "name_vi": "CPI",
                "value": 3.57,
                "unit": "% YoY",
                "as_of": "2026-06",
                "source_primary": "NSO",
                "source_url": "https://www.nso.gov.vn/",
                "source_quality": "AUTO_NSO_PARSE",
            }
        ]
        memory = run_report.update_indicator_memory(
            cards,
            datetime(2026, 7, 16, tzinfo=timezone.utc),
            {"version": 1, "states": {}, "events": []},
        )
        self.assertEqual(memory["events"], [])
        self.assertEqual(memory["states"]["cpi"]["value"], 3.57)

    def test_indicator_memory_can_restore_non_daily_values_only(self) -> None:
        memory = {
            "states": {
                "cpi": {
                    "value": 3.57,
                    "as_of": "2026-06",
                    "source_primary": "NSO",
                    "source_url": "https://www.nso.gov.vn/",
                    "source_quality": "AUTO_NSO_PARSE",
                },
                "stock_market": {
                    "value": 1804.24,
                    "source_primary": "Vietcap",
                    "source_quality": "AUTO",
                },
            }
        }
        cached = run_report.cached_values_from_memory(memory)
        self.assertEqual(cached["cpi"]["source_quality"], "STALE_CACHE_AUTO_NSO_PARSE")
        self.assertNotIn("stock_market", cached)

    def test_memory_records_numeric_change_and_keeps_it_pending_without_key(self) -> None:
        original = {
            "version": 1,
            "states": {
                "cpi": {
                    "value": 3.2,
                    "first_seen_at": "2026-06-01T00:00:00+00:00",
                    "last_changed_at": "2026-06-01T00:00:00+00:00",
                }
            },
            "events": [],
        }
        cards = [
            {
                "key": "cpi",
                "name_vi": "CPI",
                "value": 3.52,
                "unit": "% YoY",
                "as_of": "2026-07",
                "source_primary": "NSO",
                "source_url": "https://www.nso.gov.vn/",
                "source_quality": "AUTO_NSO_PARSE",
            }
        ]
        now = datetime(2026, 8, 3, tzinfo=timezone.utc)
        memory = run_report.update_indicator_memory(cards, now, original)
        event = memory["events"][0]
        self.assertEqual(event["event_type"], "change")
        self.assertAlmostEqual(event["absolute_change"], 0.32)
        self.assertEqual(event["percent_change"], 10.0)

        with patch.dict(os.environ, {}, clear=True):
            analysis = run_report.analyze_indicator_changes(memory, cards, now)
        self.assertEqual(analysis["status"], "waiting_for_api_key")
        self.assertEqual(memory["events"][0]["ai_status"], "pending")

    def test_history_is_preserved_while_an_official_source_is_unavailable(self) -> None:
        original_history_file = run_report.HISTORY_FILE
        with tempfile.TemporaryDirectory() as temporary_dir:
            run_report.HISTORY_FILE = Path(temporary_dir) / "history.json"
            run_report.HISTORY_FILE.write_text(
                '{"series":{"cpi":[{"date":"2026-06-03T00:00:00+00:00","value":3.57,"unit":"% YoY"}]}}',
                encoding="utf-8",
            )
            try:
                history = run_report.update_history(
                    [{"key": "cpi", "value": None, "frequency": "monthly", "vip": True}],
                    datetime(2026, 7, 16, tzinfo=timezone.utc),
                )
            finally:
                run_report.HISTORY_FILE = original_history_file
        self.assertEqual(history["series"]["cpi"][0]["value"], 3.57)

    def test_successful_gemini_analysis_marks_only_submitted_events(self) -> None:
        calls = []

        class FakeInteractions:
            def create(self, **kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(output_text="Phan tich co nguon.")

        class FakeClient:
            def __init__(self, api_key):
                self.api_key = api_key
                self.interactions = FakeInteractions()

        fake_google = types.ModuleType("google")
        fake_google.genai = types.SimpleNamespace(Client=FakeClient)
        memory = {
            "states": {},
            "events": [
                {
                    "id": "cpi:1",
                    "key": "cpi",
                    "event_type": "change",
                    "previous_value": 3.2,
                    "current_value": 3.5,
                    "ai_status": "pending",
                }
            ],
        }
        now = datetime(2026, 8, 3, tzinfo=timezone.utc)
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-secret"}, clear=True):
            with patch.dict(sys.modules, {"google": fake_google}):
                analysis = run_report.analyze_indicator_changes(memory, [], now)

        self.assertEqual(analysis["status"], "success")
        self.assertEqual(memory["events"][0]["ai_status"], "analyzed")
        self.assertEqual(calls[0]["tools"], [{"type": "google_search"}])
        self.assertEqual(calls[0]["generation_config"]["thinking_level"], "high")


if __name__ == "__main__":
    unittest.main()

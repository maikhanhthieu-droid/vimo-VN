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
    def test_scope_matches_original_41_indicators(self) -> None:
        keys = {spec.key for spec in run_report.SPECS}
        self.assertEqual(len(keys), 41)
        self.assertIn("agriculture_snapshot", keys)
        self.assertNotIn("global_equities", keys)
        self.assertNotIn("banking_liquidity", keys)

    def test_verified_baseline_has_a_source_and_date_for_every_entry(self) -> None:
        baselines = run_report.load_verified_baselines()
        self.assertEqual(len(baselines), 26)
        for key, baseline in baselines.items():
            with self.subTest(key=key):
                self.assertIsNotNone(baseline.get("value"))
                self.assertRegex(baseline.get("as_of", ""), r"^20\d{2}-\d{2}-\d{2}$")
                self.assertTrue(str(baseline.get("source_url", "")).startswith("https://"))
                self.assertTrue(str(baseline.get("source_quality", "")).startswith("VERIFIED_BASELINE_"))

    def test_verified_history_never_uses_an_unsourced_old_value(self) -> None:
        series = run_report.load_verified_history()
        self.assertGreaterEqual(len(series), 15)
        for key, points in series.items():
            with self.subTest(key=key):
                self.assertGreaterEqual(len(points), 1)
                for point in points:
                    self.assertIsNotNone(point.get("value"))
                    self.assertRegex(point.get("date", ""), r"^20\d{2}-\d{2}-\d{2}$")
                    self.assertTrue(str(point.get("source_url", "")).startswith("https://"))
                    self.assertTrue(str(point.get("source_quality", "")).startswith("VERIFIED_HISTORY_"))

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

    def test_nso_extended_parser_keeps_periods_units_and_trade_sign(self) -> None:
        text = " ".join(
            [
                "Tính chung sáu tháng đầu năm 2026, tổng mức bán lẻ hàng hóa và doanh thu dịch vụ tiêu dùng theo giá hiện hành ước đạt 3.889,5 nghìn tỷ đồng, tăng 12,9% so với cùng kỳ.",
                "Khách quốc tế đến Việt Nam sáu tháng đầu năm nay đạt 12,3 triệu lượt người, tăng 14,9%.",
                "Tính chung sáu tháng đầu năm 2026, kim ngạch xuất khẩu hàng hóa đạt 266,52 tỷ USD.",
                "Tính chung sáu tháng đầu năm 2026, kim ngạch nhập khẩu hàng hóa đạt 283,17 tỷ USD.",
                "Tính chung sáu tháng đầu năm 2026, cán cân thương mại hàng hóa nhập siêu 16,65 tỷ USD.",
                "Tổng vốn đầu tư nước ngoài đăng ký vào Việt Nam tính đến ngày 30/6/2026 đạt 34,65 tỷ USD.",
                "Vốn đầu tư trực tiếp nước ngoài thực hiện tại Việt Nam sáu tháng đầu năm 2026 ước đạt 13,03 tỷ USD.",
                "Tính chung sáu tháng đầu năm 2026, cả nước có gần 111,7 nghìn doanh nghiệp đăng ký thành lập mới.",
                "Số doanh nghiệp rút lui khỏi thị trường là 151,1 nghìn doanh nghiệp.",
                "Vốn khu vực Nhà nước đạt 508,3 nghìn tỷ đồng, chiếm 28,1% tổng vốn và tăng 12,5% so với cùng kỳ.",
                "Lũy kế tổng thu ngân sách Nhà nước sáu tháng đầu năm 2026 ước đạt 1.568,2 nghìn tỷ đồng.",
                "Tính đến thời điểm 26/6/2026, huy động vốn tăng 5,02%; tăng trưởng tín dụng của nền kinh tế đạt 7,41%.",
                "GDP sáu tháng đầu năm 2026 tăng 8,18%; khu vực nông, lâm nghiệp và thủy sản tăng 3,87%.",
                "Khu vực có vốn đầu tư nước ngoài (kể cả dầu thô) đạt 213,01 tỷ USD, tăng 26,0%, chiếm 79,9%.",
                "Hoa Kỳ là thị trường xuất khẩu lớn nhất đạt 86,5 tỷ USD. Trung Quốc là thị trường nhập khẩu lớn nhất đạt 115,2 tỷ USD.",
            ]
        )
        result = run_report.parse_nso_economic_report(text, "2026-06-30", "https://www.nso.gov.vn/report")
        self.assertEqual(result["retail"]["value"], 12.9)
        self.assertEqual(result["international_visitors"]["value"], 12.3)
        self.assertEqual(result["trade_balance"]["value"], -16.65)
        self.assertEqual(result["business_new"]["value"], 111700.0)
        self.assertEqual(result["business_exited"]["value"], 151100.0)
        self.assertEqual(result["credit"]["as_of"], "2026-06-26")
        self.assertEqual(result["trade_by_sector"]["value"], 79.9)
        self.assertEqual(result["trade_by_market"]["value"], "Mỹ XK 86.5; Trung Quốc NK 115.2")

    def test_nso_period_end_uses_the_report_period_not_publish_date(self) -> None:
        result = run_report.nso_report_period_end(
            "Thông cáo tình hình kinh tế quý II và sáu tháng đầu năm 2026",
            "2026-07-03T01:59:25",
        )
        self.assertEqual(result, "2026-06-30")

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
                    "unit": "% YoY",
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
        self.assertEqual(history["series"]["cpi"][-1]["value"], 3.57)
        self.assertIn(5.6, [point["value"] for point in history["series"]["cpi"]])

    def test_history_uses_observation_date_and_removes_future_month_bucket(self) -> None:
        original_history_file = run_report.HISTORY_FILE
        original_verified_history_file = run_report.VERIFIED_HISTORY_FILE
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            run_report.HISTORY_FILE = root / "history.json"
            run_report.VERIFIED_HISTORY_FILE = root / "verified_history.json"
            run_report.HISTORY_FILE.write_text(
                '{"series":{"cpi":[{"date":"2026-07-03T00:00:00+00:00","bucket":"2026-07","value":4.69,"unit":"% YoY"}]}}',
                encoding="utf-8",
            )
            run_report.VERIFIED_HISTORY_FILE.write_text('{"series":{}}', encoding="utf-8")
            try:
                history = run_report.update_history(
                    [
                        {
                            "key": "cpi",
                            "value": 4.69,
                            "unit": "% YoY",
                            "as_of": "2026-06-30",
                            "frequency": "monthly",
                            "vip": True,
                        },
                        {
                            "key": "deposit_rate",
                            "value": "5,9-6,0",
                            "unit": "%/năm",
                            "as_of": "2026-05-31",
                            "frequency": "monthly",
                            "vip": True,
                        },
                    ],
                    datetime(2026, 7, 16, tzinfo=timezone.utc),
                )
            finally:
                run_report.HISTORY_FILE = original_history_file
                run_report.VERIFIED_HISTORY_FILE = original_verified_history_file
        self.assertEqual(history["series"]["cpi"][0]["date"], "2026-06-30")
        self.assertEqual(history["series"]["cpi"][0]["bucket"], "2026-06")
        self.assertEqual(history["series"]["deposit_rate"][0]["value"], "5,9-6,0")

    def test_trend_forecast_requires_two_numeric_periods(self) -> None:
        one_point = [{"date": "2026-05-31", "value": 5.6}]
        two_points = [*one_point, {"date": "2026-06-30", "value": 4.69}]
        self.assertIsNone(run_report.trend_forecast(one_point))
        forecast = run_report.trend_forecast(two_points)
        self.assertIsNotNone(forecast)
        self.assertEqual(forecast["confidence"], "LOW")
        self.assertLess(forecast["forecast_1m"]["value"], 4.69)

    def test_macro_strategy_is_general_and_scenario_based(self) -> None:
        cards = [
            {"key": "pmi_manufacturing", "value": 51.8},
            {"key": "iip", "value": 10.8},
            {"key": "retail", "value": 12.9},
            {"key": "credit", "value": 7.41},
            {"key": "cpi", "value": 4.69},
            {"key": "trade_balance", "value": -16.65},
            {"key": "oil_prices", "value": 100.0},
        ]
        strategy = run_report.build_macro_strategy(cards, {"status": "waiting_for_api_key"})
        self.assertEqual(strategy["stance"], "NẮM GIỮ / TÍCH LŨY CHỌN LỌC")
        self.assertIn("không phải khuyến nghị", strategy["disclaimer"].lower())
        self.assertTrue(strategy["base_case"])

    def test_render_exposes_all_inspectable_cards_and_strategy_tab(self) -> None:
        cards = [
            {
                "key": "cpi",
                "name_vi": "CPI",
                "group": "real_economy",
                "group_name": "Kinh tế thực",
                "value": 4.69,
                "unit": "% YoY",
                "vip": True,
                "frequency": "monthly",
                "source_primary": "NSO",
                "source_url": "https://www.nso.gov.vn/",
                "source_quality": "AUTO",
                "source_note": None,
                "direction": "down",
                "as_of": "2026-06-30",
            },
            {
                "key": "pmi_manufacturing",
                "name_vi": "PMI sản xuất",
                "group": "real_economy",
                "group_name": "Kinh tế thực",
                "value": 51.8,
                "unit": "điểm",
                "vip": True,
                "frequency": "monthly",
                "source_primary": "VGP",
                "source_url": "https://en.baochinhphu.vn/",
                "source_quality": "AUTO",
                "source_note": None,
                "direction": "up",
                "as_of": "2026-07-03",
            },
        ]
        payload = {
            "cards": cards,
            "coverage": {"available_cards": 2, "total_cards": 2, "source_count": 2},
            "generated_at_bkk": "2026-07-16T21:00:00+07:00",
            "status": "ok",
            "source_health": {},
            "history": {"series": {"cpi": [{"date": "2026-05-31", "value": 5.6}, {"date": "2026-06-30", "value": 4.69}]}},
            "card_insights": {
                "cpi": {
                    "current": {"value": 4.69, "unit": "% YoY", "date": "2026-06-30"},
                    "previous": {"value": 5.6, "unit": "% YoY", "date": "2026-05-31"},
                    "forecast_1m": {"value": 4.24, "low": 3.92, "high": 4.55, "as_of": "2026-07-30"},
                    "forecast_3m": None,
                    "reason_short": "CPI giảm theo số liệu quan sát.",
                    "confidence": "LOW",
                    "method": "Test",
                    "disclaimer": "Tham khảo",
                },
                "pmi_manufacturing": {},
            },
            "macro_strategy": {
                "stance": "NẮM GIỮ",
                "reason_short": "Cân bằng.",
                "score": 0,
                "confidence": "LOW",
                "gemini_status": "waiting_for_api_key",
                "positive_drivers": [],
                "risk_drivers": [],
                "base_case": "Giữ.",
                "bull_case": "Tăng.",
                "bear_case": "Giảm.",
                "method": "Test",
                "disclaimer": "Tham khảo.",
            },
        }
        rendered = run_report.render_html(payload)
        self.assertEqual(rendered.count('class="card ok inspectable'), 2)
        self.assertIn('data-tab="strategy"', rendered)
        self.assertIn('id="detailForecast1"', rendered)
        self.assertIn("Số cũ · dự báo tham khảo · lý do", rendered)

    def test_successful_gemini_analysis_marks_only_submitted_events(self) -> None:
        calls = []

        class FakeInteractions:
            def create(self, **kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(
                    output_text=(
                        '{"summary_vi":"Phân tích có nguồn.",'
                        '"portfolio_note":{"stance":"NẮM GIỮ / TÍCH LŨY CHỌN LỌC",'
                        '"reason_short":"Cân bằng tăng trưởng và lạm phát.","base_case":"Giữ",'
                        '"bull_case":"Tăng", "bear_case":"Giảm", "confidence":"LOW"},'
                        '"indicators":[{"key":"cpi","reason_short":"CPI tăng theo dữ liệu.",'
                        '"forecast_1m":3.4,"forecast_3m":3.2,"unit":"% YoY",'
                        '"confidence":"LOW","sources":["https://www.nso.gov.vn/"]}]}'
                    )
                )

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
        self.assertEqual(analysis["analysis_data"]["indicators"][0]["forecast_1m"], 3.4)
        self.assertEqual(calls[0]["tools"], [{"type": "google_search"}])
        self.assertEqual(calls[0]["generation_config"]["thinking_level"], "high")


if __name__ == "__main__":
    unittest.main()

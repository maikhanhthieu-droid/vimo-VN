import json
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from scripts import forecast_consensus


def history(
    *,
    start: float,
    daily_change: float,
    count: int = 120,
) -> list[dict]:
    first = date(2026, 1, 1)
    return [
        {
            "date": (first + timedelta(days=index)).isoformat(),
            "value": start + daily_change * index,
        }
        for index in range(count)
    ]


class ForecastConsensusTests(unittest.TestCase):
    def test_single_mature_source_is_visible_but_explicitly_low_confidence(self) -> None:
        external = {
            "series": {
                "stock_market": [{
                    "provider": "Vietcap",
                    "source_kind": "observed_history",
                    "source_url": "https://trading.vietcap.com.vn/",
                    "points": history(start=1500, daily_change=0.5),
                }]
            },
            "official_forecasts": {},
        }
        result = forecast_consensus.build_forecast_consensus(
            "stock_market", 1560, external
        )
        self.assertEqual(result["forecast_status"], "SINGLE_SOURCE")
        self.assertEqual(result["confidence"], "LOW")
        self.assertIsNotNone(result["forecast_1m"])
        self.assertIn("NGHI NGỜ", result["warning"])
        self.assertEqual(result["source_count"], 1)

    def test_two_agreeing_sources_produce_consensus_with_provenance(self) -> None:
        external = {
            "series": {
                "oil_prices": [
                    {
                        "provider": "Yahoo Finance",
                        "source_kind": "observed_history",
                        "source_url": "https://finance.yahoo.com/quote/CL%3DF/history",
                        "points": history(start=70, daily_change=0.04),
                    },
                    {
                        "provider": "FRED",
                        "source_kind": "observed_history",
                        "source_url": "https://fred.stlouisfed.org/series/DCOILWTICO",
                        "points": history(start=70.2, daily_change=0.038),
                    },
                ]
            },
            "official_forecasts": {},
        }
        result = forecast_consensus.build_forecast_consensus(
            "oil_prices", 75, external
        )
        self.assertEqual(result["forecast_status"], "CONSENSUS")
        self.assertEqual(result["confidence"], "MEDIUM")
        self.assertIsNotNone(result["forecast_1m"])
        self.assertIsNotNone(result["forecast_3m"])
        self.assertEqual(
            {source["provider"] for source in result["forecast_sources"]},
            {"Yahoo Finance", "FRED"},
        )

    def test_disagreeing_sources_hide_consensus_numbers(self) -> None:
        external = {
            "series": {
                "oil_prices": [
                    {
                        "provider": "Source Up",
                        "source_kind": "observed_history",
                        "source_url": "https://example.com/up",
                        "points": history(start=50, daily_change=0.5),
                    },
                    {
                        "provider": "Source Down",
                        "source_kind": "observed_history",
                        "source_url": "https://example.com/down",
                        "points": history(start=110, daily_change=-0.5),
                    },
                ]
            },
            "official_forecasts": {},
        }
        result = forecast_consensus.build_forecast_consensus(
            "oil_prices", 80, external
        )
        self.assertEqual(result["forecast_status"], "DISAGREEMENT")
        self.assertIsNone(result["forecast_1m"])
        self.assertIsNone(result["forecast_3m"])
        self.assertIn("mâu thuẫn", result["warning"])

    def test_short_single_source_does_not_publish_a_number(self) -> None:
        external = {
            "series": {
                "dxy": [{
                    "provider": "Short API",
                    "source_kind": "observed_history",
                    "source_url": "https://example.com/short",
                    "points": history(start=100, daily_change=0.02, count=30),
                }]
            },
            "official_forecasts": {},
        }
        result = forecast_consensus.build_forecast_consensus("dxy", 101, external)
        self.assertEqual(result["forecast_status"], "INSUFFICIENT_SOURCES")
        self.assertIsNone(result["forecast_1m"])

    def test_provider_health_json_never_needs_a_secret_value(self) -> None:
        payload = {
            "providers": [
                {
                    "provider": "FRED",
                    "status": "not_configured",
                    "series": [],
                    "errors": {},
                    "key_required": True,
                }
            ]
        }
        encoded = json.dumps(payload)
        self.assertNotIn("api_key", encoded.casefold())
        self.assertNotIn("secret", encoded.casefold())

    def test_recent_cache_is_reused_but_keeps_original_fetch_time(self) -> None:
        now = datetime(2026, 7, 27, tzinfo=timezone.utc)
        cached = {
            "generated_at": "2026-07-25T00:00:00+00:00",
            "series": {
                "dxy": [{
                    "provider": "Yahoo Finance",
                    "source_kind": "observed_history",
                    "source_url": "https://finance.yahoo.com/quote/DX-Y.NYB/history",
                    "points": history(start=100, daily_change=0.02),
                    "fetched_at": "2026-07-25T00:00:00+00:00",
                    "cache_status": "live",
                }]
            },
            "official_forecasts": {},
        }
        live = {
            "series": {},
            "official_forecasts": {},
            "providers": [{
                "provider": "Yahoo Finance",
                "status": "unavailable",
                "series": [],
                "errors": {"dxy": "HTTP_429"},
                "key_required": False,
            }],
            "generated_at": now.isoformat(),
        }
        result = forecast_consensus._merge_cached_data(live, cached, now)
        source = result["series"]["dxy"][0]
        self.assertEqual(source["cache_status"], "stale")
        self.assertEqual(source["cache_age_days"], 2)
        self.assertEqual(source["fetched_at"], "2026-07-25T00:00:00+00:00")
        self.assertEqual(result["providers"][0]["status"], "stale_cache")

    def test_eia_shared_demo_is_explicit_when_personal_key_is_missing(self) -> None:
        now = datetime(2026, 7, 27, tzinfo=timezone.utc)
        fake_eia = {
            "provider": "EIA STEO",
            "source_kind": "official_forecast",
            "source_url": "https://www.eia.gov/outlooks/steo/",
            "as_of": "2026-07-27",
            "fetched_at": now.isoformat(),
            "cache_status": "live",
            "forecast_1m": {"value": 70, "low": 70, "high": 70, "as_of": "2026-08-01"},
            "forecast_3m": {"value": 68, "low": 68, "high": 68, "as_of": "2026-10-01"},
            "observations": 2,
            "span_days": 0,
            "method": "official",
            "official": True,
        }
        with (
            patch.object(
                forecast_consensus,
                "fetch_yahoo_history",
                side_effect=ValueError("unavailable"),
            ),
            patch.object(
                forecast_consensus,
                "fetch_vietcap_vnindex_history",
                side_effect=ValueError("unavailable"),
            ),
            patch.object(
                forecast_consensus,
                "fetch_eia_wti_forecast",
                return_value=fake_eia,
            ) as eia,
        ):
            result = forecast_consensus.collect_external_forecast_data(now)
        eia.assert_called_once_with("DEMO_KEY", now)
        provider = next(
            item for item in result["providers"] if item["provider"] == "EIA STEO"
        )
        self.assertEqual(provider["status"], "demo")
        self.assertEqual(provider["credential_mode"], "shared_demo")
        self.assertEqual(
            result["official_forecasts"]["oil_prices"][0]["credential_mode"],
            "shared_demo",
        )


if __name__ == "__main__":
    unittest.main()

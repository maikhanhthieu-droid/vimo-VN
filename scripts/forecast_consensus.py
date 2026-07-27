"""Deterministic 1-month/3-month forecast inputs and consensus.

This module deliberately separates observed facts from model output:

* public market APIs provide dated historical observations;
* EIA STEO can provide an official oil forecast when ``EIA_API_KEY`` exists;
* FRED can provide an independent official history when ``FRED_API_KEY`` exists;
* every numeric projection retains provider, URL, observation count and date;
* conflicting sources produce ``DISAGREEMENT`` and no displayed consensus.

No LLM is called here and no missing value is imputed.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


YAHOO_SERIES = {
    "fx_market_usd_vnd": "VND=X",
    "gold_world": "GC=F",
    "dxy": "DX-Y.NYB",
    "oil_prices": "CL=F",
    "us_10y_yield": "^TNX",
}

FRED_SERIES = {
    "gold_world": "GOLDAMGBD228NLBM",
    "oil_prices": "DCOILWTICO",
    "us_10y_yield": "DGS10",
}

RELATIVE_LIMITS = {
    "fx_market_usd_vnd": (0.03, 0.07),
    "stock_market": (0.12, 0.25),
    "gold_world": (0.12, 0.28),
    "dxy": (0.05, 0.12),
    "oil_prices": (0.15, 0.35),
}

LEVEL_KEYS = {"us_10y_yield"}
LEVEL_LIMITS = {"us_10y_yield": (0.75, 1.50)}
RELATIVE_DISAGREEMENT = {
    "fx_market_usd_vnd": (0.025, 0.06),
    "stock_market": (0.06, 0.15),
    "gold_world": (0.08, 0.18),
    "dxy": (0.035, 0.08),
    "oil_prices": (0.12, 0.25),
}
LEVEL_DISAGREEMENT = {"us_10y_yield": (0.45, 0.90)}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}


def _safe_error(exc: Exception) -> str:
    """Return a non-secret error category; never include request URLs."""

    if isinstance(exc, HTTPError):
        return f"HTTP_{exc.code}"
    if isinstance(exc, (TimeoutError, URLError)):
        return "NETWORK_ERROR"
    if isinstance(exc, (ValueError, TypeError, json.JSONDecodeError)):
        return "INVALID_RESPONSE"
    return type(exc).__name__.upper()


def _fetch_json(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> Any:
    request_headers = dict(DEFAULT_HEADERS)
    request_headers.update(headers or {})
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
        method = "POST"
    request = Request(url, data=data, headers=request_headers, method=method)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.4)
    assert last_error is not None
    raise last_error


def _point(date: str, value: Any) -> dict[str, Any] | None:
    try:
        parsed = datetime.fromisoformat(str(date)[:10]).date().isoformat()
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return {"date": parsed, "value": number}


def _dedupe_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for item in points:
        normalized = _point(item.get("date", ""), item.get("value"))
        if normalized is not None:
            by_date[normalized["date"]] = normalized
    return [by_date[key] for key in sorted(by_date)]


def fetch_yahoo_history(symbol: str) -> list[dict[str, Any]]:
    encoded = quote(symbol, safe="")
    clean_url = f"https://finance.yahoo.com/quote/{encoded}/history"
    for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{encoded}?range=6mo&interval=1d"
        try:
            payload = _fetch_json(url)
            result = payload.get("chart", {}).get("result", [])
            if not result:
                continue
            row = result[0]
            timestamps = row.get("timestamp", [])
            closes = row.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            points: list[dict[str, Any]] = []
            for stamp, close in zip(timestamps, closes):
                if close is None:
                    continue
                date = datetime.fromtimestamp(int(stamp), tz=timezone.utc).date().isoformat()
                normalized = _point(date, close)
                if normalized is not None:
                    points.append(normalized)
            if points:
                return _dedupe_points(points)
        except Exception:
            continue
    raise ValueError(f"Yahoo returned no usable history for {clean_url}")


def fetch_vietcap_vnindex_history(now: datetime) -> list[dict[str, Any]]:
    url = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
    payload = {
        "timeFrame": "ONE_DAY",
        "symbols": ["VNINDEX"],
        "to": int(now.timestamp()),
        "countBack": 180,
    }
    response = _fetch_json(
        url,
        payload=payload,
        headers={
            "Origin": "https://trading.vietcap.com.vn",
            "Referer": "https://trading.vietcap.com.vn/",
        },
    )
    if not isinstance(response, list) or not response:
        raise ValueError("Vietcap returned an empty response")
    row = response[0]
    points: list[dict[str, Any]] = []
    for stamp, close in zip(row.get("t", []), row.get("c", [])):
        if close is None:
            continue
        date = datetime.fromtimestamp(int(stamp), tz=timezone.utc).date().isoformat()
        normalized = _point(date, close)
        if normalized is not None:
            points.append(normalized)
    points = _dedupe_points(points)
    if not points:
        raise ValueError("Vietcap returned no usable VN-Index history")
    return points


def fetch_fred_history(series_id: str, api_key: str, now: datetime) -> list[dict[str, Any]]:
    start = (now.date() - timedelta(days=220)).isoformat()
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
        "sort_order": "asc",
    }
    # Never return or log this request URL because it contains api_key.
    payload = _fetch_json(
        "https://api.stlouisfed.org/fred/series/observations?" + urlencode(params)
    )
    points = [
        normalized
        for item in payload.get("observations", [])
        if (normalized := _point(item.get("date", ""), item.get("value"))) is not None
    ]
    points = _dedupe_points(points)
    if not points:
        raise ValueError("FRED returned no usable observations")
    return points


def _month_period(date: datetime, months: int) -> str:
    zero_based = date.year * 12 + date.month - 1 + months
    year, month_index = divmod(zero_based, 12)
    return f"{year:04d}-{month_index + 1:02d}"


def fetch_eia_wti_forecast(api_key: str, now: datetime) -> dict[str, Any]:
    one_period = _month_period(now, 1)
    three_period = _month_period(now, 3)
    params: list[tuple[str, str]] = [
        ("api_key", api_key),
        ("frequency", "monthly"),
        ("data[0]", "value"),
        ("facets[seriesId][]", "WTIPUUS"),
        ("start", one_period),
        ("end", three_period),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
        ("offset", "0"),
        ("length", "10"),
    ]
    # Never return or log this request URL because it contains api_key.
    payload = _fetch_json("https://api.eia.gov/v2/steo/data/?" + urlencode(params))
    values: dict[str, float] = {}
    for item in payload.get("response", {}).get("data", []):
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values[str(item.get("period"))] = value
    if one_period not in values or three_period not in values:
        raise ValueError("EIA STEO did not return both requested horizons")
    return {
        "provider": "EIA STEO",
        "source_kind": "official_forecast",
        "source_url": "https://www.eia.gov/outlooks/steo/",
        "as_of": now.date().isoformat(),
        "fetched_at": now.isoformat(),
        "cache_status": "live",
        "forecast_1m": {
            "value": values[one_period],
            "low": values[one_period],
            "high": values[one_period],
            "as_of": f"{one_period}-01",
        },
        "forecast_3m": {
            "value": values[three_period],
            "low": values[three_period],
            "high": values[three_period],
            "as_of": f"{three_period}-01",
        },
        "observations": 2,
        "span_days": 0,
        "method": "EIA Short-Term Energy Outlook monthly projection.",
        "official": True,
    }


def _cache_age_days(source: dict[str, Any], now: datetime, fallback: str = "") -> int | None:
    raw = str(source.get("fetched_at") or fallback or "")
    try:
        fetched = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return max(0, (now.astimezone(timezone.utc) - fetched.astimezone(timezone.utc)).days)


def _merge_cached_data(
    live: dict[str, Any],
    cached: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    if not isinstance(cached, dict):
        return live
    cached_generated_at = str(cached.get("generated_at") or "")
    recovered_by_provider: dict[str, set[str]] = {}

    for key, cached_sources in cached.get("series", {}).items():
        if not isinstance(cached_sources, list):
            continue
        current = live.setdefault("series", {}).setdefault(str(key), [])
        current_providers = {
            str(item.get("provider") or "").casefold()
            for item in current
            if isinstance(item, dict)
        }
        for cached_source in cached_sources:
            if not isinstance(cached_source, dict):
                continue
            provider = str(cached_source.get("provider") or "").strip()
            if not provider or provider.casefold() in current_providers:
                continue
            age = _cache_age_days(cached_source, now, cached_generated_at)
            if age is None or age > 7:
                continue
            recovered = dict(cached_source)
            recovered["cache_status"] = "stale"
            recovered["cache_age_days"] = age
            current.append(recovered)
            current_providers.add(provider.casefold())
            recovered_by_provider.setdefault(provider, set()).add(str(key))

    for key, cached_forecasts in cached.get("official_forecasts", {}).items():
        if not isinstance(cached_forecasts, list):
            continue
        current = live.setdefault("official_forecasts", {}).setdefault(str(key), [])
        current_providers = {
            str(item.get("provider") or "").casefold()
            for item in current
            if isinstance(item, dict)
        }
        for cached_forecast in cached_forecasts:
            if not isinstance(cached_forecast, dict):
                continue
            provider = str(cached_forecast.get("provider") or "").strip()
            if not provider or provider.casefold() in current_providers:
                continue
            age = _cache_age_days(cached_forecast, now, cached_generated_at)
            if age is None or age > 45:
                continue
            recovered = dict(cached_forecast)
            recovered["cache_status"] = "stale"
            recovered["cache_age_days"] = age
            current.append(recovered)
            current_providers.add(provider.casefold())
            recovered_by_provider.setdefault(provider, set()).add(str(key))

    health_by_provider = {
        str(item.get("provider") or ""): item
        for item in live.get("providers", [])
        if isinstance(item, dict)
    }
    for provider, keys in recovered_by_provider.items():
        health = health_by_provider.get(provider)
        if health is None:
            health = {
                "provider": provider,
                "status": "stale_cache",
                "series": [],
                "errors": {},
                "key_required": False,
            }
            live.setdefault("providers", []).append(health)
        if health.get("status") in {"unavailable", "not_configured"}:
            health["status"] = "stale_cache"
        health["series"] = sorted(set(health.get("series", [])) | keys)
    return live


def collect_external_forecast_data(
    now: datetime,
    *,
    fred_api_key: str = "",
    eia_api_key: str = "",
    cached_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch independent forecast inputs without making any provider mandatory."""

    series: dict[str, list[dict[str, Any]]] = {}
    official_forecasts: dict[str, list[dict[str, Any]]] = {}
    providers: list[dict[str, Any]] = []

    yahoo_ok: list[str] = []
    yahoo_errors: dict[str, str] = {}
    for key, symbol in YAHOO_SERIES.items():
        try:
            points = fetch_yahoo_history(symbol)
            if key == "us_10y_yield":
                for item in points:
                    if item["value"] > 20:
                        item["value"] /= 10
            series.setdefault(key, []).append(
                {
                    "provider": "Yahoo Finance",
                    "source_kind": "observed_history",
                    "source_url": f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/history",
                    "points": points,
                    "fetched_at": now.isoformat(),
                    "cache_status": "live",
                }
            )
            yahoo_ok.append(key)
        except Exception as exc:
            yahoo_errors[key] = _safe_error(exc)
    providers.append(
        {
            "provider": "Yahoo Finance",
            "status": "ok" if len(yahoo_ok) == len(YAHOO_SERIES) else "degraded" if yahoo_ok else "unavailable",
            "series": sorted(yahoo_ok),
            "errors": yahoo_errors,
            "key_required": False,
        }
    )

    try:
        points = fetch_vietcap_vnindex_history(now)
        series.setdefault("stock_market", []).append(
            {
                "provider": "Vietcap",
                "source_kind": "observed_history",
                "source_url": "https://trading.vietcap.com.vn/",
                "points": points,
                "fetched_at": now.isoformat(),
                "cache_status": "live",
            }
        )
        providers.append(
            {
                "provider": "Vietcap",
                "status": "ok",
                "series": ["stock_market"],
                "errors": {},
                "key_required": False,
            }
        )
    except Exception as exc:
        providers.append(
            {
                "provider": "Vietcap",
                "status": "unavailable",
                "series": [],
                "errors": {"stock_market": _safe_error(exc)},
                "key_required": False,
            }
        )

    if fred_api_key:
        fred_ok: list[str] = []
        fred_errors: dict[str, str] = {}
        for key, series_id in FRED_SERIES.items():
            try:
                points = fetch_fred_history(series_id, fred_api_key, now)
                series.setdefault(key, []).append(
                    {
                        "provider": "FRED",
                        "source_kind": "observed_history",
                        "source_url": f"https://fred.stlouisfed.org/series/{series_id}",
                        "points": points,
                        "fetched_at": now.isoformat(),
                        "cache_status": "live",
                    }
                )
                fred_ok.append(key)
            except Exception as exc:
                fred_errors[key] = _safe_error(exc)
        providers.append(
            {
                "provider": "FRED",
                "status": "ok" if len(fred_ok) == len(FRED_SERIES) else "degraded" if fred_ok else "unavailable",
                "series": sorted(fred_ok),
                "errors": fred_errors,
                "key_required": True,
            }
        )
    else:
        providers.append(
            {
                "provider": "FRED",
                "status": "not_configured",
                "series": [],
                "errors": {},
                "key_required": True,
            }
        )

    if eia_api_key:
        try:
            forecast = fetch_eia_wti_forecast(eia_api_key, now)
            official_forecasts.setdefault("oil_prices", []).append(forecast)
            providers.append(
                {
                    "provider": "EIA STEO",
                    "status": "ok",
                    "series": ["oil_prices"],
                    "errors": {},
                    "key_required": True,
                }
            )
        except Exception as exc:
            providers.append(
                {
                    "provider": "EIA STEO",
                    "status": "unavailable",
                    "series": [],
                    "errors": {"oil_prices": _safe_error(exc)},
                    "key_required": True,
                }
            )
    else:
        providers.append(
            {
                "provider": "EIA STEO",
                "status": "not_configured",
                "series": [],
                "errors": {},
                "key_required": True,
            }
        )

    live = {
        "series": series,
        "official_forecasts": official_forecasts,
        "providers": providers,
        "generated_at": now.isoformat(),
    }
    return _merge_cached_data(live, cached_data, now)


def _sample_points(points: list[dict[str, Any]], limit: int = 32) -> list[dict[str, Any]]:
    if len(points) <= limit:
        return points
    indexes = {
        round(index * (len(points) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [points[index] for index in sorted(indexes)]


def _precision(reference: float) -> int:
    if abs(reference) >= 1000:
        return 0
    if abs(reference) >= 100:
        return 1
    return 2


def _round(value: float, reference: float) -> float:
    return round(value, _precision(reference))


def _add_months(date: str, months: int) -> str:
    parsed = datetime.fromisoformat(date[:10]).date()
    zero_based = parsed.year * 12 + parsed.month - 1 + months
    year, month_index = divmod(zero_based, 12)
    month = month_index + 1
    day = min(parsed.day, monthrange(year, month)[1])
    return parsed.replace(year=year, month=month, day=day).isoformat()


def _source_projection(
    key: str,
    source: dict[str, Any],
    current_value: float,
) -> dict[str, Any] | None:
    points = _dedupe_points(list(source.get("points", [])))[-240:]
    if len(points) < 4:
        return None
    first_date = datetime.fromisoformat(points[0]["date"])
    last_date = datetime.fromisoformat(points[-1]["date"])
    span_days = (last_date - first_date).days
    if span_days < 20:
        return None

    relative = key not in LEVEL_KEYS
    if relative and any(float(item["value"]) <= 0 for item in points):
        return None
    sampled = _sample_points(points)
    transformed = [
        {
            "date": datetime.fromisoformat(item["date"]),
            "value": math.log(float(item["value"])) if relative else float(item["value"]),
        }
        for item in sampled
    ]
    slopes: list[float] = []
    for left_index, left in enumerate(transformed):
        for right in transformed[left_index + 1 :]:
            days = (right["date"] - left["date"]).days
            if days >= 3:
                slopes.append((right["value"] - left["value"]) / days)
    if not slopes:
        return None
    slope = statistics.median(slopes)

    daily_changes: list[float] = []
    all_transformed = [
        (
            datetime.fromisoformat(item["date"]),
            math.log(float(item["value"])) if relative else float(item["value"]),
        )
        for item in points
    ]
    for (left_date, left_value), (right_date, right_value) in zip(
        all_transformed, all_transformed[1:]
    ):
        days = (right_date - left_date).days
        if days > 0:
            daily_changes.append((right_value - left_value) / days)
    median_change = statistics.median(daily_changes) if daily_changes else 0.0
    mad = (
        statistics.median(abs(change - median_change) for change in daily_changes)
        if daily_changes
        else 0.0
    )

    provider_last = float(points[-1]["value"])
    one_cap, three_cap = (
        LEVEL_LIMITS.get(key, (1.0, 2.0))
        if not relative
        else RELATIVE_LIMITS.get(key, (0.08, 0.18))
    )

    def horizon(days: int, months: int, damping: float, cap: float) -> dict[str, Any]:
        raw_delta = slope * days * damping
        raw_delta = max(-cap, min(cap, raw_delta))
        if relative:
            provider_center = provider_last * math.exp(raw_delta)
            ratio = provider_center / provider_last
            center = current_value * ratio
            uncertainty_ratio = max(0.005, mad * math.sqrt(days) * 1.35, abs(raw_delta) * 0.22)
            uncertainty_ratio = min(cap * 0.75, uncertainty_ratio)
            low = center * math.exp(-uncertainty_ratio)
            high = center * math.exp(uncertainty_ratio)
        else:
            center = current_value + raw_delta
            uncertainty = max(0.04, mad * math.sqrt(days) * 1.35, abs(raw_delta) * 0.22)
            uncertainty = min(cap * 0.75, uncertainty)
            low = center - uncertainty
            high = center + uncertainty
        return {
            "value": _round(center, current_value),
            "low": _round(low, current_value),
            "high": _round(high, current_value),
            "as_of": _add_months(points[-1]["date"], months),
        }

    return {
        "provider": str(source.get("provider") or "unknown"),
        "source_kind": str(source.get("source_kind") or "observed_history"),
        "source_url": str(source.get("source_url") or ""),
        "as_of": points[-1]["date"],
        "fetched_at": source.get("fetched_at"),
        "cache_status": source.get("cache_status", "live"),
        "cache_age_days": source.get("cache_age_days", 0),
        "forecast_1m": horizon(30, 1, 0.35, one_cap),
        "forecast_3m": horizon(90, 3, 0.20, three_cap),
        "observations": len(points),
        "span_days": span_days,
        "method": "Damped robust median-slope projection from dated API history.",
        "official": False,
    }


def _member_mature(member: dict[str, Any]) -> bool:
    if member.get("official"):
        return True
    return int(member.get("observations") or 0) >= 60 and int(member.get("span_days") or 0) >= 90


def _median_forecast(
    members: list[dict[str, Any]],
    horizon: str,
    reference: float,
) -> dict[str, Any]:
    rows = [member[horizon] for member in members]
    values = [float(row["value"]) for row in rows]
    lows = [float(row.get("low", row["value"])) for row in rows]
    highs = [float(row.get("high", row["value"])) for row in rows]
    target_dates = sorted(str(row["as_of"]) for row in rows)
    return {
        "value": _round(statistics.median(values), reference),
        "low": _round(min(lows), reference),
        "high": _round(max(highs), reference),
        "as_of": target_dates[len(target_dates) // 2],
    }


def build_forecast_consensus(
    key: str,
    current_value: Any,
    external_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Build an auditable forecast; never manufacture a missing source."""

    try:
        current = float(current_value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(current):
        return None

    members: list[dict[str, Any]] = []
    for source in external_data.get("series", {}).get(key, []):
        projection = _source_projection(key, source, current)
        if projection is not None:
            members.append(projection)
    members.extend(external_data.get("official_forecasts", {}).get(key, []))

    unique: dict[str, dict[str, Any]] = {}
    for member in members:
        provider = str(member.get("provider") or "").strip()
        if provider:
            unique[provider.casefold()] = member
    members = list(unique.values())

    base = {
        "forecast_status": "INSUFFICIENT_SOURCES",
        "confidence": "WAITING_FOR_DATA",
        "forecast_1m": None,
        "forecast_3m": None,
        "forecast_sources": members,
        "source_count": len(members),
        "method": "Không đủ chuỗi API có ngày và định nghĩa phù hợp để dự báo.",
        "warning": "Không điền số thay thế.",
    }
    if not members:
        return base

    if len(members) == 1:
        member = members[0]
        if not _member_mature(member):
            base["confidence"] = "LOW"
            base["method"] = "Nguồn duy nhất chưa đủ tối thiểu 60 quan sát và 90 ngày."
            return base
        return {
            **base,
            "forecast_status": "SINGLE_SOURCE",
            "confidence": "LOW",
            "forecast_1m": member["forecast_1m"],
            "forecast_3m": member["forecast_3m"],
            "method": (
                "Ngoại suy giảm chấn từ một nguồn API có lịch sử đủ dài; "
                "chưa có nguồn độc lập để xác nhận."
            ),
            "warning": "NGHI NGỜ: chỉ có một nguồn API; không phải dự báo đồng thuận.",
        }

    one_values = [float(member["forecast_1m"]["value"]) for member in members]
    three_values = [float(member["forecast_3m"]["value"]) for member in members]
    if key in LEVEL_KEYS:
        one_limit, three_limit = LEVEL_DISAGREEMENT.get(key, (0.5, 1.0))
        one_spread = max(one_values) - min(one_values)
        three_spread = max(three_values) - min(three_values)
    else:
        one_limit, three_limit = RELATIVE_DISAGREEMENT.get(key, (0.05, 0.12))
        denominator = max(abs(current), 1e-9)
        one_spread = (max(one_values) - min(one_values)) / denominator
        three_spread = (max(three_values) - min(three_values)) / denominator
    if one_spread > one_limit or three_spread > three_limit:
        return {
            **base,
            "forecast_status": "DISAGREEMENT",
            "confidence": "LOW",
            "method": "Các nguồn lệch nhau vượt ngưỡng; không công bố số consensus.",
            "warning": "NGHI NGỜ: nguồn API mâu thuẫn; xem từng nguồn để audit.",
        }

    official = any(bool(member.get("official")) for member in members)
    tight = one_spread <= one_limit / 2 and three_spread <= three_limit / 2
    return {
        **base,
        "forecast_status": "CONSENSUS",
        "confidence": "HIGH" if official and tight else "MEDIUM",
        "forecast_1m": _median_forecast(members, "forecast_1m", current),
        "forecast_3m": _median_forecast(members, "forecast_3m", current),
        "method": (
            f"Trung vị {len(members)} nguồn độc lập; biên bao phủ toàn bộ "
            "khoảng nguồn, không dùng AI tạo số."
        ),
        "warning": None,
    }

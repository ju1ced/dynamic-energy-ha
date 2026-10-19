from datetime import datetime, timedelta, timezone

from custom_components.dynamic_energy.forecast import (
    LoadProfile,
    build_load_profile,
    build_quarter_inputs,
    parse_pv_forecast,
    pv_kw_at,
)
from custom_components.dynamic_energy.planner import PriceSlot

UTC = timezone.utc


def test_pv_forecast_parses_half_hour_rows_and_rejects_bad_data():
    start = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    rows = [{"period_start": start + timedelta(minutes=30 * i), "pv_estimate": 1.0 + i} for i in range(3)]
    parsed = parse_pv_forecast([rows])
    assert [p[2] for p in parsed] == [1.0, 2.0, 3.0]
    assert parsed[0][1] - parsed[0][0] == timedelta(minutes=30)
    assert pv_kw_at(parsed, start + timedelta(minutes=45)) == 2.0
    iso = [{"period_start": (start + timedelta(minutes=30 * i)).isoformat(), "pv_estimate": 1.0} for i in range(2)]
    assert len(parse_pv_forecast([iso])) == 2
    assert parse_pv_forecast([[{"period_start": start, "pv_estimate": -1}]]) == []
    assert parse_pv_forecast([[{"period_start": "nope", "pv_estimate": 1}]]) == []
    assert parse_pv_forecast(["x"]) == []
    assert parse_pv_forecast([None, None]) == []


def test_load_profile_uses_median_per_weekday_class_and_hour():
    monday = datetime(2026, 10, 19, 8, tzinfo=UTC)  # weekday
    saturday = datetime(2026, 10, 24, 8, tzinfo=UTC)
    samples = [(monday, 1.0), (monday + timedelta(days=7), 3.0), (monday + timedelta(days=14), 2.0), (saturday, 5.0)]
    profile = build_load_profile(samples, fallback_kw=0.6)
    assert profile.kw(monday) == 2.0
    assert profile.kw(saturday) == 5.0
    assert profile.kw(monday.replace(hour=3)) == profile.kw(monday.replace(hour=3))  # falls back
    assert LoadProfile({}, 0.6).kw(monday) == 0.6


def test_quarter_inputs_cover_prices_from_now_with_pv_and_export_prices():
    base = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    imports = [PriceSlot(base + timedelta(minutes=15 * i), base + timedelta(minutes=15 * (i + 1)), 0.3) for i in range(8)]
    exports = [PriceSlot(s.start, s.end, 0.1) for s in imports]
    pv = parse_pv_forecast([[{"period_start": base, "pv_estimate": 2.0}, {"period_start": base + timedelta(minutes=30), "pv_estimate": 0.0}]])
    pv += [(base + timedelta(hours=1), base + timedelta(hours=2), 0.0)]
    now = base + timedelta(minutes=20)
    quarters, flags = build_quarter_inputs(imports, exports, now, LoadProfile({}, 0.8), pv, 0.02, lambda d: d)
    # the running quarter is planned as a whole
    assert quarters[0].start == base + timedelta(minutes=15) and quarters[0].end == base + timedelta(minutes=30)
    assert abs(quarters[0].hours - 0.25) < 1e-9
    assert quarters[0].base_net_kwh < 0  # 0.8 kW load - 2 kW PV
    assert quarters[0].export_rate == 0.1
    assert flags == []
    assert [q.start for q in quarters[1:]] == [base + timedelta(minutes=30 + 15 * i) for i in range(6)]


def test_quarter_inputs_flag_missing_forecasts_and_gaps():
    base = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    imports = [PriceSlot(base, base + timedelta(minutes=15), 0.3), PriceSlot(base + timedelta(minutes=30), base + timedelta(minutes=45), 0.3)]
    quarters, flags = build_quarter_inputs(imports, [], base, LoadProfile({}, 0.8), [], 0.02, lambda d: d)
    assert len(quarters) == 1
    assert {"GAP_IN_PRICES", "NO_PV_FORECAST", "NO_EXPORT_PRICES_FALLBACK_USED"} <= set(flags)
    assert build_quarter_inputs([], [], base, LoadProfile({}, 0.8), [], 0.02, lambda d: d)[1] == ["NO_IMPORT_PRICES"]

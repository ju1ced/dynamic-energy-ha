from datetime import datetime, timedelta, timezone

from custom_components.dynamic_energy.planner import (
    PlanSettings,
    PriceSlot,
    STATUS_INCOMPLETE,
    STATUS_NO_PRICES,
    STATUS_PLANNED,
    STATUS_TARGET_REACHED,
    parse_price_slots,
    plan_ev_charging,
)

BASE = datetime(2026, 10, 19, 22, 0, tzinfo=timezone.utc)  # 00:00 local


def slots(prices, start=BASE, minutes=15):
    return [
        PriceSlot(start + timedelta(minutes=minutes * i), start + timedelta(minutes=minutes * (i + 1)), p)
        for i, p in enumerate(prices)
    ]


def settings(**overrides):
    values = dict(
        departure=BASE + timedelta(hours=8),
        needed_kwh=6.0,
        ev_max_kw=7.4,
        max_grid_import_kw=7.0,
        base_load_kw=0.6,
        price_ceiling=0.5,
    )
    values.update(overrides)
    return PlanSettings(**values)


def test_charges_cheapest_quarters_and_reaches_target():
    prices = [0.40] * 32
    for i in range(8, 12):
        prices[i] = 0.10
    plan = plan_ev_charging(slots(prices), BASE, settings())
    assert plan.status == STATUS_PLANNED
    assert [s.start for s in plan.slots] == [BASE + timedelta(minutes=15 * i) for i in range(8, 12)]
    assert abs(plan.planned_kwh - 6.0) < 1e-6
    assert plan.cost_eur < plan.unmanaged_cost_eur


def test_shared_import_limit_caps_power():
    plan = plan_ev_charging(slots([0.1] * 32), BASE, settings(needed_kwh=20.0, ev_max_kw=11.0))
    assert max(s.kw for s in plan.slots) <= 7.0 - 0.6 + 1e-9


def test_never_charges_after_departure_or_before_now():
    now = BASE + timedelta(minutes=30)
    plan = plan_ev_charging(slots([0.1] * 48), now, settings(departure=BASE + timedelta(hours=2)))
    assert plan.slots and all(s.start >= now and s.end <= BASE + timedelta(hours=2) for s in plan.slots)


def test_partial_current_slot_only_counts_remaining_time():
    now = BASE + timedelta(minutes=10)
    plan = plan_ev_charging(slots([0.1] * 8), now, settings(needed_kwh=1.0))
    first = plan.slots[0]
    assert first.start == now and first.end == BASE + timedelta(minutes=15)


def test_price_ceiling_blocks_and_reports_required_ceiling():
    plan = plan_ev_charging(slots([0.3] * 32), BASE, settings(price_ceiling=0.2))
    assert plan.status == STATUS_INCOMPLETE
    assert plan.planned_kwh == 0.0
    assert plan.required_ceiling == 0.3
    assert plan.blocked_by_ceiling == 32


def test_no_headroom_blocks_everything():
    plan = plan_ev_charging(slots([0.1] * 32), BASE, settings(base_load_kw=7.5))
    assert plan.status == STATUS_INCOMPLETE
    assert plan.blocked_by_limit == 32
    assert plan.required_ceiling is None


def test_incomplete_when_prices_do_not_reach_departure():
    plan = plan_ev_charging(slots([0.1] * 8), BASE, settings())
    assert plan.status == STATUS_INCOMPLETE
    assert plan.prices_cover_until == BASE + timedelta(hours=2)


def test_target_reached_and_no_prices():
    assert plan_ev_charging(slots([0.1] * 8), BASE, settings(needed_kwh=0.0)).status == STATUS_TARGET_REACHED
    assert plan_ev_charging([], BASE, settings()).status == STATUS_NO_PRICES


def test_parse_infers_slot_length_and_merges_days():
    attrs = {
        "today": [
            {"start": "2026-10-19T23:30:00+02:00", "all_in": 0.3},
            {"start": "2026-10-19T23:45:00+02:00", "all_in": 0.31},
        ],
        "tomorrow": [
            {"start": "2026-10-20T00:00:00+02:00", "all_in": 0.32},
            {"start": "2026-10-20T00:15:00+02:00", "all_in": 0.33},
        ],
    }
    parsed = parse_price_slots(attrs)
    assert len(parsed) == 4
    assert all(s.end - s.start == timedelta(minutes=15) for s in parsed)
    assert parsed[-1].price == 0.33


def test_parse_hourly_and_rejects_bad_rows():
    hourly = {"today": [{"start": "2026-10-19T10:00:00+02:00", "all_in": 0.3},
                        {"start": "2026-10-19T11:00:00+02:00", "all_in": 0.3}]}
    assert [s.end - s.start for s in parse_price_slots(hourly)] == [timedelta(hours=1)] * 2
    assert parse_price_slots({"today": [{"start": "2026-10-19T10:00:00+02:00", "all_in": 0.3}]}) == []
    assert parse_price_slots({"today": [{"start": "2026-10-19T10:00:00+02:00", "all_in": "x"}]}) == []
    assert parse_price_slots({"today": [{"start": "2026-10-19T10:00:00", "all_in": 0.3}]}) == []
    assert parse_price_slots({}) == []
    conflicting = {"today": [{"start": "2026-10-19T10:00:00+02:00", "all_in": 0.3},
                             {"start": "2026-10-19T10:00:00+02:00", "all_in": 0.4}]}
    assert parse_price_slots(conflicting) == []


from custom_components.dynamic_energy.planner import SlotContext  # noqa: E402


def test_context_reduces_headroom_and_prefers_pv_surplus_slots():
    prices = [0.30] * 32
    context = {}
    for i in range(32):
        start = BASE + timedelta(minutes=15 * i)
        # quarters 4-7 have 5 kW PV surplus; quarters 8-11 are busy with the battery
        net = -5.0 if 4 <= i < 8 else 6.0 if 8 <= i < 12 else 0.6
        context[start] = SlotContext(base_net_kw=net, export_price=0.05)
    plan = plan_ev_charging(slots(prices), BASE, settings(needed_kwh=4.0, price_ceiling=0.5), context)
    chosen = [s.start for s in plan.slots]
    assert chosen == [BASE + timedelta(minutes=15 * i) for i in (4, 5, 6)]  # the surplus slots first
    busy = [s for s in plan.slots if BASE + timedelta(minutes=15 * 8) <= s.start < BASE + timedelta(minutes=15 * 12)]
    assert all(s.kw <= 1.0 + 1e-9 for s in busy)  # 7 kW limit - 6 kW house/battery
    # two slots take 1.85 kWh (1.25 kWh surplus at the export price, 0.6 kWh imported),
    # the third takes the last 0.3 kWh entirely from surplus
    assert abs(plan.cost_eur - (2 * (1.25 * 0.05 + 0.6 * 0.30) + 0.3 * 0.05)) < 1e-6

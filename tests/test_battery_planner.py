from datetime import datetime, timedelta, timezone

from custom_components.dynamic_energy.battery_planner import (
    ACTION_CHARGE_FROM_GRID,
    ACTION_DISCHARGE_TO_LOAD,
    ACTION_HOLD,
    ACTION_STORE_PV_SURPLUS,
    BatteryParams,
    QuarterInput,
    STATUS_NO_ACTION,
    STATUS_PLANNED,
    plan_battery,
)

START = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
PARAMS = BatteryParams(
    capacity_kwh=10.0, max_charge_kw=5.0, max_discharge_kw=5.0,
    round_trip_efficiency=0.81, degradation_eur_per_kwh=0.0,
)


def quarters(rows):
    """rows: (base_net_kwh, import_rate, export_rate) per quarter."""
    return [
        QuarterInput(START + timedelta(minutes=15 * i), START + timedelta(minutes=15 * (i + 1)), b, imp, exp)
        for i, (b, imp, exp) in enumerate(rows)
    ]


def test_buys_cheap_and_sells_expensive_when_spread_beats_losses():
    rows = [(0.2, 0.10, 0.05)] * 16 + [(0.5, 0.60, 0.05)] * 16
    plan = plan_battery(quarters(rows), PARAMS, 0.0, cap_kw=7.0)
    assert plan.status == STATUS_PLANNED
    assert plan.saving_eur > 0
    assert any(s.action == ACTION_CHARGE_FROM_GRID for s in plan.slots[:16])
    assert any(s.action == ACTION_DISCHARGE_TO_LOAD for s in plan.slots[16:])


def test_holds_when_spread_is_too_small_after_losses():
    rows = [(0.2, 0.30, 0.05)] * 16 + [(0.5, 0.32, 0.05)] * 16
    plan = plan_battery(quarters(rows), PARAMS, 0.0, cap_kw=7.0)
    assert all(s.action == ACTION_HOLD for s in plan.slots)
    assert abs(plan.saving_eur) < 1e-9


def test_stores_pv_surplus_instead_of_exporting_cheaply():
    rows = [(-1.0, 0.30, 0.02)] * 8 + [(0.6, 0.45, 0.02)] * 8
    plan = plan_battery(quarters(rows), PARAMS, 0.0, cap_kw=7.0)
    assert any(s.action == ACTION_STORE_PV_SURPLUS for s in plan.slots[:8])
    assert plan.surplus_charge_kwh > 0
    assert plan.saving_eur > 0


def test_never_exports_battery_energy_and_respects_cap_and_soc():
    rows = [(0.1, 0.05, 0.50)] * 8 + [(0.0, 0.90, 0.50)] * 8
    plan = plan_battery(quarters(rows), PARAMS, 5.0, cap_kw=2.0)
    for slot in plan.slots:
        assert slot.discharge_kwh <= max(0.0, slot.base_net_kwh) + 1e-9
        assert 0 <= slot.soc_end_kwh <= PARAMS.capacity_kwh + 1e-9
        assert slot.grid_net_kw <= 2.0 + 1e-9
    assert abs(plan.slots[-1].soc_end_kwh - 5.0) < 0.3  # horizon ends near the start SOC


def test_cap_lower_than_house_load_never_makes_a_slot_worse():
    rows = [(2.0, 0.05, 0.02)] * 8  # 8 kW net load, cap 1 kW
    plan = plan_battery(quarters(rows), PARAMS, 0.0, cap_kw=1.0)
    assert plan.status == STATUS_PLANNED
    assert all(s.grid_net_kw <= 8.0 + 1e-9 for s in plan.slots)
    assert all(s.charge_kwh == 0.0 for s in plan.slots)


def test_cost_is_never_worse_than_doing_nothing():
    rows = [(0.3, 0.2 + 0.05 * (i % 5), 0.03) for i in range(40)]
    plan = plan_battery(quarters(rows), PARAMS, 2.0, cap_kw=7.0)
    assert plan.planned_cost_eur <= plan.baseline_cost_eur + 1e-9


def test_invalid_input_returns_no_action():
    assert plan_battery([], PARAMS, 0.0, 7.0).status == STATUS_NO_ACTION
    gap = quarters([(0.1, 0.1, 0.1)] * 3)
    gap[2] = QuarterInput(gap[2].start + timedelta(minutes=15), gap[2].end + timedelta(minutes=15), 0.1, 0.1, 0.1)
    assert plan_battery(gap, PARAMS, 0.0, 7.0).reason == "INTERVALS_NOT_CONTIGUOUS"
    assert plan_battery(quarters([(0.1, 0.1, 0.1)]), PARAMS, 99.0, 7.0).reason == "INVALID_INPUT"

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_energy.const import (
    CONF_BATTERY_SOC_ENTITY,
    CONF_EXPORT_PRICE_ENTITY,
    CONF_MONTH_PEAK_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_PV_FORECAST_ENTITIES,
    DOMAIN,
)
from custom_components.dynamic_energy.history import async_load_profile

PRICE = "sensor.contract_huidige_prijs"
EXPORT = "sensor.contract_injectieprijs"
PV_TODAY = "sensor.pv_vandaag"
PV_TOMORROW = "sensor.pv_morgen"
SOC = "sensor.batterij_soc"
PEAK = "sensor.maandpiek"


def _day_rows(day_start, price_at, key):
    return [
        {"start": (day_start + timedelta(minutes=15 * i)).isoformat(), key: price_at(i)}
        for i in range(96)
    ]


def _price(i):
    hour = i // 4
    if 2 <= hour < 5:
        return 0.10
    if 17 <= hour < 21:
        return 0.60
    return 0.30


def _pv_rows(day_start, peak_kw):
    rows = []
    for i in range(48):
        hour = i / 2
        kw = peak_kw if 10 <= hour < 15 else 0.0
        rows.append({"period_start": day_start + timedelta(minutes=30 * i), "pv_estimate": kw})
    return rows


def _states(hass: HomeAssistant, soc="40"):
    today = dt_util.start_of_local_day()
    tomorrow = today + timedelta(days=1)
    hass.states.async_set(PRICE, "0.3", {
        "today": _day_rows(today, _price, "all_in"), "tomorrow": _day_rows(tomorrow, _price, "all_in")})
    hass.states.async_set(EXPORT, "0.1", {
        "today": _day_rows(today, lambda i: 0.05, "injection"),
        "tomorrow": _day_rows(tomorrow, lambda i: 0.05, "injection")})
    hass.states.async_set(PV_TODAY, "8", {"detailedForecast": _pv_rows(today, 3.0)})
    hass.states.async_set(PV_TOMORROW, "8", {"detailedForecast": _pv_rows(tomorrow, 3.0)})
    hass.states.async_set(PEAK, "5.4")
    if soc is not None:
        hass.states.async_set(SOC, soc)


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            CONF_PRICE_ENTITY: PRICE,
            CONF_EXPORT_PRICE_ENTITY: EXPORT,
            CONF_BATTERY_SOC_ENTITY: SOC,
            CONF_MONTH_PEAK_ENTITY: PEAK,
            CONF_PV_FORECAST_ENTITIES: [PV_TODAY, PV_TOMORROW],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_battery_plan_charges_cheap_and_discharges_expensive(hass: HomeAssistant):
    _states(hass)
    await _setup(hass)
    plan = hass.states.get("sensor.dynamic_energy_battery_plan")
    assert plan.state == "PLANNED"
    assert plan.attributes["read_only"] is True
    assert plan.attributes["saving_eur"] > 0
    actions = {row["action"] for row in plan.attributes["schedule"]}
    assert "DISCHARGE_TO_LOAD" in actions
    assert actions & {"CHARGE_FROM_GRID", "STORE_PV_SURPLUS"}
    assert plan.attributes["grid_cap_kw"] == 5.4  # month peak, capped by the 7 kW import limit
    assert "FLAT_LOAD_ASSUMED" in plan.attributes["quality_flags"]
    for row in plan.attributes["schedule"]:
        assert row["grid_net_kw"] <= max(plan.attributes["grid_cap_kw"], 0.6 + 1e-6) + 1e-6 or row["battery_kw"] <= 0
    assert hass.states.get("sensor.dynamic_energy_battery_planned_saving").state not in ("unknown", "unavailable")
    assert hass.states.get("sensor.dynamic_energy_battery_action_now") is not None


async def test_battery_plan_needs_a_soc_and_can_be_switched_off(hass: HomeAssistant):
    _states(hass, soc=None)
    await _setup(hass)
    plan = hass.states.get("sensor.dynamic_energy_battery_plan")
    assert plan.state == "NO_ACTION"
    assert plan.attributes["reason"] == "NO_BATTERY_SOC"

    hass.states.async_set(SOC, "40")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dynamic_energy_battery_plan").state == "PLANNED"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.dynamic_energy_battery_planning"}, blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dynamic_energy_battery_plan").state == "DISABLED"


async def test_ev_plan_fits_around_the_battery_plan(hass: HomeAssistant):
    _states(hass)
    await _setup(hass)
    ev = hass.states.get("sensor.dynamic_energy_ev_plan")
    assert ev.state in ("PLANNED", "INCOMPLETE")
    battery = hass.states.get("sensor.dynamic_energy_battery_plan").attributes["schedule"]
    assert ev.attributes["schedule"]
    for slot in ev.attributes["schedule"]:
        row = next(r for r in battery if r["start"] <= slot["start"] < r["end"])
        # house + battery + EV never exceed the shared import limit
        assert max(0.0, row["grid_net_kw"] + slot["kw"]) <= 7.0 + 1e-6


async def test_load_profile_from_statistics_excludes_the_ev(hass: HomeAssistant):
    hass.states.async_set("sensor.house_w", "800", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.ev_w", "0", {"unit_of_measurement": "W"})
    start = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)
    rows_house = [{"start": (start + timedelta(hours=h)).timestamp(), "mean": 1000.0 + (h % 24) * 10} for h in range(24 * 14)]
    rows_ev = [{"start": r["start"], "mean": 400.0} for r in rows_house]

    class Instance:
        async def async_add_executor_job(self, func, *args):
            return {"sensor.house_w": rows_house, "sensor.ev_w": rows_ev}

    with patch("custom_components.dynamic_energy.history.get_instance", return_value=Instance()):
        profile = await async_load_profile(hass, "sensor.house_w", "sensor.ev_w", 0.6)
    assert profile is not None
    noon = dt_util.as_local(start + timedelta(hours=12)).replace(minute=0)
    assert 0.5 < profile.kw(noon) < 1.0  # (1000..1230 W - 400 W EV) in kW

    with patch("custom_components.dynamic_energy.history.get_instance", side_effect=KeyError):
        assert await async_load_profile(hass, "sensor.house_w", None, 0.6) is None

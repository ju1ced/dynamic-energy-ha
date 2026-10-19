from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_energy.const import CONF_EV_SOC_ENTITY, CONF_PRICE_ENTITY, DOMAIN

PRICE = "sensor.contract_huidige_prijs"


def _rows(start, count, price):
    return [
        {"start": (start + timedelta(minutes=15 * i)).isoformat(), "all_in": price}
        for i in range(count)
    ]


def _set_prices(hass: HomeAssistant, cheap_from: int = 8):
    start = dt_util.start_of_local_day() + timedelta(days=1)  # tomorrow 00:00 local
    today = dt_util.start_of_local_day()
    rows_today = _rows(today, 96, 0.40)
    rows_tomorrow = _rows(start, 96, 0.40)
    for i in range(cheap_from, cheap_from + 8):
        rows_tomorrow[i]["all_in"] = 0.10
    hass.states.async_set(PRICE, "0.4", {"today": rows_today, "tomorrow": rows_tomorrow})


async def _setup(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_PRICE_ENTITY: PRICE, **data}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_creates_plan_and_settings_entities(hass: HomeAssistant):
    hass.config.language = "nl"  # entity IDs must not depend on the language
    _set_prices(hass)
    await _setup(hass)

    plan = hass.states.get("sensor.dynamic_energy_ev_plan")
    assert plan is not None
    assert plan.attributes["read_only"] is True
    assert plan.state in ("PLANNED", "INCOMPLETE")
    for entity_id in (
        "sensor.dynamic_energy_ev_planned_energy",
        "sensor.dynamic_energy_ev_plan_cost",
        "sensor.dynamic_energy_ev_next_charge_start",
        "binary_sensor.dynamic_energy_ev_charge_advised",
        "number.dynamic_energy_price_ceiling",
        "number.dynamic_energy_max_grid_import_kw",
        "switch.dynamic_energy_ev_planning",
        "time.dynamic_energy_ev_departure",
    ):
        assert hass.states.get(entity_id) is not None, entity_id


async def test_settings_change_the_plan_and_switch_disables_it(hass: HomeAssistant):
    _set_prices(hass)
    await _setup(hass)
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.dynamic_energy_price_ceiling", "value": 0.05}, blocking=True,
    )
    await hass.async_block_till_done()
    blocked = hass.states.get("sensor.dynamic_energy_ev_plan")
    assert blocked.state == "INCOMPLETE"
    assert blocked.attributes["planned_kwh"] == 0

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.dynamic_energy_ev_planning"}, blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dynamic_energy_ev_plan").state == "DISABLED"


async def test_car_soc_entity_reduces_the_needed_energy(hass: HomeAssistant):
    _set_prices(hass)
    hass.states.async_set("sensor.car_soc", "80")
    await _setup(hass, **{CONF_EV_SOC_ENTITY: "sensor.car_soc"})
    assert hass.states.get("sensor.dynamic_energy_ev_plan").state == "TARGET_REACHED"

    hass.states.async_set("sensor.car_soc", "50")
    await hass.async_block_till_done()
    plan = hass.states.get("sensor.dynamic_energy_ev_plan")
    assert plan.attributes["needed_kwh"] == 18.0


async def test_missing_price_entity_state_gives_no_prices(hass: HomeAssistant):
    await _setup(hass)
    assert hass.states.get("sensor.dynamic_energy_ev_plan").state == "NO_PRICES"


async def test_config_flow_validates_the_price_entity(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    bad = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PRICE_ENTITY: "sensor.missing"})
    assert bad["errors"]["base"] == "price_entity_not_found"

    hass.states.async_set(PRICE, "0.4", {"unit": "x"})
    bad = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PRICE_ENTITY: PRICE})
    assert bad["errors"]["base"] == "price_entity_without_prices"

    _set_prices(hass)
    ok = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PRICE_ENTITY: PRICE})
    assert ok["type"] == "create_entry"
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert again["type"] == "abort"

"""Constants for Dynamic Energy."""

from __future__ import annotations

from datetime import time

DOMAIN = "dynamic_energy"

CONF_PRICE_ENTITY = "price_entity"
CONF_EV_SOC_ENTITY = "ev_soc_entity"
CONF_EXPORT_PRICE_ENTITY = "export_price_entity"
CONF_PV_FORECAST_ENTITIES = "pv_forecast_entities"
CONF_BATTERY_SOC_ENTITY = "battery_soc_entity"
CONF_HOUSE_POWER_ENTITY = "house_power_entity"
CONF_EV_POWER_ENTITY = "ev_power_entity"
CONF_MONTH_PEAK_ENTITY = "month_peak_entity"

OPTIONAL_ENTITY_KEYS = (
    CONF_EV_SOC_ENTITY,
    CONF_EXPORT_PRICE_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_HOUSE_POWER_ENTITY,
    CONF_EV_POWER_ENTITY,
    CONF_MONTH_PEAK_ENTITY,
)

PLATFORMS = ["binary_sensor", "number", "sensor", "switch", "time"]

# Defaults for the user-editable planning settings (number/time/switch entities).
DEFAULT_SETTINGS: dict[str, float | bool | time] = {
    "enabled": True,
    "max_grid_import_kw": 7.0,
    "price_ceiling": 0.40,
    "ev_target_soc": 80.0,
    "ev_start_soc": 20.0,
    "ev_capacity_kwh": 60.0,
    "ev_max_charge_kw": 7.4,
    "base_load_kw": 0.6,
    "departure": time(7, 30),
    "battery_enabled": True,
    "battery_capacity_kwh": 14.24,
    "battery_max_charge_kw": 5.0,
    "battery_max_discharge_kw": 5.0,
    "battery_efficiency": 85.0,  # round trip, percent
    "battery_min_soc": 10.0,
    "battery_max_soc": 100.0,
    "battery_degradation": 0.06,  # EUR per discharged kWh
    "peak_allowance_kw": 0.0,
    "export_price_fallback": 0.02,
}

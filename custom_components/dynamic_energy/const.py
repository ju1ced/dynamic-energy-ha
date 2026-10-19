"""Constants for Dynamic Energy."""

from __future__ import annotations

from datetime import time

DOMAIN = "dynamic_energy"

CONF_PRICE_ENTITY = "price_entity"
CONF_EV_SOC_ENTITY = "ev_soc_entity"

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
}

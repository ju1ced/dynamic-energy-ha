"""Coordinator that turns the price entity and the settings into an EV plan."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CONF_EV_SOC_ENTITY, CONF_PRICE_ENTITY, DEFAULT_SETTINGS, DOMAIN
from .planner import (
    EvPlan,
    PlanSettings,
    STATUS_NO_PRICES,
    disabled_plan,
    parse_price_slots,
    plan_ev_charging,
    _empty_plan,
)

_LOGGER = logging.getLogger(__name__)


def _number(value: Any) -> float | None:
    if value in (None, STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


class DynamicEnergyCoordinator(DataUpdateCoordinator[EvPlan]):
    """Recompute the advisory plan; never writes to any device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=1),
        )
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.price_entity: str = entry.options.get(
            CONF_PRICE_ENTITY, entry.data[CONF_PRICE_ENTITY]
        )
        self.ev_soc_entity: str | None = entry.options.get(
            CONF_EV_SOC_ENTITY, entry.data.get(CONF_EV_SOC_ENTITY)
        )

    @property
    def watched_entities(self) -> list[str]:
        return [e for e in (self.price_entity, self.ev_soc_entity) if e]

    async def async_set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value
        await self.async_refresh()  # not debounced: setting changes apply at once

    def restore_setting(self, key: str, value: Any) -> None:
        """Apply a restored value without triggering a refresh."""
        self.settings[key] = value

    def _start_soc(self) -> float:
        measured = _number(
            getattr(self.hass.states.get(self.ev_soc_entity or ""), "state", None)
        )
        if measured is not None and 0 <= measured <= 100:
            return measured
        return float(self.settings["ev_start_soc"])

    def _next_departure(self, now_utc):
        local = dt_util.as_local(now_utc)
        departure = self.settings["departure"]
        candidate = local.replace(
            hour=departure.hour, minute=departure.minute, second=0, microsecond=0
        )
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate

    async def _async_update_data(self) -> EvPlan:
        if not self.settings["enabled"]:
            return disabled_plan()
        state = self.hass.states.get(self.price_entity)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return _empty_plan(STATUS_NO_PRICES, 0.0, None)
        slots = parse_price_slots(dict(state.attributes))
        now = dt_util.utcnow()
        soc = self._start_soc()
        needed = max(
            0.0,
            (float(self.settings["ev_target_soc"]) - soc)
            / 100.0
            * float(self.settings["ev_capacity_kwh"]),
        )
        return plan_ev_charging(
            slots,
            now,
            PlanSettings(
                departure=self._next_departure(now),
                needed_kwh=needed,
                ev_max_kw=float(self.settings["ev_max_charge_kw"]),
                max_grid_import_kw=float(self.settings["max_grid_import_kw"]),
                base_load_kw=float(self.settings["base_load_kw"]),
                price_ceiling=float(self.settings["price_ceiling"]),
            ),
        )

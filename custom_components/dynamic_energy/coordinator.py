"""Coordinator: prices + forecasts + settings -> battery plan and EV plan."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .battery_planner import (
    STATUS_NO_ACTION,
    BatteryParams,
    BatteryPlan,
    disabled_plan as disabled_battery_plan,
    plan_battery,
    _empty as empty_battery_plan,
)
from .const import (
    CONF_BATTERY_SOC_ENTITY,
    CONF_EV_POWER_ENTITY,
    CONF_EV_SOC_ENTITY,
    CONF_EXPORT_PRICE_ENTITY,
    CONF_HOUSE_POWER_ENTITY,
    CONF_MONTH_PEAK_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_PV_FORECAST_ENTITIES,
    DEFAULT_SETTINGS,
    DOMAIN,
)
from .forecast import LoadProfile, build_quarter_inputs, parse_pv_forecast
from .history import async_load_profile
from .planner import (
    EvPlan,
    PlanSettings,
    SlotContext,
    STATUS_NO_PRICES,
    disabled_plan,
    parse_price_slots,
    plan_ev_charging,
    _empty_plan,
)

_LOGGER = logging.getLogger(__name__)

PROFILE_REFRESH = timedelta(hours=6)
MIN_BILLED_PEAK_KW = 2.5  # Flemish capacity tariff bills at least 2.5 kW


@dataclass(frozen=True)
class PlanBundle:
    ev: EvPlan
    battery: BatteryPlan
    flags: tuple[str, ...]
    battery_soc_percent: float | None
    load_source: str  # "history" or "flat"
    cap_kw: float


def _number(value: Any) -> float | None:
    if value in (None, STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


class DynamicEnergyCoordinator(DataUpdateCoordinator[PlanBundle]):
    """Recompute the advisory plans; never writes to any device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=1),
        )
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        # Options hold the complete set once the options flow has been used.
        config = dict(entry.options or entry.data)
        self.price_entity: str = config[CONF_PRICE_ENTITY]
        self.export_entity: str | None = config.get(CONF_EXPORT_PRICE_ENTITY)
        self.ev_soc_entity: str | None = config.get(CONF_EV_SOC_ENTITY)
        self.battery_soc_entity: str | None = config.get(CONF_BATTERY_SOC_ENTITY)
        self.house_entity: str | None = config.get(CONF_HOUSE_POWER_ENTITY)
        self.ev_power_entity: str | None = config.get(CONF_EV_POWER_ENTITY)
        self.month_peak_entity: str | None = config.get(CONF_MONTH_PEAK_ENTITY)
        self.pv_entities: list[str] = list(config.get(CONF_PV_FORECAST_ENTITIES) or [])
        self._profile: LoadProfile | None = None
        self._profile_at: datetime | None = None
        self._battery_key: tuple | None = None
        self._battery_plan: BatteryPlan | None = None

    @property
    def watched_entities(self) -> list[str]:
        entities = [
            self.price_entity,
            self.export_entity,
            self.ev_soc_entity,
            self.battery_soc_entity,
            self.month_peak_entity,
            *self.pv_entities,
        ]
        return [entity for entity in entities if entity]

    async def async_set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value
        await self.async_refresh()  # not debounced: setting changes apply at once

    def restore_setting(self, key: str, value: Any) -> None:
        """Apply a restored value without triggering a refresh."""
        self.settings[key] = value

    # -- inputs -----------------------------------------------------------

    def _state_number(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        return _number(state.state) if state else None

    def _start_soc(self) -> float:
        measured = self._state_number(self.ev_soc_entity)
        if measured is not None and 0 <= measured <= 100:
            return measured
        return float(self.settings["ev_start_soc"])

    def _next_departure(self, now_utc: datetime) -> datetime:
        local = dt_util.as_local(now_utc)
        departure = self.settings["departure"]
        candidate = local.replace(
            hour=departure.hour, minute=departure.minute, second=0, microsecond=0
        )
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate

    async def _load_profile(self, now: datetime) -> LoadProfile:
        fallback = float(self.settings["base_load_kw"])
        if self.house_entity:
            stale = self._profile_at is None or now - self._profile_at > PROFILE_REFRESH
            if stale:
                self._profile_at = now
                loaded = await async_load_profile(
                    self.hass, self.house_entity, self.ev_power_entity, fallback
                )
                self._profile = loaded
            if self._profile is not None:
                return LoadProfile(self._profile.values, fallback)
        return LoadProfile({}, fallback)

    def _cap_kw(self) -> float:
        max_import = float(self.settings["max_grid_import_kw"])
        peak = self._state_number(self.month_peak_entity)
        if peak is None or peak <= 0:
            return max_import
        allowance = float(self.settings["peak_allowance_kw"])
        return min(max_import, max(MIN_BILLED_PEAK_KW, peak) + allowance)

    # -- plans ------------------------------------------------------------

    async def _plan_battery(self, quarters, flags: list[str]) -> tuple[BatteryPlan, float | None, float]:
        cap = self._cap_kw()
        if not self.settings["battery_enabled"]:
            return disabled_battery_plan(), None, cap
        soc = self._state_number(self.battery_soc_entity)
        if soc is None or not 0 <= soc <= 100:
            return empty_battery_plan(STATUS_NO_ACTION, "NO_BATTERY_SOC", cap), None, cap
        if not quarters:
            return empty_battery_plan(STATUS_NO_ACTION, "NO_PRICES", cap), soc, cap
        capacity = float(self.settings["battery_capacity_kwh"])
        minimum = float(self.settings["battery_min_soc"])
        maximum = float(self.settings["battery_max_soc"])
        window = capacity * (maximum - minimum) / 100.0
        if window <= 0:
            return empty_battery_plan(STATUS_NO_ACTION, "INVALID_SETTINGS", cap), soc, cap
        initial = min(window, max(0.0, (soc - minimum) / 100.0 * capacity))
        params = BatteryParams(
            capacity_kwh=window,
            max_charge_kw=float(self.settings["battery_max_charge_kw"]),
            max_discharge_kw=float(self.settings["battery_max_discharge_kw"]),
            round_trip_efficiency=float(self.settings["battery_efficiency"]) / 100.0,
            degradation_eur_per_kwh=float(self.settings["battery_degradation"]),
        )
        key = (
            params,
            round(initial / params.state_step_kwh),
            round(cap, 3),
            tuple(
                (q.start, round(q.base_net_kwh, 3), q.import_rate, q.export_rate)
                for q in quarters
            ),
        )
        if key == self._battery_key and self._battery_plan is not None:
            return self._battery_plan, soc, cap
        plan = await self.hass.async_add_executor_job(
            plan_battery, quarters, params, initial, cap
        )
        self._battery_key, self._battery_plan = key, plan
        return plan, soc, cap

    async def _async_update_data(self) -> PlanBundle:
        now = dt_util.utcnow()
        flags: list[str] = []
        state = self.hass.states.get(self.price_entity)
        usable = state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        import_slots = parse_price_slots(dict(state.attributes)) if usable else []

        export_slots = []
        if self.export_entity:
            export_state = self.hass.states.get(self.export_entity)
            if export_state is not None and export_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                export_slots = parse_price_slots(dict(export_state.attributes), price_key="injection")
        pv = parse_pv_forecast(
            [
                getattr(self.hass.states.get(entity), "attributes", {}).get("detailedForecast")
                for entity in self.pv_entities
            ]
        )
        profile = await self._load_profile(now)
        quarters, quarter_flags = build_quarter_inputs(
            import_slots,
            export_slots,
            now,
            profile,
            pv,
            float(self.settings["export_price_fallback"]),
            dt_util.as_local,
        )
        flags.extend(quarter_flags)
        if not self.house_entity or self._profile is None:
            flags.append("FLAT_LOAD_ASSUMED")

        battery, soc, cap = await self._plan_battery(quarters, flags)

        if not self.settings["enabled"]:
            ev = disabled_plan()
        elif not import_slots:
            ev = _empty_plan(STATUS_NO_PRICES, 0.0, None)
        else:
            needed = max(
                0.0,
                (float(self.settings["ev_target_soc"]) - self._start_soc())
                / 100.0
                * float(self.settings["ev_capacity_kwh"]),
            )
            ev = plan_ev_charging(
                import_slots,
                now,
                PlanSettings(
                    departure=self._next_departure(now),
                    needed_kwh=needed,
                    ev_max_kw=float(self.settings["ev_max_charge_kw"]),
                    max_grid_import_kw=float(self.settings["max_grid_import_kw"]),
                    base_load_kw=float(self.settings["base_load_kw"]),
                    price_ceiling=float(self.settings["price_ceiling"]),
                ),
                self._ev_context(battery, quarters),
            )
        return PlanBundle(
            ev=ev,
            battery=battery,
            flags=tuple(sorted(set(flags))),
            battery_soc_percent=soc,
            load_source="history" if self._profile is not None and self.house_entity else "flat",
            cap_kw=cap,
        )

    @staticmethod
    def _ev_context(battery: BatteryPlan, quarters) -> dict[datetime, SlotContext] | None:
        """House + battery grid net load per slot, so the EV fits around them."""
        if battery.slots:
            return {
                slot.slot_start: SlotContext(slot.grid_net_kw, slot.export_rate)
                for slot in battery.slots
            }
        if quarters:
            return {
                q.slot_start or q.start: SlotContext(q.base_net_kwh / q.hours, q.export_rate)
                for q in quarters
            }
        return None

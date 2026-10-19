"""Sensors describing the advisory EV plan."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .battery_planner import (
    ACTION_CHARGE_FROM_GRID,
    ACTION_DISCHARGE_TO_LOAD,
    ACTION_HOLD,
    ACTION_STORE_PV_SURPLUS,
    STATUS_DISABLED as BATTERY_DISABLED,
    STATUS_NO_ACTION as BATTERY_NO_ACTION,
    STATUS_PLANNED as BATTERY_PLANNED,
)
from .const import DOMAIN
from .entity import DynamicEnergyEntity
from .planner import (
    STATUS_DISABLED,
    STATUS_INCOMPLETE,
    STATUS_NO_PRICES,
    STATUS_PLANNED,
    STATUS_TARGET_REACHED,
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            EvPlanSensor(coordinator, entry.entry_id, "ev_plan"),
            EvPlannedEnergySensor(coordinator, entry.entry_id, "ev_planned_energy"),
            EvPlanCostSensor(coordinator, entry.entry_id, "ev_plan_cost"),
            EvNextChargeSensor(coordinator, entry.entry_id, "ev_next_charge_start"),
            BatteryPlanSensor(coordinator, entry.entry_id, "battery_plan"),
            BatteryActionSensor(coordinator, entry.entry_id, "battery_action_now"),
            BatterySavingSensor(coordinator, entry.entry_id, "battery_planned_saving"),
        ]
    )


def _iso(value: datetime) -> str:
    return dt_util.as_local(value).isoformat()


class EvPlanSensor(DynamicEnergyEntity, SensorEntity):
    """State = plan status; the schedule is an attribute for dashboards."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        STATUS_PLANNED,
        STATUS_TARGET_REACHED,
        STATUS_INCOMPLETE,
        STATUS_DISABLED,
        STATUS_NO_PRICES,
    ]
    _unrecorded_attributes = frozenset({"schedule"})

    @property
    def native_value(self) -> str:
        return self.coordinator.data.ev.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self.coordinator.data.ev
        return {
            "read_only": True,
            "schedule": [
                {
                    "start": _iso(slot.start),
                    "end": _iso(slot.end),
                    "kw": round(slot.kw, 3),
                    "kwh": round(slot.kwh, 3),
                    "price": round(slot.price, 6),
                    "reason": slot.reason,
                }
                for slot in plan.slots
            ],
            "needed_kwh": round(plan.needed_kwh, 3),
            "planned_kwh": round(plan.planned_kwh, 3),
            "unmet_kwh": round(plan.unmet_kwh, 3),
            "cost_eur": round(plan.cost_eur, 4),
            "unmanaged_cost_eur": round(plan.unmanaged_cost_eur, 4),
            "saving_eur": round(plan.unmanaged_cost_eur - plan.cost_eur, 4),
            "required_price_ceiling": plan.required_ceiling,
            "blocked_by_price_ceiling": plan.blocked_by_ceiling,
            "blocked_by_import_limit": plan.blocked_by_limit,
            "prices_cover_until": (
                _iso(plan.prices_cover_until) if plan.prices_cover_until else None
            ),
        }


class EvPlannedEnergySensor(DynamicEnergyEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def native_value(self) -> float:
        return round(self.coordinator.data.ev.planned_kwh, 3)


class EvPlanCostSensor(DynamicEnergyEntity, SensorEntity):
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float:
        return round(self.coordinator.data.ev.cost_eur, 4)


class EvNextChargeSensor(DynamicEnergyEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        now = dt_util.utcnow()
        upcoming = [slot for slot in self.coordinator.data.ev.slots if slot.end > now]
        return min((slot.start for slot in upcoming), default=None)


BATTERY_ACTIONS = [
    ACTION_CHARGE_FROM_GRID,
    ACTION_STORE_PV_SURPLUS,
    ACTION_DISCHARGE_TO_LOAD,
    ACTION_HOLD,
]


class BatteryPlanSensor(DynamicEnergyEntity, SensorEntity):
    """State = plan status; the schedule is an attribute for dashboards."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [BATTERY_PLANNED, BATTERY_NO_ACTION, BATTERY_DISABLED]
    _unrecorded_attributes = frozenset({"schedule"})

    @property
    def native_value(self) -> str:
        return self.coordinator.data.battery.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        bundle = self.coordinator.data
        plan = bundle.battery
        return {
            "read_only": True,
            "reason": plan.reason,
            "quality_flags": list(bundle.flags),
            "load_source": bundle.load_source,
            "grid_cap_kw": round(plan.cap_kw or bundle.cap_kw, 3),
            "battery_soc_percent": bundle.battery_soc_percent,
            "schedule": [
                {
                    "start": _iso(slot.start),
                    "end": _iso(slot.end),
                    "action": slot.action,
                    "reason": slot.reason,
                    "battery_kw": round(slot.battery_kw, 3),
                    "soc_kwh": round(slot.soc_end_kwh, 3),
                    "grid_net_kw": round(slot.grid_net_kw, 3),
                    "import_price": round(slot.import_rate, 6),
                    "export_price": round(slot.export_rate, 6),
                }
                for slot in plan.slots
            ],
            "charge_kwh": round(plan.charge_kwh, 3),
            "discharge_kwh": round(plan.discharge_kwh, 3),
            "grid_charge_kwh": round(plan.grid_charge_kwh, 3),
            "surplus_charge_kwh": round(plan.surplus_charge_kwh, 3),
            "baseline_cost_eur": round(plan.baseline_cost_eur, 4),
            "planned_cost_eur": round(plan.planned_cost_eur, 4),
            "saving_eur": round(plan.saving_eur, 4),
        }


class BatteryActionSensor(DynamicEnergyEntity, SensorEntity):
    """What the plan says the battery should do in the current quarter."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = BATTERY_ACTIONS

    def _current(self):
        now = dt_util.utcnow()
        return next(
            (slot for slot in self.coordinator.data.battery.slots if slot.start <= now < slot.end),
            None,
        )

    @property
    def native_value(self) -> str | None:
        slot = self._current()
        return slot.action if slot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slot = self._current()
        if slot is None:
            return {"read_only": True}
        return {
            "read_only": True,
            "battery_kw": round(slot.battery_kw, 3),
            "reason": slot.reason,
            "until": _iso(slot.end),
        }


class BatterySavingSensor(DynamicEnergyEntity, SensorEntity):
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        plan = self.coordinator.data.battery
        return round(plan.saving_eur, 4) if plan.slots else None

"""Sensors describing the advisory EV plan."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

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
        return self.coordinator.data.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self.coordinator.data
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
        return round(self.coordinator.data.planned_kwh, 3)


class EvPlanCostSensor(DynamicEnergyEntity, SensorEntity):
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float:
        return round(self.coordinator.data.cost_eur, 4)


class EvNextChargeSensor(DynamicEnergyEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        now = dt_util.utcnow()
        upcoming = [slot for slot in self.coordinator.data.slots if slot.end > now]
        return min((slot.start for slot in upcoming), default=None)

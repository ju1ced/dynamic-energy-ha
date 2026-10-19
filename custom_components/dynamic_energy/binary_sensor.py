"""Advisory binary sensor: is the current slot part of the plan?"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import DynamicEnergyEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EvChargeAdvisedBinarySensor(coordinator, entry.entry_id, "ev_charge_advised")])


class EvChargeAdvisedBinarySensor(DynamicEnergyEntity, BinarySensorEntity):
    """On while the plan says to charge. It switches nothing by itself."""

    @property
    def is_on(self) -> bool:
        now = dt_util.utcnow()
        return any(slot.start <= now < slot.end for slot in self.coordinator.data.ev.slots)

"""Departure time setting."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .entity import DynamicEnergyEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DepartureTime(coordinator, entry.entry_id, "ev_departure")])


class DepartureTime(DynamicEnergyEntity, TimeEntity, RestoreEntity):
    @property
    def native_value(self) -> time:
        return self.coordinator.settings["departure"]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            try:
                self.coordinator.restore_setting("departure", time.fromisoformat(last.state))
            except ValueError:
                pass

    async def async_set_value(self, value: time) -> None:
        await self.coordinator.async_set_setting("departure", value.replace(second=0, microsecond=0))
        self.async_write_ha_state()

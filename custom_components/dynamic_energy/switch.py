"""Enable or pause the advisory plan. Controls no hardware."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .entity import DynamicEnergyEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PlanningSwitch(coordinator, entry.entry_id, "ev_planning")])


class PlanningSwitch(DynamicEnergyEntity, SwitchEntity, RestoreEntity):
    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.settings["enabled"])

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self.coordinator.restore_setting("enabled", last.state == "on")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_setting("enabled", True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_setting("enabled", False)
        self.async_write_ha_state()

"""Shared base entity."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DynamicEnergyCoordinator


class DynamicEnergyEntity(CoordinatorEntity[DynamicEnergyCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DynamicEnergyCoordinator, entry_id: str, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_translation_key = key
        self._object_id = key  # Home Assistant prefixes the device name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Dynamic Energy",
            manufacturer="ju1ced",
            model="EV planner (advisory)",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def suggested_object_id(self) -> str | None:
        """Keep entity IDs identical in every Home Assistant language."""
        return self._object_id

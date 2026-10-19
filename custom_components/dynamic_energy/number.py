"""Planning settings as number entities (restored across restarts)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_SETTINGS, DOMAIN
from .entity import DynamicEnergyEntity


@dataclass(frozen=True)
class _Spec:
    key: str
    minimum: float
    maximum: float
    step: float
    unit: str


SPECS = (
    _Spec("max_grid_import_kw", 0.5, 25.0, 0.1, UnitOfPower.KILO_WATT),
    _Spec("price_ceiling", 0.0, 2.0, 0.005, "EUR/kWh"),
    _Spec("ev_target_soc", 0.0, 100.0, 1.0, PERCENTAGE),
    _Spec("ev_start_soc", 0.0, 100.0, 1.0, PERCENTAGE),
    _Spec("ev_capacity_kwh", 1.0, 200.0, 1.0, UnitOfEnergy.KILO_WATT_HOUR),
    _Spec("ev_max_charge_kw", 1.0, 22.0, 0.1, UnitOfPower.KILO_WATT),
    _Spec("base_load_kw", 0.0, 10.0, 0.1, UnitOfPower.KILO_WATT),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SettingNumber(coordinator, entry.entry_id, spec) for spec in SPECS])


class SettingNumber(DynamicEnergyEntity, RestoreNumber):
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, entry_id: str, spec: _Spec) -> None:
        super().__init__(coordinator, entry_id, spec.key)
        self._key = spec.key
        self._attr_native_min_value = spec.minimum
        self._attr_native_max_value = spec.maximum
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = spec.unit

    @property
    def native_value(self) -> float:
        return float(self.coordinator.settings[self._key])

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            value = float(last.native_value)
            if self._attr_native_min_value <= value <= self._attr_native_max_value:
                self.coordinator.restore_setting(self._key, value)
        else:
            self.coordinator.restore_setting(self._key, float(DEFAULT_SETTINGS[self._key]))

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_setting(self._key, float(value))
        self.async_write_ha_state()

"""Dynamic Energy: advisory EV charging plan on dynamic prices.

The integration only reads Home Assistant states. It creates sensors and
settings entities and never switches a charger, battery or inverter.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN, PLATFORMS
from .coordinator import DynamicEnergyCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = DynamicEnergyCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    async def _refresh(_event) -> None:
        await coordinator.async_request_refresh()

    entry.async_on_unload(
        async_track_state_change_event(hass, coordinator.watched_entities, _refresh)
    )
    entry.async_on_unload(entry.add_update_listener(_reload))
    # Entities need data when they are added; the settings entities restore their
    # saved values while being added, so refresh again afterwards.
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_refresh()
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded

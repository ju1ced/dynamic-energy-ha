"""Config and options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .const import CONF_EV_SOC_ENTITY, CONF_PRICE_ENTITY, DOMAIN


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(
            CONF_PRICE_ENTITY, default=defaults.get(CONF_PRICE_ENTITY)
        ): EntitySelector(EntitySelectorConfig(domain="sensor")),
    }
    soc = defaults.get(CONF_EV_SOC_ENTITY)
    key = vol.Optional(CONF_EV_SOC_ENTITY, description={"suggested_value": soc})
    fields[key] = EntitySelector(EntitySelectorConfig(domain="sensor"))
    return vol.Schema(fields)


def _price_entity_error(hass, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    if state is None:
        return "price_entity_not_found"
    if not isinstance(state.attributes.get("today"), list):
        return "price_entity_without_prices"
    return None


class DynamicEnergyConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = _price_entity_error(self.hass, user_input[CONF_PRICE_ENTITY])
            if error is None:
                return self.async_create_entry(title="Dynamic Energy", data=user_input)
            errors["base"] = error
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return DynamicEnergyOptionsFlow()


class DynamicEnergyOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            error = _price_entity_error(self.hass, user_input[CONF_PRICE_ENTITY])
            if error is None:
                return self.async_create_entry(data=user_input)
            errors["base"] = error
            current = user_input
        return self.async_show_form(
            step_id="init", data_schema=_schema(current), errors=errors
        )

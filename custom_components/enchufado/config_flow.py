"""Config flow for Enchufado integration.

Two-step setup:
  1. Datadis credentials → authenticate + fetch supply list
  2. Select CUPS → auto-fetch contract (power values, postal code) → create entry
"""
import logging
from typing import Any, Dict, Optional

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.selector import selector

from .const import (
    CONF_AUTHORIZED_NIF,
    CONF_CUPS,
    CONF_DATADIS_PASSWORD,
    CONF_DATADIS_USER,
    CONF_DISTRIBUTOR_CODE,
    CONF_POINT_TYPE,
    CONF_POWER_HIGH,
    CONF_POWER_LOW,
    DOMAIN,
)
from .datadis import async_get_contract_detail, async_get_supplies, async_login

_LOGGER = logging.getLogger(__name__)

_AUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DATADIS_USER): cv.string,
        vol.Required(CONF_DATADIS_PASSWORD): cv.string,
        vol.Optional(CONF_AUTHORIZED_NIF, default=""): cv.string,
    }
)


class EnchufadoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    _supplies: list = []
    _token: str = None
    data: Dict[str, Any] = {}

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        errors = {}
        if user_input is not None:
            username = user_input[CONF_DATADIS_USER].strip()
            password = user_input[CONF_DATADIS_PASSWORD]
            authorized_nif = user_input.get(CONF_AUTHORIZED_NIF, "").strip() or None

            token = await async_login(username, password)
            if token is None:
                errors["base"] = "cannot_connect"
            else:
                supplies = await async_get_supplies(token, authorized_nif)
                if not supplies:
                    errors["base"] = "no_supplies"
                else:
                    self._token = token
                    self._supplies = supplies
                    self.data = {
                        CONF_DATADIS_USER: username,
                        CONF_DATADIS_PASSWORD: password,
                        CONF_AUTHORIZED_NIF: authorized_nif,
                    }
                    return await self.async_step_cups()

        return self.async_show_form(step_id="user", data_schema=_AUTH_SCHEMA, errors=errors)

    async def async_step_cups(self, user_input: Optional[Dict[str, Any]] = None):
        cups_options = [
            f"{s['cups']} ({s['distributor_name']})" for s in self._supplies
        ]
        cups_schema = vol.Schema(
            {vol.Required(CONF_CUPS): selector({"select": {"options": cups_options}})}
        )

        errors = {}
        if user_input is not None:
            selected_label = user_input[CONF_CUPS]
            cups_value = selected_label.split(" (")[0].strip()

            supply = next((s for s in self._supplies if s["cups"] == cups_value), None)
            if supply is None:
                return self.async_abort(reason="cups_not_found")

            # Auto-fetch contracted power and postal code from Datadis
            power_high = 4.6
            power_low = 4.6
            try:
                contract = await async_get_contract_detail(
                    self._token,
                    cups_value,
                    supply["distributor_code"],
                    self.data.get(CONF_AUTHORIZED_NIF),
                )
                if contract:
                    powers = contract.get("contractedPowerkW", [])
                    if isinstance(powers, list) and len(powers) >= 1:
                        power_high = float(powers[0])
                        power_low = float(powers[-1])
                    elif isinstance(powers, (int, float)):
                        power_high = power_low = float(powers)
                    _LOGGER.debug("Contract powers: %s → high=%.2f, low=%.2f", powers, power_high, power_low)
            except Exception as err:
                _LOGGER.warning("Could not fetch contract detail: %s", err)

            self.data.update(
                {
                    CONF_CUPS: cups_value,
                    CONF_DISTRIBUTOR_CODE: supply["distributor_code"],
                    CONF_POINT_TYPE: supply["point_type"],
                    CONF_POWER_HIGH: power_high,
                    CONF_POWER_LOW: power_low,
                }
            )
            return self.async_create_entry(title=cups_value, data=self.data)

        return self.async_show_form(step_id="cups", data_schema=cups_schema, errors=errors)

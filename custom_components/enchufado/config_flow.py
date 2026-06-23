"""Config flow for Enchufado integration.

Based on pvpc_energy by yinyang17 (https://github.com/yinyang17/pvpc_energy).
"""
import logging
from typing import Any, Dict, Optional

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

AUTH_SCHEMA = vol.Schema(
    {
        vol.Required("datadis_user"): cv.string,
        vol.Required("datadis_password"): cv.string,
        vol.Required("cups"): cv.string,
        vol.Optional("authorized_nif", default=""): cv.string,
        vol.Optional("power_high", default=4.6): vol.Coerce(float),
        vol.Optional("power_low", default=4.6): vol.Coerce(float),
        vol.Optional("zip_code", default=""): cv.string,
        vol.Optional("bills_number", default=5): cv.positive_int,
    }
)


class EnchufadoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    data: Optional[Dict[str, Any]] = None

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        errors = {}
        if user_input is not None:
            _LOGGER.debug("async_step_user: cups=%s", user_input.get("cups"))
            cups = user_input["cups"].strip().upper()
            user_input["cups"] = cups
            if user_input.get("authorized_nif") == "":
                user_input["authorized_nif"] = None
            if user_input.get("zip_code") == "":
                user_input["zip_code"] = None
            return self.async_create_entry(title=cups, data=user_input)

        return self.async_show_form(step_id="user", data_schema=AUTH_SCHEMA, errors=errors)

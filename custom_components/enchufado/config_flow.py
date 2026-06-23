"""Config flow for Enchufado integration.

Two-step setup: credentials → CUPS selection (fetched from Datadis).
"""
import logging
from typing import Any, Dict, Optional

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.selector import selector

from .const import (
    CONF_AUTHORIZED_NIF,
    CONF_BILLS_NUMBER,
    CONF_CUPS,
    CONF_DATADIS_PASSWORD,
    CONF_DATADIS_USER,
    CONF_DISTRIBUTOR_CODE,
    CONF_POINT_TYPE,
    CONF_POWER_HIGH,
    CONF_POWER_LOW,
    CONF_ZIP_CODE,
    DOMAIN,
)
from .datadis import Datadis

_LOGGER = logging.getLogger(__name__)

_AUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DATADIS_USER): cv.string,
        vol.Required(CONF_DATADIS_PASSWORD): cv.string,
        vol.Optional(CONF_AUTHORIZED_NIF, default=""): cv.string,
    }
)

_CUPS_SCHEMA_BASE = {
    vol.Optional(CONF_POWER_HIGH, default=4.6): vol.Coerce(float),
    vol.Optional(CONF_POWER_LOW, default=4.6): vol.Coerce(float),
    vol.Optional(CONF_ZIP_CODE, default=""): cv.string,
    vol.Optional(CONF_BILLS_NUMBER, default=5): cv.positive_int,
}


class EnchufadoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    data: Optional[Dict[str, Any]] = None
    _supplies: list = []

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        errors = {}
        if user_input is not None:
            username = user_input[CONF_DATADIS_USER].strip()
            password = user_input[CONF_DATADIS_PASSWORD]
            authorized_nif = user_input.get(CONF_AUTHORIZED_NIF, "").strip() or None

            supplies = await Datadis.async_get_supplies(
                username=username, password=password, authorized_nif=authorized_nif
            )
            if not supplies:
                errors["base"] = "cannot_connect"
            else:
                self.data = {
                    CONF_DATADIS_USER: username,
                    CONF_DATADIS_PASSWORD: password,
                    CONF_AUTHORIZED_NIF: authorized_nif,
                }
                self._supplies = supplies
                return await self.async_step_cups()

        return self.async_show_form(step_id="user", data_schema=_AUTH_SCHEMA, errors=errors)

    async def async_step_cups(self, user_input: Optional[Dict[str, Any]] = None):
        cups_options = [
            f"{s['cups']} ({s['distributor_name']})" for s in self._supplies
        ]
        cups_schema = vol.Schema(
            {
                vol.Required(CONF_CUPS): selector({"select": {"options": cups_options}}),
                **_CUPS_SCHEMA_BASE,
            }
        )

        if user_input is not None:
            selected_label = user_input[CONF_CUPS]
            cups_value = selected_label.split(" (")[0].strip()

            supply = next((s for s in self._supplies if s["cups"] == cups_value), None)
            if supply is None:
                return self.async_abort(reason="cups_not_found")

            self.data.update(
                {
                    CONF_CUPS: cups_value,
                    CONF_DISTRIBUTOR_CODE: supply["distributor_code"],
                    CONF_POINT_TYPE: supply["point_type"],
                    CONF_POWER_HIGH: user_input[CONF_POWER_HIGH],
                    CONF_POWER_LOW: user_input[CONF_POWER_LOW],
                    CONF_ZIP_CODE: user_input.get(CONF_ZIP_CODE, "").strip() or None,
                    CONF_BILLS_NUMBER: user_input[CONF_BILLS_NUMBER],
                }
            )
            return self.async_create_entry(title=cups_value, data=self.data)

        return self.async_show_form(step_id="cups", data_schema=cups_schema, errors={})

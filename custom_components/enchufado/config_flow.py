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
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .const import (
    CONF_AUTHORIZED_NIF,
    CONF_BILLING_CYCLE_DAY,
    CONF_CUPS,
    CONF_DATADIS_PASSWORD,
    CONF_DATADIS_USER,
    CONF_DISTRIBUTOR_CODE,
    CONF_ESIOS_TOKEN,
    CONF_POINT_TYPE,
    CONF_POWER_HIGH,
    CONF_POWER_LOW,
    CONF_ZIP_CODE,
    DEFAULT_BILLING_CYCLE_DAY,
    DOMAIN,
)
from .datadis import async_get_contract_detail, async_get_supplies, async_login
from .ree import validate_esios_token

_LOGGER = logging.getLogger(__name__)

_AUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DATADIS_USER): cv.string,
        vol.Required(CONF_DATADIS_PASSWORD): cv.string,
        vol.Optional(CONF_AUTHORIZED_NIF, default=""): cv.string,
        vol.Required(CONF_ESIOS_TOKEN): cv.string,
    }
)

_CYCLE_DAY_SELECTOR = selector(
    {"number": {"min": 1, "max": 28, "step": 1, "mode": "box"}}
)

_RECONFIGURE_SCHEMA = vol.Schema({vol.Required(CONF_ESIOS_TOKEN): cv.string})


class EnchufadoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EnchufadoOptionsFlow()

    _supplies: list = []
    _token: str = None
    data: Dict[str, Any] = {}

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        errors = {}
        if user_input is not None:
            username = user_input[CONF_DATADIS_USER].strip()
            password = user_input[CONF_DATADIS_PASSWORD]
            authorized_nif = user_input.get(CONF_AUTHORIZED_NIF, "").strip() or None
            esios_token = user_input[CONF_ESIOS_TOKEN].strip()

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
                        CONF_ESIOS_TOKEN: esios_token,
                    }
                    return await self.async_step_cups()

        return self.async_show_form(step_id="user", data_schema=_AUTH_SCHEMA, errors=errors)

    async def async_step_cups(self, user_input: Optional[Dict[str, Any]] = None):
        cups_options = [
            f"{s['cups']} ({s['distributor_name']})" for s in self._supplies
        ]
        cups_schema = vol.Schema(
            {
                vol.Required(CONF_CUPS): selector({"select": {"options": cups_options}}),
                vol.Optional(
                    CONF_BILLING_CYCLE_DAY, default=DEFAULT_BILLING_CYCLE_DAY
                ): _CYCLE_DAY_SELECTOR,
            }
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
                    CONF_ZIP_CODE: supply.get("postal_code") or "",
                    CONF_BILLING_CYCLE_DAY: int(
                        user_input.get(CONF_BILLING_CYCLE_DAY) or DEFAULT_BILLING_CYCLE_DAY
                    ),
                }
            )
            return self.async_create_entry(title=cups_value, data=self.data)

        return self.async_show_form(step_id="cups", data_schema=cups_schema, errors=errors)

    async def async_step_reauth(self, entry_data: Dict[str, Any]):
        """ESIOS rejected the stored token during an import."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: Optional[Dict[str, Any]] = None):
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            esios_token = user_input[CONF_ESIOS_TOKEN].strip()
            error = await validate_esios_token(esios_token)
            if error is None:
                # The entry's update listener reloads the integration.
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_ESIOS_TOKEN: esios_token}
                )
                return self.async_abort(reason="reauth_successful")
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=_RECONFIGURE_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(self, user_input: Optional[Dict[str, Any]] = None):
        """Add or change the ESIOS token of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            esios_token = user_input[CONF_ESIOS_TOKEN].strip()
            error = await validate_esios_token(esios_token)
            if error is None:
                # The entry's update listener reloads the integration.
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_ESIOS_TOKEN: esios_token}
                )
                return self.async_abort(reason="reconfigure_successful")
            errors["base"] = error

        return self.async_show_form(
            step_id="reconfigure", data_schema=_RECONFIGURE_SCHEMA, errors=errors
        )


class EnchufadoOptionsFlow(config_entries.OptionsFlow):
    """Edit the billing cycle day after setup (Datadis does not publish it)."""

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            day = int(user_input[CONF_BILLING_CYCLE_DAY])
            return self.async_create_entry(data={CONF_BILLING_CYCLE_DAY: day})

        current = self.config_entry.options.get(
            CONF_BILLING_CYCLE_DAY,
            self.config_entry.data.get(CONF_BILLING_CYCLE_DAY, DEFAULT_BILLING_CYCLE_DAY),
        )
        schema = vol.Schema(
            {vol.Required(CONF_BILLING_CYCLE_DAY, default=int(current)): _CYCLE_DAY_SELECTOR}
        )
        return self.async_show_form(step_id="init", data_schema=schema)

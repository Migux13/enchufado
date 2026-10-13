"""Repair flows for Enchufado."""
import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv

from .const import CONF_ESIOS_TOKEN
from .ree import validate_esios_token


class MissingEsiosTokenRepairFlow(RepairsFlow):
    """Ask for the ESIOS token from the repair itself (same as Reconfigure)."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input=None) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None) -> data_entry_flow.FlowResult:
        errors = {}
        if user_input is not None:
            token = user_input[CONF_ESIOS_TOKEN].strip()
            error = await validate_esios_token(token)
            if error is None:
                entry = self.hass.config_entries.async_get_entry(self._entry_id)
                if entry is None:
                    return self.async_abort(reason="entry_not_found")
                # The entry's update listener reloads the integration.
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_ESIOS_TOKEN: token}
                )
                return self.async_create_entry(data={})
            errors["base"] = error

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Required(CONF_ESIOS_TOKEN): cv.string}),
            errors=errors,
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    """Create the flow for a fixable issue."""
    return MissingEsiosTokenRepairFlow((data or {})["entry_id"])

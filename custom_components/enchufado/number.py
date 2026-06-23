"""Number entities for Enchufado integration."""
import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([EnchufadoBillsNumber(entry)], True)


class EnchufadoBillsNumber(NumberEntity, RestoreEntity):
    """Slider to control how many historical bills are shown in the summary."""

    _attr_unique_id = f"{DOMAIN}_bills_number"
    _attr_has_entity_name = True
    _attr_name = "Facturas a mostrar"
    _attr_icon = "mdi:receipt-text-outline"
    _attr_native_min_value = 1
    _attr_native_max_value = 24
    _attr_native_step = 1
    _attr_native_value = 5.0
    _attr_mode = NumberMode.SLIDER

    def __init__(self, entry) -> None:
        self._entry_id = entry.entry_id

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, DOMAIN)},
            name="Enchufado",
            manufacturer="xMigux",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state and state.state not in ("unknown", "unavailable"):
            try:
                self._attr_native_value = float(state.state)
            except (ValueError, TypeError):
                pass

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = int(value)
        self.async_write_ha_state()

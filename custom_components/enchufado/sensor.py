"""Maximum demanded power sensors (one per energy period) for Enchufado."""
import datetime
import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfPower
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import maxpower
from .const import DOMAIN, SIGNAL_MAX_POWER
from .coordinator import EnchufadoCoordinator

_LOGGER = logging.getLogger(__name__)

_PERIOD_NAMES = {1: "P1 (punta)", 2: "P2 (llano)", 3: "P3 (valle)"}


async def async_setup_entry(hass, entry, async_add_entities):
    await EnchufadoCoordinator.load_max_power_history(hass)
    async_add_entities([EnchufadoMaxPowerSensor(period) for period in maxpower.PERIODS])


class EnchufadoMaxPowerSensor(SensorEntity):
    """Maximum power demanded in the last closed month for one energy period."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_icon = "mdi:flash-alert-outline"

    def __init__(self, period: int) -> None:
        self._period = period
        self._attr_unique_id = f"{DOMAIN}_max_power_p{period}"
        self._attr_name = f"Potencia máxima demandada {_PERIOD_NAMES[period]}"
        self._refresh()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, DOMAIN)}, name="Enchufado", manufacturer="xMigux")

    def _refresh(self) -> None:
        values = maxpower.sensor_values(
            EnchufadoCoordinator.max_power_history,
            datetime.date.today(),
            EnchufadoCoordinator.contracted_on,
        )[self._period]
        if values is None:
            self._attr_native_value = None
            self._attr_extra_state_attributes = {}
            return
        self._attr_native_value = values["kw"]
        self._attr_extra_state_attributes = {
            "month": values["month"],
            "max_date": values["date"],
            "max_time": values["time"],
            "last_12_months": values["last_12_months"],
            "contracted_power_kw": values["contracted_kw"],
        }

    async def async_added_to_hass(self) -> None:
        def _on_update() -> None:
            self._refresh()
            self.async_write_ha_state()

        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_MAX_POWER, _on_update))

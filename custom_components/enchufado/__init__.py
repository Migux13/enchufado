"""Enchufado — PVPC energy statistics via Datadis."""
import asyncio
import logging
from os import makedirs
from os.path import exists
from random import randint

from homeassistant.helpers.event import async_track_time_change

from .const import DOMAIN, USER_FILES_PATH
from .coordinator import EnchufadoCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry) -> bool:
    _LOGGER.debug("async_setup_entry: entry_id=%s", entry.entry_id)
    hass_data = dict(entry.data)
    EnchufadoCoordinator.set_config(hass_data, hass)

    unsub = entry.add_update_listener(options_update_listener)
    hass_data["unsub_options_update_listener"] = unsub
    hass.data[DOMAIN][entry.entry_id] = hass_data

    await hass.async_add_executor_job(_setup_services, hass)
    hass.async_create_task(EnchufadoCoordinator.import_energy_data(hass))
    return True


def _setup_services(hass) -> None:
    async def _handle_import(call):
        hass.async_create_task(EnchufadoCoordinator.import_energy_data(hass))

    async def _handle_force_import(call):
        hass.async_create_task(EnchufadoCoordinator.import_energy_data(hass, True))

    async def _handle_reprocess(call):
        hass.async_create_task(EnchufadoCoordinator.reprocess_energy_data(hass))

    async def _handle_scheduled_import(now):
        await asyncio.sleep(randint(0, 3600))
        hass.async_create_task(EnchufadoCoordinator.import_energy_data(hass))

    hass.services.register(DOMAIN, "import_energy_data", _handle_import)
    hass.services.register(DOMAIN, "force_import_energy_data", _handle_force_import)
    hass.services.register(DOMAIN, "reprocess_energy_data", _handle_reprocess)
    async_track_time_change(hass, _handle_scheduled_import, hour=6, minute=30, second=0)


async def options_update_listener(hass, config_entry):
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass, entry) -> bool:
    entry_data = hass.data[DOMAIN].pop(entry.entry_id)
    entry_data["unsub_options_update_listener"]()
    return True


def setup(hass, config):
    if not exists(USER_FILES_PATH):
        makedirs(USER_FILES_PATH)
    hass.data.setdefault(DOMAIN, {})
    return True

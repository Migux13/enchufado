"""Coordinator for Enchufado integration.

Statistics handling and data persistence derived from pvpc_energy by yinyang17
(https://github.com/yinyang17/pvpc_energy), used under MIT licence.
"""
import datetime
import logging
import time
from os import makedirs
from os.path import exists

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import CURRENCY_EURO, UnitOfEnergy
from homeassistant.util.unit_conversion import EnergyConverter

from .const import (
    BILLING_PERIODS_FILE,
    CONF_AUTHORIZED_NIF,
    CONF_CUPS,
    CONF_DATADIS_PASSWORD,
    CONF_DATADIS_USER,
    CONF_DISTRIBUTOR_CODE,
    CONF_POINT_TYPE,
    CONF_POWER_HIGH,
    CONF_POWER_LOW,
    CONSUMPTION_STATISTIC_ID,
    CONSUMPTION_STATISTIC_NAME,
    COST_STATISTIC_ID,
    COST_STATISTIC_NAME,
    CURRENT_BILL_STATE,
    DOMAIN,
    ENERGY_FILE,
    USER_FILES_PATH,
)
from .datadis import Datadis
from .ree import REE

_LOGGER = logging.getLogger(__name__)


class EnchufadoCoordinator:
    datadis_user = None
    datadis_password = None
    cups = None
    authorized_nif = None
    power_high = None
    power_low = None

    consumption_metadata = StatisticMetaData(
        name=CONSUMPTION_STATISTIC_NAME,
        mean_type=StatisticMeanType.NONE,
        unit_class=EnergyConverter.UNIT_CLASS,
        has_sum=True,
        source=DOMAIN,
        statistic_id=CONSUMPTION_STATISTIC_ID,
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    )
    cost_metadata = StatisticMetaData(
        name=COST_STATISTIC_NAME,
        mean_type=StatisticMeanType.NONE,
        unit_class=None,
        has_sum=True,
        source=DOMAIN,
        statistic_id=COST_STATISTIC_ID,
        unit_of_measurement=CURRENCY_EURO,
    )

    @staticmethod
    def set_config(config, hass):
        _LOGGER.debug("set_config: %s", {k: v for k, v in config.items() if "password" not in k})
        EnchufadoCoordinator.datadis_user = config[CONF_DATADIS_USER]
        EnchufadoCoordinator.datadis_password = config[CONF_DATADIS_PASSWORD]
        EnchufadoCoordinator.cups = config[CONF_CUPS]
        EnchufadoCoordinator.authorized_nif = config.get(CONF_AUTHORIZED_NIF)
        EnchufadoCoordinator.power_high = config.get(CONF_POWER_HIGH, 4.6)
        EnchufadoCoordinator.power_low = config.get(CONF_POWER_LOW, 4.6)

        Datadis.setup(
            username=config[CONF_DATADIS_USER],
            password=config[CONF_DATADIS_PASSWORD],
            cups=config[CONF_CUPS],
            distributor_code=config[CONF_DISTRIBUTOR_CODE],
            point_type=config[CONF_POINT_TYPE],
            authorized_nif=config.get(CONF_AUTHORIZED_NIF),
        )

    @staticmethod
    async def reprocess_energy_data(hass):
        _LOGGER.debug("reprocess_energy_data()")
        consumptions, prices = await EnchufadoCoordinator.load_energy_data(hass, ENERGY_FILE)
        if consumptions:
            c_stats, cost_stats = EnchufadoCoordinator.create_statistics(0, consumptions, prices, 0, 0)
            get_instance(hass).async_add_executor_job(
                async_add_external_statistics, hass, EnchufadoCoordinator.consumption_metadata, c_stats
            )
            get_instance(hass).async_add_executor_job(
                async_add_external_statistics, hass, EnchufadoCoordinator.cost_metadata, cost_stats
            )

    @staticmethod
    async def import_energy_data(hass, force_update=False):
        _LOGGER.debug("import_energy_data(force_update=%s)", force_update)

        start_date = datetime.datetime(
            year=datetime.date.today().year - 2,
            month=datetime.date.today().month,
            day=1,
        ).date()
        end_date = datetime.date.today() - datetime.timedelta(days=2)

        consumptions, prices = await EnchufadoCoordinator.load_energy_data(hass, ENERGY_FILE, start_date)
        consumptions_len = len(consumptions)
        prices_len = len(prices)

        first_consumption_date = None
        last_consumption_date = None
        if consumptions:
            first_consumption_date = datetime.datetime.fromtimestamp(min(consumptions.keys())).date()
            last_consumption_date = datetime.datetime.fromtimestamp(max(consumptions.keys())).date()

        # --- Fetch consumption from Datadis ---
        datadis_start = start_date
        if not force_update and last_consumption_date and last_consumption_date >= start_date:
            if end_date <= last_consumption_date:
                datadis_start = None  # Already up to date
            else:
                datadis_start = last_consumption_date + datetime.timedelta(days=1)

        if force_update or datadis_start is not None:
            fetch_from = start_date if force_update else (datadis_start or start_date)
            _LOGGER.info("Fetching Datadis consumption: %s → %s", fetch_from, end_date)
            new_consumptions = await Datadis.consumptions(fetch_from, end_date)
            if new_consumptions:
                consumptions.update(new_consumptions)

        # --- Fetch PVPC prices from REE ---
        first_price_date = None
        last_price_date = None
        if prices:
            first_price_date = datetime.datetime.fromtimestamp(min(prices.keys())).date()
            last_price_date = datetime.datetime.fromtimestamp(max(prices.keys())).date()

        if force_update or first_price_date is None or first_price_date > start_date:
            await EnchufadoCoordinator.get_data(REE.pvpc, start_date, end_date, prices, 28, force_update)
        elif end_date > last_price_date:
            await EnchufadoCoordinator.get_data(
                REE.pvpc, last_price_date + datetime.timedelta(days=1), end_date, prices, 28
            )

        # --- Save and update statistics if data changed ---
        if force_update or len(consumptions) > consumptions_len or len(prices) > prices_len:
            await EnchufadoCoordinator.save_energy_data(hass, ENERGY_FILE, consumptions, prices)

            if consumptions:
                last_stat = await get_instance(hass).async_add_executor_job(
                    get_last_statistics, hass, 1, CONSUMPTION_STATISTIC_ID, True, set()
                )
                if (
                    force_update
                    or not last_stat
                    or first_consumption_date is None
                    or first_consumption_date
                    != datetime.datetime.fromtimestamp(min(consumptions.keys())).date()
                    or last_consumption_date
                    != datetime.datetime.fromtimestamp(
                        last_stat[CONSUMPTION_STATISTIC_ID][0]["start"], datetime.UTC
                    ).date()
                ):
                    c_stats, cost_stats = EnchufadoCoordinator.create_statistics(
                        0, consumptions, prices, 0, 0
                    )
                else:
                    start = datetime.datetime.fromtimestamp(
                        last_stat[CONSUMPTION_STATISTIC_ID][0]["start"], datetime.UTC
                    )
                    stats = await get_instance(hass).async_add_executor_job(
                        statistics_during_period,
                        hass,
                        start,
                        None,
                        {CONSUMPTION_STATISTIC_ID, COST_STATISTIC_ID},
                        "hour",
                        None,
                        {"sum"},
                    )
                    total_consumption = stats[CONSUMPTION_STATISTIC_ID][0]["sum"]
                    total_cost = stats[COST_STATISTIC_ID][0]["sum"]
                    last_ts = stats[COST_STATISTIC_ID][0]["start"]
                    c_stats, cost_stats = EnchufadoCoordinator.create_statistics(
                        last_ts, consumptions, prices, total_consumption, total_cost
                    )

                _LOGGER.info(
                    "Inserting statistics: %d consumption, %d cost records",
                    len(c_stats),
                    len(cost_stats),
                )
                get_instance(hass).async_add_executor_job(
                    async_add_external_statistics,
                    hass,
                    EnchufadoCoordinator.consumption_metadata,
                    c_stats,
                )
                get_instance(hass).async_add_executor_job(
                    async_add_external_statistics,
                    hass,
                    EnchufadoCoordinator.cost_metadata,
                    cost_stats,
                )

        _LOGGER.debug("import_energy_data() done")

    @staticmethod
    async def get_data(getter, start_date, end_date, data, days, force_update=False):
        """Fetch data in chunks, skipping already-cached date ranges."""
        data_len = len(data)
        request_start_date = end_date + datetime.timedelta(days=1)
        while request_start_date > start_date:
            request_end_date = request_start_date - datetime.timedelta(days=1)
            while (
                not force_update
                and int(time.mktime(request_end_date.timetuple())) in data
                and request_end_date >= start_date
            ):
                request_end_date -= datetime.timedelta(days=1)
            if request_end_date < start_date:
                break
            request_start_date = request_end_date - datetime.timedelta(days=1)
            while (
                (force_update or int(time.mktime(request_start_date.timetuple())) not in data)
                and request_start_date >= start_date
                and (request_end_date - request_start_date).days < days
            ):
                request_start_date -= datetime.timedelta(days=1)
            request_start_date += datetime.timedelta(days=1)
            new_data = await getter(request_start_date, request_end_date)
            if new_data is None:
                break
            if new_data:
                data.update(new_data)
        _LOGGER.debug("get_data: +%d new records", len(data) - data_len)

    @staticmethod
    async def load_energy_data(hass, file_path, start_date=None):
        consumptions = {}
        prices = {}
        if not exists(file_path):
            return consumptions, prices

        with await hass.async_add_executor_job(open, file_path, "r") as f:
            has_reading_type = "reading_type" in f.readline()
            for line in f:
                parts = line.rstrip("\n").split(",")
                if has_reading_type:
                    timestamp, consumption, price, reading_type = parts[-4:]
                else:
                    timestamp, consumption, price = parts[-3:]
                    reading_type = ""
                timestamp = int(timestamp)
                if consumption not in ("", "-"):
                    consumptions[timestamp] = {"value": float(consumption), "reading_type": reading_type}
                if price not in ("", "-"):
                    prices[timestamp] = float(price)

        if start_date and consumptions:
            timestamps = sorted(consumptions.keys())
            previous_day = datetime.datetime.fromtimestamp(timestamps[0]).date()
            hours = 0
            for ts in timestamps:
                day = datetime.datetime.fromtimestamp(ts).date()
                if day == previous_day:
                    hours += 1
                elif hours in (23, 24, 25) or day < start_date:
                    previous_day = day
                    hours = 1
                else:
                    consumptions = {
                        t: consumptions[t]
                        for t in timestamps
                        if datetime.datetime.fromtimestamp(t).date() < previous_day
                    }
                    break

        return consumptions, prices

    @staticmethod
    async def save_energy_data(hass, file_path, consumptions, prices):
        timestamps = sorted(set(list(consumptions.keys()) + list(prices.keys())))
        with await hass.async_add_executor_job(open, file_path, "w") as f:
            f.write("date,timestamp,consumption,price,reading_type\n")
            for ts in timestamps:
                date = datetime.datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H")
                c = consumptions.get(ts)
                consumption = "" if c is None else c["value"]
                reading_type = "" if c is None else c["reading_type"]
                price = "" if ts not in prices else prices[ts]
                f.write(f"{date},{ts},{consumption},{price},{reading_type}\n")

    @staticmethod
    def create_statistics(last_statistic_timestamp, consumptions, prices, total_energy_consumption, total_energy_cost):
        _LOGGER.debug(
            "create_statistics: last_ts=%s, consumptions=%d, prices=%d",
            last_statistic_timestamp, len(consumptions), len(prices),
        )
        day_energy_consumption = 0.0
        day_energy_cost = 0.0
        consumption_statistics = []
        cost_statistics = []
        timestamp = max(min(consumptions.keys()), last_statistic_timestamp + 3600)
        last_timestamp = max(consumptions.keys())

        while timestamp <= last_timestamp:
            c = consumptions.get(timestamp)
            consumption = c["value"] if c else 0.0
            total_energy_consumption += consumption
            day_energy_consumption += consumption
            if datetime.datetime.fromtimestamp(timestamp).hour == 0:
                day_energy_consumption = consumption

            start = datetime.datetime.fromtimestamp(timestamp, datetime.UTC)
            consumption_statistics.append(
                StatisticData(start=start, state=day_energy_consumption, sum=total_energy_consumption)
            )

            price = prices.get(timestamp, 0.0)
            hour_cost = consumption * price
            total_energy_cost += hour_cost
            day_energy_cost += hour_cost
            if datetime.datetime.fromtimestamp(timestamp).hour == 0:
                day_energy_cost = hour_cost
            cost_statistics.append(
                StatisticData(start=start, state=day_energy_cost, sum=total_energy_cost)
            )
            timestamp += 3600

        return consumption_statistics, cost_statistics

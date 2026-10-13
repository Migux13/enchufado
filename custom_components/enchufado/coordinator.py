"""Coordinator for Enchufado integration.

Statistics handling and data persistence derived from pvpc_energy by yinyang17
(https://github.com/yinyang17/pvpc_energy), used under MIT licence.
"""
import asyncio
import datetime
import logging
from functools import partial
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

from homeassistant.helpers import entity_registry as er

from .cnmc import CNMCUnavailable, calculate_bill
from .const import (
    BILLING_PERIODS_FILENAME,
    BILL_STATISTIC_ID,
    BILL_STATISTIC_NAME,
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
    DATADIS_QUERIES_FILENAME,
    CONSUMPTION_STATISTIC_ID,
    CONSUMPTION_STATISTIC_NAME,
    COST_STATISTIC_ID,
    COST_STATISTIC_NAME,
    CURRENT_BILL_STATE,
    DOMAIN,
    ENERGY_FILENAME,
    TOTAL_COST_SIGNATURE_FILENAME,
    TOTAL_COST_STATISTIC_ID,
    TOTAL_COST_STATISTIC_NAME,
)
from . import tariffs
from .datadis import Datadis
from . import quota
from .ree import REE, ESIOSAuthError
from .util import MADRID_TZ, madrid_timestamp

_LOGGER = logging.getLogger(__name__)

# Computed fields of a billing period; dropped to force a recalculation.
_BILL_FIELDS = ("total_cost", "total_consumption", "power_cost", "energy_cost", "rent_cost", "tax_cost")


class EnchufadoCoordinator:
    # serialises the plan -> send -> record sequence of Datadis consumption queries
    _datadis_lock = asyncio.Lock()
    datadis_user = None
    datadis_password = None
    cups = None
    authorized_nif = None
    power_high = None
    power_low = None
    billing_cycle_day = DEFAULT_BILLING_CYCLE_DAY
    zip_code = None
    esios_token = None
    config_entry = None
    user_files_path = None
    energy_file = None
    billing_periods_file = None
    total_cost_signature_file = None
    datadis_queries_file = None

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
    total_cost_metadata = StatisticMetaData(
        name=TOTAL_COST_STATISTIC_NAME,
        mean_type=StatisticMeanType.NONE,
        unit_class=None,
        has_sum=True,
        source=DOMAIN,
        statistic_id=TOTAL_COST_STATISTIC_ID,
        unit_of_measurement=CURRENCY_EURO,
    )
    bill_metadata = StatisticMetaData(
        name=BILL_STATISTIC_NAME,
        mean_type=StatisticMeanType.NONE,
        unit_class=None,
        has_sum=True,
        source=DOMAIN,
        statistic_id=BILL_STATISTIC_ID,
        unit_of_measurement=CURRENCY_EURO,
    )

    _SENSITIVE_CONFIG_KEYS = {CONF_DATADIS_USER, CONF_DATADIS_PASSWORD, CONF_AUTHORIZED_NIF, CONF_ESIOS_TOKEN}

    @staticmethod
    def set_config(config, hass):
        _LOGGER.debug(
            "set_config: %s",
            {k: v for k, v in config.items() if k not in EnchufadoCoordinator._SENSITIVE_CONFIG_KEYS},
        )
        EnchufadoCoordinator.datadis_user = config[CONF_DATADIS_USER]
        EnchufadoCoordinator.datadis_password = config[CONF_DATADIS_PASSWORD]
        EnchufadoCoordinator.cups = config[CONF_CUPS]
        EnchufadoCoordinator.authorized_nif = config.get(CONF_AUTHORIZED_NIF)
        EnchufadoCoordinator.power_high = config.get(CONF_POWER_HIGH, 4.6)
        EnchufadoCoordinator.power_low = config.get(CONF_POWER_LOW, 4.6)
        EnchufadoCoordinator.zip_code = config.get(CONF_ZIP_CODE, "")
        EnchufadoCoordinator.billing_cycle_day = int(
            config.get(CONF_BILLING_CYCLE_DAY) or DEFAULT_BILLING_CYCLE_DAY
        )
        EnchufadoCoordinator.esios_token = config.get(CONF_ESIOS_TOKEN)
        if not EnchufadoCoordinator.esios_token:
            _LOGGER.warning(
                "No ESIOS API token configured — PVPC prices won't update. "
                "Add one from Settings → Devices & services → Enchufado → Reconfigure."
            )

        EnchufadoCoordinator.user_files_path = hass.config.path(DOMAIN)
        EnchufadoCoordinator.energy_file = f"{EnchufadoCoordinator.user_files_path}/{ENERGY_FILENAME}"
        EnchufadoCoordinator.datadis_queries_file = (
            f"{EnchufadoCoordinator.user_files_path}/{DATADIS_QUERIES_FILENAME}"
        )
        EnchufadoCoordinator.billing_periods_file = (
            f"{EnchufadoCoordinator.user_files_path}/{BILLING_PERIODS_FILENAME}"
        )

        EnchufadoCoordinator.total_cost_signature_file = (
            f"{EnchufadoCoordinator.user_files_path}/{TOTAL_COST_SIGNATURE_FILENAME}"
        )

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
        consumptions, prices = await EnchufadoCoordinator.load_energy_data(
            hass, EnchufadoCoordinator.energy_file
        )
        if consumptions:
            c_stats, cost_stats = EnchufadoCoordinator.create_statistics(0, consumptions, prices, 0, 0)
            async_add_external_statistics(hass, EnchufadoCoordinator.consumption_metadata, c_stats)
            async_add_external_statistics(hass, EnchufadoCoordinator.cost_metadata, cost_stats)
            await EnchufadoCoordinator.update_total_cost_statistics(hass, consumptions, prices, True, set())

    @staticmethod
    async def import_energy_data(hass, force_update=False):
        _LOGGER.debug("import_energy_data(force_update=%s)", force_update)
        energy_file_exists = await hass.async_add_executor_job(exists, EnchufadoCoordinator.energy_file)
        _LOGGER.debug("energy_data path: %s (exists=%s)", EnchufadoCoordinator.energy_file, energy_file_exists)

        # Datadis rejects months whose 1st day exceeds the 2-year window.
        # Advancing by 1 month keeps us safely within the limit.
        _today = datetime.date.today()
        if _today.month == 12:
            start_date = datetime.date(_today.year - 1, 1, 1)
        else:
            start_date = datetime.date(_today.year - 2, _today.month + 1, 1)
        # Datadis already publishes D+1 and the day-ahead PVPC prices are out by the evening
        end_date = datetime.date.today() + datetime.timedelta(days=1)

        consumptions, prices = await EnchufadoCoordinator.load_energy_data(
            hass, EnchufadoCoordinator.energy_file, start_date
        )
        consumptions_len = len(consumptions)
        prices_len = len(prices)
        price_keys_before = set(prices)

        first_consumption_date = None
        last_consumption_date = None
        if consumptions:
            first_consumption_date = datetime.datetime.fromtimestamp(min(consumptions.keys()), MADRID_TZ).date()
            last_consumption_date = datetime.datetime.fromtimestamp(max(consumptions.keys()), MADRID_TZ).date()

        # --- Fetch consumption from Datadis: at most one query a day (see quota.py) ---
        consumption_corrected = False
        async with EnchufadoCoordinator._datadis_lock:
            now = datetime.datetime.now(MADRID_TZ)
            queries = quota.load(
                await hass.async_add_executor_job(EnchufadoCoordinator._read_text, EnchufadoCoordinator.datadis_queries_file)
            )
            query = quota.plan_query(
                now.date(), queries, now, last_consumption_date, start_date, force_update
            )
            if query is None:
                _LOGGER.info("No Datadis consumption query due now (one per day, none repeated within 24 h)")
            else:
                _LOGGER.info("Datadis consumption query: %s → %s", query[0], query[1])
                new_consumptions, status = await Datadis.consumptions(*query)
                if status is not None:  # a response came back, so Datadis counted the query
                    queries = quota.record(queries, query, now)
                    await hass.async_add_executor_job(
                        EnchufadoCoordinator._write_text,
                        EnchufadoCoordinator.datadis_queries_file,
                        quota.dump(queries),
                    )
                # Keep every complete day returned; days that changed (estimated -> real) are overwritten
                consumption_corrected = any(
                    ts in consumptions and consumptions[ts] != value for ts, value in new_consumptions.items()
                )
                consumptions.update(new_consumptions)

        # --- Fetch PVPC prices from REE ---
        first_price_date = None
        last_price_date = None
        if prices:
            first_price_date = datetime.datetime.fromtimestamp(min(prices.keys()), MADRID_TZ).date()
            last_price_date = datetime.datetime.fromtimestamp(max(prices.keys()), MADRID_TZ).date()

        old_price_timestamps = set(prices)
        if EnchufadoCoordinator.esios_token:
            ree_pvpc = partial(REE.pvpc, token=EnchufadoCoordinator.esios_token)
            try:
                if force_update or first_price_date is None or first_price_date > start_date:
                    await EnchufadoCoordinator.get_data(ree_pvpc, start_date, end_date, prices, 28, force_update)
                elif end_date > last_price_date:
                    await EnchufadoCoordinator.get_data(
                        ree_pvpc, last_price_date + datetime.timedelta(days=1), end_date, prices, 28
                    )
            except ESIOSAuthError as err:
                _LOGGER.warning("%s: asking for a new ESIOS token", err)
                if EnchufadoCoordinator.config_entry is not None:
                    EnchufadoCoordinator.config_entry.async_start_reauth(hass)

        # --- Save and update statistics if data changed ---
        if (
            force_update
            or consumption_corrected
            or len(consumptions) > consumptions_len
            or len(prices) > prices_len
        ):
            await EnchufadoCoordinator.save_energy_data(
                hass, EnchufadoCoordinator.energy_file, consumptions, prices
            )

            if consumptions:
                last_stat = await get_instance(hass).async_add_executor_job(
                    get_last_statistics, hass, 1, CONSUMPTION_STATISTIC_ID, True, set()
                )
                last_cost_stat = await get_instance(hass).async_add_executor_job(
                    get_last_statistics, hass, 1, COST_STATISTIC_ID, True, set()
                )
                # Prices that arrive late (e.g. after setting the ESIOS token) for hours
                # whose cost statistic was already written at price 0 are not reached by
                # the incremental path below: rebuild the cost statistics from scratch.
                backfilled_prices = False
                if last_cost_stat and last_cost_stat.get(COST_STATISTIC_ID):
                    last_cost_ts = last_cost_stat[COST_STATISTIC_ID][0]["start"]
                    backfilled_prices = any(
                        ts <= last_cost_ts for ts in prices if ts not in old_price_timestamps
                    )
                    if backfilled_prices:
                        _LOGGER.info("New prices for hours already in the cost statistic: rebuilding it")
                if (
                    force_update
                    or consumption_corrected
                    or backfilled_prices
                    or not last_stat
                    or first_consumption_date is None
                    or first_consumption_date
                    != datetime.datetime.fromtimestamp(min(consumptions.keys()), MADRID_TZ).date()
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
                async_add_external_statistics(hass, EnchufadoCoordinator.consumption_metadata, c_stats)
                async_add_external_statistics(hass, EnchufadoCoordinator.cost_metadata, cost_stats)

        # --- Total cost (energy + fixed terms + taxes) ---
        await EnchufadoCoordinator.update_total_cost_statistics(
            hass, consumptions, prices, force_update, price_keys_before
        )

        # --- Billing simulation via CNMC ---
        billing_periods = await EnchufadoCoordinator.get_billing_periods(hass, consumptions)
        await EnchufadoCoordinator.calculate_bills(hass, billing_periods, consumptions, force_update, prices)

        _LOGGER.debug("import_energy_data() done")

    @staticmethod
    def _read_text(file_path):
        """Blocking: file contents or None. Call via executor."""
        if not exists(file_path):
            return None
        with open(file_path, "r") as f:
            return f.read().strip()

    @staticmethod
    def _write_text(file_path, text):
        """Blocking: write a small text file. Call via executor."""
        with open(file_path, "w") as f:
            f.write(text)

    @staticmethod
    async def update_total_cost_statistics(hass, consumptions, prices, force_update, price_keys_before):
        """Write enchufado:total_cost: energy + prorated fixed terms + taxes, hour by hour.

        Rebuilt from scratch when forced, when the regulated-values table or the contracted
        power changed (signature), when prices arrive for hours already written, or when
        there is no statistic yet; otherwise it only appends the new hours.
        """
        if not consumptions:
            return
        power_high = EnchufadoCoordinator.power_high or 4.6
        power_low = EnchufadoCoordinator.power_low or 4.6
        cycle_day = EnchufadoCoordinator.billing_cycle_day
        signature = tariffs.signature(power_high, power_low, cycle_day)
        stored_signature = await hass.async_add_executor_job(
            EnchufadoCoordinator._read_text, EnchufadoCoordinator.total_cost_signature_file
        )
        last = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, TOTAL_COST_STATISTIC_ID, True, {"state", "sum"}
        )
        last_row = last[TOTAL_COST_STATISTIC_ID][0] if last and last.get(TOTAL_COST_STATISTIC_ID) else None

        full = force_update or stored_signature != signature or last_row is None
        if not full and any(ts <= last_row["start"] for ts in prices if ts not in price_keys_before):
            full = True
        if full:
            _LOGGER.info("Rebuilding %s from scratch", TOTAL_COST_STATISTIC_ID)
            first_ts, total, day_cost = min(consumptions), 0.0, 0.0
        else:
            first_ts = int(last_row["start"]) + 3600
            total, day_cost = last_row["sum"] or 0.0, last_row["state"] or 0.0

        last_ts = max(consumptions)
        rows = tariffs.create_total_cost_series(
            first_ts, last_ts, consumptions, prices, power_high, power_low, day_cost, cycle_day
        )
        if rows:
            stats = []
            for ts, hour_cost, state in rows:
                total += hour_cost
                stats.append(
                    StatisticData(start=datetime.datetime.fromtimestamp(ts, datetime.UTC), state=state, sum=total)
                )
            _LOGGER.info("Inserting %d %s records", len(stats), TOTAL_COST_STATISTIC_ID)
            async_add_external_statistics(hass, EnchufadoCoordinator.total_cost_metadata, stats)
        if stored_signature != signature:
            await hass.async_add_executor_job(
                EnchufadoCoordinator._write_text, EnchufadoCoordinator.total_cost_signature_file, signature
            )

    @staticmethod
    def _log_cnmc_difference(period, consumptions, prices):
        """Debug only: local total cost of a period vs the CNMC simulated bill."""
        first_ts = madrid_timestamp(period["start_date"])
        last_ts = madrid_timestamp(period["end_date"] + datetime.timedelta(days=1)) - 3600
        rows = tariffs.create_total_cost_series(
            first_ts,
            last_ts,
            {ts: c for ts, c in consumptions.items() if first_ts <= ts <= last_ts},
            prices,
            period["power_high"],
            period["power_low"],
            cycle_day=EnchufadoCoordinator.billing_cycle_day,
        )
        local = sum(cost for _, cost, _ in rows)
        _LOGGER.debug(
            "Total cost %s→%s: local=%.2f € CNMC=%.2f € diff=%+.2f €",
            period["start_date"], period["end_date"], local, period["total_cost"], local - period["total_cost"],
        )

    @staticmethod
    async def get_data(getter, start_date, end_date, data, days, force_update=False):
        """Fetch data in chunks, skipping already-cached date ranges."""
        data_len = len(data)
        request_start_date = end_date + datetime.timedelta(days=1)
        while request_start_date > start_date:
            request_end_date = request_start_date - datetime.timedelta(days=1)
            while (
                not force_update
                and madrid_timestamp(request_end_date) in data
                and request_end_date >= start_date
            ):
                request_end_date -= datetime.timedelta(days=1)
            if request_end_date < start_date:
                break
            request_start_date = request_end_date - datetime.timedelta(days=1)
            while (
                (force_update or madrid_timestamp(request_start_date) not in data)
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
    def _read_energy_file(file_path, start_date=None):
        """Blocking: read + parse the energy CSV file. Call via executor."""
        consumptions = {}
        prices = {}
        if not exists(file_path):
            return consumptions, prices

        with open(file_path, "r") as f:
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
            previous_day = datetime.datetime.fromtimestamp(timestamps[0], MADRID_TZ).date()
            hours = 0
            for ts in timestamps:
                day = datetime.datetime.fromtimestamp(ts, MADRID_TZ).date()
                if day == previous_day:
                    hours += 1
                elif hours in (23, 24, 25) or day < start_date:
                    previous_day = day
                    hours = 1
                else:
                    consumptions = {
                        t: consumptions[t]
                        for t in timestamps
                        if datetime.datetime.fromtimestamp(t, MADRID_TZ).date() < previous_day
                    }
                    break

        return consumptions, prices

    @staticmethod
    async def load_energy_data(hass, file_path, start_date=None):
        return await hass.async_add_executor_job(
            EnchufadoCoordinator._read_energy_file, file_path, start_date
        )

    @staticmethod
    def _write_energy_file(file_path, consumptions, prices):
        """Blocking: write the energy CSV file. Call via executor."""
        timestamps = sorted(set(list(consumptions.keys()) + list(prices.keys())))
        with open(file_path, "w") as f:
            f.write("date,timestamp,consumption,price,reading_type\n")
            for ts in timestamps:
                date = datetime.datetime.fromtimestamp(ts, MADRID_TZ).strftime("%d/%m/%Y %H")
                c = consumptions.get(ts)
                consumption = "" if c is None else c["value"]
                reading_type = "" if c is None else c["reading_type"]
                price = "" if ts not in prices else prices[ts]
                f.write(f"{date},{ts},{consumption},{price},{reading_type}\n")

    @staticmethod
    async def save_energy_data(hass, file_path, consumptions, prices):
        await hass.async_add_executor_job(
            EnchufadoCoordinator._write_energy_file, file_path, consumptions, prices
        )

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
            if datetime.datetime.fromtimestamp(timestamp, MADRID_TZ).hour == 0:
                day_energy_consumption = consumption

            start = datetime.datetime.fromtimestamp(timestamp, datetime.UTC)
            consumption_statistics.append(
                StatisticData(start=start, state=day_energy_consumption, sum=total_energy_consumption)
            )

            price = prices.get(timestamp, 0.0)
            hour_cost = consumption * price
            total_energy_cost += hour_cost
            day_energy_cost += hour_cost
            if datetime.datetime.fromtimestamp(timestamp, MADRID_TZ).hour == 0:
                day_energy_cost = hour_cost
            cost_statistics.append(
                StatisticData(start=start, state=day_energy_cost, sum=total_energy_cost)
            )
            timestamp += 3600

        return consumption_statistics, cost_statistics

    # ------------------------------------------------------------------ billing

    @staticmethod
    def generate_monthly_periods(start_date, end_date, power_high, power_low):
        """Return a list of monthly billing-period dicts covering start_date..end_date."""
        periods = []
        current = start_date.replace(day=1)
        while current <= end_date:
            if current.month == 12:
                next_month = current.replace(year=current.year + 1, month=1, day=1)
            else:
                next_month = current.replace(month=current.month + 1, day=1)
            period_end = min(next_month - datetime.timedelta(days=1), end_date)
            periods.append(
                {
                    "start_date": current,
                    "end_date": period_end,
                    "power_high": power_high,
                    "power_low": power_low,
                }
            )
            current = next_month
        return periods

    @staticmethod
    def load_billing_periods(file_path):
        """Read billing periods from CSV (sync — call via async_add_executor_job)."""
        periods = []
        if not exists(file_path):
            return periods
        with open(file_path, "r") as f:
            header = f.readline().rstrip("\n").split(",")
            for line in f:
                parts = line.rstrip("\n").split(",")
                row = dict(zip(header, parts))
                try:
                    period = {
                        "start_date": datetime.datetime.strptime(row["start_date"], "%Y-%m-%d").date(),
                        "end_date": datetime.datetime.strptime(row["end_date"], "%Y-%m-%d").date(),
                        "power_high": float(row["power_high"]),
                        "power_low": float(row["power_low"]),
                    }
                    for field in ("total_cost", "total_consumption", "power_cost", "energy_cost", "rent_cost", "tax_cost"):
                        val = row.get(field, "")
                        period[field] = val if val in ("", "-") else float(val)
                    periods.append(period)
                except (ValueError, KeyError) as exc:
                    _LOGGER.warning("load_billing_periods: skipping row: %s (%s)", line.strip(), exc)
        return periods

    @staticmethod
    def save_billing_periods(file_path, billing_periods):
        """Write billing periods to CSV (sync — call via async_add_executor_job)."""
        with open(file_path, "w") as f:
            f.write(
                "start_date,end_date,power_high,power_low,"
                "total_cost,total_consumption,power_cost,energy_cost,rent_cost,tax_cost\n"
            )
            for p in billing_periods:
                f.write(
                    f"{p['start_date'].isoformat()},{p['end_date'].isoformat()},"
                    f"{p['power_high']},{p['power_low']},"
                    f"{p.get('total_cost', '')},"
                    f"{p.get('total_consumption', '')},"
                    f"{p.get('power_cost', '')},"
                    f"{p.get('energy_cost', '')},"
                    f"{p.get('rent_cost', '')},"
                    f"{p.get('tax_cost', '')}\n"
                )

    @staticmethod
    async def get_billing_periods(hass, consumptions):
        """Load existing periods from CSV and extend with any new calendar months."""
        existing = await hass.async_add_executor_job(
            EnchufadoCoordinator.load_billing_periods, EnchufadoCoordinator.billing_periods_file
        )
        if not consumptions:
            return existing

        existing_by_start = {p["start_date"]: p for p in existing}
        start_date = datetime.datetime.fromtimestamp(min(consumptions.keys()), MADRID_TZ).date()
        end_date = datetime.datetime.fromtimestamp(max(consumptions.keys()), MADRID_TZ).date()
        today = datetime.date.today()

        last_day_with_data = {}
        for ts in consumptions:
            day = datetime.datetime.fromtimestamp(ts, MADRID_TZ).date()
            key = (day.year, day.month)
            if day >= last_day_with_data.get(key, day):
                last_day_with_data[key] = day

        for p in EnchufadoCoordinator.generate_monthly_periods(
            start_date,
            end_date,
            EnchufadoCoordinator.power_high or 4.6,
            EnchufadoCoordinator.power_low or 4.6,
        ):
            if p["start_date"] not in existing_by_start:
                existing_by_start[p["start_date"]] = p
            else:
                existing_p = existing_by_start[p["start_date"]]
                is_current_month = p["start_date"].month == today.month and p["start_date"].year == today.year
                # Datadis lags ~2 days, so a month is first stored while still incomplete.
                # Also extend a closed period when there is consumption after its saved
                # end_date inside the month (this repairs rows saved truncated by earlier
                # versions too). The new end is the last day WITH data, because
                # calculate_bill() needs the period to match the first/last consumption day.
                new_end = last_day_with_data.get((p["start_date"].year, p["start_date"].month), p["end_date"])
                new_end = min(new_end, p["end_date"])
                extended = new_end > existing_p["end_date"]
                if is_current_month or extended:
                    if extended and not is_current_month:
                        _LOGGER.info(
                            "Billing period %s ended early (%s): extending to %s and recalculating",
                            p["start_date"], existing_p["end_date"], new_end,
                        )
                    existing_p["end_date"] = new_end
                    for field in _BILL_FIELDS:
                        existing_p.pop(field, None)

        result = sorted(existing_by_start.values(), key=lambda p: p["start_date"])
        return result

    @staticmethod
    async def get_bill(hass, billing_period, consumptions):
        """Extract consumption for one billing period and request CNMC estimate."""
        start_ts = madrid_timestamp(billing_period["start_date"])
        end_ts = madrid_timestamp(billing_period["end_date"]) + 86399

        period_consumptions = {
            ts: consumptions[ts]["value"]
            for ts in consumptions
            if start_ts <= ts <= end_ts
        }

        if not period_consumptions:
            _LOGGER.info(
                "get_bill: no consumption data for %s→%s",
                billing_period["start_date"], billing_period["end_date"],
            )
            return billing_period

        _LOGGER.info(
            "get_bill: calling CNMC for %s→%s (%d hours)",
            billing_period["start_date"], billing_period["end_date"], len(period_consumptions),
        )
        updated, _ = await calculate_bill(
            billing_period,
            EnchufadoCoordinator.cups,
            period_consumptions,
            EnchufadoCoordinator.zip_code or "",
        )
        return updated

    @staticmethod
    async def calculate_bills(hass, billing_periods, consumptions, force_update=False, prices=None):
        """Calculate CNMC bills for recent periods and publish enchufado.current_bill state."""
        if not billing_periods or not consumptions:
            return

        bills_number = 5
        ent_registry = er.async_get(hass)
        entity_id = ent_registry.async_get_entity_id("number", DOMAIN, "enchufado_bills_number")
        if entity_id:
            state = hass.states.get(entity_id)
            if state and state.state not in (None, "unknown", "unavailable"):
                try:
                    bills_number = int(float(state.state))
                except ValueError:
                    pass

        _LOGGER.info(
            "calculate_bills: processing %d periods, bills_number=%d",
            len(billing_periods), bills_number,
        )
        changed = False
        for period in billing_periods[-bills_number:]:
            has_cost = "total_cost" in period and period["total_cost"] not in ("", None)
            if force_update or not has_cost:
                try:
                    await EnchufadoCoordinator.get_bill(hass, period, consumptions)
                    if prices and isinstance(period.get("total_cost"), (int, float)) and _LOGGER.isEnabledFor(logging.DEBUG):
                        try:
                            EnchufadoCoordinator._log_cnmc_difference(period, consumptions, prices)
                        except Exception as diff_err:  # never block on a debug comparison
                            _LOGGER.debug("Could not compare with CNMC: %s", diff_err)
                except CNMCUnavailable as err:
                    _LOGGER.warning(
                        "CNMC comparator unavailable (%s): bill simulation skipped, "
                        "it will be retried on the next import",
                        err,
                    )
                    changed = True
                    break
                except Exception as err:
                    _LOGGER.error(
                        "calculate_bills: error processing %s: %s",
                        period["start_date"], err,
                    )
                changed = True

        if changed:
            await hass.async_add_executor_job(
                EnchufadoCoordinator.save_billing_periods,
                EnchufadoCoordinator.billing_periods_file,
                billing_periods,
            )

        calculated = [
            p for p in billing_periods
            if "total_cost" in p and p["total_cost"] not in ("", None)
        ][-bills_number:]

        if not calculated:
            return

        latest = calculated[-1]
        total_cost = latest.get("total_cost", "-")
        state_value = f"{total_cost:.2f} €" if isinstance(total_cost, (int, float)) else str(total_cost)

        hass.states.async_set(
            CURRENT_BILL_STATE,
            state_value,
            {
                "friendly_name": "Factura actual",
                "bills": [
                    {
                        "start": p["start_date"].isoformat(),
                        "end": p["end_date"].isoformat(),
                        "total_cost": p.get("total_cost"),
                        "total_consumption": p.get("total_consumption"),
                        "power_cost": p.get("power_cost"),
                        "energy_cost": p.get("energy_cost"),
                        "rent_cost": p.get("rent_cost"),
                        "tax_cost": p.get("tax_cost"),
                    }
                    for p in calculated
                ],
            },
        )
        _LOGGER.info("calculate_bills: published %d bills, latest=%.2f €", len(calculated), total_cost if isinstance(total_cost, (int, float)) else 0)

        # Insert enchufado:bill as an external statistic (one point per period)
        bill_stats = []
        cumulative = 0.0
        for p in billing_periods:
            tc = p.get("total_cost")
            if not isinstance(tc, (int, float)):
                continue
            cumulative += tc
            # Midnight Europe/Madrid (22:00/23:00 UTC), like the other statistics
            start = datetime.datetime.fromtimestamp(madrid_timestamp(p["start_date"]), datetime.UTC)
            bill_stats.append(StatisticData(start=start, state=tc, sum=cumulative))

        if bill_stats:
            # Versions up to 0.3.1 stored these points at midnight UTC, which would stay
            # next to the new ones and double-count. The whole series is rebuilt from the
            # CSV on every run, so clear it once when the legacy alignment is detected.
            last_bill_stat = await get_instance(hass).async_add_executor_job(
                get_last_statistics, hass, 1, BILL_STATISTIC_ID, False, set()
            )
            if last_bill_stat and last_bill_stat.get(BILL_STATISTIC_ID):
                last_start = datetime.datetime.fromtimestamp(
                    last_bill_stat[BILL_STATISTIC_ID][0]["start"], datetime.UTC
                )
                if last_start.hour == 0:  # Madrid midnight is never 00:00 UTC
                    _LOGGER.info("Clearing legacy UTC-aligned %s statistics before rewriting", BILL_STATISTIC_ID)
                    get_instance(hass).async_clear_statistics([BILL_STATISTIC_ID])
            async_add_external_statistics(hass, EnchufadoCoordinator.bill_metadata, bill_stats)

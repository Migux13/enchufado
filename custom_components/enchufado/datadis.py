"""Datadis connector for enchufado using the e-data library.

Replaces ufd.py from pvpc_energy (yinyang17) to support e-distribución
and other distributors via the Datadis platform.
"""
import datetime
import logging
import time

from edata.helpers import EdataHelper

_LOGGER = logging.getLogger(__name__)


class Datadis:
    username = None
    password = None
    cups = None
    authorized_nif = None
    storage_path = None
    _edata: EdataHelper = None

    @staticmethod
    def setup(username, password, cups, authorized_nif=None, storage_path=None):
        Datadis.username = username
        Datadis.password = password
        Datadis.cups = cups
        Datadis.authorized_nif = authorized_nif
        Datadis.storage_path = storage_path
        Datadis._edata = EdataHelper(
            username,
            password,
            cups,
            authorized_nif,
            storage_dir_path=storage_path,
        )

    @staticmethod
    async def consumptions(start_date, end_date):
        """Fetch hourly consumption from Datadis for the given date range.

        Returns {unix_timestamp: {'value': kwh, 'reading_type': 'R'|'E'}}
        Compatible with pvpc_energy's UFD.consumptions() interface.
        """
        if Datadis._edata is None:
            _LOGGER.error("Datadis not initialized — call Datadis.setup() first")
            return {}

        start_dt = datetime.datetime.combine(start_date, datetime.time.min)
        end_dt = datetime.datetime.combine(end_date, datetime.time(23, 59, 59))

        try:
            await Datadis._edata.async_update(start_dt, end_dt)
        except Exception as err:
            _LOGGER.error("Error fetching consumption from Datadis: %s", err)
            return {}

        records = Datadis._edata.data.get("consumptions", [])
        result = {}
        for record in records:
            try:
                dt = record.datetime if hasattr(record, "datetime") else record["datetime"]
                kwh = record.consumption_kwh if hasattr(record, "consumption_kwh") else record["consumption_kwh"]
                real = getattr(record, "real", record.get("real", True)) if not hasattr(record, "real") else record.real

                if not isinstance(dt, datetime.datetime):
                    continue

                ts = int(time.mktime(dt.timetuple()))
                result[ts] = {"value": float(kwh), "reading_type": "R" if real else "E"}
            except (KeyError, AttributeError, TypeError) as err:
                _LOGGER.warning("Skipping consumption record: %s", err)

        _LOGGER.info("Datadis: fetched %d consumption records (%s → %s)", len(result), start_date, end_date)
        return result

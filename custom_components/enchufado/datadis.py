"""Datadis private API client.

Implements authentication and consumption data fetching directly against
https://datadis.es/private-api without external library dependencies.
"""
import datetime
import logging
import time

import aiohttp

_LOGGER = logging.getLogger(__name__)

_URL_TOKEN = "https://datadis.es/nikola-auth/tokens/login"
_URL_SUPPLIES = "https://datadis.es/api-private/api/get-supplies-v2"
_URL_CONSUMPTION = "https://datadis.es/api-private/api/get-consumption-data-v2"

DISTRIBUTOR_CODES = {
    "1": "Viesgo",
    "2": "E-distribución",
    "3": "E-redes",
    "4": "ASEME",
    "5": "UFD",
    "6": "EOSA",
    "7": "CIDE",
    "8": "IDE",
}


class Datadis:
    username: str = None
    password: str = None
    cups: str = None
    distributor_code: str = None
    point_type: int = None
    authorized_nif: str = None

    _token: str = None

    @staticmethod
    def setup(username, password, cups, distributor_code, point_type, authorized_nif=None):
        Datadis.username = username
        Datadis.password = password
        Datadis.cups = cups
        Datadis.distributor_code = str(distributor_code)
        Datadis.point_type = int(point_type)
        Datadis.authorized_nif = authorized_nif
        Datadis._token = None

    @staticmethod
    async def async_login(username=None, password=None) -> str | None:
        """Authenticate against Datadis and return the Bearer token, or None on failure."""
        usr = username or Datadis.username
        pwd = password or Datadis.password
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(_URL_TOKEN, data={"username": usr, "password": pwd}) as resp:
                    if resp.status == 200:
                        token = await resp.text()
                        if username is None:
                            Datadis._token = token
                        return token
                    text = await resp.text()
                    _LOGGER.error("Datadis login failed (%s): %s", resp.status, text)
        except Exception as err:
            _LOGGER.error("Datadis login exception: %s", err)
        return None

    @staticmethod
    async def _get(url: str, params: dict) -> list | dict | None:
        """Authenticated GET, refreshing the token once on 401."""
        if Datadis._token is None:
            if not await Datadis.async_login():
                return None

        headers = {"Authorization": f"Bearer {Datadis._token}", "Accept-Encoding": "identity"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
                    if resp.status == 401:
                        _LOGGER.debug("Datadis 401 — refreshing token")
                        if await Datadis.async_login():
                            headers["Authorization"] = f"Bearer {Datadis._token}"
                            async with session.get(url, params=params, headers=headers) as retry:
                                if retry.status == 200:
                                    return await retry.json(content_type=None)
                    text = await resp.text()
                    _LOGGER.warning("Datadis GET %s → %s: %s", url, resp.status, text[:200])
        except Exception as err:
            _LOGGER.error("Datadis GET %s exception: %s", url, err)
        return None

    @staticmethod
    async def async_get_supplies(username=None, password=None, authorized_nif=None) -> list[dict]:
        """Fetch available supply points. Can be called with explicit credentials for config flow."""
        if username is not None:
            token = await Datadis.async_login(username, password)
            if token is None:
                return []
            headers = {"Authorization": f"Bearer {token}", "Accept-Encoding": "identity"}
            params = {}
            if authorized_nif:
                params["authorizedNif"] = authorized_nif
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(_URL_SUPPLIES, params=params, headers=headers) as resp:
                        if resp.status != 200:
                            return []
                        data = await resp.json(content_type=None)
            except Exception as err:
                _LOGGER.error("Error fetching supplies: %s", err)
                return []
        else:
            params = {}
            if authorized_nif or Datadis.authorized_nif:
                params["authorizedNif"] = authorized_nif or Datadis.authorized_nif
            data = await Datadis._get(_URL_SUPPLIES, params)
            if data is None:
                return []

        supplies = []
        for item in data.get("supplies", data if isinstance(data, list) else []):
            if all(k in item for k in ("cups", "pointType", "distributorCode")):
                supplies.append({
                    "cups": item["cups"],
                    "point_type": item["pointType"],
                    "distributor_code": item["distributorCode"],
                    "distributor_name": DISTRIBUTOR_CODES.get(str(item["distributorCode"]), item["distributorCode"]),
                    "address": item.get("address"),
                    "postal_code": item.get("postalCode"),
                    "valid_from": item.get("validDateFrom"),
                    "valid_to": item.get("validDateTo"),
                })
        return supplies

    @staticmethod
    async def consumptions(start_date: datetime.date, end_date: datetime.date) -> dict:
        """Fetch hourly consumption from Datadis for the given date range.

        Returns {unix_timestamp: {'value': kwh, 'reading_type': 'R'|'E'}}
        Compatible with pvpc_energy's UFD.consumptions() interface.
        Dates are in local (Madrid) time; timestamps are local Unix epoch.
        """
        if not all([Datadis.cups, Datadis.distributor_code, Datadis.point_type]):
            _LOGGER.error("Datadis not configured — call Datadis.setup() first")
            return {}

        params = {
            "cups": Datadis.cups,
            "distributorCode": Datadis.distributor_code,
            "startDate": start_date.strftime("%Y/%m"),
            "endDate": end_date.strftime("%Y/%m"),
            "measurementType": "0",  # 0 = hourly
            "pointType": str(Datadis.point_type),
        }
        if Datadis.authorized_nif:
            params["authorizedNif"] = Datadis.authorized_nif

        data = await Datadis._get(_URL_CONSUMPTION, params)
        if not data:
            return {}

        records = data if isinstance(data, list) else data.get("timeCurve", [])
        result = {}
        for item in records:
            try:
                raw_hour = int(item["time"].split(":")[0]) - 1  # "01:00" → hour 0
                dt = datetime.datetime.strptime(
                    f"{item['date']} {str(raw_hour).zfill(2)}:00", "%Y/%m/%d %H:%M"
                )
                # Filter to requested range
                if not (start_date <= dt.date() <= end_date):
                    continue
                ts = int(time.mktime(dt.timetuple()))
                result[ts] = {
                    "value": float(item["consumptionKWh"]),
                    "reading_type": "R" if item.get("obtainMethod") == "Real" else "E",
                }
            except (KeyError, ValueError, TypeError) as err:
                _LOGGER.debug("Skipping consumption record: %s — %s", item, err)

        _LOGGER.info(
            "Datadis: fetched %d consumption records (%s → %s)", len(result), start_date, end_date
        )
        return result

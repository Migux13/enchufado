"""Datadis private API client.

Implements authentication and data fetching directly against
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
_URL_CONTRACT = "https://datadis.es/api-private/api/get-contract-detail-v2"

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


async def _request(token: str, url: str, params: dict) -> list | dict | None:
    """Authenticated GET against the Datadis private API."""
    headers = {"Authorization": f"Bearer {token}", "Accept-Encoding": "identity"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                text = await resp.text()
                _LOGGER.warning("Datadis %s → %s: %s", url, resp.status, text[:200])
    except Exception as err:
        _LOGGER.error("Datadis request to %s failed: %s", url, err)
    return None


async def async_login(username: str, password: str) -> str | None:
    """POST credentials to Datadis and return the Bearer token, or None on failure."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(_URL_TOKEN, data={"username": username, "password": password}) as resp:
                if resp.status == 200:
                    return await resp.text()
                text = await resp.text()
                _LOGGER.error("Datadis login failed (%s): %s", resp.status, text[:200])
    except Exception as err:
        _LOGGER.error("Datadis login exception: %s", err)
    return None


async def async_get_supplies(token: str, authorized_nif: str | None = None) -> list[dict]:
    """Return a list of supply points for the authenticated user."""
    params = {}
    if authorized_nif:
        params["authorizedNif"] = authorized_nif

    data = await _request(token, _URL_SUPPLIES, params)
    if not data:
        return []

    # API may return {"supplies": [...]} or a plain list
    items = data.get("supplies", data) if isinstance(data, dict) else data
    supplies = []
    for item in items:
        if not all(k in item for k in ("cups", "pointType", "distributorCode")):
            continue
        supplies.append({
            "cups": item["cups"],
            "point_type": int(item["pointType"]),
            "distributor_code": str(item["distributorCode"]),
            "distributor_name": DISTRIBUTOR_CODES.get(str(item["distributorCode"]), str(item["distributorCode"])),
            "address": item.get("address"),
            "postal_code": item.get("postalCode"),
            "valid_from": item.get("validDateFrom"),
            "valid_to": item.get("validDateTo"),
        })
    return supplies


async def async_get_contract_detail(
    token: str,
    cups: str,
    distributor_code: str,
    authorized_nif: str | None = None,
) -> dict | None:
    """Return the most recent active contract for the given CUPS, or None."""
    params = {"cups": cups, "distributorCode": distributor_code}
    if authorized_nif:
        params["authorizedNif"] = authorized_nif

    data = await _request(token, _URL_CONTRACT, params)
    if not data:
        return None

    items = data.get("contract", data) if isinstance(data, dict) else data
    if not items:
        return None

    # Pick the most recently started contract
    contracts = sorted(
        items,
        key=lambda c: c.get("startDate", ""),
        reverse=True,
    )
    return contracts[0] if contracts else None


class Datadis:
    """Runtime state for the Datadis connection (set once per HA entry load)."""

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
    async def _ensure_token() -> bool:
        if Datadis._token:
            return True
        token = await async_login(Datadis.username, Datadis.password)
        if token:
            Datadis._token = token
            return True
        return False

    @staticmethod
    async def consumptions(start_date: datetime.date, end_date: datetime.date) -> dict:
        """Fetch hourly consumption from Datadis for the given date range.

        Returns {unix_timestamp: {'value': kwh, 'reading_type': 'R'|'E'}}
        Compatible with pvpc_energy coordinator interface.
        Timestamps are local (Madrid) Unix epoch.
        """
        if not all([Datadis.cups, Datadis.distributor_code, Datadis.point_type]):
            _LOGGER.error("Datadis not configured — call Datadis.setup() first")
            return {}

        if not await Datadis._ensure_token():
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

        data = await _request(Datadis._token, _URL_CONSUMPTION, params)

        # On 401 the token may have expired — refresh once
        if data is None:
            Datadis._token = None
            if not await Datadis._ensure_token():
                return {}
            data = await _request(Datadis._token, _URL_CONSUMPTION, params)

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
                if not (start_date <= dt.date() <= end_date):
                    continue
                ts = int(time.mktime(dt.timetuple()))
                result[ts] = {
                    "value": float(item["consumptionKWh"]),
                    "reading_type": "R" if item.get("obtainMethod") == "Real" else "E",
                }
            except (KeyError, ValueError, TypeError) as err:
                _LOGGER.debug("Skipping consumption record %s: %s", item, err)

        _LOGGER.info(
            "Datadis: fetched %d consumption records (%s → %s)", len(result), start_date, end_date
        )
        return result

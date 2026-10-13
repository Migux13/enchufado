"""Datadis private API client.

Implements authentication and data fetching directly against
https://datadis.es/private-api without external library dependencies.
"""
import datetime
import logging

import aiohttp

from .maxpower import parse_max_power
from .util import parse_consumption_days

_LOGGER = logging.getLogger(__name__)

_URL_TOKEN = "https://datadis.es/nikola-auth/tokens/login"
_URL_SUPPLIES = "https://datadis.es/api-private/api/get-supplies-v2"
_URL_CONSUMPTION = "https://datadis.es/api-private/api/get-consumption-data-v2"
_URL_MAX_POWER = "https://datadis.es/api-private/api/get-max-power-v2"
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


async def _request(token: str, url: str, params: dict) -> tuple:
    """Authenticated GET against the Datadis private API. Returns (data, status)."""
    headers = {"Authorization": f"Bearer {token}", "Accept-Encoding": "identity"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None), 200
                text = await resp.text()
                _LOGGER.warning("Datadis %s → %s: %s", url, resp.status, text[:200])
                return None, resp.status
    except Exception as err:
        _LOGGER.error("Datadis request to %s failed: %s", url, err)
    return None, None


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

    data, _ = await _request(token, _URL_SUPPLIES, params)
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


async def async_get_contracts(
    token: str,
    cups: str,
    distributor_code: str,
    authorized_nif: str | None = None,
) -> list | None:
    """Return every contract in the get-contract-detail-v2 response, or None on failure."""
    params = {"cups": cups, "distributorCode": distributor_code}
    if authorized_nif:
        params["authorizedNif"] = authorized_nif

    data, _ = await _request(token, _URL_CONTRACT, params)
    if not data:
        return None
    if isinstance(data, dict) and data.get("distributorError"):
        _LOGGER.warning("Datadis contract distributorError: %s", data["distributorError"])
    items = data.get("contract", data) if isinstance(data, dict) else data
    return items if isinstance(items, list) else None


async def async_get_contract_detail(
    token: str,
    cups: str,
    distributor_code: str,
    authorized_nif: str | None = None,
) -> dict | None:
    """Return the most recent active contract for the given CUPS, or None."""
    items = await async_get_contracts(token, cups, distributor_code, authorized_nif)
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
    async def consumptions(first_month: datetime.date, last_month: datetime.date) -> tuple:
        """Send ONE consumption query for the months first_month..last_month (hourly data).

        Returns (records, status): ``records`` maps unix_timestamp (Madrid local) to
        {'value': kwh, 'reading_type': 'R'|'E'} for every complete day in the response
        (see parse_consumption_days), ``status`` is the HTTP status, or None if no
        response was received (the query is then not considered sent; neither is a 401).

        Never retried: Datadis answers 429 to an identical query for 24 h, so a failure
        (429 included) is logged and the caller waits for the next daily run.
        """
        if not all([Datadis.cups, Datadis.distributor_code, Datadis.point_type]):
            _LOGGER.error("Datadis not configured — call Datadis.setup() first")
            return {}, None

        # Fresh login right before the query: with one query a day a cached token has
        # normally expired, and a 401 here would lose the day.
        Datadis._token = None
        if not await Datadis._ensure_token():
            return {}, None

        params = {
            "cups": Datadis.cups,
            "distributorCode": Datadis.distributor_code,
            "startDate": first_month.strftime("%Y/%m"),
            "endDate": last_month.strftime("%Y/%m"),
            "measurementType": "0",  # 0 = hourly
            "pointType": str(Datadis.point_type),
        }
        if Datadis.authorized_nif:
            params["authorizedNif"] = Datadis.authorized_nif

        data, status = await _request(Datadis._token, _URL_CONSUMPTION, params)
        if status == 401:
            Datadis._token = None  # not retried and not recorded as sent (see the coordinator)
        if status == 429:
            _LOGGER.warning("Datadis consumption query %s→%s rejected (429); waiting for tomorrow",
                            params["startDate"], params["endDate"])
        if not data:
            return {}, status

        records = data if isinstance(data, list) else data.get("timeCurve", [])
        result = parse_consumption_days(records)
        _LOGGER.info(
            "Datadis: %d hourly records in complete days (query %s → %s)",
            len(result), params["startDate"], params["endDate"],
        )
        return result, status

    @staticmethod
    async def max_power(month: datetime.date) -> tuple:
        """Maximum demanded power of one closed month (one query to get-max-power-v2).

        Returns (records, status): ``records`` as in maxpower.parse_max_power, ``status``
        the HTTP status or None when no response was received. Like the consumption
        query it is never retried: Datadis rejects an identical one for 24 h.
        """
        if not all([Datadis.cups, Datadis.distributor_code]):
            _LOGGER.error("Datadis not configured — call Datadis.setup() first")
            return [], None

        Datadis._token = None  # fresh login, the query is rare and a 401 would lose the day
        if not await Datadis._ensure_token():
            return [], None

        params = {
            "cups": Datadis.cups,
            "distributorCode": Datadis.distributor_code,
            "startDate": month.strftime("%Y/%m"),
            "endDate": month.strftime("%Y/%m"),
        }
        if Datadis.authorized_nif:
            params["authorizedNif"] = Datadis.authorized_nif

        data, status = await _request(Datadis._token, _URL_MAX_POWER, params)
        if status == 401:
            Datadis._token = None
        if not data:
            return [], status

        records, errors = parse_max_power(data)
        if errors:
            _LOGGER.warning("Datadis max power distributorError: %s", errors)
        _LOGGER.info("Datadis: %d max power records (%s)", len(records), params["startDate"])
        return records, status

    @staticmethod
    async def contracts() -> list | None:
        """Every contract of the supply (get-contract-detail-v2, no 24 h rule), or None."""
        if not all([Datadis.cups, Datadis.distributor_code]):
            return None
        Datadis._token = None  # fresh login: this runs once a day
        if not await Datadis._ensure_token():
            return None
        return await async_get_contracts(
            Datadis._token, Datadis.cups, Datadis.distributor_code, Datadis.authorized_nif
        )

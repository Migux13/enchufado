"""REE/ESIOS API client for PVPC prices.

Derived from pvpc_energy by yinyang17 (https://github.com/yinyang17/pvpc_energy).
"""
import asyncio
import datetime
import aiohttp
import logging

_LOGGER = logging.getLogger(__name__)


class ESIOSAuthError(Exception):
    """ESIOS rejected the API token (HTTP 401/403)."""


class REE:
    _url = "https://api.esios.ree.es/indicators/1001?geo_ids[]=8741&start_date={start_date}&end_date={end_date}"

    @staticmethod
    def _headers(token):
        return {
            "Accept": "application/json; application/vnd.esios-api-v2+json",
            "Content-Type": "application/json",
            "Host": "api.esios.ree.es",
            "x-api-key": token,
        }

    @staticmethod
    async def pvpc(start_date, end_date, token):
        _LOGGER.debug("REE.pvpc: %s -> %s", start_date.isoformat(), end_date.isoformat())
        url = REE._url.format(
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%dT23%%3A00%%3A00"),
        )
        response = None
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=REE._headers(token)) as resp:
                if resp.status in (401, 403):
                    raise ESIOSAuthError(f"ESIOS rejected the token (HTTP {resp.status})")
                if resp.status == 200:
                    response = await resp.json()

        if response is None:
            return None

        result = {}
        for value in response["indicator"]["values"]:
            ts = int(datetime.datetime.fromisoformat(value["datetime"]).timestamp())
            result[ts] = round(value["value"] / 1000, 5)
        _LOGGER.debug("REE.pvpc: got %d price records", len(result))
        return result


async def validate_esios_token(token):
    """Check a token with a one-day request to indicator 1001.

    Returns None if the token works, otherwise an error key for the config/repair
    flows: "invalid_esios_token" (rejected by ESIOS) or "cannot_connect_esios".
    """
    day = datetime.date.today() - datetime.timedelta(days=2)
    url = REE._url.format(
        start_date=day.strftime("%Y-%m-%d"),
        end_date=day.strftime("%Y-%m-%dT23%%3A00%%3A00"),
    )
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(url, headers=REE._headers(token)) as resp:
                if resp.status == 200:
                    return None
                if resp.status in (401, 403):
                    return "invalid_esios_token"
                _LOGGER.warning("ESIOS token validation: unexpected HTTP %s", resp.status)
                return "cannot_connect_esios"
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        _LOGGER.warning("ESIOS token validation failed: %s", err)
        return "cannot_connect_esios"

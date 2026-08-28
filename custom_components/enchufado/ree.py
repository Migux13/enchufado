"""REE/ESIOS API client for PVPC prices.

Derived from pvpc_energy by yinyang17 (https://github.com/yinyang17/pvpc_energy).
"""
import datetime
import aiohttp
import logging

_LOGGER = logging.getLogger(__name__)


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

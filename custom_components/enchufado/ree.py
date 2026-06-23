"""REE/ESIOS API client for PVPC prices.

Based on pvpc_energy by yinyang17 (https://github.com/yinyang17/pvpc_energy).
"""
import datetime
import aiohttp
import logging

_LOGGER = logging.getLogger(__name__)


class REE:
    _token = "20dada670614470c2f0cd1ff9042018bbedd5ab3796b1f96fd56d0dc209f4480"
    _url = "https://api.esios.ree.es/indicators/1001?geo_ids[]=8741&start_date={start_date}&end_date={end_date}"

    @staticmethod
    def _headers():
        return {
            "Accept": "application/json; application/vnd.esios-api-v2+json",
            "Content-Type": "application/json",
            "Host": "api.esios.ree.es",
            "x-api-key": REE._token,
        }

    @staticmethod
    async def pvpc(start_date, end_date):
        _LOGGER.debug("REE.pvpc: %s -> %s", start_date.isoformat(), end_date.isoformat())
        url = REE._url.format(
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%dT23%%3A00%%3A00"),
        )
        response = None
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=REE._headers(), ssl=False) as resp:
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

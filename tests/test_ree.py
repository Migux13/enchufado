"""REE.pvpc must raise ESIOSAuthError on 401/403 so the integration can start a reauth."""
import asyncio
import datetime
import importlib.util
import sys
import types
from pathlib import Path

import pytest


class _Resp:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


def _load_ree(monkeypatch, resp):
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientError = Exception
    aiohttp.ClientTimeout = lambda **kw: None

    class _Session:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, *a, **kw):
            return resp

    aiohttp.ClientSession = _Session
    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp)
    path = Path(__file__).parent.parent / "custom_components" / "enchufado" / "ree.py"
    spec = importlib.util.spec_from_file_location("enchufado_ree", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_raises(monkeypatch, status):
    ree = _load_ree(monkeypatch, _Resp(status))
    day = datetime.date(2026, 5, 1)
    with pytest.raises(ree.ESIOSAuthError):
        asyncio.run(ree.REE.pvpc(day, day, "token"))


def test_other_errors_still_return_none(monkeypatch):
    ree = _load_ree(monkeypatch, _Resp(500))
    day = datetime.date(2026, 5, 1)
    assert asyncio.run(ree.REE.pvpc(day, day, "token")) is None


def test_success_parses_prices(monkeypatch):
    payload = {"indicator": {"values": [{"datetime": "2026-05-01T00:00:00.000+02:00", "value": 100.0}]}}
    ree = _load_ree(monkeypatch, _Resp(200, payload))
    day = datetime.date(2026, 5, 1)
    assert list(asyncio.run(ree.REE.pvpc(day, day, "token")).values()) == [0.1]

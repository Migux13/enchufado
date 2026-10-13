"""Unit tests for EnchufadoCoordinator.get_billing_periods (Home Assistant stubbed out)."""
import asyncio
import datetime
import importlib.abc
import importlib.machinery
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

D = datetime.date


class _Stub(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in ("homeassistant", "voluptuous", "aiohttp"):
            return importlib.machinery.ModuleSpec(name, self, is_package=True)

    def create_module(self, spec):
        module = MagicMock()
        module.__path__ = []
        module.__spec__ = spec
        module.__name__ = spec.name
        return module

    def exec_module(self, module):
        pass


@pytest.fixture(scope="module")
def coordinator():
    finder = _Stub()
    sys.meta_path.insert(0, finder)
    sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
    try:
        from enchufado import coordinator as module
        from enchufado.util import madrid_timestamp
        yield module.EnchufadoCoordinator, madrid_timestamp
    finally:
        sys.meta_path.remove(finder)
        sys.path.pop(0)


class _Hass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _run(coordinator, saved, days, today):
    coord, ts = coordinator
    coord.billing_periods_file = "unused.csv"
    coord.power_high = coord.power_low = 5.5
    coord.load_billing_periods = staticmethod(lambda _f: [dict(p) for p in saved])
    consumptions = {ts(d) + 3600 * h: {"value": 1.0} for d in days for h in range(24)}

    class FakeDate(datetime.date):
        @classmethod
        def today(cls):
            return today

    import enchufado.coordinator as module
    original = module.datetime.date
    module.datetime.date = FakeDate
    try:
        return asyncio.run(coord.get_billing_periods(_Hass(), consumptions))
    finally:
        module.datetime.date = original


def _row(start, end, cost=50.0):
    return {"start_date": start, "end_date": end, "power_high": 5.5, "power_low": 5.5,
            "total_cost": cost, "total_consumption": 10.0}


def _days(first, last):
    return [first + datetime.timedelta(days=i) for i in range((last - first).days + 1)]


def test_truncated_month_with_data_to_month_end(coordinator):
    saved = [_row(D(2026, 6, 1), D(2026, 6, 28))]
    result = _run(coordinator, saved, _days(D(2026, 6, 1), D(2026, 7, 5)), D(2026, 7, 6))
    june = result[0]
    assert june["end_date"] == D(2026, 6, 30)
    assert "total_cost" not in june and "total_consumption" not in june


def test_month_with_gap_on_last_day_is_not_extended_past_data(coordinator):
    # Data stops on the 29th (30th missing); saved period already ends on the 29th.
    days = _days(D(2026, 6, 1), D(2026, 6, 29)) + _days(D(2026, 7, 1), D(2026, 7, 5))
    saved = [_row(D(2026, 6, 1), D(2026, 6, 29))]
    result = _run(coordinator, saved, days, D(2026, 7, 6))
    assert result[0]["end_date"] == D(2026, 6, 29)
    assert result[0]["total_cost"] == 50.0  # untouched: no recalculation needed

    # Saved ended on the 28th: it is extended to the 29th (last day with data), not the 30th.
    saved = [_row(D(2026, 6, 1), D(2026, 6, 28))]
    result = _run(coordinator, saved, days, D(2026, 7, 6))
    assert result[0]["end_date"] == D(2026, 6, 29)
    assert "total_cost" not in result[0]


def test_current_month_is_refreshed(coordinator):
    saved = [_row(D(2026, 10, 1), D(2026, 10, 8))]
    result = _run(coordinator, saved, _days(D(2026, 10, 1), D(2026, 10, 11)), D(2026, 10, 13))
    assert result[0]["end_date"] == D(2026, 10, 11)
    assert "total_cost" not in result[0]

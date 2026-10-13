"""Tests for maxpower.py and the per-endpoint quota. All fixtures are invented."""
import datetime
import importlib.util
from pathlib import Path

import pytest

_BASE = Path(__file__).parent.parent / "custom_components" / "enchufado"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"enchufado_{name}", _BASE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


maxpower = _load("maxpower")
quota = _load("quota")
D = datetime.date
TZ = datetime.timezone(datetime.timedelta(hours=2))


def at(day, hour=6, minute=30):
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def response(*rows, errors=()):
    return {
        "maxPower": [
            {"cups": "ES0000000000000000XX0X", "date": d, "time": t, "maxPower": kw, "period": p}
            for d, t, kw, p in rows
        ],
        "distributorError": list(errors),
    }


MARCH = response(
    ("2026/03/12", "18:45", 3.141, "1"),
    ("2026/03/03", "13:15", 1.618, "2"),
    ("2026/03/21", "04:00", 2.718, "3"),
)
APRIL = response(
    ("2026/04/30", "21:30", 4.2, "1"),
    ("2026/04/04", "09:00", 1.1, "2"),
    ("2026/04/05", "02:45", 0.9, "3"),
)


def test_parse_returns_every_period_with_its_date_and_quarter_hour():
    records, errors = maxpower.parse_max_power(MARCH)
    assert errors == []
    assert [(r["period"], r["date"], r["time"], r["kw"]) for r in records] == [
        (1, D(2026, 3, 12), "18:45", 3.141),
        (2, D(2026, 3, 3), "13:15", 1.618),
        (3, D(2026, 3, 21), "04:00", 2.718),
    ]


def test_parse_reports_distributor_errors_and_skips_bad_records():
    data = response(("2026/03/12", "18:45", 3.0, "1"), errors=[{"errorCode": "99", "errorDescription": "example"}])
    data["maxPower"] += [{"date": "bad"}, {"date": "2026/03/01", "time": "00:00", "maxPower": 1, "period": "9"}]
    records, errors = maxpower.parse_max_power(data)
    assert len(records) == 1
    assert errors == [{"errorCode": "99", "errorDescription": "example"}]
    assert maxpower.parse_max_power(None) == ([], [])


def test_merge_keeps_the_highest_per_period_and_marks_the_month_done():
    records, _ = maxpower.parse_max_power(response(
        ("2026/03/12", "18:45", 3.0, "1"), ("2026/03/13", "19:00", 3.5, "1"), ("2026/03/14", "19:00", 3.2, "1"),
    ))
    history = maxpower.merge({"checked": [], "months": {}}, D(2026, 3, 1), records)
    assert history["months"]["2026-03"]["1"] == {"kw": 3.5, "date": "2026-03-13", "time": "19:00"}
    assert maxpower.done_months(history) == {D(2026, 3, 1)}


def _history():
    history = {"checked": [], "months": {}}
    for data, month in ((MARCH, D(2026, 3, 1)), (APRIL, D(2026, 4, 1))):
        history = maxpower.merge(history, month, maxpower.parse_max_power(data)[0])
    return history


def test_sensor_values_use_the_last_closed_month_and_the_matching_contracted_power():
    values = maxpower.sensor_values(_history(), D(2026, 5, 10), lambda day: (6.9, 3.3))
    assert values[1]["kw"] == 4.2 and values[1]["month"] == "2026-04"
    assert (values[1]["date"], values[1]["time"]) == ("2026-04-30", "21:30")
    assert values[1]["contracted_kw"] == 6.9  # period 1 -> power_high
    assert values[2]["contracted_kw"] == 6.9  # period 2 -> power_high
    assert values[3]["contracted_kw"] == 3.3  # period 3 -> power_low
    assert values[1]["last_12_months"] == {"2026-03": 3.141, "2026-04": 4.2}


def test_sensor_values_ignore_months_that_are_not_closed_yet():
    values = maxpower.sensor_values(_history(), D(2026, 4, 20), lambda day: (6.9, 3.3))
    assert values[1]["month"] == "2026-03"


def test_sensor_values_without_data_and_last_12_months_window():
    assert maxpower.sensor_values({"checked": [], "months": {}}, D(2026, 5, 10), lambda d: (1, 1)) == {
        1: None, 2: None, 3: None,
    }
    history = {"checked": [], "months": {}}
    for i in range(14):
        month = D(2025 + (i + 1) // 12, (i + 1) % 12 + 1, 1)
        history = maxpower.merge(history, month, [{"period": 1, "date": month, "time": "10:00", "kw": float(i)}])
    values = maxpower.sensor_values(history, D(2027, 1, 15), lambda d: (1, 1))
    assert len(values[1]["last_12_months"]) == 12


def test_history_round_trip_and_corrupt_file():
    history = _history()
    assert maxpower.load(maxpower.dump(history)) == history
    assert maxpower.load("garbage") == {"checked": [], "months": {}}
    assert maxpower.load(None) == {"checked": [], "months": {}}


@pytest.mark.parametrize("day,expected", [
    (D(2026, 5, 1), None), (D(2026, 5, 2), None),
    (D(2026, 5, 3), (D(2026, 4, 1), D(2026, 4, 1))),
    (D(2026, 1, 15), (D(2025, 12, 1), D(2025, 12, 1))),
])
def test_max_power_is_asked_from_day_3_for_the_closed_previous_month(day, expected):
    assert quota.plan_max_power(day, [], at(day)) == expected


def test_max_power_is_not_asked_again_once_the_month_is_answered():
    assert quota.plan_max_power(D(2026, 5, 20), [], at(D(2026, 5, 20)), {D(2026, 4, 1)}) is None


def test_max_power_respects_the_24h10_lock_and_retries_the_next_day():
    q = (D(2026, 4, 1), D(2026, 4, 1))
    log = quota.record([], q, at(D(2026, 5, 3), 6, 31), quota.MAX_POWER)
    assert quota.plan_max_power(D(2026, 5, 3), log, at(D(2026, 5, 3), 20)) is None
    assert quota.plan_max_power(D(2026, 5, 4), log, at(D(2026, 5, 4), 6, 30)) is None  # 23 h 59 min
    assert quota.plan_max_power(D(2026, 5, 4), log, at(D(2026, 5, 4), 6, 42)) == q


def test_endpoints_have_independent_quotas():
    today = D(2026, 5, 3)
    consumption_q = (D(2026, 5, 1), D(2026, 5, 1))
    log = quota.record([], consumption_q, at(today), quota.CONSUMPTION)
    # a consumption query today does not block max power...
    assert quota.plan_max_power(today, log, at(today, 7)) == (D(2026, 4, 1), D(2026, 4, 1))
    # ...and a max power query does not count as the day's consumption query
    log = quota.record([], (D(2026, 4, 1), D(2026, 4, 1)), at(today), quota.MAX_POWER)
    assert quota.plan_query(today, log, at(today, 7), last_stored_day=today) is not None


def test_log_round_trip_keeps_the_endpoint_and_old_entries_are_consumption():
    log = quota.record([], (D(2026, 4, 1), D(2026, 4, 1)), at(D(2026, 5, 3)), quota.MAX_POWER)
    assert quota.load(quota.dump(log)) == log
    legacy = '[{"start": "2026-05-01", "end": "2026-05-01", "sent_at": "2026-05-03T06:30:00+02:00"}]'
    assert quota.load(legacy)[0]["endpoint"] == quota.CONSUMPTION

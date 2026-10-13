"""Tests for the daily Datadis query planning (quota.py) and day parsing (util.py)."""
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


quota = _load("quota")
util = _load("util")
D = datetime.date
TZ = util.MADRID_TZ


def at(day, hour=6, minute=30):
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def run_days(first, n, **plan_kwargs):
    """Simulate one import per day at 06:30; returns the queries sent."""
    log, sent = [], []
    for i in range(n):
        today = first + datetime.timedelta(days=i)
        now = at(today)
        q = quota.plan_query(today, log, now, last_stored_day=today - datetime.timedelta(days=1), **plan_kwargs)
        if q:
            sent.append(q)
            log = quota.record(log, q, now)
    return sent


def test_alternates_and_never_repeats_the_previous_days_query():
    sent = run_days(D(2026, 10, 1), 40)
    assert len(sent) == 40  # exactly one per day
    assert all(a != b for a, b in zip(sent, sent[1:]))
    assert sent[0] == (D(2026, 9, 1), D(2026, 10, 1))
    assert sent[1] == (D(2026, 10, 1), D(2026, 10, 1))


def test_month_rollover_does_not_repeat():
    sent = run_days(D(2026, 10, 28), 10)
    assert all(a != b for a, b in zip(sent, sent[1:]))


def test_only_one_query_per_day_even_after_restart_or_reload():
    today = D(2026, 10, 13)
    log = quota.record([], (D(2026, 10, 1), D(2026, 10, 1)), at(today, 6, 31))
    for hour in (7, 12, 23):
        assert quota.plan_query(today, log, at(today, hour), last_stored_day=today) is None


def test_next_day_is_allowed_before_24h10_if_range_differs():
    log = quota.record([], (D(2026, 10, 1), D(2026, 10, 1)), at(D(2026, 10, 13), 6, 31))
    q = quota.plan_query(D(2026, 10, 14), log, at(D(2026, 10, 14), 6, 30), last_stored_day=D(2026, 10, 13))
    assert q == (D(2026, 9, 1), D(2026, 10, 1))


def test_identical_query_is_blocked_within_24h10():
    q = (D(2026, 10, 1), D(2026, 10, 1))
    log = [{"start": q[0], "end": q[1], "sent_at": at(D(2026, 10, 13), 6, 31)}]
    assert quota.is_blocked(q, log, at(D(2026, 10, 14), 6, 40) - datetime.timedelta(minutes=1)) is True
    assert quota.is_blocked(q, log, at(D(2026, 10, 14), 6, 41)) is False


def test_first_import_sends_one_wide_query():
    today = D(2026, 10, 13)
    q = quota.plan_query(today, [], at(today), last_stored_day=None, window_start=D(2024, 11, 1))
    assert q == (D(2024, 11, 1), D(2026, 10, 1))


def test_gap_older_than_previous_month_sends_a_catch_up_query():
    today = D(2026, 10, 13)
    q = quota.plan_query(today, [], at(today), last_stored_day=D(2026, 7, 20), window_start=D(2024, 11, 1))
    assert q == (D(2026, 7, 1), D(2026, 10, 1))


def test_force_may_resend_but_never_an_identical_query():
    today = D(2026, 10, 13)
    wide = (D(2024, 11, 1), D(2026, 10, 1))
    log = quota.record([], (D(2026, 10, 1), D(2026, 10, 1)), at(today, 6, 31))
    assert quota.plan_query(today, log, at(today, 9), last_stored_day=today, window_start=D(2024, 11, 1), force=True) == wide
    log = quota.record(log, wide, at(today, 9))
    assert quota.plan_query(today, log, at(today, 10), last_stored_day=today, window_start=D(2024, 11, 1), force=True) is None


def test_log_round_trip_and_corrupt_file():
    log = quota.record([], (D(2026, 9, 1), D(2026, 10, 1)), at(D(2026, 10, 13)))
    assert quota.load(quota.dump(log)) == log
    assert quota.load("not json") == []
    assert quota.load(None) == []


def test_record_drops_old_entries():
    old = quota.record([], (D(2026, 9, 1), D(2026, 9, 1)), at(D(2026, 10, 1)))
    log = quota.record(old, (D(2026, 10, 1), D(2026, 10, 1)), at(D(2026, 10, 13)))
    assert len(log) == 1


def _day(day, hours, kwh=0.1, method="Real"):
    return [
        {"date": day.strftime("%Y/%m/%d"), "time": f"{h:02d}:00", "consumptionKWh": kwh, "obtainMethod": method}
        for h in range(1, hours + 1)
    ]


def test_parse_keeps_complete_days_outside_any_requested_range():
    rows = _day(D(2026, 10, 12), 24) + _day(D(2026, 10, 13), 24, method="Estimated")
    out = util.parse_consumption_days(rows)
    assert len(out) == 48
    midnight = util.madrid_timestamp(D(2026, 10, 13))
    assert out[midnight] == {"value": 0.1, "reading_type": "E"}
    assert out[midnight - 3600]["reading_type"] == "R"


@pytest.mark.parametrize("day,hours", [(D(2026, 3, 29), 23), (D(2026, 10, 25), 25)])
def test_parse_accepts_dst_days(day, hours):
    out = util.parse_consumption_days(_day(day, hours))
    assert len(out) == hours
    assert max(out) - min(out) == 3600 * (hours - 1)


def test_parse_drops_incomplete_days_and_bad_records():
    rows = _day(D(2026, 10, 12), 24) + _day(D(2026, 10, 13), 20) + [{"date": "x"}, {}]
    out = util.parse_consumption_days(rows)
    assert len(out) == 24

"""Tests for contracts.py and the per-day contracted power in tariffs.py. All fixtures are invented."""
import datetime
import importlib.util
from pathlib import Path

_BASE = Path(__file__).parent.parent / "custom_components" / "enchufado"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"enchufado_{name}", _BASE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contracts = _load("contracts")
tariffs = _load("tariffs")
D = datetime.date
TZ = tariffs.MADRID_TZ


def contract(start, end, powers, owner_start="2020-01-01"):
    """Same keys and formats as a get-contract-detail-v2 contract, with invented values."""
    return {
        "cups": "ES0000000000000000XX0X",
        "distributor": "EXAMPLE DISTRIBUTOR",
        "marketer": "EXAMPLE MARKETER",
        "contractedPowerkW": powers,
        "startDate": start,
        "endDate": end,
        "dateOwner": [{"startDate": owner_start, "endDate": ""}],
    }


# Power raised from 4.4 to 6.9 kW in the middle of June 2026.
TWO_CONTRACTS = [
    contract("2024/02/10", "2026/06/15", [4.4, 4.4]),
    contract("2026/06/16", "", [6.9, 3.3]),
]
ONE_CONTRACT = [contract("2024/02/10", "", [4.4, 4.4])]


def test_parse_reads_dates_with_slashes_and_ignores_dateowner():
    parsed = contracts.parse_contracts(TWO_CONTRACTS)
    assert parsed == [
        {"start": D(2024, 2, 10), "end": D(2026, 6, 15), "high": 4.4, "low": 4.4},
        {"start": D(2026, 6, 16), "end": None, "high": 6.9, "low": 3.3},
    ]


def test_parse_accepts_a_scalar_power_and_skips_unusable_contracts():
    items = [contract("2024/02/10", "", 5.0), contract("bad", "", [1.0]), contract("2024/03/01", "", []), "x"]
    parsed = contracts.parse_contracts(items)
    assert [(c["start"], c["high"], c["low"]) for c in parsed] == [(D(2024, 2, 10), 5.0, 5.0)]


def test_single_contract_in_force():
    parsed = contracts.parse_contracts(ONE_CONTRACT)
    today = D(2026, 7, 1)
    assert contracts.in_force(parsed, today)["high"] == 4.4
    rows = contracts.update_schedule([], parsed, today)
    assert rows == [(D(2024, 2, 10), 4.4, 4.4)]
    assert tariffs.powers_on(D(2026, 1, 1), 4.4, 4.4, rows) == (4.4, 4.4)


def test_schedule_from_two_contracts_has_one_row_per_start():
    parsed = contracts.parse_contracts(TWO_CONTRACTS)
    rows = contracts.update_schedule([], parsed, D(2026, 7, 1))
    assert rows == [(D(2024, 2, 10), 4.4, 4.4), (D(2026, 6, 16), 6.9, 3.3)]
    assert tariffs.powers_on(D(2026, 6, 15), 0, 0, rows) == (4.4, 4.4)
    assert tariffs.powers_on(D(2026, 6, 16), 0, 0, rows) == (6.9, 3.3)
    assert tariffs.powers_on(D(2020, 1, 1), 0, 0, rows) == (4.4, 4.4)  # before the first row


def test_power_change_without_a_new_start_date_starts_today_and_keeps_the_past():
    stored = [(D(2024, 2, 10), 4.4, 4.4)]
    parsed = contracts.parse_contracts([contract("2024/02/10", "", [6.9, 3.3])])
    rows = contracts.update_schedule(stored, parsed, D(2026, 7, 1))
    assert rows == [(D(2024, 2, 10), 4.4, 4.4), (D(2026, 7, 1), 6.9, 3.3)]
    assert contracts.update_schedule(rows, parsed, D(2026, 7, 2)) == rows  # stable afterwards


def test_existing_rows_are_never_edited():
    stored = [(D(2026, 6, 16), 6.9, 3.3)]
    parsed = contracts.parse_contracts([contract("2026/06/16", "", [6.9, 3.3])])
    assert contracts.update_schedule(stored, parsed, D(2026, 9, 1)) == stored


def test_dump_and_load_round_trip_and_corrupt_file():
    rows = [(D(2024, 2, 10), 4.4, 4.4), (D(2026, 6, 16), 6.9, 3.3)]
    assert contracts.load(contracts.dump(D(2026, 7, 1), rows)) == {"checked": D(2026, 7, 1), "rows": rows}
    assert contracts.load("garbage") == {"checked": None, "rows": []}
    assert contracts.load(None) == {"checked": None, "rows": []}
    assert "ES00" not in contracts.dump(D(2026, 7, 1), rows)


def _series(first, last, power_high, power_low, schedule=None):
    ts0 = int(datetime.datetime.combine(first, datetime.time(), TZ).timestamp())
    ts1 = int(datetime.datetime.combine(last + datetime.timedelta(days=1), datetime.time(), TZ).timestamp()) - 3600
    consumptions = {ts: {"value": 0.2} for ts in range(ts0, ts1 + 1, 3600)}
    prices = {ts: 0.15 for ts in consumptions}
    rows = tariffs.create_total_cost_series(
        ts0, ts1, consumptions, prices, power_high, power_low, power_schedule=schedule
    )
    return {ts: cost for ts, cost, _ in rows}


def test_hours_before_a_power_change_are_untouched_and_later_ones_use_the_new_power():
    first, last = D(2026, 6, 10), D(2026, 6, 22)
    schedule = contracts.update_schedule([], contracts.parse_contracts(TWO_CONTRACTS), D(2026, 7, 1))
    # the entry already holds the NEW power, as after update_contract()
    with_schedule = _series(first, last, 6.9, 3.3, schedule)
    old_only = _series(first, last, 4.4, 4.4)
    new_only = _series(first, last, 6.9, 3.3)
    change = int(datetime.datetime(2026, 6, 16, tzinfo=TZ).timestamp())
    before = [ts for ts in with_schedule if ts < change]
    after = [ts for ts in with_schedule if ts >= change]
    assert before and after
    assert all(with_schedule[ts] == old_only[ts] for ts in before)
    assert all(with_schedule[ts] == new_only[ts] for ts in after)
    assert with_schedule[after[0]] != old_only[after[0]]


def test_without_a_schedule_the_current_power_applies_to_every_day():
    first, last = D(2026, 6, 10), D(2026, 6, 12)
    assert _series(first, last, 6.9, 3.3) == _series(first, last, 6.9, 3.3, schedule=[])


def test_signature_includes_the_power_schedule():
    rows = [(D(2024, 2, 10), 4.4, 4.4), (D(2026, 6, 16), 6.9, 3.3)]
    base = tariffs.signature(6.9, 3.3)
    assert tariffs.signature(6.9, 3.3, 1, rows) != base
    assert tariffs.signature(6.9, 3.3, 1, rows) == tariffs.signature(6.9, 3.3, 1, list(rows))
    assert tariffs.signature(6.9, 3.3, 1, rows) != tariffs.signature(6.9, 3.3, 1, rows[:1])
    assert tariffs.signature(6.9, 3.3, 1, None) == base  # no schedule: unchanged hash

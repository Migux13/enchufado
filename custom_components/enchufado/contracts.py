"""Contracted power by date, from Datadis get-contract-detail-v2.

No Home Assistant imports, so it can be unit-tested on its own.

Only the start/end dates and the contracted power of each contract are read; nothing else
in the response (supply point, holder, address...) is kept or logged.

The schedule is a list of ``(valid_from, power_high, power_low)`` rows sorted by date, the
same shape ``tariffs.powers_on`` takes. It is persisted so that, once a power change is
seen, hours already computed keep the power of their day.
"""
import datetime
import json


def _date(value):
    """Date of a "YYYY/MM/DD" string (the dateOwner entries use "-", which is not read)."""
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y/%m/%d").date()
    except (ValueError, TypeError):
        return None


def parse_contracts(items):
    """[{"start": date, "end": date | None, "high": kW, "low": kW}] sorted by start.

    ``end`` is None for a contract in force (empty endDate). The first contracted power
    is power_high and the last one power_low, as in the config flow. Contracts without a
    start date or a usable power are skipped.
    """
    parsed = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        start = _date(item.get("startDate"))
        powers = item.get("contractedPowerkW")
        try:
            if isinstance(powers, list) and powers:
                high, low = float(powers[0]), float(powers[-1])
            elif isinstance(powers, (int, float)):
                high = low = float(powers)
            else:
                continue
        except (ValueError, TypeError):
            continue
        if start is None:
            continue
        parsed.append({"start": start, "end": _date(item.get("endDate")), "high": high, "low": low})
    return sorted(parsed, key=lambda c: c["start"])


def in_force(parsed, today):
    """The contract in force today: the latest started one without end date, else the
    latest one that already started; None if there is none."""
    started = [c for c in parsed if c["start"] <= today]
    open_ended = [c for c in started if c["end"] is None or c["end"] >= today]
    pool = open_ended or started
    return pool[-1] if pool else None


def _powers(rows, day):
    if not rows:
        return None
    chosen = rows[0]
    for row in rows:
        if row[0] > day:
            break
        chosen = row
    return chosen[1], chosen[2]


def update_schedule(rows, parsed, today):
    """Schedule rows plus what the contracts say.

    - Every contract start date missing from the schedule is added with its powers
      (the first time this gives the history of the contracts in the response).
    - If the contract in force has a power different from the one the schedule gives for
      today (the distributor changed it without a new start date), a row starting today is
      added, so earlier days keep the old power.
    Existing rows are never edited.
    """
    table = {row[0]: (row[1], row[2]) for row in rows}
    for c in parsed:
        table.setdefault(c["start"], (c["high"], c["low"]))
    merged = [(d, high, low) for d, (high, low) in sorted(table.items())]
    current = in_force(parsed, today)
    if current is not None and _powers(merged, today) != (current["high"], current["low"]):
        table[today] = (current["high"], current["low"])
        merged = [(d, high, low) for d, (high, low) in sorted(table.items())]
    return merged


def dump(checked, rows):
    return json.dumps(
        {
            "checked": checked.isoformat(),
            "rows": [{"from": d.isoformat(), "high": high, "low": low} for d, high, low in rows],
        }
    )


def load(text):
    """{"checked": date | None, "rows": [...]}; missing or corrupt content gives an empty one."""
    empty = {"checked": None, "rows": []}
    if not text:
        return empty
    try:
        data = json.loads(text)
        rows = sorted(
            (datetime.date.fromisoformat(r["from"]), float(r["high"]), float(r["low"]))
            for r in data["rows"]
        )
        checked = datetime.date.fromisoformat(data["checked"]) if data.get("checked") else None
        return {"checked": checked, "rows": rows}
    except (ValueError, KeyError, TypeError):
        return empty

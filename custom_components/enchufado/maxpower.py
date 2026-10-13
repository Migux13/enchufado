"""Maximum demanded power (Datadis get-max-power-v2): parsing, history and sensor values.

No Home Assistant imports, so it can be unit-tested on its own.

Datadis returns one record per energy period (1 punta, 2 llano, 3 valle) and month, with
the date and the quarter-hour at which the maximum was measured. Against the contracted
power of a 2.0TD supply, periods 1 and 2 correspond to ``power_high`` and 3 to ``power_low``.

History layout (JSON, kept in the integration's data directory):
    {"checked": ["YYYY-MM", ...],
     "months": {"YYYY-MM": {"1": {"kw": float, "date": "YYYY-MM-DD", "time": "HH:MM"}, ...}}}
"""
import datetime
import json

PERIODS = (1, 2, 3)


def parse_max_power(data):
    """(records, errors) of a get-max-power-v2 response.

    ``records`` is a list of {"period": int, "date": date, "time": "HH:MM", "kw": float};
    malformed records are skipped. ``errors`` is the response's distributorError list.
    """
    if not isinstance(data, dict):
        return [], []
    errors = data.get("distributorError") or []
    records = []
    for item in data.get("maxPower") or []:
        try:
            period = int(item["period"])
            if period not in PERIODS:
                continue
            records.append(
                {
                    "period": period,
                    "date": datetime.datetime.strptime(item["date"], "%Y/%m/%d").date(),
                    "time": str(item["time"]),
                    "kw": float(item["maxPower"]),
                }
            )
        except (KeyError, ValueError, TypeError):
            continue
    return records, errors


def month_key(day):
    return f"{day.year:04d}-{day.month:02d}"


def _months_between(first, last):
    day = first.replace(day=1)
    while day <= last:
        yield day
        day = (day.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)


def merge(history, queried_month, records, last_queried_month=None):
    """History with ``records`` added (highest value per period and month) and every month
    from ``queried_month`` to ``last_queried_month`` (default: just the first) marked as
    answered."""
    months = {k: dict(v) for k, v in (history.get("months") or {}).items()}
    for r in records:
        slot = months.setdefault(month_key(r["date"]), {})
        current = slot.get(str(r["period"]))
        if current is None or r["kw"] > current["kw"]:
            slot[str(r["period"])] = {"kw": r["kw"], "date": r["date"].isoformat(), "time": r["time"]}
    answered = {month_key(m) for m in _months_between(queried_month, last_queried_month or queried_month)}
    checked = sorted(set(history.get("checked") or []) | answered)
    return {"checked": checked, "months": months}


def done_months(history):
    """First day of every month already answered by Datadis."""
    out = set()
    for key in history.get("checked") or []:
        try:
            out.add(datetime.date.fromisoformat(f"{key}-01"))
        except ValueError:
            continue
    return out


def sensor_values(history, today, contracted):
    """{period: values or None} for the sensors, from the last closed month with data.

    ``contracted(date)`` returns (power_high, power_low) in force on a date. Values hold
    "kw", "date", "time", "month", "last_12_months" ({"YYYY-MM": kw}, oldest first) and
    "contracted_kw" (the power the maximum is compared with).
    """
    closed = month_key((today.replace(day=1) - datetime.timedelta(days=1)))
    months = history.get("months") or {}
    keys = sorted(k for k in months if k <= closed)
    result = {}
    for period in PERIODS:
        with_data = [k for k in keys if str(period) in months[k]]
        if not with_data:
            result[period] = None
            continue
        last = with_data[-1]
        entry = months[last][str(period)]
        high, low = contracted(datetime.date.fromisoformat(entry["date"]))
        result[period] = {
            "kw": entry["kw"],
            "date": entry["date"],
            "time": entry["time"],
            "month": last,
            "last_12_months": {k: months[k][str(period)]["kw"] for k in with_data[-12:]},
            "contracted_kw": low if period == 3 else high,
        }
    return result


def dump(history):
    return json.dumps(history, sort_keys=True)


def load(text):
    """Parse a stored history; missing or corrupt content gives an empty one."""
    empty = {"checked": [], "months": {}}
    if not text:
        return empty
    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("months"), dict):
            return {"checked": list(data.get("checked") or []), "months": data["months"]}
    except (ValueError, TypeError):
        pass
    return empty

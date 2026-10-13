"""Daily quota of Datadis consumption queries.

get-consumption-data-v2 only takes months (YYYY/MM) and answers 429 to an identical query
for 24 h. This module decides which single query to send each day and keeps a log of sent
queries so that none repeats within the lock window, not even across restarts or reloads.

No Home Assistant imports, so it can be unit-tested on its own.
"""
import datetime
import json

QUERY_LOCK = datetime.timedelta(hours=24, minutes=10)
_KEEP = datetime.timedelta(days=3)


def month_start(day):
    return day.replace(day=1)


def previous_month(first):
    return (first - datetime.timedelta(days=1)).replace(day=1)


def is_blocked(query, log, now):
    """True if an identical query (start, end) was sent within the lock window."""
    return any(
        (e["start"], e["end"]) == query and now - e["sent_at"] < QUERY_LOCK for e in log
    )


def plan_query(today, log, now, last_stored_day=None, window_start=None, force=False):
    """Month range (first_month, last_month) to query now, or None.

    - First import (nothing stored) or a gap older than the previous month: one wide
      query from ``window_start`` / the month of the last stored day.
    - Otherwise one query per day, alternating "current month" and "previous + current"
      so it is never identical to the previous day's.
    - A query already sent today (Madrid date of ``now``) means no more today, except for
      ``force`` (manual service call), which still never repeats an identical query.
    Returns None when the chosen query is blocked; it is never swapped for another one and
    never retried in a loop. ``now`` must be a Madrid-timezone aware datetime.
    """
    current = month_start(today)
    previous = previous_month(current)
    sent_today = any(e["sent_at"].date() == now.date() for e in log)
    if sent_today and not force:
        return None

    if force and window_start is not None:
        wide = (month_start(window_start), current)
    elif last_stored_day is None and window_start is not None:
        wide = (month_start(window_start), current)
    elif last_stored_day is not None and month_start(last_stored_day) < previous:
        wide = (month_start(last_stored_day), current)
    else:
        wide = None

    if wide:
        query = wide
    else:
        last = max(log, key=lambda e: e["sent_at"], default=None)
        single, pair = (current, current), (previous, current)
        # alternate against the last query sent; anything but "current only" -> "current only"
        query = pair if last is not None and (last["start"], last["end"]) == single else single
        if last is None:
            query = pair
    return None if is_blocked(query, log, now) else query


def dump(log):
    return json.dumps(
        [
            {"start": e["start"].isoformat(), "end": e["end"].isoformat(), "sent_at": e["sent_at"].isoformat()}
            for e in log
        ]
    )


def load(text):
    """Parse a stored log; corrupt or missing content gives an empty log."""
    if not text:
        return []
    try:
        return [
            {
                "start": datetime.date.fromisoformat(e["start"]),
                "end": datetime.date.fromisoformat(e["end"]),
                "sent_at": datetime.datetime.fromisoformat(e["sent_at"]),
            }
            for e in json.loads(text)
        ]
    except (ValueError, KeyError, TypeError):
        return []


def record(log, query, now):
    """Log with ``query`` added and old entries dropped."""
    kept = [e for e in log if now - e["sent_at"] < _KEEP]
    kept.append({"start": query[0], "end": query[1], "sent_at": now})
    return kept

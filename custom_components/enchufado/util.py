"""Shared helpers for Enchufado integration."""
import datetime
import logging
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

MADRID_TZ = ZoneInfo("Europe/Madrid")


def madrid_timestamp(value: datetime.date) -> int:
    """Convert a naive date/datetime representing Europe/Madrid local time to a Unix timestamp.

    time.mktime() interprets a naive timetuple in the host machine's system timezone,
    not Europe/Madrid specifically. On a host configured with a different timezone
    (common on cloud VMs, NAS or Docker installs that default to UTC), that silently
    shifts every consumption/price record into the wrong hour.
    """
    if not isinstance(value, datetime.datetime):
        value = datetime.datetime.combine(value, datetime.time())
    return int(value.replace(tzinfo=MADRID_TZ).timestamp())


def hours_in_day(day: datetime.date) -> int:
    """23, 24 or 25 depending on the DST change."""
    start = madrid_timestamp(day)
    end = madrid_timestamp(day + datetime.timedelta(days=1))
    return round((end - start) / 3600)


def parse_consumption_days(records) -> dict:
    """Hourly consumption of every complete day in a Datadis response.

    A day is complete when it has as many records as hours in that day (23, 24 or 25 on a
    DST change). The records are assigned to the day's hours in order (stable sort by the
    "HH:00" label, which is the response order if it is chronological), so it does not
    matter how Datadis numbers the repeated or missing hour of a DST day. Incomplete days
    and malformed records are dropped. The requested range is not applied: whatever
    complete days Datadis returns are kept.
    Returns {unix_timestamp: {"value": kwh, "reading_type": "R"|"E"}}.
    """
    days = {}
    for item in records:
        try:
            day = datetime.datetime.strptime(item["date"], "%Y/%m/%d").date()
            hour = int(item["time"].split(":")[0])
            value = float(item["consumptionKWh"])
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        reading_type = "R" if item.get("obtainMethod") == "Real" else "E"
        days.setdefault(day, []).append((hour, value, reading_type))

    result = {}
    for day, rows in days.items():
        expected = hours_in_day(day)
        if len(rows) != expected:
            if expected != 24:
                _LOGGER.warning(
                    "Datadis: DST day %s has %d records, expected %d; day skipped", day, len(rows), expected
                )
            continue
        midnight = madrid_timestamp(day)
        for index, (_, value, reading_type) in enumerate(sorted(rows, key=lambda r: r[0])):
            result[midnight + 3600 * index] = {"value": value, "reading_type": reading_type}
    return result

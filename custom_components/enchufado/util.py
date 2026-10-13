"""Shared helpers for Enchufado integration."""
import datetime
from zoneinfo import ZoneInfo

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

    A day is complete when it has one record per hour (23, 24 or 25 on a DST change),
    numbered "01:00".."N:00". Incomplete days and malformed records are dropped. The
    requested range is not applied: whatever complete days Datadis returns are kept.
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
        days.setdefault(day, {})[hour] = (value, reading_type)

    result = {}
    for day, hours in days.items():
        if sorted(hours) != list(range(1, hours_in_day(day) + 1)):
            continue
        midnight = madrid_timestamp(day)
        for hour, (value, reading_type) in hours.items():
            result[midnight + 3600 * (hour - 1)] = {"value": value, "reading_type": reading_type}
    return result

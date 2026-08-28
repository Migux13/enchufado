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

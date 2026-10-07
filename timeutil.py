"""Central Africa Time (CAT, UTC+2, no daylight saving) for the whole app.

The server (Render) runs on UTC. The app keeps every stored timestamp in UTC
(models use datetime.utcnow), which is the safe thing to store, and converts
to CAT only when a time is shown to a person. Zimbabwe has no daylight saving,
so a fixed +2 hour offset is exact all year round and needs no time-zone
database on the server.

Also, activate_cat() points the Python process itself at CAT, so every
date.today() / datetime.now() in the code (invoice dates, overdue checks,
"today" on dashboards, ...) agrees with the calendar in Harare rather than
with UTC - between 22:00 and 24:00 UTC it is already tomorrow in Zimbabwe.
"""
import os
import time
from datetime import datetime, date, timedelta, timezone

CAT_OFFSET = timedelta(hours=2)
CAT = timezone(CAT_OFFSET, "CAT")


def activate_cat():
    """Make date.today() / datetime.now() read Harare time in this process.
    "CAT-2" is a POSIX zone string (UTC+2, abbreviation CAT) that needs no
    tz database. No-op where time.tzset doesn't exist (Windows desktop runs
    already use the PC's own clock, which is local time)."""
    os.environ["TZ"] = "CAT-2"
    if hasattr(time, "tzset"):
        time.tzset()


def to_cat(value):
    """A stored UTC datetime as a naive datetime in CAT. Dates, strings and
    None come back unchanged, so it is safe to apply to any value."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(CAT).replace(tzinfo=None)
        return value + CAT_OFFSET
    return value


def now_cat():
    """The current time in Harare (naive)."""
    return datetime.utcnow() + CAT_OFFSET


def today_cat():
    """Today's date in Harare."""
    return now_cat().date()

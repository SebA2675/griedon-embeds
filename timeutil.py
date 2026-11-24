import calendar
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


VALID_REPEATS = ("once", "every_30m", "hourly", "every_6h", "every_12h", "daily", "weekly", "monthly")

_INTERVAL_MINUTES = {
    "every_30m": 30,
    "hourly": 60,
    "every_6h": 360,
    "every_12h": 720,
}


def to_utc(date_text, time_text, tz_name):
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError("Unknown time zone")
    try:
        naive = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("Use a date like 2026-12-01 and a time like 18:30")
    try:
        return int(naive.replace(tzinfo=tz).timestamp())
    except (OverflowError, OSError, ValueError):
        raise ValueError("That date is out of range")


def _add_month(day):
    y = day.year + (1 if day.month == 12 else 0)
    m = 1 if day.month == 12 else day.month + 1
    max_d = calendar.monthrange(y, m)[1]
    return day.replace(year=y, month=m, day=min(day.day, max_d))


def advance(run_date, run_time, tz_name, repeat, after, current_ts=None):
    if repeat in _INTERVAL_MINUTES:
        step = _INTERVAL_MINUTES[repeat] * 60
        ts = int(current_ts) if current_ts else to_utc(run_date, run_time, tz_name)
        for _ in range(10000):
            ts += step
            if ts > after:
                dt = datetime.fromtimestamp(ts, ZoneInfo(tz_name))
                return dt.date().isoformat(), ts
        raise ValueError("Could not find the next run")

    day = datetime.strptime(run_date, "%Y-%m-%d").date()
    for _ in range(4000):
        if repeat == "monthly":
            day = _add_month(day)
        else:
            day += timedelta(days=(1 if repeat == "daily" else 7))
        ts = to_utc(day.isoformat(), run_time, tz_name)
        if ts > after:
            return day.isoformat(), ts
    raise ValueError("Could not find the next run")


def first_run(run_date, run_time, tz_name, repeat, now=None):
    now = int(now or time.time())
    ts = to_utc(run_date, run_time, tz_name)
    if ts > now:
        return run_date, ts
    if repeat == "once":
        raise ValueError("That time is already in the past")
    return advance(run_date, run_time, tz_name, repeat, now, current_ts=ts)
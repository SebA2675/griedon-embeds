import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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


def step_days(repeat):
    return 1 if repeat == "daily" else 7


def advance(run_date, run_time, tz_name, repeat, after):
    day = datetime.strptime(run_date, "%Y-%m-%d").date()
    for _ in range(4000):
        day += timedelta(days=step_days(repeat))
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
    return advance(run_date, run_time, tz_name, repeat, now)

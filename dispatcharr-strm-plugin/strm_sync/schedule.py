"""When a scheduled sync is due. Pure: no Django, so it is tested on its own.

Dispatcharr's Celery beat runs a check every few minutes (see tasks.py); this
module decides whether that check starts a sync. Doing it this way means a
schedule change takes effect on the next check, without the plugin having to
notice that its settings were saved.
"""

import re
from datetime import datetime, timedelta

TIME_RE = re.compile(r"^\s*([01]?\d|2[0-3])[:.]([0-5]\d)\s*$")

# A daily run missed by more than this (Dispatcharr was off) waits for the next day.
DAILY_GRACE = timedelta(hours=12)


def parse_time(text):
    """"03:00" -> (3, 0); None when it is not a 24-hour time."""
    match = TIME_RE.match(str(text or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def daily_slot(now, hour, minute):
    """The most recent HH:MM at or before now (now is time-zone aware, local)."""
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > now:
        slot -= timedelta(days=1)
    return slot


def _parse(iso):
    try:
        return datetime.fromisoformat(iso) if iso else None
    except (TypeError, ValueError):
        return None


def check(mode, hours, time_text, now, sched_state, last_run_start=None):
    """Whether to start a scheduled sync now.

    mode: "off", "interval" or "daily". now: aware datetime in the user's time zone.
    sched_state: dict saved between checks; updated in place.
    last_run_start: aware datetime of the last sync of any kind, so a sync run by
    hand also restarts an "every N hours" clock.
    Returns True when a sync should be queued.
    """
    if mode == "interval":
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            return False
        if hours < 1:
            return False
        anchor = _parse(sched_state.get("anchor"))
        if last_run_start and (anchor is None or last_run_start > anchor):
            anchor = last_run_start
        if anchor is None:
            # Turned on just now: first run one interval from now, not straight away.
            sched_state["anchor"] = now.isoformat()
            return False
        if now - anchor >= timedelta(hours=hours) - timedelta(minutes=1):
            sched_state["anchor"] = now.isoformat()
            return True
        return False

    if mode == "daily":
        parsed = parse_time(time_text)
        if not parsed:
            return False
        slot = daily_slot(now, *parsed)
        key = slot.isoformat()
        done = sched_state.get("daily_slot")
        if done is None or sched_state.get("daily_time") != f"{parsed[0]:02d}:{parsed[1]:02d}":
            # First check after turning it on or changing the time: wait for the next slot.
            sched_state["daily_slot"] = key
            sched_state["daily_time"] = f"{parsed[0]:02d}:{parsed[1]:02d}"
            return False
        if done == key:
            return False
        sched_state["daily_slot"] = key
        return now - slot <= DAILY_GRACE

    return False


def next_run(mode, hours, time_text, now, sched_state, last_run_start=None):
    """Aware datetime of the next scheduled sync, for the dashboard. None when off."""
    if mode == "interval":
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            return None
        if hours < 1:
            return None
        anchor = _parse(sched_state.get("anchor"))
        if last_run_start and (anchor is None or last_run_start > anchor):
            anchor = last_run_start
        return (anchor or now) + timedelta(hours=hours)

    if mode == "daily":
        parsed = parse_time(time_text)
        if not parsed:
            return None
        slot = daily_slot(now, *parsed)
        if sched_state.get("daily_slot") != slot.isoformat() and sched_state.get("daily_slot") is not None \
                and now - slot <= DAILY_GRACE:
            return slot  # due at the next check
        return slot + timedelta(days=1)

    return None

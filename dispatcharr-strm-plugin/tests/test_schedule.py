from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from strm_sync import schedule

LONDON = ZoneInfo("Europe/London")


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=LONDON)


def test_parse_time():
    assert schedule.parse_time("03:00") == (3, 0)
    assert schedule.parse_time(" 7.30 ") == (7, 30)
    assert schedule.parse_time("23:59") == (23, 59)
    for bad in ("24:00", "3pm", "", None, "12:60"):
        assert schedule.parse_time(bad) is None


def test_off_never_runs():
    assert not schedule.check("off", 1, "03:00", at(11, 3), {})


def test_interval_waits_one_interval_after_being_turned_on():
    state = {}
    assert not schedule.check("interval", 6, "", at(11, 10), state)            # just turned on
    assert not schedule.check("interval", 6, "", at(11, 15, 55), state)
    assert schedule.check("interval", 6, "", at(11, 16), state)                # 6 hours later
    assert not schedule.check("interval", 6, "", at(11, 16, 5), state)         # not again 5 minutes later
    assert schedule.check("interval", 6, "", at(11, 22), state)


def test_interval_counts_from_a_sync_run_by_hand():
    state = {}
    schedule.check("interval", 6, "", at(11, 10), state)
    manual = at(11, 14)
    assert not schedule.check("interval", 6, "", at(11, 16), state, last_run_start=manual)
    assert schedule.check("interval", 6, "", at(11, 20), state, last_run_start=manual)
    assert schedule.next_run("interval", 6, "", at(11, 15), {"anchor": at(11, 10).isoformat()}, manual) == at(11, 20)


def test_interval_below_one_hour_is_ignored():
    state = {"anchor": at(1, 0).isoformat()}
    assert not schedule.check("interval", 0.5, "", at(11, 10), state)
    assert schedule.next_run("interval", "x", "", at(11, 10), state) is None


def test_daily_runs_once_per_day_after_the_time():
    state = {}
    assert not schedule.check("daily", 0, "03:00", at(11, 14), state)          # turned on at 14:00: wait for tomorrow
    assert schedule.next_run("daily", 0, "03:00", at(11, 14), state) == at(12, 3)
    assert not schedule.check("daily", 0, "03:00", at(12, 2, 55), state)
    assert schedule.check("daily", 0, "03:00", at(12, 3, 2), state)            # first check after 03:00
    assert not schedule.check("daily", 0, "03:00", at(12, 3, 7), state)
    assert not schedule.check("daily", 0, "03:00", at(12, 23), state)
    assert schedule.check("daily", 0, "03:00", at(13, 3, 4), state)


def test_daily_missed_by_a_long_time_waits_for_the_next_day():
    state = {}
    schedule.check("daily", 0, "03:00", at(11, 14), state)
    # Dispatcharr was off from before 03:00 until 16:00 the next day: 13 hours late.
    assert not schedule.check("daily", 0, "03:00", at(12, 16), state)
    # A shorter outage (back at 09:00) still runs that morning's sync.
    state = {}
    schedule.check("daily", 0, "03:00", at(11, 14), state)
    assert schedule.check("daily", 0, "03:00", at(12, 9), state)


def test_daily_time_change_waits_for_the_new_time():
    state = {}
    schedule.check("daily", 0, "03:00", at(11, 14), state)
    assert not schedule.check("daily", 0, "12:00", at(11, 14, 5), state)       # 12:00 today has passed: tomorrow
    assert schedule.check("daily", 0, "12:00", at(12, 12, 1), state)


def test_daily_across_the_clock_change():
    state = {}
    schedule.check("daily", 0, "03:00", datetime(2026, 3, 28, 12, tzinfo=LONDON), state)
    # Clocks go forward at 01:00 on 29 March; 03:00 local still comes once.
    assert schedule.check("daily", 0, "03:00", datetime(2026, 3, 29, 3, 1, tzinfo=LONDON), state)
    assert not schedule.check("daily", 0, "03:00", datetime(2026, 3, 29, 4, tzinfo=LONDON), state)

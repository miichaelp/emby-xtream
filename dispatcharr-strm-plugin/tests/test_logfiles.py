import logging
import os
from datetime import datetime

from strm_sync.logfiles import DailyLogHandler, prune, rotate_file, rotated_files


def ts(day, hour=12, minute=0, second=0):
    return datetime(2026, 11, day, hour, minute, second).timestamp()


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def logger_with(handler):
    log = logging.getLogger(f"test.logfiles.{id(handler)}")
    log.handlers[:] = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


def names(directory):
    return sorted(os.listdir(directory))


def test_a_new_file_starts_on_a_new_day(tmp_path):
    clock = Clock(ts(13, 23, 59))
    handler = DailyLogHandler(str(tmp_path / "strm-sync.log"), keep=7, clock=clock, check_every=0)
    log = logger_with(handler)
    log.info("late on the 13th")
    os.utime(tmp_path / "strm-sync.log", (ts(13, 23, 59), ts(13, 23, 59)))
    clock.now = ts(14, 0, 1)
    log.info("early on the 14th")
    assert names(tmp_path) == ["strm-sync-2026-11-13.log", "strm-sync.log"]
    assert "late on the 13th" in (tmp_path / "strm-sync-2026-11-13.log").read_text()
    assert (tmp_path / "strm-sync.log").read_text().strip().endswith("early on the 14th")
    assert "13th" not in (tmp_path / "strm-sync.log").read_text()


def test_only_the_set_number_of_old_files_is_kept(tmp_path):
    path = str(tmp_path / "strm-sync.log")
    for day in range(1, 11):
        old = tmp_path / f"strm-sync-2026-11-{day:02d}.log"
        old.write_text("x")
        os.utime(old, (ts(day), ts(day)))
    (tmp_path / "other.log").write_text("not ours")
    assert prune(path, 3) == 7
    assert names(tmp_path) == ["other.log", "strm-sync-2026-11-08.log", "strm-sync-2026-11-09.log",
                               "strm-sync-2026-11-10.log"]
    assert [os.path.basename(f) for f in rotated_files(path)] == [
        "strm-sync-2026-11-10.log", "strm-sync-2026-11-09.log", "strm-sync-2026-11-08.log"]


def test_by_hand_gets_the_time_and_an_empty_log_is_left_alone(tmp_path):
    clock = Clock(ts(14, 10, 15, 2))
    handler = DailyLogHandler(str(tmp_path / "strm-sync.log"), keep=7, clock=clock, check_every=0)
    log = logger_with(handler)
    assert handler.rotate(by_hand=True) is None                  # nothing written yet
    log.info("first")
    os.utime(tmp_path / "strm-sync.log", (ts(14, 10), ts(14, 10)))
    target = handler.rotate(by_hand=True)
    assert os.path.basename(target) == "strm-sync-2026-11-14_101502.log"
    log.info("second")
    assert (tmp_path / "strm-sync.log").read_text().strip().endswith("second")
    assert "first" in open(target).read()


def test_another_process_rotating_is_followed(tmp_path):
    path = str(tmp_path / "sync.log")
    clock = Clock(ts(14))
    mine = DailyLogHandler(path, keep=7, clock=clock, check_every=5)
    log = logger_with(mine)
    log.info("before")
    rotate_file(path, 7, now=ts(14), by_hand=True)              # the web process put it aside
    clock.now += 6
    log.info("after")
    assert open(path).read().strip().endswith("after")
    assert len(rotated_files(path)) == 1


def test_keep_zero_keeps_no_old_files(tmp_path):
    path = tmp_path / "strm-sync.log"
    path.write_text("x")
    os.utime(path, (ts(13), ts(13)))
    rotate_file(str(path), 0, now=ts(14))
    assert names(tmp_path) == []

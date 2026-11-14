"""Log files that start afresh each day, keep a set number of old days, and can be
rotated by hand.

    strm-sync.log              today's log
    strm-sync-2026-11-13.log   an earlier day
    strm-sync-2026-11-14_101502.log   put aside by hand at 10:15:02

Several processes write the same file (Dispatcharr's web process and each sync
process), so rotation is decided from the file itself: before a line is
written, a file last written on an earlier day is renamed first. A process
that finds its file renamed by another simply opens the new one.
"""

import glob
import logging
import os
import time
from datetime import datetime


def _day(timestamp):
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def rotated_files(path):
    """Old log files of this log, newest first."""
    base, ext = os.path.splitext(path)
    files = [f for f in glob.glob(f"{glob.escape(base)}-*{ext}") if os.path.isfile(f)]
    return sorted(files, key=lambda f: (os.path.getmtime(f), f), reverse=True)


def prune(path, keep):
    """Deletes old log files beyond the newest `keep`. Returns how many went."""
    gone = 0
    for old in rotated_files(path)[max(int(keep), 0):]:
        try:
            os.remove(old)
            gone += 1
        except OSError:
            pass
    return gone


def rotate_file(path, keep, now=None, by_hand=False):
    """Puts the current log aside under a dated name. Returns the new name, or None.

    A daily rotation names it after the day it was last written. A rotation by
    hand, or a second one on the same day, adds the time.
    """
    now = time.time() if now is None else now
    try:
        stat = os.stat(path)
    except OSError:
        return None
    if stat.st_size == 0:
        return None
    base, ext = os.path.splitext(path)
    day = _day(stat.st_mtime)
    target = f"{base}-{day}{ext}"
    if by_hand or os.path.exists(target):
        target = f"{base}-{day}_{datetime.fromtimestamp(now).strftime('%H%M%S')}{ext}"
    try:
        os.replace(path, target)
    except OSError:
        return None
    prune(path, keep)
    return target


class DailyLogHandler(logging.Handler):
    """A file handler that starts a new file each day (see the module notes)."""

    def __init__(self, path, keep=7, clock=time.time, check_every=5.0):
        super().__init__()
        self.path = path
        self.keep = keep
        self.clock = clock
        self.check_every = check_every
        self._last_check = None
        self._stream = None
        self._open()

    def _open(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._stream = open(self.path, "a", encoding="utf-8")

    def _close_stream(self):
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
            self._stream = None

    def _check(self, now):
        """Reopens a file another process rotated, and rotates one from an earlier day."""
        try:
            on_disk = os.stat(self.path)
        except OSError:
            on_disk = None
        try:
            mine = os.fstat(self._stream.fileno()) if self._stream else None
        except (OSError, ValueError):
            mine = None
        if on_disk is None or mine is None or (on_disk.st_ino, on_disk.st_dev) != (mine.st_ino, mine.st_dev):
            self._close_stream()
            self._open()
            on_disk = os.stat(self.path)
        if on_disk.st_size and _day(on_disk.st_mtime) != _day(now):
            self.rotate(now)

    def rotate(self, now=None, by_hand=False):
        """Puts the current file aside and starts a new one. Returns the old file's new name."""
        now = self.clock() if now is None else now
        self.acquire()
        try:
            self._close_stream()
            target = rotate_file(self.path, self.keep, now, by_hand=by_hand)
            self._open()
            self._last_check = now
            return target
        finally:
            self.release()

    def emit(self, record):
        try:
            message = self.format(record)
            now = self.clock()
            self.acquire()
            try:
                if self._last_check is None or now - self._last_check >= self.check_every:
                    self._last_check = now
                    self._check(now)
                self._stream.write(message + "\n")
                self._stream.flush()
            finally:
                self.release()
        except Exception:
            self.handleError(record)

    def close(self):
        self.acquire()
        try:
            self._close_stream()
        finally:
            self.release()
        super().close()

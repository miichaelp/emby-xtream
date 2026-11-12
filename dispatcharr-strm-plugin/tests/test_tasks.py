import json
import os

import fakeredis
import pytest

from fakes import FakeSource, episode, movie, show
from strm_sync import runtime, tasks
from strm_sync.engine import Settings

BASE = "http://d:9191"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A run_sync that talks to a fake Redis, a fake catalogue and temp folders."""
    redis = fakeredis.FakeStrictRedis(decode_responses=True)
    out = tmp_path / "strm"
    box = {
        "redis": redis, "out": out, "queued": [], "emby": [],
        "raw": {"output_dir": str(out), "base_url": BASE},
        "enabled": True,
        "source": FakeSource(movies=[movie(1, "Heat"), movie(2, "Ronin")],
                             shows=[show(1, "Show", [episode(1, 1, 1)])]),
    }
    monkeypatch.setattr(runtime, "DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(runtime, "redis_client", lambda: redis)
    monkeypatch.setattr(tasks, "load_settings", lambda: (Settings(box["raw"]), box["enabled"]))
    monkeypatch.setattr(tasks, "queue_sync", lambda *a, **k: box["queued"].append((a, k)))
    monkeypatch.setattr(runtime, "notify_emby", lambda s: box["emby"].append(1) or "Emby library scan started")
    import strm_sync.source as source_module
    monkeypatch.setattr(source_module, "DispatcharrSource", lambda log: box["source"])
    return box


def test_full_run_writes_files_history_counts_and_releases_the_lock(env):
    assert tasks.run_sync(trigger="manual") == "Sync finished"
    assert (env["out"] / "Movies/Heat/Heat.strm").exists()
    assert (env["out"] / "Shows/Show/Season 01/Show - S01E01.strm").exists()

    state = runtime.load_state()
    entry = state["history"][0]
    assert entry["success"] and entry["trigger"] == "manual" and entry["message"] == "Completed"
    assert entry["movies"]["added"] == 2 and entry["series"]["episodes"] == 1
    assert state["counts"] == {"movies": 2, "movie_files": 2, "series": 1, "episodes": 1, "episode_files": 1}
    assert env["emby"] == [1]                                  # files changed, so Emby was told
    assert not env["redis"].exists(runtime.K_LOCK) and not env["redis"].exists(runtime.K_PROGRESS)
    assert runtime.read_progress(env["redis"]) == {"running": False}

    tasks.run_sync(trigger="manual")
    assert env["emby"] == [1]                                  # nothing changed: no second scan
    assert len(runtime.load_state()["history"]) == 2


def test_disabled_or_misconfigured_does_not_run(env):
    env["enabled"] = False
    assert tasks.run_sync() == "STRM Sync is disabled"
    env["enabled"] = True
    env["raw"]["base_url"] = ""
    assert "Dispatcharr address" in tasks.run_sync()
    assert "Dispatcharr address" in runtime.load_state()["last_error"]
    assert not env["out"].exists()
    env["raw"].update(base_url=BASE, auto_sync=False)
    assert tasks.run_sync(trigger="auto") == "Sync after an M3U refresh is turned off"


def test_second_run_while_locked_is_refused_and_auto_reruns_afterwards(env):
    other = runtime.Control(env["redis"])
    assert other.acquire()
    assert tasks.run_sync(trigger="manual") == "A sync is already running"
    assert not env["redis"].exists(runtime.K_RERUN)
    assert tasks.run_sync(trigger="auto") == "A sync is already running"
    assert env["redis"].exists(runtime.K_RERUN)                # a refresh finished meanwhile
    assert env["redis"].get(runtime.K_LOCK) == other.token     # the running sync keeps its lock
    other.release()

    tasks.run_sync(trigger="manual")
    assert len(env["queued"]) == 1 and env["queued"][0][1]["trigger"] == "auto"
    assert not env["redis"].exists(runtime.K_RERUN)


def test_stop_flag_stops_the_run_and_is_cleared(env):
    env["source"] = FakeSource(movies=[movie(i, f"Movie {i}") for i in range(1, 50)])

    class StopOnThird(FakeSource):
        def iter_movies(self, only_ids=None):
            for index, item in enumerate(self.movies):
                if index == 3:
                    assert runtime.request_stop(env["redis"])
                    import time
                    time.sleep(0.6)                              # the stop check is throttled to 2 per second
                yield item

    env["source"] = StopOnThird(movies=env["source"].movies)
    tasks.run_sync()
    entry = runtime.load_state()["history"][0]
    assert entry["stopped"] and not entry["success"] and entry["message"].startswith("Stopped")
    assert entry["movies"]["total"] < 49
    assert "counts" not in runtime.load_state()                # a stopped run does not move the counts
    assert not env["redis"].exists(runtime.K_STOP)
    assert not runtime.request_stop(env["redis"])              # nothing running any more


def test_failed_items_are_kept_then_cleared_by_a_retry(env):
    blocker = env["out"] / "Movies" / "Ronin"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("in the way")
    tasks.run_sync()
    state = runtime.load_state()
    assert [(f["kind"], f["id"]) for f in state["failed"]] == [("movie", 2)]
    assert state["history"][0]["message"] == "Completed with 1 failed item(s)"

    os.remove(blocker)
    tasks.run_sync(only={"movies": [2], "series": []})
    state = runtime.load_state()
    assert state["failed"] == []
    assert state["history"][0]["trigger"] == "retry"
    assert (env["out"] / "Movies/Ronin/Ronin.strm").exists()


def test_a_crash_is_recorded_and_the_lock_released(env):
    class Broken(FakeSource):
        def count_movies(self, only_ids=None):
            raise RuntimeError("database is down")

    env["source"] = Broken()
    assert tasks.run_sync() == "database is down"
    state = runtime.load_state()
    assert state["last_error"] == "database is down" and not state["history"][0]["success"]
    assert not env["redis"].exists(runtime.K_LOCK)


def test_delete_removes_only_our_files(env):
    tasks.run_sync()
    mine = env["out"] / "Movies" / "Mine" / "Mine.strm"
    mine.parent.mkdir()
    mine.write_text("http://nas/x.mkv")
    assert tasks.run_delete(kinds=["movies"]) == "Deleted"
    assert os.listdir(env["out"] / "Movies") == ["Mine"]
    assert (env["out"] / "Shows/Show/Season 01/Show - S01E01.strm").exists()
    assert runtime.load_state()["counts"]["movies"] == 0


def test_vod_refresh_hook_queues_a_sync_only_for_that_task(env):
    class Task:
        def __init__(self, name):
            self.name = name

    tasks._on_task_finished(task=Task("apps.m3u.tasks.refresh_single_m3u_account"))
    assert env["queued"] == []
    tasks._on_task_finished(task=Task(tasks.VOD_REFRESH_TASK), retval="VOD refresh failed: boom")
    assert env["queued"] == []
    tasks._on_task_finished(task=Task(tasks.VOD_REFRESH_TASK), retval="Batch VOD refresh completed")
    assert len(env["queued"]) == 1 and env["queued"][0][1] == {"trigger": "auto", "countdown": 10}
    env["raw"]["auto_sync"] = False
    tasks._on_task_finished(task=Task(tasks.VOD_REFRESH_TASK))
    assert len(env["queued"]) == 1


def test_progress_is_visible_to_the_dashboard_while_running(env):
    control = runtime.Control(env["redis"], trigger="auto")
    assert control.acquire()
    control.progress("movies", "Writing movies", 5, 20, {"failed": 1, "added": 4}, force=True)
    seen = runtime.read_progress(env["redis"])
    assert seen["running"] and seen["phase"] == "Writing movies" and (seen["done"], seen["total"]) == (5, 20)
    assert seen["failed"] == 1 and seen["trigger"] == "auto" and not seen["stopping"]
    runtime.request_stop(env["redis"])
    assert runtime.read_progress(env["redis"])["stopping"]
    control.release()
    runtime.mark_queued(env["redis"])
    assert runtime.read_progress(env["redis"])["queued"]


def test_manifest_and_dashboard_page(env, tmp_path, monkeypatch):
    manifest = tasks.manifest()
    ids = [f["id"] for f in manifest["fields"]]
    assert len(ids) == len(set(ids))
    # Every setting the engine reads has a field, so its default is what the UI shows.
    for name in ("output_dir", "base_url", "safety_limit", "episode_fetch_delay_ms", "url_style", "emby_api_key"):
        assert name in ids
    defaults = {f["id"]: f.get("default") for f in manifest["fields"] if f["type"] != "info"}
    s = Settings(defaults)
    assert s.cleanup_orphans and s.multi_version and s.safety_limit == 0.2 and s.url_style == "proxy"
    assert s.version_label == "folder" and s.episode_titles is False
    # Every setting sits under a heading, and every heading has words under it.
    assert manifest["fields"][0]["type"] == "info"
    assert all(f.get("description") for f in manifest["fields"] if f["type"] == "info")

    monkeypatch.setattr(runtime, "logos_dir", lambda: str(tmp_path / "logos"))
    assert runtime.install_dashboard()
    page = (tmp_path / "logos" / runtime.PLUGIN_KEY / "index.html").read_text()
    assert "__PLUGIN_KEY__" not in page and f"var KEY = '{runtime.PLUGIN_KEY}'" in page
    assert manifest["help_url"] == runtime.DASHBOARD_URL


def test_log_hides_secrets(env):
    # The log file handler is added once per process; drop the one an earlier test made
    # so this test's own data folder gets the file.
    for handler in list(runtime.log.handlers):
        if getattr(handler, "_strm_sync", False):
            runtime.log.removeHandler(handler)
    log = runtime.setup_logging()
    log.info("wrote http://d:9191/movie/emby/s3cretpw/7.mkv")
    for handler in log.handlers:
        handler.flush()
    text = runtime.read_log(secrets=("s3cretpw", ""))
    assert "s3cretpw" not in text and "/movie/emby/***/7.mkv" in text


def test_check_schedule_queues_a_sync_when_due(env, monkeypatch):
    from datetime import datetime, timedelta, timezone as tz

    clock = {"now": datetime(2026, 11, 11, 10, tzinfo=tz.utc)}
    monkeypatch.setattr(tasks, "local_now", lambda: clock["now"])

    assert tasks.check_schedule() == "Not scheduled"                    # schedule off by default
    env["raw"].update(schedule="interval", schedule_hours=2)
    assert tasks.check_schedule() == "Not due"                          # first check: clock starts
    clock["now"] += timedelta(hours=2)
    assert tasks.check_schedule() == "Queued"
    assert env["queued"][-1][1] == {"trigger": "schedule"}
    assert tasks.check_schedule() == "Not due"

    clock["now"] += timedelta(hours=2)
    other = runtime.Control(env["redis"])
    other.acquire()
    assert tasks.check_schedule() == "Already running"
    other.release()
    assert len(env["queued"]) == 1

    env["raw"].update(schedule="daily", schedule_time="25:00")
    assert tasks.check_schedule() == "Not scheduled"                    # bad time is a settings problem


def test_scheduled_run_is_recorded_and_respects_the_switch(env):
    env["raw"]["schedule"] = "off"
    assert tasks.run_sync(trigger="schedule") == "The schedule is turned off"
    env["raw"]["schedule"] = "daily"
    assert tasks.run_sync(trigger="schedule") == "Sync finished"
    assert runtime.load_state()["history"][0]["trigger"] == "schedule"


def test_schedule_settings_problems():
    base = {"output_dir": "/s", "base_url": BASE}
    assert any("1 hour" in p for p in Settings(dict(base, schedule="interval", schedule_hours=0)).problems())
    assert any("24-hour" in p for p in Settings(dict(base, schedule="daily", schedule_time="3pm")).problems())
    assert Settings(dict(base, schedule="daily", schedule_time="03:00")).problems() == []
    assert Settings(dict(base, schedule="weekly")).schedule == "off"


def test_the_worker_hook_checks_the_schedule_at_most_once_a_minute(env, monkeypatch):
    checks = []
    monkeypatch.setattr(tasks, "check_schedule", lambda: checks.append(1))

    class Task:
        name = "core.tasks.scan_and_process_files"

    for _ in range(5):
        tasks._on_task_finished(task=Task())
    assert checks == [1]
    env["redis"].delete(runtime.K_SCHEDULE_CHECK)        # a minute later
    tasks._on_task_finished(task=Task())
    assert checks == [1, 1]


def test_a_folder_that_cannot_be_created_is_explained(env, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    env["raw"]["output_dir"] = str(locked / "STRMSync")
    try:
        if os.access(str(locked), os.W_OK):
            pytest.skip("running as root: permissions are not enforced")
        assert "does not exist inside the Dispatcharr container" in tasks.run_sync()
    finally:
        locked.chmod(0o700)


def test_ensure_output_dir_explains_a_missing_mapping(monkeypatch):
    def refuse(path, exist_ok=False):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(os, "makedirs", refuse)
    with pytest.raises(RuntimeError) as err:
        tasks.ensure_output_dir("/STRMSync-not-there")
    assert "docker-compose.yml" in str(err.value) and ":/STRMSync-not-there" in str(err.value)


def test_top_folder_is_refused():
    assert any("top folder" in p for p in Settings({"output_dir": "/", "base_url": BASE}).problems())
    assert Settings({"output_dir": "/strm/", "base_url": BASE}).output_dir == "/strm"


def test_the_log_is_also_written_into_the_strm_folder(env):
    env["raw"]["xc_password"] = "s3cretpw"
    runtime.log.info("before the sync")                  # not in the STRM folder: no folder known yet
    tasks.run_sync()
    runtime.log.info("password s3cretpw in a line")
    for handler in runtime.log.handlers:
        handler.flush()
    text = (env["out"] / "strm-sync.log").read_text()
    assert "Sync started (manual)" in text and "Added movie: Heat (1 file)" in text
    assert "s3cretpw" not in text and "password *** in a line" in text
    assert "before the sync" not in text
    # A second run keeps one handler, not two.
    tasks.run_sync()
    assert sum(1 for h in runtime.log.handlers if getattr(h, "_strm_sync_output", None)) == 1

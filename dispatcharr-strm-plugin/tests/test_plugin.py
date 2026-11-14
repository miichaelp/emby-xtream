import fakeredis
import pytest

from strm_sync import runtime, tasks
from strm_sync.engine import Settings


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    redis = fakeredis.FakeStrictRedis(decode_responses=True)
    box = {"raw": {"output_dir": str(tmp_path / "strm"), "base_url": "http://d:9191", "xc_password": "s3cretpw"},
           "sync": [], "delete": [], "redis": redis}
    monkeypatch.setattr(runtime, "DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(runtime, "logos_dir", lambda: str(tmp_path / "logos"))
    monkeypatch.setattr(runtime, "redis_client", lambda: redis)
    monkeypatch.setattr(tasks, "load_settings", lambda: (Settings(box["raw"]), True))
    monkeypatch.setattr(tasks, "queue_sync", lambda kinds, **k: box["sync"].append((list(kinds), k)))
    monkeypatch.setattr(tasks, "queue_delete", lambda kinds: box["delete"].append(list(kinds)))
    box["ensured"] = []
    monkeypatch.setattr(tasks, "remove_old_schedule_entry", lambda: box["ensured"].append(1))
    monkeypatch.setattr(tasks, "next_scheduled_run", lambda settings, state: None)
    from strm_sync.plugin import Plugin

    box["plugin"] = Plugin()
    return box


def run(box, action, params=None):
    return box["plugin"].run(action, params or {}, {"settings": {}, "logger": runtime.log})


def test_plugin_matches_its_manifest_and_installs_the_dashboard(plugin, tmp_path):
    p = plugin["plugin"]
    manifest = tasks.manifest()
    assert (p.name, p.version) == (manifest["name"], manifest["version"])
    assert {a["id"] for a in p.actions} >= {"sync_all", "stop", "retry_failed", "delete_movies"}
    assert (tmp_path / "logos" / runtime.PLUGIN_KEY / "index.html").exists()
    for action in p.actions:                       # every button has a handler
        assert hasattr(p, f"_do_{action['id']}")


def test_sync_buttons_queue_the_right_kinds(plugin):
    assert run(plugin, "sync_all")["status"] == "queued"
    run(plugin, "sync_movies")
    run(plugin, "sync_series")
    assert [k for k, _ in plugin["sync"]] == [["movies", "series"], ["movies"], ["series"]]
    assert run(plugin, "nope")["status"] == "error"


def test_sync_is_refused_when_settings_are_missing_or_one_is_running(plugin):
    plugin["raw"]["base_url"] = ""
    answer = run(plugin, "sync_all")
    assert answer["status"] == "error" and "Dispatcharr address" in answer["message"]
    plugin["raw"]["base_url"] = "http://d:9191"
    control = runtime.Control(plugin["redis"])
    control.acquire()
    assert "already running" in run(plugin, "sync_all")["message"]
    assert "Stop it first" in run(plugin, "delete_movies")["message"]
    assert plugin["sync"] == [] and plugin["delete"] == []
    assert "Stopping" in run(plugin, "stop")["message"]
    control.release()
    assert run(plugin, "stop")["message"] == "No sync is running."
    assert run(plugin, "delete_series")["status"] == "queued" and plugin["delete"] == [["series"]]


def test_retry_failed_passes_the_failed_ids(plugin):
    assert "no failed items" in run(plugin, "retry_failed")["message"]
    runtime.save_state({"failed": [
        {"kind": "movie", "id": 5, "name": "A", "error": "x"}, {"kind": "movie", "id": 3, "name": "B", "error": "x"},
        {"kind": "series", "id": 9, "name": "C", "error": "x"}]})
    run(plugin, "retry_failed")
    assert plugin["sync"][0][1]["only"] == {"movies": [3, 5], "series": [9]}


def test_dashboard_data_holds_no_secrets(plugin):
    runtime.save_state({"history": [{"end": "2026-01-01T00:00:00+00:00", "message": "Completed"}], "counts": {"movies": 4}})
    data = run(plugin, "dashboard")
    assert data["counts"] == {"movies": 4} and data["problems"] == [] and data["progress"] == {"running": False}
    assert "s3cretpw" not in str(data) and "xc_password" not in str(data)
    status = run(plugin, "status")["message"]
    assert "Last sync" in status and "Dashboard: http://d:9191/logos/" in status


def test_stop_hook_requests_a_stop(plugin):
    control = runtime.Control(plugin["redis"])
    control.acquire()
    plugin["plugin"].stop({})
    assert plugin["redis"].exists(runtime.K_STOP)


def test_the_old_schedule_entry_is_removed_once_on_load(plugin):
    assert plugin["ensured"] == [1]
    run(plugin, "dashboard")
    assert plugin["ensured"] == [1]

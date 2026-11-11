"""Starts the real background process (runner.py) against a stand-in Dispatcharr project."""

import json
import os
import shutil
import sys
import time

import pytest

from strm_sync import runtime

FAKEPROJECT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakeproject")


def wait_for(path, text, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            if text in content:
                return content
        except OSError:
            pass
        time.sleep(0.2)
    raise AssertionError(f"{text!r} never appeared in {path}:\n" + (open(path).read() if os.path.exists(path) else "(missing)"))


@pytest.fixture
def spawned(tmp_path, monkeypatch):
    pytest.importorskip("django")
    pytest.importorskip("fakeredis")
    # Run from a copy named differently from the package, to prove the folder name does not matter.
    plugin_copy = tmp_path / "STRM Sync copy"
    shutil.copytree(os.path.dirname(runtime.__file__), plugin_copy, ignore=shutil.ignore_patterns("__pycache__"))
    data = tmp_path / "data"
    monkeypatch.setattr(runtime, "PLUGIN_DIR", str(plugin_copy))
    monkeypatch.setattr(runtime, "DATA_DIR", str(data))
    monkeypatch.setattr(runtime, "project_dir", lambda: FAKEPROJECT)
    monkeypatch.setattr(runtime, "python_executable", lambda: sys.executable)
    monkeypatch.setenv("FAKEPROJECT_ARGV_FILE", str(tmp_path / "argv.json"))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in sys.path if "site-packages" in p))
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    return tmp_path, data


def test_runner_starts_django_as_a_worker_and_runs_the_job(spawned, monkeypatch):
    tmp_path, data = spawned
    monkeypatch.setenv("FAKEPROJECT_ENABLED", "0")
    started = time.time()
    runtime.spawn({"job": "sync", "kinds": ["movies"], "trigger": "manual", "only": None})
    assert time.time() - started < 5                      # returns at once; the process runs on
    output = wait_for(str(data / "runner.log"), "sync -> STRM Sync is disabled")
    assert "Traceback" not in output
    with open(tmp_path / "argv.json") as fh:
        assert json.load(fh) == ["celery", "strm-sync-runner"]


def test_runner_writes_files_when_enabled(spawned, monkeypatch):
    tmp_path, data = spawned
    monkeypatch.setenv("FAKEPROJECT_ENABLED", "1")
    monkeypatch.setenv("FAKEPROJECT_OUT", str(tmp_path / "strm"))
    runtime.spawn({"job": "sync", "kinds": ["movies"], "trigger": "manual", "only": None})
    # The stand-in project has no VOD tables, so the sync reaches the database and fails there:
    # proof that settings, lock and the catalogue code all ran in the new process.
    output = wait_for(str(data / "runner.log"), "sync ->")
    assert "runner failed" not in output
    state = json.loads((data / "state.json").read_text())
    assert state["history"][0]["trigger"] == "manual"
    assert (tmp_path / "strm").is_dir()


def test_runner_failure_is_logged(spawned, monkeypatch):
    tmp_path, data = spawned
    (tmp_path / "not-dispatcharr").mkdir()
    monkeypatch.setattr(runtime, "project_dir", lambda: str(tmp_path / "not-dispatcharr"))
    runtime.spawn({"job": "sync"})
    output = wait_for(str(data / "runner.log"), "runner failed")
    assert "ModuleNotFoundError" in output or "No module named" in output

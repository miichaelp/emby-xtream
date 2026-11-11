"""Everything a run needs around the engine: the lock, live progress, the Stop
flag, saved history, the log file, the dashboard page and the Emby refresh.

Progress and flags live in Redis because the sync runs in a Celery worker and
the dashboard is answered by the web process. History lives in a JSON file.
"""

import json
import logging
import os
import shutil
import time
import uuid
from logging.handlers import RotatingFileHandler

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
# Dispatcharr derives the plugin key from the folder name the same way.
PLUGIN_KEY = os.path.basename(PLUGIN_DIR).replace(" ", "_").lower()
DATA_DIR = os.environ.get("STRM_SYNC_DATA_DIR", "/data/strm_sync")

K_LOCK = "strm_sync:lock"
K_STOP = "strm_sync:stop"
K_PROGRESS = "strm_sync:progress"
K_RERUN = "strm_sync:rerun"
K_QUEUED = "strm_sync:queued"
K_SCHEDULE_CHECK = "strm_sync:schedule_check"
LOCK_TTL = 900
MAX_HISTORY = 10

log = logging.getLogger("strm_sync")


def data_dir():
    """Where history and the log are kept; falls back to the plugin folder."""
    for candidate in (DATA_DIR, os.path.join(PLUGIN_DIR, "data")):
        try:
            os.makedirs(candidate, exist_ok=True)
            if os.access(candidate, os.W_OK):
                return candidate
        except OSError:
            continue
    return PLUGIN_DIR


def setup_logging():
    """Adds the plugin's own log file once per process."""
    log.setLevel(logging.INFO)
    if any(getattr(h, "_strm_sync", False) for h in log.handlers):
        return log
    try:
        handler = RotatingFileHandler(
            os.path.join(data_dir(), "sync.log"), maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handler._strm_sync = True
        log.addHandler(handler)
    except OSError:
        pass
    return log


def read_log(max_lines=2000, secrets=(), name="sync.log"):
    """The tail of a log file in the data folder, with the given secrets blanked out."""
    path = os.path.join(data_dir(), name)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-max_lines:]
    except OSError:
        return ""
    text = "".join(lines)
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
    return text


def redis_client():
    from core.utils import RedisClient

    return RedisClient.get_client()


def _text(value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


# ---- saved state ---------------------------------------------------------------

def _state_path():
    return os.path.join(data_dir(), "state.json")


def load_state():
    try:
        with open(_state_path(), "r", encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    path = _state_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def record_run(state, entry, result, kinds, only):
    """Folds one finished run into the saved state."""
    history = state.setdefault("history", [])
    history.insert(0, entry)
    del history[MAX_HISTORY:]

    # Failed items: a full run of a kind replaces that kind's list; a retry only
    # replaces the items it retried.
    failed = state.get("failed", [])
    kind_of = {"movies": "movie", "series": "series"}
    for kind in kinds:
        ran = result.get(kind) is not None
        if not ran:
            continue
        if only is None:
            failed = [f for f in failed if f.get("kind") != kind_of[kind]]
        else:
            retried = set(only.get(kind) or [])
            failed = [f for f in failed if not (f.get("kind") == kind_of[kind] and f.get("id") in retried)]
    seen = {(f.get("kind"), f.get("id")) for f in failed}
    for item in result.get("failed_items", []):
        if (item.get("kind"), item.get("id")) not in seen:
            failed.append(item)
            seen.add((item.get("kind"), item.get("id")))
    state["failed"] = failed[:500]

    # Library counts only move on a full run that was not stopped.
    if only is None and not result.get("stopped"):
        counts = state.setdefault("counts", {})
        if result.get("movies"):
            counts["movies"] = result["movies"]["total"] - result["movies"]["skipped"] - result["movies"]["failed"]
            counts["movie_files"] = result["movies"]["files"]
        if result.get("series"):
            counts["series"] = result["series"]["total"] - result["series"]["skipped"] - result["series"]["failed"]
            counts["episodes"] = result["series"].get("episodes", 0)
            counts["episode_files"] = result["series"]["files"]
    state["warnings"] = result.get("warnings", [])
    state["removed"] = result.get("removed", [])
    return state


# ---- lock, stop, progress ------------------------------------------------------

class Control:
    """What the engine talks to while it runs."""

    def __init__(self, redis, trigger="manual", kinds=()):
        self.redis = redis
        self.token = uuid.uuid4().hex
        self.trigger = trigger
        self.kinds = list(kinds)
        self.started = time.time()
        self._last_write = 0.0
        self._last_stop_check = 0.0
        self._stop = False

    def acquire(self):
        return bool(self.redis.set(K_LOCK, self.token, nx=True, ex=LOCK_TTL))

    def release(self):
        try:
            if _text(self.redis.get(K_LOCK)) == self.token:
                self.redis.delete(K_LOCK)
            self.redis.delete(K_STOP, K_PROGRESS)
        except Exception:
            log.exception("Could not release the sync lock")

    def should_stop(self):
        now = time.time()
        if not self._stop and now - self._last_stop_check >= 0.5:
            self._last_stop_check = now
            try:
                self._stop = bool(self.redis.exists(K_STOP))
            except Exception:
                pass
        return self._stop

    def progress(self, kind, phase, done, total, stats=None, force=False):
        now = time.time()
        if not force and now - self._last_write < 0.5 and done < total:
            return
        self._last_write = now
        stats = stats or {}
        payload = {
            "running": True,
            "kind": kind,
            "phase": phase,
            "done": int(done),
            "total": int(total),
            "failed": int(stats.get("failed", 0)),
            "skipped": int(stats.get("skipped", 0)),
            "added": int(stats.get("added", 0)),
            "trigger": self.trigger,
            "started": self.started,
            "stopping": self._stop,
            "updated": now,
        }
        try:
            pipe = self.redis.pipeline()
            pipe.set(K_PROGRESS, json.dumps(payload), ex=LOCK_TTL)
            pipe.expire(K_LOCK, LOCK_TTL)  # heartbeat: a crashed worker frees the lock by itself
            pipe.execute()
        except Exception:
            pass


def read_progress(redis):
    """Live progress for the dashboard: running, queued, or idle."""
    try:
        raw = _text(redis.get(K_PROGRESS))
        locked = bool(redis.exists(K_LOCK))
        queued = _text(redis.get(K_QUEUED))
        stopping = bool(redis.exists(K_STOP))
    except Exception as exc:
        return {"running": False, "error": f"Redis is not reachable: {exc}"}
    if raw and locked:
        try:
            progress = json.loads(raw)
            progress["stopping"] = stopping or progress.get("stopping", False)
            return progress
        except ValueError:
            pass
    if locked:
        return {"running": True, "phase": "Starting", "done": 0, "total": 0, "stopping": stopping}
    if queued:
        try:
            return {"running": False, "queued": True, "queued_for": max(0, time.time() - float(queued))}
        except ValueError:
            pass
    return {"running": False}


def request_stop(redis):
    if not redis.exists(K_LOCK):
        return False
    redis.set(K_STOP, "1", ex=3600)
    return True


def mark_queued(redis):
    redis.set(K_QUEUED, str(time.time()), ex=600)


def clear_queued(redis):
    redis.delete(K_QUEUED)


# ---- the sync process ---------------------------------------------------------

def python_executable():
    """The Python of Dispatcharr's environment.

    In the web process sys.executable is the uWSGI or Gunicorn binary, not Python.
    """
    import shutil
    import sys

    candidates = []
    if os.path.basename(sys.executable or "").startswith("python"):
        candidates.append(sys.executable)
    for prefix in (sys.prefix, os.environ.get("VIRTUAL_ENV", ""), "/dispatcharrpy"):
        if prefix:
            candidates += [os.path.join(prefix, "bin", "python3"), os.path.join(prefix, "bin", "python")]
    found = shutil.which("python3")
    if found:
        candidates.append(found)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("Could not find Dispatcharr's Python to start the sync")


def project_dir():
    """Dispatcharr's code folder (where manage.py is), for the sync process to import from."""
    try:
        from django.conf import settings

        return str(settings.BASE_DIR)
    except Exception:
        return "/app"


def spawn(job):
    """Starts runner.py with the job, detached, and returns at once.

    The shell starts the runner in the background and exits, so the runner is
    adopted by the container's init and nothing here waits for it. Its output
    goes to runner.log in the data folder, for when it fails before logging.
    """
    import subprocess

    runner = os.path.join(PLUGIN_DIR, "runner.py")
    out = os.path.join(data_dir(), "runner.log")
    try:
        if os.path.getsize(out) > 1_000_000:
            os.remove(out)
    except OSError:
        pass
    env = dict(os.environ)
    env.setdefault("DJANGO_SETTINGS_MODULE", "dispatcharr.settings")
    env["STRM_SYNC_DATA_DIR"] = data_dir()
    subprocess.run(
        ["sh", "-c", 'nohup "$0" "$@" >>"$STRM_SYNC_RUNNER_LOG" 2>&1 </dev/null &',
         python_executable(), runner, project_dir(), json.dumps(job)],
        cwd=project_dir(), env=dict(env, STRM_SYNC_RUNNER_LOG=out), check=True, timeout=30,
    )


# ---- dashboard page ------------------------------------------------------------

def logos_dir():
    try:
        from django.conf import settings

        for candidate in getattr(settings, "BACKUP_DATA_DIRS", []) or []:
            if os.path.basename(str(candidate).rstrip("/")) == "logos":
                return str(candidate)
    except Exception:
        pass
    return os.environ.get("LOGOS_DIR", "/data/logos")


DASHBOARD_URL = f"/logos/{PLUGIN_KEY}/index.html"


def install_dashboard():
    """Copies the dashboard page to where Dispatcharr's web server hands out static files.

    Dispatcharr has no page a plugin can add to, but its nginx serves /data/logos
    as /logos/. The page holds no data: it asks the plugin for everything through
    Dispatcharr's own signed-in API.
    """
    source = os.path.join(PLUGIN_DIR, "dashboard", "index.html")
    target_dir = os.path.join(logos_dir(), PLUGIN_KEY)
    target = os.path.join(target_dir, "index.html")
    try:
        with open(source, "r", encoding="utf-8") as fh:
            html = fh.read().replace("__PLUGIN_KEY__", PLUGIN_KEY)
        try:
            with open(target, "r", encoding="utf-8") as fh:
                if fh.read() == html:
                    return True
        except OSError:
            pass
        os.makedirs(target_dir, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(html)
        return True
    except OSError as exc:
        log.warning("Could not install the dashboard page at %s: %s", target, exc)
        return False


def remove_dashboard():
    target_dir = os.path.join(logos_dir(), PLUGIN_KEY)
    if os.path.isfile(os.path.join(target_dir, "index.html")):
        shutil.rmtree(target_dir, ignore_errors=True)


# ---- Emby ----------------------------------------------------------------------

def notify_emby(settings):
    """Asks Emby to scan its libraries. Returns a short outcome for the log."""
    if not (settings.emby_url and settings.emby_api_key):
        return ""
    import requests

    try:
        response = requests.post(
            f"{settings.emby_url}/emby/Library/Refresh",
            headers={"X-Emby-Token": settings.emby_api_key},
            timeout=15,
        )
        if response.status_code < 300:
            return "Emby library scan started"
        return f"Emby answered {response.status_code} to the library scan request"
    except Exception as exc:
        return f"Emby could not be reached for the library scan: {exc}"

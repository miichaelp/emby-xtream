"""Starting syncs: by hand, after Dispatcharr's VOD refresh, and on a schedule.

A sync runs as its own process (runner.py), not as a Celery task. Dispatcharr
only loads plugins in its Celery worker's child processes, so the parent that
receives tasks rejects a plugin's tasks as "unregistered". A separate process
also keeps a long sync out of Dispatcharr's web and worker processes.

What this module does need from Celery is a signal: after every task a worker
child runs, the hook at the bottom checks whether Dispatcharr just finished a
VOD refresh, and whether the schedule says a sync is due. Dispatcharr runs a
file scan every 20 seconds, so the hook gets a chance at least that often.
"""

import json
import os
import time
from datetime import datetime, timezone

from celery.signals import task_postrun

from . import core, runtime
from .engine import MOVIES_DIR, SHOWS_DIR, Engine, Settings

VOD_REFRESH_TASK = "apps.vod.tasks.refresh_vod_content"
# The beat entry version 0.2.0 created; its task can never be registered (see above).
OLD_TICK_NAME = "STRM Sync: schedule check"
SCHEDULE_CHECK_SECONDS = 60
ALL_KINDS = ("movies", "series")


def manifest():
    with open(os.path.join(runtime.PLUGIN_DIR, "plugin.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_settings():
    """(Settings, enabled). Saved values over the manifest defaults."""
    from apps.plugins.models import PluginConfig

    raw = {f["id"]: f.get("default") for f in manifest().get("fields", []) if f.get("type") != "info"}
    config = PluginConfig.objects.filter(key=runtime.PLUGIN_KEY).first()
    if config is None:
        return Settings(raw), False
    raw.update(config.settings or {})
    return Settings(raw), bool(config.enabled)


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _queue(job):
    runtime.mark_queued(runtime.redis_client())
    try:
        runtime.spawn(job)
    except Exception:
        runtime.clear_queued(runtime.redis_client())
        raise


def queue_sync(kinds=ALL_KINDS, trigger="manual", only=None, countdown=0):
    runtime.setup_logging().info("Starting a sync (%s): %s", trigger, ", ".join(kinds))
    _queue({"job": "sync", "kinds": list(kinds), "trigger": trigger, "only": only, "delay": countdown})


def queue_delete(kinds):
    _queue({"job": "delete", "kinds": list(kinds)})


def run_sync(kinds=None, trigger="manual", only=None):
    """One sync run. kinds: ["movies", "series"]; only: ids per kind for a retry."""
    log = runtime.setup_logging()
    redis = runtime.redis_client()
    runtime.clear_queued(redis)
    kinds = [k for k in (kinds or ALL_KINDS) if k in ALL_KINDS]

    settings, enabled = load_settings()
    if not enabled:
        return "STRM Sync is disabled"
    if trigger == "auto" and not settings.auto_sync:
        return "Sync after an M3U refresh is turned off"
    if trigger == "schedule" and settings.schedule == "off":
        return "The schedule is turned off"

    state = runtime.load_state()
    problems = settings.problems()
    if problems:
        log.warning("Sync not started: %s", " ".join(problems))
        state["last_error"] = " ".join(problems)
        runtime.save_state(state)
        return state["last_error"]

    control = runtime.Control(redis, trigger=trigger, kinds=kinds)
    if not control.acquire():
        if trigger == "auto":
            # A refresh finished while a sync was running: run once more afterwards.
            redis.set(runtime.K_RERUN, "1", ex=6 * 3600)
        log.info("A sync is already running; this %s request was not started", trigger)
        return "A sync is already running"

    started = time.time()
    result = None
    error = ""
    try:
        redis.delete(runtime.K_STOP)
        control.progress("", "Starting", 0, 0, force=True)
        os.makedirs(settings.output_dir, exist_ok=True)
        from .source import DispatcharrSource

        log.info("Sync started (%s): %s", trigger, ", ".join(kinds))
        engine = Engine(settings, DispatcharrSource(log), control, log)
        result = engine.run(state, kinds=kinds, only=only)
    except Exception as exc:
        error = str(exc)
        log.exception("Sync failed")
    finally:
        try:
            _finish(state, settings, control, result, error, kinds, trigger, only, started, log)
        finally:
            control.release()
            if redis.get(runtime.K_RERUN):
                redis.delete(runtime.K_RERUN)
                queue_sync(ALL_KINDS, trigger="auto", countdown=5)
    return error or "Sync finished"


def _finish(state, settings, control, result, error, kinds, trigger, only, started, log):
    result = result or {"movies": None, "series": None, "stopped": False, "failed_items": [], "warnings": []}
    changed = 0
    for kind in ALL_KINDS:
        stats = result.get(kind)
        if stats:
            changed += stats["files_added"] + stats["updated"] + stats["deleted"]

    emby = ""
    if changed and not error:
        emby = runtime.notify_emby(settings)
        if emby:
            log.info(emby)

    failed_count = len(result.get("failed_items", []))
    if error:
        message = f"Failed: {error}"
    elif result.get("stopped"):
        message = "Stopped. Files already written were kept and nothing was removed."
    else:
        message = "Completed" + (f" with {failed_count} failed item(s)" if failed_count else "")

    entry = {
        "start": _iso(started),
        "end": _iso(time.time()),
        "duration": round(time.time() - started, 1),
        "trigger": "retry" if only is not None else trigger,
        "kinds": kinds,
        "success": not error and not result.get("stopped"),
        "stopped": bool(result.get("stopped")),
        "message": message,
        "movies": result.get("movies"),
        "series": result.get("series"),
        "added_titles": result.get("added_titles", []),
        "emby": emby,
    }
    state["last_error"] = error
    runtime.record_run(state, entry, result, kinds, only)
    runtime.save_state(state)


def run_delete(kinds=None):
    """Removes every .strm this plugin wrote for the given kinds. Foreign files stay."""
    log = runtime.setup_logging()
    redis = runtime.redis_client()
    runtime.clear_queued(redis)
    settings, enabled = load_settings()
    if not enabled or not settings.output_dir or not settings.base_url:
        return "Nothing to delete"
    control = runtime.Control(redis, trigger="delete", kinds=kinds or [])
    if not control.acquire():
        return "A sync is running; stop it first"
    try:
        prefixes = core.owned_url_prefixes(settings.base_url)
        state = runtime.load_state()
        for kind, root_name in (("movies", MOVIES_DIR), ("series", SHOWS_DIR)):
            if kind not in (kinds or []):
                continue
            control.progress(kind, f"Deleting {kind}", 0, 0, force=True)
            root = os.path.join(settings.output_dir, root_name)
            outcome = core.delete_all_owned(root, prefixes)
            log.info("Deleted %d %s file(s) on request", outcome.deleted, kind)
            counts = state.setdefault("counts", {})
            if kind == "movies":
                counts.update({"movies": 0, "movie_files": 0})
            else:
                counts.update({"series": 0, "episodes": 0, "episode_files": 0})
            if kind == "series":
                # Episode lists stay in Dispatcharr; only our "seen" marks are reset.
                state["series_last_modified"] = {}
        runtime.save_state(state)
        runtime.notify_emby(settings)
    finally:
        control.release()
    return "Deleted"


def local_now():
    """Now, in the time zone set in Dispatcharr's system settings."""
    from zoneinfo import ZoneInfo

    from django.utils import timezone as dj_timezone

    try:
        from core.models import CoreSettings

        zone = ZoneInfo(CoreSettings.get_system_time_zone())
    except Exception:
        zone = dj_timezone.get_default_timezone()
    return dj_timezone.now().astimezone(zone)


def last_run_start(state):
    history = state.get("history") or []
    if not history:
        return None
    try:
        return datetime.fromisoformat(history[0]["start"])
    except (KeyError, TypeError, ValueError):
        return None


def remove_old_schedule_entry():
    """Deletes the beat entry 0.2.0 made, which only produced "unregistered task" errors."""
    from django_celery_beat.models import PeriodicTask

    return PeriodicTask.objects.filter(name=OLD_TICK_NAME).delete()[0]


def check_schedule():
    """Starts a scheduled sync when one is due. Returns what it did, for the log and tests."""
    from . import schedule

    settings, enabled = load_settings()
    if not enabled or settings.schedule == "off" or settings.problems():
        return "Not scheduled"
    state = runtime.load_state()
    before = json.dumps(state.get("schedule") or {}, sort_keys=True)
    sched = state.setdefault("schedule", {})
    due = schedule.check(settings.schedule, settings.schedule_hours, settings.schedule_time,
                         local_now(), sched, last_run_start(state))
    if json.dumps(sched, sort_keys=True) != before:
        runtime.save_state(state)
    if not due:
        return "Not due"
    if runtime.read_progress(runtime.redis_client()).get("running"):
        runtime.setup_logging().info("Scheduled sync skipped: a sync is already running")
        return "Already running"
    runtime.setup_logging().info("Scheduled sync starting")
    queue_sync(ALL_KINDS, trigger="schedule")
    return "Queued"


def next_scheduled_run(settings, state):
    from . import schedule

    if settings.schedule == "off":
        return None
    when = schedule.next_run(settings.schedule, settings.schedule_hours, settings.schedule_time,
                             local_now(), dict(state.get("schedule") or {}), last_run_start(state))
    return when.isoformat() if when else None


def _on_task_finished(sender=None, task=None, retval=None, **_kwargs):
    """Runs in a Celery worker child after every task.

    After Dispatcharr's VOD refresh (which an M3U account refresh ends by
    queueing, so the new movies and series are in the database) it starts a
    sync. After any task, at most once a minute across all workers, it checks
    the schedule.
    """
    name = getattr(task or sender, "name", "")
    if name != VOD_REFRESH_TASK:
        try:
            if runtime.redis_client().set(runtime.K_SCHEDULE_CHECK, "1", nx=True, ex=SCHEDULE_CHECK_SECONDS):
                check_schedule()
        except Exception:
            runtime.log.exception("Schedule check failed")
        return
    if isinstance(retval, str) and "failed" in retval.lower():
        return  # Dispatcharr reports a failed refresh through its return value
    try:
        settings, enabled = load_settings()
        if enabled and settings.auto_sync and not settings.problems():
            runtime.setup_logging().info("Dispatcharr finished a VOD refresh; queueing a sync")
            queue_sync(ALL_KINDS, trigger="auto", countdown=10)
    except Exception:
        runtime.log.exception("Could not queue a sync after the VOD refresh")


# A reload imports this module again; the uid keeps a single hook connected.
task_postrun.disconnect(dispatch_uid="strm_sync.vod_refresh")
task_postrun.connect(_on_task_finished, weak=False, dispatch_uid="strm_sync.vod_refresh")

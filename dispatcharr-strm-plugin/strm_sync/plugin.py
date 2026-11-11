"""STRM Sync: a Dispatcharr plugin that writes .strm files for movies and series.

This file is what Dispatcharr loads. It only answers button presses and
dashboard requests; the work happens in Celery (tasks.py).
"""

import os

from . import runtime
from . import tasks  # noqa: F401  registers the Celery tasks and the VOD-refresh hook

_MANIFEST = tasks.manifest()


class Plugin:
    name = _MANIFEST["name"]
    version = _MANIFEST["version"]
    description = _MANIFEST["description"]
    author = _MANIFEST.get("author", "")
    help_url = runtime.DASHBOARD_URL
    fields = _MANIFEST["fields"]
    actions = _MANIFEST["actions"]

    def __init__(self):
        runtime.setup_logging()
        runtime.install_dashboard()

    # ---- actions -----------------------------------------------------------------

    def run(self, action, params, context):
        params = params if isinstance(params, dict) else {}
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            return {"status": "error", "message": f"Unknown action: {action}"}
        try:
            return handler(params)
        except Exception as exc:
            runtime.log.exception("Action %s failed", action)
            return {"status": "error", "message": str(exc)}

    def stop(self, context):
        """Dispatcharr calls this when the plugin is disabled or removed."""
        try:
            runtime.request_stop(runtime.redis_client())
        except Exception:
            pass

    def _start(self, kinds, only=None):
        settings, _enabled = tasks.load_settings()
        problems = settings.problems()
        if problems:
            return {"status": "error", "message": " ".join(problems)}
        redis = runtime.redis_client()
        progress = runtime.read_progress(redis)
        if progress.get("running"):
            return {"status": "error", "message": "A sync is already running."}
        tasks.queue_sync(kinds, trigger="manual", only=only)
        return {"status": "queued", "message": f"Sync started. Follow it on the dashboard: {runtime.DASHBOARD_URL}"}

    def _do_sync_all(self, params):
        return self._start(tasks.ALL_KINDS)

    def _do_sync_movies(self, params):
        return self._start(["movies"])

    def _do_sync_series(self, params):
        return self._start(["series"])

    def _do_stop(self, params):
        if runtime.request_stop(runtime.redis_client()):
            return {"status": "ok", "message": "Stopping. The title being written finishes first."}
        return {"status": "ok", "message": "No sync is running."}

    def _do_retry_failed(self, params):
        failed = runtime.load_state().get("failed", [])
        if not failed:
            return {"status": "ok", "message": "There are no failed items to retry."}
        only = {
            "movies": sorted({f["id"] for f in failed if f.get("kind") == "movie" and f.get("id") is not None}),
            "series": sorted({f["id"] for f in failed if f.get("kind") == "series" and f.get("id") is not None}),
        }
        return self._start(tasks.ALL_KINDS, only=only)

    def _delete(self, kind):
        redis = runtime.redis_client()
        if runtime.read_progress(redis).get("running"):
            return {"status": "error", "message": "A sync is running. Stop it first."}
        tasks.queue_delete([kind])
        return {"status": "queued", "message": f"Deleting {kind} files in the background."}

    def _do_delete_movies(self, params):
        return self._delete("movies")

    def _do_delete_series(self, params):
        return self._delete("series")

    def _do_status(self, params):
        data = self._do_dashboard(params)
        progress = data["progress"]
        if progress.get("running"):
            message = f"{progress.get('phase', 'Working')}: {progress.get('done', 0)} of {progress.get('total', 0)}"
        elif progress.get("queued"):
            message = "Waiting for a worker to pick the sync up."
        elif data["history"]:
            last = data["history"][0]
            message = f"Last sync {last['end']}: {last['message']}"
        else:
            message = "No sync has run yet."
        if data["problems"]:
            message += " " + " ".join(data["problems"])
        return {"status": "ok", "message": message}

    # ---- dashboard (called by the page, not shown as buttons) ---------------------

    def _do_dashboard(self, params):
        settings, enabled = tasks.load_settings()
        state = runtime.load_state()
        return {
            "status": "ok",
            "version": self.version,
            "enabled": enabled,
            "problems": settings.problems(),
            "progress": runtime.read_progress(runtime.redis_client()),
            "history": state.get("history", []),
            "failed": state.get("failed", []),
            "counts": state.get("counts", {}),
            "warnings": state.get("warnings", []),
            "removed": state.get("removed", []),
            "last_error": state.get("last_error", ""),
            "settings": {
                "output_dir": settings.output_dir,
                "base_url": settings.base_url,
                "sync_movies": settings.sync_movies,
                "sync_series": settings.sync_series,
                "auto_sync": settings.auto_sync,
                "url_style": settings.url_style,
                "multi_version": settings.multi_version,
                "cleanup_orphans": settings.cleanup_orphans,
                "safety_limit": int(round(settings.safety_limit * 100)),
                "fetch_episodes": settings.fetch_episodes,
                "emby": bool(settings.emby_url and settings.emby_api_key),
                "output_dir_exists": bool(settings.output_dir) and os.path.isdir(settings.output_dir),
            },
        }

    def _do_log(self, params):
        settings, _enabled = tasks.load_settings()
        text = runtime.read_log(secrets=(settings.xc_password, settings.emby_api_key))
        return {"status": "ok", "log": text}


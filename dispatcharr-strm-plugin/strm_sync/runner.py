"""Runs one sync (or delete) in its own process. Started by runtime.spawn().

    python runner.py <dispatcharr project dir> '<job json>'

It sets up Django with Dispatcharr's settings, loads this plugin folder as a
package under a private name (the folder name need not be a valid module
name), and calls the job in tasks.py.
"""

import importlib.util
import json
import os
import sys
import time

PACKAGE = "_strm_sync_runner"


def load_plugin_package():
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        PACKAGE, os.path.join(plugin_dir, "__init__.py"), submodule_search_locations=[plugin_dir])
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = package
    # There is no __init__.py: an empty package with the folder as its path is all that is needed.
    return package


def log_to_container(log):
    """Also writes the sync's log lines to the container's own output.

    This process is detached, so its stdout goes to runner.log. The container's
    output is PID 1's stdout; writing there makes the sync show up in
    `docker logs` and Dispatcharr's log view next to everything else.
    """
    import logging

    try:
        stream = open("/proc/1/fd/1", "a", buffering=1, encoding="utf-8")
    except OSError:
        return
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s STRM Sync: %(message)s"))
    log.addHandler(handler)
    log.propagate = False  # Django's console logging would print the same line to runner.log again


def main(argv):
    project_dir, job = argv[1], json.loads(argv[2])
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dispatcharr.settings")

    # Dispatcharr reads the command line to decide what kind of process this is
    # (dispatcharr/db/process_label.py, dispatcharr/app_initialization.py). As a
    # background job this must count as a Celery worker: the standard database
    # backend rather than the gevent pool of the web process, and none of the
    # start-up work that only the main web process does.
    sys.argv = ["celery", "strm-sync-runner"]

    import django

    django.setup()

    load_plugin_package()
    runtime = importlib.import_module(PACKAGE + ".runtime")
    log_to_container(runtime.setup_logging())
    tasks = importlib.import_module(PACKAGE + ".tasks")
    delay = float(job.get("delay") or 0)
    if delay > 0:
        time.sleep(delay)
    if job.get("job") == "delete":
        outcome = tasks.run_delete(kinds=job.get("kinds"))
    else:
        outcome = tasks.run_sync(kinds=job.get("kinds"), trigger=job.get("trigger", "manual"), only=job.get("only"))
    print(time.strftime("%Y-%m-%d %H:%M:%S"), job.get("job", "sync"), "->", outcome, flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        import traceback

        print(time.strftime("%Y-%m-%d %H:%M:%S"), "runner failed:", flush=True)
        traceback.print_exc()
        # Whatever happened, a "queued" mark must not stay on the dashboard.
        try:
            from core.utils import RedisClient

            RedisClient.get_client().delete("strm_sync:queued")
        except Exception:
            pass
        sys.exit(1)

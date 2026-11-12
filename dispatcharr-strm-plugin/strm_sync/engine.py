"""The sync itself: turns Dispatcharr's VOD catalogue into .strm files.

It reads plain dicts from a "source" and reports through a "control" object,
so it has no Django imports and runs in tests against fakes. See source.py for
the real catalogue and runtime.py for the real control.
"""

import os
import time

from . import core, schedule

MOVIES_DIR = "Movies"
SHOWS_DIR = "Shows"


class SettingsError(ValueError):
    pass


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_number(value, default, minimum=None, maximum=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


class Settings:
    """Plugin settings, read once per run and checked before anything is written."""

    def __init__(self, raw):
        raw = raw or {}
        self.output_dir = str(raw.get("output_dir") or "").strip().rstrip("/") or ("/" if str(raw.get("output_dir") or "").strip() else "")
        self.base_url = core.normalize_base_url(raw.get("base_url"))
        self.sync_movies = _as_bool(raw.get("sync_movies"), True)
        self.sync_series = _as_bool(raw.get("sync_series"), True)
        self.auto_sync = _as_bool(raw.get("auto_sync"), True)
        self.schedule = str(raw.get("schedule") or "off").strip().lower()
        if self.schedule not in ("off", "interval", "daily"):
            self.schedule = "off"
        self.schedule_hours = _as_number(raw.get("schedule_hours"), 24, 0)
        self.schedule_time = str(raw.get("schedule_time") or "03:00").strip()
        self.url_style = str(raw.get("url_style") or "proxy").strip().lower()
        self.xc_username = str(raw.get("xc_username") or "").strip()
        self.xc_password = str(raw.get("xc_password") or "").strip()
        self.multi_version = _as_bool(raw.get("multi_version"), True)
        self.version_label = str(raw.get("version_label") or "provider").strip().lower()
        if self.version_label not in ("provider", "number", "folder"):
            self.version_label = "provider"
        self.episode_titles = _as_bool(raw.get("episode_titles"), True)
        self.movie_folder_mode = str(raw.get("movie_folder_mode") or "single").strip().lower()
        self.series_folder_mode = str(raw.get("series_folder_mode") or "single").strip().lower()
        self.tmdb_folder_ids = _as_bool(raw.get("tmdb_folder_ids"), True)
        self.add_year = _as_bool(raw.get("add_year"), True)
        self.write_nfo = _as_bool(raw.get("write_nfo"), False)
        self.clean_names = _as_bool(raw.get("clean_names"), True)
        self.remove_terms = core.parse_terms(raw.get("remove_terms"))
        self.excluded_categories = core.parse_terms(raw.get("excluded_categories"))
        self.fetch_episodes = _as_bool(raw.get("fetch_episodes"), True)
        self.episode_fetch_limit = int(_as_number(raw.get("episode_fetch_limit"), 0, 0))
        self.episode_fetch_delay_ms = int(_as_number(raw.get("episode_fetch_delay_ms"), 500, 0, 60000))
        self.cleanup_orphans = _as_bool(raw.get("cleanup_orphans"), True)
        self.safety_limit = _as_number(raw.get("safety_limit"), 20, 0, 100) / 100.0
        self.emby_url = core.normalize_base_url(raw.get("emby_url"))
        self.emby_api_key = str(raw.get("emby_api_key") or "").strip()

    def problems(self):
        """Reasons a sync cannot start, in the user's words. Empty when it can."""
        out = []
        if not self.output_dir:
            out.append("Set the STRM folder in the plugin settings.")
        elif not os.path.isabs(self.output_dir):
            out.append("The STRM folder must be a full path inside the Dispatcharr container, such as /strm.")
        elif self.output_dir == "/":
            out.append("The STRM folder cannot be the container's top folder. Use the path from your volume line, such as /strm.")
        if not self.base_url:
            out.append("Set the Dispatcharr address that Emby uses, such as http://192.168.0.11:9191.")
        elif not self.base_url.lower().startswith(("http://", "https://")):
            out.append("The Dispatcharr address must start with http:// or https://.")
        if self.url_style == "xtream" and not (self.xc_username and self.xc_password):
            out.append("Xtream-style URLs need the Dispatcharr user name and its Xtream password.")
        if self.schedule == "interval" and self.schedule_hours < 1:
            out.append("The schedule needs at least 1 hour between syncs.")
        if self.schedule == "daily" and not schedule.parse_time(self.schedule_time):
            out.append("The daily sync time must be a 24-hour time such as 03:00.")
        if not (self.sync_movies or self.sync_series):
            out.append("Movies and Series are both turned off, so there is nothing to sync.")
        return out


def new_stats():
    return {
        "total": 0,       # titles seen
        "added": 0,       # titles with at least one new file
        "updated": 0,     # titles whose files changed
        "unchanged": 0,
        "skipped": 0,     # no usable name, excluded category, or a duplicate folder
        "failed": 0,
        "deleted": 0,     # orphaned files removed
        "files": 0,       # .strm files now in place for this kind
        "files_added": 0,
    }


def _duration(seconds):
    seconds = int(max(seconds, 0))
    if seconds < 90:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def _from(item):
    """" (series 1234)": the provider's id for this copy of the show.

    One account can list the same show in several categories, each with its own
    id; that is what a second fetch of the same show is.
    """
    external = item.get("external_id")
    return f" (series {external})" if external else ""


def _nth_try(tries):
    if tries <= 1:
        return ""
    suffix = {2: "nd", 3: "rd"}.get(tries, "th")
    return f" ({tries}{suffix} try)"


class Engine:
    def __init__(self, settings, source, control, log, sleep=time.sleep):
        self.s = settings
        self.source = source
        self.control = control
        self.log = log
        self.sleep = sleep
        self.failed_items = []
        self.warnings = []
        self.removed = []
        self.added_titles = []
        self.stopped = False
        self.counters_fetched = 0
        self.last_fetch_seconds = 0.0
        self.known_dirs = set()
        self.written_urls = set()
        self.gap = self.s.episode_fetch_delay_ms / 1000.0
        self._last_request = None
        self.clock = time.monotonic
        self._beat_label = None
        self._beat_start = self._beat_last = 0.0

    # ---- helpers -----------------------------------------------------------------

    def _stop_requested(self):
        if not self.stopped and self.control.should_stop():
            self.stopped = True
            self.log.info("Stop requested: finishing the current title, then stopping")
        return self.stopped

    def _title(self, item):
        name = core.clean_name(item.get("name"), self.s.remove_terms, self.s.clean_names)
        if self.s.add_year:
            name = core.with_year(name, item.get("year"))
        return core.with_language(name, item.get("language"))

    def _subfolder(self, root_name, mode, category):
        if mode == "category":
            cat = core.sanitize_file_name(core.clean_name(category, None, self.s.clean_names)) or "Uncategorized"
            return os.path.join(self.s.output_dir, root_name, cat)
        return os.path.join(self.s.output_dir, root_name)

    def _urls(self, content_type, xc_path, item_id, uuid, versions):
        """(suffix, url) per file for one movie or episode."""
        if not versions:
            return []
        first = versions[0]
        many = self.s.multi_version and len(versions) > 1
        if self.s.url_style == "xtream":
            first_url = core.xtream_url(
                self.s.base_url, xc_path, self.s.xc_username, self.s.xc_password, item_id, first.get("ext"))
        else:
            # Without stream_id Dispatcharr picks the best provider and can fail over.
            first_url = core.proxy_url(
                self.s.base_url, content_type, uuid, first.get("stream_id") if many else None)
        if not many:
            return [("", first_url)]
        mode = self.s.version_label
        labels = [v.get("folder") if mode == "folder" else v.get("provider") for v in versions]
        suffixes = core.version_entries(labels, mode)
        out = [(suffixes[0], first_url)]
        for suffix, version in list(zip(suffixes, versions))[1:]:
            out.append((suffix, core.proxy_url(self.s.base_url, content_type, uuid, version.get("stream_id"))))
        return out

    def _write_files(self, directory, base, urls, written, stats):
        """Writes each version. Returns "added", "updated" or "same" for the title."""
        outcome = "same"
        for suffix, url in urls:
            path = os.path.join(directory, f"{base}{suffix}.strm")
            result = core.write_if_changed(path, url, self.known_dirs)
            written.add(path)
            self.written_urls.add(url)
            stats["files"] += 1
            if result == "added":
                stats["files_added"] += 1
                outcome = "added"
            elif result == "updated" and outcome != "added":
                outcome = "updated"
        return outcome

    def _heartbeat(self, label, stats, total, every=30.0):
        """A progress line in the log every 30 seconds, with speed and time left."""
        now = self.clock()
        if self._beat_label != label:
            self._beat_label, self._beat_start, self._beat_last = label, now, now
            return
        if now - self._beat_last < every or not stats["total"]:
            return
        self._beat_last = now
        rate = stats["total"] / max(now - self._beat_start, 0.001)
        left = (total - stats["total"]) / rate if rate > 0 else 0
        self.log.info(
            "%s: %d of %d done (%d new, %d changed, %d unchanged, %d skipped, %d failed), "
            "%.1f per second, about %s left",
            label, stats["total"], total, stats["added"], stats["updated"], stats["unchanged"],
            stats["skipped"], stats["failed"], rate, _duration(left))

    def _count(self, stats, outcome):
        stats[{"added": "added", "updated": "updated", "same": "unchanged"}[outcome]] += 1

    def _fail(self, kind, item, error, stats):
        stats["failed"] += 1
        self.failed_items.append({
            "kind": kind,
            "id": item.get("id"),
            "name": item.get("name") or "",
            "error": str(error)[:300],
        })
        self.log.error("Failed to write %s '%s' (id=%s): %s", kind, item.get("name"), item.get("id"), error)

    # ---- movies ------------------------------------------------------------------

    def sync_movies(self, only_ids=None):
        stats = new_stats()
        written, nfos, used = set(), set(), {}
        total = self.source.count_movies(only_ids)
        self.control.progress("movies", "Writing movies", 0, total, stats)
        self.log.info("Movie sync started: %d movies in Dispatcharr", total)
        complete = True
        try:
            for movie in self.source.iter_movies(only_ids):
                if self._stop_requested():
                    break
                stats["total"] += 1
                try:
                    self._write_movie(movie, written, nfos, used, stats)
                except Exception as exc:  # one bad title must not end the run
                    self._fail("movie", movie, exc, stats)
                self.control.progress("movies", "Writing movies", stats["total"], total, stats)
                self._heartbeat("Movies", stats, total)
        except Exception as exc:
            complete = False
            self.warnings.append(f"Reading movies from Dispatcharr failed: {exc}")
            self.log.exception("Reading movies from Dispatcharr failed")

        self._cleanup("movies", MOVIES_DIR, written, nfos, stats, complete and only_ids is None)
        self.log.info(
            "Movie sync %s: %d movies, %d added, %d updated, %d unchanged, %d skipped, %d failed, %d files removed",
            "stopped" if self.stopped else "finished", stats["total"], stats["added"], stats["updated"],
            stats["unchanged"], stats["skipped"], stats["failed"], stats["deleted"])
        return stats

    def _write_movie(self, movie, written, nfos, used, stats):
        if core.category_excluded(movie.get("category"), self.s.excluded_categories) or not movie.get("versions"):
            stats["skipped"] += 1
            return
        title = self._title(movie)
        folder = core.folder_name(title, movie.get("tmdb_id"), self.s.tmdb_folder_ids)
        if not folder:
            stats["skipped"] += 1
            return
        directory = os.path.join(
            self._subfolder(MOVIES_DIR, self.s.movie_folder_mode, movie.get("category")), folder)
        key = directory.lower()  # the share may ignore case
        if used.setdefault(key, movie["id"]) != movie["id"]:
            # Two different entries with the same cleaned title: the first keeps the folder.
            stats["skipped"] += 1
            self.log.debug("Movie '%s' (id=%s) shares a folder with id=%s; skipped",
                           movie.get("name"), movie["id"], used[key])
            return

        urls = self._urls("movie", "movie", movie["id"], movie["uuid"], movie["versions"])
        outcome = self._write_files(directory, folder, urls, written, stats)
        if self.s.write_nfo and core.valid_tmdb_id(movie.get("tmdb_id")):
            nfo = os.path.join(directory, folder + ".nfo")
            if not os.path.exists(nfo):
                core.write_if_changed(nfo, core.movie_nfo(title, movie["tmdb_id"], movie.get("year")))
            nfos.add(nfo)
        self._count(stats, outcome)
        if outcome == "added" and len(self.added_titles) < 20:
            self.added_titles.append(title)
        if outcome != "same":
            self.log.info("%s movie: %s (%d file%s)", "Added" if outcome == "added" else "Updated",
                          title, len(urls), "" if len(urls) == 1 else "s")

    # ---- series ------------------------------------------------------------------

    def _pending_episode_lists(self, state, only_ids):
        """{series id: [relations to fetch]} for shows whose episode list has to come from the provider.

        Dispatcharr only loads a show's episodes when someone opens it, so a fresh
        catalogue has shows without episodes.
        """
        if not self.s.fetch_episodes:
            return {}
        known = state.setdefault("series_last_modified", {})
        pending = self.source.series_needing_episodes(known, only_ids)
        if self.s.episode_fetch_limit and len(pending) > self.s.episode_fetch_limit:
            self.warnings.append(
                f"{len(pending)} shows need their episode list; fetching {self.s.episode_fetch_limit} this run "
                "(the limit in the plugin settings). The rest follow on later syncs.")
            pending = pending[: self.s.episode_fetch_limit]
        by_series = {}
        for item in pending:
            by_series.setdefault(item["series_id"], []).append(item)
        if by_series:
            self.log.info("%d shows need their episode list from the provider; each is fetched just "
                          "before it is written", len(by_series))
        return by_series

    # Waits before each further try. Refusals (HTTP 403 or 429) get five more tries in
    # quick succession: a real provider refused about 45% of all requests at random,
    # whatever the time since the previous request or the last answered one (under 1.5 s
    # and over 20 s alike), and answered the same request when asked again. Waiting longer
    # did not help, so the waits are short and the number of tries is what counts.
    RETRY_WAITS_LIMITED = (1.0, 2.0, 3.0, 5.0, 8.0)
    RETRY_WAITS_OTHER = (10.0,)

    @staticmethod
    def _limited(reason):
        return "HTTP 403" in reason or "HTTP 429" in reason

    def _wait_turn(self):
        """Keeps at least the configured time between provider requests.

        Counted from the end of the last request, so the time spent writing
        files in between already counts towards it.
        """
        if self._last_request is None or self.gap <= 0:
            return
        wait = self.gap - (self.clock() - self._last_request)
        if wait > 0:
            self.sleep(wait)

    def _fetch_lists(self, items, known, counters):
        """Fetches one show's episode lists. True when at least one provider answered."""
        answered = False
        for item in items:
            self._wait_turn()
            self.counters_fetched += 1
            status, reason = self._fetch_one(item)
            waits = ()
            if status == "error":
                waits = self.RETRY_WAITS_LIMITED if self._limited(reason) else self.RETRY_WAITS_OTHER
            tries = 1
            for attempt, wait in enumerate(waits, 2):
                if status != "error" or self._stop_requested():
                    break
                tries = attempt
                self.log.debug("Episode list of '%s'%s failed (%s); try %d in %d s",
                               item.get("name"), _from(item), reason, attempt, int(wait))
                self.sleep(wait)
                status, reason = self._fetch_one(item)
            if status == "ok":
                answered = True
                known[str(item["relation_id"])] = item.get("last_modified") or ""
                self.log.info("Fetched the episode list of '%s'%s in %.1f s%s",
                              item.get("name"), _from(item), self.last_fetch_seconds, _nth_try(tries))
            elif status == "empty":
                # Not a failure: the provider lists the show without episodes. Dispatcharr asks
                # again after a day, and so do we.
                self.log.info("No episodes yet for '%s'%s", item.get("name"), _from(item))
            else:
                counters["lists_failed"] += 1
                self.log.warning("Episode list of '%s'%s failed after %d tries: %s",
                                 item.get("name"), _from(item), tries, reason)
                self.failed_items.append({
                    "kind": "series",
                    "id": item.get("series_id"),
                    "name": item.get("name") or "",
                    "error": reason,
                })
        return answered

    def _fetch_one(self, item):
        started = self.clock()
        try:
            outcome = self.source.fetch_episodes(item)
        except Exception as exc:
            return "error", str(exc)[:200] or exc.__class__.__name__
        finally:
            self._last_request = self.clock()
            self.last_fetch_seconds = self._last_request - started
        if outcome is True:
            return "ok", ""
        if outcome is False or outcome is None:
            return "error", "The provider did not answer"
        return outcome

    def sync_series(self, state, only_ids=None):
        stats = new_stats()
        episodes = {"total": 0, "files": 0, "files_added": 0}
        written, nfos, used = set(), set(), {}
        counters = {"lists_failed": 0}
        self.counters_fetched = 0

        pending = self._pending_episode_lists(state, only_ids)
        known = state.setdefault("series_last_modified", {})
        total = self.source.count_series(only_ids)
        self.control.progress("series", "Writing series", 0, total, stats, force=True)
        self.log.info("Series sync started: %d shows in Dispatcharr", total)
        complete = True
        try:
            if not self.stopped:
                for show in self.source.iter_series(only_ids):
                    if self._stop_requested():
                        break
                    stats["total"] += 1
                    try:
                        items = pending.pop(show["id"], None)
                        if items:
                            self.control.progress(
                                "series", f"Fetching episodes: {show.get('name')}", stats["total"] - 1, total, stats)
                            if self._fetch_lists(items, known, counters):
                                # Read the show again: its episodes were added just now.
                                show = next(iter(self.source.iter_series([show["id"]])), show)
                        self._write_show(show, written, nfos, used, stats, episodes)
                    except Exception as exc:
                        self._fail("series", show, exc, stats)
                    self.control.progress("series", "Writing series", stats["total"], total, stats)
                    self._heartbeat("Series", stats, total)
        except Exception as exc:
            complete = False
            self.warnings.append(f"Reading series from Dispatcharr failed: {exc}")
            self.log.exception("Reading series from Dispatcharr failed")

        if self.counters_fetched:
            self.log.info("Episode lists: %d asked for, %d without an answer",
                          self.counters_fetched, counters["lists_failed"])
        self._cleanup("series", SHOWS_DIR, written, nfos, stats, complete and only_ids is None)
        stats["episodes"] = episodes["total"]
        self.log.info(
            "Series sync %s: %d shows, %d episodes, %d added, %d updated, %d unchanged, %d skipped, "
            "%d failed, %d files removed",
            "stopped" if self.stopped else "finished", stats["total"], episodes["total"], stats["added"],
            stats["updated"], stats["unchanged"], stats["skipped"], stats["failed"], stats["deleted"])
        return stats

    def _write_show(self, show, written, nfos, used, stats, episodes):
        if core.category_excluded(show.get("category"), self.s.excluded_categories) or not show.get("episodes"):
            stats["skipped"] += 1
            return
        title = self._title(show)
        series_name = core.sanitize_file_name(title)
        folder = core.folder_name(title, show.get("tmdb_id"), self.s.tmdb_folder_ids)
        if not folder:
            stats["skipped"] += 1
            return
        directory = os.path.join(
            self._subfolder(SHOWS_DIR, self.s.series_folder_mode, show.get("category")), folder)
        key = directory.lower()  # the share may ignore case
        if used.setdefault(key, show["id"]) != show["id"]:
            stats["skipped"] += 1
            self.log.debug("Show '%s' (id=%s) shares a folder with id=%s; skipped",
                           show.get("name"), show["id"], used[key])
            return

        outcome = "same"
        seen_codes = set()
        changed_episodes = 0
        for ep in show["episodes"]:
            if not ep.get("versions"):
                continue
            season = ep.get("season")
            season = 1 if season is None or season < 0 else int(season)
            number = ep.get("episode")
            number = 0 if number is None or number < 0 else int(number)
            if (season, number) in seen_codes:
                continue
            seen_codes.add((season, number))
            base = core.episode_file_base(
                series_name, season, number, ep.get("title") if self.s.episode_titles else "")
            season_dir = os.path.join(directory, f"Season {season:02d}")
            urls = self._urls("episode", "series", ep["id"], ep["uuid"], ep["versions"])
            before = stats["files_added"]
            result = self._write_files(season_dir, base, urls, written, stats)
            episodes["total"] += 1
            episodes["files_added"] += stats["files_added"] - before
            if result != "same":
                changed_episodes += 1
            if result == "added":
                outcome = "added"
            elif result == "updated" and outcome != "added":
                outcome = "updated"

        if not seen_codes:
            stats["skipped"] += 1
            return
        if self.s.write_nfo and core.valid_tmdb_id(show.get("tmdb_id")):
            nfo = os.path.join(directory, "tvshow.nfo")
            if not os.path.exists(nfo):
                core.write_if_changed(nfo, core.show_nfo(title, show["tmdb_id"]))
            nfos.add(nfo)
        self._count(stats, outcome)
        if outcome == "added" and len(self.added_titles) < 20:
            self.added_titles.append(title)
        if outcome != "same":
            self.log.info("%s show: %s (%d of %d episodes new or changed)",
                          "Added" if outcome == "added" else "Updated", title, changed_episodes, len(seen_codes))

    # ---- cleanup -----------------------------------------------------------------

    def _cleanup(self, kind, root_name, written, nfos, stats, catalogue_complete):
        """Remove files for titles Dispatcharr no longer has. Only after a run that saw everything."""
        if not self.s.cleanup_orphans:
            return
        reason = ""
        if self.stopped:
            reason = "the sync was stopped"
        elif not catalogue_complete:
            reason = "the run did not cover the whole catalogue"
        elif stats["failed"]:
            reason = f"{stats['failed']} title(s) failed to write"
        root = os.path.join(self.s.output_dir, root_name)
        prefixes = core.owned_url_prefixes(self.s.base_url)
        if reason:
            self.log.info("Removing files of titles no longer available skipped: %s", reason)
            # Old names of renamed files are still safe to remove: their streams were just
            # written under the new names.
            if written:
                result = core.cleanup_orphans(root, written, prefixes, 0, nfos, self.written_urls, renames_only=True)
                stats["deleted"] = result.deleted
                if result.renamed:
                    self.log.info("Removed %d old %s file name(s) left by a rename: the same streams are on "
                                  "disk under their new names", result.renamed, kind)
            return

        self.control.progress(kind, "Removing files for titles no longer available", stats["total"], stats["total"], stats)
        result = core.cleanup_orphans(root, written, prefixes, self.s.safety_limit, nfos, self.written_urls)
        if result.renamed:
            self.log.info("Removed %d old %s file name(s) left by a rename: the same streams are on disk "
                          "under their new names", result.renamed, kind)
        stats["deleted"] = result.deleted
        if result.skipped_reason and result.orphans:
            label = "movie" if kind == "movies" else "series"
            message = f"Old {label} files of titles no longer available were not removed: {result.skipped_reason}."
            self.warnings.append(message)
            self.log.warning(message)
        if result.deleted:
            self.removed.extend(result.removed_titles)
            self.log.info("Removed %d old %s file(s): %s%s", result.deleted, kind,
                          "; ".join(result.removed_titles[:25]),
                          " ..." if result.deleted > 25 else "")

    # ---- entry point -------------------------------------------------------------

    def run(self, state, kinds=("movies", "series"), only=None):
        """Runs the requested kinds. only: {"movies": [ids], "series": [ids]} for a retry."""
        started = time.time()
        result = {"movies": None, "series": None}
        if "movies" in kinds and self.s.sync_movies and not self.stopped:
            ids = (only or {}).get("movies") if only is not None else None
            if only is None or ids:
                result["movies"] = self.sync_movies(ids)
        if "series" in kinds and self.s.sync_series and not self._stop_requested():
            ids = (only or {}).get("series") if only is not None else None
            if only is None or ids:
                result["series"] = self.sync_series(state, ids)
        result.update({
            "stopped": self.stopped,
            "failed_items": self.failed_items,
            "warnings": self.warnings,
            "removed": self.removed[:200],
            "added_titles": self.added_titles,
            "duration": round(time.time() - started, 1),
        })
        return result

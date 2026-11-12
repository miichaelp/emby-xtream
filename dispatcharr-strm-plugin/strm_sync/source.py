"""Reads Dispatcharr's VOD catalogue straight from its database.

Django is imported inside the methods so the module can be loaded without it
(tests, and Dispatcharr's own manifest-only discovery).

What counts as "in the catalogue" follows Dispatcharr's own Xtream output:
content on an active M3U account, best provider first (highest account
priority). On top of that, content whose VOD category is switched off for its
account is left out.
"""

from datetime import timedelta
from itertools import groupby

CHUNK = 2000


class capture_errors:
    """Collects the ERROR messages the given loggers emit while the block runs."""

    def __init__(self, names):
        import logging

        self.names = names
        self.messages = []
        outer = self

        class _Handler(logging.Handler):
            def emit(self, record):
                try:
                    outer.messages.append(record.getMessage())
                except Exception:
                    pass

        self.handler = _Handler(level=logging.ERROR)

    def __enter__(self):
        import logging

        for name in self.names:
            logging.getLogger(name).addHandler(self.handler)
        return self.messages

    def __exit__(self, *_exc):
        import logging

        for name in self.names:
            logging.getLogger(name).removeHandler(self.handler)
        return False


def friendly_error(message):
    """The provider error Dispatcharr logged, in the user's words."""
    import re

    # requests puts the full URL, with the account's user name and password, in its messages.
    text = re.sub(r"\s*for url:\s*\S+", "", (message or "").strip())
    text = re.sub(r"https?://\S+", "<provider address>", text)
    low = text.lower()
    if not text:
        return "The provider did not answer"
    if "timed out" in low or "timeout" in low:
        return "The provider did not answer within 60 seconds"
    if "max retries exceeded" in low or "connection" in low and ("refused" in low or "reset" in low or "aborted" in low):
        return "Could not connect to the provider"
    for code in ("401", "403", "404", "429", "500", "502", "503", "504", "512", "513", "884"):
        if f" {code} " in f" {low} " or f"{code} client error" in low or f"{code} server error" in low:
            if code == "429":
                return "The provider is limiting requests (HTTP 429): try a longer pause between requests"
            return f"The provider answered with an error (HTTP {code})"
    if "invalid series info" in low or "json" in low:
        return "The provider sent an answer that is not a valid episode list"
    # Dispatcharr's messages start with its own context; keep the end, which names the cause.
    return text.split(": ", 2)[-1][:200]


class DispatcharrSource:
    def __init__(self, log):
        self.log = log
        self._disabled = None
        self._series_index = None

    # ---- shared ------------------------------------------------------------------

    def _disabled_categories(self):
        """(account id, category id) pairs the user switched off in Dispatcharr."""
        if self._disabled is None:
            from apps.vod.models import M3UVODCategoryRelation

            self._disabled = set(
                M3UVODCategoryRelation.objects.filter(enabled=False).values_list("m3u_account_id", "category_id"))
        return self._disabled

    def _allowed(self, account_id, category_id):
        return category_id is None or (account_id, category_id) not in self._disabled_categories()

    @staticmethod
    def _versions(rows):
        """One entry per provider stream, best first, without repeats of a stream id."""
        seen, out = set(), []
        for row in rows:
            stream_id = str(row["stream_id"] or "")
            if not stream_id or stream_id in seen:
                continue
            seen.add(stream_id)
            out.append({
                "stream_id": stream_id,
                "ext": row["container_extension"] or "mp4",
                "provider": row["m3u_account__name"] or "",
                "folder": row.get("folder") or "",
                "account_id": row["m3u_account_id"],
            })
        return out

    # ---- movies ------------------------------------------------------------------

    def _movie_relations(self, only_ids):
        from apps.vod.models import M3UMovieRelation

        qs = M3UMovieRelation.objects.filter(m3u_account__is_active=True)
        if only_ids is not None:
            qs = qs.filter(movie_id__in=list(only_ids))
        return qs

    def count_movies(self, only_ids=None):
        return self._movie_relations(only_ids).values("movie_id").distinct().count()

    def iter_movies(self, only_ids=None):
        rows = (
            self._movie_relations(only_ids)
            .order_by("movie_id", "-m3u_account__priority", "id")
            .values(
                "movie_id", "movie__uuid", "movie__name", "movie__year", "movie__tmdb_id", "movie__language",
                "category_id", "category__name", "m3u_account_id", "m3u_account__name",
                "stream_id", "container_extension",
            )
            .iterator(chunk_size=CHUNK)
        )
        rows = (dict(r, folder=r["category__name"]) for r in rows)
        for movie_id, group in groupby(rows, key=lambda r: r["movie_id"]):
            group = [r for r in group if self._allowed(r["m3u_account_id"], r["category_id"])]
            if not group:
                # Every provider's category for it is switched off: yield it without
                # versions so it is counted and skipped, and its old files are removed.
                yield {"id": movie_id, "name": "", "versions": []}
                continue
            first = group[0]
            yield {
                "id": movie_id,
                "uuid": str(first["movie__uuid"]),
                "name": first["movie__name"],
                "year": first["movie__year"],
                "tmdb_id": first["movie__tmdb_id"],
                "language": first["movie__language"],
                "category": first["category__name"],
                "versions": self._versions(group),
            }

    # ---- series ------------------------------------------------------------------

    def _series_relations(self, only_ids):
        from apps.vod.models import M3USeriesRelation

        qs = M3USeriesRelation.objects.filter(m3u_account__is_active=True)
        if only_ids is not None:
            qs = qs.filter(series_id__in=list(only_ids))
        return qs

    def _index_series(self, only_ids):
        """series id -> details, and the (series, account) pairs whose category is on."""
        shows, allowed = {}, set()
        rows = (
            self._series_relations(only_ids)
            .order_by("series_id", "-m3u_account__priority", "id")
            .values(
                "series_id", "series__uuid", "series__name", "series__year", "series__tmdb_id",
                "series__language", "category_id", "category__name", "m3u_account_id",
            )
            .iterator(chunk_size=CHUNK)
        )
        for row in rows:
            if not self._allowed(row["m3u_account_id"], row["category_id"]):
                continue
            allowed.add((row["series_id"], row["m3u_account_id"]))
            shows.setdefault(row["series_id"], {
                "id": row["series_id"],
                "uuid": str(row["series__uuid"]),
                "name": row["series__name"],
                "year": row["series__year"],
                "tmdb_id": row["series__tmdb_id"],
                "language": row["series__language"],
                "category": row["category__name"],
            })
        return shows, allowed

    def count_series(self, only_ids=None):
        shows, _allowed = self._index_series(only_ids)
        return len(shows)

    def iter_series(self, only_ids=None):
        from apps.vod.models import M3UEpisodeRelation

        shows, allowed = self._index_series(only_ids)
        qs = M3UEpisodeRelation.objects.filter(m3u_account__is_active=True)
        if only_ids is not None:
            qs = qs.filter(episode__series_id__in=list(only_ids))
        rows = (
            qs.order_by(
                "episode__series_id", "episode__season_number", "episode__episode_number", "episode_id",
                "-m3u_account__priority", "id",
            )
            .values(
                "episode_id", "episode__uuid", "episode__name", "episode__season_number",
                "episode__episode_number", "episode__series_id", "m3u_account_id", "m3u_account__name",
                "series_relation__category_id", "series_relation__category__name",
                "stream_id", "container_extension",
            )
            .iterator(chunk_size=CHUNK)
        )
        yielded = set()
        for series_id, series_rows in groupby(rows, key=lambda r: r["episode__series_id"]):
            show = shows.get(series_id)
            if show is None:
                continue  # no provider with its category switched on
            episodes = []
            for _episode_id, ep_rows in groupby(series_rows, key=lambda r: r["episode_id"]):
                # A copy is in use when its own folder is switched on. Older rows without a
                # folder fall back to the account carrying the show in a folder that is on.
                ep_rows = [
                    dict(r, folder=r["series_relation__category__name"] or show.get("category") or "")
                    for r in ep_rows
                    if (self._allowed(r["m3u_account_id"], r["series_relation__category_id"])
                        if r["series_relation__category_id"] is not None
                        else (series_id, r["m3u_account_id"]) in allowed)
                ]
                if not ep_rows:
                    continue
                first = ep_rows[0]
                episodes.append({
                    "id": first["episode_id"],
                    "uuid": str(first["episode__uuid"]),
                    "title": first["episode__name"],
                    "season": first["episode__season_number"],
                    "episode": first["episode__episode_number"],
                    "versions": self._versions(ep_rows),
                })
            yielded.add(series_id)
            yield dict(show, episodes=episodes)
        # Shows without any episode in the database yet still count towards the total.
        for series_id, show in shows.items():
            if series_id not in yielded:
                yield dict(show, episodes=[])

    # ---- episode lists -----------------------------------------------------------

    def series_needing_episodes(self, known, only_ids=None):
        """Shows whose episode list has to be fetched from the provider.

        known maps a series relation id to the provider's last_modified value at
        our last fetch. A show needs fetching when Dispatcharr never loaded its
        episodes, or the provider has changed it since. A show already loaded
        that we see for the first time is only recorded, not fetched again.
        """
        from django.utils import timezone

        retry_after = timezone.now() - timedelta(hours=24)
        rows = (
            self._series_relations(only_ids)
            .filter(m3u_account__account_type="XC")
            .order_by("-m3u_account__priority", "id")
            .values(
                "id", "series_id", "series__name", "category_id", "m3u_account_id",
                "m3u_account__name", "external_series_id", "custom_properties", "last_episode_refresh",
            )
            .iterator(chunk_size=CHUNK)
        )
        pending = []
        for row in rows:
            if not self._allowed(row["m3u_account_id"], row["category_id"]):
                continue
            props = row["custom_properties"] if isinstance(row["custom_properties"], dict) else {}
            basic = props.get("basic_data") if isinstance(props.get("basic_data"), dict) else {}
            last_modified = str(basic.get("last_modified") or "")
            key = str(row["id"])
            item = {
                "relation_id": row["id"],
                "series_id": row["series_id"],
                "name": row["series__name"],
                "last_modified": last_modified,
                "account": row["m3u_account__name"] or "",
                "external_id": row["external_series_id"] or "",
            }
            if not props.get("episodes_fetched"):
                # Asked within the last day and still empty: the provider has nothing. Forced
                # (only_ids) retries ask anyway.
                recent = row["last_episode_refresh"] and row["last_episode_refresh"] > retry_after
                if only_ids is not None or not recent:
                    pending.append(item)
            elif key not in known:
                known[key] = last_modified
            elif last_modified and known[key] != last_modified:
                pending.append(item)
        return pending

    def fetch_episodes(self, item):
        """Gets one show's episodes from its IPTV provider and has Dispatcharr store them.

        Returns ("ok", ""), ("empty", "") when the provider answered without
        episodes, or ("error", reason).

        One request per show. Dispatcharr's own refresh_series_episodes sends a
        separate login request first and then the episode request straight after;
        providers that limit requests refused the second one about half the time
        with HTTP 403. The Xtream API needs no login call, so the plugin asks for
        the episode list on its own and passes it to refresh_series_episodes,
        which stores it exactly as when someone opens the show in Dispatcharr.
        """
        from django.utils import timezone

        from apps.vod.models import M3UEpisodeRelation, M3USeriesRelation
        from apps.vod.tasks import refresh_series_episodes
        from core.xtream_codes import Client

        relation = M3USeriesRelation.objects.select_related("series", "m3u_account").get(id=item["relation_id"])
        account = relation.m3u_account
        with Client(account.server_url, account.username, account.password,
                    account.get_user_agent_string()) as client:
            client.server_info = {"login": "not needed"}  # skips the separate login request
            try:
                info = client.get_series_info(relation.external_series_id)
            except Exception as exc:
                return "error", friendly_error(str(exc))

        episodes = info.get("episodes") if isinstance(info, dict) else None
        if not episodes:
            # Recorded the way Dispatcharr records an empty answer, so the show is asked
            # again after a day rather than on every sync. Stored episodes stay.
            props = dict(relation.custom_properties or {})
            props["episodes_fetched"] = M3UEpisodeRelation.objects.filter(series_relation=relation).exists()
            relation.custom_properties = props
            relation.last_episode_refresh = timezone.now()
            relation.save(update_fields=["custom_properties", "last_episode_refresh"])
            return "empty", ""

        with capture_errors(("apps.vod.tasks", "core.xtream_codes")) as errors:
            refresh_series_episodes(account, relation.series, relation.external_series_id, episodes_data=episodes)
        relation.refresh_from_db(fields=["custom_properties", "last_episode_refresh"])
        props = relation.custom_properties if isinstance(relation.custom_properties, dict) else {}
        if not props.get("episodes_fetched"):
            return "error", "Dispatcharr could not store the episodes: " + friendly_error(errors[0] if errors else "")
        return "ok", ""

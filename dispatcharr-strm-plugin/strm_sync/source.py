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
                ep_rows = [r for r in ep_rows if (series_id, r["m3u_account_id"]) in allowed]
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
                "custom_properties", "last_episode_refresh",
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
        """Has Dispatcharr load one show's episodes from its provider. True when it answered."""
        from apps.vod.models import M3USeriesRelation
        from apps.vod.tasks import refresh_series_episodes

        relation = M3USeriesRelation.objects.select_related("series", "m3u_account").get(id=item["relation_id"])
        before = relation.last_episode_refresh
        # Dispatcharr logs and swallows provider errors here, so the outcome is read back.
        refresh_series_episodes(relation.m3u_account, relation.series, relation.external_series_id)
        relation.refresh_from_db(fields=["custom_properties", "last_episode_refresh"])
        props = relation.custom_properties if isinstance(relation.custom_properties, dict) else {}
        return bool(props.get("episodes_fetched")) and relation.last_episode_refresh != before

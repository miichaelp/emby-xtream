import logging
import os
import sys
from datetime import timedelta

import pytest

django = pytest.importorskip("django")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "stubs"))

from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[],
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        USE_TZ=True,
    )
    django.setup()

from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.m3u.models import M3UAccount  # noqa: E402
from apps.vod import models as vod  # noqa: E402
from apps.vod import tasks as vod_tasks  # noqa: E402
from strm_sync.source import DispatcharrSource  # noqa: E402

MODELS = [M3UAccount, vod.VODCategory, vod.Movie, vod.Series, vod.Episode, vod.M3UMovieRelation,
          vod.M3USeriesRelation, vod.M3UEpisodeRelation, vod.M3UVODCategoryRelation]


@pytest.fixture
def db():
    with connection.schema_editor() as editor:
        for model in MODELS:
            editor.create_model(model)
    vod_tasks.calls.clear()
    vod_tasks.behaviour.clear()
    yield
    with connection.schema_editor() as editor:
        for model in reversed(MODELS):
            editor.delete_model(model)


@pytest.fixture
def world(db):
    """Two active providers (Main outranks Backup), one inactive, three categories."""
    w = type("World", (), {})()
    w.main = M3UAccount.objects.create(name="Main", priority=10)
    w.backup = M3UAccount.objects.create(name="Backup", priority=1)
    w.off = M3UAccount.objects.create(name="Switched off", priority=99, is_active=False)
    w.action = vod.VODCategory.objects.create(name="Action")
    w.hidden = vod.VODCategory.objects.create(name="Hidden")
    w.drama = vod.VODCategory.objects.create(name="Drama", category_type="series")
    vod.M3UVODCategoryRelation.objects.create(m3u_account=w.main, category=w.action, enabled=True)
    vod.M3UVODCategoryRelation.objects.create(m3u_account=w.main, category=w.hidden, enabled=False)
    vod.M3UVODCategoryRelation.objects.create(m3u_account=w.backup, category=w.hidden, enabled=False)
    return w


def source():
    return DispatcharrSource(logging.getLogger("test"))


def test_movies_grouped_best_provider_first(world):
    heat = vod.Movie.objects.create(name="Heat", year=1995, tmdb_id="949")
    # Created lowest priority first, to prove the order comes from priority and not row order.
    vod.M3UMovieRelation.objects.create(m3u_account=world.backup, movie=heat, category=None, stream_id="b1", container_extension=None)
    vod.M3UMovieRelation.objects.create(m3u_account=world.main, movie=heat, category=world.action, stream_id="m1", container_extension="mkv")
    vod.M3UMovieRelation.objects.create(m3u_account=world.off, movie=heat, category=world.action, stream_id="x1")
    solo = vod.Movie.objects.create(name="Ronin", language="fr")
    vod.M3UMovieRelation.objects.create(m3u_account=world.backup, movie=solo, category=world.action, stream_id="b2", container_extension="mp4")

    movies = list(source().iter_movies())
    assert source().count_movies() == 2
    first = movies[0]
    assert (first["id"], first["name"], first["year"], first["tmdb_id"], first["category"]) == (heat.id, "Heat", 1995, "949", "Action")
    assert first["uuid"] == str(heat.uuid)
    assert [(v["stream_id"], v["provider"], v["ext"]) for v in first["versions"]] == [("m1", "Main", "mkv"), ("b1", "Backup", "mp4")]
    assert movies[1]["language"] == "fr" and len(movies[1]["versions"]) == 1

    assert [m["id"] for m in source().iter_movies(only_ids=[solo.id])] == [solo.id]


def test_movies_in_switched_off_categories_are_left_out(world):
    partly = vod.Movie.objects.create(name="Partly")
    vod.M3UMovieRelation.objects.create(m3u_account=world.main, movie=partly, category=world.hidden, stream_id="m1")
    vod.M3UMovieRelation.objects.create(m3u_account=world.backup, movie=partly, category=world.action, stream_id="b1")
    fully = vod.Movie.objects.create(name="Fully hidden")
    vod.M3UMovieRelation.objects.create(m3u_account=world.main, movie=fully, category=world.hidden, stream_id="m2")
    inactive_only = vod.Movie.objects.create(name="Inactive only")
    vod.M3UMovieRelation.objects.create(m3u_account=world.off, movie=inactive_only, category=world.action, stream_id="x")

    movies = {m["id"]: m for m in source().iter_movies()}
    assert [v["provider"] for v in movies[partly.id]["versions"]] == ["Backup"]
    assert movies[partly.id]["category"] == "Action"
    assert movies[fully.id]["versions"] == []          # counted, skipped by the engine
    assert inactive_only.id not in movies


def test_duplicate_stream_ids_collapse(world):
    m = vod.Movie.objects.create(name="Twin")
    vod.M3UMovieRelation.objects.create(m3u_account=world.main, movie=m, category=world.action, stream_id="7")
    vod.M3UMovieRelation.objects.create(m3u_account=world.backup, movie=m, category=world.action, stream_id="7")
    assert [v["provider"] for v in next(source().iter_movies())["versions"]] == ["Main"]


def _series(world, name, account, category, external, fetched=True, last_modified=None, refreshed=None):
    series, _ = vod.Series.objects.get_or_create(name=name)
    props = {"episodes_fetched": fetched, "detailed_fetched": fetched}
    if last_modified is not None:
        props["basic_data"] = {"last_modified": last_modified}
    rel = vod.M3USeriesRelation.objects.create(
        m3u_account=account, series=series, category=category, external_series_id=external,
        custom_properties=props, last_episode_refresh=refreshed)
    return series, rel


def test_series_with_episodes_and_versions(world):
    show, _ = _series(world, "Show", world.main, world.drama, "s1")
    _series(world, "Show", world.backup, world.drama, "s1b")
    e2 = vod.Episode.objects.create(name="Two", series=show, season_number=1, episode_number=2)
    e1 = vod.Episode.objects.create(name="One", series=show, season_number=1, episode_number=1)
    special = vod.Episode.objects.create(name="Special", series=show, season_number=0, episode_number=0)
    for ep, sid in ((e1, "11"), (e2, "12"), (special, "10")):
        vod.M3UEpisodeRelation.objects.create(m3u_account=world.main, episode=ep, stream_id=sid, container_extension="mkv")
    vod.M3UEpisodeRelation.objects.create(m3u_account=world.backup, episode=e1, stream_id="91")
    vod.M3UEpisodeRelation.objects.create(m3u_account=world.off, episode=e1, stream_id="x")
    empty, _ = _series(world, "Not loaded yet", world.main, world.drama, "s2", fetched=False)
    hidden, _ = _series(world, "Hidden show", world.main, world.hidden, "s3")
    vod.M3UEpisodeRelation.objects.create(
        m3u_account=world.main, stream_id="h1",
        episode=vod.Episode.objects.create(name="H", series=hidden, season_number=1, episode_number=1))

    shows = {s["id"]: s for s in source().iter_series()}
    assert source().count_series() == 2 and hidden.id not in shows
    assert shows[empty.id]["episodes"] == []
    got = shows[show.id]
    assert (got["name"], got["category"], got["uuid"]) == ("Show", "Drama", str(show.uuid))
    assert [(e["season"], e["episode"], e["title"]) for e in got["episodes"]] == [(0, 0, "Special"), (1, 1, "One"), (1, 2, "Two")]
    assert [v["provider"] for v in got["episodes"][1]["versions"]] == ["Main", "Backup"]
    assert got["episodes"][1]["id"] == e1.id and got["episodes"][1]["uuid"] == str(e1.uuid)
    assert [s["id"] for s in source().iter_series(only_ids=[empty.id])] == [empty.id]


def test_an_episode_from_a_provider_whose_category_is_off_is_not_a_version(world):
    show, _ = _series(world, "Show", world.main, world.drama, "s1")
    _series(world, "Show", world.backup, world.hidden, "s1b")       # Backup has it in a switched-off category
    ep = vod.Episode.objects.create(name="One", series=show, season_number=1, episode_number=1)
    vod.M3UEpisodeRelation.objects.create(m3u_account=world.main, episode=ep, stream_id="11")
    vod.M3UEpisodeRelation.objects.create(m3u_account=world.backup, episode=ep, stream_id="91")
    got = next(source().iter_series())
    assert [v["provider"] for v in got["episodes"][0]["versions"]] == ["Main"]


def test_which_shows_need_their_episode_list(world):
    now = timezone.now()
    _, new = _series(world, "New", world.main, world.drama, "new", fetched=False)
    _, empty_recent = _series(world, "Empty, asked today", world.main, world.drama, "er", fetched=False, refreshed=now - timedelta(hours=2))
    _, empty_old = _series(world, "Empty, asked last week", world.main, world.drama, "eo", fetched=False, refreshed=now - timedelta(days=7))
    _, first_seen = _series(world, "Loaded, first time we see it", world.main, world.drama, "fs", last_modified="100")
    _, unchanged = _series(world, "Unchanged", world.main, world.drama, "un", last_modified="200")
    _, changed = _series(world, "Changed", world.main, world.drama, "ch", last_modified="301")
    _, hidden = _series(world, "Hidden", world.main, world.hidden, "hi", fetched=False)
    _, inactive = _series(world, "Inactive", world.off, world.drama, "in", fetched=False)

    known = {str(unchanged.id): "200", str(changed.id): "300"}
    pending = source().series_needing_episodes(known)
    assert sorted(p["name"] for p in pending) == ["Changed", "Empty, asked last week", "New"]
    assert known[str(first_seen.id)] == "100"                       # recorded, not fetched again
    changed_item = next(p for p in pending if p["name"] == "Changed")
    assert changed_item["relation_id"] == changed.id and changed_item["last_modified"] == "301"

    forced = source().series_needing_episodes({}, only_ids=[empty_recent.series_id])
    assert [p["name"] for p in forced] == ["Empty, asked today"]    # a retry asks even within the day


def test_fetch_episodes_reads_the_outcome_back(world):
    _, ok = _series(world, "Ok", world.main, world.drama, "ok", fetched=False)
    _, nothing = _series(world, "Nothing", world.main, world.drama, "nothing", fetched=False)
    _, error = _series(world, "Error", world.main, world.drama, "error", fetched=True, last_modified="5")
    vod_tasks.behaviour.update({"nothing": "empty", "error": "error"})
    src = source()
    assert src.fetch_episodes({"relation_id": ok.id}) == ("ok", "")
    assert src.fetch_episodes({"relation_id": nothing.id}) == ("empty", "")
    # Already loaded, provider times out on the re-check: an error with Dispatcharr's own reason.
    assert src.fetch_episodes({"relation_id": error.id}) == ("error", "The provider did not answer within 60 seconds")
    assert vod_tasks.calls == ["ok", "nothing", "error"]


@pytest.mark.parametrize("message,expected", [
    ("Error refreshing episodes for series X: HTTPConnectionPool(host='p', port=80): Read timed out.", "within 60 seconds"),
    ("Failed to get series info: 429 Client Error: Too Many Requests for url", "HTTP 429"),
    ("Failed to get series info: 502 Server Error: Bad Gateway for url", "HTTP 502"),
    ("Error refreshing episodes for series X: HTTPConnectionPool(host='p'): Max retries exceeded", "connect"),
    ("Invalid series info response for series_id 5: []", "not a valid episode list"),
    ("", "did not answer"),
    ("Error refreshing episodes for series X: something odd", "something odd"),
])
def test_friendly_error(message, expected):
    from strm_sync.source import friendly_error

    assert expected in friendly_error(message)

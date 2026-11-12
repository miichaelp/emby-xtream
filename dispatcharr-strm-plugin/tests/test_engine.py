import os

from fakes import LOG, FakeControl, FakeSource, episode, movie, show, version
from strm_sync import core
from strm_sync.engine import Engine, Settings

BASE = "http://d:9191"


def settings(tmp_path, **over):
    raw = {"output_dir": str(tmp_path), "base_url": BASE + "/"}
    raw.update(over)
    return Settings(raw)


def run(tmp_path, source, control=None, state=None, only=None, kinds=("movies", "series"), **over):
    engine = Engine(settings(tmp_path, **over), source, control or FakeControl(), LOG, sleep=lambda s: None)
    return engine.run(state if state is not None else {}, kinds=kinds, only=only)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def tree(tmp_path):
    out = []
    for dirpath, _d, files in os.walk(tmp_path):
        out += [os.path.relpath(os.path.join(dirpath, f), tmp_path).replace(os.sep, "/") for f in files]
    return sorted(out)


def test_settings_problems():
    assert Settings({}).problems()
    assert any("http" in p for p in Settings({"output_dir": "/data/strm", "base_url": "dispatcharr:9191"}).problems())
    assert any("full path" in p for p in Settings({"output_dir": "strm", "base_url": BASE}).problems())
    assert any("Xtream" in p for p in Settings({"output_dir": "/s", "base_url": BASE, "url_style": "xtream"}).problems())
    assert Settings({"output_dir": "/data/strm", "base_url": BASE}).problems() == []
    assert Settings({"output_dir": "/s", "base_url": BASE, "safety_limit": "20"}).safety_limit == 0.2


def test_movie_layout_and_proxy_url(tmp_path):
    src = FakeSource(movies=[movie(1, "EN - Heat", year=1995, tmdb="949")])
    result = run(tmp_path, src)
    path = tmp_path / "Movies" / "Heat (1995) [tmdbid=949]" / "Heat (1995) [tmdbid=949].strm"
    # One provider: no stream_id, so Dispatcharr picks the provider and can fail over.
    assert read(path) == f"{BASE}/proxy/vod/movie/m-1"
    assert result["movies"]["added"] == 1 and result["movies"]["files"] == 1
    assert result["added_titles"] == ["Heat (1995)"]


def test_movie_versions_named_by_provider(tmp_path):
    src = FakeSource(movies=[movie(1, "Heat (1995)", versions=[
        version(11, "Main"), version(12, "Backup 4K"), version(13, "Backup 4K")])])
    run(tmp_path, src)
    d = tmp_path / "Movies" / "Heat (1995)"
    assert read(d / "Heat (1995).strm") == f"{BASE}/proxy/vod/movie/m-1?stream_id=11"
    assert read(d / "Heat (1995) - Backup 4K.strm") == f"{BASE}/proxy/vod/movie/m-1?stream_id=12"
    assert read(d / "Heat (1995) - Backup 4K 2.strm") == f"{BASE}/proxy/vod/movie/m-1?stream_id=13"


def test_versions_off_and_numbered(tmp_path):
    two = [version(11, "Main"), version(12, "Backup")]
    run(tmp_path / "a", FakeSource(movies=[movie(1, "Heat", versions=two)]), multi_version=False)
    assert tree(tmp_path / "a") == ["Movies/Heat/Heat.strm"]
    run(tmp_path / "b", FakeSource(movies=[movie(1, "Heat", versions=two)]), version_label="number")
    assert tree(tmp_path / "b") == ["Movies/Heat/Heat - Version 2.strm", "Movies/Heat/Heat.strm"]


def test_xtream_url_style(tmp_path):
    src = FakeSource(
        movies=[movie(7, "Heat", versions=[version(11, "Main", "mp4"), version(12, "Backup")])],
        shows=[show(3, "Show", [episode(40, 1, 2, "Two")])])
    run(tmp_path, src, url_style="xtream", xc_username="emby", xc_password="pw")
    assert read(tmp_path / "Movies/Heat/Heat.strm") == f"{BASE}/movie/emby/pw/7.mp4"
    assert read(tmp_path / "Movies/Heat/Heat - Backup.strm") == f"{BASE}/proxy/vod/movie/m-7?stream_id=12"
    assert read(tmp_path / "Shows/Show/Season 01/Show - S01E02 - Two.strm") == f"{BASE}/series/emby/pw/40.mkv"


def test_series_layout_specials_and_versions(tmp_path):
    src = FakeSource(shows=[show(1, "|UK| The Show", year=2020, tmdb="55", episodes=[
        episode(1, 1, 1, "The Show - S01E01 - Pilot"),
        episode(2, 0, 0, "Special"),
        episode(3, None, None, "Loose"),               # season missing -> 1, number missing -> 0
        episode(4, 2, 3, "Both", versions=[version(71, "Main"), version(72, "Backup")]),
        episode(5, 2, 3, "Duplicate number"),          # same code as the one above: first wins
        episode(6, 3, 1, "No stream", versions=[]),
    ])])
    result = run(tmp_path, src)
    assert tree(tmp_path) == [
        "Shows/The Show (2020) [tmdbid=55]/Season 00/The Show (2020) - S00E00 - Special.strm",
        "Shows/The Show (2020) [tmdbid=55]/Season 01/The Show (2020) - S01E00 - Loose.strm",
        "Shows/The Show (2020) [tmdbid=55]/Season 01/The Show (2020) - S01E01 - Pilot.strm",
        "Shows/The Show (2020) [tmdbid=55]/Season 02/The Show (2020) - S02E03 - Both - Backup.strm",
        "Shows/The Show (2020) [tmdbid=55]/Season 02/The Show (2020) - S02E03 - Both.strm",
    ]
    season2 = tmp_path / "Shows/The Show (2020) [tmdbid=55]/Season 02"
    assert read(season2 / "The Show (2020) - S02E03 - Both - Backup.strm") == f"{BASE}/proxy/vod/episode/e-4?stream_id=72"
    assert result["series"]["episodes"] == 4 and result["series"]["files"] == 5 and result["series"]["added"] == 1


def test_category_folders_exclusions_and_nfo(tmp_path):
    src = FakeSource(
        movies=[movie(1, "Heat", tmdb="949", year=1995, category="|EN| Action"),
                movie(2, "Nope", category="Adult XXX"),
                movie(3, "No Streams", versions=[])],
        shows=[show(1, "Show", [episode(1, 1, 1)], tmdb="7", category="Drama")])
    result = run(tmp_path, src, movie_folder_mode="category", series_folder_mode="category",
                 excluded_categories="*xxx*", write_nfo=True)
    assert tree(tmp_path) == [
        "Movies/Action/Heat (1995) [tmdbid=949]/Heat (1995) [tmdbid=949].nfo",
        "Movies/Action/Heat (1995) [tmdbid=949]/Heat (1995) [tmdbid=949].strm",
        "Shows/Drama/Show [tmdbid=7]/Season 01/Show - S01E01.strm",
        "Shows/Drama/Show [tmdbid=7]/tvshow.nfo",
    ]
    assert result["movies"]["skipped"] == 2
    assert '<uniqueid type="tmdb" default="true">949</uniqueid>' in read(
        tmp_path / "Movies/Action/Heat (1995) [tmdbid=949]/Heat (1995) [tmdbid=949].nfo")


def test_second_run_changes_nothing_and_rewrites_only_what_changed(tmp_path):
    movies = [movie(1, "Heat"), movie(2, "Ronin")]
    run(tmp_path, FakeSource(movies=movies))
    before = os.stat(tmp_path / "Movies/Heat/Heat.strm").st_mtime_ns
    again = run(tmp_path, FakeSource(movies=movies))
    assert again["movies"]["unchanged"] == 2 and again["movies"]["added"] == 0
    assert os.stat(tmp_path / "Movies/Heat/Heat.strm").st_mtime_ns == before
    # Ronin gains a provider: only Ronin is touched.
    movies[1] = movie(2, "Ronin", versions=[version(21, "Main"), version(22, "Backup")])
    third = run(tmp_path, FakeSource(movies=movies))
    assert third["movies"]["unchanged"] == 1 and third["movies"]["added"] == 1


def test_same_title_twice_keeps_the_first_and_language_separates(tmp_path):
    src = FakeSource(movies=[movie(1, "Heat"), movie(2, "heat"), movie(3, "Heat", language="fr")])
    result = run(tmp_path, src)
    assert read(tmp_path / "Movies/Heat/Heat.strm").endswith("/m-1")
    assert (tmp_path / "Movies/Heat [FR]/Heat [FR].strm").exists()
    assert result["movies"]["skipped"] == 1


def test_cleanup_removes_titles_dispatcharr_dropped_but_not_foreign_files(tmp_path):
    run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}") for i in range(1, 13)],
                             shows=[show(1, "Show", [episode(1, 1, 1), episode(2, 1, 2)])]))
    mine = tmp_path / "Movies" / "Home Video" / "Home Video.strm"
    mine.parent.mkdir()
    mine.write_text("http://nas/home.mkv")

    result = run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}") for i in range(1, 12)],
                                      shows=[show(1, "Show", [episode(1, 1, 1)])]))
    assert result["movies"]["deleted"] == 1 and result["series"]["deleted"] == 1
    assert not (tmp_path / "Movies/Movie 12").exists()
    assert not (tmp_path / "Shows/Show/Season 01/Show - S01E02.strm").exists()
    assert mine.exists()
    assert result["removed"] == ["Movie 12/Movie 12.strm".replace("/", os.sep),
                                 "Show/Season 01/Show - S01E02.strm".replace("/", os.sep)]


def test_cleanup_safety_limit_holds_when_the_catalogue_shrinks(tmp_path):
    run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}") for i in range(1, 21)]))
    # Dispatcharr suddenly has 5 movies (an account switched off, a half-finished refresh).
    result = run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}") for i in range(1, 6)]))
    assert result["movies"]["deleted"] == 0
    assert len(tree(tmp_path)) == 20
    assert any("safety limit" in w and "not removed" in w for w in result["warnings"])


def test_empty_catalogue_never_deletes(tmp_path):
    run(tmp_path, FakeSource(movies=[movie(1, "Heat"), movie(2, "Ronin")]))
    result = run(tmp_path, FakeSource(movies=[]), safety_limit=0)
    assert result["movies"]["deleted"] == 0 and len(tree(tmp_path)) == 2


def test_stop_keeps_files_and_skips_cleanup(tmp_path):
    run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}") for i in range(1, 6)]))
    # Only movies 6-9 exist now, so a full run would remove 1-5. Stop after two titles.
    control = FakeControl(stop_after=3)
    result = run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}") for i in range(6, 10)],
                                      shows=[show(1, "Show", [episode(1, 1, 1)])]), control, safety_limit=0)
    assert result["stopped"]
    assert result["movies"]["deleted"] == 0 and result["movies"]["total"] == 2
    assert (tmp_path / "Movies/Movie 1/Movie 1.strm").exists()
    assert result["series"] is None                      # series never started
    assert not (tmp_path / "Shows").exists()


def test_failed_title_is_recorded_and_blocks_cleanup(tmp_path):
    run(tmp_path, FakeSource(movies=[movie(1, "Heat"), movie(2, "Ronin")]))
    blocker = tmp_path / "Movies" / "Blocked"
    blocker.write_text("a file where the folder should go")
    result = run(tmp_path, FakeSource(movies=[movie(1, "Heat"), movie(3, "Blocked")]), safety_limit=0)
    assert result["movies"]["failed"] == 1
    assert result["failed_items"][0]["kind"] == "movie" and result["failed_items"][0]["id"] == 3
    assert (tmp_path / "Movies/Ronin/Ronin.strm").exists()      # not removed on an incomplete run
    assert result["movies"]["deleted"] == 0


def test_catalogue_read_error_blocks_cleanup(tmp_path):
    run(tmp_path, FakeSource(movies=[movie(1, "Heat"), movie(2, "Ronin")]))
    broken = movie(2, "Ronin")
    broken["boom"] = True
    result = run(tmp_path, FakeSource(movies=[movie(1, "Heat"), broken]), safety_limit=0)
    assert result["movies"]["deleted"] == 0 and len(tree(tmp_path)) == 2
    assert any("Reading movies" in w for w in result["warnings"])


def test_retry_only_writes_the_given_ids_and_never_cleans_up(tmp_path):
    src = FakeSource(movies=[movie(1, "Heat"), movie(2, "Ronin"), movie(3, "Spy")])
    run(tmp_path, src)
    os.remove(tmp_path / "Movies/Ronin/Ronin.strm")
    result = run(tmp_path, src, only={"movies": [2], "series": []})
    assert result["movies"]["total"] == 1 and result["movies"]["added"] == 1 and result["movies"]["deleted"] == 0
    assert result["series"] is None
    assert len(tree(tmp_path)) == 3


class LoadingSource(FakeSource):
    """Shows start without episodes; fetching one "loads" them, as Dispatcharr does."""

    def __init__(self, shows, pending, loaded):
        super().__init__(shows=shows, pending=pending)
        self.loaded = loaded          # series id -> episodes it gets once fetched
        self.order = []

    def iter_series(self, only_ids=None):
        for item in self._pick(self.shows, only_ids):
            self.order.append(("read", item["id"]))
            yield item

    def fetch_episodes(self, item):
        self.order.append(("fetch", item["series_id"]))
        ok = super().fetch_episodes(item)
        if ok == ("ok", "") and item["series_id"] in self.loaded:
            show = next(s for s in self.shows if s["id"] == item["series_id"])
            show["episodes"] = self.loaded[item["series_id"]]
        return ok


def test_episode_lists_are_fetched_just_before_each_show_is_written(tmp_path):
    shows = [show(i, f"Show {i}", []) for i in range(1, 6)]
    pending = [{"relation_id": i, "series_id": i, "name": f"Show {i}", "last_modified": str(1000 + i)}
               for i in range(1, 6)]
    src = LoadingSource(shows, pending, {i: [episode(10 * i, 1, 1)] for i in range(1, 6)})
    src.fetch_results = {
        2: ("empty", ""),                                                     # no episodes: not a failure
        3: [("error", "The provider did not answer within 60 seconds")] * 2,  # fails twice: one retry only
        4: [RuntimeError("boom"), ("ok", "")],                                # fails, then the retry works
    }
    sleeps = []
    engine = Engine(settings(tmp_path, episode_fetch_limit=4, episode_fetch_delay_ms=0), src, FakeControl(), LOG,
                    sleep=sleeps.append)
    state = {}
    result = engine.run(state)

    assert src.fetched == [1, 2, 3, 3, 4, 4]                          # the limit holds the fifth back
    assert sleeps == [10.0, 10.0]
    assert src.order[:4] == [("read", 1), ("fetch", 1), ("read", 1), ("read", 2)]   # fetch, re-read, write, next
    assert state["series_last_modified"] == {"1": "1001", "4": "1004"}  # only answered shows are marked
    assert [(f["id"], f["error"]) for f in result["failed_items"]] == [(3, "The provider did not answer within 60 seconds")]
    assert any("5 shows need their episode list" in w for w in result["warnings"])
    assert tree(tmp_path) == ["Shows/Show 1/Season 01/Show 1 - S01E01.strm",
                              "Shows/Show 4/Season 01/Show 4 - S01E01.strm"]
    assert result["series"]["skipped"] == 3 and result["series"]["deleted"] == 0


def test_stop_during_episode_fetching_writes_nothing_more(tmp_path):
    shows = [show(i, f"Show {i}", []) for i in range(1, 4)]
    pending = [{"relation_id": i, "series_id": i, "name": f"Show {i}", "last_modified": "1"} for i in range(1, 4)]
    src = LoadingSource(shows, pending, {i: [episode(10 * i, 1, 1)] for i in range(1, 4)})
    result = run(tmp_path, src, control=FakeControl(stop_after=4), kinds=("series",))
    assert result["stopped"] and len(src.fetched) < 3


def test_fetch_can_be_turned_off_and_movies_only_skips_series(tmp_path):
    src = FakeSource(movies=[movie(1, "Heat")], shows=[show(1, "Show", [episode(1, 1, 1)])],
                     pending=[{"relation_id": 1, "series_id": 1, "name": "Show", "last_modified": "1"}])
    run(tmp_path, src, fetch_episodes=False)
    assert src.fetched == []
    only_movies = run(tmp_path / "x", src, kinds=("movies",))
    assert only_movies["series"] is None and tree(tmp_path / "x") == ["Movies/Heat/Heat.strm"]
    off = run(tmp_path / "y", src, sync_movies=False)
    assert off["movies"] is None and tree(tmp_path / "y") == ["Shows/Show/Season 01/Show - S01E01.strm"]


def test_changing_the_address_never_deletes_the_old_files_blindly(tmp_path):
    run(tmp_path, FakeSource(movies=[movie(1, "Heat"), movie(2, "Ronin")]))
    # New address: Heat is rewritten in place; Ronin (gone from the catalogue) holds the old
    # address, so it no longer counts as ours and is left alone.
    result = run(tmp_path, FakeSource(movies=[movie(1, "Heat")]), base_url="http://new:9191", safety_limit=0)
    assert read(tmp_path / "Movies/Heat/Heat.strm") == "http://new:9191/proxy/vod/movie/m-1"
    assert (tmp_path / "Movies/Ronin/Ronin.strm").exists() and result["movies"]["deleted"] == 0
    assert core.is_owned_strm(str(tmp_path / "Movies/Heat/Heat.strm"), core.owned_url_prefixes("http://new:9191"))


def test_log_names_every_written_title_and_reports_progress(tmp_path, caplog):
    import logging

    src = FakeSource(movies=[movie(i, f"Movie {i}") for i in range(1, 5)],
                     shows=[show(1, "Show", [episode(1, 1, 1), episode(2, 1, 2)])])
    engine = Engine(settings(tmp_path), src, FakeControl(), LOG, sleep=lambda s: None)
    ticks = iter(range(0, 1000, 20))                       # every call is 20 s later
    engine.clock = lambda: next(ticks)
    with caplog.at_level(logging.INFO, logger=LOG.name):
        engine.run({})
    text = caplog.text
    assert "Added movie: Movie 1 (1 file)" in text and "Added movie: Movie 4 (1 file)" in text
    assert "Added show: Show (2 of 2 episodes new or changed)" in text
    assert "Movies: 3 of 4 done (3 new" in text and "per second, about" in text

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOG.name):
        Engine(settings(tmp_path), src, FakeControl(), LOG, sleep=lambda s: None).run({})
    assert "Added movie" not in caplog.text and "Updated" not in caplog.text   # unchanged titles stay quiet


class Clock:
    """A fake clock that sleeping moves forward."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 2))
        self.now += seconds


def _limited_engine(tmp_path, results, delay_ms=1000, count=None):
    count = count or len(results)
    shows = [show(i, f"Show {i}", []) for i in range(1, count + 1)]
    pending = [{"relation_id": i, "series_id": i, "name": f"Show {i}", "last_modified": "1"} for i in range(1, count + 1)]
    src = LoadingSource(shows, pending, {i: [episode(10 * i, 1, 1)] for i in range(1, count + 1)})
    src.fetch_results = results
    clock = Clock()
    engine = Engine(settings(tmp_path, episode_fetch_delay_ms=delay_ms), src, FakeControl(), LOG, sleep=clock.sleep)
    engine.clock = clock
    return engine, src, clock


REFUSED = ("error", "The provider answered with an error (HTTP 403)")


def test_a_refusal_is_tried_five_more_times_at_the_same_pace(tmp_path, caplog):
    engine, src, clock = _limited_engine(tmp_path, {
        1: ("ok", ""),
        2: [REFUSED, REFUSED, ("ok", "")],                    # third try works
        3: ("ok", ""),
        4: [REFUSED] * 6,                                     # never works
    })
    import logging

    with caplog.at_level(logging.INFO, logger=LOG.name):
        result = engine.run({}, kinds=("series",))
    assert src.fetched == [1, 2, 2, 2, 3, 4, 4, 4, 4, 4, 4]
    assert clock.sleeps.count(8.0) == 1 and clock.sleeps.count(5.0) == 1
    assert engine.gap == 1.0                                  # the pace is never changed
    assert [f["id"] for f in result["failed_items"]] == [4]
    assert "'Show 2' in 0.0 s (3rd try)" in caplog.text     # one line per show
    assert "failed after 6 tries" in caplog.text and "try 2 in" not in caplog.text


def test_the_gap_counts_from_the_end_of_the_last_request(tmp_path):
    engine, src, clock = _limited_engine(tmp_path, {i: ("ok", "") for i in range(1, 6)})
    engine.run({}, kinds=("series",))
    # The fake clock only moves when sleeping, so each wait is the full second.
    assert clock.sleeps == [1.0, 1.0, 1.0, 1.0]


def test_fetch_lines_name_the_series_id(tmp_path, caplog):
    import logging

    shows = [show(1, "Show", [])]
    pending = [{"relation_id": 1, "series_id": 1, "name": "Show", "last_modified": "1",
                "account": "Backup IPTV", "external_id": "5521"},
               {"relation_id": 2, "series_id": 1, "name": "Show", "last_modified": "1",
                "account": "Main IPTV", "external_id": "77"}]
    src = LoadingSource(shows, pending, {1: [episode(1, 1, 1)]})
    src.fetch_results = {2: [REFUSED] * 6}
    with caplog.at_level(logging.INFO, logger=LOG.name):
        Engine(settings(tmp_path), src, FakeControl(), LOG, sleep=lambda s: None).run({}, kinds=("series",))
    assert "'Show' (series 5521) in" in caplog.text and "Backup IPTV" not in caplog.text
    assert "'Show' (series 77) failed after 6 tries" in caplog.text


def test_versions_named_by_provider_folder(tmp_path):
    src = FakeSource(
        movies=[movie(1, "Heat", versions=[version(11, "IPTV", folder="MOVIES"),
                                           version(12, "IPTV", folder="MOVIES ⁴ᴷ ³⁸⁴⁰ᵖ")])],
        shows=[show(1, "13 Reasons Why", [episode(5, 1, 1, "Κασέτα 1, Πλευρά Α", versions=[
            version(51, "IPTV", folder="NETFLIX SERIES"), version(52, "IPTV", folder="NETFLIX SERIES ⁴ᴷ")])])])
    run(tmp_path, src, version_label="folder", remove_terms="4K")
    assert tree(tmp_path) == [
        "Movies/Heat/Heat - MOVIES 4K 3840p.strm",             # clean-up terms do not touch labels
        "Movies/Heat/Heat - MOVIES.strm",
        "Shows/13 Reasons Why/Season 01/13 Reasons Why - S01E01 - Κασέτα 1, Πλευρά Α - NETFLIX SERIES 4K.strm",
        "Shows/13 Reasons Why/Season 01/13 Reasons Why - S01E01 - Κασέτα 1, Πλευρά Α - NETFLIX SERIES.strm",
    ]
    assert read(tmp_path / "Movies/Heat/Heat - MOVIES.strm") == f"{BASE}/proxy/vod/movie/m-1?stream_id=11"


def test_episode_titles_can_be_left_out_of_file_names(tmp_path):
    src = FakeSource(shows=[show(1, "13 Reasons Why", [episode(5, 1, 1, "Κασέτα 1, Πλευρά Α", versions=[
        version(51, folder="NETFLIX SERIES"), version(52, folder="NETFLIX SERIES ⁴ᴷ")])])])
    run(tmp_path, src, version_label="folder", episode_titles=False)
    assert tree(tmp_path) == [
        "Shows/13 Reasons Why/Season 01/13 Reasons Why - S01E01 - NETFLIX SERIES 4K.strm",
        "Shows/13 Reasons Why/Season 01/13 Reasons Why - S01E01 - NETFLIX SERIES.strm",
    ]


def test_a_single_version_has_no_folder_suffix(tmp_path):
    run(tmp_path, FakeSource(movies=[movie(1, "Heat", versions=[version(11, folder="MOVIES")])]), version_label="folder")
    assert tree(tmp_path) == ["Movies/Heat/Heat.strm"]


def test_renaming_removes_the_old_names_even_past_the_safety_limit(tmp_path):
    def shows():
        return [show(i, f"Show {i}", [episode(10 * i, 1, 1, "Κασέτα", versions=[
            version(100 + i, folder="NETFLIX SERIES"), version(200 + i, folder="NETFLIX SERIES ⁴ᴷ")])])
            for i in range(1, 13)]

    run(tmp_path, FakeSource(shows=shows()), version_label="number")          # Show - S01E01 - Κασέτα / - Version 2
    assert len(tree(tmp_path)) == 24
    result = run(tmp_path, FakeSource(shows=shows()), version_label="folder", episode_titles=False)
    files = tree(tmp_path)
    assert len(files) == 24 and all("Κασέτα" not in f and "Version" not in f for f in files)
    assert result["series"]["deleted"] == 24 and not result["warnings"]       # every file renamed: all old names go


def test_a_rename_never_hides_a_real_catalogue_drop(tmp_path):
    run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}") for i in range(1, 21)]), episode_titles=True)
    # Ten movies vanish from Dispatcharr in the same run as a rename of the rest.
    result = run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}", year=2000) for i in range(1, 11)]))
    files = tree(tmp_path)
    assert result["movies"]["deleted"] == 10                                   # the ten renamed old names
    assert sum("(2000)" in f for f in files) == 10 and len(files) == 20       # the ten vanished titles stay
    assert any("safety limit" in w for w in result["warnings"])


def test_a_stopped_sync_removes_nothing_and_does_not_scan(tmp_path, monkeypatch):
    run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}") for i in range(1, 6)]))
    reads = []
    monkeypatch.setattr(core, "owned_strm_url", lambda path, prefixes: reads.append(path))
    result = run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}", year=2000) for i in range(1, 5)]),
                 control=FakeControl(stop_after=3), safety_limit=0)
    assert result["stopped"] and result["movies"]["deleted"] == 0 and reads == []
    assert "Movies/Movie 1/Movie 1.strm" in tree(tmp_path)                   # tidied by the next full run


def test_a_partial_run_still_removes_renamed_names_but_nothing_else(tmp_path):
    run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}") for i in range(1, 6)]))
    blocker = tmp_path / "Movies" / "Movie 9 (2000)"
    blocker.write_text("in the way")                                          # one title fails to write
    movies = [movie(i, f"Movie {i}", year=2000) for i in range(1, 5)] + [movie(9, "Movie 9", year=2000)]
    result = run(tmp_path, FakeSource(movies=movies), safety_limit=0)
    files = tree(tmp_path)
    assert result["movies"]["failed"] == 1 and result["movies"]["deleted"] == 4   # the four renamed
    assert "Movies/Movie 1/Movie 1.strm" not in files and "Movies/Movie 5/Movie 5.strm" in files


def test_clean_up_opens_only_files_this_run_did_not_write(tmp_path, monkeypatch):
    run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}") for i in range(1, 30)]))
    real = core.owned_strm_url
    reads = []
    monkeypatch.setattr(core, "owned_strm_url", lambda path, prefixes: reads.append(path) or real(path, prefixes))
    result = run(tmp_path, FakeSource(movies=[movie(i, f"Movie {i}") for i in range(1, 29)]))
    assert [os.path.basename(p) for p in reads] == ["Movie 29.strm"] and result["movies"]["deleted"] == 1

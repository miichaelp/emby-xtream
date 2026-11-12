import os

import pytest

from strm_sync import core


@pytest.mark.parametrize("raw,expected", [
    ("|UK| The Office", "The Office"),
    ("┃FR┃ Lupin ┃4K┃", "Lupin"),
    ("EN - American Gigolo", "American Gigolo"),
    ("4K-A+ - Down Cemetery Road (2025) (GB)", "Down Cemetery Road (2025) (GB)"),
    ("D+ - 24 (2001) (US)", "24 (2001) (US)"),
    ("4K-AMZ - 007: Road to a Million", "007: Road to a Million"),
    ("NF - Solar Opposites (2020) (US)", "Solar Opposites (2020) (US)"),
    ("FBI - Most Wanted", "FBI - Most Wanted"),      # an acronym, not a provider tag
    ("NCIS - Los Angeles", "NCIS - Los Angeles"),
    ("Plain Title", "Plain Title"),
])
def test_clean_name_removes_provider_tags(raw, expected):
    assert core.clean_name(raw) == expected


def test_clean_name_user_terms_and_fallback():
    assert core.clean_name("Movie MULTI [VOSTFR]", ["multi", "[VOSTFR]"]) == "Movie"
    assert core.clean_name("|UK|") == "|UK|"          # never cleans a name down to nothing
    assert core.clean_name("|UK| Show", enabled=False) == "|UK| Show"


def test_sanitize_file_name():
    assert core.sanitize_file_name('What/If...? <2021>: "A" ') == "WhatIf... 2021 A"
    assert core.sanitize_file_name("  ..  ") == ""
    assert core.sanitize_file_name("CON") == "_CON"
    assert len(core.sanitize_file_name("x" * 400)) == 150


def test_year_language_and_folder():
    assert core.with_year("Heat", 1995) == "Heat (1995)"
    assert core.with_year("Heat (1995)", 1995) == "Heat (1995)"
    assert core.with_year("Heat", None) == "Heat"
    assert core.with_language("Heat (1995)", "fr") == "Heat (1995) [FR]"
    assert core.folder_name("Heat (1995)", "949") == "Heat (1995) [tmdbid=949]"
    assert core.folder_name("Heat (1995)", "0") == "Heat (1995)"
    assert core.folder_name("Heat (1995)", "949", use_ids=False) == "Heat (1995)"
    assert core.strip_id_suffix("Heat (1995) [tmdbid=949]") == "Heat (1995)"


def test_episode_file_base_strips_repeated_show_name():
    assert core.episode_file_base("American Gigolo", 1, 1, "EN - American Gigolo - S01E01") == "American Gigolo - S01E01 - EN"
    assert core.episode_file_base("Yago (2016)", 1, 33, "Yago - S01E33 - Episode 33") == "Yago (2016) - S01E33 - Episode 33"
    assert core.episode_file_base("Show", 0, 0, "") == "Show - S00E00"
    assert core.episode_file_base("Show", 2, 5, "Pilot: Part 1") == "Show - S02E05 - Pilot Part 1"


def test_urls():
    assert core.proxy_url("http://d:9191", "movie", "u-1") == "http://d:9191/proxy/vod/movie/u-1"
    assert core.proxy_url("http://d:9191", "episode", "u-1", "50 1") == "http://d:9191/proxy/vod/episode/u-1?stream_id=50%201"
    assert core.xtream_url("http://d:9191", "movie", "me", "p/w", 7, "mkv") == "http://d:9191/movie/me/p%2Fw/7.mkv"
    assert core.normalize_base_url(" http://d:9191/ ") == "http://d:9191"


def test_version_entries():
    assert core.version_entries(["A"]) == [""]
    assert core.version_entries(["A", "Provider B", "Provider B", ""]) == ["", " - Provider B", " - Provider B 2", " - Version 4"]
    assert core.version_entries(["A", "B", "C"], "number") == ["", " - Version 2", " - Version 3"]
    # Folder mode labels every version, first included.
    assert core.version_entries(["NETFLIX SERIES", "NETFLIX SERIES ⁴ᴷ ³⁸⁴⁰ᵖ", ""], "folder") == [
        " - NETFLIX SERIES", " - NETFLIX SERIES 4K 3840p", " - Version 3"]
    assert core.version_entries(["Same", "same"], "folder") == [" - Same", " - same 2"]


def test_folder_label_turns_raised_letters_into_normal_ones():
    assert core.folder_label("APPLE+ SERIES ⁴ᴷ ³⁸⁴⁰ᵖ ᴰᵒˡᵇʸ ᵛⁱˢⁱᵒⁿ") == "APPLE+ SERIES 4K 3840p Dolby vision"
    assert core.folder_label("AMAZON REALITY ᴰᵒˡᵇʸ ᴬᵁᴰᴵᴼ ⁴ᵏ ³⁸⁴⁰ᵖ") == "AMAZON REALITY Dolby AUDIO 4k 3840p"
    assert core.folder_label("|EN| Films: 4K") == "Films 4K"
    assert core.folder_label(None) == ""


def test_category_excluded():
    assert core.category_excluded("FR - Films", ["fr - *"])
    assert core.category_excluded("Adult XXX 4K", ["*xxx*"])
    assert not core.category_excluded("Drama", ["*xxx*", "FR - *"])
    assert not core.category_excluded(None, ["*"])


PREFIXES = core.owned_url_prefixes("http://d:9191")


def _strm(path, content="http://d:9191/proxy/vod/movie/u"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return str(path)


def test_ownership_is_decided_by_content(tmp_path):
    ours = _strm(tmp_path / "a.strm")
    xc = _strm(tmp_path / "b.strm", "http://d:9191/series/u/p/5.mkv")
    theirs = _strm(tmp_path / "c.strm", "http://other-server/movie.mkv")
    empty = _strm(tmp_path / "d.strm", "")
    assert core.is_owned_strm(ours, PREFIXES) and core.is_owned_strm(xc, PREFIXES)
    assert not core.is_owned_strm(theirs, PREFIXES)
    assert not core.is_owned_strm(empty, PREFIXES)
    assert not core.is_owned_strm(str(tmp_path / "missing.strm"), PREFIXES)
    assert not core.is_owned_strm(ours, ())


def test_write_if_changed(tmp_path):
    path = str(tmp_path / "x" / "f.strm")
    assert core.write_if_changed(path, "a") == "added"
    assert core.write_if_changed(path, "a") == "same"
    assert core.write_if_changed(path, "b") == "updated"
    assert open(path).read() == "b"
    assert os.listdir(tmp_path / "x") == ["f.strm"]
    dirs = set()
    second = str(tmp_path / "y" / "g.strm")
    assert core.write_if_changed(second, "c", dirs) == "added" and dirs == {str(tmp_path / "y")}
    assert core.write_if_changed(str(tmp_path / "y" / "h.strm"), "d", dirs) == "added"


def test_cleanup_removes_only_owned_orphans(tmp_path):
    root = tmp_path / "Movies"
    kept = _strm(root / "Kept" / "Kept.strm")
    orphan = _strm(root / "Gone" / "Gone.strm")
    with open(root / "Gone" / "Gone.nfo", "w") as fh:
        fh.write(core.movie_nfo("Gone", "5"))
    foreign = _strm(root / "Mine" / "Mine.strm", "http://my-nas/film.mkv")
    shared_orphan = _strm(root / "Shared" / "Shared.strm")
    user_file = root / "Shared" / "trailer.strm"
    _strm(user_file, "http://youtube/trailer")
    user_nfo = root / "Shared" / "notes.nfo"
    user_nfo.write_text("my notes")

    result = core.cleanup_orphans(str(root), {kept}, PREFIXES, 0)

    assert result.deleted == 2 and result.owned == 3
    assert os.path.exists(kept) and os.path.exists(foreign)
    assert not os.path.exists(orphan) and not (root / "Gone").exists()      # folder gone with its own .nfo
    assert not os.path.exists(shared_orphan)
    assert user_file.exists() and user_nfo.exists()                          # the user's files and folder stay
    assert sorted(result.removed_titles) == [os.path.join("Gone", "Gone.strm"), os.path.join("Shared", "Shared.strm")]


def test_cleanup_keeps_foreign_nfo_beside_an_orphan(tmp_path):
    root = tmp_path / "Movies"
    orphan = _strm(root / "Film" / "Film.strm")
    hand_written = root / "Film" / "Film.nfo"
    hand_written.write_text("<movie><title>mine</title></movie>")
    core.cleanup_orphans(str(root), {str(root / "x.strm")}, PREFIXES, 0)
    assert not os.path.exists(orphan)
    assert hand_written.exists()


def test_cleanup_safety_limit_and_empty_run(tmp_path):
    root = tmp_path / "Shows"
    files = [_strm(root / f"S{i}" / "Season 01" / f"S{i} - S01E01.strm") for i in range(20)]

    # 15 of 20 would go: over a 20% limit, so nothing is removed.
    blocked = core.cleanup_orphans(str(root), set(files[:5]), PREFIXES, 0.2)
    assert blocked.deleted == 0 and blocked.orphans == 15 and "safety limit" in blocked.skipped_reason
    assert all(os.path.exists(f) for f in files)

    # A run that wrote nothing proves nothing.
    nothing = core.cleanup_orphans(str(root), set(), PREFIXES, 0)
    assert nothing.deleted == 0 and nothing.skipped_reason

    # 2 of 20 is within the limit; empty show folders are pruned up to the root.
    allowed = core.cleanup_orphans(str(root), set(files[2:]), PREFIXES, 0.2)
    assert allowed.deleted == 2
    assert not (root / "S0").exists() and (root / "S5").exists() and root.exists()


def test_cleanup_small_library_is_not_guarded_by_ratio(tmp_path):
    root = tmp_path / "Movies"
    a = _strm(root / "A" / "A.strm")
    b = _strm(root / "B" / "B.strm")
    assert core.cleanup_orphans(str(root), {a}, PREFIXES, 0.2).deleted == 1
    assert not os.path.exists(b)


def test_show_folder_with_only_our_tvshow_nfo_is_removed(tmp_path):
    root = tmp_path / "Shows"
    ep = _strm(root / "Show" / "Season 01" / "Show - S01E01.strm")
    (root / "Show" / "tvshow.nfo").write_text(core.show_nfo("Show", "9"))
    core.cleanup_orphans(str(root), {str(root / "other.strm")}, PREFIXES, 0)
    assert not os.path.exists(ep) and not (root / "Show").exists()


def test_delete_all_owned(tmp_path):
    root = tmp_path / "Movies"
    for i in range(30):
        _strm(root / f"M{i}" / f"M{i}.strm")
    foreign = _strm(root / "Mine" / "Mine.strm", "http://nas/x.mkv")
    result = core.delete_all_owned(str(root), PREFIXES)
    assert result.deleted == 30                      # no safety limit on a deliberate delete
    assert os.path.exists(foreign) and os.listdir(root) == ["Mine"]


def test_cleanup_removes_renamed_files_past_the_safety_limit(tmp_path):
    root = tmp_path / "Movies"
    old = [_strm(root / f"M{i}" / f"M{i}.strm", f"http://d:9191/proxy/vod/movie/u{i}") for i in range(20)]
    new = [_strm(root / f"M{i}" / f"M{i} - 4K.strm", f"http://d:9191/proxy/vod/movie/u{i}") for i in range(20)]
    result = core.cleanup_orphans(str(root), set(new), PREFIXES, 0.2,
                                  written_urls={f"http://d:9191/proxy/vod/movie/u{i}" for i in range(20)})
    assert result.renamed == 20 and result.deleted == 20 and not result.skipped_reason
    assert not any(os.path.exists(p) for p in old) and all(os.path.exists(p) for p in new)


def test_cleanup_stops_when_asked(tmp_path):
    root = tmp_path / "Shows"
    for i in range(4100):
        _strm(root / "S" / f"e{i}.strm")
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] >= 2                     # stop at the second check (4000 files in)

    seen = []
    result = core.cleanup_orphans(str(root), {str(root / "x.strm")}, PREFIXES, 0, should_stop=stop,
                                  progress=lambda stage, n: seen.append((stage, n)))
    assert result.deleted == 0 and result.skipped_reason == "the sync was stopped"
    assert seen == [("scan", 2000), ("scan", 4000)]


def test_delete_all_stops_part_way_through_deleting(tmp_path):
    root = tmp_path / "Shows"
    for i in range(1200):
        _strm(root / "S" / f"e{i}.strm")
    reports = []

    def report(stage, n):
        reports.append((stage, n))

    result = core.delete_all_owned(str(root), PREFIXES, should_stop=lambda: ("delete", 1000) in reports,
                                   progress=report)
    # The stop is asked for after the report at 1,000; the file being deleted then is the last.
    assert result.deleted == 1001 and result.skipped_reason == "the sync was stopped"
    assert len(os.listdir(root / "S")) == 199


USER_TAGS = ["4K-", "4K-EN - ", "4K-MRVL - ", "4K-TOP - ", "4K-AMZ - ", "4K-A+ - ", "4K-NF -", "4k-MAX -",
             "4K-SC - ", "4K -SC-", "4K-DE-DV - ", "4K-D+ - ", "4M-", "PRMT - ", "AMZ -", "Sky - ", "A+ - ",
             "D+ - ", "DSC+ - ", "DSC+S - ", "TOP - ", "SHWT - ", "NICK - ", "MAX - ", "NF-DO - ", "UNV - ",
             "EN-DO - ", "EN- ", "P+ - ", "#", "SC -", "SC- ", "SC-DO - ", "MRVL - ", "GR -"]


@pytest.mark.parametrize("raw,expected", [
    ("4K-NF - The Witcher (US)", "The Witcher (US)"),
    ("4K-MAX - Youth (2026) (GB)", "Youth (2026) (GB)"),
    ("4K -SC- Outside (2026) (GB)", "Outside (2026) (GB)"),
    ("4K-SC - Outside", "Outside"),
    ("4K-DE-DV - Dark", "Dark"),
    ("NF-DO - Boots (2025) (US)", "Boots (2025) (US)"),
    ("DSC+S - Gold Rush", "Gold Rush"),
    ("DSC+ - Gold Rush", "Gold Rush"),
    ("EN-DO - Planet Earth", "Planet Earth"),
    ("EN- Planet Earth", "Planet Earth"),
    ("SC-DO - Outside", "Outside"),
    ("SC- Outside", "Outside"),
    ("# Alive", "Alive"),
    ("Sky - High (2020)", "High (2020)"),                 # a start tag, as asked
    ("Under the Sky - Part 2", "Under the Sky - Part 2"),  # in the middle: untouched
    ("En Vivo", "En Vivo"),                              # "EN-" needs its dash
    ("Maximum Overdrive", "Maximum Overdrive"),          # "MAX - " needs its dash
    ("4K-", "4K-"),                                      # never cleaned down to nothing
])
def test_user_start_tags(raw, expected):
    assert core.clean_name(raw, prefix_patterns=core.prefix_tag_patterns(USER_TAGS)) == expected


def test_default_start_tags_are_the_user_list():
    import json

    manifest = json.load(open(os.path.join(os.path.dirname(core.__file__), "plugin.json"), encoding="utf-8"))
    field = next(f for f in manifest["fields"] if f["id"] == "prefix_tags")
    tags = field["default"].splitlines()
    assert "4K -SC-" in tags and "#" in tags and "NF-DO - " in tags
    assert core.clean_name("4K-AMZ - The Boys", prefix_patterns=core.prefix_tag_patterns(tags)) == "The Boys"

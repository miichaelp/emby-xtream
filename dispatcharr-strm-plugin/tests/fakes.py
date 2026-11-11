import logging


def version(stream_id, provider="Provider A", ext="mkv"):
    return {"stream_id": str(stream_id), "ext": ext, "provider": provider, "account_id": 1}


def movie(mid, name, year=None, tmdb=None, versions=None, category="Films", language=""):
    return {"id": mid, "uuid": f"m-{mid}", "name": name, "year": year, "tmdb_id": tmdb, "language": language,
            "category": category, "versions": versions if versions is not None else [version(100 + mid)]}


def episode(eid, season, number, title="", versions=None):
    return {"id": eid, "uuid": f"e-{eid}", "title": title, "season": season, "episode": number,
            "versions": versions if versions is not None else [version(500 + eid)]}


def show(sid, name, episodes, year=None, tmdb=None, category="Shows", language=""):
    return {"id": sid, "uuid": f"s-{sid}", "name": name, "year": year, "tmdb_id": tmdb, "language": language,
            "category": category, "episodes": episodes}


class FakeSource:
    def __init__(self, movies=(), shows=(), pending=()):
        self.movies = list(movies)
        self.shows = list(shows)
        self.pending = list(pending)
        self.fetch_results = {}
        self.fetched = []
        self.fail_reading = False

    def _pick(self, items, only_ids):
        return [i for i in items if only_ids is None or i["id"] in only_ids]

    def count_movies(self, only_ids=None):
        return len(self._pick(self.movies, only_ids))

    def iter_movies(self, only_ids=None):
        for item in self._pick(self.movies, only_ids):
            if item.get("boom"):
                raise RuntimeError("database went away")
            yield item

    def count_series(self, only_ids=None):
        return len(self._pick(self.shows, only_ids))

    def iter_series(self, only_ids=None):
        yield from self._pick(self.shows, only_ids)

    def series_needing_episodes(self, known, only_ids=None):
        return [p for p in self.pending if only_ids is None or p["series_id"] in only_ids]

    def fetch_episodes(self, item):
        self.fetched.append(item["relation_id"])
        outcome = self.fetch_results.get(item["relation_id"], ("ok", ""))
        if isinstance(outcome, list):          # one answer per attempt
            outcome = outcome.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeControl:
    def __init__(self, stop_after=None):
        self.calls = []
        self.stop_after = stop_after   # stop once this many progress updates were seen

    def should_stop(self):
        return self.stop_after is not None and len(self.calls) >= self.stop_after

    def progress(self, kind, phase, done, total, stats=None, force=False):
        self.calls.append((kind, phase, done, total))


LOG = logging.getLogger("strm_sync.tests")

"""Stand-in for Dispatcharr's core.xtream_codes.Client. Tests set `answers` per series id."""

requests = []        # (kind, series_id) per request "sent"
answers = {}         # series id -> dict to return, or an Exception to raise


class Client:
    def __init__(self, server_url, username, password, user_agent=None):
        self.server_url, self.username, self.password = server_url, username, password
        self.server_info = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def authenticate(self):
        requests.append(("login", None))
        self.server_info = {"user_info": {}}

    def get_series_info(self, series_id):
        if not self.server_info:
            self.authenticate()
        requests.append(("series_info", series_id))
        answer = answers.get(series_id, {"episodes": {"1": [{"id": 1, "episode_num": 1}]}})
        if isinstance(answer, Exception):
            raise answer
        return answer

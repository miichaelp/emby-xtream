import os


class _Config:
    enabled = os.environ.get("FAKEPROJECT_ENABLED") == "1"
    settings = {"output_dir": os.environ.get("FAKEPROJECT_OUT", ""), "base_url": "http://d:9191"}


class _Query:
    def first(self):
        if os.environ.get("FAKEPROJECT_LEAK") == "1":
            # What Dispatcharr's XC client logs on a refused request.
            import logging

            logging.getLogger("core.xtream_codes").error(
                "XC API Request failed: 403 Client Error: Forbidden for url: "
                "http://p/player_api.php?username=leakuser1&password=leakpw99&action=get_series_info")
        return _Config()


class _Manager:
    def filter(self, **_kwargs):
        return _Query()


class PluginConfig:
    objects = _Manager()

import os


class _Config:
    enabled = os.environ.get("FAKEPROJECT_ENABLED") == "1"
    settings = {"output_dir": os.environ.get("FAKEPROJECT_OUT", ""), "base_url": "http://d:9191"}


class _Query:
    def first(self):
        return _Config()


class _Manager:
    def filter(self, **_kwargs):
        return _Query()


class PluginConfig:
    objects = _Manager()

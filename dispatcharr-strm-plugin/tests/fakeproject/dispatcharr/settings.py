import json
import os
import sys

# What the runner told Dispatcharr about itself; test_runner.py reads this back.
with open(os.environ["FAKEPROJECT_ARGV_FILE"], "w") as fh:
    json.dump(sys.argv, fh)

SECRET_KEY = "test"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
INSTALLED_APPS = []
USE_TZ = True

# Like Dispatcharr's own logging: handlers that write to stdout/stderr, made at setup.
LOGGING = {
    "version": 1,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

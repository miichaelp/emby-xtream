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

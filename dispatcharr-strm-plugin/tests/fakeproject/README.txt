A stand-in for Dispatcharr's project folder, just enough for runner.py to start:
Django settings, a PluginConfig lookup and a Redis client. test_runner.py points
runtime.spawn() at it and checks that the real background process runs.

Stand-ins for the Dispatcharr models that source.py reads, with the same module
paths and field names (copied by hand from Dispatcharr's apps/m3u/models.py and
apps/vod/models.py). They let the catalogue queries run on SQLite in tests. They
are not Dispatcharr: a renamed field upstream would not be caught here.

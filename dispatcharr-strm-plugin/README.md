# STRM Sync for Dispatcharr

A Dispatcharr plugin that writes `.strm` files for Dispatcharr's movies and series, so Emby,
Jellyfin or Plex can add them as normal Movie and TV Show libraries. It runs by itself after each
M3U / VOD refresh, and has a dashboard page with progress, history and a Stop button.

It does inside Dispatcharr what the Movies and Series sync of the Emby *Xtream Tuner* plugin does
from the Emby side, with the same folder layout. Because it reads Dispatcharr's own database, a
sync makes no requests to Dispatcharr's API and cannot overload it.

> **Not yet run against a live Dispatcharr.** The sync logic, clean-up, stop and dashboard are
> covered by automated tests, but the part that reads Dispatcharr's database was tested against
> stand-in tables with the same field names, not a real server. Try it on a new, empty folder
> first.

## What it does

- **Movies**: `Movies/Title (Year) [tmdbid=1]/Title (Year) [tmdbid=1].strm`
- **Series**: `Shows/Show/Season 01/Show - S01E01 - Title.strm`. Specials go to `Season 00`.
- **Versions**: a title that more than one provider carries gets one file per provider, named
  after the provider (`Title - Backup Provider.strm`), which Emby shows as versions.
- **Episode lists**: Dispatcharr only loads a show's episodes when someone opens it. The plugin
  fetches the missing ones, one show at a time with a pause between requests, and again when the
  provider changes a show.
- **Only what changed is written.** A file that already holds the right address is left alone,
  so the media server does not rescan it.
- **Clean-up**: files for titles Dispatcharr no longer has are removed. Only `.strm` files this
  plugin wrote are ever deleted; your own files in the same folders stay. With more than 10 files,
  a sync never removes more than the safety limit (20% by default) at once. A sync that was
  stopped, or in which a title failed, removes nothing.
- **Names**: provider tags such as `|UK|`, `EN - ` or `4K-NF - ` are removed, plus your own terms.
- **Categories**: VOD categories switched off in Dispatcharr are left out. You can leave out more
  by name, with `*` as a wildcard.
- **Optional**: `.nfo` files with the TMDB id, a subfolder per category, and a library scan in
  Emby or Jellyfin after a sync that changed files.

## Install

1. In Dispatcharr open **Plugins**, click **Import** and upload `strm_sync.zip`. Enable the plugin.
2. **Restart Dispatcharr once.** Its background workers only load a plugin when they start, and
   they do the syncing.
3. Make the STRM folder visible to your media server. The plugin writes inside the Dispatcharr
   container, to `/data/strm` by default. Map that path to a folder the media server can read, for
   example in `docker-compose.yml`:

   ```yaml
   volumes:
     - "C:/Emby Media/STRM:/data/strm"
   ```

4. In the plugin's settings set **Dispatcharr address as your media server reaches it**, for
   example `http://192.168.0.11:9191`. This address is written into every file, so it has to work
   from the media server. Save.
5. Open the dashboard with the **Docs** link on the plugin card, or go to
   `http://<dispatcharr>/logos/strm_sync/index.html`, and click **Sync movies & series**.
6. In Emby add `<folder>/Movies` as a Movies library and `<folder>/Shows` as a TV Shows library.

The first series sync asks the provider once per show for its episode list, so it can take a
while on a large catalogue. You can stop it at any time and it carries on next time. To spread it
out, set **Most shows to fetch per sync**.

### Coming from the Emby Xtream Tuner plugin

Use one or the other for movies and series, not both: two writers in the same folder undo each
other. Turn off **Sync Movies** and **Sync Series** in Xtream Tuner (Live TV can stay on), and
point this plugin at a new, empty folder. Folder names can differ from the old ones (year and
`[tmdbid=…]` are added here), so reusing the old folder would leave duplicates.

## The dashboard

Dispatcharr has no way for a plugin to add a page, so the dashboard is a single static page that
Dispatcharr's web server hands out from its `logos` folder. The page itself contains no data. It
uses the login your browser already has for Dispatcharr and asks the plugin for everything
through Dispatcharr's own admin-only API, so sign in to Dispatcharr in the same browser first.

It shows library counts, the last 10 syncs, live progress with **Stop sync**, failed items with
**Retry**, warnings from the last sync, the files it removed, and the plugin's log (passwords and
API keys removed). Settings stay on Dispatcharr's Plugins page.

## Stream addresses

- **Dispatcharr proxy** (default): `http://<dispatcharr>/proxy/vod/movie/<id>`. No login is
  written into the files. Dispatcharr's network access settings must allow the media server to
  reach streams.
- **Xtream URL**: `http://<dispatcharr>/movie/<user>/<password>/<id>.mkv`. Dispatcharr then shows
  which user is watching. The user's Xtream password is written into the files.

Changing the address later rewrites the files of every title still in the catalogue on the next
sync. Files that still hold the old address are no longer recognised as the plugin's and are left
in place.

## Development

```bash
pip install pytest fakeredis django celery
```

```bash
python -m pytest tests
```

`strm_sync/core.py` and `engine.py` have no Dispatcharr imports. `source.py` holds the database
queries, `runtime.py` the lock, progress and saved state, `tasks.py` the Celery tasks and the hook
on Dispatcharr's `refresh_vod_content` task.

Build the zip to import:

```bash
bash build.sh
```

# STRM Sync for Dispatcharr

A Dispatcharr plugin that writes `.strm` files for Dispatcharr's movies and series, so Emby,
Jellyfin or Plex can add them as normal Movie and TV Show libraries. It syncs by hand, after each
M3U refresh, on a schedule, or any mix of those, and has a dashboard page with progress, history
and a Stop button.

It does inside Dispatcharr what the Movies and Series sync of the Emby *Xtream Tuner* plugin does
from the Emby side, with the same folder layout. Because it reads Dispatcharr's own database, a
sync makes no requests to Dispatcharr's API and cannot overload it.

> **Not yet run against a live Dispatcharr.** The sync logic, clean-up, stop and dashboard are
> covered by automated tests, but the part that reads Dispatcharr's database was tested against
> stand-in tables with the same field names, not a real server. Try it on a new, empty folder
> first.

## What it does

- **When it syncs** (plugin settings, *When to sync*):
  - **By hand**: always, with the buttons on the plugin card or the dashboard.
  - **After each M3U refresh**: on by default. Starts once Dispatcharr has finished loading the
    account's movies and series.
  - **On a schedule**: every few hours (counted from the last sync, including one run by hand),
    or once a day at a set time in the time zone from Dispatcharr's system settings.

  A refresh and a schedule can both be on. If a sync is already running when another is due, the
  second one is skipped (a refresh that finishes meanwhile still gets one sync afterwards).

- **Movies**: `Movies/Title (Year) [tmdbid=1]/Title (Year) [tmdbid=1].strm`
- **Series**: `Shows/Show/Season 01/Show - S01E01 - Title.strm`. Specials go to `Season 00`.
- **Versions**: a title that more than one provider carries, or one provider lists in several
  folders, gets one file per copy, which Emby shows as versions. **Name versions by** chooses the
  label: the provider folder (`Title - NETFLIX SERIES 4K 3840p.strm`, raised letters such as
  `⁴ᴷ ³⁸⁴⁰ᵖ` turned into normal ones, clean-up terms not applied), the provider name, or a
  number.
- **Episode titles** in file names can be turned off (`Show - S01E01.strm`); media servers take
  titles from their own metadata, and some providers send them in another language.
- **Renaming**: changing either setting renames files. The next sync writes the new names and
  removes the old ones, unless that is more than the removal safety limit allows: then set the
  limit to 0 for one sync.
- **Episode lists**: Dispatcharr only loads a show's episodes from the IPTV provider when someone
  opens the show. The plugin asks the provider for the missing ones, one show at a time with a
  pause between requests, just before writing that show, and again when the provider changes a
  show. Each show is one request to the provider: the episode list only, without the separate
  login request Dispatcharr sends first when someone opens a show (providers that limit requests
  refused that second, back-to-back request with HTTP 403). The answer is handed to Dispatcharr,
  which stores it as usual. A request the provider refuses (HTTP 403 or 429) is tried up to five more
  times, after 1, 2, 3, 5 and 8 seconds: one provider refused about 45% of all requests at
  random, however far apart, and answered when asked again. Other errors are retried once after
  10 seconds. The failed list shows the reason. Turn **Ask the IPTV provider for missing episode lists** off to keep the
  plugin away from the provider entirely: then only shows Dispatcharr already has episodes for get
  files. Movies never need the provider.
- **Log**: the dashboard's log, Dispatcharr's container log (lines start with `STRM Sync:`), and
  `strm-sync.log` at the top of the STRM folder, next to `Movies` and `Shows`.
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
2. **Restart Dispatcharr once.** Syncing by hand works without it, but the sync after an M3U
   refresh and the schedule are started from Dispatcharr's background workers, which only load a
   plugin when they start. Restart again after updating the plugin.
3. Map a folder on your computer into the container, in `docker-compose.yml` under `volumes:`,
   and recreate the container (`docker compose up -d`):

   ```yaml
   volumes:
     - "C:/Users/you/STRMSync:/strm"
   ```

   The left side is the folder on your computer, the right side what Dispatcharr calls it. Set
   **STRM folder** in the plugin settings to the right side (`/strm`, the default). Files written
   there appear in the folder on your computer.

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

`strm_sync/core.py`, `engine.py` and `schedule.py` have no Dispatcharr imports.

A sync runs as its own process (`runner.py`), not as a Celery task: Dispatcharr loads plugins only
in its Celery worker's child processes, so the parent that receives tasks rejects plugin tasks as
unregistered. The runner presents itself to Dispatcharr as a Celery process, so it gets the normal
database backend and skips the web process's start-up work. What the plugin does take from Celery
is the `task_postrun` signal in the worker children: after Dispatcharr's `refresh_vod_content` it
starts a sync, and after any task (Dispatcharr scans files every 20 seconds) it checks the
schedule, at most once a minute. The background process's own output goes to `runner.log`, which
the dashboard's log view includes.

`source.py` holds the database queries, `runtime.py` the lock, progress, saved state and process
start, `tasks.py` the sync and delete jobs and the Celery hook.

Build the zip to import:

```bash
bash build.sh
```

"""Pure helpers for STRM Sync: naming, URLs, file ownership and orphan cleanup.

Nothing here imports Django or Dispatcharr, so it can be tested on its own.
The naming matches the Emby "Xtream Tuner" plugin, so a library that plugin
wrote keeps its folders when this one takes over.
"""

import fnmatch
import os
import re
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

INVALID_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MULTI_SPACE = re.compile(r"\s{2,}")
# One or more |XX| / ┃XX┃ / │XX│ tags at the start, then the same tags anywhere.
BOX_PREFIX = re.compile(r"^(\s*[┃│|][^┃│|]+[┃│|]\s*)+")
BOX_ANYWHERE = re.compile(r"\s*[┃│|][^┃│|]+[┃│|]\s*")
# "EN - ", "4K-NF - ", "D+ - ", "A+ - ": a short provider label before " - ".
# Limited to labels that are two capitals, or contain a digit, "+" or "-", so
# show acronyms such as "FBI - Most Wanted" are left alone.
DASH_PREFIX = re.compile(r"^(?:[A-Z]{2}|(?=[A-Z0-9+\-]*[0-9+\-])[A-Z0-9+\-]{2,8})\s+-\s+")
YEAR_IN_TITLE = re.compile(r"\((\d{4})\)")
ID_SUFFIX = re.compile(r" \[(?:tmdbid|tvdbid|imdbid)=[^\]]+\]$", re.IGNORECASE)

# Windows refuses these as file or folder names, with or without an extension.
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def sanitize_file_name(name):
    """A name safe as one path component on Windows and Linux."""
    if not name or not str(name).strip():
        return ""
    result = INVALID_FILE_CHARS.sub("", str(name))
    result = MULTI_SPACE.sub(" ", result).strip(". ")
    if result.split(".")[0].upper() in RESERVED_NAMES:
        result = "_" + result
    # Leave room for " - S01E01 - title - provider.strm" under the 255 byte limit.
    return result[:150].rstrip(". ")


def parse_terms(text):
    """One term per line (commas also split). Blank entries dropped."""
    if not text:
        return []
    return [t.strip() for t in re.split(r"[\r\n,]+", str(text)) if t.strip()]


def clean_name(name, remove_terms=None, enabled=True):
    """Remove provider tags (|UK|, "EN - ", "4K-NF - ") and the user's own terms."""
    if not name or not str(name).strip():
        return name or ""
    original = str(name).strip()
    if not enabled:
        return original
    result = BOX_PREFIX.sub("", original)
    result = BOX_ANYWHERE.sub(" ", result)
    result = DASH_PREFIX.sub("", result.strip())
    for term in remove_terms or []:
        result = re.sub(re.escape(term), "", result, flags=re.IGNORECASE)
    result = MULTI_SPACE.sub(" ", result).strip()
    return result or original


def with_year(name, year):
    """Append "(year)" unless the title already carries one. Emby matches on it."""
    try:
        year = int(year)
    except (TypeError, ValueError):
        return name
    if year < 1870 or year > 2200 or YEAR_IN_TITLE.search(name or ""):
        return name
    return f"{name} ({year})"


def with_language(name, language):
    """Dispatcharr keeps one row per language; tag the name so they do not share a folder."""
    if not language or not name:
        return name
    tag = f"[{str(language).upper()}]"
    return name if name.upper().endswith(tag) else f"{name} {tag}"


def valid_tmdb_id(value):
    try:
        return int(str(value).strip()) > 0
    except (TypeError, ValueError):
        return False


def folder_name(title, tmdb_id=None, use_ids=True):
    """Folder for a movie or show, optionally with Emby's [tmdbid=N] hint."""
    name = sanitize_file_name(title)
    if name and use_ids and valid_tmdb_id(tmdb_id):
        return f"{name} [tmdbid={int(str(tmdb_id).strip())}]"
    return name


def strip_id_suffix(name):
    return ID_SUFFIX.sub("", name or "")


def strip_episode_title_duplicate(title, series_name, season, episode):
    """Drop a repeated "Show - S01E01" from an episode title some providers embed."""
    result = (title or "").strip()
    if not result:
        return result
    if series_name:
        result = re.sub(
            r"[\s\-]*" + re.escape(series_name) + r"[\s\-]*S\d+E\d+[\s\-]*",
            "",
            result,
            flags=re.IGNORECASE,
        ).strip("- ")
    code = f"S{season:02d}E{episode:02d}"
    idx = result.upper().find(code)
    if idx >= 0:
        result = result[idx + len(code):].strip("- ")
    return result


def episode_file_base(series_name, season, episode, title):
    """"Show - S01E01 - Title" (no extension)."""
    cleaned = sanitize_file_name(strip_episode_title_duplicate(title, series_name, season, episode))
    # A title that is only "Episode 3" or the code itself adds nothing.
    suffix = f" - {cleaned}" if cleaned else ""
    return f"{series_name} - S{season:02d}E{episode:02d}{suffix}"


def category_excluded(category_name, patterns):
    """True when the category matches one of the user's patterns (* wildcards, any case)."""
    if not patterns or not category_name:
        return False
    name = category_name.strip().lower()
    return any(fnmatch.fnmatchcase(name, p.strip().lower()) for p in patterns)


def normalize_base_url(url):
    return (url or "").strip().rstrip("/")


def proxy_url(base_url, content_type, uuid, stream_id=None):
    """Dispatcharr VOD proxy URL. Without stream_id Dispatcharr picks the best provider."""
    url = f"{base_url}/proxy/vod/{content_type}/{uuid}"
    if stream_id not in (None, ""):
        url += f"?stream_id={quote(str(stream_id), safe='')}"
    return url


def xtream_url(base_url, content_path, username, password, item_id, extension):
    """Dispatcharr's Xtream-style URL; Dispatcharr then shows which user is watching."""
    return (
        f"{base_url}/{content_path}/{quote(str(username), safe='')}/"
        f"{quote(str(password), safe='')}/{item_id}.{extension or 'mp4'}"
    )


def version_entries(versions, numbered=False):
    """Suffix for each version of one title: "" for the first, " - Label" for the rest.

    versions: list of provider names, best first. Duplicate labels get a number.
    """
    out = []
    seen = {}
    for index, provider in enumerate(versions):
        if index == 0:
            out.append("")
            continue
        label = sanitize_file_name(provider) if not numbered else ""
        if not label:
            label = f"Version {index + 1}"
        key = label.lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            label = f"{label} {seen[key]}"
        out.append(f" - {label}")
    return out


def owned_url_prefixes(base_url):
    """What every URL this plugin writes starts with. Ownership is decided by content."""
    base = normalize_base_url(base_url)
    if not base:
        return ()
    return (f"{base}/proxy/vod/", f"{base}/movie/", f"{base}/series/")


def is_owned_strm(path, prefixes):
    """A .strm is ours only if it holds a URL we would have written.

    Unreadable, empty or anything else means foreign, and foreign is kept.
    """
    if not prefixes:
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(2048).strip()
    except OSError:
        return False
    return bool(head) and head.startswith(tuple(prefixes))


def write_if_changed(path, content):
    """Write the file unless it already holds exactly this. Returns "added", "updated" or "same"."""
    existed = os.path.isfile(path)
    if existed:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                if fh.read() == content:
                    return "same"
        except (OSError, UnicodeDecodeError):
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    os.replace(tmp, path)
    return "updated" if existed else "added"


def movie_nfo(title, tmdb_id, year=None):
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<movie>", f"  <title>{xml_escape(title)}</title>"]
    if year:
        lines.append(f"  <year>{int(year)}</year>")
    lines.append(f'  <uniqueid type="tmdb" default="true">{int(str(tmdb_id).strip())}</uniqueid>')
    lines.append("</movie>")
    return "\n".join(lines) + "\n"


def show_nfo(title, tmdb_id):
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<tvshow>\n'
        f"  <title>{xml_escape(title)}</title>\n"
        f'  <uniqueid type="tmdb" default="true">{int(str(tmdb_id).strip())}</uniqueid>\n'
        "</tvshow>\n"
    )


NFO_MARKER = '<uniqueid type="tmdb" default="true">'


def is_owned_nfo(path, strm_names_in_dir):
    """An .nfo is ours when it has one of the two shapes we write.

    {name}.nfo beside a {name}.strm we own, or tvshow.nfo; and it carries our uniqueid line.
    """
    base = os.path.basename(path)
    stem = base[:-4]
    if base.lower() != "tvshow.nfo" and stem not in strm_names_in_dir:
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return NFO_MARKER in fh.read(4096)
    except OSError:
        return False


def _path_key(path):
    """Paths compare without case: the STRM folder is often a Windows or SMB share.

    On such a share a file we wrote as "Heat/Heat.strm" can come back from a
    directory listing as "heat/Heat.strm". Treating those as different would
    make our own fresh file look like an orphan.
    """
    return os.path.abspath(path).lower()


class CleanupResult:
    def __init__(self):
        self.deleted = 0
        self.owned = 0
        self.orphans = 0
        self.skipped_reason = ""
        self.removed_titles = []


def cleanup_orphans(root, written_paths, prefixes, safety_ratio, written_nfos=None):
    """Delete .strm files we own under root that this run did not write.

    written_paths: every .strm path this run wrote or confirmed (absolute).
    safety_ratio: with more than 10 owned files, refuse to delete more than
    this share at once (0.2 = 20%); 0 turns the guard off.

    Only files are deleted, one at a time, and only ours. A folder is removed
    only once it is empty. Never a recursive delete: a folder match is not
    proof of ownership.
    """
    result = CleanupResult()
    if not os.path.isdir(root):
        return result
    if not written_paths:
        # Absence of data is never evidence of deletion.
        result.skipped_reason = "nothing was written this run"
        return result

    written = {_path_key(p) for p in written_paths}
    kept_nfos = {_path_key(p) for p in (written_nfos or ())}
    orphans = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(".strm"):
                continue
            path = os.path.join(dirpath, name)
            if not is_owned_strm(path, prefixes):
                continue
            result.owned += 1
            if _path_key(path) not in written:
                orphans.append(path)

    result.orphans = len(orphans)
    if not orphans:
        return result
    if safety_ratio and result.owned > 10 and len(orphans) / result.owned > safety_ratio:
        result.skipped_reason = (
            f"{len(orphans)} of {result.owned} files would be removed, more than the safety limit "
            f"of {int(round(safety_ratio * 100))}%"
        )
        return result

    touched_dirs = set()
    titles = []
    for path in orphans:
        directory = os.path.dirname(path)
        stem = os.path.basename(path)[:-5]
        try:
            os.remove(path)
        except OSError:
            continue
        result.deleted += 1
        touched_dirs.add(directory)
        titles.append(os.path.relpath(path, root))
        # The movie's own .nfo goes with its .strm.
        nfo = os.path.join(directory, stem + ".nfo")
        if os.path.isfile(nfo) and _path_key(nfo) not in kept_nfos and is_owned_nfo(nfo, {stem}):
            _remove_quietly(nfo)

    result.removed_titles = sorted(titles)[:200]
    _prune_empty_dirs(root, touched_dirs, kept_nfos)
    return result


def delete_all_owned(root, prefixes):
    """Delete every .strm we own under root (the "delete all content" button).

    Deliberate, so the safety limit is off. Foreign files and their folders stay.
    """
    # A "written" set that matches nothing makes every owned file an orphan.
    return cleanup_orphans(root, {os.path.join(root, ".none")}, prefixes, 0)


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _prune_empty_dirs(root, start_dirs, kept_nfos):
    """Remove folders left empty, walking up to (never including) root.

    A show folder holding only our tvshow.nfo counts as empty.
    """
    root_abs = os.path.abspath(root)
    pending = sorted({os.path.abspath(d) for d in start_dirs}, key=len, reverse=True)
    seen = set()
    while pending:
        directory = pending.pop(0)
        if directory in seen or directory == root_abs or not directory.startswith(root_abs + os.sep):
            continue
        seen.add(directory)
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        if entries == ["tvshow.nfo"]:
            nfo = os.path.join(directory, "tvshow.nfo")
            if _path_key(nfo) not in kept_nfos and is_owned_nfo(nfo, set()):
                _remove_quietly(nfo)
                entries = []
        if entries:
            continue
        try:
            os.rmdir(directory)
        except OSError:
            continue
        pending.append(os.path.dirname(directory))
        pending.sort(key=len, reverse=True)

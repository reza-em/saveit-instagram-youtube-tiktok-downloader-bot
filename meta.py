"""Track metadata: key-less enrichment (iTunes / MusicBrainz), ID3/M4A tag embedding via ffmpeg, caption + hashtag building."""
import os, re, json, time, logging, threading
import requests
import engine

log = logging.getLogger("meta")
UA = "SaveIt/1.0 ( https://t.me/saveit_downloader_bot )"
FIELDS = ("album", "year", "genre")
_cache = {}
_clock = threading.Lock()
_last_mb = [0.0]

def _n(s):
    return engine._norm(s)

def _year(v):
    m = re.search(r"(19|20)\d{2}", str(v or ""))
    return m.group(0) if m else ""

# ------------------------------------------------------------------ lookups
def _itunes(title, artist):
    import recognize
    res = recognize.itunes_search(("%s %s" % (artist, title)).strip(), 10)
    kt, ka = _n(title), _n(artist)
    for c in res:
        if _n(c["title"]) == kt and (not ka or ka in _n(c["artist"]) or _n(c["artist"]) in ka):
            return {"album": c.get("album") or "", "year": _year(c.get("year")), "genre": c.get("genre") or "", "cover": c.get("cover")}
    return {}

def _musicbrainz(title, artist):
    with _clock:                                             # MusicBrainz asks for <= 1 request/second
        wait = 1.1 - (time.time() - _last_mb[0])
        if wait > 0: time.sleep(wait)
        _last_mb[0] = time.time()
    q = 'recording:"%s"' % title.replace('"', " ")
    if artist: q += ' AND artist:"%s"' % artist.replace('"', " ")
    r = requests.get("https://musicbrainz.org/ws/2/recording/", params={"query": q, "fmt": "json", "limit": 3}, headers={"User-Agent": UA}, timeout=12)
    for x in (r.json().get("recordings") or []):
        if _n(x.get("title")) != _n(title):
            continue
        rel = (x.get("releases") or [{}])[0]
        tags = sorted(x.get("tags") or [], key=lambda t: -t.get("count", 0))
        return {"album": rel.get("title") or "", "year": _year(x.get("first-release-date") or rel.get("date")),
                "genre": (tags[0]["name"].title() if tags else "")}
    return {}

def lookup(title, artist):
    """Missing album/year/genre (+cover) from free services; never raises. Cached per (title, artist)."""
    key = (_n(title), _n(artist))
    if key in _cache:
        return dict(_cache[key])
    out = {}
    for fn in (_itunes, _musicbrainz):
        try:
            for k, v in (fn(title, artist) or {}).items():
                if v and not out.get(k): out[k] = v
        except Exception as e:
            log.info("metadata lookup %s failed: %s", fn.__name__, type(e).__name__)
        if all(out.get(k) for k in FIELDS):
            break
    _cache[key] = dict(out)
    if len(_cache) > 500:
        _cache.pop(next(iter(_cache)))
    return out

def complete(m, lookup_fn=None):
    """Fill empty album/year/genre/cover of metadata dict m in place. -> m"""
    if not all(m.get(k) for k in FIELDS) and m.get("title"):
        extra = (lookup_fn or lookup)(m["title"], m.get("artist") or "")
        for k, v in extra.items():
            if v and not m.get(k): m[k] = v
    return m

def sc_description(url):
    """SoundCloud track description (yt-dlp probe), '' when unavailable."""
    try:
        return (engine.probe_ytdlp(url, "soundcloud").get("description") or "").strip()
    except Exception:
        return ""

# ------------------------------------------------------------------ tags
def embed_tags(path, m, cover=None, wd=None):
    """Write title/artist/album/date/genre (+cover art) into an mp3/m4a with ffmpeg. -> new path (or the original on failure)."""
    ff = engine.ffmpeg_path()
    if not ff or not os.path.isfile(path):
        return path
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".mp3", ".m4a", ".mp4"):
        return path
    wd = wd or os.path.dirname(path)
    out = os.path.join(wd, "tagged" + ext)
    cmd = [ff, "-y", "-loglevel", "error", "-i", path]
    has_cover = bool(cover and os.path.isfile(cover))
    if has_cover: cmd += ["-i", cover]
    cmd += ["-map", "0:a"]
    if has_cover: cmd += ["-map", "1:v"]
    cmd += ["-c", "copy"]
    if has_cover:
        cmd += ["-disposition:v:0", "attached_pic"]
        if ext == ".mp3": cmd += ["-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
    if ext == ".mp3": cmd += ["-id3v2_version", "3", "-write_id3v1", "1"]
    for k, tag in (("title", "title"), ("artist", "artist"), ("album", "album"), ("year", "date"), ("genre", "genre")):
        if m.get(k): cmd += ["-metadata", "%s=%s" % (tag, str(m[k])[:200])]
    cmd.append(out)
    rc, _, err = engine.run_capture(cmd, 120)
    if rc != 0 or not os.path.isfile(out) or os.path.getsize(out) < 1000:
        log.info("tag embed skipped: %s", (err or b"")[:120] if isinstance(err, bytes) else str(err)[:120])
        return path
    final = os.path.join(wd, "track" + ext)
    os.replace(out, final)
    if os.path.abspath(path) != os.path.abspath(final):
        try: os.remove(path)
        except OSError: pass
    return final

def read_tags(path):
    ff = engine.shutil.which("ffprobe")
    rc, out, _ = engine.run_capture([ff, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], 30)
    j = json.loads(out)
    tags = {k.lower(): v for k, v in (j.get("format", {}).get("tags") or {}).items()}
    tags["_has_cover"] = any(s.get("codec_type") == "video" for s in j.get("streams", []))
    return tags

# ------------------------------------------------------------------ captions / hashtags
def clean_tag(s):
    s = re.sub(r"[^\w\u200c]+", "_", (s or "").strip(), flags=re.U).strip("_")
    return ("#" + s) if s and not s.isdigit() else ""

def hashtags(m, extra=(), limit=10):
    """Genre, artist(s), year + channel / owner tags (deduplicated, order kept). Items starting with '#' are kept as typed."""
    items = [m.get("genre")] + [x.strip() for x in re.split(r",|&| feat\.? | ft\.? ", m.get("artist") or "") if x.strip()] + list(extra)
    if m.get("year"): items.append(str(m["year"]))
    out, seen = [], set()
    for it in items:
        if not it: continue
        it = str(it).strip().lstrip("#")
        tag = ("#" + it) if re.fullmatch(r"(19|20)\d{2}", it) else clean_tag(it)
        if len(tag) > 1 and tag.lower() not in seen:
            seen.add(tag.lower()); out.append(tag)
    return out[:limit]

def lines(m, source_label=None):
    """Caption lines for title / artist / album / year / genre (missing fields omitted) [+ source]."""
    L = []
    if m.get("title"): L.append("🎵 " + m["title"].strip())
    if m.get("artist"): L.append("👤 " + m["artist"].strip())
    if m.get("album"): L.append("💿 " + m["album"].strip())
    if m.get("year"): L.append("📅 " + str(m["year"]))
    if m.get("genre"): L.append("🎼 " + m["genre"].strip())
    if source_label: L.append("🔎 " + source_label)
    return L

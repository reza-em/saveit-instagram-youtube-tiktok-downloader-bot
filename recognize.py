"""Music recognition ("Shazam-like").

Backends (tried in order):
  1. shazamio  - unofficial Shazam API, needs no key (default, works out of the box)
  2. ACRCloud  - optional fallback, only if ACRCLOUD_HOST + ACRCLOUD_ACCESS_KEY + ACRCLOUD_ACCESS_SECRET are set in the env
Song search / alternative versions / duration come from the public iTunes Search API (no key).
Nothing here ever logs URLs with secrets or the env values.
"""
import os, re, io, json, time, hmac, base64, hashlib, asyncio, logging
import requests
import engine

log = logging.getLogger("recognize")
CLIP_SECONDS = 18
MAX_INPUT_BYTES = 20 * 1024 * 1024        # Telegram getFile limit for bots
RECOGNIZE_TIMEOUT = 45
UA = "SaveIt/1.0"     # iTunes search rejects browser-like User-Agents (403)

class RecognizeError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code + ": " + str(detail)[:200])
        self.code, self.detail = code, detail   # codes: noaudio, toobig, backend, convert

# ------------------------------------------------------------------ audio prep
def _duration(path):
    ff = engine.shutil.which("ffprobe")
    if not ff:
        return 0.0
    try:
        rc, out, _ = engine.run_capture([ff, "-v", "error", "-print_format", "json", "-show_format", path], 30)
        return float(json.loads(out).get("format", {}).get("duration") or 0)
    except Exception:
        return 0.0

def clip_windows(duration):
    """Start offsets (seconds) of the clips to try, most promising first."""
    if duration <= CLIP_SECONDS + 4:
        return [0.0]
    a = max(0.0, min(duration * 0.3, duration - CLIP_SECONDS))
    b = max(0.0, min(duration * 0.65, duration - CLIP_SECONDS))
    return [a] if abs(a - b) < 8 else [a, b]

def make_clip(src, workdir, start, idx=0):
    """-> mono 44.1 kHz WAV of ~18 s (video/audio track of any container)."""
    ff = engine.ffmpeg_path()
    if not ff:
        raise RecognizeError("convert", "ffmpeg missing")
    out = os.path.join(workdir, "clip%d.wav" % idx)
    cmd = [ff, "-y", "-loglevel", "error"]
    if start > 0:
        cmd += ["-ss", "%.2f" % start]
    cmd += ["-i", src, "-vn", "-map", "0:a:0", "-t", str(CLIP_SECONDS), "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", out]
    rc, _, err = engine.run_capture(cmd, 120)
    if rc != 0 or not os.path.isfile(out) or os.path.getsize(out) < 8000:
        if "does not contain any stream" in (err or "") or "matches no streams" in (err or "") or os.path.isfile(out):
            raise RecognizeError("noaudio", err)
        raise RecognizeError("convert", err)
    return out

# ------------------------------------------------------------------ backends
def _best_cover(track):
    imgs = (track or {}).get("images") or {}
    return imgs.get("coverarthq") or imgs.get("coverart") or imgs.get("background")

def _shazam_candidate(track):
    meta = {m.get("title"): m.get("text") for sec in (track.get("sections") or []) for m in (sec.get("metadata") or [])}
    lyr = next((sec.get("text") for sec in (track.get("sections") or []) if sec.get("type") == "LYRICS" and sec.get("text")), None)
    return {"title": track.get("title") or "", "artist": track.get("subtitle") or "", "cover": _best_cover(track),
            "album": meta.get("Album") or "", "year": (meta.get("Released") or "")[:4], "genre": ((track.get("genres") or {}).get("primary") or ""),
            "duration": 0, "source": "shazam", "shazam_id": track.get("key"), "lyrics": "\n".join(lyr) if isinstance(lyr, list) else ""}

def _shazam(path):
    from shazamio import Shazam
    async def go():
        return await asyncio.wait_for(Shazam().recognize(path), RECOGNIZE_TIMEOUT)
    loop = asyncio.new_event_loop()
    try:
        r = loop.run_until_complete(go())
    finally:
        loop.close()
    t = (r or {}).get("track")
    return _shazam_candidate(t) if t and t.get("title") else None

def acr_configured():
    return all(os.environ.get(k) for k in ("ACRCLOUD_HOST", "ACRCLOUD_ACCESS_KEY", "ACRCLOUD_ACCESS_SECRET"))

def _acrcloud(path):
    host, key, secret = (os.environ[k] for k in ("ACRCLOUD_HOST", "ACRCLOUD_ACCESS_KEY", "ACRCLOUD_ACCESS_SECRET"))
    ts = str(int(time.time()))
    sig = base64.b64encode(hmac.new(secret.encode(), "\n".join(["POST", "/v1/identify", key, "audio", "1", ts]).encode(), hashlib.sha1).digest()).decode()
    data = open(path, "rb").read()
    r = requests.post("https://%s/v1/identify" % host, timeout=30,
                      data={"access_key": key, "sample_bytes": len(data), "timestamp": ts, "signature": sig, "data_type": "audio", "signature_version": "1"},
                      files={"sample": ("clip.wav", data)})
    j = r.json()
    if j.get("status", {}).get("code") != 0:
        return None
    m = (j.get("metadata", {}).get("music") or [None])[0]
    if not m:
        return None
    return {"title": m.get("title") or "", "artist": ", ".join(a.get("name", "") for a in (m.get("artists") or [])), "cover": None,
            "album": (m.get("album") or {}).get("name") or "", "year": (m.get("release_date") or "")[:4], "genre": "",
            "duration": int((m.get("duration_ms") or 0) / 1000), "source": "acrcloud"}

BACKENDS = [("shazam", _shazam), ("acrcloud", _acrcloud)]

def identify_clip(path):
    """Run the backends on one clip. Returns candidate dict or None (not recognised). Raises RecognizeError('backend') when
    every configured backend failed with an error (as opposed to simply not knowing the song)."""
    errors, answered = [], False
    for name, fn in BACKENDS:
        if name == "acrcloud" and not acr_configured():
            continue
        try:
            c = fn(path)
            answered = True
            if c:
                log.info("recognized via %s", name)
                return c
        except Exception as e:
            errors.append("%s: %s" % (name, type(e).__name__))
            log.warning("recognition backend %s failed: %s", name, type(e).__name__)
    if errors and not answered:
        raise RecognizeError("backend", "; ".join(errors))
    return None

# ------------------------------------------------------------------ iTunes (search / alternatives / duration / cover)
def _n(s):
    return engine._norm(s)

_last_itunes = [0.0]

def itunes_search(query, limit=8):
    """iTunes Search API (no key). It throttles bursts (empty/403 replies), so retry a few times with a short pause."""
    res = None
    for attempt in range(4):
        wait = 1.2 - (time.time() - _last_itunes[0])
        if wait > 0:
            time.sleep(wait)
        _last_itunes[0] = time.time()
        try:
            r = requests.get("https://itunes.apple.com/search", params={"term": query, "entity": "song", "limit": limit},
                             timeout=15, headers={"User-Agent": UA})
            res = r.json().get("results") or []
            break
        except Exception as e:
            log.info("itunes search attempt %d failed: %s", attempt + 1, type(e).__name__)
            time.sleep(1.5 * (attempt + 1))
    if res is None:
        return []
    out = []
    for x in res:
        if not x.get("trackName"):
            continue
        cover = (x.get("artworkUrl100") or "").replace("100x100bb", "600x600bb") or None
        out.append({"title": x["trackName"], "artist": x.get("artistName") or "", "cover": cover, "album": x.get("collectionName") or "",
                    "year": (x.get("releaseDate") or "")[:4], "genre": x.get("primaryGenreName") or "",
                    "duration": int((x.get("trackTimeMillis") or 0) / 1000), "source": "itunes"})
    return out

def youtube_search_songs(query, n=5):
    """Fallback for text search when iTunes is unavailable: YouTube results shaped like song candidates."""
    rc, out, err = engine.run_capture(engine._ytdlp_base("youtube") + ["--flat-playlist", "-J", "ytsearch%d:%s" % (n + 3, query)], 60)
    if rc != 0 or not out.strip().startswith("{"):
        return []
    res = []
    for x in json.loads(out).get("entries") or []:
        if not x or not x.get("title") or (x.get("duration") or 0) > 900:
            continue
        artist = re.sub(r"\s*-\s*Topic$", "", x.get("channel") or x.get("uploader") or "")
        title = x["title"]
        if " - " in title:
            a_, t_ = title.split(" - ", 1); artist, title = a_.strip(), t_.strip()
        res.append({"title": re.sub(r"\s*[\(\[](official|lyrics?|audio|video|hd|4k).*?[\)\]]", "", title, flags=re.I).strip() or title,
                    "artist": artist, "cover": x.get("thumbnails", [{}])[-1].get("url") if x.get("thumbnails") else None,
                    "album": "", "year": "", "genre": "", "duration": int(x.get("duration") or 0), "source": "youtube"})
        if len(res) >= n:
            break
    return res

def search_songs(query, n=5):
    """Free-text song search -> up to n distinct candidates."""
    seen, out = set(), []
    for c in (itunes_search(query, 15) or youtube_search_songs(query, n)):
        k = (_n(c["title"]), _n(c["artist"]))
        if k in seen:
            continue
        seen.add(k); out.append(c)
        if len(out) >= n:
            break
    return out

def enrich(first, n=5):
    """Fill duration/cover of the recognised song from iTunes and collect alternative candidates (other versions/artists
    with the same title). Returns [first, alt1, ...] (<= n)."""
    res = itunes_search("%s %s" % (first["artist"], first["title"]), 12)
    key = (_n(first["title"]), _n(first["artist"]))
    for c in res:
        if (_n(c["title"]), _n(c["artist"])) == key:
            if not first.get("duration"): first["duration"] = c["duration"]
            if not first.get("cover"): first["cover"] = c["cover"]
            if not first.get("album"): first["album"] = c["album"]
            break
    out, seen = [first], {key}
    for c in res:
        k = (_n(c["title"]), _n(c["artist"]))
        if k in seen:
            continue
        seen.add(k); out.append(c)
        if len(out) >= n:
            break
    return out

# ------------------------------------------------------------------ public entry
def recognize_media(src, workdir, n=5):
    """Any audio/video file -> list of candidates ([] = not recognised). Raises RecognizeError."""
    if os.path.getsize(src) > MAX_INPUT_BYTES:
        raise RecognizeError("toobig")
    dur = _duration(src)
    found = None
    for i, start in enumerate(clip_windows(dur)):
        clip = make_clip(src, workdir, start, i)
        found = identify_clip(clip)
        if found:
            break
    if not found:
        return []
    return enrich(found, n)

def to_meta(c):
    """Candidate -> the metadata dict used by the Spotify-style YouTube matcher."""
    arts = [a.strip() for a in re.split(r",|&| feat\.? | ft\.? ", c.get("artist") or "") if a.strip()] or [c.get("artist") or ""]
    return {"id": None, "title": c["title"], "artist": c.get("artist") or "", "artists": arts,
            "duration": int(c.get("duration") or 0), "cover": c.get("cover"), "year": c.get("year") or "",
            "album": c.get("album") or "", "genre": c.get("genre") or "", "lyrics": c.get("lyrics") or ""}

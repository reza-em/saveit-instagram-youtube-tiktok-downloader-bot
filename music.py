"""Music source chain: find and download a song from a real MUSIC source (not from YouTube first).

Providers (all reachable without keys/accounts; checked from the box):
  radiojavan  - Persian/Iranian music, open search API, direct 256 kbps MP3 links
  soundcloud  - `scsearch` via yt-dlp (many official uploads are DRM-flagged -> skipped at download time, next candidate is tried)
  audius      - free API, full MP3 streams (mostly indie / covers -> the scoring rejects covers)
  bandcamp    - public search API + yt-dlp (streamable tracks)
  ytmusic     - LAST fallback only: ytmusicapi search (metadata) + yt-dlp download (often blocked by YouTube's bot check on
                datacenter IPs; a YouTube cookies.txt uploaded by the admin helps)
Deezer / iTunes previews / Jamendo need keys or are blocked -> not used.

Flow: meta(title, artist, duration) -> search all providers in parallel -> score each hit (title / artist / duration, penalise
live/cover/remix/lyrics...) -> ranked list. download_audio() walks the ranked list until one downloads and passes a duration check.
"""
import os, re, json, time, logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout
import requests
import engine
from engine import EngineError

log = logging.getLogger("music")
UA = engine.UA
LABELS = {"radiojavan": "Radio Javan", "soundcloud": "SoundCloud", "audius": "Audius", "bandcamp": "Bandcamp", "ytmusic": "YouTube Music"}
SHORT = {"radiojavan": "RJ", "soundcloud": "SC", "audius": "Audius", "bandcamp": "BC", "ytmusic": "YTM"}
PRIMARY = ("radiojavan", "soundcloud", "audius", "bandcamp")
MIN_SCORE = 50
MAX_RANKED = 4
SEARCH_TIMEOUT = 45
DIRECT_LIMIT = 60 * 1024 * 1024
LAST_STATUS = {}          # provider -> "ok:N" / "error:Type" of the latest search (diagnostics, live test)

_BAD = ("live", "cover", "remix", "karaoke", "instrumental", "sped up", "speed up", "slowed", "reverb", "8d", "nightcore", "mashup",
        "bootleg", "tribute", "originally performed", "bass boosted", "acoustic", "piano version", "tutorial", "reaction", "edit",
        "type beat", "loop", "lesson", "reprise", "medley", "flip")
_LYR = ("lyrics", "lyric video", "with lyrics")

_FA = str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "أ": "ا", "إ": "ا", "ؤ": "و", "\u200c": " ", "\u200f": "", "\u200e": ""})

def _n(s):
    s = (s or "").translate(_FA)
    s = re.sub(r"[\u064b-\u065f\u0670]", "", s)
    return engine._norm(s)

def _has_word(w, text):
    return re.search(r"(?<!\w)%s(?!\w)" % re.escape(w), text) is not None

def fmt_dur(s):
    s = int(s or 0)
    return "%d:%02d" % (s // 60, s % 60) if s else ""

def display_title(c):
    if c.get("song"):
        return c["song"].strip()
    t = (c.get("title") or "").strip().strip('"')
    if " - " in t:
        a, b = t.split(" - ", 1)
        art = _n(c.get("artist") or c.get("uploader") or "")
        if art and (_n(a) in art or art in _n(a)):
            return b.strip().strip('"')
        if art and (_n(b) in art or art in _n(b)):
            return a.strip().strip('"')
    return t

# ------------------------------------------------------------------ providers
def _s_radiojavan(q):
    r = requests.get("https://play.radiojavan.com/api/p/search", params={"query": q}, headers={"User-Agent": UA}, timeout=12)
    out = []
    for x in (r.json().get("mp3s") or [])[:8]:
        if not x.get("link"):
            continue
        out.append({"source": "radiojavan", "id": str(x.get("id")), "title": x.get("title") or "", "song": x.get("song") or "",
                    "artist": x.get("artist") or "", "duration": int(x.get("duration") or 0), "cover": x.get("photo") or x.get("thumbnail"),
                    "direct": x["link"], "direct_alt": [u for u in (x.get("hq_link"),) if u]})
    return out

def _s_soundcloud(q):
    cmd = engine._ytdlp_base("soundcloud") + ["--flat-playlist", "-J", "scsearch8:" + q]
    rc, out, err = engine.run_capture(cmd, 40)
    if rc != 0 or not out.strip().startswith("{"):
        raise EngineError(engine.classify_error(err), err)
    res = []
    for e in json.loads(out).get("entries") or []:
        url = e.get("webpage_url") or e.get("url")
        if not url or not e.get("title"):
            continue
        arts = e.get("artists")
        thumbs = e.get("thumbnails") or []
        cover = next((t["url"] for t in thumbs if t.get("id") in ("t500x500", "t300x300", "original")), thumbs[-1]["url"] if thumbs else None)
        res.append({"source": "soundcloud", "id": str(e.get("id")), "title": e.get("title"), "artist": (arts[0] if isinstance(arts, list) and arts else e.get("uploader") or ""),
                    "uploader": e.get("uploader") or "", "duration": int(float(e.get("duration") or 0)), "cover": cover, "url": url})
    return res

def _s_audius(q):
    r = requests.get("https://api.audius.co/v1/tracks/search", params={"query": q, "app_name": "SaveIt", "limit": 8}, headers={"User-Agent": UA}, timeout=15)
    out = []
    for t in r.json().get("data") or []:
        if t.get("is_streamable") is False or not t.get("id"):
            continue
        art = t.get("artwork") or {}
        out.append({"source": "audius", "id": str(t["id"]), "title": t.get("title") or "", "artist": (t.get("user") or {}).get("name") or "",
                    "uploader": (t.get("user") or {}).get("name") or "", "duration": int(t.get("duration") or 0),
                    "cover": art.get("480x480") or art.get("1000x1000") or art.get("150x150"),
                    "direct": "https://api.audius.co/v1/tracks/%s/stream?app_name=SaveIt" % t["id"]})
    return out

def _s_bandcamp(q):
    r = requests.post("https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic", timeout=15, headers={"User-Agent": UA},
                      json={"search_text": q, "search_filter": "t", "full_page": False, "fan_id": None})
    out = []
    for x in (r.json().get("auto") or {}).get("results") or []:
        if x.get("type") != "t" or not x.get("item_url_path"):
            continue
        out.append({"source": "bandcamp", "id": str(x.get("id")), "title": x.get("name") or "", "artist": x.get("band_name") or "",
                    "uploader": x.get("band_name") or "", "duration": 0, "cover": x.get("img"), "url": x["item_url_path"]})
    return out[:8]

def _s_ytmusic(q):
    from ytmusicapi import YTMusic
    res = YTMusic().search(q, filter="songs", limit=8)
    out = []
    for x in res:
        if not x.get("videoId"):
            continue
        th = x.get("thumbnails") or []
        out.append({"source": "ytmusic", "id": x["videoId"], "title": x.get("title") or "", "artist": ", ".join(a.get("name", "") for a in (x.get("artists") or [])),
                    "duration": int(x.get("duration_seconds") or 0), "cover": th[-1]["url"] if th else None,
                    "url": "https://music.youtube.com/watch?v=" + x["videoId"]})
    return out

SEARCHERS = {"radiojavan": _s_radiojavan, "soundcloud": _s_soundcloud, "audius": _s_audius, "bandcamp": _s_bandcamp, "ytmusic": _s_ytmusic}

def search_providers(queries, providers):
    """Run providers in parallel; `queries` = {provider: [q, ...]} or a list used for every provider. Returns flat list of cands."""
    jobs = []
    for p in providers:
        qs = queries.get(p) if isinstance(queries, dict) else queries
        for q in qs or []:
            jobs.append((p, q))
    res = []
    if not jobs:
        return res
    ex = ThreadPoolExecutor(max_workers=len(jobs))
    futs = [(p, q, ex.submit(SEARCHERS[p], q)) for p, q in jobs]
    deadline = time.time() + SEARCH_TIMEOUT
    counts = {}
    for p, q, f in futs:
        try:
            got = f.result(timeout=max(1, deadline - time.time()))
            counts[p] = counts.get(p, 0) + len(got); res += got
            LAST_STATUS[p] = "ok:%d" % counts[p]
        except FTimeout:
            LAST_STATUS[p] = "error:Timeout"; log.info("music search %s timed out", p)
        except Exception as e:
            LAST_STATUS[p] = "error:%s" % type(e).__name__; log.info("music search %s failed: %s", p, type(e).__name__)
    ex.shutdown(wait=False)
    return res

# ------------------------------------------------------------------ scoring
def score_track(c, meta, query=""):
    """Match a provider hit against known metadata {title, artist, artists, duration}. -> (score, reason); score < 0 = reject."""
    t_raw = c.get("title") or ""
    low = t_raw.lower() + " " + (c.get("song") or "").lower()
    mt = _n(meta["title"])
    if not mt:
        return -1, "no title"
    variants = [c.get("song") or "", t_raw]
    if " - " in t_raw:
        a, b = t_raw.split(" - ", 1); variants += [b, a]
    tscore = 0
    for v in variants:
        vn = _n(v)
        if not vn:
            continue
        if vn == mt: s = 40
        elif _has_word(mt, vn): s = 28
        elif set(mt.split()) <= set(vn.split()): s = 20
        else: s = 0
        tscore = max(tscore, s)
    if not tscore:
        return -1, "title mismatch"
    score = tscore
    arts = [_n(a) for a in (meta.get("artists") or [meta.get("artist") or ""]) if a]
    first = arts[0] if arts else ""
    afield = _n(c.get("artist") or "")
    ufield = _n(c.get("uploader") or "")
    ttl = _n(t_raw)
    if first and (first in afield or (len(afield) > 3 and afield in first) or first in ufield):
        score += 25 + (5 if afield == first else 0)
    elif first and first in ttl:
        score += 10
    elif any(tok in (afield + " " + ufield + " " + ttl) for a in arts for tok in a.split() if len(tok) > 3):
        score += 5
    else:
        score -= 25
    dur, mdur = int(c.get("duration") or 0), int(meta.get("duration") or 0)
    if dur:
        if dur > 1200:
            return -1, "too long"
        if mdur:
            d = abs(dur - mdur)
            if d > max(15, mdur * 0.12):
                return -1, "duration off by %ss" % d
            score += 30 if d <= 3 else 22 if d <= 7 else 10
        elif dur < 45:
            return -1, "preview/snippet"
    elif mdur:
        score -= 10
    ctx = (meta["title"] + " " + query).lower()
    for w in _BAD:
        if _has_word(w, low) and not _has_word(w, ctx):
            score -= 100        # a different version of the song (live/cover/remix/...) is worse than no match
    for w in _LYR:
        if w in low and w not in ctx:
            score -= 70         # lyric-video uploads
    src = c.get("source")
    if src == "radiojavan": score += 10
    elif src == "ytmusic": score += 8
    elif src == "soundcloud" and first and _n(c.get("uploader") or "") == first: score += 15
    elif src == "bandcamp" and afield == first: score += 5
    return score, "ok"

def rank_query(c, q):
    """Relevance of a hit for a free-text search query (no metadata known). < 0 = drop."""
    qt = [t for t in _n(q).split() if len(t) > 1]
    if not qt:
        return -1
    hay = set(_n(" ".join([c.get("artist") or "", c.get("uploader") or "", c.get("title") or "", c.get("song") or ""])).split())
    f = sum(1 for t in qt if t in hay) / len(qt)
    if f < 0.6:
        return -1
    s = 60 * f
    low = ((c.get("title") or "") + " " + (c.get("song") or "")).lower(); ql = q.lower()
    for w in _BAD:
        if _has_word(w, low) and not _has_word(w, ql):
            s -= 40
    for w in _LYR:
        if w in low and w not in ql:
            s -= 15
    dur = int(c.get("duration") or 0)
    if dur and dur < 60: s -= 30
    if dur > 900: s -= 30
    s += {"radiojavan": 10, "ytmusic": 6, "bandcamp": 3}.get(c.get("source"), 0)
    if c.get("source") == "soundcloud" and _n(c.get("uploader") or "") and _n(c.get("uploader")) in _n(q): s += 8
    return s

def _dedupe(items, keyfn, scorefn):
    best = {}
    for c in items:
        k = keyfn(c)
        s = scorefn(c)
        if k not in best or s > best[k][0]:
            best[k] = (s, c)
    return [c for s, c in sorted(best.values(), key=lambda x: -x[0])]

# ------------------------------------------------------------------ search UX
def search(query, n=5):
    """Free-text search across the providers -> up to n distinct, best-first hits (each has source/title/artist/duration/cover)."""
    q = (query or "").strip()[:120]
    hits = search_providers([q], PRIMARY)
    if len(hits) < 3:
        hits += search_providers([q], ["ytmusic"])
    for c in hits:
        c["_s"] = rank_query(c, q)
    hits = [c for c in hits if c["_s"] >= 0]
    out = _dedupe(hits, lambda c: (_n(display_title(c)), _n(c.get("artist") or "")), lambda c: c["_s"])
    return out[:n]

# ------------------------------------------------------------------ metadata -> best sources
def _queries(meta):
    artist, title = (meta.get("artist") or "").strip(), meta["title"].strip()
    q = ("%s %s" % (artist, title)).strip()
    rj = [q] + ([artist] if artist else [])
    return {"radiojavan": rj, "soundcloud": [q], "audius": [q], "bandcamp": [q], "ytmusic": [q]}

def find_best(meta):
    """Ranked list (<= MAX_RANKED) of downloadable candidates for known metadata. YouTube Music only when nothing else matched."""
    qs = _queries(meta)
    cands = search_providers(qs, PRIMARY)
    def ranked(cs):
        out = []
        for c in cs:
            s, why = score_track(c, meta)
            log.info("music cand %s score=%s (%s) dur=%s %r - %r", c["source"], s, why, c.get("duration"), c.get("artist"), (c.get("title") or "")[:50])
            if s >= MIN_SCORE:
                c["score"] = s; out.append(c)
        return sorted(out, key=lambda c: -c["score"])
    r = _dedupe_ranked(ranked(cands))
    if not r:
        r = ranked(search_providers(qs, ["ytmusic"]))
        if not r and LAST_STATUS.get("ytmusic", "").startswith("error"):
            try:                                            # ytmusicapi down -> plain YouTube search scoring
                m = engine.spotify_match(meta)
                r = [{"source": "ytmusic", "id": m["id"], "title": m["title"], "artist": m["channel"], "duration": m["duration"],
                      "cover": None, "url": "https://www.youtube.com/watch?v=" + m["id"], "score": m["score"]}]
            except EngineError:
                pass
    if not r:
        raise EngineError("nomedia", "no confident match for %r" % meta["title"])
    # one candidate per source first (diversity for fallbacks), then the rest
    seen, first, rest = set(), [], []
    for c in r:
        (rest if c["source"] in seen else first).append(c); seen.add(c["source"])
    return (first + rest)[:MAX_RANKED]

def _dedupe_ranked(r):
    seen, out = set(), []
    for c in r:
        k = (c["source"], _n(display_title(c)), _n(c.get("artist") or c.get("uploader") or ""), int(c.get("duration") or 0) // 3)
        if k in seen:
            continue
        seen.add(k); out.append(c)
    return out

def _late_yt(meta, tried):
    """Last resort after every primary source failed at download time: several YouTube Music candidates."""
    if "ytmusic" in tried or not meta:
        return []
    cs = search_providers({"ytmusic": [("%s %s" % (meta.get("artist") or "", meta["title"])).strip()]}, ["ytmusic"])
    out = []
    for c in cs:
        s, why = score_track(c, meta)
        if s >= MIN_SCORE:
            c["score"] = s; out.append(c)
    return _dedupe_ranked(sorted(out, key=lambda c: -c["score"]))[:3]

def _caption(title, artist, cand):
    cap = engine._audio_caption(title, artist)
    return cap + "\n🔎 %s ▸ %s" % (LABELS.get(cand["source"], cand["source"]), (cand.get("title") or "")[:120])

def _match(c):
    return {"source": c["source"], "source_label": LABELS.get(c["source"], c["source"]), "id": c.get("id"),
            "title": c.get("title") or "", "channel": c.get("artist") or c.get("uploader") or "", "score": c.get("score")}

def audio_info(meta):
    """Metadata (Spotify / recognition) -> downloadable audio info via the provider chain."""
    ranked = find_best(meta)
    top = ranked[0]
    title, artist = meta["title"], meta.get("artist") or ""
    return {"kind": "audio", "platform": "spotify", "dl_cands": ranked, "meta": {k: meta.get(k) for k in ("title", "artist", "artists", "duration")},
            "tags": {k: meta.get(k) or "" for k in ("album", "year", "genre")}, "lyrics": meta.get("lyrics") or "",
            "url": top.get("url") or top.get("direct") or "",
            "title": title[:200], "uploader": artist, "duration": int(meta.get("duration") or top.get("duration") or 0),
            "thumb": meta.get("cover") or top.get("cover"), "via": "music", "id": meta.get("id"),
            "caption": _caption(title, artist, top), "match": _match(top)}

def info_for_cand(c):
    """A search hit the user picked -> audio info (no network)."""
    title = display_title(c)
    artist = c.get("artist") or c.get("uploader") or ""
    return {"kind": "audio", "platform": "spotify", "dl_cands": [c], "url": c.get("url") or c.get("direct") or "",
            "meta": {"title": title, "artist": artist, "artists": [artist], "duration": int(c.get("duration") or 0)},
            "tags": {k: c.get(k) or "" for k in ("album", "year", "genre")}, "lyrics": "",
            "title": title[:200], "uploader": artist, "duration": int(c.get("duration") or 0), "thumb": c.get("cover"), "via": "music",
            "id": c.get("id"), "caption": _caption(title, artist, c), "match": _match(c)}

# ------------------------------------------------------------------ downloading
def _fetch(url, dest, cb=None):
    with requests.get(url, headers={"User-Agent": UA}, stream=True, timeout=30, allow_redirects=True) as r:
        if r.status_code != 200:
            raise EngineError("notfound", "HTTP %s" % r.status_code)
        total = int(r.headers.get("content-length") or 0)
        n = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                n += len(chunk)
                if n > DIRECT_LIMIT:
                    raise EngineError("toolong", "too large")
                f.write(chunk)
                if cb and total:
                    cb(100.0 * n / total)
    return dest

def _to_m4a(path, workdir):
    ff = engine.ffmpeg_path()
    out = os.path.join(workdir, "conv.m4a")
    rc, _, err = engine.run_capture([ff, "-y", "-loglevel", "error", "-i", path, "-vn", "-c:a", "aac", "-b:a", "192k", out], 300)
    if rc != 0 or not os.path.isfile(out):
        raise EngineError("unknown", err)
    return out

def _download_cand(c, fmt, wd, cb):
    if c.get("direct"):
        dst = os.path.join(wd, "dl.mp3")
        urls = [c["direct"]] + list(c.get("direct_alt") or [])
        last = None
        for u in urls[:1]:
            try:
                _fetch(u, dst, cb); last = None; break
            except EngineError as e:
                last = e
        if last: raise last
        return _to_m4a(dst, wd) if fmt == "m4a" else dst
    plat = "youtube" if c["source"] == "ytmusic" else c["source"]
    if fmt == "m4a":
        extra, f = ["-x", "--audio-format", "m4a", "--audio-quality", "0"], "ba[ext=m4a]/ba/b"
    else:
        extra, f = ["-x", "--audio-format", "mp3", "--audio-quality", "0"], "ba/b"
    engine._run_ytdlp(plat, c["url"], wd, f, extra, cb)
    out = engine._newest(wd, (".mp3", ".m4a", ".opus", ".aac", ".ogg"))
    if not out:
        raise EngineError("nomedia", "no audio output")
    return out

def _duration_ok(path, expected):
    d = (engine.probe_media(path) or {}).get("duration") or 0
    if not d:
        return True
    if expected and expected > 90 and d < 45:
        return False                               # 30 s preview
    return not expected or abs(d - expected) <= max(20, expected * 0.15)

def download_audio(info, fmt, wd, cb=None):
    """Try the ranked candidates in order; the first that downloads and passes the duration check wins. When all fail, YouTube
    Music candidates are tried as the last resort. Updates info's caption/match to the source that was really used."""
    errs, tried = [], set()
    def attempt(c):
        tried.add(c["source"])
        try:
            path = _download_cand(c, fmt, wd, cb)
            if not _duration_ok(path, int(c.get("duration") or info.get("duration") or 0)):
                raise EngineError("nomedia", "duration mismatch (preview?)")
            info["match"] = _match(c)
            info["caption"] = _caption(info["title"], info.get("uploader") or "", c)
            if not info.get("thumb"):
                info["thumb"] = c.get("cover")
            log.info("music download ok via %s (%s)", c["source"], os.path.basename(path))
            return path
        except EngineError as e:
            if "drm" in (e.detail or "").lower():
                e = EngineError("nomedia", "DRM-protected (official upload)")
            errs.append(e); log.info("music download via %s failed: %s %s", c["source"], e.code, (e.detail or "")[:60].replace("\n", " "))
            engine._clear_media(wd)
            return None
    for c in info["dl_cands"]:
        p = attempt(c)
        if p: return p
    for c in _late_yt(info.get("meta"), tried):
        p = attempt(c)
        if p: return p
    codes = [e.code for e in errs]
    if "ipblock" in codes and all(x in ("ipblock", "nomedia", "notfound", "unknown", "login") for x in codes):
        raise EngineError("ipblock", "; ".join((e.detail or "")[:80] for e in errs))
    pick = next((e for e in errs if e.code not in ("unknown", "nomedia", "notfound")), None) or (errs[-1] if errs else EngineError("nomedia", "no candidates"))
    raise pick

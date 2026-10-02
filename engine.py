"""Download engine: URL classification, probing and downloading via yt-dlp (main) and gallery-dl (fallback).
No Telegram code in here. Both tools are run as subprocesses of the venv python so `pip install -U` takes
effect immediately and hung jobs can be killed."""
import os, re, sys, json, time, shutil, subprocess, threading, logging, glob
from urllib.parse import urlparse, parse_qs

log = logging.getLogger("engine")
BASE = os.path.dirname(os.path.abspath(__file__))
import plat as _plat
TMP_ROOT = os.path.join(BASE, _plat.PLAT.tmp_name)       # per platform: cleanup_all() of one process must not wipe the other one's jobs
COOKIE_DIR = os.path.join(BASE, "cookies")
PY = sys.executable
TG_LIMIT = 50 * 1024 * 1024
SAFE_LIMIT = _plat.PLAT.upload_mb * 1000 * 1000          # stay below 50 MB with headroom (Telegram counts 50*1024*1024 but be safe)
MAX_DURATION = 4 * 3600                # refuse videos longer than this
MAX_DOWNLOAD_BYTES = 400 * 1024 * 1024 # hard cap for a single download on disk
PROBE_TIMEOUT = 90
DL_TIMEOUT = 900
LADDER = [1080, 720, 480, 360, 240, 144]
PLATFORMS = ("youtube", "instagram", "tiktok", "pinterest", "linkedin", "threads", "twitter", "soundcloud", "spotify", "music_id")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

class EngineError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = (detail or "")[-400:]

# ---------------------------------------------------------------- URL classification
URL_RE = re.compile(r"https?://[^\s<>\"'«»]+", re.I)
IG_RESERVED = {"explore", "accounts", "direct", "p", "reel", "reels", "tv", "stories", "about", "legal", "web",
               "developer", "privacy", "directory", "challenge", "emails", "nametag", "graphql", "api", "share",
               "tags", "locations", "s"}
SC_RESERVED = {"discover", "stream", "you", "upload", "search", "pages", "charts", "feed", "settings", "messages",
               "notifications", "terms-of-use", "jobs", "imprint", "popular", "mobile", "creators", "artists", "go", "people"}
USER_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

def extract_urls(text):
    out = []
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:!?)»”’")
        if u not in out:
            out.append(u)
    return out

def _host(u):
    h = (urlparse(u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h

def classify(url):
    """-> {"platform", "kind", "url", ...} or None if unsupported.
    kinds: video (youtube/tiktok/pin), post (instagram p/reel/tv), story, stories, highlight, profile, resolve"""
    try:
        p = urlparse(url)
    except Exception:
        return None
    h = _host(url)
    path = [x for x in p.path.split("/") if x]
    if h in ("youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com", "youtu.be"):
        q = parse_qs(p.query)
        if h == "youtu.be":
            return {"platform": "youtube", "kind": "video", "url": url} if path else None
        if path and path[0] in ("shorts", "live", "embed", "v") and len(path) > 1:
            return {"platform": "youtube", "kind": "video", "url": url}
        if path and path[0] == "watch" and q.get("v"):
            return {"platform": "youtube", "kind": "video", "url": url}
        return None
    if h in ("instagram.com", "instagr.am", "m.instagram.com"):
        if not path:
            return None
        if path[0] == "stories":
            if len(path) >= 3 and path[1] == "highlights":
                return {"platform": "instagram", "kind": "highlight", "url": url, "id": path[2]}
            if len(path) >= 3 and path[2].isdigit():
                return {"platform": "instagram", "kind": "story", "url": url, "user": path[1], "id": path[2]}
            if len(path) >= 2:
                return {"platform": "instagram", "kind": "stories", "url": url, "user": path[1]}
            return None
        if path[0] in ("p", "reel", "reels", "tv") and len(path) >= 2:
            return {"platform": "instagram", "kind": "post", "url": url}
        if len(path) >= 3 and path[1] in ("p", "reel", "tv"):
            return {"platform": "instagram", "kind": "post", "url": "https://www.instagram.com/%s/%s/" % (path[1], path[2])}
        if path[0] == "share" and len(path) >= 2:
            return {"platform": "instagram", "kind": "resolve", "url": url}
        if path[0].lower() not in IG_RESERVED and USER_RE.match(path[0]) and len(path) <= 2:
            return {"platform": "instagram", "kind": "profile", "url": url, "user": path[0]}
        return None
    if h.endswith("tiktok.com"):
        if h in ("vm.tiktok.com", "vt.tiktok.com") or (path and path[0] == "t"):
            return {"platform": "tiktok", "kind": "resolve", "url": url}
        if len(path) >= 3 and path[0].startswith("@") and path[1] in ("video", "photo"):
            return {"platform": "tiktok", "kind": "video", "url": url}
        if len(path) >= 2 and path[0] == "v":
            return {"platform": "tiktok", "kind": "video", "url": url}
        return None
    if h in ("soundcloud.com", "m.soundcloud.com", "on.soundcloud.com", "soundcloud.app.goo.gl"):
        if h in ("on.soundcloud.com", "soundcloud.app.goo.gl"):
            return {"platform": "soundcloud", "kind": "resolve", "url": url} if path else None
        if not path or path[0].lower() in SC_RESERVED:
            return None
        clean = "https://soundcloud.com/" + "/".join(path)          # drop tracking query (private tracks keep /s-token in the path)
        if len(path) >= 3 and path[1] == "sets":
            return {"platform": "soundcloud", "kind": "collection", "url": clean}
        if len(path) == 2 and path[1] not in ("tracks", "albums", "sets", "reposts", "likes", "following", "followers", "popular-tracks", "comments"):
            return {"platform": "soundcloud", "kind": "audio", "url": clean}
        if len(path) == 3 and path[2].startswith("s-") and path[1] != "sets":
            return {"platform": "soundcloud", "kind": "audio", "url": clean}
        return None
    if h in ("open.spotify.com", "play.spotify.com", "spotify.link", "spoti.fi"):
        if h in ("spotify.link", "spoti.fi"):
            return {"platform": "spotify", "kind": "resolve", "url": url} if path else None
        if path and path[0].startswith("intl-"):
            path = path[1:]
        if path and path[0] == "embed":
            path = path[1:]
        if len(path) >= 2 and path[0] in ("track", "album", "playlist") and re.fullmatch(r"[A-Za-z0-9]{10,30}", path[1]):
            kind = "audio" if path[0] == "track" else "collection"
            return {"platform": "spotify", "kind": kind, "sp_type": path[0], "sp_id": path[1],
                    "url": "https://open.spotify.com/%s/%s" % (path[0], path[1])}
        return None
    if re.match(r"^([a-z]{2,3}\.)?pinterest\.[a-z.]{2,6}$", h) or h == "pin.it":
        if h == "pin.it":
            return {"platform": "pinterest", "kind": "resolve", "url": url} if path else None
        if len(path) >= 2 and path[0] == "pin":
            return {"platform": "pinterest", "kind": "video", "url": url}
        return None
    import threads, twitterx
    c = threads.classify(url, h, path) or twitterx.classify(url, h, path)
    if c: return c
    import linkedin
    return linkedin.classify(url, h, path)

def parse_username(text):
    """'@name' (or a bare name when awaiting) -> name, else None."""
    t = (text or "").strip()
    if t.startswith("@"):
        t = t[1:]
    t = t.split()[0] if t.split() else ""
    return t if USER_RE.match(t) and not t.isdigit() else None

def resolve_redirect(url, timeout=15):
    import requests
    r = requests.get(url, allow_redirects=True, timeout=timeout, headers={"User-Agent": UA}, stream=True)
    final = r.url
    r.close()
    return final

# ---------------------------------------------------------------- error classification
def classify_error(text):
    t = (text or "").lower()
    def has(*ks): return any(k in t for k in ks)
    if has("ip address is blocked", "ip is blocked", "your ip"):
        return "ipblock"
    if has("age-restricted", "age restricted", "confirm your age", "inappropriate for some users", "age verification"):
        return "age"
    if has("not available in your country", "geo restrict", "geo-restrict", "geo-block", "blocked it in your country",
           "not made this video available in your country"):
        return "geo"
    if has("sign in to confirm you", "confirm you're not a bot", "confirm you’re not a bot"):
        return "ipblock"
    if has("private video", "is private", "private account", "this account is private", "video is private"):
        return "private"
    if has("429", "too many requests", "rate-limit", "rate limit", "try again in a few minutes",
           "please wait a few minutes", "temporarily blocked"):
        return "rate"
    if has("login required", "log in", "login", "sign in", "use --cookies", "empty media response",
           "not granting access", "requires authentication", "authentication required", "checkpoint"):
        return "login"
    if has("not found", "404", "video unavailable", "has been removed", "does not exist", "could not be found",
           "no longer available", "unavailable"):
        return "notfound"
    if has("unsupported url", "is not a valid url"):
        return "unsupported"
    if has("no video formats found", "no media", "no video could be found", "there is no video"):
        return "nomedia"
    if has("timed out", "timeout"):
        return "timeout"
    return "unknown"

# ---------------------------------------------------------------- cookies / env
COOKIE_PLATFORMS = {"instagram": ("instagram.com",), "youtube": ("youtube.com", "google.com")}

def cookie_path(platform):
    p = os.path.join(COOKIE_DIR, f"{platform}.txt")
    return p if os.path.isfile(p) else None

def validate_cookies(data, platform):
    """Netscape cookies.txt check. Returns number of usable cookie lines for the platform (0 = invalid). Never logs content."""
    try:
        text = data.decode("utf-8", "ignore") if isinstance(data, (bytes, bytearray)) else data
    except Exception:
        return 0
    doms = COOKIE_PLATFORMS.get(platform, ())
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        if not line or line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) != 7:
            continue
        if any(f[0].lstrip(".").endswith(d) for d in doms) and f[5].strip():
            n += 1
    return n

def save_cookies(platform, data):
    os.makedirs(COOKIE_DIR, exist_ok=True)
    os.chmod(COOKIE_DIR, 0o700)
    dst = os.path.join(COOKIE_DIR, f"{platform}.txt")
    tmp = dst + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, dst)
    os.chmod(dst, 0o600)

def delete_cookies(platform):
    try:
        os.remove(os.path.join(COOKIE_DIR, f"{platform}.txt"))
        return True
    except FileNotFoundError:
        return False

def _env():
    env = dict(os.environ)
    for k in list(env):
        if "TOKEN" in k or k.startswith("DL_TELEGRAM") or k.startswith("DL_BALE"):
            env.pop(k, None)
    env["PATH"] = os.path.dirname(PY) + os.pathsep + env.get("PATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    return env

def ffmpeg_path():
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None

def _ytdlp_base(platform, cookies=True):
    cmd = [PY, "-m", "yt_dlp", "--no-warnings", "--no-playlist", "--ignore-config", "--no-colors",
           "--socket-timeout", "25", "--retries", "3", "--user-agent", UA]
    ff = ffmpeg_path()
    if ff and shutil.which("ffmpeg") != ff:
        cmd += ["--ffmpeg-location", ff]
    if platform == "youtube" and shutil.which("node"):
        cmd += ["--js-runtimes", "node"]
    cp = cookie_path(platform) if cookies else None
    if cp:
        cmd += ["--cookies", cp]
    return cmd

# ---------------------------------------------------------------- subprocess helpers
def run_capture(cmd, timeout):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=_env())
    except subprocess.TimeoutExpired:
        raise EngineError("timeout", "probe timeout")
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")

def run_stream(cmd, timeout, on_line=None, cwd=None, size_watch_dir=None):
    """Run with merged output, kill on timeout or if the work dir grows past MAX_DOWNLOAD_BYTES."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_env(), cwd=cwd,
                         text=True, errors="replace", bufsize=1)
    state = {"timeout": False, "big": False}
    def watchdog():
        t0 = time.time()
        while p.poll() is None:
            if time.time() - t0 > timeout:
                state["timeout"] = True; p.kill(); return
            if size_watch_dir and dir_size(size_watch_dir) > MAX_DOWNLOAD_BYTES:
                state["big"] = True; p.kill(); return
            time.sleep(1.0)
    threading.Thread(target=watchdog, daemon=True).start()
    tail = []
    for line in p.stdout:
        line = line.rstrip("\n")
        tail.append(line); tail = tail[-30:]
        if on_line:
            try: on_line(line)
            except Exception: pass
    p.wait()
    if state["timeout"]:
        raise EngineError("timeout", "download exceeded %ss" % timeout)
    if state["big"]:
        raise EngineError("toolong", "exceeded size cap")
    return p.returncode, "\n".join(tail)

def dir_size(d):
    t = 0
    for root, _, files in os.walk(d):
        for f in files:
            try: t += os.path.getsize(os.path.join(root, f))
            except OSError: pass
    return t

# ---------------------------------------------------------------- probing
def _est_size(f, duration):
    s = f.get("filesize") or f.get("filesize_approx")
    if not s and f.get("tbr") and duration:
        s = f["tbr"] * 1000 / 8 * duration
    return int(s) if s else None

def _qual(f):
    h, w = f.get("height"), f.get("width")
    return int(min(h, w)) if h and w else int(h)

def _is_avc(f):
    return (f.get("vcodec") or "").lower().startswith(("avc", "h264"))

def summarize_formats(info):
    """info = yt-dlp json -> {"quals": {q: {"fid","size","muxed"}}, "audio_size", "has_audio"}.
    q is the short-side resolution (so a 1080x1920 Short is '1080p'). For each q the H.264 variant is preferred
    (plays everywhere in Telegram), then the smallest one. Watermarked TikTok variants are skipped when others exist."""
    dur = info.get("duration") or 0
    fmts = info.get("formats") or []
    vids = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("height") and f.get("format_id")]
    clean = [f for f in vids if "watermark" not in (f.get("format_note") or "").lower() and f.get("format_id") != "download"]
    vids = clean or vids
    auds = [f for f in fmts if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    a_best = max(auds, key=lambda f: (f.get("abr") or 0)) if auds else None
    a_size = _est_size(a_best, dur) if a_best else None
    by_q = {}
    for f in vids:
        by_q.setdefault(_qual(f), []).append(f)
    quals = {}
    for q, fs in by_q.items():
        def key(f):
            s = _est_size(f, dur)
            return (0 if _is_avc(f) else 1, 0 if f.get("protocol", "").startswith("http") else 1, s if s is not None else 10**12)
        f = sorted(fs, key=key)[0]
        s = _est_size(f, dur)
        muxed = f.get("acodec") not in (None, "none")
        tot = None if s is None else s + (0 if muxed else (a_size or 0))
        quals[q] = {"fid": f["format_id"], "size": tot, "muxed": muxed}
    # audio_expected: False only when EVERY video format explicitly says acodec=none and no audio-only format exists
    # (e.g. a muted Instagram reel). Unknown (None) acodec counts as "probably has audio" -> we verify after download.
    unknown_prog = any(f.get("vcodec") in (None, "none") and f.get("acodec") in (None, "none") and f.get("ext") in ("mp4", "m4v", "mov")
                       and f.get("format_id") for f in fmts if f.get("vcodec") is None)
    explicit_silent = bool(vids) and all(f.get("acodec") == "none" for f in vids) and not auds and not unknown_prog
    return {"quals": dict(sorted(quals.items(), reverse=True)), "audio_size": a_size,
            "has_audio": bool(auds) or any(f.get("acodec") not in (None, "none") for f in vids),
            "audio_expected": not explicit_silent}

def ladder_options(heights):
    """Quality ladder to offer: standard heights <= max available (closest available at or below each rung)."""
    avail = sorted(heights, reverse=True)
    if not avail:
        return []
    out = []
    for rung in LADDER:
        c = [h for h in avail if h <= rung]
        if not c:
            continue
        if rung > avail[0] and out:          # rung above the best is meaningless
            continue
        h = c[0]
        if h not in out and (rung <= avail[0] or not out):
            out.append(h)
    if not out:
        out = [avail[-1]]
    return out

def pick_best_fit(quals, limit=SAFE_LIMIT, below=None):
    """Highest quality whose estimated size fits (optionally only those < `below`). Unknown size counts as fitting
    (it is verified after download). Returns q or None."""
    for q in sorted(quals, reverse=True):
        if below is not None and q >= below:
            continue
        s = quals[q]["size"]
        if s is None or s <= limit:
            return q
    return None

def probe_ytdlp(url, platform):
    cmd = _ytdlp_base(platform) + ["-J", url]
    rc, out, err = run_capture(cmd, PROBE_TIMEOUT)
    if rc != 0 or not out.strip().startswith("{"):
        raise EngineError(classify_error(err), err)
    info = json.loads(out)
    return info

def info_to_video(info, platform, url):
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise EngineError("nomedia", "empty playlist")
        if len(entries) > 1:
            return {"kind": "gallery", "platform": platform, "url": url, "title": info.get("title") or "",
                    "uploader": info.get("uploader") or "", "entries": len(entries), "via": "ytdlp",
                    "caption": build_caption_text(info, platform)}
        info = entries[0]
    dur = info.get("duration") or 0
    if dur and dur > MAX_DURATION:
        raise EngineError("toolong", "duration %s" % dur)
    fs = summarize_formats(info)
    live = info.get("is_live")
    if live:
        raise EngineError("unsupported", "live stream")
    return {"kind": "video", "platform": platform, "url": url, "title": (info.get("title") or "").strip()[:200] or "video",
            "uploader": info.get("uploader") or info.get("channel") or "", "duration": int(dur or 0),
            "quals": fs["quals"], "audio_size": fs["audio_size"], "has_audio": fs["has_audio"],
            "audio_expected": True if platform == "instagram" else fs["audio_expected"], "caption": build_caption_text(info, platform),
            "thumb": info.get("thumbnail"), "via": "ytdlp", "id": info.get("id")}

GENERIC_TITLE_RE = re.compile(r"^(Pinterest video #\d+|Video by .+|Photo by .+|Post by .+|video|Instagram|Pinterest)$", re.I)
CAPTION_RAW_MAX = 3900

def build_caption_text(info, platform):
    """Original post text from yt-dlp info: description (TikTok/IG/Pinterest) / title + description (YouTube)."""
    title = (info.get("title") or "").strip()
    desc = (info.get("description") or "").strip()
    if platform == "youtube":
        txt = title + ("\n\n" + desc if desc else "")
    elif desc:
        txt = desc
    else:
        txt = "" if GENERIC_TITLE_RE.match(title) else title
    return txt.strip()[:CAPTION_RAW_MAX]

def gallery_caption(meta):
    """Original caption from gallery-dl metadata (Instagram: description; highlights: highlight_title; Pinterest: title/description)."""
    for k in ("description", "highlight_title", "title", "closeup_description", "grid_title"):
        v = meta.get(k)
        if isinstance(v, str) and v.strip() and not GENERIC_TITLE_RE.match(v.strip()):
            return v.strip()[:CAPTION_RAW_MAX]
    return ""

def gallery_list(url, cookies_platform=None, timeout=PROBE_TIMEOUT):
    """gallery-dl -j -> list of {"url","ext","type","w","h"} + meta."""
    cmd = [PY, "-m", "gallery_dl", "-j", "--no-input", "-o", "user-agent=" + UA]
    cp = cookie_path(cookies_platform) if cookies_platform else None
    if cp:
        cmd += ["--cookies", cp]
    cmd += [url]
    rc, out, err = run_capture(cmd, timeout)
    try:
        data = json.loads(out) if out.strip() else []
    except Exception:
        data = []
    items, meta, errs = [], {}, []
    for e in data:
        if not isinstance(e, list) or not e:
            continue
        if e[0] == -1 and len(e) > 1 and isinstance(e[1], dict):
            errs.append("%s: %s" % (e[1].get("error"), e[1].get("message")))
        elif e[0] == 2 and len(e) > 1 and isinstance(e[1], dict):
            meta = e[1]
        elif e[0] == 3 and len(e) > 2 and isinstance(e[1], str):
            m = e[2] if isinstance(e[2], dict) else {}
            ext = (m.get("extension") or os.path.splitext(urlparse(e[1]).path)[1].lstrip(".") or "jpg").lower()
            typ = "video" if ext in ("mp4", "webm", "mov", "m4v", "mkv") else "image"
            items.append({"url": e[1], "ext": ext, "type": typ, "w": m.get("width"), "h": m.get("height")})
    if not items:
        msg = " | ".join(errs) or err or "no items"
        code = classify_error(msg)
        if code == "unknown" and errs:
            code = "nomedia"
        raise EngineError(code, msg)
    return items, meta

def meta_title(meta, default=""):
    for k in ("title", "description", "caption"):
        v = meta.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().replace("\n", " ")[:120]
    return default

def probe(c):
    """c = classify() dict -> info dict for the bot UI. Raises EngineError."""
    plat, kind, url = c["platform"], c["kind"], c["url"]
    if kind == "resolve":
        if plat == "linkedin":
            import linkedin
            url = linkedin.resolve_short(url)
        else:
            url = resolve_redirect(url)
        if plat == "twitter" and "/status/" not in url:
            raise EngineError("unsupported", "t.co target is not a tweet")
        c2 = classify(url)
        if not c2 or c2["kind"] == "resolve":
            raise EngineError("unsupported", "unresolvable short link")
        return probe(c2)
    if plat == "linkedin":
        import linkedin
        return linkedin.probe(dict(c, url=url))
    if plat == "threads":
        import threads
        return threads.probe(c)
    if plat == "twitter":
        import twitterx
        return twitterx.probe(c)
    if plat == "soundcloud":
        return probe_soundcloud(c)
    if plat == "spotify":
        return probe_spotify(c)
    if plat in ("youtube", "tiktok") or (plat == "pinterest") or (plat == "instagram" and kind == "post"):
        try:
            info = probe_ytdlp(url, plat)
            v = info_to_video(info, plat, url)
            if v["kind"] == "gallery":
                items, meta = gallery_list(url, "instagram" if plat == "instagram" else None)
                return _gallery_info(plat, url, items, meta)
            return v
        except EngineError as e:
            if plat == "youtube" or e.code in ("toolong", "timeout", "age", "geo", "private", "rate", "ipblock"):
                if not (plat in ("instagram", "pinterest") and e.code in ("private", "ipblock", "rate")):
                    raise
            if plat == "tiktok" and e.code in ("ipblock", "notfound", "rate"):
                raise
            first = e
        # fallback: gallery-dl (images, carousels, photo slideshows)
        try:
            items, meta = gallery_list(url, "instagram" if plat == "instagram" else None)
            return _gallery_info(plat, url, items, meta)
        except EngineError as e2:
            # keep the most informative error: a login error from gallery-dl beats yt-dlp "unknown"
            raise (e2 if first.code in ("unknown", "nomedia") and e2.code != "unknown" else first)
    if plat == "instagram" and kind in ("story", "stories", "highlight"):
        if not cookie_path("instagram"):
            raise EngineError("cookies_needed", "no instagram cookies")
        items, meta = gallery_list(url, "instagram")
        return _gallery_info(plat, url, items, meta, kind=kind)
    if plat == "instagram" and kind == "profile":
        return {"kind": "profile", "platform": "instagram", "user": c["user"], "url": url}
    raise EngineError("unsupported", "kind %s" % kind)

def _gallery_info(plat, url, items, meta, kind="gallery"):
    u = meta.get("username")
    if not u and isinstance(meta.get("user"), dict):
        u = meta["user"].get("username")
    if not u and isinstance(meta.get("pinner"), dict):
        u = meta["pinner"].get("username")
    return {"kind": "gallery", "platform": plat, "url": url, "items": items, "sub": kind,
            "title": meta_title(meta, "Instagram" if plat == "instagram" else plat.title()), "uploader": u or "",
            "caption": gallery_caption(meta)}

# ---------------------------------------------------------------- SoundCloud / Spotify
def max_tracks():
    try:
        import store
        return max(1, min(50, int(store.settings().get("max_tracks", 10))))
    except Exception:
        return 10

def _audio_caption(title, artist, desc=""):
    parts = ["🎵 " + title.strip()] if title else []
    if artist:
        parts.append("👤 " + artist.strip())
    txt = "\n".join(parts)
    if desc and desc.strip():
        txt += "\n\n" + desc.strip()
    return txt[:CAPTION_RAW_MAX]

def sc_track_info(info, url):
    dur = int(info.get("duration") or 0)
    if dur > MAX_DURATION:
        raise EngineError("toolong", "duration %s" % dur)
    artist = info.get("artist") or info.get("uploader") or ""
    title = (info.get("title") or "").strip() or "track"
    return {"kind": "audio", "platform": "soundcloud", "url": url, "title": title[:200], "uploader": artist,
            "duration": dur, "thumb": info.get("thumbnail"), "via": "ytdlp", "id": info.get("id"),
            "caption": _audio_caption(title, artist, info.get("description") or "")}

def probe_soundcloud(c):
    url = c["url"]
    if c["kind"] == "audio":
        return sc_track_info(probe_ytdlp(url, "soundcloud"), url)
    # set / album: flat listing, first N entries only
    n = max_tracks()
    cmd = [x for x in _ytdlp_base("soundcloud") if x != "--no-playlist"] + ["--yes-playlist", "--flat-playlist",
                                                                          "--playlist-end", str(n), "-J", url]
    rc, out, err = run_capture(cmd, PROBE_TIMEOUT)
    if rc != 0 or not out.strip().startswith("{"):
        raise EngineError(classify_error(err), err)
    j = json.loads(out)
    entries = [x for x in (j.get("entries") or []) if x and x.get("url")]
    if not entries:
        raise EngineError("nomedia", "empty set")
    total = int(j.get("playlist_count") or len(entries))
    return {"kind": "collection", "platform": "soundcloud", "url": url, "title": (j.get("title") or "SoundCloud")[:150],
            "uploader": j.get("uploader") or "", "total": total,
            "items": [{"url": x["url"]} for x in entries[:n]], "thumb": None}

# --- Spotify: public metadata only (embed page JSON, oEmbed / page meta fallback), audio comes from YouTube
SP_EMBED = "https://open.spotify.com/embed/%s/%s"

def _best_image(ent):
    imgs = ((ent.get("visualIdentity") or {}).get("image")) or []
    if not imgs:
        imgs = ((ent.get("coverArt") or {}).get("extractedColors") and []) or (ent.get("coverArt") or {}).get("sources") or []
    imgs = [i for i in imgs if i.get("url")]
    if not imgs:
        return None
    return max(imgs, key=lambda i: (i.get("maxWidth") or i.get("width") or 0))["url"]

def spotify_entity(sp_type, sp_id):
    import requests
    try:
        r = requests.get(SP_EMBED % (sp_type, sp_id), headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=20)
    except Exception as ex:
        raise EngineError("timeout", str(ex))
    if r.status_code == 404:
        raise EngineError("notfound", "spotify 404")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    ent = None
    if m:
        try:
            ent = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
        except Exception:
            ent = None
    if not ent or not (ent.get("title") or ent.get("name")):
        raise EngineError("notfound", "no spotify metadata")
    return ent

def _spotify_track_fallback(sp_id):
    """oEmbed gives the title + cover; the artist comes from the page's og:description ('Artist · Song · 2020')."""
    import requests
    j = requests.get("https://open.spotify.com/oembed", params={"url": "https://open.spotify.com/track/" + sp_id},
                     headers={"User-Agent": UA}, timeout=15).json()
    title, artist = j.get("title") or "", ""
    try:
        h = requests.get("https://open.spotify.com/track/" + sp_id, headers={"User-Agent": UA}, timeout=15).text
        d = re.search(r'property="og:description" content="([^"]+)"', h)
        if d:
            artist = d.group(1).split("·")[0].strip()
    except Exception:
        pass
    return {"title": title, "artist": artist, "duration": 0, "cover": j.get("thumbnail_url")}

def spotify_track_meta(sp_id):
    try:
        ent = spotify_entity("track", sp_id)
        artists = [a.get("name") for a in (ent.get("artists") or []) if a.get("name")]
        if not artists and ent.get("subtitle"):
            artists = [ent["subtitle"]]
        return {"id": sp_id, "title": (ent.get("title") or ent.get("name") or "").strip(),
                "artist": ", ".join(artists), "artists": artists, "duration": int((ent.get("duration") or 0) / 1000),
                "cover": _best_image(ent), "year": ((ent.get("releaseDate") or {}).get("isoString") or "")[:4]}
    except EngineError as e:
        if e.code == "notfound":
            try:
                fb = _spotify_track_fallback(sp_id)
                if fb["title"]:
                    fb.update({"id": sp_id, "artists": [fb["artist"]] if fb["artist"] else [], "year": ""})
                    return fb
            except Exception:
                pass
        raise

_BAD_WORDS = ("live", "cover", "remix", "karaoke", "instrumental", "sped up", "speed up", "slowed", "reverb", "8d",
              "nightcore", "tutorial", "reaction", "loop", "mashup", "acoustic", "piano version", "bass boosted", "lesson")

def _norm(s):
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", (s or "").lower())
    s = re.sub(r"[^\w\s]", " ", s, flags=re.U)
    return " ".join(s.split())

def score_candidate(cand, meta):
    """Score a YouTube search result against Spotify metadata. Returns (score, reason); score < 0 = reject."""
    title_raw = (cand.get("title") or "")
    t = title_raw.lower()
    tn, sn = _norm(title_raw), _norm(meta["title"])
    an = _norm(meta.get("artist") or "")
    first_artist = _norm((meta.get("artists") or [meta.get("artist") or ""])[0])
    chan = (cand.get("channel") or cand.get("uploader") or "")
    cn = _norm(chan)
    dur, sdur = cand.get("duration"), meta.get("duration") or 0
    score = 0
    if dur:
        if dur > 1200:
            return -1, "too long"
        if sdur:
            d = abs(dur - sdur)
            if d > max(15, sdur * 0.12):
                return -1, "duration off by %ss" % d
            score += 40 if d <= 3 else 30 if d <= 6 else 15 if d <= 12 else 5
    if sn and sn in tn:
        score += 30
    elif sn and set(sn.split()) <= set(tn.split()):
        score += 20
    elif sn and cn and sn in cn:
        score += 5
    else:
        return -1, "title mismatch"
    if first_artist and (first_artist in tn or first_artist in cn):
        score += 20
    elif an and any(w in tn for w in an.split() if len(w) > 3):
        score += 8
    else:
        score -= 15
    if chan.endswith("- Topic"):
        score += 25
    if "official audio" in t or "audio only" in t:
        score += 12
    if "vevo" in cn:
        score += 5
    if "official" in t and "video" in t:
        score += 3
    if "lyric" in t:
        score -= 3
    for w in _BAD_WORDS:
        if re.search(r"(?<!\w)%s(?!\w)" % re.escape(w), t) and not re.search(r"(?<!\w)%s(?!\w)" % re.escape(w), meta["title"].lower()):
            score -= 60          # a different version of the song (live/cover/remix/...) is worse than no match
    return score, "ok"

MATCH_MIN = 45

def spotify_match(meta):
    """-> best YouTube candidate dict (id,title,channel,duration,score) or raise EngineError('nomedia')."""
    queries = ["%s - %s" % (meta.get("artist") or "", meta["title"]),
               "%s %s official audio" % (meta.get("artist") or "", meta["title"])]
    seen, best = set(), None
    for q in queries:
        cmd = _ytdlp_base("youtube") + ["--flat-playlist", "-J", "ytsearch8:" + q.strip()]
        rc, out, err = run_capture(cmd, PROBE_TIMEOUT)
        if rc != 0 or not out.strip().startswith("{"):
            if best:
                break
            raise EngineError(classify_error(err), err)
        for x in json.loads(out).get("entries") or []:
            if not x or not x.get("id") or x["id"] in seen:
                continue
            seen.add(x["id"])
            sc, why = score_candidate(x, meta)
            log.info("spotify match cand id=%s score=%s (%s) dur=%s chan=%r title=%r", x["id"], sc, why, x.get("duration"),
                     x.get("channel"), (x.get("title") or "")[:60])
            if sc >= MATCH_MIN and (best is None or sc > best["score"]):
                best = {"id": x["id"], "title": x.get("title") or "", "channel": x.get("channel") or x.get("uploader") or "",
                        "duration": x.get("duration") or 0, "score": sc}
        if best and best["score"] >= 90:
            break
    if not best:
        raise EngineError("nomedia", "no confident YouTube match for %r" % meta["title"])
    return best

def spotify_audio_info(meta):
    """Metadata -> audio info. Uses the music provider chain (Radio Javan / SoundCloud / Audius / Bandcamp, YouTube Music last)."""
    import music
    return music.audio_info(meta)

def probe_spotify(c):
    if c["kind"] == "audio":
        return spotify_audio_info(spotify_track_meta(c["sp_id"]))
    ent = spotify_entity(c["sp_type"], c["sp_id"])
    tl = [t for t in (ent.get("trackList") or []) if (t.get("uri") or "").startswith("spotify:track:")]
    if not tl:
        raise EngineError("nomedia", "empty collection")
    n = max_tracks()
    items = [{"sp_id": t["uri"].split(":")[-1], "title": t.get("title") or "", "artist": (t.get("subtitle") or "").replace("\xa0", " "),
              "duration": int((t.get("duration") or 0) / 1000)} for t in tl[:n]]
    return {"kind": "collection", "platform": "spotify", "url": c["url"], "sp_type": c["sp_type"],
            "title": (ent.get("title") or ent.get("name") or "Spotify")[:150], "uploader": ent.get("subtitle") or "",
            "total": len(tl), "items": items, "thumb": _best_image(ent)}

def resolve_item(info, item):
    """Turn one collection item into a downloadable audio info (Spotify: match on YouTube; SoundCloud: probe)."""
    if info["platform"] == "spotify":
        return spotify_audio_info({"id": item["sp_id"], "title": item["title"], "artist": item["artist"],
                                   "artists": [x.strip() for x in item["artist"].split(",") if x.strip()],
                                   "duration": item.get("duration") or 0, "cover": info.get("thumb")})
    return sc_track_info(probe_ytdlp(item["url"], "soundcloud"), item["url"])

# ---------------------------------------------------------------- downloading
PROG_RE = re.compile(r"PROG\|\s*([\d.]+)%")

def _newest(d, exts):
    files = [f for f in glob.glob(os.path.join(d, "*")) if os.path.isfile(f) and f.lower().endswith(exts)]
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    return files[0] if files else None

def _run_ytdlp(platform, url, workdir, fmt, extra, on_progress):
    cmd = _ytdlp_base(platform) + ["--newline", "--progress-template", "download:PROG|%(progress._percent_str)s",
                                   "-o", os.path.join(workdir, "%(id)s.%(ext)s"), "--max-filesize", "%dM" % (MAX_DOWNLOAD_BYTES // 1024 // 1024)]
    if fmt:
        cmd += ["-f", fmt]
    cmd += extra + [url]
    def on_line(line):
        m = PROG_RE.search(line)
        if m and on_progress:
            on_progress(float(m.group(1)))
    rc, tail = run_stream(cmd, DL_TIMEOUT, on_line, size_watch_dir=workdir)
    if rc != 0:
        raise EngineError(classify_error(tail), tail)

def has_audio_stream(path):
    """True/False from ffprobe; None if ffprobe is unavailable or fails (treated as 'unknown, assume ok')."""
    ff = shutil.which("ffprobe")
    if not ff:
        return None
    try:
        rc, out, _ = run_capture([ff, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
                                  "-of", "json", path], 30)
        if rc != 0:
            return None
        return len(json.loads(out).get("streams") or []) > 0
    except Exception:
        return None

def stream_info(path):
    """ffprobe -> {"v": {codec,profile,pix_fmt}|None, "a": {codec,profile,channels,rate}|None, "dur": float}; {} if unavailable."""
    ff = shutil.which("ffprobe")
    if not ff:
        return {}
    try:
        rc, out, _ = run_capture([ff, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], 30)
        j = json.loads(out)
    except Exception:
        return {}
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), None)
    au = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {"v": {"codec": v.get("codec_name"), "profile": v.get("profile"), "pix_fmt": v.get("pix_fmt")} if v else None,
            "a": {"codec": au.get("codec_name"), "profile": au.get("profile"), "channels": au.get("channels"),
                  "rate": au.get("sample_rate")} if au else None,
            "dur": float((j.get("format") or {}).get("duration") or 0)}

def describe_streams(si):
    if not si:
        return "ffprobe-unavailable"
    v, a = si.get("v"), si.get("a")
    return "video=%s audio=%s" % ("%s/%s" % (v["codec"], v["pix_fmt"]) if v else "NONE",
                                  "%s/%s/%sch/%sHz" % (a["codec"], a["profile"], a["channels"], a["rate"]) if a else "NONE")

REENCODE_VIDEO = ("vp9", "vp8", "av1")

def needs_compat(si):
    """True when the file is not plain H.264/HEVC + AAC-LC (stereo). Instagram ships VP9 video and/or HE-AAC (SBR)
    audio, which some Telegram clients decode silently or not at all."""
    if not si or not si.get("v"):
        return False
    v, a = si["v"], si.get("a")
    if v["codec"] in REENCODE_VIDEO or (v.get("pix_fmt") not in (None, "yuv420p", "yuvj420p", "yuv420p10le")):
        return True
    if a and (a["codec"] != "aac" or (a.get("profile") or "LC") != "LC" or (a.get("channels") or 2) > 2):
        return True
    return False

def telegram_compat(path, workdir):
    """Normalise to Telegram-safe streams: AAC-LC stereo 44.1 kHz audio (always mapped explicitly with -map 0:a:0?,
    never -an), video stream copied unless it is VP8/VP9/AV1 (then H.264). mp4 + faststart. Returns (path, changed);
    on any failure, or if audio would be lost, the original file is kept."""
    si = stream_info(path)
    if not needs_compat(si):
        return path, False
    ff = ffmpeg_path()
    if not ff:
        return path, False
    out = os.path.join(workdir, "compat_" + os.path.splitext(os.path.basename(path))[0] + ".mp4")
    vcopy = si["v"]["codec"] not in REENCODE_VIDEO and si["v"].get("pix_fmt") in (None, "yuv420p", "yuvj420p", "yuv420p10le")
    cmd = [ff, "-y", "-i", path, "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy" if vcopy else "libx264"]
    if not vcopy:
        cmd += ["-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    cmd += ["-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k", "-ac", "2", "-ar", "44100", "-movflags", "+faststart", out]
    try:
        rc, _, err = run_capture(cmd, 900)
    except EngineError:
        return path, False
    if rc != 0 or not os.path.isfile(out) or os.path.getsize(out) < 1000:
        log.warning("compat transcode failed (rc=%s); keeping original", rc)
        return path, False
    so = stream_info(out)
    if si.get("a") and not so.get("a"):
        log.warning("compat transcode LOST audio; keeping original")
        return path, False
    log.info("compat: [%s] -> [%s]", describe_streams(si), describe_streams(so))
    return out, True

def _clear_media(workdir):
    for f in glob.glob(os.path.join(workdir, "*")):
        if os.path.isfile(f):
            try: os.remove(f)
            except OSError: pass

def _keep(path, workdir):
    d = os.path.join(workdir, "keep"); os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, os.path.basename(path)); shutil.move(path, dst)
    return dst

def video_attempts(info, q):
    """Ordered (format, extra-args) attempts. Every one of them asks for video AND audio and merges with ffmpeg
    (mp4). First = exact format chosen at probe time; then generic bv*+ba at the same resolution cap; then the best
    pre-merged single file ('b') as a last yt-dlp resort."""
    quals = info.get("quals") or {}
    merge = ["--merge-output-format", "mp4"]
    cap = ["-S", "res:%d" % q] if q else []
    ff = ffmpeg_path()
    att = []
    if q in quals:
        f = quals[q]
        # NOTE: no bare "<fid>" alternative at the end -- that silently accepted a video-only stream (no audio)
        # whenever the audio part could not be selected. Missing audio now falls through to the next attempt.
        fmt = f["fid"] if f["muxed"] else f["fid"] + "+ba[ext=m4a]/" + f["fid"] + "+ba"
        att.append((fmt, merge))
    else:
        yt = ["-S", "res,vcodec:h264,acodec:aac"] if info["platform"] == "youtube" else []
        att.append(("bv*+ba/b", yt + merge))
    att.append(("bv*+ba/b", cap + merge))
    att.append(("b/bv*+ba", cap + merge))
    if not ff:                       # no ffmpeg -> cannot merge separate streams: only pre-merged files make sense
        att = [("b/bv*", cap)] + att[-1:]
    return att

def download_video_raw(info, q, workdir, on_progress=None):
    """Download one video at quality q (key of info['quals']; None = best). Returns file path.
    The result is verified with ffprobe: if audio is expected but the file has no audio stream we retry with other
    format selections and, for Instagram, with gallery-dl (DASH merged / pre-merged). If it's still silent the
    file is returned with info['_audio_missing']=True (the caller tells the user)."""
    info.pop("_audio_missing", None)
    if info.get("direct_url"):                       # LinkedIn / Threads / X: mp4 URL taken from the page / public API (no yt-dlp extractor)
        _clear_media(workdir)
        dst = os.path.join(workdir, "direct_video.mp4")
        download_direct(info["direct_url"], dst)
        return dst
    expect = info.get("audio_expected", True)
    kept, last = None, None
    if info.get("fallback_url"):                     # X: yt-dlp first, the public-API mp4 if yt-dlp cannot fetch it any more
        try:
            return _download_video_ytdlp(info, q, workdir, on_progress, expect)
        except EngineError as e:
            if e.code in ("toolong",): raise
            log.info("twitter yt-dlp download failed (%s) -> API mp4", e.code)
            _clear_media(workdir)
            dst = os.path.join(workdir, "direct_video.mp4")
            download_direct(info["fallback_url"], dst)
            return dst
    return _download_video_ytdlp(info, q, workdir, on_progress, expect)

def _download_video_ytdlp(info, q, workdir, on_progress, expect):
    kept, last = None, None
    for i, (fmt, extra) in enumerate(video_attempts(info, q)):
        _clear_media(workdir)
        try:
            _run_ytdlp(info["platform"], info["url"], workdir, fmt, extra, on_progress)
        except EngineError as e:
            last = e
            if e.code not in ("unknown", "nomedia"):
                raise
            continue
        out = _newest(workdir, (".mp4", ".mkv", ".webm", ".mov"))
        if not out:
            last = EngineError("nomedia", "no output file"); continue
        au = has_audio_stream(out)
        if au is not False or not expect:
            return out
        log.warning("attempt %d produced a video WITHOUT audio (%s, %s); trying another selection", i + 1, info["platform"], fmt[:40])
        kept = kept or _keep(out, workdir)
    if info["platform"] == "instagram":
        try:
            _clear_media(workdir)
            out, ok_audio = download_gallery_video(info, None, workdir)
            if ok_audio is not False:
                return out
            kept = kept or _keep(out, workdir)
        except EngineError as e:
            last = last or e
    if kept:
        info["_audio_missing"] = True
        return kept
    raise last or EngineError("nomedia", "no output file")

def download_video(info, q, workdir, on_progress=None):
    """download_video_raw + Telegram-compat normalisation + per-download diagnostics in bot.log."""
    path = download_video_raw(info, q, workdir, on_progress)
    si = stream_info(path)
    log.info("DL platform=%s via=%s q=%s cookies=%s fmt=%s size=%s streams[%s] audio_expected=%s audio_missing=%s",
             info["platform"], "yt-dlp", q, bool(cookie_path(info["platform"])),
             ((info.get("quals") or {}).get(q) or {}).get("fid"), os.path.getsize(path), describe_streams(si),
             info.get("audio_expected"), bool(info.get("_audio_missing")))
    if info["platform"] in ("instagram", "threads", "twitter"):
        path = _normalise(path, workdir, info)
    return path

def _normalise(path, workdir, info=None):
    new, changed = telegram_compat(path, workdir)
    if changed:
        au = has_audio_stream(new)
        if au is False and has_audio_stream(path) is not False:
            log.warning("normalised file has no audio although source had; using source")
            return path
        if info is not None and info.get("_audio_missing") and au:
            info.pop("_audio_missing", None)
        return new
    return path

def download_gallery_video(info, index, workdir):
    """Instagram video through gallery-dl. `index` = 0-based position in info['items'] (None = single video post).
    Tries DASH (yt-dlp merges separate video+audio with ffmpeg) then the pre-merged progressive file; returns
    (path, has_audio) preferring a file that has an audio stream."""
    first = None
    for mode in ("dash", "merged"):
        sub = os.path.join(workdir, "gd_" + mode); os.makedirs(sub, exist_ok=True)
        cmd = [PY, "-m", "gallery_dl", "--no-input", "-D", sub, "-o", "user-agent=" + UA, "-o", "videos=" + mode,
               "-o", "filename={num:>03}.{extension}"]
        cp = cookie_path("instagram")
        if cp: cmd += ["--cookies", cp]
        if index is not None: cmd += ["--range", str(index + 1)]
        cmd += [info["url"]]
        try:
            run_stream(cmd, DL_TIMEOUT, None, size_watch_dir=workdir)
        except EngineError:
            continue
        vids = sorted(f for f in glob.glob(os.path.join(sub, "*")) if f.lower().endswith((".mp4", ".mov", ".webm", ".mkv", ".m4v")))
        if not vids:
            continue
        au = has_audio_stream(vids[0])
        log.info("DL platform=instagram via=gallery-dl mode=%s cookies=%s streams[%s]", mode, bool(cookie_path("instagram")),
                 describe_streams(stream_info(vids[0])))
        if au is not False:
            n = _normalise(vids[0], workdir)
            return n, has_audio_stream(n)
        first = first or vids[0]
    if first:
        return first, False
    raise EngineError("nomedia", "gallery-dl produced no video")

def download_audio(info, fmt_name, workdir, on_progress=None):
    """fmt_name 'mp3' or 'm4a'. Returns file path."""
    if info.get("dl_cands"):                      # music chain (Spotify links / recognition / search): try the ranked sources
        import music
        return music.download_audio(info, fmt_name, workdir, on_progress)
    if info.get("direct_url"):                    # Threads / LinkedIn / X-API videos: fetch the mp4, extract its audio with ffmpeg
        ff = ffmpeg_path()
        if not ff: raise EngineError("unknown", "ffmpeg missing")
        src = os.path.join(workdir, "direct_src.mp4"); download_direct(info["direct_url"], src)
        if has_audio_stream(src) is False: raise EngineError("nomedia", "video has no audio track")
        out = os.path.join(workdir, "audio." + fmt_name)
        cod = ["-c:a", "aac", "-b:a", "192k"] if fmt_name == "m4a" else ["-c:a", "libmp3lame", "-b:a", "192k"]
        rc, _, err = run_capture([ff, "-y", "-i", src, "-vn"] + cod + [out], 600)
        if rc != 0 or not os.path.isfile(out): raise EngineError("unknown", err)
        return out
    if fmt_name == "m4a":
        extra = ["-x", "--audio-format", "m4a", "--audio-quality", "0"]
        f = "ba[ext=m4a]/ba/b"
    else:
        extra = ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
        f = "ba/b"
    _run_ytdlp(info.get("dl_platform") or info["platform"], info["url"], workdir, f, extra, on_progress)
    out = _newest(workdir, (".mp3", ".m4a", ".opus", ".aac", ".ogg"))
    if not out:
        raise EngineError("nomedia", "no audio output")
    return out

def reencode_audio(path, kbps, workdir):
    out = os.path.join(workdir, "fit_%dk.mp3" % kbps)
    ff = ffmpeg_path()
    if not ff:
        raise EngineError("unknown", "ffmpeg missing")
    rc, _, err = run_capture([ff, "-y", "-i", path, "-vn", "-b:a", "%dk" % kbps, "-ac", "1" if kbps < 64 else "2", out], 300)
    if rc != 0 or not os.path.isfile(out):
        raise EngineError("unknown", err)
    return out

def fit_audio_bitrate(duration, limit=SAFE_LIMIT):
    """Highest standard bitrate (kbps) that fits `duration` seconds in `limit` bytes, or None (<32 kbps)."""
    if not duration:
        return 128
    for k in (192, 128, 96, 64, 48, 32):
        if duration * k * 1000 / 8 <= limit:
            return k
    return None

def download_gallery(info, workdir, which=None, on_progress=None):
    """Download gallery items with gallery-dl. `which` = 1-based index or None for all. Returns sorted file list."""
    cmd = [PY, "-m", "gallery_dl", "--no-input", "-D", workdir, "-o", "user-agent=" + UA, "-o", "filename={num:>03}.{extension}"]
    cp = cookie_path("instagram") if info["platform"] == "instagram" else None
    if cp:
        cmd += ["--cookies", cp]
    if which:
        cmd += ["--range", str(which)]
    cmd += [info["url"]]
    rc, tail = run_stream(cmd, DL_TIMEOUT, None, size_watch_dir=workdir)
    files = sorted(f for f in glob.glob(os.path.join(workdir, "*")) if os.path.isfile(f)
                   and not f.endswith((".part", ".json", ".txt")))
    if not files:
        raise EngineError(classify_error(tail) if rc else "nomedia", tail)
    return files

def download_direct(url, dest, limit=MAX_DOWNLOAD_BYTES):
    import requests
    r = requests.get(url, stream=True, timeout=30, headers={"User-Agent": UA})
    if r.status_code != 200:
        raise EngineError("notfound", "HTTP %s" % r.status_code)
    n = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(65536):
            n += len(chunk)
            if n > limit:
                raise EngineError("toolong", "too large")
            f.write(chunk)
    return dest

# ---------------------------------------------------------------- Instagram profile picture
def profile_picture(user, workdir):
    """Best-effort highest-res avatar. Returns (path, hires: bool).
    1) gallery-dl avatar extractor (full-size; usually needs cookies from datacenter IPs)
    2) og:image scrape of the public profile page (small, low-res) -> hires False"""
    last = None
    try:
        items, _ = gallery_list("https://www.instagram.com/%s/avatar/" % user, "instagram", timeout=60)
        dst = os.path.join(workdir, "%s.jpg" % user)
        download_direct(items[0]["url"], dst)
        return dst, True
    except EngineError as e:
        last = e
        log.info("avatar via gallery-dl failed: %s", e.code)
    try:
        import requests, html as _html
        r = requests.get("https://www.instagram.com/%s/" % user, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        m = re.search(r'property="og:image"\s+content="([^"]+)"', r.text) or re.search(r'content="([^"]+)"\s+property="og:image"', r.text)
        if m and r.status_code == 200:
            u = _html.unescape(m.group(1))
            dst = os.path.join(workdir, "%s.jpg" % user)
            download_direct(u, dst, 10 * 1024 * 1024)
            return dst, False
    except EngineError as e:
        last = last or e
    except Exception as e:
        last = last or EngineError("unknown", str(e))
    raise last or EngineError("notfound", "no avatar")

# ---------------------------------------------------------------- ffprobe
def probe_media(path):
    """-> dict(width,height,duration) via ffprobe (best effort)."""
    ff = shutil.which("ffprobe")
    if not ff:
        return {}
    try:
        rc, out, _ = run_capture([ff, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], 30)
        j = json.loads(out)
        v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
        return {"width": v.get("width"), "height": v.get("height"), "duration": int(float(j.get("format", {}).get("duration") or 0))}
    except Exception:
        return {}

def make_thumb(path, workdir):
    ff = ffmpeg_path()
    if not ff:
        return None
    out = os.path.join(workdir, "thumb.jpg")
    try:
        run_capture([ff, "-y", "-ss", "1", "-i", path, "-frames:v", "1", "-vf", "scale=320:-2", out], 30)
        if not os.path.isfile(out):
            run_capture([ff, "-y", "-i", path, "-frames:v", "1", "-vf", "scale=320:-2", out], 30)
        return out if os.path.isfile(out) else None
    except Exception:
        return None

def versions():
    def v(mod):
        try:
            r = subprocess.run([PY, "-m", mod, "--version"], capture_output=True, timeout=30, env=_env())
            return r.stdout.decode().strip().splitlines()[0] if r.stdout else "?"
        except Exception:
            return "?"
    return v("yt_dlp"), v("gallery_dl")

def update_engine():
    """pip install -U yt-dlp gallery-dl (inside the venv). Returns (ok, output_tail)."""
    cmd = [PY, "-m", "pip", "install", "-U", "--disable-pip-version-check", "yt-dlp", "gallery-dl", "yt-dlp-ejs"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=400, env=_env())
    except subprocess.TimeoutExpired:
        return False, "pip timed out"
    tail = (r.stdout + r.stderr).decode("utf-8", "replace")[-500:]
    return r.returncode == 0, tail

def new_workdir():
    os.makedirs(TMP_ROOT, exist_ok=True)
    import tempfile
    return tempfile.mkdtemp(prefix="job_", dir=TMP_ROOT)

def cleanup(d):
    shutil.rmtree(d, ignore_errors=True)

def cleanup_all():
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
    os.makedirs(TMP_ROOT, exist_ok=True)

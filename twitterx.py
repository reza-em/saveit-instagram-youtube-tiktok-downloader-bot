"""Twitter / X status links (twitter.com, x.com, mobile.twitter.com, t.co short links, fxtwitter/vxtwitter/fixupx mirrors).
Public posts only, no login, no cookies. Order of attempts:
 1. api.fxtwitter.com (public JSON of the tweet: text, author, every photo / video / GIF with all mp4 bitrates);
 2. api.vxtwitter.com (same idea, no bitrate list);
 3. yt-dlp's twitter extractor (video) and gallery-dl (images) if both public APIs are down.
A single video/GIF goes through yt-dlp when possible (quality ladder, audio check) and falls back to the best mp4 from the API;
photos / several media are a gallery (a tweet with 4 photos -> one album). Text-only tweets -> 'nomedia'; deleted -> 'notfound';
protected accounts -> 'private'."""
import re, logging
from urllib.parse import urlparse
import requests

log = logging.getLogger("twitterx")
API_UA = "SaveItBot/1.0 (+https://t.me/saveit_downloader_bot)"      # fxtwitter's Cloudflare rejects browser-like UAs on some routes
TW_HOSTS = ("twitter.com", "x.com", "mobile.twitter.com", "m.twitter.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com", "fixvx.com", "twittpr.com", "nitter.net")
SKIP_FIRST = {"i", "intent", "share", "home", "explore", "search", "settings", "hashtag", "messages", "notifications", "compose"}

def classify(url, host, path):
    """-> classify dict or None. host lower-case, no 'www.'."""
    if host == "t.co":
        return {"platform": "twitter", "kind": "resolve", "url": url} if path else None
    if host not in TW_HOSTS:
        return None
    for i, seg in enumerate(path):
        if seg == "status" and i + 1 < len(path) and path[i + 1].isdigit():
            user = path[0] if i >= 1 and path[0].lower() not in SKIP_FIRST else "i"
            tid = path[i + 1]
            return {"platform": "twitter", "kind": "post", "url": "https://x.com/%s/status/%s" % (user, tid), "user": user, "id": tid}
    return None

def _mp4_variants(m):
    """fxtwitter media dict -> [(bitrate, url)] of progressive mp4s, best first."""
    out = []
    for f in (m.get("formats") or m.get("variants") or []):
        u = f.get("url") or ""
        if (f.get("container") == "mp4" or f.get("content_type") == "video/mp4") and u:
            out.append((int(f.get("bitrate") or 0), u))
    if not out and m.get("url"):
        out.append((0, m["url"]))
    return sorted(out, reverse=True)

def _from_fx(t):
    media = (t.get("media") or {}).get("all") or []
    if not media and (t.get("quote") or {}).get("media"):         # a quote-tweet without media of its own: the quoted media
        media = (t["quote"]["media"] or {}).get("all") or []
    items = []
    for m in media:
        typ = m.get("type")
        if typ == "photo":
            items.append({"type": "photo", "url": m.get("url"), "w": m.get("width") or 0, "h": m.get("height") or 0})
        elif typ in ("video", "gif"):
            items.append({"type": typ, "url": m.get("url"), "variants": _mp4_variants(m), "w": m.get("width") or 0, "h": m.get("height") or 0,
                          "duration": m.get("duration") or 0, "thumb": m.get("thumbnail_url")})
    a = t.get("author") or {}
    return {"text": (t.get("text") or "").strip(), "user": a.get("screen_name") or "", "name": a.get("name") or "", "items": items,
            "sensitive": bool(t.get("possibly_sensitive"))}

def _from_vx(t):
    items = []
    for m in t.get("media_extended") or []:
        typ = {"image": "photo", "video": "video", "gif": "gif"}.get(m.get("type"))
        if not typ: continue
        sz = m.get("size") or {}
        items.append({"type": typ, "url": m.get("url"), "variants": ([(0, m["url"])] if typ != "photo" else []), "w": sz.get("width") or 0,
                      "h": sz.get("height") or 0, "duration": (m.get("duration_millis") or 0) / 1000.0, "thumb": m.get("thumbnail_url")})
    return {"text": (t.get("text") or "").strip(), "user": t.get("user_screen_name") or "", "name": t.get("user_name") or "", "items": items, "sensitive": False}

def fetch_tweet(user, tid):
    """-> parsed tweet dict, or None if both public APIs are unavailable. Raises EngineError for definitive answers (notfound/private)."""
    from engine import EngineError
    u = user or "i"
    try:
        r = requests.get("https://api.fxtwitter.com/%s/status/%s" % (u, tid), headers={"User-Agent": API_UA}, timeout=20)
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 200 and j.get("tweet"):
            return _from_fx(j["tweet"])
        if r.status_code == 404 or j.get("code") == 404:
            raise EngineError("notfound", "tweet not found (deleted?)")
        if r.status_code in (401, 403) and j.get("code") in (401, 403):
            raise EngineError("private", "protected tweet")
        log.info("fxtwitter HTTP %s", r.status_code)
    except EngineError:
        raise
    except Exception as e:
        log.info("fxtwitter failed: %s", type(e).__name__)
    try:
        r = requests.get("https://api.vxtwitter.com/%s/status/%s" % (u, tid), headers={"User-Agent": API_UA}, timeout=20)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            return _from_vx(r.json())
        log.info("vxtwitter HTTP %s", r.status_code)
    except Exception as e:
        log.info("vxtwitter failed: %s", type(e).__name__)
    return None

def _pick_variant(variants, duration, limit):
    """Highest bitrate whose estimated size fits `limit`; otherwise the lowest one."""
    vs = [v for v in variants if v[1]]
    if not vs: return None
    for br, u in vs:
        if not br or not duration or br * duration / 8 <= limit * 0.95:
            return u
    return vs[-1][1]

def _ext(url, default="jpg"):
    m = re.search(r"\.(jpg|jpeg|png|webp|gif)(?:\?|$)", url or "", re.I)
    ext = (m.group(1).lower() if m else None) or (re.search(r"[?&]format=(\w+)", url or "") or [None, default])[1]
    return "jpg" if ext == "jpeg" else ext

def _title(tw):
    first = (tw["text"].split("\n")[0] if tw["text"] else "").strip()
    return (first[:120]) or ("X @%s" % tw["user"] if tw["user"] else "X post")

def probe(c):
    """c = classify dict (kind 'post') -> info dict (video or gallery). Raises EngineError."""
    import engine
    from engine import EngineError
    url = c["url"]
    tw = fetch_tweet(c.get("user"), c["id"])
    if tw is None:                                                  # both APIs down: yt-dlp (video) then gallery-dl (images)
        return _probe_tools(c)
    if not tw["items"]:
        raise EngineError("nomedia", "text-only tweet")
    who = ("@" + tw["user"]) if tw["user"] else ""
    caption = tw["text"][:engine.CAPTION_RAW_MAX]
    it = tw["items"]
    if len(it) == 1 and it[0]["type"] in ("video", "gif"):
        m = it[0]
        best = _pick_variant(m["variants"], m["duration"], engine.SAFE_LIMIT) or m["url"]
        try:
            info = engine.probe_ytdlp(url if c.get("user") not in (None, "i") else "https://x.com/%s/status/%s" % (tw["user"] or "i", c["id"]), "twitter")
            v = engine.info_to_video(info, "twitter", url)
            if v["kind"] == "video" and v["quals"]:
                v.update(caption=caption or v["caption"], uploader=who or v["uploader"], title=_title(tw), fallback_url=best,
                         thumb=v.get("thumb") or m.get("thumb"))
                if m["type"] == "gif": v["audio_expected"] = False; v["no_audio"] = True
                return v
        except EngineError as e:
            if e.code == "toolong": raise
            log.info("twitter yt-dlp failed (%s) -> API mp4", e.code)
        return {"kind": "video", "platform": "twitter", "url": url, "title": _title(tw), "uploader": who, "duration": int(m["duration"] or 0),
                "quals": {}, "audio_size": None, "has_audio": m["type"] != "gif", "audio_expected": m["type"] != "gif", "no_audio": m["type"] == "gif", "caption": caption,
                "thumb": m.get("thumb"), "via": "direct", "direct_url": best, "id": c["id"]}
    items = []
    for m in it:
        if m["type"] == "photo":
            items.append({"url": m["url"], "ext": _ext(m["url"]), "type": "image", "w": m["w"], "h": m["h"], "direct": True})
        else:
            items.append({"url": _pick_variant(m["variants"], m["duration"], engine.SAFE_LIMIT) or m["url"], "ext": "mp4", "type": "video",
                          "w": m["w"], "h": m["h"], "direct": True})
    return {"kind": "gallery", "platform": "twitter", "url": url, "items": items, "sub": "gallery", "title": _title(tw), "uploader": who, "caption": caption}

def _probe_tools(c):
    import engine
    from engine import EngineError
    url = c["url"]
    try:
        info = engine.probe_ytdlp(url, "twitter")
        v = engine.info_to_video(info, "twitter", url)
        if v["kind"] == "video":
            return v
    except EngineError as e:
        first = e
    else:
        first = None
    try:
        items, meta = engine.gallery_list(url)
        return engine._gallery_info("twitter", url, items, meta)
    except EngineError as e2:
        raise (first if first and first.code not in ("unknown", "nomedia") else e2)

"""LinkedIn (public posts only, no login, no credentials): classification, page parsing (JSON-LD text + author, <video data-sources>,
feed images) and probing. Video goes through yt-dlp's LinkedIn extractor when it works, otherwise the direct mp4 from the page is used.
Login walls / private posts raise EngineError('login'); nothing is ever sent to LinkedIn except anonymous GETs."""
import re, json, html, logging
from urllib.parse import urlparse
import requests

log = logging.getLogger("linkedin")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MAX_IMAGES = 10
_URN = r"urn:li:(?:activity|share|ugcPost|groupPost):\d{10,25}"

def is_host(h):
    return h == "linkedin.com" or h.endswith(".linkedin.com")

def classify(url, host, path):
    """-> classify-dict or None. host is lower-case without www."""
    if host == "lnkd.in":
        return {"platform": "linkedin", "kind": "resolve", "url": url} if path else None
    if not is_host(host):
        return None
    full = "/" + "/".join(path)
    if path and path[0] == "posts" and len(path) >= 2 and re.search(r"activity|share|ugcpost", full, re.I):
        return {"platform": "linkedin", "kind": "post", "url": url}
    if re.search(r"/feed/update/" + _URN, full, re.I) or re.search(_URN, url, re.I) and path[:1] in (["feed"], ["embed"]):
        return {"platform": "linkedin", "kind": "post", "url": url}
    if path and path[0] == "video" and len(path) >= 2:
        return {"platform": "linkedin", "kind": "post", "url": url}
    return None

# ------------------------------------------------------------------ page
def fetch_page(url, timeout=25):
    """GET the public page. -> html text. EngineError: login (auth wall), notfound, ipblock (999/429), timeout."""
    from engine import EngineError
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"}, timeout=timeout, allow_redirects=True)
    except requests.Timeout:
        raise EngineError("timeout", "linkedin page timeout")
    except requests.RequestException as e:
        raise EngineError("unknown", "linkedin fetch: " + type(e).__name__)
    final = r.url or ""
    if r.status_code in (999, 429):
        raise EngineError("ipblock", "linkedin HTTP %s" % r.status_code)
    if r.status_code == 404:
        raise EngineError("notfound", "linkedin 404")
    if r.status_code in (401, 403) or re.search(r"/(authwall|login|uas/login|signup|checkpoint)", urlparse(final).path, re.I):
        raise EngineError("login", "linkedin auth wall")
    if r.status_code != 200:
        raise EngineError("unknown", "linkedin HTTP %s" % r.status_code)
    return r.text

def resolve_short(url):
    """lnkd.in short link -> final URL. Follows redirects; if LinkedIn shows its 'leaving LinkedIn' interstitial the external target
    is read from it. The result may be a LinkedIn post (supported) or some other site (then the caller says 'unsupported')."""
    from engine import EngineError
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        raise EngineError("unknown", "lnkd.in: " + type(e).__name__)
    final = r.url
    if "lnkd.in" in (urlparse(final).hostname or ""):
        m = re.search(r'data-tracking-control-name="external_url_click"[^>]*href="([^"]+)"', r.text) or re.search(r'href="([^"]+)"[^>]*data-tracking-control-name="external_url_click"', r.text)
        if m:
            final = html.unescape(m.group(1))
    return final

def _json_ld(t):
    out = []
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', t, re.S):
        try:
            j = json.loads(m.group(1))
            out += j if isinstance(j, list) else [j]
        except ValueError:
            pass
    return out

def _name(x):
    return (x.get("name") or "") if isinstance(x, dict) else (x or "")

def parse_page(t):
    """-> {"text","author","videos":[{"url","bitrate"}],"poster","images":[url],"duration","width","height"}"""
    ld = next((j for j in _json_ld(t) if isinstance(j, dict) and j.get("@type") in ("SocialMediaPosting", "VideoObject", "Article", "NewsArticle")), {})
    text = ld.get("articleBody") or ld.get("description") or ""
    if not text:
        m = re.search(r'<meta property="og:description" content="([^"]*)"', t)
        text = html.unescape(m.group(1)) if m else ""
    author = _name(ld.get("author")) or _name(ld.get("creator"))
    vids, poster = [], None
    m = re.search(r'<video[^>]*?data-sources="([^"]*)"', t)
    if m:
        try:
            for s in json.loads(html.unescape(m.group(1))):
                if s.get("src") and "mp4" in (s.get("type") or "mp4"):
                    vids.append({"url": html.unescape(s["src"]), "bitrate": int(s.get("data-bitrate") or 0)})
        except ValueError:
            pass
        pm = re.search(r'<video[^>]*?data-poster-url="([^"]*)"', t)
        poster = html.unescape(pm.group(1)) if pm else None
    if not vids and ld.get("contentUrl"):
        vids.append({"url": ld["contentUrl"], "bitrate": 0})
    images, seen = [], set()
    if not vids:
        cands = []
        img = ld.get("image")
        if isinstance(img, dict): img = img.get("url")
        if isinstance(img, list): cands += [(_n if isinstance(_n, str) else _n.get("url")) for _n in img]
        elif img: cands.append(img)
        cands += [html.unescape(u) for u in re.findall(r'https://media\.licdn\.com/dms/image/[^"\s<>]*feedshare[^"\s<>]*', t)]
        for u in cands:
            if not u or "feedshare" not in u: continue
            k = u.split("?")[0]
            if k in seen: continue
            seen.add(k); images.append(u)
    dur = 0
    dm = re.search(r"PT(?:(\d+)M)?(?:(\d+)S)?", str(ld.get("duration") or ""))
    if dm: dur = int(dm.group(1) or 0) * 60 + int(dm.group(2) or 0)
    return {"text": text.strip(), "author": author, "videos": vids, "poster": poster or (ld.get("thumbnailUrl") if isinstance(ld.get("thumbnailUrl"), str) else None),
            "images": images[:MAX_IMAGES], "duration": dur, "width": int(ld.get("width") or 0), "height": int(ld.get("height") or 0),
            "title": (ld.get("headline") or ld.get("name") or "")}

def _title(pg):
    t = re.sub(r"\s*\|\s*[^|]+$", "", pg.get("title") or "") if pg.get("title") and pg.get("author") and pg["title"].endswith(pg["author"]) else pg.get("title") or ""
    t = (t or pg["text"].split("\n")[0] or "LinkedIn").strip()
    return (t[:120] + "…") if len(t) > 120 else t

def probe(c):
    """c = classify dict (kind 'post', final linkedin URL) -> info dict for the bot UI (video or gallery). Raises EngineError."""
    import engine
    from engine import EngineError
    url = c["url"]
    page = fetch_page(url)
    pg = parse_page(page)
    caption = pg["text"][:engine.CAPTION_RAW_MAX]
    if pg["videos"]:
        try:
            info = engine.probe_ytdlp(url, "linkedin")
            v = engine.info_to_video(info, "linkedin", url)
            if v["kind"] == "video":
                v["caption"] = caption or v["caption"]
                v["uploader"] = pg["author"] or v["uploader"]
                v["title"] = _title(pg) or v["title"]
                v["thumb"] = v.get("thumb") or pg["poster"]
                v["duration"] = v.get("duration") or pg["duration"]
                return v
        except EngineError as e:
            if e.code in ("toolong",):
                raise
            log.info("linkedin yt-dlp failed (%s) -> using the page's direct video", e.code)
        best = max(pg["videos"], key=lambda s: s["bitrate"])
        return {"kind": "video", "platform": "linkedin", "url": url, "title": _title(pg), "uploader": pg["author"], "duration": pg["duration"],
                "quals": {}, "audio_size": None, "has_audio": True, "audio_expected": True, "caption": caption, "thumb": pg["poster"],
                "via": "direct", "direct_url": best["url"], "id": None}
    if pg["images"]:
        items = [{"url": u, "ext": "jpg", "type": "image", "w": 0, "h": 0} for u in pg["images"]]
        return {"kind": "gallery", "platform": "linkedin", "url": url, "items": items, "sub": "gallery", "title": _title(pg),
                "uploader": pg["author"], "caption": caption}
    raise EngineError("nomedia", "no video/image in the LinkedIn post")

"""Threads (threads.net / threads.com public posts, no login, no credentials).
yt-dlp and gallery-dl have no Threads extractor, so the media is read from Meta's own public *embed* page
(https://www.threads.com/@user/post/CODE/embed): it lists the post text, the author and every video (<source src>) and
image of the post, carousels included. Limits: the embed endpoint is flaky (it sometimes answers with an error page for a
post that exists, so it is retried a few times) and it does not exist for private accounts / deleted posts / posts Meta
does not allow to embed -> 'notfound'. Text-only posts -> 'nomedia'. Only anonymous GETs are ever made."""
import re, html, time, logging
import requests

log = logging.getLogger("threads")
UA = "Mozilla/5.0"          # the embed page answers real HTML for this short UA; long browser UAs get the heavy app shell
HOSTS = ("threads.net", "threads.com")
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{5,30}$")
TRIES = 4

def is_host(h):
    return h in HOSTS or any(h.endswith("." + x) for x in HOSTS)

def classify(url, host, path):
    """-> classify dict or None. Accepts /@user/post/CODE, /t/CODE and /share/CODE (+ tracking query)."""
    if not is_host(host) or not path:
        return None
    code = None
    if len(path) >= 3 and path[0].startswith("@") and path[1] == "post":
        code = path[2]
    elif len(path) >= 2 and path[0] in ("t", "share"):
        code = path[1]
    if not code or not CODE_RE.match(code):
        return None
    return {"platform": "threads", "kind": "post", "url": "https://www.threads.com/t/%s" % code, "code": code}

def _get_embed(code):
    """-> embed HTML with the post, retrying the flaky endpoint. Raises EngineError."""
    from engine import EngineError
    last = None
    for i in range(TRIES):
        try:
            r = requests.get("https://www.threads.com/t/%s/embed" % code, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"}, timeout=25)
        except requests.Timeout:
            raise EngineError("timeout", "threads embed timeout")
        except requests.RequestException as e:
            raise EngineError("unknown", "threads fetch: " + type(e).__name__)
        if r.status_code in (429, 403):
            raise EngineError("rate", "threads HTTP %s" % r.status_code)
        if r.status_code >= 500:
            last = EngineError("unknown", "threads HTTP %s" % r.status_code); time.sleep(0.6); continue
        if "BodyTextContainer" in r.text or "PostDateContainer" in r.text:
            return r.text
        last = EngineError("notfound", "threads embed error page")
        time.sleep(0.6)
    raise last or EngineError("notfound", "threads embed unavailable")

def parse_embed(s):
    """-> {"author", "text", "items": [{"type": "video"|"image", "url"}]} from the embed HTML (pure, testable)."""
    au = re.search(r'class="HeaderLink"><span>(.*?)</span>', s, re.S)
    tx = re.search(r'BodyTextContainer"><span>(.*?)</span></span>', s, re.S)
    text = html.unescape(re.sub(r"<[^>]+>", "", tx.group(1))).strip() if tx else ""
    items = []
    m = re.search(r'class="(?:SoloMediaContainer|MediaScrollContainer)[^"]*".*?class="PostDateContainer"', s, re.S)
    if m:
        for mm in re.finditer(r'<source src="([^"]+)"|<img[^>]+class="img"[^>]+src="([^"]+)"|<img[^>]+src="([^"]+)"[^>]+class="img"', m.group(0)):
            v, i1, i2 = mm.group(1), mm.group(2), mm.group(3)
            if v: items.append({"type": "video", "url": html.unescape(v)})
            else: items.append({"type": "image", "url": html.unescape(i1 or i2)})
    return {"author": html.unescape(au.group(1)).strip() if au else "", "text": text, "items": items}

def probe(c):
    """c = classify dict -> info dict (video or gallery) for the bot UI. Raises EngineError."""
    import engine
    from engine import EngineError
    pg = parse_embed(_get_embed(c["code"]))
    if not pg["items"]:
        raise EngineError("nomedia", "text-only Threads post")
    url = c["url"]
    who = ("@" + pg["author"]) if pg["author"] else ""
    title = (pg["text"].split("\n")[0][:120] or ("Threads " + who)).strip()
    caption = pg["text"][:engine.CAPTION_RAW_MAX]
    it = pg["items"]
    if len(it) == 1 and it[0]["type"] == "video":
        return {"kind": "video", "platform": "threads", "url": url, "title": title, "uploader": who, "duration": 0, "quals": {},
                "audio_size": None, "has_audio": True, "audio_expected": False, "caption": caption, "thumb": None,
                "via": "direct", "direct_url": it[0]["url"], "id": c["code"]}
    items = [{"url": x["url"], "ext": "mp4" if x["type"] == "video" else "jpg", "type": x["type"], "w": 0, "h": 0, "direct": True} for x in it]
    return {"kind": "gallery", "platform": "threads", "url": url, "items": items, "sub": "gallery", "title": title, "uploader": who, "caption": caption}

"""Channel auto-poster (owner only): target channels, post jobs (artist / titles / search / chart feeds), posting queue with
interval + daily cap, posted-log (no duplicates), auto feeds from public charts and the bot's own most-downloaded tracks.

State lives in store.state["poster"]. All network I/O happens OUTSIDE store transactions. Telegram sends go through core.call,
so tests can mock them. Nothing here posts unless the owner created a job / feed."""
import os, re, time, json, math, logging, threading, hashlib
from concurrent.futures import ThreadPoolExecutor
import requests
import store, logic, core as C
from store import transaction

log = logging.getLogger("poster")
UA = "SaveIt/1.0 ( https://t.me/saveit_downloader_bot )"
MAX_CHANNELS = 20
MAX_JOB_ITEMS = 60
MIN_INTERVAL = 1
POSTED_CAP = 5000
MAX_TRIES = 3
TICK = 20
DEFAULT_FOOTER = {"fa": "📣 {channel}\n🤖 {bot}", "en": "📣 {channel}\n🤖 {bot}"}

def now():
    return int(time.time())

def _n(s):
    import engine
    return engine._norm(re.sub(r"[\(\[].*?[\)\]]", " ", s or ""))

def track_key(artist, title):
    return "%s|%s" % (_n(artist), _n(title))

def _P(st):
    p = st.setdefault("poster", {})
    for k, v in (("channels", []), ("next_ch", 1), ("jobs", []), ("next_job", 1), ("feeds", []), ("next_feed", 1), ("posted", {}), ("footer", {}), ("checked", 0), ("diss_tags", None), ("tracks", {}), ("viral", None)):
        p.setdefault(k, v)
    return p

def snap():
    with transaction(write=False) as st:
        import copy
        return copy.deepcopy(_P(st))

# ------------------------------------------------------------------ channels
def channels():
    return snap()["channels"]

def get_channel(cid):
    return next((c for c in channels() if c["id"] == int(cid)), None)

def add_channel(chat_id, title, handle, label, lang="fa", tags=None):
    with transaction() as st:
        p = _P(st)
        if any(c["chat_id"] == int(chat_id) for c in p["channels"]):
            return None
        if len(p["channels"]) >= MAX_CHANNELS:
            return 0
        cid = p["next_ch"]; p["next_ch"] = cid + 1
        p["channels"].append({"id": cid, "chat_id": int(chat_id), "title": str(title)[:80], "handle": (handle or "")[:80],
                              "label": (label or "")[:40], "lang": lang if lang in ("fa", "en") else "fa", "tags": list(tags or [])[:10], "ok": True})
        return cid

def update_channel(cid, **kw):
    with transaction() as st:
        c = next((c for c in _P(st)["channels"] if c["id"] == int(cid)), None)
        if not c: return False
        for k, v in kw.items():
            if k in ("title", "handle", "label"): c[k] = str(v).strip()[:80]
            elif k == "lang" and v in ("fa", "en"): c["lang"] = v
            elif k == "tags": c["tags"] = [t for t in v][:10]
            elif k == "ok": c["ok"] = bool(v)
        return True

def remove_channel(cid):
    with transaction() as st:
        p = _P(st); n = len(p["channels"])
        p["channels"] = [c for c in p["channels"] if c["id"] != int(cid)]
        for j in p["jobs"]:
            if j["channel_id"] == int(cid) and j["status"] in ("running", "paused", "draft"):
                j["status"] = "cancelled"
        p["feeds"] = [f for f in p["feeds"] if f["channel_id"] != int(cid)]
        return len(p["channels"]) != n

def parse_tags(text):
    out = []
    for t in re.split(r"[\s,،;]+", text or ""):
        t = t.strip().lstrip("#")
        if t:
            tag = "#" + re.sub(r"[^\w\u200c]+", "_", t, flags=re.U).strip("_")
            if len(tag) > 1 and tag.lower() not in [x.lower() for x in out]: out.append(tag)
    return out[:10]

# ------------------------------------------------------------------ deep links under every post (lyrics / download in the bot)
TRACKS_CAP = 3000

def track_id(artist, title):
    return hashlib.sha1(track_key(artist, title).encode("utf-8")).hexdigest()[:10]

def register_track(item):
    """Remember tid -> title/artist (+ the matched source) so `?start=lyr_<tid>` keeps working later. -> tid"""
    tid = track_id(item["artist"], item["title"])
    c = item.get("cand") or {}
    with transaction() as st:
        t = _P(st)["tracks"]
        t[tid] = {"title": item["title"][:150], "artist": item["artist"][:100], "duration": int(c.get("duration") or item.get("duration") or 0),
                  "rj_id": c.get("id") if c.get("source") == "radiojavan" else None, "ts": now()}
        if len(t) > TRACKS_CAP:
            for k in sorted(t, key=lambda k: t[k].get("ts", 0))[:len(t) - TRACKS_CAP]: t.pop(k, None)
    return tid

def get_track(tid):
    return snap()["tracks"].get(str(tid))

def post_markup(tid, bot=None):
    """Two URL buttons under a channel post: lyrics + download, both deep links into the bot."""
    bot = (bot or C.BOT_USERNAME or "saveit_downloader_bot").lstrip("@")
    return C.kb([[C.btn("📝 متن آهنگ / Lyrics", url="https://t.me/%s?start=lyr_%s" % (bot, tid)),
                  C.btn("⬇️ دانلود در ربات", url="https://t.me/%s?start=dl_%s" % (bot, tid))],
                 [C.btn("⭐ حمایت با استارز / Support", url="https://t.me/%s?start=sup" % bot)]])

# ------------------------------------------------------------------ diss / diss-back tracks
# Identification is heuristic, NOT an oracle: (1) keyword match on the TITLE (دیس / diss / diss back / disback / بیف / beef ...), (2) a small hand-made
# list of well-known diss tracks (DISS_KNOWN), (3) the owner can mark any track. Such tracks get the editable diss/beef hashtags in the caption.
DISS_DEFAULT_TAGS = ["#دیس", "#دیس_بک", "#Diss", "#DissTrack", "#بیف", "#Beef"]
DISS_RX = re.compile(r"دیس(?!کو)|بیف|(?<![a-z])diss(?![a-z])|diss\s?(back|bala|track)|dissback|disback|dis\s?back|(?<![a-z])beef(?![a-z])", re.I)
DISS_NOT_RX = re.compile(r"\b(outro|intro|skit|interlude)\b|diss\s?love|disslove|دیس\s?لاو|type\s*beat|بیت\s*رپ|instrumental|ریمیکس|remix|mix\b|تیزر|teaser", re.I)
DISS_KNOWN = [("Eminem", "Killshot"), ("Kendrick Lamar", "Not Like Us"), ("Kendrick Lamar", "Euphoria"), ("Drake", "Family Matters"),
              ("Kendrick Lamar", "Meet the Grahams"), ("J. Cole", "7 Minute Drill"), ("2Pac", "Hit 'Em Up"), ("Nas", "Ether"), ("JAY-Z", "Takeover"),
              ("Pusha T", "The Story of Adidon"), ("Machine Gun Kelly", "Rap Devil")]
# hand-made Iranian list (titles I could confirm in Radio Javan / YouTube Music; the owner should review it - add/remove here)
DISS_KNOWN_IR = [("Zedbazi", "Diss Back"), ("Sepehr Khalse", "Diss Bala"), ("Ho3ein", "Q69"), ("Fadaei", "Bilit")]
DISS_KNOWN = DISS_KNOWN_IR + DISS_KNOWN
# keyword hits from YouTube Music / SoundCloud are only trusted when the artist is a recognised rap act (amateur / meme "diss tracks" are dropped)
DISS_TRUST_ARTISTS = ["Eminem", "Kendrick", "Drake", "J. Cole", "2Pac", "Nas", "JAY-Z", "Jay-Z", "Pusha", "Machine Gun Kelly", "Meek Mill", "50 Cent", "Ja Rule",
                      "LL Cool J", "Remy Ma", "Nicki Minaj", "Lil Wayne", "Joyner Lucas", "Tyler", "Travis Scott", "Snoop", "Ice Cube", "Dr. Dre", "Lil Durk"]

def diss_tags():
    t = snap().get("diss_tags")
    return list(t) if t else list(DISS_DEFAULT_TAGS)

def set_diss_tags(tags):
    with transaction() as st:
        _P(st)["diss_tags"] = list(tags)[:12] or None

def is_diss(artist, title, meta=None):
    if (meta or {}).get("diss"): return True
    t = title or ""
    if DISS_NOT_RX.search(t): return False
    if DISS_RX.search(t): return True
    k = track_key(artist, title)
    return any(track_key(a, b) == k for a, b in DISS_KNOWN)

def link_of(ch):
    h = (ch.get("handle") or "").strip()
    if not h: return ch.get("title") or ""
    if h.startswith("@") or h.startswith("http"): return h
    return "@" + h

def footer_template(lang):
    f = snap()["footer"].get(lang)
    return f if f else DEFAULT_FOOTER[lang]

def set_footer(lang, text):
    with transaction() as st:
        f = _P(st)["footer"]
        if text: f[lang] = text[:300]
        else: f.pop(lang, None)

def render_footer(ch, lang=None):
    lang = lang or ch.get("lang", "fa")
    t = footer_template(lang)
    bot = "@" + (C.BOT_USERNAME or "saveit_downloader_bot")
    try:
        return t.format(channel=link_of(ch), bot=bot, botlink="https://t.me/" + bot[1:], label=ch.get("label") or "")
    except (KeyError, IndexError, ValueError):
        return t

# ------------------------------------------------------------------ posted log
def was_posted(chan_id, key):
    return key in snap()["posted"].get(str(chan_id), [])

def mark_posted(chan_id, key):
    with transaction() as st:
        lst = _P(st)["posted"].setdefault(str(chan_id), [])
        if key not in lst: lst.append(key)
        del lst[:-POSTED_CAP]

def posted_count(chan_id):
    return len(snap()["posted"].get(str(chan_id), []))

# ------------------------------------------------------------------ own download stats
def record_track(title, artist):
    """Count a finished music download (for the 'most downloaded in this bot' chart)."""
    if not title: return
    k = track_key(artist, title)
    with transaction() as st:
        t = st["stats"].setdefault("tracks", {})
        e = t.setdefault(k, {"title": title[:120], "artist": (artist or "")[:80], "n": 0})
        e["n"] += 1
        if len(t) > 2000:
            for kk in sorted(t, key=lambda x: t[x]["n"])[:200]: t.pop(kk, None)

def top_own(n=10):
    t = store.snapshot()["stats"].get("tracks", {})
    return sorted(t.values(), key=lambda e: -e["n"])[:n]

# ------------------------------------------------------------------ jobs
def slim(c):
    return {k: c.get(k) for k in ("source", "id", "title", "song", "artist", "uploader", "duration", "cover", "direct", "direct_alt", "url", "plays", "created") if c.get(k) not in (None, "")}

def make_item(artist, title, cand=None, alts=None, meta=None):
    meta = dict(meta or {})
    if is_diss(artist, title, meta): meta["diss"] = True
    return {"artist": (artist or "").strip()[:100], "title": (title or "").strip()[:150], "status": "pending", "tries": 0, "err": "",
            "sel": True, "cand": slim(cand) if cand else None, "alts": [slim(a) for a in (alts or [])][:2], "meta": meta}

def create_job(channel_id, name, items, interval=30, daily=0, status="draft", feed_id=None, unmatched=None, tags=None):
    with transaction() as st:
        p = _P(st)
        jid = p["next_job"]; p["next_job"] = jid + 1
        p["jobs"].append({"id": jid, "channel_id": int(channel_id), "name": str(name)[:60], "status": status, "interval": max(MIN_INTERVAL, int(interval)),
                          "daily": max(0, int(daily)), "day": "", "day_n": 0, "next_at": now() if status == "running" else 0, "created": now(),
                          "items": items[:MAX_JOB_ITEMS], "unmatched": list(unmatched or [])[:60], "feed_id": feed_id, "tags": list(tags or []),
                          "fails_row": 0, "last_err": ""})
        return jid

def jobs():
    return snap()["jobs"]

def get_job(jid):
    return next((j for j in jobs() if j["id"] == int(jid)), None)

def _job(st, jid):
    return next((j for j in _P(st)["jobs"] if j["id"] == int(jid)), None)

def update_job(jid, **kw):
    with transaction() as st:
        j = _job(st, jid)
        if not j: return False
        for k, v in kw.items():
            if k == "interval": j[k] = max(MIN_INTERVAL, int(v))
            elif k == "daily": j[k] = max(0, int(v))
            else: j[k] = v
        return True

def toggle_item(jid, idx):
    with transaction() as st:
        j = _job(st, jid)
        if not j or not (0 <= idx < len(j["items"])): return None
        j["items"][idx]["sel"] = not j["items"][idx].get("sel", True)
        return j["items"][idx]["sel"]

def start_job(jid):
    """draft -> running: unselected items are dropped, dedupe against the posted-log is applied."""
    with transaction() as st:
        p = _P(st); j = _job(st, jid)
        if not j or j["status"] != "draft": return None
        done = set(p["posted"].get(str(j["channel_id"]), []))
        keep = [it for it in j["items"] if it.get("sel", True) and track_key(it["artist"], it["title"]) not in done]
        j["items"] = keep
        j["status"] = "running" if keep else "done"; j["next_at"] = now()
        return len(keep)

def set_status(jid, status):
    with transaction() as st:
        j = _job(st, jid)
        if not j: return False
        if status == "running" and j["status"] in ("paused",): j["next_at"] = now(); j["fails_row"] = 0
        if status in ("paused", "running", "cancelled") and j["status"] in ("running", "paused", "draft"):
            j["status"] = status
            return True
        return False

def counts(j):
    c = {"pending": 0, "posted": 0, "failed": 0, "skipped": 0}
    for it in j["items"]:
        c[it["status"]] = c.get(it["status"], 0) + 1
    return c

def remove_job(jid):
    with transaction() as st:
        p = _P(st); n = len(p["jobs"])
        p["jobs"] = [j for j in p["jobs"] if j["id"] != int(jid) or j["status"] in ("running",)]
        return len(p["jobs"]) != n

# ------------------------------------------------------------------ feeds
FEED_SOURCES = ("rj_trending", "rj_popular", "rj_rap", "diss", "audius", "itunes", "soundcloud", "shazam", "own")
GENRES = {"pop": {"itunes": 14, "audius": "Pop"}, "rap": {"itunes": 18, "audius": "Hip-Hop/Rap"}, "hiphop": {"itunes": 18, "audius": "Hip-Hop/Rap"},
          "rock": {"itunes": 21, "audius": "Rock"}, "electronic": {"itunes": 7, "audius": "Electronic"}, "dance": {"itunes": 17, "audius": "Electronic"},
          "rnb": {"itunes": 15, "audius": "R&B/Soul"}, "country": {"itunes": 6, "audius": "Country"}, "jazz": {"itunes": 2, "audius": "Jazz"},
          "latin": {"itunes": 12, "audius": "Latin"}, "alternative": {"itunes": 20, "audius": "Alternative"}, "": {"itunes": None, "audius": None}}
SRC_TAGS = {"rj_trending": ["#Trending"], "rj_popular": ["#Popular"], "rj_rap": ["#Persian_Rap"], "diss": ["#Diss"], "audius": ["#Trending"], "itunes": ["#Top"], "soundcloud": ["#Hot"],
            "shazam": ["#Shazam_Top"], "own": ["#Most_Downloaded"]}
CHART_STATUS = {}

def create_feed(channel_id, name, genre, sources, top_n=10, refresh_h=24, interval=60, daily=0, auto=False, tags=None):
    with transaction() as st:
        p = _P(st)
        fid = p["next_feed"]; p["next_feed"] = fid + 1
        p["feeds"].append({"id": fid, "channel_id": int(channel_id), "name": str(name)[:40], "genre": (genre or "").strip().lower()[:30],
                           "sources": [s for s in sources if s in FEED_SOURCES] or ["rj_trending"], "top_n": max(1, min(30, int(top_n))),
                           "refresh_h": max(1, int(refresh_h)), "interval": max(MIN_INTERVAL, int(interval)), "daily": max(0, int(daily)),
                           "auto": bool(auto), "enabled": True, "tags": list(tags or [])[:10], "last": 0})
        return fid

def feeds():
    return snap()["feeds"]

def get_feed(fid):
    return next((f for f in feeds() if f["id"] == int(fid)), None)

def update_feed(fid, **kw):
    with transaction() as st:
        f = next((f for f in _P(st)["feeds"] if f["id"] == int(fid)), None)
        if not f: return False
        for k, v in kw.items():
            if k == "top_n": f[k] = max(1, min(30, int(v)))
            elif k in ("refresh_h",): f[k] = max(1, int(v))
            elif k == "interval": f[k] = max(MIN_INTERVAL, int(v))
            elif k == "daily": f[k] = max(0, int(v))
            elif k == "sources": f[k] = [s for s in v if s in FEED_SOURCES] or f["sources"]
            elif k == "genre": f[k] = str(v).strip().lower()[:30]
            elif k in ("auto", "enabled"): f[k] = bool(v)
            elif k == "tags": f[k] = list(v)[:10]
            elif k == "name": f[k] = str(v)[:40]
            elif k == "last": f[k] = int(v)
        return True

def remove_feed(fid):
    with transaction() as st:
        p = _P(st); n = len(p["feeds"])
        p["feeds"] = [f for f in p["feeds"] if f["id"] != int(fid)]
        return len(p["feeds"]) != n

# ---- chart fetchers: each -> list of {"artist","title","cand"?,"meta"?}; failures -> [] + CHART_STATUS
def _plays(v):
    try: return int(str(v or "0").replace(",", "").split(".")[0] or 0)
    except ValueError: return 0

def _rj_cand(x):
    return {"source": "radiojavan", "id": str(x.get("id")), "title": x.get("title") or "", "song": x.get("song") or "", "artist": x.get("artist") or "",
            "duration": int(float(x.get("duration") or 0)), "cover": x.get("photo") or x.get("thumbnail"), "direct": x.get("link"),
            "plays": _plays(x.get("plays")), "created": (x.get("created_at") or x.get("date") or "")[:10],
            "direct_alt": [u for u in (x.get("hq_link"),) if u]}

def chart_rj(kind, n):
    r = requests.get("https://play.radiojavan.com/api/p/mp3s", params={"type": kind}, headers={"User-Agent": UA}, timeout=15)
    out = []
    for x in r.json()[:n * 2]:
        if x.get("link") and x.get("song"):
            out.append({"artist": x.get("artist") or "", "title": x["song"], "cand": _rj_cand(x), "meta": {}})
    return out

# Radio Javan has no genre filter: "rj_rap" = the most-played tracks of a curated list of Persian rap artists (Radio Javan artist pages, direct
# MP3 links), one track per artist per round, most-played first. Edit the list to taste.
RAP_ARTISTS = ["Hichkas", "Sasy", "Yas", "Behzad Leito", "Sijal", "Shayea", "Ho3ein", "Zedbazi", "Erfan", "Koorosh", "Putak", "Amir Tataloo",
               "Sepehr Khalse", "Pishro", "Quf", "Ahmad Solo", "Sami Low", "Hoorosh Band", "Tester Pishro", "Saman Wilson"]

def chart_rj_rap(n, per_artist=6):
    def one(a):
        try:
            r = requests.get("https://play.radiojavan.com/api/p/artist", params={"query": a}, headers={"User-Agent": UA}, timeout=20)
            ka = _n(a); rows = []
            for x in sorted(r.json().get("mp3s") or [], key=lambda x: -int(str(x.get("plays") or "0").replace(",", "") or 0)):
                if x.get("link") and x.get("song") and (ka in _n(x.get("artist")) or _n(x.get("artist")) in ka):
                    rows.append({"artist": x.get("artist") or a, "title": x["song"], "cand": _rj_cand(x), "meta": {"genre": "Hip-Hop/Rap"}})
                if len(rows) >= per_artist: break
            return rows
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=5) as ex:
        per = list(ex.map(one, RAP_ARTISTS))
    out = []
    for i in range(per_artist):
        for rows in per:
            if i < len(rows): out.append(rows[i])
    return out[:n * 2]

NOT_A_SONG = re.compile(r"\b(type\s*beat|beats?|instrumental|remix|mix|mixtape|freestyle beat|bootleg|prod\.?\s*by|sample pack)\b", re.I)

DISS_QUERIES = {"soundcloud": ["دیس رپ فارسی", "دیس بک رپ", "Persian rap diss", "diss track rap 2026"],
                "ytmusic": ["دیس رپ فارسی", "دیس بک رپ", "Persian rap diss track", "Iranian rap diss back", "rap diss track 2026", "diss back rap"]}

def chart_diss(n):
    """Diss / diss-back tracks: title-keyword search on SoundCloud + YouTube Music + Radio Javan (+ DISS_KNOWN). Heuristic, see DISS_RX."""
    import music
    cs = music.search_providers(DISS_QUERIES, ["soundcloud", "ytmusic"])
    try:
        for q in ("دیس", "diss", "disback"):
            r = requests.get("https://play.radiojavan.com/api/p/search", params={"query": q}, headers={"User-Agent": UA}, timeout=15).json()
            for x in r.get("mp3s") or []:
                if x.get("link") and x.get("song"): cs.append(dict(_rj_cand(x), created=x.get("created_at") or x.get("date") or ""))
    except Exception:
        pass
    out, seen = [], set()
    for c in cs:
        t = c.get("song") or c.get("title") or ""
        d = int(c.get("duration") or 0)
        if not is_diss(c.get("artist"), t) or (d and not 60 <= d <= 600) or NOT_A_SONG.search(t): continue
        a = c.get("artist") or c.get("uploader") or ""
        if c.get("source") != "radiojavan" and not any(_n(x) in _n(a) for x in RAP_ARTISTS + DISS_TRUST_ARTISTS): continue
        k = track_key(a, t)
        if k in seen: continue
        seen.add(k)
        out.append({"artist": a, "title": t, "cand": c, "meta": {"genre": "Hip-Hop/Rap", "diss": True}, "created": c.get("created", "")})
    out.sort(key=lambda e: e.get("created") or "", reverse=True)
    out += [{"artist": a, "title": b, "cand": None, "meta": {"genre": "Hip-Hop/Rap", "diss": True}} for a, b in DISS_KNOWN
            if track_key(a, b) not in seen]
    return out[:n * 2]

def chart_audius(genre, n):
    g = GENRES.get(genre, {}).get("audius")
    p = {"time": "week", "limit": n * 2, "app_name": "SaveIt"}
    if g: p["genre"] = g
    r = requests.get("https://api.audius.co/v1/tracks/trending", params=p, headers={"User-Agent": UA}, timeout=15)
    out = []
    for t in r.json().get("data") or []:
        if t.get("is_streamable") is False or not t.get("id"): continue
        if NOT_A_SONG.search(t.get("title") or "") or not (60 <= int(t.get("duration") or 0) <= 600): continue   # beats / mixes / remixes are not songs
        art = t.get("artwork") or {}
        c = {"source": "audius", "id": str(t["id"]), "title": t.get("title") or "", "artist": (t.get("user") or {}).get("name") or "", "duration": int(t.get("duration") or 0),
             "cover": art.get("480x480") or art.get("150x150"), "direct": "https://api.audius.co/v1/tracks/%s/stream?app_name=SaveIt" % t["id"]}
        c["plays"] = int(t.get("play_count") or 0); c["created"] = (t.get("release_date") or t.get("created_at") or "")[:10]
        out.append({"artist": c["artist"], "title": c["title"], "cand": c, "meta": {"genre": t.get("genre") or ""}})
    return out

def chart_itunes(genre, n, cc="us"):
    gid = GENRES.get(genre, {}).get("itunes")
    url = "https://itunes.apple.com/%s/rss/topsongs/limit=%d%s/json" % (cc, n * 2, ("/genre=%d" % gid) if gid else "")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    out = []
    for e in r.json().get("feed", {}).get("entry") or []:
        title, artist = e["im:name"]["label"], e["im:artist"]["label"]
        out.append({"artist": artist, "title": re.sub(r"\s*\(feat\..*?\)", "", title), "cand": None,
                    "meta": {"album": (e.get("im:collection") or {}).get("im:name", {}).get("label", ""), "genre": (e.get("category") or {}).get("attributes", {}).get("label", ""),
                             "year": (((e.get("im:releaseDate") or {}).get("attributes") or {}).get("label") or "")[:4],
                             "created": (((e.get("im:releaseDate") or {}).get("attributes") or {}).get("label") or "")[:10]}})
    return out

def chart_soundcloud(genre, n):
    import music
    cs = music.search_providers(["%s hits %s" % (genre or "top", time.strftime("%Y"))], ["soundcloud"])
    return [{"artist": c.get("artist") or c.get("uploader") or "", "title": c["title"], "cand": c, "meta": {}} for c in cs
            if 90 <= int(c.get("duration") or 0) <= 600][:n * 2]

def chart_shazam(genre, n):
    import asyncio
    from shazamio import Shazam
    from shazamio.enums import GenreMusic
    g = next((x for x in GenreMusic if x.name.lower().replace("_", "") in (genre or "").replace("-", "").replace("hiphop", "hiphoprap")), None)
    async def go():
        s = Shazam()
        r = await (s.top_world_genre_tracks(genre=g, limit=n * 2) if g else s.top_world_tracks(limit=n * 2))
        return r
    loop = asyncio.new_event_loop()
    try: r = loop.run_until_complete(asyncio.wait_for(go(), 25))
    finally: loop.close()
    return [{"artist": t.get("subtitle") or "", "title": t.get("title") or "", "cand": None, "meta": {"genre": (t.get("genres") or {}).get("primary", "")}} for t in (r.get("tracks") or [])]

def chart_own(n):
    return [{"artist": e["artist"], "title": e["title"], "cand": None, "meta": {}} for e in top_own(n * 2)]

def fetch_source(src, genre, n):
    try:
        res = {"rj_trending": lambda: chart_rj("trending", n), "rj_popular": lambda: chart_rj("popular", n), "rj_rap": lambda: chart_rj_rap(n), "diss": lambda: chart_diss(n), "audius": lambda: chart_audius(genre, n),
               "itunes": lambda: chart_itunes(genre, n), "soundcloud": lambda: chart_soundcloud(genre, n), "shazam": lambda: chart_shazam(genre, n),
               "own": lambda: chart_own(n)}[src]()
        CHART_STATUS[src] = "ok:%d" % len(res)
        if src not in ("rj_rap",):                      # rj_rap is a per-artist catalogue pull, its order says nothing about popularity
            for i, e in enumerate(res):
                e["meta"] = dict(e.get("meta") or {}, rank={src: [i, len(res)]})
        return res
    except Exception as e:
        CHART_STATUS[src] = "error:" + type(e).__name__
        log.info("chart %s failed: %s", src, type(e).__name__)
        return []

# ------------------------------------------------------------------ VIRAL focus
# "Viral" cannot be measured directly from outside Instagram/TikTok, so it is a computed SCORE from what is reachable without keys:
#   chart position (Radio Javan trending/popular, Audius trending, iTunes rap chart, Shazam when it answers) + appearing on several charts +
#   play counts (Radio Javan / Audius) + recency (release date) + the admin's manual viral list (+ diss/beef).  Ordinary catalogue tracks
#   (no chart, not on the list) get half weight and are not tagged viral.  Limits: no real Reels/TikTok data, Radio Javan has no genre filter
#   (rap artists are recognised from RAP_ARTISTS / IR_RAP_EXTRA), scores are a heuristic ranking, not proof.
VIRAL_DEFAULT_TAGS = ["#اینستا_وایرال", "#InstaViral", "#ReelsViral", "#وایرال", "#Viral", "#Trending"]
W_CHART = {"rj_trending": 38, "rj_popular": 30, "audius": 26, "itunes": 26, "shazam": 30, "soundcloud": 14, "own": 12, "diss": 10}
VIRAL_MIN = 35                 # score at/above which a track counts as viral (gets the viral tags)
MIN_QUEUE = 10                 # never thin a queue below this many pending tracks
IRAN_SHARE = 0.65              # at least this share of a selection is Iranian
IR_RAP_EXTRA = ["Sami Beigi", "Sohrab MJ", "Alireza JJ", "Canis", "Mehrad Hidden", "Arta", "Fadaei", "Young Sudden", "Parsalip", "Gdaal", "Poori", "Isam", "CIA",
                "Mr.Mp", "Catchybeatz", "Onedam", "Poobon", "Heliyom", "Tahas", "Arshiyas", "Shahin Najafi", "Yasin Torki", "Sepehr Khalse", "Hoorosh Band"]
MAX_VIRAL_LIST = 200

def viral_cfg():
    with transaction() as st:
        v = _P(st).get("viral")
        if not v:
            v = _P(st)["viral"] = {"on": True, "tags": None, "list": []}
        import copy; return copy.deepcopy(v)

def _vset(**kw):
    with transaction() as st:
        v = _P(st).get("viral") or {"on": True, "tags": None, "list": []}
        v.update(kw); _P(st)["viral"] = v

def viral_on(): return bool(viral_cfg()["on"])
def set_viral_on(b): _vset(on=bool(b))
def viral_tags():
    t = viral_cfg().get("tags"); return list(t) if t else list(VIRAL_DEFAULT_TAGS)
def set_viral_tags(tags): _vset(tags=list(tags)[:12] or None)
def viral_list(): return viral_cfg()["list"]
def add_viral_list(text):
    """Lines 'artist - title' -> appended (deduped). -> number added."""
    cur = viral_list(); keys = {track_key(x["artist"], x["title"]) for x in cur}; n = 0
    for a, t in parse_titles(text):
        k = track_key(a, t)
        if t and k not in keys and len(cur) < MAX_VIRAL_LIST: keys.add(k); cur.append({"artist": a, "title": t}); n += 1
    _vset(list=cur); return n
def clear_viral_list(): _vset(list=[])
def remove_viral_item(i):
    cur = viral_list()
    if 0 <= i < len(cur): cur.pop(i)
    _vset(list=cur)

def _allow(names):
    return [" %s " % _n(a) for a in names]
def is_rap_artist(artist):
    an = " %s " % _n(artist)
    return any(a in an for a in _allow(RAP_ARTISTS + IR_RAP_EXTRA + DISS_TRUST_ARTISTS))
def is_iranian(e):
    c = e.get("cand") or {}
    txt = (e.get("artist") or "") + (e.get("title") or "")
    return c.get("source") == "radiojavan" or bool(re.search(r"[\u0600-\u06FF]", txt)) or any(" %s " % _n(a) in " %s " % _n(e.get("artist")) for a in RAP_ARTISTS + IR_RAP_EXTRA)

def _age_days(created):
    try:
        if isinstance(created, (int, float)): return max(0, (now() - int(created)) / 86400)
        t = time.mktime(time.strptime(str(created)[:10], "%Y-%m-%d")); return max(0, (now() - t) / 86400)
    except (ValueError, TypeError):
        return None

def viral_score(e):
    """-> (score, reasons). e = {artist,title,cand?,meta?}; meta.rank = {source: [pos, n]}."""
    m = e.get("meta") or {}; c = e.get("cand") or {}
    s, why = 0.0, []
    rank = m.get("rank") or {}
    for src, (pos, n) in rank.items():
        w = W_CHART.get(src, 10) * (1 - pos / max(n, 1)); s += w
        if w: why.append("%s#%d" % (src, pos + 1))
    if len(rank) > 1: s += 12 * (len(rank) - 1); why.append("multi-chart")
    manual = bool(m.get("manual"))
    if manual: s += 70; why.append("admin list")
    diss = is_diss(e.get("artist"), e.get("title"), m)
    if diss: s += 10; why.append("diss/beef")
    charted = bool(rank) or manual
    plays = int(m.get("plays") or c.get("plays") or 0)
    if plays > 0: s += min(20, max(0, (math.log10(plays) - 4.0) * 8))
    age = _age_days(m.get("created") or c.get("created"))
    if age is not None:
        r = 28 if age <= 7 else 22 if age <= 21 else 14 if age <= 45 else 7 if age <= 90 else 2 if age <= 365 else 0
        s += r
        if r >= 14: why.append("new (%dd)" % age)
    if is_iranian(e): s += 6
    if not charted and not diss: s *= 0.5; why.append("catalogue x0.5")
    return round(s, 1), why

def _merge(entries):
    out = {}
    for e in entries:
        k = track_key(e["artist"], e["title"])
        if not e["title"]: continue
        if k not in out:
            out[k] = {"artist": e["artist"], "title": e["title"], "cand": e.get("cand"), "meta": dict(e.get("meta") or {}), "created": e.get("created", "")}
        else:
            o = out[k]; m = dict(e.get("meta") or {})
            o["meta"]["rank"] = dict(o["meta"].get("rank") or {}, **(m.get("rank") or {}))
            for f in ("plays", "created", "album", "year", "diss", "manual"):
                if m.get(f) and not o["meta"].get(f): o["meta"][f] = m[f]
            if e.get("cand") and (not o["cand"] or (e["cand"].get("source") == "radiojavan" and o["cand"].get("source") != "radiojavan")): o["cand"] = e["cand"]
    return out

def _is_rap_entry(e, genre):
    """Rap-only guard: known genre must match; sources that do not report a genre must come from a recognised rap artist."""
    g = (e.get("meta") or {}).get("genre")
    if not genre_ok(genre, g): return False
    if GENRE_WORDS.get((genre or "").lower()) and not g:
        return is_rap_artist(e["artist"]) or bool((e.get("meta") or {}).get("manual")) or is_diss(e["artist"], e["title"], e.get("meta"))
    return True

def rank_entries(entries, genre, limit, done=(), per_artist=None, iran_share=IRAN_SHARE, extra_seen=()):
    """merge -> rap-only -> score -> drop duplicates/posted -> per-artist cap -> Iranian share -> sorted by score. -> [(score, why, entry)]"""
    per_artist = per_artist or MAX_PER_ARTIST
    manual = {track_key(x["artist"], x["title"]): x for x in viral_list()}
    merged = _merge(entries)
    for k, x in manual.items():                                   # the admin's list always takes part
        if k not in merged: merged[k] = {"artist": x["artist"], "title": x["title"], "cand": None, "meta": {}}
        merged[k]["meta"]["manual"] = True
    scored = []
    for k, e in merged.items():
        if k in done or k in extra_seen or not _is_rap_entry(e, genre): continue
        sc, why = viral_score(e); e["meta"]["viral"] = sc; e["meta"]["vwhy"] = why[:5]
        scored.append((sc, why, e))
    scored.sort(key=lambda r: -r[0])
    ir_need = int(math.ceil(limit * iran_share)); picked, per, ir, fo = [], {}, 0, 0
    for sc, why, e in scored:
        if len(picked) >= limit: break
        ka = _n(e["artist"])
        if ka and per.get(ka, 0) >= per_artist: continue
        iran = is_iranian(e)
        if not iran and fo >= limit - ir_need: continue            # keep room for Iranian tracks
        per[ka] = per.get(ka, 0) + 1; picked.append((sc, why, e)); ir += iran; fo += (not iran)
    return picked

def viral_item(e, sc):
    it = make_item(e["artist"], e["title"], e.get("cand"), None, dict(e.get("meta") or {}))
    it["meta"]["viral"] = sc
    return it

def is_viral_item(it):
    m = it.get("meta") or {}
    return viral_on() and (float(m.get("viral") or 0) >= VIRAL_MIN or bool(m.get("manual")))

def rerank_job(jid, sources=None, genre="rap"):
    """Re-order the PENDING items of a job by viral score (highest first) and drop non-viral filler (never below MIN_QUEUE).
    -> {"kept": n, "dropped": n, "iran": share, "top": [(score, artist, title)]}"""
    j = get_job(jid)
    if not j: return None
    src = sources or ["rj_trending", "rj_popular", "rj_rap", "diss", "audius", "itunes"]
    pool = []
    for s in src: pool += fetch_source(s, genre, 30)
    cmap = _merge(pool)
    manual = {track_key(x["artist"], x["title"]) for x in viral_list()}
    scored = []
    for it in j["items"]:
        if it["status"] != "pending": continue
        k = track_key(it["artist"], it["title"])
        e = {"artist": it["artist"], "title": it["title"], "cand": it.get("cand"), "meta": dict(it.get("meta") or {})}
        if k in cmap:
            m = cmap[k]["meta"]
            e["meta"]["rank"] = m.get("rank") or {}
            for f in ("plays", "created"):
                if m.get(f): e["meta"][f] = m[f]
        if k in manual: e["meta"]["manual"] = True
        sc, why = viral_score(e); e["meta"]["viral"] = sc; e["meta"]["vwhy"] = why[:5]
        scored.append((sc, k, it, e))
    scored.sort(key=lambda r: -r[0])
    keep = [r for r in scored if r[0] >= VIRAL_MIN or r[3]["meta"].get("manual") or r[3]["meta"].get("diss")]
    if len(keep) < MIN_QUEUE:
        keep += [r for r in scored if r not in keep][:MIN_QUEUE - len(keep)]
    keep.sort(key=lambda r: -r[0]); ks = {id(r[2]) for r in keep}
    with transaction() as st:
        jj = _job(st, jid)
        done = [it for it in jj["items"] if it["status"] != "pending"]
        byk = {track_key(it["artist"], it["title"]): it for it in jj["items"] if it["status"] == "pending"}
        order = []
        for sc, k, it, e in keep:
            cur = byk.get(k)
            if not cur: continue
            cur["meta"] = dict(cur.get("meta") or {}, viral=sc, vwhy=e["meta"].get("vwhy", []), **({"manual": True} if e["meta"].get("manual") else {}))
            if e["meta"].get("plays"): cur["meta"]["plays"] = e["meta"]["plays"]
            order.append(cur)
        dropped = []
        for sc, k, it, e in scored:
            cur = byk.get(k)
            if cur is not None and cur not in order:
                cur["status"] = "skipped"; cur["err"] = "not viral (%.0f)" % sc; dropped.append(cur)
        jj["items"] = done + dropped + order
    ir = sum(1 for r in keep if is_iranian(r[3])) / max(1, len(keep))
    return {"kept": len(keep), "dropped": len(dropped), "iran": round(ir, 2), "top": [(r[0], r[2]["artist"], r[2]["title"]) for r in keep[:8]]}

MAX_PER_ARTIST = 2
GENRE_WORDS = {"rap": r"rap|hip", "hiphop": r"rap|hip", "rock": r"rock|metal|punk", "pop": r"pop", "electronic": r"electro|dance|house|techno|edm",
               "rnb": r"r&b|rnb|soul", "jazz": r"jazz", "country": r"country", "latin": r"latin", "alternative": r"alt|indie"}

def genre_ok(want, got):
    """A feed with a genre only accepts tracks whose (known) genre matches; unknown genre passes (the source already filtered)."""
    w = GENRE_WORDS.get((want or "").lower())
    return not (w and got) or bool(re.search(w, str(got), re.I))

def feed_items(feed, channel_id):
    """Round-robin over the feed's sources, skip tracks already in the channel's posted-log and duplicates, keep top N."""
    n_fetch = 30 if viral_on() else feed["top_n"]
    lists = [fetch_source(s, feed["genre"], n_fetch) for s in feed["sources"]]
    done = set(snap()["posted"].get(str(channel_id), []))
    if viral_on():                                          # rank everything by viral score, most viral first
        picked = rank_entries([e for l in lists for e in l], feed["genre"], feed["top_n"], done=done)
        return [viral_item(e, sc) for sc, why, e in picked]
    out, seen, i, per = [], set(), 0, {}
    while len(out) < feed["top_n"] and any(i < len(l) for l in lists):
        for l in lists:
            if i < len(l):
                e = l[i]; k = track_key(e["artist"], e["title"])
                if k in done or k in seen or not e["title"]: continue
                if not genre_ok(feed["genre"], (e.get("meta") or {}).get("genre")): continue     # strict genre: a rap feed never posts pop / rock / ...
                ka = _n(e["artist"])
                if ka and per.get(ka, 0) >= MAX_PER_ARTIST: continue                            # one uploader must not flood a feed
                per[ka] = per.get(ka, 0) + 1
                seen.add(k)
                out.append(make_item(e["artist"], e["title"], e.get("cand"), None, e.get("meta")))
                if len(out) >= feed["top_n"]: break
        i += 1
    return out

def feed_tags(feed, ch):
    src = [t for s in feed["sources"] for t in SRC_TAGS.get(s, [])]
    return list(dict.fromkeys((["#" + feed["genre"]] if feed["genre"] else []) + src + feed.get("tags", []) + ch.get("tags", [])))

def refresh_feed(fid, notify=None):
    """Fetch the chart and turn it into a job: running (auto mode) or a draft awaiting the owner's approval. -> job id or None."""
    f = get_feed(fid)
    if not f: return None
    ch = get_channel(f["channel_id"])
    update_feed(fid, last=now())
    if not ch: return None
    items = feed_items(f, ch["id"])
    if not items:
        return None
    with transaction() as st:                               # a new refresh replaces the unposted rest of the feed's previous job (keeps the daily cap honest)
        for oj in _P(st)["jobs"]:
            if oj.get("feed_id") == f["id"] and oj["status"] in ("running", "paused"):
                for it in oj["items"]:
                    if it["status"] == "pending": it["status"] = "skipped"; it["err"] = "superseded"
                oj["status"] = "done"
    jid = create_job(ch["id"], f["name"], items, f["interval"], f["daily"], "running" if f["auto"] else "draft", feed_id=f["id"], tags=feed_tags(f, ch))
    if notify: notify("feed_ready" if f["auto"] else "feed_draft", job=jid, feed=f, channel=ch, n=len(items))
    return jid

# ------------------------------------------------------------------ discovery for manual jobs
def itunes_artist_titles(artist, n=50):
    import recognize
    try:
        r = requests.get("https://itunes.apple.com/search", params={"term": artist, "entity": "song", "attribute": "artistTerm", "limit": min(200, n * 3)},
                         headers={"User-Agent": "SaveIt/1.0"}, timeout=15)
        ka = _n(artist)
        return [{"artist": x.get("artistName") or artist, "title": x["trackName"], "meta": {"album": x.get("collectionName") or "", "genre": x.get("primaryGenreName") or "",
                 "year": (x.get("releaseDate") or "")[:4]}, "duration": int((x.get("trackTimeMillis") or 0) / 1000)}
                for x in r.json().get("results", []) if x.get("trackName") and ka in _n(x.get("artistName"))]
    except Exception as e:
        log.info("itunes artist lookup failed: %s", type(e).__name__)
        return []

def artist_entries(artist, limit):
    """'all songs of <artist>': Radio Javan artist page (direct MP3 links) first, then iTunes discography titles (matched later)."""
    out, seen = [], set()
    try:
        r = requests.get("https://play.radiojavan.com/api/p/artist", params={"query": artist}, headers={"User-Agent": UA}, timeout=20)
        j = r.json(); ka = _n(artist)
        rows = sorted(j.get("mp3s") or [], key=lambda x: -int(str(x.get("plays") or "0").replace(",", "") or 0))
        for x in rows:
            if not x.get("link") or not x.get("song"): continue
            a = x.get("artist") or artist
            if ka not in _n(a) and _n(a) not in ka: continue
            k = track_key(a, x["song"])
            if k in seen: continue
            seen.add(k); out.append({"artist": a, "title": x["song"], "cand": _rj_cand(x), "meta": {}})
    except Exception as e:
        log.info("radiojavan artist lookup failed: %s", type(e).__name__)
    for e in itunes_artist_titles(artist, limit):
        k = track_key(e["artist"], e["title"])
        if k not in seen:
            seen.add(k); out.append({"artist": e["artist"], "title": e["title"], "cand": None, "meta": e["meta"], "duration": e.get("duration", 0)})
    return out[:limit]

def parse_titles(text):
    """'artist - title' per line (also – — and '|'); a line without a separator is a search query."""
    out = []
    for ln in (text or "").splitlines():
        ln = ln.strip().lstrip("-•*0123456789.) ").strip() if re.match(r"^\s*\d+[\.\)]\s", ln) else ln.strip()
        if not ln: continue
        m = re.split(r"\s+[-–—|]\s+", ln, maxsplit=1)
        out.append((m[0].strip(), m[1].strip()) if len(m) == 2 else ("", ln))
    return out[:MAX_JOB_ITEMS]

def match_entries(entries, progress=None, workers=4):
    """entries: [{artist,title,cand?,meta?}] -> (items, unmatched_labels). Entries without a cand are matched through the music chain."""
    import music
    from engine import EngineError
    def one(e):
        if e.get("cand"):
            return make_item(e["artist"], e["title"], e["cand"], None, e.get("meta")), None
        meta = {"title": e["title"], "artist": e["artist"], "artists": [e["artist"]] if e["artist"] else [], "duration": int(e.get("duration") or 0)}
        try:
            ranked = music.find_best(meta)
        except EngineError:
            return None, "%s - %s" % (e["artist"], e["title"]) if e["artist"] else e["title"]
        except Exception:
            return None, "%s - %s" % (e["artist"], e["title"]) if e["artist"] else e["title"]
        top = ranked[0]
        return make_item(e["artist"] or top.get("artist") or "", e["title"], top, ranked[1:3], e.get("meta")), None
    items, bad = [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (it, b) in enumerate(ex.map(one, entries)):
            (items.append(it) if it else bad.append(b))
            if progress: progress(i + 1, len(entries))
    return items, bad

# ------------------------------------------------------------------ posting
FATAL_RE = re.compile(r"chat not found|bot was kicked|not enough rights|have no rights|write_forbidden|need administrator|bot is not a member|forbidden", re.I)

def build_caption(ch, tags, extra_tags=()):
    import meta as MT
    body = "\n".join(MT.lines(tags))
    ht = " ".join(MT.hashtags(tags, list(extra_tags) + list(ch.get("tags") or []), limit=18))
    foot = render_footer(ch)
    from core import esc
    cap = esc(body)
    def join(h):
        return cap + (("\n\n" + esc(h)) if h else "") + (("\n\n" + esc(foot)) if foot else "")
    text = join(ht)
    while len(text) > 1024 and ht:
        ht = " ".join(ht.split(" ")[:-1]); text = join(ht)
    return text[:1024]

def build_info(item):
    import music
    meta = {"title": item["title"], "artist": item["artist"], "artists": [item["artist"]] if item["artist"] else [], "duration": int(item.get("duration") or 0)}
    if item.get("cand"):
        info = music.info_for_cand(dict(item["cand"]))
        if item.get("alts"): info["dl_cands"] = [dict(item["cand"])] + [dict(a) for a in item["alts"]]
        info["meta"].update({k: v for k, v in meta.items() if v and k != "duration"})
    else:
        info = music.audio_info(meta)
    info["title"] = item["title"][:200] or info["title"]
    info["uploader"] = item["artist"] or info.get("uploader") or ""
    info["tags"] = dict(info.get("tags") or {})
    for k in ("album", "year", "genre"):
        if (item.get("meta") or {}).get(k): info["tags"][k] = item["meta"][k]
    return info

def _retry_after(msg):
    m = re.search(r"retry after (\d+)", msg, re.I)
    return int(m.group(1)) if m else None

def post_item(ch, job, item):
    """Download + upload one track to the channel. -> ('ok', msg_id) | ('retry', seconds) | ('fail', code) | ('fatal', code)"""
    import engine, jobs as J
    from engine import EngineError
    wd = engine.new_workdir()
    try:
        info = build_info(item)
        info["_uid"], info["_chat"] = 0, ch["chat_id"]
        path = engine.download_audio(info, "mp3", wd, None)
        if os.path.getsize(path) > J.LIMIT:
            k = engine.fit_audio_bitrate(info.get("duration"))
            if not k: return "fail", "toobig"
            path = engine.reencode_audio(path, k, wd)
            if os.path.getsize(path) > J.LIMIT: return "fail", "toobig"
        path = J.prepare_music_file(info, path, wd, show_source=False)
        tags = info.get("tags") or {}
        extra = (viral_tags() if is_viral_item(item) else []) + (diss_tags() if (item.get("meta") or {}).get("diss") else []) + list(job.get("tags") or [])
        data = {"chat_id": ch["chat_id"], "caption": build_caption(ch, tags, extra), "parse_mode": "HTML",
                "title": (tags.get("title") or info["title"])[:64], "performer": (tags.get("artist") or "")[:64]}
        if info.get("duration"): data["duration"] = info["duration"]
        tid = register_track(item)
        data["reply_markup"] = post_markup(tid)
        thumb = J._thumb_from_url(info.get("thumb"), wd)
        try:
            with open(path, "rb") as fa:
                if thumb:
                    with open(thumb, "rb") as ft:
                        data["thumbnail"] = "attach://thumbnail"
                        try:
                            r = C.call("sendAudio", data, files={"audio": (os.path.basename(path), fa), "thumbnail": ("thumb.jpg", ft)}, timeout=900)
                        except C.ApiError as e:
                            if _retry_after(str(e)) or FATAL_RE.search(str(e)): raise
                            data.pop("thumbnail", None); fa.seek(0)
                            r = C.call("sendAudio", data, files={"audio": (os.path.basename(path), fa)}, timeout=900)
                else:
                    r = C.call("sendAudio", data, files={"audio": (os.path.basename(path), fa)}, timeout=900)
        except C.ApiError as e:
            ra = _retry_after(str(e))
            if ra: return "retry", min(ra + 1, 3600)
            if FATAL_RE.search(str(e)): return "fatal", C.safe(e)[:80]
            return "fail", "upload: " + C.safe(e)[:60]
        return "ok", (r or {}).get("message_id")
    except EngineError as e:
        return "fail", e.code
    except Exception as e:
        log.warning("post_item crashed: %s", type(e).__name__)
        return "fail", type(e).__name__
    finally:
        engine.cleanup(wd)

def _midnight_next():
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday + 1, 0, 1, 0, 0, 0, -1)))

def process_job(jid, notify=None, t=None):
    """One step for one running job (at most one post). -> 'posted' | 'wait' | 'done' | 'failed' | 'paused' | None"""
    t = t or now()
    with transaction() as st:
        j = _job(st, jid)
        if not j or j["status"] != "running" or j["next_at"] > t: return None
        ch = next((c for c in _P(st)["channels"] if c["id"] == j["channel_id"]), None)
        if not ch:
            j["status"] = "paused"; j["last_err"] = "channel removed"; return "paused"
        if j["day"] != logic.today(): j["day"] = logic.today(); j["day_n"] = 0
        if j["daily"] and j["day_n"] >= j["daily"]:
            j["next_at"] = _midnight_next(); return "wait"
        idx = next((i for i, it in enumerate(j["items"]) if it["status"] == "pending"), None)
        if idx is None:
            j["status"] = "done"; job = json.loads(json.dumps(j)); done = True
        else:
            done = False; item = json.loads(json.dumps(j["items"][idx])); job = json.loads(json.dumps(j)); chan = dict(ch)
    if done:
        if notify: notify("job_done", job=job, counts=counts(job))
        return "done"
    if was_posted(chan["id"], track_key(item["artist"], item["title"])):
        res = ("skip", "duplicate")
    else:
        res = post_item(chan, job, item)
    with transaction() as st:
        j = _job(st, jid)
        if not j: return None
        ik = track_key(item["artist"], item["title"])            # find the item by key: the queue may have been re-ordered while we were posting
        it = next((x for x in j["items"] if x["status"] == "pending" and track_key(x["artist"], x["title"]) == ik), None)
        if it is None: return "wait"
        kind, val = res
        if kind == "retry":
            j["next_at"] = t + int(val); j["last_err"] = "flood wait %ss" % val; return "wait"
        if kind == "ok":
            it["status"] = "posted"; it["mid"] = val; it["tid"] = track_id(it["artist"], it["title"]); j["day_n"] += 1; j["fails_row"] = 0; j["next_at"] = t + j["interval"] * 60
            _P(st)["posted"].setdefault(str(j["channel_id"]), []).append(track_key(it["artist"], it["title"]))
            del _P(st)["posted"][str(j["channel_id"])][:-POSTED_CAP]
            out = "posted"
        elif kind == "skip":
            it["status"] = "skipped"; it["err"] = val; j["next_at"] = t; out = "wait"
        elif kind == "fatal":
            j["status"] = "paused"; j["last_err"] = val; out = "paused"
        else:
            it["tries"] += 1; it["err"] = str(val)[:80]; j["last_err"] = it["err"]
            if it["tries"] >= MAX_TRIES:
                it["status"] = "failed"; j["fails_row"] += 1
            j["next_at"] = t + (60 * it["tries"] if it["status"] == "pending" else j["interval"] * 60)
            if j["fails_row"] >= 3:
                j["status"] = "paused"; out = "paused"
            else:
                out = "failed" if it["status"] == "failed" else "wait"
        snapshot_job = json.loads(json.dumps(j))
    if out == "paused" and notify: notify("job_paused", job=snapshot_job, err=snapshot_job["last_err"])
    return out

def tick(notify=None, t=None):
    """Worker step: refresh due feeds, then advance every running job by at most one post."""
    t = t or now()
    for f in feeds():
        if f["enabled"] and f["last"] + f["refresh_h"] * 3600 <= t:
            try: refresh_feed(f["id"], notify)
            except Exception as e: log.warning("feed refresh failed: %s", type(e).__name__)
    for j in jobs():
        if j["status"] == "running" and j["next_at"] <= t:
            try: process_job(j["id"], notify, t)
            except Exception as e: log.warning("job step failed: %s", type(e).__name__)

def worker(notify):
    while True:
        time.sleep(TICK)
        try: tick(notify)
        except Exception as e: log.warning("poster tick: %s", type(e).__name__)

def verify(ref):
    """'@name' / t.me link / numeric id -> (chat dict, status_key). status: ok | notfound | notadmin | nopost"""
    ref = (ref or "").strip()
    if re.fullmatch(r"-?\d{5,20}", ref): chat_ref = int(ref)
    else:
        u = logic.clean_username(ref)
        if not u: return None, "notfound"
        chat_ref = "@" + u
    try: chat = C.call("getChat", {"chat_id": chat_ref}, timeout=20)
    except C.ApiError: return None, "notfound"
    return chat, check_admin(chat["id"])

def check_admin(chat_id):
    try: me = C.call("getChatMember", {"chat_id": chat_id, "user_id": C.BOT_ID}, timeout=20)
    except C.ApiError: return "notadmin"
    if me.get("status") == "creator": return "ok"
    if me.get("status") != "administrator": return "notadmin"
    if me.get("can_post_messages") is False: return "nopost"
    return "ok"

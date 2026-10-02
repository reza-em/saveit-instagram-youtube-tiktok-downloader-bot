"""Lyrics from free key-less sources: Shazam (if the recogniser returned them), Radio Javan (Persian), lrclib.net, lyrics.ovh."""
import re, logging
import requests

log = logging.getLogger("lyrics")
UA = "SaveIt/1.0 ( https://t.me/saveit_downloader_bot )"
MIN_LEN = 40
LABELS = {"shazam": "Shazam", "radiojavan": "Radio Javan", "lrclib": "lrclib.net", "lyricsovh": "lyrics.ovh"}

def _clean(t):
    t = (t or "").replace("\r", "")
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t

def from_radiojavan(title, artist, rj_id=None):
    H = {"User-Agent": UA}
    if not rj_id:
        r = requests.get("https://play.radiojavan.com/api/p/search", params={"query": ("%s %s" % (artist, title)).strip()}, headers=H, timeout=12)
        import engine
        kt, ka = engine._norm(title), engine._norm(artist)
        for x in (r.json().get("mp3s") or [])[:6]:
            if engine._norm(x.get("song") or "") == kt and (not ka or ka in engine._norm(x.get("artist") or "")):
                rj_id = x.get("id"); break
    if not rj_id:
        return ""
    j = requests.get("https://play.radiojavan.com/api/p/mp3", params={"id": rj_id}, headers=H, timeout=12).json()
    return _clean(j.get("lyric"))

def from_lrclib(title, artist, duration=0):
    H = {"User-Agent": UA}
    p = {"track_name": title, "artist_name": artist}
    if duration: p["duration"] = int(duration)
    for params in (p, {"track_name": title, "artist_name": artist}):
        r = requests.get("https://lrclib.net/api/get", params=params, headers=H, timeout=15)
        if r.status_code == 200 and r.json().get("plainLyrics"):
            return _clean(r.json()["plainLyrics"])
    r = requests.get("https://lrclib.net/api/search", params={"q": ("%s %s" % (artist, title)).strip()}, headers=H, timeout=15)
    if r.status_code == 200 and isinstance(r.json(), list):
        for x in r.json()[:5]:
            if x.get("plainLyrics"):
                return _clean(x["plainLyrics"])
    return ""

def from_lyricsovh(title, artist):
    if not artist:
        return ""
    from urllib.parse import quote
    r = requests.get("https://api.lyrics.ovh/v1/%s/%s" % (quote(artist, safe=""), quote(title, safe="")), headers={"User-Agent": UA}, timeout=15)
    return _clean(r.json().get("lyrics")) if r.status_code == 200 else ""

def find(title, artist="", duration=0, rj_id=None, shazam=""):
    """-> (text, source_key) or (None, None). Persian-looking text tries Radio Javan first."""
    fa = bool(re.search(r"[\u0600-\u06FF]", "%s %s" % (title, artist)))
    chain = []
    if shazam and len(shazam) >= MIN_LEN: chain.append(("shazam", lambda: shazam))
    rj = ("radiojavan", lambda: from_radiojavan(title, artist, rj_id))
    chain += [rj] if (fa or rj_id) else []
    chain += [("lrclib", lambda: from_lrclib(title, artist, duration)), ("lyricsovh", lambda: from_lyricsovh(title, artist))]
    if not (fa or rj_id): chain.append(rj)
    for key, fn in chain:
        try:
            t = _clean(fn())
            if len(t) >= MIN_LEN:
                return t, key
        except Exception as e:
            log.info("lyrics via %s failed: %s", key, type(e).__name__)
    return None, None

def split(text, limit=3900):
    """Chunks <= limit split on line boundaries."""
    out, cur = [], ""
    for ln in text.split("\n"):
        while len(ln) > limit:
            if cur: out.append(cur); cur = ""
            out.append(ln[:limit]); ln = ln[limit:]
        if len(cur) + len(ln) + 1 > limit:
            out.append(cur); cur = ln
        else:
            cur = (cur + "\n" + ln) if cur else ln
    if cur: out.append(cur)
    return out

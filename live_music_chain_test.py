"""Live test of the music provider chain (search + match + download) from this box. Honest per-source status."""
import os, sys, logging, json
os.environ.setdefault("DL_TELEGRAM_BOT_TOKEN", "0:live-test")
import engine, music
logging.basicConfig(level=logging.WARNING)
def line(t): print(t, flush=True)
wd = engine.new_workdir()
def show_chain(name, meta):
    line("\n=== %s  (meta: %s - %s, %ss)" % (name, meta["artist"], meta["title"], meta.get("duration")))
    try:
        info = music.audio_info(meta)
    except engine.EngineError as e:
        line("  NO MATCH: %s" % e.code); return None
    line("  provider status:", ) if False else None
    line("  status: " + ", ".join("%s=%s" % kv for kv in music.LAST_STATUS.items()))
    for i, c in enumerate(info["dl_cands"]):
        line("  #%d %-10s score=%s dur=%ss  %s | %s" % (i + 1, c["source"], c.get("score"), c.get("duration"), c.get("artist"), c.get("title")))
    engine._clear_media(wd)
    try:
        p = engine.download_audio(info, "mp3", wd)
        d = engine.probe_media(p).get("duration")
        line("  DOWNLOADED via %s: %d KB, %ss, audio=%s" % (info["match"]["source_label"], os.path.getsize(p) // 1024, d, engine.has_audio_stream(p)))
        line("  caption: " + info["caption"].replace("\n", " | "))
    except engine.EngineError as e:
        line("  DOWNLOAD FAILED: %s %s" % (e.code, e.detail[:120]))
    return info
def show_search(q):
    line("\n=== SEARCH %r" % q)
    hits = music.search(q, 5)
    line("  status: " + ", ".join("%s=%s" % kv for kv in music.LAST_STATUS.items()))
    for i, h in enumerate(hits):
        line("  %d. [%s] %s — %s  %s" % (i + 1, music.SHORT[h["source"]], music.display_title(h), h.get("artist") or h.get("uploader"), music.fmt_dur(h.get("duration"))))
try:
    show_chain("English hit", {"id": None, "title": "Never Gonna Give You Up", "artist": "Rick Astley", "artists": ["Rick Astley"], "duration": 213, "cover": None})
    show_chain("Persian (Googoosh)", {"id": None, "title": "Hamsafar", "artist": "Googoosh", "artists": ["Googoosh"], "duration": 269, "cover": None})
    show_chain("Persian (Shadmehr, Persian script)", {"id": None, "title": "ترس", "artist": "شادمهر عقیلی", "artists": ["شادمهر عقیلی"], "duration": 0, "cover": None})
    # Spotify link -> metadata -> chain
    c = engine.classify("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
    meta = engine.spotify_track_meta(c["sp_id"]); line("\nSpotify link -> %s - %s (%ss)" % (meta["artist"], meta["title"], meta["duration"]))
    show_chain("Spotify link track", meta)
    for q in ("Rick Astley Never Gonna Give You Up", "Googoosh", "گوگوش همسفر", "Shadmehr Aghili Tars"):
        show_search(q)
finally:
    engine.cleanup(wd)

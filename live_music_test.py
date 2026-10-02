"""Live SoundCloud + Spotify checks from this box (network required). Prints honest results."""
import os, sys, logging, json
os.environ.setdefault("DL_TELEGRAM_BOT_TOKEN", "0:live-test")
import engine
logging.basicConfig(level=logging.INFO, format="%(message)s")
def show(t, v): print(("OK   " if v else "FAIL ") + t)
wd = engine.new_workdir()
try:
    # SoundCloud track
    info = engine.probe(engine.classify("https://soundcloud.com/forss/flickermood"))
    show("soundcloud probe: %s / %s (%ss)" % (info["title"], info["uploader"], info["duration"]), info["kind"] == "audio")
    p = engine.download_audio(info, "mp3", wd); show("soundcloud mp3 download %d KB" % (os.path.getsize(p) // 1024), os.path.getsize(p) > 50000)
    engine._clear_media(wd)
    # SoundCloud set (flat)
    coll = engine.probe(engine.classify("https://soundcloud.com/forss/sets/soulhack"))
    show("soundcloud set: %s, %d of %s tracks" % (coll["title"], len(coll["items"]), coll["total"]), coll["kind"] == "collection" and coll["items"])
    it = engine.resolve_item(coll, coll["items"][0]); show("soundcloud set item resolved: %s" % it["title"], it["kind"] == "audio")
    # Spotify track
    info = engine.probe(engine.classify("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"))
    show("spotify meta: %s - %s (%ss) cover=%s" % (info["uploader"], info["title"], info["duration"], bool(info["thumb"])), info["title"] == "Never Gonna Give You Up")
    print("     matched:", json.dumps(info["match"], ensure_ascii=False))
    p = engine.download_audio(info, "m4a", wd); show("spotify->youtube m4a download %d KB" % (os.path.getsize(p) // 1024), os.path.getsize(p) > 50000)
    print("     ffprobe audio:", engine.has_audio_stream(p))
    # Spotify album
    alb = engine.probe(engine.classify("https://open.spotify.com/album/4LH4d3cOWNNsVw41Gqt2kv"))
    show("spotify album: %s / %s, %d of %d tracks" % (alb["title"], alb["uploader"], len(alb["items"]), alb["total"]), alb["kind"] == "collection")
    ai = engine.resolve_item(alb, alb["items"][0]); print("     first track ->", ai["match"]["title"], ai["match"]["channel"], ai["match"]["score"])
finally:
    engine.cleanup(wd)

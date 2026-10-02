"""Live: download via the bot's engine at several qualities and verify an audio stream with ffprobe."""
import os, engine as E
CASES = [("YouTube", "https://www.youtube.com/watch?v=jNQXAC9IVRw"), ("YouTube Short", "https://www.youtube.com/shorts/bQm1iU3R6pg"),
         ("TikTok", "https://www.tiktok.com/@scout2015/video/6718335390845095173"), ("Pinterest video", "https://www.pinterest.com/pin/1970393582328573/")]
for name, url in CASES:
    info = E.probe(E.classify(url))
    qs = sorted(info["quals"])
    for q in ([qs[0], E.pick_best_fit(info["quals"], E.SAFE_LIMIT)] if len(qs) > 1 else qs):
        wd = E.new_workdir()
        try:
            p = E.download_video(info, q, wd)
            print("%-14s q=%-5s %9d bytes audio=%s flagged_missing=%s caption=%r" % (name, q, os.path.getsize(p), E.has_audio_stream(p), info.get("_audio_missing"), (info.get("caption") or "")[:50]))
        except E.EngineError as e:
            print(name, q, "FAIL", e.code)
        finally: E.cleanup(wd)

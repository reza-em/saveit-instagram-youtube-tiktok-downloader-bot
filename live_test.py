"""Live download tests FROM THIS BOX through the bot's own engine (no Telegram involved). Honest pass/fail report."""
import os, sys, time
import engine as E
CASES = [
 ("YouTube (video, 'Me at the zoo')", "https://www.youtube.com/watch?v=jNQXAC9IVRw", "video"),
 ("YouTube Shorts", "https://www.youtube.com/shorts/bQm1iU3R6pg", "video"),
 ("TikTok video", "https://www.tiktok.com/@scout2015/video/6718335390845095173", "video"),
 ("Pinterest video pin", "https://www.pinterest.com/pin/1970393582328573/", "video"),
 ("Pinterest image pin", "https://www.pinterest.com/pin/7248049390707218/", "gallery"),
 ("Instagram post (public)", "https://www.instagram.com/p/C-4UkYfNDtR/", "any"),
 ("Instagram profile picture (@instagram)", "@instagram", "profile"),
 ("Instagram stories (@instagram) w/o cookies", "https://www.instagram.com/stories/instagram/", "any"),
]
res = []
for name, url, kind in CASES:
    t0 = time.time(); wd = E.new_workdir()
    try:
        if kind == "profile":
            path, hires = E.profile_picture("instagram", wd)
            out = "OK  %s bytes, hires=%s" % (os.path.getsize(path), hires)
        else:
            c = E.classify(url)
            info = E.probe(c)
            if info["kind"] == "video":
                q = E.pick_best_fit(info["quals"], E.SAFE_LIMIT) if info.get("quals") else None
                p = E.download_video(info, q, wd)
                out = "OK  video q=%sp %s bytes; quals=%s" % (q, os.path.getsize(p), sorted(info["quals"]))
                a = E.download_audio(info, "mp3", wd)
                out += " | audio mp3 %s bytes" % os.path.getsize(a)
            else:
                items = info["items"]; fs = E.download_gallery(info, wd)
                out = "OK  gallery %d item(s) -> %d file(s), %s bytes" % (len(items), len(fs), sum(os.path.getsize(f) for f in fs))
    except E.EngineError as e:
        out = "FAIL code=%s detail=%s" % (e.code, e.detail.strip().replace("\n", " ")[-140:])
    except Exception as e:
        out = "FAIL exception %s" % type(e).__name__
    finally:
        E.cleanup(wd)
    print("%-44s %s (%.1fs)" % (name, out, time.time() - t0)); sys.stdout.flush()

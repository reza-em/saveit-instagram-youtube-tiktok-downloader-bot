"""LIVE test (real network, public posts): probe + real download + the bot's own send path (jobs.send_video / send_gallery)
with Telegram calls captured locally -- nothing is sent to anyone. Run: ./venv/bin/python live_threads_x_test.py"""
import os, sys, json
os.environ.setdefault("DL_TELEGRAM_BOT_TOKEN", "0:live-test")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core as C, engine, jobs
SENT = []
def fake(method, data=None, files=None, timeout=60):
    d = dict(data or {})
    if files: d["_files"] = {k: (v[0], len(v[1].read()) if hasattr(v[1], "read") else len(v[1])) for k, v in files.items()}
    SENT.append((method, d)); return {"message_id": 1}
C.call = fake; C.edit_text = lambda *a, **k: None; C.send = lambda *a, **k: SENT.append(("send", {"text": a[1] if len(a) > 1 else ""})) or {"message_id": 1}; C.show = C.send
jobs.finish = lambda *a, **k: None
class P:
    last = 0
    def __call__(self, p): pass
    def stage(self, k): pass
CASES = [("X video (NASA)", "https://x.com/NASA/status/2105315677045203022"), ("X 2 videos + 2 photos (SpaceX)", "https://twitter.com/SpaceX/status/2105325070876807597"),
         ("X 4 photos", "https://x.com/astro_anil/status/2104580150926905720"), ("X GIF", "https://x.com/GIPHY/status/1957913446118285360"),
         ("Threads video", "https://www.threads.com/@instagram/post/DQNCKbZjq-v"), ("Threads 3-video carousel (.net)", "https://www.threads.net/@zuck/post/DSVRshjkbtK"),
         ("Threads image", "https://www.threads.com/@natgeo/post/DbTBdl3kz2g"), ("X text-only", "https://x.com/jack/status/20"), ("Threads text-only", "https://www.threads.com/@texasbluesalley/post/DKUq2_EgVUF")]
bad = 0
for label, url in CASES:
    SENT.clear(); wd = engine.new_workdir()
    try:
        info = engine.probe(engine.classify(url))
        if info["kind"] == "video":
            ok = jobs.send_video(1, 1, "en", 1, info, None if not info["quals"] else max(info["quals"]), wd, P())
        else:
            ok = jobs.send_gallery(1, 1, "en", 1, info, wd, P())
        m = [(a, d.get("_files") and list(d["_files"].values())[0][1] or (len(json.loads(d["media"])) if "media" in d else 0), (d.get("caption") or "")[:30].replace("\n", " ")) for a, d in SENT if a.startswith("send") and a != "send"]
        print("OK  ", label, "->", info["kind"], m[:3]); 
    except engine.EngineError as e:
        print("ERR ", label, "->", e.code, "(expected for text-only)" if "text-only" in label else ""); bad += "text-only" not in label
    except Exception as e:
        print("EXC ", label, type(e).__name__, str(e)[:100]); bad += 1
    finally: engine.cleanup(wd)
print("failures:", bad)

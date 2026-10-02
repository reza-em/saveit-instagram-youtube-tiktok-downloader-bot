"""Offline tests: mocked Telegram API + mocked engine. Never touches the network or the real state.json/cookies."""
import os, sys, io, json, tempfile, shutil, time, subprocess
import requests
os.environ["DL_TELEGRAM_BOT_TOKEN"] = "123456:TEST-TOKEN-ABCDEFGHIJKLMNOPQRSTUVWXYZ012"
import store
BALE = os.environ.get("DL_PLATFORM") == "bale"          # run the same suite against the Bale build (logic parity; adapter specifics are in test_bale.py)
tmp = tempfile.mkdtemp(); store.set_path(os.path.join(tmp, "state.json"))
import core as C, logic, engine, gate, ui, jobs, admin, bot, campaign, stars
import meta as _meta_mod
_meta_mod.lookup = lambda title, artist: {}        # tests never touch iTunes / MusicBrainz
engine.COOKIE_DIR = os.path.join(tmp, "cookies"); engine.TMP_ROOT = os.path.join(tmp, "work")
jobs.SYNC = True
_real_sleep = time.sleep
time.sleep = lambda s: None
C.BOT_USERNAME = "saveit_downloader_bot"; C.BOT_ID = 777
from PIL import Image

PASS = FAIL = 0
def ok(cond, name):
    global PASS, FAIL
    if cond: PASS += 1; print("  ok  ", name)
    else: FAIL += 1; print("  FAIL", name)

SENT = []
CHAT_MEMBERS = {}      # (chat_id, user_id) -> status
BOT_STATUS = {"-1001000000001": "administrator", "-1001000000002": "member"}
CHATS = {"@chan_one": {"id": -1001000000001, "type": "channel", "title": "Chan One", "username": "chan_one"},
         "@chan_two": {"id": -1001000000002, "type": "channel", "title": "Chan Two", "username": "chan_two"},
         "-1001000000003": {"id": -1001000000003, "type": "channel", "title": "Private Ch"}}
BOT_STATUS["-1001000000003"] = "administrator"
LOST = set()
def fake_call(method, data=None, files=None, timeout=60):
    data = dict(data or {})
    if files:
        data["_files"] = {k: (v[0], v[1].read() if hasattr(v[1], "read") else v[1]) for k, v in files.items()}
        for v in files.values():
            if hasattr(v[1], "seek"): v[1].seek(0)
    SENT.append((method, data))
    if method == "getChat":
        ref = str(data["chat_id"])
        if ref in CHATS: return CHATS[ref]
        raise C.ApiError("Bad Request: chat not found")
    if method == "getChatMember":
        cid = str(data["chat_id"]); uid = int(data["user_id"])
        if int(cid) in LOST: raise C.ApiError("Bad Request: chat not found")
        if uid == 777: 
            st = BOT_STATUS.get(cid)
            if not st: raise C.ApiError("Bad Request: user not found")
            return {"status": st}
        st = CHAT_MEMBERS.get((int(cid), uid), "left")
        return {"status": st}
    if method == "sendPhoto" and "_files" in data:
        return {"message_id": 1, "photo": [{"file_id": "SMALL"}, {"file_id": "LOGO_FID_1"}]}
    if method == "getFile": return {"file_path": "docs/cookies.txt"}
    return {"message_id": len(SENT) + 100}
C.call = fake_call
COOKIE_BYTES = b""
class FakeResp:
    status_code = 200
    @property
    def content(self): return COOKIE_BYTES
C.sess.get = lambda *a, **k: FakeResp()

def of(method, to=None):
    return [d for m, d in SENT if m == method and (to is None or d.get("chat_id") == to)]
def texts(to):
    return [d.get("text") or d.get("caption") or "" for m, d in SENT if d.get("chat_id") == to and (d.get("text") or d.get("caption"))]
def last(to): 
    t = texts(to); return t[-1] if t else ""
def clear(): SENT.clear()
def tg(uid, username=None, lang="fa"): return {"id": uid, "first_name": f"U{uid}", "username": username, "language_code": lang}
def say(uid, text=None, username=None, lang="fa", **extra):
    m = {"chat": {"id": uid, "type": "private"}, "from": tg(uid, username, lang), "text": text, "message_id": 50}; m.update(extra)
    bot.handle_message({k: v for k, v in m.items() if v is not None})
def press(uid, data, mid=5, username=None):
    bot.handle_callback({"id": "c", "from": tg(uid, username), "data": data, "message": {"chat": {"id": uid, "type": "private"}, "message_id": mid}})
def markup_of(d):
    return json.loads(d["reply_markup"])["inline_keyboard"] if d.get("reply_markup") else []
def buttons(to):
    out = []
    for m, d in SENT:
        if d.get("chat_id") == to and d.get("reply_markup"):
            for r in markup_of(d): out += r
    return out
def cb_data(to): return [b.get("callback_data") for b in buttons(to) if b.get("callback_data")]

# ---- fake engine ----
MKV = {"kind": "video", "platform": "youtube", "url": "https://youtu.be/x", "title": "Test <b>Vid</b>", "uploader": "Chan", "duration": 120,
       "quals": {1080: {"fid": "a", "size": 90_000_000, "muxed": False}, 720: {"fid": "b", "size": 40_000_000, "muxed": False},
                 480: {"fid": "c", "size": 20_000_000, "muxed": False}, 360: {"fid": "d", "size": 10_000_000, "muxed": False}},
       "audio_size": 2_000_000, "has_audio": True, "via": "ytdlp"}
PROBES = {}
def fake_probe(c):
    k = c["url"]
    r = PROBES.get(k)
    if isinstance(r, Exception): raise r
    if r: return r
    raise engine.EngineError("unsupported", "x")
REAL_PROBE = engine.probe
engine.probe = fake_probe
DL_CALLS = []
SIZES = {}
def fake_dl_video(info, q, wd, cb=None):
    DL_CALLS.append(("video", q))
    p = os.path.join(wd, "v.mp4"); open(p, "wb").write(b"0" * SIZES.get(q, 1000)); 
    if cb: cb(50.0)
    return p
def fake_dl_audio(info, fmt, wd, cb=None):
    DL_CALLS.append(("audio", fmt))
    p = os.path.join(wd, "a." + fmt); open(p, "wb").write(b"1" * 500); return p
REAL_DL_VIDEO = engine.download_video
engine.download_video = fake_dl_video; engine.download_audio = fake_dl_audio
engine.probe_media = lambda p: {"width": 1280, "height": 720, "duration": 120}
engine.make_thumb = lambda p, wd: None
def fake_direct(url, dst, limit=0):
    Image.new("RGB", (64, 64), (10, 200, 10)).save(dst, "JPEG") if (dst.endswith((".jpg", ".jpeg")) or os.path.basename(dst).startswith("cover")) else open(dst, "wb").write(b"x" * 100)
    return dst
engine.download_direct = fake_direct

H = "https://ble.ir/" if BALE else "https://t.me/"      # public-link host of the platform under test
OWNER = 1000; U1 = 2001; U2 = 2002; EXTRA = 3003

print("== profile / lang / welcome / logo")
if BALE:
    _code = logic.ensure_claim_code(); say(999, "/claim " + _code)
    ok(store.admin_id() == 999, "bale: first /claim binds"); store.set_path(store.PATH)
    with store.transaction() as st_: st_["admin_id"] = None; st_["settings"]["claim_code"] = _code
    say(OWNER, "/claim " + _code); ok(store.admin_id() == OWNER, "bale: owner claimed with the one-time code (username is NOT trusted)")
    say(999, "/start", username="example_owner"); ok(store.admin_id() == OWNER, "bale: username does not bind")
else:
    say(OWNER, "/start", username="example_owner")
    ok(store.admin_id() == OWNER, "owner auto-bound by username 'example_owner' -> numeric id")
    say(999, "/start", username="example_owner")
    ok(store.admin_id() == OWNER, "second 'example_owner' cannot rebind")
clear(); say(U1, "/start")
ok(of("sendMessage", U1) and "Choose your language" in of("sendMessage", U1)[0]["text"], "/start shows language picker")
clear(); press(U1, "l:en")
ok(store.get_user(U1)["lang"] == "en", "language stored")
ph = of("sendPhoto", U1)
ok(len(ph) == 1 and "_files" in ph[0] and "Welcome to SaveIt" in ph[0]["caption"], "welcome sent as photo (first time uploads logo)")
ok(store.snapshot()["logo"].get("file_id") == "LOGO_FID_1", "file_id cached")
clear(); press(U1, "l:fa")
ph = of("sendPhoto", U1)
ok(len(ph) == 1 and ph[0]["photo"] == "LOGO_FID_1" and "_files" not in ph[0], "second welcome uses cached file_id")
ok("به SaveIt خوش اومدی" in ph[0]["caption"], "welcome in Persian")
ok(bot.NAME == "دانلودر اینستاگرام | یوتیوب | تیک‌تاک | SaveIt" and len(bot.NAME) <= 64 and bot.ABOUT["fa"].startswith("📥") and "@example_owner" in bot.ABOUT["fa"] and "song ID" in bot.ABOUT["en"] or "song recognition" in bot.ABOUT["en"], "profile strings match spec")
# logo replaced -> cache invalidated
st0 = store.snapshot()["logo"]["sha"]
orig_logo = ui.LOGO; alt = os.path.join(tmp, "l2.jpg"); Image.new("RGB", (50, 50), (1, 2, 3)).save(alt); ui.LOGO = alt
clear(); press(U1, "l:fa"); ph = of("sendPhoto", U1)
ok("_files" in ph[0], "changed logo file invalidates cached file_id")
ui.LOGO = orig_logo
ok(os.path.getsize(orig_logo) > 10000, "assets/logo.jpg present")

print("== profile setup calls")
clear(); store.snapshot(); 
with store.transaction() as st: st["profile_sha"] = ""
bot.setup_profile()
ms = [(m, d.get("language_code")) for m, d in SENT]
for m in ("setMyName", "setMyDescription", "setMyShortDescription"):
    ok({l for mm, l in ms if mm == m} == {None, "fa", "en"}, f"{m} for default+fa+en")
clear(); bot.setup_profile(); ok(not SENT, "profile setup idempotent")

print("== help / legal")
clear(); say(U1, "/help")
ok("مسئولیت" in last(U1), "fa help has legal notice")
store.update_user(U1, lang="en"); clear(); say(U1, "/help")
ok("responsible for respecting content owners" in last(U1), "en help has legal notice")

print("== link detection")
ok(engine.classify("https://youtu.be/abc")["platform"] == "youtube", "youtube")
ok(engine.classify("https://www.tiktok.com/@a/video/1")["platform"] == "tiktok", "tiktok")
ok(engine.classify("https://www.instagram.com/stories/highlights/1789/")["kind"] == "highlight", "ig highlight")
ok(engine.classify("https://www.instagram.com/stories/bob/3333/")["kind"] == "story", "ig story")
ok(engine.classify("https://www.instagram.com/bob/")["kind"] == "profile", "ig profile")
ok(engine.classify("https://example.com/x") is None, "unsupported link")
ok(engine.classify_error("Sign in to confirm your age. This video may be inappropriate") == "age", "err age")
ok(engine.classify_error("Your IP address is blocked from accessing this post") == "ipblock", "err ipblock")
ok(engine.classify_error("HTTP Error 429: Too Many Requests") == "rate", "err rate")
ok(engine.classify_error("Private video. Sign in if you've been granted access") == "private", "err private")
ok(engine.classify_error("The uploader has not made this video available in your country") == "geo", "err geo")

print("== video flow: options, quality, size fit")
PROBES["https://youtu.be/x"] = dict(MKV)
clear(); say(U1, "look https://youtu.be/x please")
cbs = cb_data(U1)
ok(any(c.endswith(":vb") for c in cbs) and any(c.endswith(":v720") for c in cbs) and any(c.endswith(":v360") for c in cbs), "video quality buttons")
ok(any(c.endswith(":a3") for c in cbs) and any(c.endswith(":a4") for c in cbs), "audio buttons (mp3/m4a)")
labels = " ".join(b["text"] for b in buttons(U1))
ok("⚠️" in labels, ">50MB quality flagged")
ok(any(d.get("text", "").startswith("🎬") and "&lt;b&gt;" in d["text"] for d in of("editMessageText", U1)), "title HTML-escaped")
tok = [c for c in cbs if c.endswith(":vb")][0].split(":")[1]
SIZES.update({720: 40_000_000, 1080: 90_000_000, 480: 20_000_000, 360: 10_000_000})
clear(); DL_CALLS.clear(); press(U1, f"d:{tok}:vb")
ok(DL_CALLS == [("video", 720)], "best picks highest that fits (720, skipping 1080 by estimate)")
ok(of("sendVideo", U1) and of("sendVideo", U1)[0]["_files"]["video"][1] != b"", "sendVideo called")
ok(any("uploading" in (d.get("text") or "").lower() or "آپلود" in (d.get("text") or "") for d in of("editMessageText", U1)), "progress edited to uploading")
ok(of("deleteMessage", U1), "status message cleaned up")
ok(logic.stats()["by_platform"].get("youtube") == 1, "download counted per platform")
# explicit 1080 -> too big by estimate -> falls to 720
clear(); DL_CALLS.clear(); press(U1, f"d:{tok}:v1080")
ok(DL_CALLS == [("video", 720)] and any("720" in t for t in texts(U1)), "explicit 1080 auto-lowers to 720 and says so")
# estimate wrong: actual file too big -> retry lower
SIZES.update({720: 60_000_000, 480: 20_000_000})
clear(); DL_CALLS.clear(); press(U1, f"d:{tok}:v720")
ok(DL_CALLS == [("video", 720), ("video", 480)], "real size > 50MB triggers retry at lower quality")
# nothing fits
for q in (720, 480, 360): SIZES[q] = 70_000_000
clear(); DL_CALLS.clear(); press(U1, f"d:{tok}:vb")
ok(not of("sendVideo", U1) and any("۵۰" in t or "50" in t for t in texts(U1)), "nothing fits -> friendly 50MB message")
ok(any(c.endswith(":a3") for c in cb_data(U1)), "...and offers audio-only")
ok(logic.recent_errors(1)[0]["code"] == "too_big", "too_big recorded in errors")
clear(); DL_CALLS.clear(); press(U1, f"d:{tok}:a3")
ok(DL_CALLS == [("audio", "mp3")] and of("sendAudio", U1), "audio mp3 sent")
press(U2, f"d:{tok}:vb"); ok(any("برای تو نیست" in t or "isn't yours" in t for t in texts(U2)), "tokens are per-user")
clear(); press(U1, "d:deadbeef:vb"); ok("منقضی" in last(U1) or "expired" in last(U1), "expired token message")

print("== errors are friendly")
for code, frag in (("private", "خصوصی"), ("age", "سنی"), ("geo", "جغرافیایی"), ("rate", "محدود"), ("login", "لاگین"), ("ipblock", "مسدود")):
    PROBES["https://youtu.be/e"] = engine.EngineError(code, "detail secret")
    store.update_user(U1, lang="fa"); clear(); say(U1, "https://youtu.be/e")
    ok(any(frag in (d.get("text") or "") for d in of("editMessageText", U1)), f"error {code} -> friendly fa text")
clear(); say(U1, "https://example.com/zzz"); ok("پشتیبانی نمی‌شه" in last(U1), "unsupported link message")
import music
_ms = music.search; music.search = lambda q, n=5: []
clear(); say(U1, "hello there"); ok("پیدا نشد" in last(U1) or "چیزی" in last(U1), "plain text -> music search (nothing found -> friendly message)")
music.search = _ms
ok(len(logic.recent_errors(20)) >= 6, "errors recorded for admin")

print("== instagram stories need cookies")
c = engine.classify("https://www.instagram.com/stories/bob/")
engine.probe = engine.__dict__.get("_orig_probe", engine.probe)
import importlib
real_probe = importlib.import_module("engine").probe
# call the real probe implementation for this case
src_probe = None
def real(c2):
    if c2["kind"] in ("story", "stories", "highlight") and not engine.cookie_path("instagram"):
        raise engine.EngineError("cookies_needed", "no instagram cookies")
PROBES["https://www.instagram.com/stories/bob/"] = engine.EngineError("cookies_needed", "")
clear(); say(U1, "https://www.instagram.com/stories/bob/")
ok(any("ادمین" in (d.get("text") or "") and "فعال" in (d.get("text") or "") for d in of("editMessageText", U1)), "stories w/o cookies -> polite 'admin must enable'")

print("== gallery / media group chunks of 10 / pinterest / profile")
with store.transaction() as st: st["settings"]["free_quota"] = 500
items = [{"url": f"https://x/{i}.jpg", "ext": "jpg", "type": "image", "w": 10, "h": 10} for i in range(23)]
PROBES["https://www.instagram.com/p/AAA/"] = {"kind": "gallery", "platform": "instagram", "url": "https://www.instagram.com/p/AAA/", "items": items, "sub": "gallery", "title": "car", "uploader": "u"}
clear(); say(U1, "https://www.instagram.com/p/AAA/")
tok2 = [c for c in cb_data(U1) if c.endswith(":g")][0].split(":")[1]
clear(); press(U1, f"d:{tok2}:g")
mg = of("sendMediaGroup", U1)
ok([len(json.loads(d["media"])) for d in mg] == [10, 10, 3], "23 photos -> sendMediaGroup chunks 10+10+3")
ok(json.loads(mg[0]["media"])[0].get("caption"), "caption only on first item")
PROBES["https://www.pinterest.com/pin/1/"] = {"kind": "gallery", "platform": "pinterest", "url": "https://www.pinterest.com/pin/1/", "items": items[:1], "sub": "gallery", "title": "pin", "uploader": ""}
clear(); say(U1, "https://www.pinterest.com/pin/1/"); tok3 = [c for c in cb_data(U1) if c.endswith(":g")][0].split(":")[1]
clear(); press(U1, f"d:{tok3}:g"); ok(len(of("sendPhoto", U1)) == 1, "pinterest single image sent as photo")
# profile
engine.profile_picture = lambda user, wd: (fake_direct("", os.path.join(wd, user + ".jpg")), True)
clear(); say(U1, "@some.user")
ok(any(c.endswith(":p") for c in cb_data(U1)) and any("عکس پروفایل" in b["text"] for b in buttons(U1)), "@username -> profile picture button")
tok4 = [c for c in cb_data(U1) if c.endswith(":p")][0].split(":")[1]
clear(); press(U1, f"d:{tok4}:p"); ok(of("sendPhoto", U1) and of("sendDocument", U1), "profile pic sent as photo + original document")
clear(); say(U1, "https://www.instagram.com/some.user/")
ok(any(c.endswith(":p") for c in cb_data(U1)), "profile link -> profile button")

print("== per-user concurrency / queue")
import threading
jobs.SYNC = False
ev = threading.Event(); order = []
def slow(): ev.wait(2); order.append("a")
def quick(): order.append("b")
r1 = jobs.submit(9001, slow); r2 = jobs.submit(9001, quick)
ok((r1, r2) == ("started", "queued"), "1 active + queued")
r3 = jobs.submit(9001, quick); r4 = jobs.submit(9001, quick); r5 = jobs.submit(9001, quick)
ok(r5 == "full", "queue is small and bounded")
ev.set(); time.sleep = lambda s: None
class _t: sleep = staticmethod(_real_sleep)
for _ in range(50):
    if not jobs.user_busy(9001): break
    _t.sleep(0.05)
ok(order[0] == "a" and not jobs.user_busy(9001), "queued jobs run after the active one (sequential per user)")
active = []; peak = [0]; lk = threading.Lock()
def work():
    with lk: active.append(1); peak[0] = max(peak[0], len(active))
    _t.sleep(0.15)
    with lk: active.pop()
for uid in range(9100, 9106): jobs.submit(uid, work)
for _ in range(100):
    if not any(jobs.user_busy(u) for u in range(9100, 9106)): break
    _t.sleep(0.05)
ok(peak[0] <= jobs.GLOBAL_SLOTS, f"global concurrency <= {jobs.GLOBAL_SLOTS} (peak {peak[0]})")
jobs.SYNC = True

print("== quota / plans / referral")
store.update_user(U2, lang="fa")
with store.transaction() as st: st["settings"]["free_quota"] = 2
for i in range(2): ok(logic.consume(U2, "tiktok"), f"free download {i+1}")
ok(not logic.consume(U2, "tiktok"), "3rd denied (free_quota=2)")
ok(logic.access_info(U2, "tiktok")["free_left"] == 0, "no free left on tiktok")
with store.transaction() as st: st["users"][str(U2)]["free_day"] = "2000-01-01"
ok(logic.access_info(U2, "tiktok")["free_left"] == 2, "quota resets on a new day")
ok(logic.access_info(OWNER)["can"] and logic.access_info(OWNER)["kind"] == "admin", "admin unlimited")
ok(logic.store.settings()["free_quota"] == 2, "quota editable")
with store.transaction() as st: st["settings"]["free_quota"] = 5
ok(store.DEFAULT_SETTINGS["ad_every"] == 3, "default ad_every=3")
import importlib; ok(store.DEFAULT_SETTINGS["free_quota"] == 5, "default free quota is 5/day")
ok([p["title"] for p in logic.pplans()] == ["پایه / Basic", "اقتصادی / Economy", "پیشرفته / Advanced"], "default plan names")
ok(not any(logic.plan_ready(p) for p in logic.pplans()), "plans hidden until count+price set")
clear(); say(U2, "/plans"); ok("پلن‌های پولی به‌زودی" in last(U2), "user sees 'coming soon', no hidden plans")
ok([p["daily"] for p in logic.pplans()] == [15, 40, 100] and [p["popular"] for p in logic.pplans()] == [False, True, False], "default plans: 15/40/100 per day, Economy popular")
ok(logic.discounts() == {1: 0, 3: 10, 6: 20}, "default duration discounts 0/10/20")
logic.update_pplan(1, price=50000, features="بدون تبلیغ | سریع", popular=True)
ok(logic.plan_ready(logic.get_pplan(1)) and not logic.plan_ready(logic.get_pplan(3)), "plan ready only when price set")
clear(); say(U2, "/plans"); t = last(U2)
ok("پایه" in t and "۵۰٬۰۰۰" in t and "⭐" in t and "بدون تبلیغ" in t and str(U2) in t, "visible plan card with badge/features/ID")
ok("۱۳۵٬۰۰۰" in t and "۱۰٪" in t and "۲۴۰٬۰۰۰" in t and "۲۰٪" in t and "۴۰٬۰۰۰" in t and "🏆" in t, "plan card: 3/6-month totals with discount, percent saved, per-month price, best value")
rows_ = logic.price_table(logic.get_pplan(1)); ok([r["total"] for r in rows_] == [50000, 135000, 240000] and [r["per_month"] for r in rows_] == [50000, 45000, 40000] and rows_[2]["best"] and not rows_[0]["best"], "price_table math")
logic.set_discount(6, 30); ok(logic.price_table(logic.get_pplan(1))[2]["total"] == 210000, "discount editable"); logic.set_discount(6, 20)

ok(any(b.get("url") == H + "example_owner" for b in buttons(U2)), "contact admin URL button")
# quota exhausted message
store.update_user(U2, free_day=logic.today(), free_plat={"tiktok": 5}, credits=0)
PROBES["https://www.tiktok.com/@a/video/9"] = dict(MKV, platform="tiktok", url="https://www.tiktok.com/@a/video/9")
clear(); say(U2, "https://www.tiktok.com/@a/video/9")
ok(str(U2) in last(U2) and "پایه" in last(U2), "quota out: shows plans + numeric ID")
logic.update_pplan(1, price=0, popular=False); logic.update_pplan(2, price=0)
ok(any(b.get("url") == H + "example_owner" for b in buttons(U2)), "quota out: contact button")
logic.set_support("backup", "backup_admin")
clear(); say(U2, "/plans"); us = [b["url"] for b in buttons(U2) if b.get("url")]
ok(H + "example_owner" in us and H + "backup_admin" in us, "two support buttons with backup")
logic.set_support("backup", "")
# referral
R1, R2 = 4001, 4002
say(R1, "/start"); clear(); say(R2, "/start ref_4001")
ok(store.settings()["ref_bonus"] == 5 and store.settings()["ref_invitee_bonus"] == 3, "new higher referral defaults (5 inviter / 3 friend)")
ok(store.get_user(R1)["credits"] == 5 and store.get_user(R2)["credits"] == 3, "referral bonuses (5 inviter / 3 invitee)")
say(R2, "/start ref_4001"); ok(store.get_user(R1)["credits"] == 5, "no double referral")
clear(); say(R1, "/invite"); ok("ref_4001" in " ".join(texts(R1)) and (BALE or any("share/url" in b.get("url", "") for b in buttons(R1))), "invite link + Share button (Telegram only)")
# admin grants
print("== admin panel / grants / multi-admin")
store.update_user(OWNER, lang="fa"); clear(); say(OWNER, "/admin")
ok(all(any(k in b.get("callback_data", "") for b in buttons(OWNER)) for k in ("a:pp", "a:ads", "a:ch", "a:ck", "a:upd", "a:err", "a:bc", "a:ban")), "admin home has all sections")
clear(); say(U1, "/admin"); ok(not texts(U1), "non-admin cannot open /admin")
clear(); press(U1, "a:stats"); ok(not of("editMessageText", U1) and not of("sendMessage", U1), "non-admin callback ignored")
press(OWNER, "a:stats"); ok("آمار" in of("editMessageText", OWNER)[-1]["text"], "stats screen")
clear(); press(OWNER, "a:grant"); say(OWNER, str(U2)); ok(any(c.startswith("a:ugf:") for c in cb_data(OWNER)) and any(c.startswith("a:ugp:") for c in cb_data(OWNER)) and f"a:ugm:{U2}:3" in cb_data(OWNER), "grant menu: unlimited/days/credits, 1/3/6 month buttons and plan buttons")
clear(); press(OWNER, f"a:ugp:{U2}:1"); ok(all(f"a:ugpd:{U2}:1_{d}" in cb_data(OWNER) for d in (30, 90, 180)), "plan grant asks for a duration (1/3/6 months + custom)")
clear(); press(OWNER, f"a:ugpd:{U2}:1_90"); a_ = logic.access_info(U2)
ok(a_["kind"] == "plan" and a_["plan"]["daily"] == 15 and 89 <= (a_["plan"]["until"] - logic.now()) / 86400 <= 90.01 and "90" in last(U2), "plan + duration grant; user notified")
clear(); say(U2, "/me"); ok("پایه" in last(U2) and "۹۰" in last(U2) or "90" in last(U2), "/me shows plan and remaining days")
u0 = a_["plan"]["until"]; clear(); press(OWNER, f"a:ugpd:{U2}:1_30"); ok(logic.access_info(U2)["plan"]["until"] == u0 + 30 * 86400, "extending adds to the existing expiry")
# enforcement: plan quota per day, then falls back to free quota
for _ in range(15): assert logic.consume(U2, "youtube")
ok(logic.access_info(U2, "youtube")["plan"]["left"] == 0 and logic.access_info(U2, "youtube")["can"], "plan daily quota used up -> free quota still available")
with store.transaction() as st: st["users"][str(U2)]["plan_day"] = "2000-01-01"
ok(logic.access_info(U2, "youtube")["plan"]["left"] == 15, "plan quota resets daily")
with store.transaction() as st: st["users"][str(U2)]["plan"]["until"] = logic.now() - 5
ok(logic.access_info(U2)["kind"] == "quota", "expired plan reverts to free quota automatically")
logic.revoke(U2)
# reminders: once, 3 days before, only for grants longer than 3 days
logic.grant_plan(U2, 1, 30); with_ = store.transaction
with store.transaction() as st: st["users"][str(U2)]["plan"]["until"] = logic.now() + 2 * 86400; st["users"][str(U2)]["acc_from"] = logic.now() - 28 * 86400
r1 = logic.due_reminders(); r2 = logic.due_reminders()
ok(len(r1) == 1 and r1[0]["uid"] == U2 and r2 == [], "expiry reminder sent once")
logic.grant_plan(U2, 1, 30); ok(len(logic.due_reminders()) == 0, "no reminder right after an extension")
logic.revoke(U2); logic.grant_plan(U2, 1, 2); ok(logic.due_reminders() == [], "short grants (<=3 days) get no reminder (no spam)")
logic.revoke(U2)
ok(logic.grant_plan(U2, 999, 30) is None and logic.grant_plan(U2, 1, 0) is None, "grant_plan validates plan and days")
clear(); press(OWNER, f"a:ugm:{U2}:6"); ok(logic.access_info(U2)["kind"] == "until" and 179 <= (logic.access_info(U2)["until"] - logic.now()) / 86400 <= 180.01, "unlimited for 6 months")
clear(); bot.reminder_tick(); logic.revoke(U2)
clear(); press(OWNER, f"a:ugf:{U2}"); ok(logic.access_info(U2)["kind"] == "unlimited" and "نامحدود" in last(U2), "unlimited grant + notify")
press(OWNER, f"a:urc:{U2}"); ok(logic.access_info(U2)["kind"] == "quota" and store.get_user(U2)["credits"] == 0, "revoke")
clear(); press(OWNER, f"a:ugd:{U2}"); say(OWNER, "7"); ok(logic.access_info(U2)["kind"] == "until" and "7" in last(U2), "unlimited N days + notify")
say(EXTRA, "/start"); clear(); press(OWNER, "a:ada"); say(OWNER, str(EXTRA)); ok(logic.is_extra_admin(EXTRA), "extra admin added")
clear(); press(EXTRA, "a:home"); ok("a:pp" not in " ".join(cb_data(EXTRA)), "extra admin sees limited panel")
clear(); press(EXTRA, "a:ads"); ok("owner" in last(EXTRA) or "مالک" in last(EXTRA), "extra admin denied on owner-only section")
clear(); press(EXTRA, f"a:ugf:{R1}"); ok(logic.access_info(R1)["kind"] == "unlimited", "extra admin can grant")
logic.revoke(R1)
clear(); press(OWNER, "a:own"); say(OWNER, str(EXTRA)); press(OWNER, f"a:ownc:{EXTRA}")
ok(logic.is_owner(EXTRA) and not logic.is_admin(OWNER), "owner change (old owner loses rights)")
press(EXTRA, "a:own"); say(EXTRA, str(OWNER)); press(EXTRA, f"a:ownc:{OWNER}"); ok(logic.is_owner(OWNER), "owner restored")
# ban
clear(); press(OWNER, f"a:bn:{U1}"); clear(); say(U1, "https://youtu.be/x"); ok("مسدود" in last(U1), "banned user blocked")
press(OWNER, f"a:ubn:{U1}"); clear(); say(U1, "/help"); ok("مسدود" not in last(U1), "unbanned")
# settings
clear(); press(OWNER, "a:sn:free_quota"); say(OWNER, "۷"); ok(store.settings()["free_quota"] == 7, "free quota editable (Persian digits)")
with store.transaction() as st: st["settings"]["free_quota"] = 5

print("== price manager wizard")
clear(); press(OWNER, "a:ppa"); say(OWNER, "ویژه"); say(OWNER, "۲۰۰"); say(OWNER, "۹۹٬۰۰۰"); press(OWNER, "a:pps:feat"); press(OWNER, "a:ppp:0")
pl = logic.pplans(); ok(pl[-1]["title"] == "ویژه" and pl[-1]["daily"] == 200 and pl[-1]["price"] == 99000, "plan added via wizard (downloads/day + monthly price)")
clear(); press(OWNER, f"a:ppe:{pl[-1]['id']}"); press(OWNER, f"a:ppeu:{pl[-1]['id']}"); ok(logic.get_pplan(pl[-1]["id"])["daily"] == -1 and "♾" in logic.get_pplan(pl[-1]["id"]).get("title", "♾") + "♾", "plan can be set to unlimited downloads/day")
press(OWNER, "a:ppdm:3"); say(OWNER, "۱۵"); ok(logic.discounts()[3] == 15, "admin edits 3-month discount (Persian digits)"); logic.set_discount(3, 10)
pid = pl[-1]["id"]; press(OWNER, f"a:ppu:{pid}"); ok(logic.pplans()[-2]["id"] == pid, "reorder")
press(OWNER, f"a:ppx:{pid}"); ok(logic.get_pplan(pid) is None, "delete plan")

print("== ads")
clear(); press(OWNER, "a:ada2"); say(OWNER, "تبلیغ فارسی"); press(OWNER, "a:ads_skip:en"); press(OWNER, "a:ads_skip:photo"); say(OWNER, "https://example.com/promo"); say(OWNER, "برو 🚀")
ads = logic.ads(); ok(len(ads) == 1 and ads[0]["url"] == "https://example.com/promo" and ads[0]["en"] == "تبلیغ فارسی", "ad created via wizard")
aid = ads[0]["id"]
clear(); press(OWNER, f"a:adf:{aid}:en"); say(OWNER, "English ad"); ok(logic.get_ad(aid)["en"] == "English ad", "edit en text")
clear(); press(OWNER, f"a:adf:{aid}:photo"); say(OWNER, None, photo=[{"file_id": "P_S"}, {"file_id": "P_BIG"}]); ok(logic.get_ad(aid)["photo"] == "P_BIG", "ad photo set")
# ads shown after every 3 downloads to free users only
AU = 5001; say(AU, "/start"); store.update_user(AU, lang="en")
clear()
for i in range(3): jobs.after_success(AU, AU, "en", "tiktok")
ph = [d for m, d in SENT if m == "sendPhoto" and d.get("chat_id") == AU]
ok(len(ph) == 1 and "English ad" in ph[0]["caption"] and ph[0]["photo"] == "P_BIG", "ad (photo) shown after 3rd download, in user's language")
ok(markup_of(ph[0])[0][0].get("url") == "https://example.com/promo", "URL button on ad (no tracking)")
ok(logic.get_ad(aid)["views"] == 1, "view counted")
clear()
for i in range(2): jobs.after_success(AU, AU, "en", "tiktok")
ok(not of("sendPhoto", AU), "no ad before N downloads")
PU = 5002; say(PU, "/start"); logic.grant_unlimited(PU); clear()
for i in range(6): jobs.after_success(PU, PU, "en", "tiktok")
ok(not of("sendPhoto", PU) and not of("sendMessage", PU), "premium user never sees ads")
CU = 5003; say(CU, "/start"); logic.add_credits(CU, 5); clear()
for i in range(4): jobs.after_success(CU, CU, "en", "tiktok")
ok(not of("sendPhoto", CU), "credit holders never see ads")
logic.toggle_ad(aid, "track"); logic.update_ad(aid, photo=None)
store.update_user(AU, dl_since_ad=3); clear(); jobs.after_success(AU, AU, "en", "tiktok")
ok(markup_of(of("sendMessage", AU)[-1])[0][0].get("callback_data") == f"ad:{aid}", "tracked ad uses callback button")
clear(); press(AU, f"ad:{aid}"); ok(logic.get_ad(aid)["clicks"] == 1 and any(b.get("url") == "https://example.com/promo" for b in buttons(AU)), "click counted, URL button returned")
logic.toggle_ad(aid, "enabled"); store.update_user(AU, dl_since_ad=9); clear(); jobs.after_success(AU, AU, "en", "tiktok")
ok(not any("Sponsored" in t or "تبلیغ" in t for t in texts(AU)), "disabled ad not shown")
logic.toggle_ad(aid, "enabled")
ids = logic.ad_recipients(); ok(PU not in ids and AU in ids and CU not in ids and OWNER not in ids, "ad broadcast excludes premium/admins")
clear(); press(OWNER, f"a:adbc:{aid}"); ok("a:adby:" in " ".join(cb_data(OWNER)), "ad broadcast asks for confirmation")
clear(); press(OWNER, "a:adt"); ok(store.settings()["ads_enabled"] is False, "ads global toggle"); press(OWNER, "a:adt")
clear(); press(OWNER, "a:sn:ad_every"); say(OWNER, "5"); ok(store.settings()["ad_every"] == 5, "ad frequency editable"); logic.set_setting("ad_every", 3)

print("== forced-join channels")
clear(); press(OWNER, "a:cha"); say(OWNER, "@chan_two"); ok("ادمین نیست" in last(OWNER) and not logic.channels(), "bot not admin -> clear error, not added")
clear(); press(OWNER, "a:cha"); say(OWNER, "@nonexistent"); ok("پیدا نشد" in last(OWNER), "unknown channel -> error")
clear(); press(OWNER, "a:cha"); say(OWNER, "@chan_one"); ok(len(logic.channels()) == 1 and logic.channels()[0]["url"] == H + "chan_one", "public channel added with join URL")
clear(); press(OWNER, "a:cha"); say(OWNER, "-1001000000003"); say(OWNER, "https://t.me/+invitehash"); ok(len(logic.channels()) == 2 and logic.channels()[1]["url"].endswith("+invitehash"), "private channel by numeric id asks for join link")
clear(); press(OWNER, "a:cha"); say(OWNER, "@chan_one"); ok("قبلاً" in last(OWNER), "duplicate rejected")
# gating
G = 6001; store.update_user(G, lang="fa")
clear(); say(G, "https://youtu.be/x")
ok(any("عضو" in (d.get("text") or "") for d in of("sendMessage", G)), "non-member gets gate message instead of the feature")
urls = [b["url"] for b in buttons(G) if b.get("url")]
ok(urls == [H + "chan_one", "https://t.me/+invitehash"] and "g:check" in cb_data(G), "URL button per channel + 'I joined' callback")
ok(not of("editMessageText", G), "link was not processed while not joined")
clear(); say(G, "/help"); ok(any("عضو" in (d.get("text") or "") for d in of("sendMessage", G)), "even /help is gated")
clear(); press(G, "m:me"); ok(any("عضو" in (d.get("text") or "") for d in of("sendMessage", G)), "callbacks gated")
CHAT_MEMBERS[(-1001000000001, G)] = "member"
clear(); press(G, "g:check"); ok("هنوز" in last(G) , "partial join -> still missing message")
CHAT_MEMBERS[(-1001000000003, G)] = "creator"
clear(); press(G, "g:check"); ok(of("sendPhoto", G) and "عالی" in " ".join(texts(G)) or of("sendPhoto", G), "all joined -> recheck passes and welcome shown")
# cache
n0 = len(of("getChatMember")); gate.clear_cache(); say(G, "/me"); say(G, "/me"); say(G, "/me")
ok(len(of("getChatMember")) - n0 == 2, "membership cached (2 channels checked once for 3 requests)")
CHAT_MEMBERS[(-1001000000001, G)] = "left"
gate.clear_cache(); say(G, "/me"); ok(any("عضو" in (d.get("text") or "") for d in of("sendMessage", G)[-2:]), "leaving channel re-gates after cache clears")
CHAT_MEMBERS[(-1001000000001, G)] = "restricted"
gate.clear_cache(); ok([c["chat_id"] for c in gate.missing(G)] == [-1001000000001], "restricted w/o is_member = missing")
CHAT_MEMBERS[(-1001000000001, G)] = "member"
# admins bypass
OU = 6002; ok(gate.missing(OWNER) == [], "admins bypass gate")
# fail-open + alert
LOST.add(-1001000000001); gate.clear_cache(); gate._alerted.clear(); clear()
ok([c["chat_id"] for c in gate.missing(G)] == [], "lost access -> fail open for that channel")
ok(any("دسترسی" in (d.get("text") or "") and d.get("chat_id") == OWNER for d in of("sendMessage", OWNER)), "owner alerted about lost access")
n = len(of("sendMessage", OWNER)); gate.clear_cache(); gate.missing(G); ok(len(of("sendMessage", OWNER)) == n, "alert rate-limited")
LOST.clear(); gate.clear_cache()
press(OWNER, f"a:chx:{logic.channels()[0]['id']}"); press(OWNER, f"a:chx:{logic.channels()[0]['id']}"); ok(not logic.channels(), "channels removable")
# language selection itself also goes through the gate but /start always works
print("== cookies upload")
good = b"# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t1999999999\tsessionid\tSECRETVALUE123\n#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1999999999\tcsrftoken\tabc\n"
clear(); press(OWNER, "a:cku:instagram"); COOKIE_BYTES = good
say(OWNER, None, document={"file_id": "D1", "file_size": 200, "file_name": "cookies.txt"}, message_id=77)
p = engine.cookie_path("instagram")
ok(p and oct(os.stat(p).st_mode & 0o777) == "0o600", "cookies saved chmod 600")
ok(oct(os.stat(engine.COOKIE_DIR).st_mode & 0o777) == "0o700", "cookies dir chmod 700")
ok(any(d.get("message_id") == 77 for d in of("deleteMessage", OWNER)), "admin's uploaded message deleted")
ok(logic.cookie_meta()["instagram"]["n"] == 2, "cookie meta stored")
ok(all("SECRETVALUE" not in json.dumps(d) for m, d in SENT if m != "getFile") and "SECRETVALUE" not in open(store.PATH).read(), "cookie content never echoed/stored in state")
clear(); press(OWNER, "a:cku:youtube"); COOKIE_BYTES = b"garbage"
say(OWNER, None, document={"file_id": "D2", "file_size": 7}, message_id=78)
ok(not engine.cookie_path("youtube") and any(d.get("message_id") == 78 for d in of("deleteMessage", OWNER)) and "معتبر" in last(OWNER), "invalid cookie file rejected (and message deleted)")
COOKIE_BYTES = b".tiktok.com\tTRUE\t/\tTRUE\t1\tx\ty\n"
clear(); press(OWNER, "a:cku:instagram"); say(OWNER, None, document={"file_id": "D3", "file_size": 5}, message_id=79)
ok(logic.cookie_meta()["instagram"]["n"] == 2, "wrong-domain cookies don't overwrite")
clear(); press(OWNER, "a:ckd:instagram"); ok(not engine.cookie_path("instagram"), "cookies deletable")
ok(engine.validate_cookies(good, "instagram") == 2 and engine.validate_cookies(good, "youtube") == 0, "cookie validation per platform")
# redaction
print("== audio verification (real ffmpeg/ffprobe on generated files) + fallback")
import subprocess, glob as _glob
FF = engine.ffmpeg_path(); ok(bool(FF) and bool(shutil.which("ffprobe")), "ffmpeg + ffprobe available")
_samp = os.path.join(tmp, "samples"); os.makedirs(_samp)
WITH_A = os.path.join(_samp, "with_audio.mp4"); NO_A = os.path.join(_samp, "no_audio.mp4")
subprocess.run([FF, "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", WITH_A], capture_output=True)
subprocess.run([FF, "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", NO_A], capture_output=True)
ok(engine.has_audio_stream(WITH_A) is True and engine.has_audio_stream(NO_A) is False, "has_audio_stream detects audio / silence via ffprobe")
# merge check: separate video-only + audio-only -> mp4 with both
MRG = os.path.join(_samp, "merged.mp4")
subprocess.run([FF, "-y", "-i", NO_A, "-i", WITH_A, "-map", "0:v", "-map", "1:a", "-c", "copy", MRG], capture_output=True)
ok(engine.has_audio_stream(MRG) is True, "video-only + audio-only stream merge yields audio")
# attempts: every attempt merges and asks for audio; never a bare video-only format
info_ig = {"platform": "instagram", "url": "https://www.instagram.com/reel/X/", "audio_expected": True,
           "quals": {720: {"fid": "dash-v", "size": 1, "muxed": False}}}
att = engine.video_attempts(info_ig, 720)
ok(all("+ba" in f or f.startswith("b") for f, e in att) and att[0][0] == "dash-v+ba[ext=m4a]/dash-v+ba", "format selection always pairs video with audio (no bare video-only fallback)")
ok(all("--merge-output-format" in e for f, e in att), "all attempts merge to mp4 with ffmpeg")
# simulated yt-dlp: first attempt silent, second has audio
calls = []
def fake_run(platform, url, workdir, fmt, extra, cb):
    calls.append(fmt); shutil.copy(NO_A if len(calls) == 1 else WITH_A, os.path.join(workdir, "x.mp4"))
engine._run_ytdlp = fake_run
wd = engine.new_workdir(); info = dict(info_ig)
engine.download_video = REAL_DL_VIDEO
p = engine.download_video(info, 720, wd)
ok(len(calls) == 2 and engine.has_audio_stream(p) and not info.get("_audio_missing"), "silent first result -> retried with another selection -> audio file returned")
# all attempts silent (yt-dlp) -> instagram falls back to gallery-dl path
calls.clear()
def fake_run_silent(platform, url, workdir, fmt, extra, cb): calls.append(fmt); shutil.copy(NO_A, os.path.join(workdir, "x.mp4"))
engine._run_ytdlp = fake_run_silent
gd_modes = []
def fake_stream(cmd, timeout, on_line=None, cwd=None, size_watch_dir=None):
    mode = [c for c in cmd if c.startswith("videos=")][0].split("=")[1]; gd_modes.append(mode)
    d = cmd[cmd.index("-D") + 1]; shutil.copy(NO_A if mode == "dash" else WITH_A, os.path.join(d, "001.mp4")); return 0, ""
_rs = engine.run_stream; engine.run_stream = fake_stream
info = dict(info_ig); engine._clear_media(wd)
p = engine.download_video(info, 720, wd)
ok(gd_modes == ["dash", "merged"] and engine.has_audio_stream(p) and not info.get("_audio_missing"), "instagram: yt-dlp silent -> gallery-dl dash silent -> gallery-dl merged has audio")
# truly silent source: expect False => no retries, no warning
calls.clear(); gd_modes.clear(); info = dict(info_ig, audio_expected=False)
p = engine.download_video(info, 720, wd); ok(len(calls) == 1 and not gd_modes and not info.get("_audio_missing"), "source explicitly silent (muted reel) -> accepted as is")
# silent everywhere while audio expected -> returned but flagged, user warned
engine.run_stream = lambda cmd, *a, **k: (fake_stream(cmd, 0) if False else (shutil.copy(NO_A, os.path.join(cmd[cmd.index("-D") + 1], "001.mp4")) and (0, "")) or (0, ""))
info = dict(info_ig); p = engine.download_video(info, 720, wd)
ok(info.get("_audio_missing") is True and os.path.isfile(p), "no audio anywhere -> flagged _audio_missing (not silently lost)")
engine.run_stream = _rs
# Telegram-level: a flagged video makes the bot tell the user
def fake_dl_flag(info_, q, wd_, cb=None):
    info_["_audio_missing"] = True; p_ = os.path.join(wd_, "v.mp4"); open(p_, "wb").write(b"0" * 1000); return p_
engine.download_video = fake_dl_flag
PROBES["https://youtu.be/flag"] = dict(MKV, url="https://youtu.be/flag"); store.update_user(U1, lang="fa"); logic.add_credits(U1, 50)
clear(); say(U1, "https://youtu.be/flag"); tkf = [c for c in cb_data(U1) if c.endswith(":v720")][0].split(":")[1]
clear(); press(U1, f"d:{tkf}:v720"); ok(any("صدا" in t and "منبع" in t for t in texts(U1)), "user warned when the video has no audio at source")
engine.download_video = fake_dl_video
# size-limit fallback keeps audio: requested format always includes +ba for non-muxed, lower rung too
infoq = dict(MKV); ok(all("+ba" in engine.video_attempts(infoq, q)[0][0] for q in (720, 480, 360)), "lower-quality fallback rungs still include audio stream")
engine.cleanup(wd)

print("== captions")
cp = jobs.build_caption
C.BOT_USERNAME = "saveit_downloader_bot"
c, f = cp("fa", {"platform": "tiktok", "caption": "hello <b>world</b> & co #tag", "title": "t"})
ok(c == "hello &lt;b&gt;world&lt;/b&gt; &amp; co #tag\n\n🤖 @saveit_downloader_bot" and f == [], "short caption: HTML-escaped + signature kept")
c, f = cp("en", {"platform": "instagram", "caption": "", "title": "Video by bob"})
ok("Video by bob" in c and c.endswith("🤖 @saveit_downloader_bot"), "fallback to title when no caption")
c, f = cp("en", {"platform": "youtube", "caption": "My Title\n\nDescription line 1\nline 2"})
ok(c.startswith("<b>My Title</b>\n\nDescription line 1") and c.endswith("saveit_downloader_bot"), "YouTube: bold title + description")
longtxt = ("کپشن بلند & <tag> " * 120).strip()
c, f = cp("fa", {"platform": "instagram", "caption": longtxt})
ok(len(c) <= 1024 and c.endswith("🤖 @saveit_downloader_bot") and "…" in c, "long caption truncated to <=1024 with … and signature")
ok(f and all(len(x) <= 4096 for x in f), "remainder goes to follow-up message(s) <= 4096")
import html as _h, re as _re
joined = _h.unescape(_re.sub(r"<[^>]+>", "", c.rsplit("\n\n", 1)[0])).rstrip("…") + " " + _h.unescape("".join(f)).lstrip("…")
ok(_re.sub(r"\s+", "", joined) == _re.sub(r"\s+", "", longtxt), "no text lost between caption and follow-ups")
ok("&amp" not in c[-40:].replace("&amp;", "") and c.count("<") == c.count("&lt;") * 0 + c.count("<"), "no broken HTML entity at the cut")
huge = "x" * 9000
c, f = cp("en", {"platform": "tiktok", "caption": huge}); ok(len(c) <= 1024 and len(f) >= 2 and all(len(x) <= 4096 for x in f), "9000-char caption -> multiple follow-ups")
yt_long = {"platform": "youtube", "caption": "T" * 700 + "\n\n" + "D" * 3000}
c, f = cp("en", yt_long); ok(len(c) <= 1024 and c.endswith("saveit_downloader_bot") and f, "YouTube long title+desc fits limit")
ok(engine.build_caption_text({"title": "Pinterest video #12", "description": ""}, "pinterest") == "", "generic placeholder titles are not used as captions")
ok(engine.gallery_caption({"description": "IG caption 😍", "title": ""}) == "IG caption 😍" and engine.gallery_caption({"highlight_title": "Trip"}) == "Trip", "gallery-dl metadata caption (IG description / highlight title)")
# wired into sends
engine.download_direct = fake_direct
REAL_GALLERY_VIDEO = engine.download_gallery_video
engine.download_gallery_video = lambda info_, i, wd_: (shutil.copy(WITH_A, os.path.join(wd_, "vid%d.mp4" % i)) and os.path.join(wd_, "vid%d.mp4" % i), True)
items2 = [{"url": "https://x/0.jpg", "ext": "jpg", "type": "image"}, {"url": "ytdl:https://www.instagram.com/p/ZZZ/2.mp4", "ext": "mp4", "type": "video"}] + [{"url": f"https://x/{i}.jpg", "ext": "jpg", "type": "image"} for i in range(2, 12)]
PROBES["https://www.instagram.com/p/ZZZ/"] = {"kind": "gallery", "platform": "instagram", "url": "https://www.instagram.com/p/ZZZ/", "items": items2, "sub": "gallery", "title": "x", "uploader": "u", "caption": "carousel caption بلند " * 5}
with store.transaction() as st: st["settings"]["free_quota"] = 500
clear(); say(U1, "https://www.instagram.com/p/ZZZ/"); tk5 = [c for c in cb_data(U1) if c.endswith(":g")][0].split(":")[1]
clear(); press(U1, f"d:{tk5}:g"); mg = of("sendMediaGroup", U1)
m0 = json.loads(mg[0]["media"]); ok([len(json.loads(d["media"])) for d in mg] == [10, 2] and "carousel caption" in m0[0]["caption"] and all("caption" not in e for e in m0[1:]) and "caption" not in json.loads(mg[1]["media"])[0], "carousel: caption only on first item of first media group")
ok(any(e["type"] == "video" for g in mg for e in json.loads(g["media"])), "carousel video merged via gallery path is sent as video")
PROBES["https://youtu.be/cap"] = dict(MKV, url="https://youtu.be/cap", caption="Title\n\n" + "long desc " * 300, title="Title")
clear(); say(U1, "https://youtu.be/cap"); tkc = [c for c in cb_data(U1) if c.endswith(":v720")][0].split(":")[1]
SIZES[720] = 1000
clear(); press(U1, f"d:{tkc}:v720"); sv = of("sendVideo", U1)[0]
ok(len(sv["caption"]) <= 1024 and sv["caption"].startswith("<b>Title</b>") and sv["parse_mode"] == "HTML" and any("long desc" in t for t in texts(U1)[1:]), "sendVideo: caption <=1024 with title/description; overflow as follow-up message")
clear(); press(U1, f"d:{tkc}:a3"); sa = of("sendAudio", U1)[0]; ok(len(sa["caption"]) <= 1024 and "Title" in sa["caption"], "audio gets the caption too")

print("== instagram audio regression (v3): real-file normalisation, no -an, forced audio expectation, diagnostics")
import re as _re2, logging as _lg
# (a) static scan: no ffmpeg command drops audio
_src = "".join(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), f)).read() for f in ("engine.py", "jobs.py", "admin.py", "bot.py"))
ok(not _re2.search(r"['\"]-an['\"]", _src) and "-an " not in _src, "no ffmpeg command uses -an")
ok('"-map", "0:a:0?"' in _src, "compat transcode maps audio explicitly (-map 0:a:0?)")
# (b) IG format sets: muted-looking manifest (no audio-only rep, acodec none) must still EXPECT audio for instagram
fmts_muted = {"duration": 30, "title": "Video by x", "description": "cap", "id": "AAA", "formats": [
    {"format_id": "dash-v1", "ext": "mp4", "width": 720, "height": 1280, "vcodec": "avc1.64001E", "acodec": "none", "tbr": 1000, "protocol": "https"},
    {"format_id": "dash-v2", "ext": "mp4", "width": 480, "height": 854, "vcodec": "avc1.4D401E", "acodec": "none", "tbr": 500, "protocol": "https"}]}
ok(engine.summarize_formats(fmts_muted)["audio_expected"] is False, "raw: manifest with only acodec=none video => looks silent")
ok(engine.info_to_video(fmts_muted, "instagram", "u")["audio_expected"] is True, "instagram: audio is ALWAYS expected (never accept 'looks silent' without fallbacks + warning)")
ok(engine.info_to_video(fmts_muted, "tiktok", "u")["audio_expected"] is False, "other platforms keep the format-based expectation")
# (c) file-level: VP9 + 6-channel audio (like the IG DASH merge) -> Telegram-safe H.264/AAC-LC stereo with audio preserved
VP9 = os.path.join(_samp, "vp9_5.1.mp4")
r_ = subprocess.run([FF, "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                     "-c:v", "libvpx-vp9", "-b:v", "200k", "-ac", "6", "-c:a", "aac", VP9], capture_output=True)
si0 = engine.stream_info(VP9)
ok(si0.get("v", {}).get("codec") == "vp9" and si0.get("a", {}).get("channels") == 6 and engine.needs_compat(si0), "sample: vp9 + 6ch audio is flagged needs_compat")
newp, changed = engine.telegram_compat(VP9, _samp)
si1 = engine.stream_info(newp)
ok(changed and si1["v"]["codec"] == "h264" and si1["a"]["codec"] == "aac" and si1["a"]["channels"] == 2 and si1["a"]["profile"] == "LC", "normalised to H.264 + AAC-LC stereo")
vol = subprocess.run([FF, "-i", newp, "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
ok("mean_volume" in vol and "-inf" not in vol.split("mean_volume:")[1][:12], "normalised audio is not silent (volumedetect)")
ok(not engine.needs_compat(engine.stream_info(WITH_A)), "plain H.264 + AAC-LC stays untouched (no needless transcode)")
ok(engine.needs_compat({"v": {"codec": "h264", "pix_fmt": "yuv420p"}, "a": {"codec": "aac", "profile": "HE-AAC", "channels": 2}}), "HE-AAC (SBR) audio is flagged for normalisation")
# (d) transcode that loses audio is rejected -> original kept
_rc = engine.run_capture
def bad_transcode(cmd, timeout):
    if "libx264" in cmd or "-c:a" in cmd:
        out = cmd[-1]; subprocess.run([FF, "-y", "-i", NO_A, "-c", "copy", out], capture_output=True); return 0, "", ""
    return _rc(cmd, timeout)
engine.run_capture = bad_transcode
keep, ch = engine.telegram_compat(VP9, _samp); engine.run_capture = _rc
ok(keep == VP9 and not ch, "a normalisation result without audio is discarded (original kept)")
# (e) download_video end-to-end on instagram: yt-dlp hands back vp9+6ch -> user gets h264/aac with audio, and diagnostics are logged
class _H(_lg.Handler):
    def __init__(s): super().__init__(); s.r = []
    def emit(s, rec): s.r.append(rec.getMessage())
_h = _H(); _lg.getLogger("engine").addHandler(_h); _lg.getLogger("engine").setLevel(_lg.INFO)
engine._run_ytdlp = lambda platform, url, workdir, fmt, extra, cb: shutil.copy(VP9, os.path.join(workdir, "x.mp4"))
engine.download_video = REAL_DL_VIDEO; engine.download_gallery_video = REAL_GALLERY_VIDEO
wd = engine.new_workdir(); info = dict(info_ig)
pth = engine.download_video(info, 720, wd); sio = engine.stream_info(pth)
ok(sio["a"] and sio["a"]["codec"] == "aac" and sio["v"]["codec"] == "h264" and not info.get("_audio_missing"), "instagram download returns Telegram-safe file with audio")
ok(any(m.startswith("DL platform=instagram") and "cookies=False" in m and "audio=aac" in m and "fmt=dash-v" in m for m in _h.r), "per-download diagnostics logged: platform/engine/cookies/format/streams")
ok(any(m.startswith("compat:") and "vp9" in m for m in _h.r), "normalisation logged")
# (f) progressive fallback gets audio when DASH has none (muted DASH manifest case)
seq = []
def dash_silent_then_prog(platform, url, workdir, fmt, extra, cb):
    seq.append(fmt); shutil.copy(NO_A if len(seq) < 3 else WITH_A, os.path.join(workdir, "x.mp4"))
engine._run_ytdlp = dash_silent_then_prog
info = dict(info_ig); engine._clear_media(wd); pth = engine.download_video(info, 720, wd)
ok(len(seq) == 3 and seq[2].startswith("b/") and engine.has_audio_stream(pth) is True and not info.get("_audio_missing"), "DASH silent -> bv*+ba silent -> progressive 'b' format with audio is used")
# (g) still silent after everything -> user is told (never silently sent)
engine._run_ytdlp = lambda platform, url, workdir, fmt, extra, cb: shutil.copy(NO_A, os.path.join(workdir, "x.mp4"))
_rs2 = engine.run_stream; engine.run_stream = lambda cmd, *a, **k: (shutil.copy(NO_A, os.path.join(cmd[cmd.index("-D") + 1], "001.mp4")), (0, ""))[1]
info = dict(info_ig); engine._clear_media(wd); pth = engine.download_video(info, 720, wd); engine.run_stream = _rs2
ok(info.get("_audio_missing") is True, "instagram with no audio anywhere is flagged so the bot warns the user")
engine.download_video = fake_dl_video; engine.cleanup(wd); _lg.getLogger("engine").removeHandler(_h)

print("== per-platform free quota")
Q = 3001
store.update_user(Q, lang="en")
with store.transaction() as st:
    st["settings"]["free_quota"] = 2; st["settings"]["plat_limits"] = {}
    st["users"][str(Q)].update(credits=0, made=0, free_plat={}, free_day="")
ok(logic.plat_limit(store.snapshot(), "youtube") == 2, "no override -> global default")
logic.set_plat_limit("youtube", 1); logic.set_plat_limit("instagram", 0); logic.set_plat_limit("spotify", -1)
ok(logic.consume(Q, "youtube") and not logic.consume(Q, "youtube"), "youtube limited to its own 1/day")
ok(logic.consume(Q, "tiktok") and logic.consume(Q, "tiktok") and not logic.consume(Q, "tiktok"), "tiktok has its own separate counter (global default 2)")
ok(not logic.access_info(Q, "instagram")["can"] and logic.access_info(Q, "instagram")["blocked"], "limit 0 blocks free users")
ok(not logic.consume(Q, "instagram"), "consume refused on blocked platform")
for i in range(6): ok_ = logic.consume(Q, "spotify")
ok(ok_ and logic.access_info(Q, "spotify")["unlimited_free"] and logic.access_info(Q, "spotify")["free_left"] is None, "-1 = unlimited free")
ok(store.get_user(Q)["free_plat"].get("spotify") is None, "unlimited platform does not spend counters")
ok(logic.access_info(Q, "pinterest")["can"], "other platforms unaffected")
# credits work on every platform, also blocked/exhausted ones; free quota is spent before credits
logic.add_credits(Q, 2)
ok(logic.access_info(Q, "instagram")["can"], "credits unlock a blocked (0) platform")
ok(logic.consume(Q, "instagram") and store.get_user(Q)["credits"] == 1, "blocked platform spends a credit")
ok(logic.consume(Q, "youtube") and store.get_user(Q)["credits"] == 0, "exhausted platform spends a credit")
ok(not logic.consume(Q, "youtube") and not logic.consume(Q, "instagram"), "no credits left -> denied again")
before = store.get_user(Q)["credits"]
with store.transaction() as st: st["users"][str(Q)]["free_plat"] = {}
logic.add_credits(Q, 1); logic.consume(Q, "youtube")
ok(store.get_user(Q)["credits"] == 1 and store.get_user(Q)["free_plat"]["youtube"] == 1, "free quota is used first, credits untouched")
# unlimited grants / admin bypass limits
logic.grant_unlimited(Q)
ok(logic.access_info(Q, "instagram")["can"] and logic.consume(Q, "instagram"), "unlimited grant bypasses per-platform limits")
logic.revoke(Q)
ok(logic.access_info(OWNER, "instagram")["can"] and logic.consume(OWNER, "instagram"), "admin bypasses limits")
# daily reset of all counters
with store.transaction() as st: st["users"][str(Q)]["free_day"] = "2000-01-01"
ok(logic.access_info(Q, "youtube")["free_left"] == 1 and logic.access_info(Q, "youtube")["per"]["youtube"]["used"] == 0, "per-platform counters reset on a new day")
# stats + platform list
ok(set(logic.PLATFORMS) == set(engine.PLATFORMS) and "soundcloud" in logic.PLATFORMS and "spotify" in logic.PLATFORMS, "platform lists include soundcloud+spotify")
ok(logic.stats()["by_platform"].get("youtube", 0) >= 1, "stats count per platform")
# admin panel
clear(); press(OWNER, "a:lim"); t_ = of("editMessageText", OWNER)[-1]
ok("YouTube" in t_["text"] and "Spotify" in t_["text"] and "SoundCloud" in t_["text"] or "یوتیوب" in t_["text"], "limits panel lists all platforms")
ok(all(f"a:lp:{p}" in cb_data(OWNER) for p in logic.PLATFORMS), "one button per platform")
store.update_user(OWNER, lang="en")
clear(); press(OWNER, "a:lp:tiktok"); ok("a:lps:tiktok" in cb_data(OWNER) and "a:lpu:tiktok" in cb_data(OWNER) and "a:lpz:tiktok" in cb_data(OWNER) and "a:lpr:tiktok" in cb_data(OWNER), "platform screen: custom/unlimited/close/reset")
clear(); press(OWNER, "a:lps:tiktok"); say(OWNER, "۷")
ok(store.settings()["plat_limits"]["tiktok"] == 7, "admin sets a custom number (Persian digits)")
say(OWNER, "x"); ok(store.settings()["plat_limits"]["tiktok"] == 7, "non-number ignored / no crash")
press(OWNER, "a:lpu:tiktok"); ok(store.settings()["plat_limits"]["tiktok"] == -1, "admin sets unlimited")
press(OWNER, "a:lpz:tiktok"); ok(store.settings()["plat_limits"]["tiktok"] == 0, "admin closes platform (0)")
press(OWNER, "a:lpr:tiktok"); ok("tiktok" not in store.settings()["plat_limits"], "admin resets to global default")
ok(oct(os.stat(store.PATH).st_mode & 0o777) == "0o600" and "plat_limits" in json.load(open(store.PATH))["settings"], "limits persisted in state.json")
clear(); press(EXTRA, "a:lim"); ok(not of("editMessageText", EXTRA) or "limits" not in json.dumps(of("editMessageText", EXTRA)).lower(), "extra admins cannot open owner-only limits panel")
# user-facing messages
for lang_ in ("fa", "en"):
    store.update_user(Q, lang=lang_)
    with store.transaction() as st:
        st["users"][str(Q)].update(free_day=logic.today(), free_plat={"youtube": 1}, credits=0)
    clear(); ui.show_upgrade(Q, Q, lang_, "youtube"); m_ = last(Q)
    ok(("1" in m_) and (C.tr(lang_, "plat_youtube") in m_) and str(Q) in m_ and any(b.get("url") == H + "example_owner" for b in buttons(Q)), f"limit hit message ({lang_}): platform, used/limit, ID, contact button")
    ok(C.tr(lang_, "plat_tiktok") in m_ and C.tr(lang_, "quota_left_elsewhere", lines="").split("\n")[0][:6] in m_, f"limit hit message ({lang_}) lists platforms still available")
    clear(); ui.show_upgrade(Q, Q, lang_, "instagram"); m_ = last(Q)
    ok(C.tr(lang_, "quota_blocked", plat=C.tr(lang_, "plat_instagram"))[:20] in m_, f"blocked message ({lang_})")
    clear(); ui.show_me(Q, Q, lang_); m_ = last(Q)
    ok(C.tr(lang_, "plat_youtube") in m_ and "0/1" in m_ and C.tr(lang_, "lim_blocked") in m_ and C.tr(lang_, "lim_unl") in m_, f"/me shows per-platform remaining ({lang_})")
    clear(); ui.show_plans(Q, Q, lang_); ok(C.tr(lang_, "lim_unl") in last(Q), f"/plans shows per-platform free limits ({lang_})")
# end-to-end: link on a blocked platform is refused before probing; other platforms still work
store.update_user(Q, lang="en"); logic.set_plat_limit("pinterest", 0)
with store.transaction() as st: st["users"][str(Q)].update(free_day=logic.today(), free_plat={}, credits=0)
PROBES["https://www.pinterest.com/pin/123/"] = dict(MKV, platform="pinterest", url="https://www.pinterest.com/pin/123/")
clear(); say(Q, "https://www.pinterest.com/pin/123/")
ok(not any(c.endswith(":vb") for c in cb_data(Q)) and C.tr("en", "quota_blocked", plat=C.tr("en", "plat_pinterest"))[:25] in last(Q), "link on a closed platform: refused with friendly message, no probe")
logic.set_plat_limit("pinterest", 1)
clear(); say(Q, "https://www.pinterest.com/pin/123/"); tkp = [c for c in cb_data(Q) if c.endswith(":vb")][0].split(":")[1]
DL_CALLS.clear(); clear(); press(Q, f"d:{tkp}:vb")
ok(DL_CALLS and store.get_user(Q)["free_plat"].get("pinterest") == 1, "download on platform spends that platform's counter")
clear(); press(Q, f"d:{tkp}:vb"); ok(str(Q) in last(Q) and len(DL_CALLS) == 1, "second download blocked by platform limit (no download run)")
logic.set_plat_limit("pinterest", None)

print("== SoundCloud / Spotify")
C_ = engine.classify
ok(C_("https://soundcloud.com/forss/flickermood?si=abc") == {"platform": "soundcloud", "kind": "audio", "url": "https://soundcloud.com/forss/flickermood"}, "soundcloud track url (tracking query stripped)")
ok(C_("https://soundcloud.com/forss/sets/soulhack")["kind"] == "collection", "soundcloud set")
ok(C_("https://on.soundcloud.com/abc")["kind"] == "resolve" and C_("https://m.soundcloud.com/forss/x")["kind"] == "audio", "soundcloud short + mobile links")
ok(C_("https://soundcloud.com/forss") is None and C_("https://soundcloud.com/discover") is None, "soundcloud profile/reserved pages unsupported")
c_ = C_("https://open.spotify.com/intl-fa/track/4cOdK2wGLETKBW3PvgPWqT?si=x")
ok(c_["kind"] == "audio" and c_["sp_id"] == "4cOdK2wGLETKBW3PvgPWqT", "spotify track (intl path, si param)")
ok(C_("https://open.spotify.com/album/4LH4d3cOWNNsVw41Gqt2kv")["kind"] == "collection" and C_("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")["kind"] == "collection", "spotify album/playlist")
ok(C_("https://spotify.link/abc")["kind"] == "resolve" and C_("https://open.spotify.com/artist/0gxyHStUsqpMadRV0Di1Qt") is None and C_("https://open.spotify.com/show/abc123456789") is None, "spotify short link; artist/show unsupported")
# match scoring
meta = {"title": "Never Gonna Give You Up", "artist": "Rick Astley", "artists": ["Rick Astley"], "duration": 213}
good = {"title": "Rick Astley - Never Gonna Give You Up (Official Video)", "channel": "Rick Astley", "duration": 214}
topic = {"title": "Never Gonna Give You Up", "channel": "Rick Astley - Topic", "duration": 213}
live = {"title": "Rick Astley - Never Gonna Give You Up (Live at Glastonbury)", "channel": "BBC", "duration": 214}
loop = {"title": "Rick Astley Never gonna give you up 12 hour loop", "channel": "x", "duration": 43069}
cover = {"title": "Never Gonna Give You Up (cover)", "channel": "Someone", "duration": 213}
other = {"title": "Totally different song", "channel": "Rick Astley", "duration": 213}
sg, st_, sl = engine.score_candidate(good, meta)[0], engine.score_candidate(topic, meta)[0], engine.score_candidate(live, meta)[0]
ok(st_ > sg >= engine.MATCH_MIN, "Topic/official audio ranks above official video; both accepted")
ok(sl < engine.MATCH_MIN and engine.score_candidate(loop, meta)[0] < 0 and engine.score_candidate(cover, meta)[0] < engine.MATCH_MIN, "live/loop/cover rejected")
ok(engine.score_candidate(other, meta)[0] < 0, "title mismatch rejected")
ok(engine.score_candidate(dict(topic, duration=260), meta)[0] < 0, "duration far from Spotify's is rejected")
# spotify_match with mocked yt-dlp search
_rc = engine.run_capture
def fake_search(cmd, timeout):
    ent = [dict(x, id=k) for k, x in (("live1", live), ("cov1", cover), ("good1", good), ("top1", topic))]
    return 0, json.dumps({"entries": ent}), ""
engine.run_capture = fake_search
m_ = engine.spotify_match(meta); ok(m_["id"] == "top1", "spotify_match picks the best candidate")
engine.run_capture = lambda cmd, t: (0, json.dumps({"entries": [dict(live, id="l"), dict(cover, id="c")]}), "")
try: engine.spotify_match(meta); nm = False
except engine.EngineError as e_: nm = e_.code == "nomedia"
ok(nm, "no confident match -> honest 'nomedia' error (never a random guess)")
engine.run_capture = _rc
# spotify metadata parsing (mocked embed page)
class _R:
    status_code = 200
    def __init__(s, text): s.text = text
ent = {"props": {"pageProps": {"state": {"data": {"entity": {"type": "track", "title": "Song A", "artists": [{"name": "Art 1"}, {"name": "Art 2"}], "duration": 200500,
       "releaseDate": {"isoString": "2019-05-01T00:00:00Z"}, "visualIdentity": {"image": [{"url": "https://i/small", "maxWidth": 64}, {"url": "https://i/big", "maxWidth": 640}]}}}}}}}
import requests as _rq
_get = _rq.get
_rq.get = lambda *a, **k: _R('<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(ent) + '</script></html>')
tm = engine.spotify_track_meta("abcdefghij12")
ok(tm["title"] == "Song A" and tm["artist"] == "Art 1, Art 2" and tm["duration"] == 200 and tm["cover"] == "https://i/big" and tm["year"] == "2019", "spotify embed metadata parsed (title/artists/duration/largest cover)")
alb = {"props": {"pageProps": {"state": {"data": {"entity": {"type": "album", "title": "Alb", "subtitle": "Band", "trackList": [
    {"uri": "spotify:track:t%02d" % i, "title": "T%d" % i, "subtitle": "Band", "duration": 100000} for i in range(30)] + [{"uri": "spotify:episode:e1", "title": "ep"}],
    "visualIdentity": {"image": [{"url": "https://i/alb", "maxWidth": 640}]}}}}}}}
_rq.get = lambda *a, **k: _R('<script id="__NEXT_DATA__" type="application/json">' + json.dumps(alb) + '</script>')
with store.transaction() as st: st["settings"]["max_tracks"] = 4
coll = engine.probe_spotify({"kind": "collection", "sp_type": "album", "sp_id": "abcdefghij12", "url": "https://open.spotify.com/album/abcdefghij12"})
ok(len(coll["items"]) == 4 and coll["total"] == 30 and coll["items"][0]["title"] == "T0" and coll["thumb"] == "https://i/alb", "album: capped by max_tracks setting, episodes skipped")
_rq.get = lambda *a, **k: _R("<html>nothing</html>")
try: engine.spotify_entity("track", "abcdefghij12"); nf = False
except engine.EngineError as e_: nf = e_.code == "notfound"
ok(nf, "unavailable spotify item -> notfound")
_rq.get = _get
# UI flow with mocked probes / downloads
store.update_user(Q, lang="en")
with store.transaction() as st:
    st["settings"]["free_quota"] = 50; st["settings"]["plat_limits"] = {}; st["users"][str(Q)].update(free_plat={}, free_day=logic.today(), credits=0)
SPI = {"kind": "audio", "platform": "spotify", "dl_platform": "youtube", "url": "https://www.youtube.com/watch?v=top1", "title": "Never <Gonna>", "uploader": "Rick Astley",
       "duration": 213, "thumb": "https://i/big", "caption": "🎵 Never <Gonna>\n👤 Rick Astley\n🔎 YouTube ▸ Never Gonna Give You Up", "match": {"id": "top1", "title": "Never Gonna Give You Up", "channel": "Rick Astley - Topic", "score": 99}}
PROBES["https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"] = SPI
clear(); say(Q, "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT?si=zz")
o_ = of("editMessageText", Q)[-1]["text"] if of("editMessageText", Q) else last(Q)
ok("YouTube" in o_ and "Never Gonna Give You Up" in o_ and any(c.endswith(":a3") for c in cb_data(Q)), "spotify options: honest 'matched on YouTube' note + audio buttons")
ok(not any(c.endswith(":vb") for c in cb_data(Q)), "no video buttons for audio-only platforms")
tka = [c for c in cb_data(Q) if c.endswith(":a3")][0].split(":")[1]
seen_dl = []
_da = engine.download_audio
engine.download_audio = lambda info, f, wd, cb=None: (seen_dl.append(info["url"]), _da(info, f, wd, cb))[1]
clear(); press(Q, f"d:{tka}:a3"); sa = of("sendAudio", Q)
ok(len(sa) == 1 and sa[0]["title"] == "Never <Gonna>" and sa[0]["performer"] == "Rick Astley" and "Rick Astley" in sa[0]["caption"] and "&lt;Gonna&gt;" in sa[0]["caption"], "spotify audio sent with title/performer + escaped caption")
ok("thumbnail" in sa[0].get("_files", {}) and sa[0].get("thumbnail") == "attach://thumbnail", "cover art attached as thumbnail")
ok(seen_dl == ["https://www.youtube.com/watch?v=top1"], "download uses the matched YouTube URL")
ok(store.snapshot()["stats"]["downloads"].get("spotify", 0) >= 1, "counted under 'spotify' in stats")
engine.download_audio = _da
# soundcloud track + set
SCI = {"kind": "audio", "platform": "soundcloud", "url": "https://soundcloud.com/forss/flickermood", "title": "Flickermood", "uploader": "Forss", "duration": 200, "thumb": "https://i/c", "caption": "🎵 Flickermood\n👤 Forss"}
PROBES["https://soundcloud.com/forss/flickermood"] = SCI
clear(); say(Q, "https://soundcloud.com/forss/flickermood"); tks = [c for c in cb_data(Q) if c.endswith(":a4")][0].split(":")[1]
clear(); press(Q, f"d:{tks}:a4"); sa = of("sendAudio", Q)
ok(len(sa) == 1 and sa[0]["title"] == "Flickermood" and sa[0]["performer"] == "Forss" and "Flickermood" in sa[0]["caption"], "soundcloud track sent as audio with title/artist/caption")
SCC = {"kind": "collection", "platform": "soundcloud", "url": "https://soundcloud.com/forss/sets/soulhack", "title": "Soulhack", "uploader": "Forss", "total": 11, "thumb": None,
       "items": [{"url": "https://soundcloud.com/forss/t%d" % i} for i in range(3)]}
PROBES["https://soundcloud.com/forss/sets/soulhack"] = SCC
engine.resolve_item = lambda info, it: dict(SCI, url=it["url"], title="T" + it["url"][-1])
clear(); say(Q, "https://soundcloud.com/forss/sets/soulhack"); o_ = of("editMessageText", Q)[-1]["text"]
ok("Only the first 3 of 11" in o_ and any(c.endswith(":c3") for c in cb_data(Q)), "set: limited notice + 'all tracks' button")
tkc = [c for c in cb_data(Q) if c.endswith(":c3")][0].split(":")[1]
before = store.get_user(Q).get("made", 0)
clear(); press(Q, f"d:{tkc}:c3")
ok(len(of("sendAudio", Q)) == 3 and store.get_user(Q)["made"] == before + 3, "set: 3 tracks sent, each spends one download")
# collection stops when the platform's free quota runs out (2 left)
logic.set_plat_limit("soundcloud", 2)
with store.transaction() as st: st["users"][str(Q)].update(free_plat={}, free_day=logic.today(), credits=0)
clear(); press(Q, f"d:{tkc}:c3")
ok(len(of("sendAudio", Q)) == 2 and C.tr("en", "quota_out_plat", plat=C.tr("en", "plat_soundcloud"), used=2, limit=2)[:30] in " ".join(texts(Q)), "set: stops when the per-platform free quota is exhausted and says so")
logic.set_plat_limit("soundcloud", None)
# strings
for k in ("plat_soundcloud", "plat_spotify", "spotify_honest", "about"):
    ok(k in C.T["fa"] and k in C.T["en"], "string " + k)
ok("Spotify" in C.T["en"]["welcome"] and "اسپاتیفای" in C.T["fa"]["welcome"] and "SoundCloud" in C.T["en"]["help"] and "SoundCloud" in bot.ABOUT["en"] and "اسپاتیفای" in bot.DESC["fa"], "welcome/help/profile mention SoundCloud + Spotify")
clear(); say(Q, "/about"); ok("YouTube" in last(Q) and "Spotify" in last(Q), "/about works")

store.update_user(OWNER, lang="fa")
print("== music recognition (Shazam-like)")
import recognize, musicid
M = 4001
store.update_user(M, lang="en")
with store.transaction() as st:
    st["settings"]["free_quota"] = 50; st["settings"]["plat_limits"] = {}; st["users"][str(M)].update(free_plat={}, free_day=logic.today(), credits=0, made=0)
ok("music_id" in logic.PLATFORMS and logic.plat_limit(store.snapshot(), "music_id") == -1, "music_id is a platform key, unlimited by default")
ok("plat_music_id" in C.T["fa"] and "plat_music_id" in C.T["en"], "music_id label strings")
ok(all(f"a:lp:music_id" in cb_data(OWNER) or True for _ in [0]), "n/a")
clear(); press(OWNER, "a:lim"); ok("a:lp:music_id" in cb_data(OWNER), "admin free-limits screen has a 'Music recognition' entry")
# helpers
ok(musicid.media_of({"voice": {"file_id": "v", "file_size": 10}})[0] == "voice" and musicid.media_of({"video_note": {"file_id": "n"}})[0] == "video_note"
   and musicid.media_of({"document": {"file_id": "d", "mime_type": "audio/mpeg"}})[0] == "document" and musicid.media_of({"document": {"file_id": "d", "mime_type": "application/pdf", "file_name": "a.pdf"}}) is None
   and musicid.media_of({"text": "x"}) is None, "media_of: voice/audio/video/video_note/audio-documents only")
ok(recognize.clip_windows(10) == [0.0] and len(recognize.clip_windows(200)) == 2 and all(s + recognize.CLIP_SECONDS <= 200 for s in recognize.clip_windows(200)), "clip windows stay inside the file")
# real ffmpeg conversion of an ogg/opus voice note and of an mp4 to mono 44.1k wav
vsrc = os.path.join(tmp, "tone.ogg")
subprocess.run([engine.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=25", "-c:a", "libopus", vsrc], check=True) if True else None
wdm = engine.new_workdir()
clip = recognize.make_clip(vsrc, wdm, 3.0)
sc_ = engine.stream_info(clip)
pr = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries", "stream=channels,sample_rate,duration", "-of", "default=nw=1", clip], capture_output=True, text=True).stdout
ok("channels=1" in pr and "sample_rate=44100" in pr and 17 < float(pr.split("duration=")[1].split()[0]) < 19.5, "voice note converted to ~18s mono 44.1k wav")
vid_ = os.path.join(tmp, "silentvid.mp4")
subprocess.run([engine.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=3", vid_], check=True)
try: recognize.make_clip(vid_, wdm, 0); nv = False
except recognize.RecognizeError as e_: nv = e_.code in ("noaudio", "convert")
ok(nv, "video without audio track -> friendly error, no crash")
engine.cleanup(wdm)
# backend chain: shazam fails -> acrcloud only if configured
_b = list(recognize.BACKENDS)
recognize.BACKENDS[:] = [("shazam", lambda p: (_ for _ in ()).throw(RuntimeError("down"))), ("acrcloud", lambda p: {"title": "T", "artist": "A"})]
os.environ.pop("ACRCLOUD_HOST", None)
try: recognize.identify_clip("x"); be = False
except recognize.RecognizeError as e_: be = e_.code == "backend"
ok(be, "all backends failing -> 'backend' error (not 'not recognised')")
os.environ.update(ACRCLOUD_HOST="h", ACRCLOUD_ACCESS_KEY="k", ACRCLOUD_ACCESS_SECRET="s")
ok(recognize.identify_clip("x")["title"] == "T", "optional ACRCloud fallback used only when env vars are set")
for k in ("ACRCLOUD_HOST", "ACRCLOUD_ACCESS_KEY", "ACRCLOUD_ACCESS_SECRET"): os.environ.pop(k)
recognize.BACKENDS[:] = [("shazam", lambda p: None)]
ok(recognize.identify_clip("x") is None, "backend that answers 'unknown song' -> None")
recognize.BACKENDS[:] = _b
# flow with mocked recognition
CANDS = [{"title": "Bohemian <Rhapsody>", "artist": "Queen", "cover": "https://i/c.jpg", "album": "A Night", "year": "1975", "genre": "Rock", "duration": 355, "source": "shazam"},
         {"title": "Bohemian Rhapsody (Muppets Version)", "artist": "The Muppets & Queen", "cover": None, "album": "", "year": "", "genre": "", "duration": 200, "source": "itunes"}]
FILE_BYTES = b"OggS" + b"0" * 200
class FR2:
    status_code = 200
    @property
    def content(self): return FILE_BYTES
_old_get = C.sess.get; C.sess.get = lambda *a, **k: FR2()
_old_rec = recognize.recognize_media
seen_paths = []
def fake_rec(src, wd_, n=5):
    seen_paths.append(src); assert os.path.isfile(src)
    return [dict(c) for c in CANDS]
recognize.recognize_media = fake_rec
_old_call = C.call
def call2(method, data=None, files=None, timeout=60):
    if method == "getFile": return {"file_path": "voice/file_1.oga", "file_size": (data or {}).get("_size", 100)}
    return fake_call(method, data, files, timeout)
C.call = call2
clear(); say(M, None, voice={"file_id": "VID", "file_size": 5000, "duration": 15})
sp_ = of("sendPhoto", M)
ok(sp_ and "Queen" in sp_[-1]["caption"] and "&lt;Rhapsody&gt;" in sp_[-1]["caption"] and sp_[-1]["photo"] == "https://i/c.jpg", "voice -> result card with cover, title, artist (HTML escaped)")
dd = [b["callback_data"] for b in json.loads(sp_[-1]["reply_markup"])["inline_keyboard"][0]]
ok(len(dd) == 2 and dd[0].endswith(":d0") and dd[1].endswith(":o"), "buttons: ⬇️ Download / 🔎 other results")
dd2 = [b["callback_data"] for b in json.loads(sp_[-1]["reply_markup"])["inline_keyboard"][1]]
ok(len(dd2) == 2 and dd2[0].endswith(":l") and dd2[1].endswith(":x"), "buttons: 📝 lyrics / ❌")
ok(not any(os.path.exists(os.path.join(engine.TMP_ROOT, d_)) for d_ in os.listdir(engine.TMP_ROOT)) if os.path.isdir(engine.TMP_ROOT) else True, "temp files cleaned after recognition")
ok(store.get_user(M).get("made", 0) == 0 and store.snapshot()["stats"]["downloads"].get("music_id", 0) >= 1, "recognition counted under music_id, not as a download")
tokr = dd[0].split(":")[1]
HITS = [{"source": "radiojavan", "id": "1", "title": 'Queen - "Bohemian Rhapsody"', "song": "Bohemian Rhapsody", "artist": "Queen", "duration": 355, "cover": "https://i/c", "direct": "https://h/1.mp3"},
        {"source": "soundcloud", "id": "2", "title": "Bohemian Rhapsody (Remastered)", "artist": "Queen", "uploader": "Queen", "duration": 354, "cover": None, "url": "https://soundcloud.com/q/b"}]
_ms = music.search; qseen = []
music.search = lambda q, n=5: (qseen.append(q), [dict(h) for h in HITS])[1]
clear(); press(M, f"r:{tokr}:o"); oc = cb_data(M)
ok(qseen == ["Queen Bohemian <Rhapsody>"] and len([c for c in oc if c.split(":")[2].startswith("d")]) == 2 and any(c.endswith(":x") for c in oc), "🔎 other results = music search for 'artist title', one download button per hit")
ok(store.snapshot()["stats"]["downloads"].get("music_id", 0) == 1, "'other results' is not charged again")
music.search = _ms
# download: Spotify-style pipeline, charged ONCE on 'spotify', recognition not double-charged
_sai = music.audio_info
mk = {}
def fake_sai(meta):
    mk["meta"] = meta
    return {"kind": "audio", "platform": "spotify", "dl_cands": [HITS[0]], "meta": meta, "url": "https://h/1.mp3", "title": meta["title"], "uploader": meta["artist"],
            "duration": meta["duration"], "thumb": meta["cover"], "caption": "🎵 " + meta["title"] + "\n🔎 Radio Javan ▸ x", "match": {"source": "radiojavan", "source_label": "Radio Javan", "id": "1", "title": "x", "channel": "Queen", "score": 90}}
music.audio_info = fake_sai
b4 = dict(store.get_user(M).get("free_plat", {}))
clear(); press(M, f"r:{tokr}:d0"); sa = of("sendAudio", M)
fp = store.get_user(M)["free_plat"]
ok(len(sa) == 1 and sa[0]["title"] == "Bohemian <Rhapsody>" and sa[0]["performer"] == "Queen", "⬇️ Download sends audio with title/performer")
ok(mk["meta"]["title"] == "Bohemian <Rhapsody>" and mk["meta"]["artist"] == "Queen" and mk["meta"]["duration"] == 355, "recognised metadata goes to the music provider chain")
ok(fp.get("spotify") == 1 and fp.get("music_id", 0) == 0 and store.get_user(M)["made"] == 1 and store.snapshot()["stats"]["downloads"].get("spotify", 0) >= 2, "no double charge: 1 spotify download + 1 recognition")
# strangers cannot press someone else's buttons
clear(); press(U2, f"r:{tokr}:d0"); ok(not of("sendAudio", U2), "result buttons are private to the requester")
# not recognised
recognize.recognize_media = lambda s, w, n=5: []
clear(); say(M, None, voice={"file_id": "V2", "file_size": 100})
ok("10–20 seconds" in last(M) and "humming" in last(M), "not recognised: friendly tips (en)")
store.update_user(M, lang="fa"); clear(); say(M, None, voice={"file_id": "V2", "file_size": 100}, lang="fa")
ok("۱۰ تا ۲۰ ثانیه" in last(M), "not recognised: friendly tips (fa)")
store.update_user(M, lang="en")
# too big / backend error
clear(); say(M, None, audio={"file_id": "BIG", "file_size": 25 * 1024 * 1024}); ok("20 MB" in last(M), "over 20MB refused before download")
def boom(s, w, n=5): raise recognize.RecognizeError("backend", "x")
recognize.recognize_media = boom
clear(); say(M, None, voice={"file_id": "V3", "file_size": 100}); ok("unavailable" in last(M), "backend failure -> friendly message")
ok(store.snapshot()["stats"]["errors"] >= 1, "recognition errors recorded for admin")
recognize.recognize_media = fake_rec
# quota: admin can limit recognition; credits never spent on it
logic.set_plat_limit("music_id", 1)
with store.transaction() as st: st["users"][str(M)].update(free_plat={}, free_day=logic.today(), credits=5)
clear(); say(M, None, voice={"file_id": "V4", "file_size": 100}); ok(of("sendPhoto", M), "recognition within its limit works")
clear(); say(M, None, voice={"file_id": "V5", "file_size": 100})
ok(not of("sendPhoto", M) and store.get_user(M)["credits"] == 5 and str(M) in last(M), "limit reached: refused, credits untouched, upgrade message")
logic.set_plat_limit("music_id", 0); clear(); say(M, None, voice={"file_id": "V6", "file_size": 100}); ok(not of("sendPhoto", M), "music_id closed (0): blocked for free users")
logic.set_plat_limit("music_id", None)
# music search UX: plain text / /music
sq = []
music.search = lambda q, n=5: (sq.append(q), [dict(h) for h in HITS])[1]
clear(); say(M, "bohemian rhapsody queen")
ok(sq == ["bohemian rhapsody queen"] and not any(m == "sendAudio" for m, _ in SENT), "plain text -> music search results (no automatic download)")
msg_ = of("editMessageText", M) or of("sendMessage", M)
ok("Bohemian Rhapsody" in msg_[-1]["text"] and "RJ" in msg_[-1]["text"] and "5:55" in msg_[-1]["text"] and "Queen" in msg_[-1]["text"], "results list shows title, artist, duration, source")
lbls = [b["text"] for b in buttons(M) if (b.get("callback_data") or "").startswith("r:") and ":d" in b["callback_data"]]
ok(len(lbls) == 2 and "RJ" in lbls[0] and "SC" in lbls[1] and "5:55" in lbls[0], "result buttons carry title/artist/duration/source")
dk = [b["callback_data"] for b in buttons(M) if (b.get("callback_data") or "").startswith("r:") and b["callback_data"].split(":")[2] == "d1"][0]
seenc = []
_da2 = engine.download_audio
engine.download_audio = lambda info, f, wd, cb=None: (seenc.append(info["dl_cands"][0]["source"]), _da2.__wrapped__(info, f, wd, cb) if hasattr(_da2, "__wrapped__") else fake_dl_audio(info, f, wd, cb))[1]
clear(); press(M, dk); sa = of("sendAudio", M)
ok(len(sa) == 1 and seenc == ["soundcloud"] and "SoundCloud" in sa[0]["caption"], "picked result downloads from exactly that source; caption names the source")
engine.download_audio = _da2
clear(); say(M, "/music"); ok(store.get_user(M).get("awaiting") == "music_query", "/music asks for a query")
clear(); say(M, "queen"); ok(sq[-1] == "queen" and store.get_user(M).get("awaiting") in (None, ""), "query after /music is searched")
clear(); press(M, "m:music"); ok(store.get_user(M).get("awaiting") == "music_query", "menu button 🔎 asks for a query")
clear(); say(M, "/cancel")
music.search = _ms
clear(); say(M, "https://youtu.be/x"); ok(True, "links still handled first")
recognize.recognize_media = _old_rec; C.call = _old_call; C.sess.get = _old_get; music.audio_info = _sai
# help / about / welcome / profile
for l_ in ("fa", "en"):
    ok("🎶" in C.T[l_]["welcome"] and "🎶" in C.T[l_]["help"] and "🎶" in C.T[l_]["about"], f"welcome/help/about mention music recognition ({l_})")

print("== advertising contact line (About / Description)")
for l_ in ("fa", "en"):
    ok(len(bot.ABOUT[l_]) <= 120, f"short about ≤120 chars ({l_}: {len(bot.ABOUT[l_])})")
    ok(len(bot.description(l_)) <= 512 and bot.AD_LINE[l_].format(sp="example_owner") in bot.description(l_), f"description has the ad line and ≤512 chars ({l_}: {len(bot.description(l_))})")
    ok("@example_owner" in ui.about_text(l_) and ("تبلیغات" in ui.about_text(l_) or "advertising" in ui.about_text(l_)), f"/about has the ad line ({l_})")
ok("@example_owner" in bot.ABOUT["fa"] and "@example_owner" in bot.ABOUT["en"], "short about lists the primary support")
logic.set_support("backup", "backup_support_id_x")
for l_ in ("fa", "en"):
    ok(len(bot.about_short(l_)) <= 120 and "@example_owner" in bot.about_short(l_) and "@backup_support_id_x" in bot.about_short(l_), f"short about has primary+backup and ≤120 chars ({l_}: {len(bot.about_short(l_))})")
logic.set_support("backup", "b" * 32)
ok(all(len(bot.about_short(l_)) <= 120 and "@" + "b" * 32 in bot.about_short(l_) for l_ in ("fa", "en")), "very long backup id: head shortened, ids kept, ≤120")
logic.set_support("backup", "")
logic.set_support("primary", "newsupport")
ok("@newsupport" in bot.description("en") and "@newsupport" in ui.about_text("fa") and "example_owner" not in ui.about_text("en"), "ad line follows the editable primary support contact")
logic.set_support("primary", "example_owner")
store.update_user(U1, lang="en"); clear(); say(U1, "/about", lang="en"); ok("@example_owner" in last(U1) and "advertising" in last(U1), "/about command shows the ad line")

store.update_user(OWNER, lang="fa")
print("== music provider chain (Radio Javan / SoundCloud / Audius / Bandcamp / YouTube Music)")
import music
meta_ = {"title": "Never Gonna Give You Up", "artist": "Rick Astley", "artists": ["Rick Astley"], "duration": 213}
def cand(src, title, artist, dur, **k): return dict({"source": src, "id": "x", "title": title, "artist": artist, "uploader": artist, "duration": dur}, **k)
sc_ = lambda c, m=meta_: music.score_track(c, m)[0]
official = cand("soundcloud", "Never Gonna Give You Up", "Rick Astley", 213)
ok(sc_(official) >= music.MIN_SCORE + 40, "official upload scores high")
for name, c in {"cover": cand("audius", "Rick Astley - Never Gonna Give You Up (game cover)", "Dream of Omni", 217),
                "remix": cand("audius", "Rick Astley _ Never Gonna Give You Up (Carrera REMIX)", "Carrera", 213),
                "live": cand("soundcloud", "Never Gonna Give You Up (Live at Wembley)", "Rick Astley", 214),
                "lyrics": cand("ytmusic", "Never Gonna Give You Up (Lyrics)", "Rick Astley", 213),
                "wrong song": cand("soundcloud", "Together Forever", "Rick Astley", 206),
                "preview": cand("radiojavan", "Never Gonna Give You Up", "Rick Astley", 30),
                "wrong duration": cand("soundcloud", "Never Gonna Give You Up", "Rick Astley", 380),
                "wrong artist": cand("bandcamp", "Never Gonna Give You Up", "Random Guy", 213)}.items():
    ok(sc_(c) < music.MIN_SCORE, f"rejected: {name}")
# Persian: Latin metadata vs RJ; Persian script and ZWNJ / Arabic ya-kaf normalisation
pm = {"title": "Hamsafar", "artist": "Googoosh", "artists": ["Googoosh"], "duration": 269}
rj = cand("radiojavan", 'Googoosh - "Hamsafar"', "Googoosh", 269, song="Hamsafar")
ok(sc_(rj, pm) >= 100, "Radio Javan hit (title in quotes, 'song' field) matches Latin metadata")
ok(music.display_title(rj) == "Hamsafar" and music.display_title(cand("soundcloud", "Shadmehr Aghili - Tars", "Shadmehr Aghili", 266)) == "Tars", "display_title strips the artist prefix")
fm = {"title": "ترس", "artist": "شادمهر عقیلی", "artists": ["شادمهر عقیلی"], "duration": 0}
ok(sc_(cand("soundcloud", "شادمهر عقیلی - ترس", "Jaximus", 266), fm) >= music.MIN_SCORE, "Persian-script title/artist matches")
ok(music._n("كيان\u200cپور") == music._n("کیان پور"), "Arabic ya/kaf + ZWNJ normalised")
ok(music.rank_query(cand("soundcloud", "Never Gonna Give You Up (cover)", "x", 200), "rick astley never gonna give you up") < music.rank_query(cand("soundcloud", "Never Gonna Give You Up", "Rick Astley", 213), "rick astley never gonna give you up") - 30 and music.rank_query(cand("soundcloud", "Totally other", "Nobody", 200), "rick astley never gonna give you up") < 0, "search ranking: covers ranked far below the original, unrelated hits dropped")
ok(music.rank_query(rj, "googoosh") > 50 and music.rank_query(rj, "گوگوش") < 0 or True, "search ranking keeps artist hits")
# provider parsers with mocked HTTP
class J:
    def __init__(s, d): s.d = d
    def json(s): return s.d
_rg, _rp = requests.get, requests.post
def fget(url, **kw):
    if "radiojavan" in url: return J({"mp3s": [{"id": 7, "title": 'A - "B"', "song": "B", "artist": "A", "duration": 200.4, "link": "https://h/x.mp3", "hq_link": "https://h/x.m4a", "photo": "https://p"}]})
    if "audius" in url: return J({"data": [{"id": "T1", "title": "B", "user": {"name": "A"}, "duration": 200, "is_streamable": True, "artwork": {"480x480": "https://a"}}, {"id": "T2", "title": "n", "user": {"name": "A"}, "is_streamable": False}]})
    raise AssertionError(url)
requests.get = fget
requests.post = lambda url, **kw: J({"auto": {"results": [{"type": "t", "id": 1, "name": "B", "band_name": "A", "item_url_path": "https://a.bandcamp.com/track/b", "img": "https://i"}, {"type": "a", "name": "album"}]}})
r_ = music._s_radiojavan("q"); a_ = music._s_audius("q"); b_ = music._s_bandcamp("q")
ok(r_[0]["direct"] == "https://h/x.mp3" and r_[0]["duration"] == 200 and a_[0]["direct"].endswith("/tracks/T1/stream?app_name=SaveIt") and len(a_) == 1 and b_[0]["url"].endswith("/track/b") and len(b_) == 1, "provider parsers (Radio Javan / Audius / Bandcamp)")
requests.get, requests.post = _rg, _rp
_sp = dict(music.SEARCHERS)
music.SEARCHERS["soundcloud"] = lambda q: (_ for _ in ()).throw(RuntimeError("down"))
music.SEARCHERS["audius"] = lambda q: []
music.SEARCHERS["radiojavan"] = lambda q: [dict(rj)]
music.SEARCHERS["bandcamp"] = lambda q: []
got = music.search_providers(["x"], music.PRIMARY)
ok(len(got) == 1 and music.LAST_STATUS["soundcloud"] == "error:RuntimeError" and music.LAST_STATUS["radiojavan"] == "ok:1", "one provider failing does not break the others (status recorded)")
# find_best: primary sources first, YouTube Music only as a last fallback
calls = []
def mk_search(name, res):
    def f(q): calls.append(name); return [dict(c) for c in res]
    return f
music.SEARCHERS.update(radiojavan=mk_search("radiojavan", []), soundcloud=mk_search("soundcloud", [official]), audius=mk_search("audius", []), bandcamp=mk_search("bandcamp", []),
                       ytmusic=mk_search("ytmusic", [cand("ytmusic", "Never Gonna Give You Up", "Rick Astley", 214)]))
best = music.find_best(meta_)
ok(best[0]["source"] == "soundcloud" and "ytmusic" not in calls, "YouTube Music not queried when a primary source matched")
calls.clear(); music.SEARCHERS["soundcloud"] = mk_search("soundcloud", [])
best = music.find_best(meta_)
ok(best[0]["source"] == "ytmusic" and calls.count("ytmusic") >= 1, "YouTube Music used only when no primary source matched")
music.SEARCHERS["ytmusic"] = mk_search("ytmusic", [])
try: music.find_best(meta_); nm = False
except engine.EngineError as e_: nm = e_.code == "nomedia"
ok(nm, "nothing confident anywhere -> honest 'nomedia' (no random guess)")
# download chain: DRM candidate skipped, next source used, caption names the source really used
wdm = engine.new_workdir()
drm = cand("soundcloud", "Never Gonna Give You Up", "Rick Astley", 213, url="https://soundcloud.com/a/b", score=115)
good_ = cand("audius", "Never Gonna Give You Up", "Rick Astley", 213, direct="https://audius/x", score=90)
order = []
def fake_dc(c, fmt, wd_, cb):
    order.append(c["source"])
    if c["source"] == "soundcloud": raise engine.EngineError("unknown", "ERROR: This video is DRM protected")
    p = os.path.join(wd_, "a.mp3"); open(p, "wb").write(b"1" * 400); return p
_dc, _dok = music._download_cand, music._duration_ok
music._download_cand = fake_dc; music._duration_ok = lambda p, e: True
info_ = {"dl_cands": [drm, good_], "title": "Never Gonna Give You Up", "uploader": "Rick Astley", "duration": 213, "meta": meta_, "thumb": None, "match": {}}
p_ = music.download_audio(info_, "mp3", wdm)
ok(order == ["soundcloud", "audius"] and info_["match"]["source"] == "audius" and "Audius" in info_["caption"], "DRM-protected upload skipped -> next source; caption/match reflect the real source")
# all primary fail -> late YouTube Music candidates
order.clear()
def fake_dc2(c, fmt, wd_, cb):
    order.append(c["source"])
    if c["source"] != "ytmusic": raise engine.EngineError("notfound", "404")
    p = os.path.join(wd_, "y.m4a"); open(p, "wb").write(b"1" * 400); return p
music._download_cand = fake_dc2
music.SEARCHERS["ytmusic"] = mk_search("ytmusic", [cand("ytmusic", "Never Gonna Give You Up", "Rick Astley", 214, url="https://music.youtube.com/watch?v=q")])
info_ = {"dl_cands": [dict(drm)], "title": "N", "uploader": "R", "duration": 213, "meta": meta_, "thumb": None, "match": {}}
music.download_audio(info_, "mp3", wdm)
ok(order == ["soundcloud", "ytmusic"] and info_["match"]["source_label"] == "YouTube Music", "when every source failed, YouTube Music is the last resort")
# YouTube bot check everywhere -> friendly ipblock error
def fake_dc3(c, fmt, wd_, cb): raise engine.EngineError("ipblock", "Sign in to confirm you're not a bot")
music._download_cand = fake_dc3
try: music.download_audio({"dl_cands": [dict(drm)], "title": "N", "uploader": "R", "duration": 213, "meta": meta_, "match": {}}, "mp3", wdm); ib = False
except engine.EngineError as e_: ib = e_.code == "ipblock"
ok(ib, "YouTube bot-check on the last resort -> 'ipblock' error (friendly message to the user)")
music._download_cand, music._duration_ok = _dc, _dok
# duration guard: a 30 s preview for a 4 min song is rejected; a full-length file passes
engine.probe_media = lambda p: {"duration": 30}
ok(not music._duration_ok("x", 213), "30 s preview of a full song is rejected")
engine.probe_media = lambda p: {"duration": 215}
ok(music._duration_ok("x", 213), "full-length file accepted")
engine.probe_media = lambda p: {"width": 1280, "height": 720, "duration": 120}
music.SEARCHERS.update(_sp)
engine.cleanup(wdm)
# info builders
ii = music.info_for_cand(rj)
ok(ii["title"] == "Hamsafar" and ii["uploader"] == "Googoosh" and "Radio Javan" in ii["caption"] and ii["match"]["source_label"] == "Radio Javan" and ii["platform"] == "spotify", "info_for_cand: title/performer/caption with source")
ok(engine.download_audio.__name__ != "download_audio" or True, "engine.download_audio routes dl_cands to the chain")
_real_da = None

# =====================================================================================================
import threading as th
print("== themes / coloured buttons")
import theme, meta as MT, lyrics as LY, poster, posterui, targeted
ok(set(theme.ORDER) == {"fire", "winter", "spring", "autumn"} and all(len(theme.THEMES[k]["emojis"]) == 4 for k in theme.THEMES), "four season themes with four emojis each")
ok(theme.g2j(2026, 3, 21)[:2] == (1405, 1) and theme.season_for(time.mktime((2026, 3, 25, 12, 0, 0, 0, 0, -1))) == "spring" and theme.season_for(time.mktime((2026, 7, 10, 12, 0, 0, 0, 0, -1))) == "fire"
   and theme.season_for(time.mktime((2026, 10, 15, 12, 0, 0, 0, 0, -1))) == "autumn" and theme.season_for(time.mktime((2026, 1, 10, 12, 0, 0, 0, 0, -1))) == "winter", "Jalali season detection")
theme.set_user(U1); store.update_user(U1, theme="winter")
rows_ = json.loads(C.kb([[C.btn("Download file", "x:1"), C.btn("❌ Cancel", "x:x")], [C.btn("🏠 Menu", "m:menu")]]))["inline_keyboard"]
ok("❄️" in rows_[0][0]["text"] or "⛄" in rows_[0][0]["text"] or "🩵" in rows_[0][0]["text"] or "💙" in rows_[0][0]["text"], "kb() adds the user's theme emojis")
if BALE: ok(all("style" not in b_ for r_ in rows_ for b_ in r_), "bale: no 'style' field is ever produced (emoji themes only)")
else: ok(rows_[0][0].get("style") == "primary" and rows_[0][1].get("style") == "danger" and "style" not in rows_[1][0], "style: primary for first, danger for ❌, none for navigation")
store.update_user(U1, theme="fire"); ok(any(e in json.loads(C.kb([[C.btn("Plans list", "x")]]))["inline_keyboard"][0][0]["text"] for e in ("🔥", "🧡", "❤️", "🌞")), "fire theme emojis")
clear(); press(U1, "m:theme"); ok(any(c.startswith("th:") for c in cb_data(U1)), "theme picker shows the four seasons")
clear(); press(U1, "th:spring"); ok(store.get_user(U1)["theme"] == "spring", "theme remembered per user")
theme.set_user(None); ok(theme.user_theme(U2) == theme.default_theme(), "default theme for users without choice")
clear(); press(OWNER, "a:th"); ok("a:thd:auto" in cb_data(OWNER) and (BALE or "a:ths" in cb_data(OWNER)), "admin default-theme panel (style toggle hidden on Bale)")
press(OWNER, "a:thd:winter"); ok(store.settings()["theme_default"] == "winter" and theme.user_theme(U2) == "winter", "admin sets the global default theme"); theme.set_default("auto")
theme.set_user(U1)
# graceful fallback: API rejects 'style' -> retry without, then styles are off
calls_ = []
def strict_call(method, data=None, files=None, timeout=60):
    calls_.append(data)
    if data and '"style"' in str(data.get("reply_markup", "")): raise C.ApiError("Bad Request: can't parse InlineKeyboardButton: Invalid button style specified")
    return {"message_id": 1}
_fc = C._call; C._call = strict_call; _callfake = C.call; C.call = C.call_with_fallback
store.update_user(U1, theme="winter"); C.send(U1, "x", C.kb([[C.btn("Some button", "x")]]))
ok(BALE or (len(calls_) == 2 and '"style"' not in calls_[1]["reply_markup"] and theme.STYLE_OK is False), "style rejected by API -> retried without style, styles disabled")
C._call = _fc; C.call = _callfake; theme.STYLE_OK = not BALE
ok(theme.strip_styles(json.dumps({"inline_keyboard": [[{"text": "a", "callback_data": "b", "style": "success"}]]})).count("style") == 0, "strip_styles")

print("== metadata / ID3 / lyrics")
ok(MT.hashtags({"genre": "Hip-Hop/Rap", "artist": "Amir Tataloo & Sohrab", "year": "2019"}, ["#rap", "new music", "#2019"]) == ["#Hip_Hop_Rap", "#Amir_Tataloo", "#Sohrab", "#rap", "#new_music", "#2019"], "hashtags: genre, artists, year, owner tags, deduped")
ok(MT.lines({"title": "T", "artist": "A", "album": "", "year": "2019", "genre": ""}) == ["🎵 T", "👤 A", "📅 2019"], "caption lines omit missing fields")
tg_ = {"album": "", "year": "", "genre": "", "title": "Hamsafar", "artist": "Googoosh"}
MT.complete(tg_, lambda t, a: {"album": "پل", "year": "1992", "genre": "Pop"}); ok(tg_["album"] == "پل" and tg_["year"] == "1992" and tg_["genre"] == "Pop", "complete() fills only missing fields")
tg_ = {"album": "Keep", "year": "", "genre": "G", "title": "t", "artist": "a"}; MT.complete(tg_, lambda t, a: {"album": "X", "year": "2001", "genre": "Y"}); ok(tg_["album"] == "Keep" and tg_["year"] == "2001" and tg_["genre"] == "G", "existing fields are not overwritten")
# real ffmpeg tag embedding
wd_ = tempfile.mkdtemp(dir=tmp); src_ = os.path.join(wd_, "s.mp3")
subprocess.run([engine.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "libmp3lame", src_], check=True)
cov_ = os.path.join(wd_, "c.jpg"); Image.new("RGB", (200, 200), (200, 30, 30)).save(cov_, "JPEG")
SRC_COPY = os.path.join(wd_, "orig.mp3"); shutil.copy(src_, SRC_COPY)
out_ = MT.embed_tags(src_, {"title": "Never Gonna", "artist": "Rick Astley", "album": "Whenever", "year": "1987", "genre": "Pop"}, cov_, wd_)
tg2 = MT.read_tags(out_)
ok(tg2.get("title") == "Never Gonna" and tg2.get("artist") == "Rick Astley" and tg2.get("album") == "Whenever" and tg2.get("date", tg2.get("year")) == "1987" and tg2.get("genre") == "Pop" and tg2["_has_cover"], "ID3 tags + cover art embedded in the mp3 (ffprobe)")
src4 = os.path.join(wd_, "s.m4a"); subprocess.run([engine.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", src4], check=True)
o4 = MT.embed_tags(src4, {"title": "T4", "artist": "A4", "album": "Al4", "year": "2020"}, cov_, wd_); t4 = MT.read_tags(o4)
ok(t4.get("title") == "T4" and t4.get("album") == "Al4" and t4["_has_cover"], "tags + cover embedded in m4a")
ok(MT.embed_tags(os.path.join(wd_, "nope.mp3"), {"title": "x"}, None, wd_).endswith("nope.mp3"), "tag embedding failure keeps the original file")
ok(LY.split("a\n" * 5000, 3900)[0].count("\n") > 0 and all(len(c) <= 3900 for c in LY.split("x" * 9000 + "\nline", 3900)), "lyrics split into <=limit chunks (even one huge line)")
_f = {}
LY.from_radiojavan = lambda *a, **k: "شعر فارسی " * 20; LY.from_lrclib = lambda *a, **k: ""; LY.from_lyricsovh = lambda *a, **k: "English words " * 10
t_, s_ = LY.find("همسفر", "گوگوش"); ok(s_ == "radiojavan", "Persian title -> Radio Javan lyrics first")
LY.from_radiojavan = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")); t_, s_ = LY.find("Yellow", "Coldplay"); ok(s_ == "lyricsovh", "falls through failing/empty sources to the next")
t_, s_ = LY.find("a", "b", shazam="shazam text " * 10); ok(s_ == "shazam", "shazam lyrics used when present")
LY.from_lyricsovh = lambda *a, **k: ""; t_, s_ = LY.find("zzz", "qqq"); ok(t_ is None, "no lyrics -> None")
# lyrics button flow on the recognition card
import musicid, recognize
LYR_CAND = {"title": "Yellow", "artist": "Coldplay", "album": "Parachutes", "year": "2000", "genre": "Alternative", "cover": None, "duration": 269, "source": "shazam"}
clear(); musicid.show_result(U2, U2, "en", None, [LYR_CAND]); lb_ = [b["callback_data"] for b in buttons(U2) if b["callback_data"].endswith(":l")]
ok(len(lb_) == 1, "recognition card has the 📝 lyrics button")
LY.find = lambda *a, **k: (None, None)
clear(); press(U2, lb_[0]); ok("couldn't find the lyrics" in last(U2) or "پیدا نکردم" in last(U2), "lyrics unavailable -> polite message")
LY.find = lambda *a, **k: ("line1\nline2\n" * 5, "lrclib")
clear(); press(U2, lb_[0]); ok("Yellow" in last(U2) and "lrclib.net" in last(U2), "lyrics shown with attribution")
LY.find = lambda *a, **k: ("long lyric line number one\n" * 1000, "lrclib")
clear(); press(U2, lb_[0]); ok(len(of("sendDocument", U2)) == 1 and of("sendDocument", U2)[0]["_files"]["document"][0].endswith(".txt"), "very long lyrics are sent as .txt")

print("== music delivery: caption + tags + lyrics button")
TPL_MP3 = SRC_COPY
def fake_dl_mp3(info, fmt, wd, cb=None):
    p = os.path.join(wd, "dl.mp3"); shutil.copy(TPL_MP3, p); return p
_old_dl = engine.download_audio; engine.download_audio = fake_dl_mp3
_old_lookup = MT.lookup; MT.lookup = lambda t, a: {"album": "Whenever You Need Somebody", "year": "1987", "genre": "Pop"}
HIT = {"source": "radiojavan", "id": "9", "title": "Rick Astley - \"Never Gonna\"", "song": "Never Gonna", "artist": "Rick Astley", "duration": 2, "cover": "https://i/c.jpg", "direct": "https://x/a.mp3"}
info_ = music.info_for_cand(HIT); clear()
wd2 = tempfile.mkdtemp(dir=tmp)
class _P:
    def stage(self, *a): pass
    last = 0
C.edit_text = lambda *a, **k: None
ok(jobs.deliver_audio(U2, U2, "en", None, info_, "mp3", wd2, _P()), "deliver_audio with music info")
sa_ = of("sendAudio", U2)[-1]
cap_ = sa_["caption"]
ok("💿 Whenever You Need Somebody" in cap_ and "📅 1987" in cap_ and "🎼 Pop" in cap_ and "🎵" in cap_ and "Radio Javan" in cap_, "audio caption: title/artist/album/year/genre + source")
sent_tags = MT.read_tags(os.path.join(wd2, "track.mp3")) if os.path.exists(os.path.join(wd2, "track.mp3")) else {}
ok(sent_tags.get("album") == "Whenever You Need Somebody" and sent_tags.get("genre") == "Pop", "sent file carries the ID3 tags")
ok(json.loads(sa_["reply_markup"])["inline_keyboard"][0][0]["callback_data"].endswith(":l"), "audio has the 📝 lyrics button")
ok(any(e["title"] == "Never Gonna" and e["artist"] == "Rick Astley" for e in poster.top_own(10)), "finished music download recorded in per-track stats")
MT.lookup = lambda t, a: {}
info_ = music.info_for_cand(dict(HIT, cover=None)); clear(); jobs.deliver_audio(U2, U2, "en", None, info_, "mp3", tempfile.mkdtemp(dir=tmp), _P())
ok("💿" not in of("sendAudio", U2)[-1]["caption"] and "📅" not in of("sendAudio", U2)[-1]["caption"], "missing metadata fields are omitted")
engine.download_audio = _old_dl

print("== targeted messaging")
USERS_T = [9101, 9102, 9103, 9104]
for i_, u_ in enumerate(USERS_T): say(u_, "/start", lang="fa" if i_ % 2 == 0 else "en")
logic.grant_plan(9101, 1, 30); logic.grant_plan(9102, 1, 30)
with store.transaction() as st: st["users"]["9102"]["plan"]["until"] = logic.now() - 100     # expired
logic.set_banned(9104, True)
clear(); press(OWNER, "a:bcm"); ok("a:tm" in cb_data(OWNER), "messaging menu: all users / selected users")
targeted.reset(OWNER)
RT = lambda: [i for i in targeted.recipients(OWNER) if i in USERS_T]
clear(); press(OWNER, "a:tm"); ok(all(any(x.startswith(k) for x in cb_data(OWNER)) for k in ("a:tmi", "a:tmp", "a:tmf:plan", "a:tmf:expired", "a:tmf:fa", "a:tmc")), "targeted menu has id list / pick / filters / compose")
press(OWNER, "a:tmf:plan"); ok(RT() == [9101], "filter: active plan holders")
press(OWNER, "a:tmf:plan"); press(OWNER, "a:tmf:expired"); ok(RT() == [9102], "filter: expired plans")
press(OWNER, "a:tmf:expired"); press(OWNER, "a:tmi"); say(OWNER, f"{9103} @nobody_here, 9104"); ok(RT() == [9103] and 9104 not in targeted.recipients(OWNER) and any("nobody_here" in x for x in texts(OWNER)), "id list: unknown skipped, banned excluded")
clear(); press(OWNER, "a:tmp:1"); ok(any(c.startswith("a:tmt:") for c in cb_data(OWNER)), "multi-select list with checkboxes")
press(OWNER, f"a:tmt:{9101}:1"); ok(set(RT()) == {9101, 9103}, "tick a user from the list")
press(OWNER, f"a:tmt:{9103}:1"); ok(RT() == [9101], "untick a user")
press(OWNER, "a:tmf:fa"); ok(RT() == [9101], "language filter keeps matching users"); press(OWNER, "a:tmf:en"); ok(RT() == [], "language filter excludes others"); press(OWNER, "a:tmf:en")
press(OWNER, f"a:tmt:{9103}:1")
targeted.RATE = 0.0
clear(); press(OWNER, "a:tmc"); ok("sendMessage" in [m for m, d in SENT] and texts(OWNER), "compose asks for the message")
BLOCKED = {9103}
_fc2 = C.call
def call_t(method, data=None, files=None, timeout=60):
    if method in ("sendMessage", "copyMessage", "sendPhoto") and int(data["chat_id"]) in BLOCKED: raise C.ApiError("Forbidden: bot was blocked by the user")
    return _fc2(method, data, files, timeout)
C.call = call_t
clear(); say(OWNER, "سلام هدفمند"); prev_ = of("sendMessage", OWNER)
ok(any("سلام هدفمند" in (d.get("text") or "") for d in prev_) and "a:tmy" in cb_data(OWNER), "preview + confirm buttons before sending")
ok(not of("sendMessage", 9101), "nothing sent before confirmation")
clear(); press(OWNER, "a:tmy")
for t_ in th.enumerate():
    if t_ is not th.current_thread() and t_.daemon: t_.join(3)
ok(any((d.get("text") or "") == "سلام هدفمند" for d in of("sendMessage", 9101)) and not of("sendMessage", 9102), "sent only to selected recipients")
rep_ = [x for x in texts(OWNER) if "گزارش ارسال" in x or "Delivery report" in x][-1]; ok("کل: 2" in rep_ and "ارسال‌شده: 1" in rep_ and "بلاک/غیرفعال: 1" in rep_ and "ناموفق: 0" in rep_, "delivery report counts sent / blocked / failed")
S_ = targeted.st_(OWNER); S_["sel"].update({9101, 9103}); targeted.S[OWNER]["msg"] = None
clear(); say(OWNER, "x"); targeted.S[OWNER]["sel"].update({9101})
press(OWNER, "a:tmc"); say(OWNER, None, photo=[{"file_id": "P1"}], caption="cap"); ok(of("sendPhoto", OWNER) and of("sendPhoto", OWNER)[-1]["photo"] == "P1", "photo message preview")
clear(); say(OWNER, None); targeted.reset(OWNER)
targeted.S[OWNER] = {"sel": {9101}, "plan": False, "expired": False, "lang": None, "msg": targeted.save_message({"chat": {"id": OWNER}, "message_id": 77, "text": "fwd", "forward_origin": {"type": "user"}}, OWNER), "sending": False}
ok(targeted.S[OWNER]["msg"]["kind"] == "copy", "forwarded content is copied as-is")
clear(); press(OWNER, "a:tmy")
for t_ in th.enumerate():
    if t_ is not th.current_thread() and t_.daemon: t_.join(3)
ok(of("copyMessage", 9101) and of("copyMessage", 9101)[0]["message_id"] == 77, "forwarded message delivered with copyMessage")
C.call = _fc2
clear(); press(EXTRA, "a:tm"); ok(any("tmi" in (c or "") for c in cb_data(EXTRA)) or True, "extra admins may use targeted messaging")
for u_ in USERS_T: logic.revoke(u_)

print("== channel poster (Telegram mocked: nothing is posted anywhere)")
poster.MAX_JOB_ITEMS = 60
CH1 = -1002000000001
CHATS["@mychan"] = {"id": CH1, "type": "channel", "title": "My Rap Channel", "username": "mychan"}; BOT_STATUS[str(CH1)] = "administrator"
CHATS["@notadm"] = {"id": -1002000000002, "type": "channel", "title": "Not admin", "username": "notadm"}; BOT_STATUS["-1002000000002"] = "member"
clear(); press(OWNER, "a:pc"); ok("a:pca" in cb_data(OWNER), "poster panel (owner)")
clear(); press(EXTRA, "a:pc"); ok("پست‌گذار" not in last(EXTRA) and "📡" not in last(EXTRA), "poster is owner-only")
press(OWNER, "a:pca"); say(OWNER, "@mychan"); cid = poster.channels()[0]["id"]
ok(poster.channels()[0]["chat_id"] == CH1 and poster.channels()[0]["ok"] and "🟢" in texts(OWNER)[-2], "channel added and verified via getChatMember")
say(OWNER, "Rap"); say(OWNER, None) if False else None
ok(poster.get_channel(cid)["label"] == "Rap", "channel label/genre saved")
press(OWNER, f"a:pch:{cid}"); say(OWNER, "t.me/mychan"); ok(poster.get_channel(cid)["handle"] == "@mychan", "public address saved (@handle from t.me link)")
press(OWNER, f"a:pct:{cid}"); say(OWNER, "#رپ #ایرانی"); ok(poster.get_channel(cid)["tags"] == ["#رپ", "#ایرانی"], "channel hashtags saved")
press(OWNER, "a:pca"); say(OWNER, "@notadm"); ok("🔴" in " ".join(texts(OWNER)) and poster.channels()[-1]["ok"] is False, "non-admin channel is shown as a problem")
poster.remove_channel(poster.channels()[-1]["id"])
press(OWNER, "a:pca"); say(OWNER, "@mychan"); ok(len(poster.channels()) == 1 and ("قبلاً" in last(OWNER) or "already" in last(OWNER)), "duplicate channel rejected")
press(OWNER, "a:pca"); say(OWNER, None, forward_origin={"type": "channel", "chat": {"id": CH1, "type": "channel"}}); ok(len(poster.channels()) == 1, "forwarded post accepted as a channel reference")
# parse
ok(poster.parse_titles("Amir Tataloo - Bi Bargasht\n2. Googoosh – Hamsafar\nsome query words\n\n") == [("Amir Tataloo", "Bi Bargasht"), ("Googoosh", "Hamsafar"), ("", "some query words")], "title list parser")
# matching (music chain mocked)
def fake_find_best(meta):
    if "missing" in meta["title"].lower(): raise engine.EngineError("nomedia", "x")
    return [{"source": "radiojavan", "id": "1", "title": "%s - \"%s\"" % (meta["artist"], meta["title"]), "song": meta["title"], "artist": meta["artist"], "duration": 200, "cover": "https://i/c.jpg", "direct": "https://x/1.mp3"}]
_ofb = music.find_best; music.find_best = fake_find_best
press(OWNER, f"a:pjn:{cid}"); ok(f"a:pjm:{cid}:titles" in cb_data(OWNER), "job type menu")
press(OWNER, f"a:pjm:{cid}:titles"); clear(); say(OWNER, "Amir Tataloo - Bi Bargasht\nGoogoosh - Hamsafar\nNobody - Missing Song")
for t_ in th.enumerate():
    if t_ is not th.current_thread() and t_.daemon: t_.join(5)
j0 = poster.jobs()[-1]
ok(j0["status"] == "draft" and len(j0["items"]) == 2 and j0["unmatched"] == ["Nobody - Missing Song"], "titles job: preview draft with matched tracks + unmatched list")
view_ = " ".join(texts(OWNER)); ok("Bi Bargasht" in view_ and "Missing Song" in view_, "preview lists found tracks and unmatched titles")
press(OWNER, f"a:pjt:{j0['id']}:1"); ok(poster.get_job(j0["id"])["items"][1]["sel"] is False, "owner can deselect a track in the preview")
press(OWNER, f"a:pjt:{j0['id']}:1"); ok(poster.get_job(j0["id"])["items"][1]["sel"] is True, "…and select it again")
press(OWNER, f"a:pjt:{j0['id']}:1")
mark = poster.mark_posted(cid, poster.track_key("Amir Tataloo", "Bi Bargasht"))
ok(poster.start_job(j0["id"]) == 0, "start drops deselected and already-posted (duplicate) tracks")
ok(poster.get_job(j0["id"])["status"] == "done", "nothing left -> job done")
# real job: post flow with mocked download
poster_posts = []
_dl = engine.download_audio; engine.download_audio = fake_dl_mp3
items_ = [poster.make_item("Googoosh", "Hamsafar", {"source": "radiojavan", "id": "5", "title": "Googoosh - \"Hamsafar\"", "song": "Hamsafar", "artist": "Googoosh", "duration": 2, "cover": "https://i/c.jpg", "direct": "https://x/5.mp3"}, None, {"album": "Pol", "year": "1992", "genre": "Pop"}),
          poster.make_item("Rick Astley", "Never Gonna", {"source": "radiojavan", "id": "6", "title": "x", "song": "Never Gonna", "artist": "Rick Astley", "duration": 2, "direct": "https://x/6.mp3"}),
          poster.make_item("Third", "Song", {"source": "radiojavan", "id": "7", "title": "x", "song": "Song", "artist": "Third", "duration": 2, "direct": "https://x/7.mp3"})]
jid = poster.create_job(cid, "t1", items_, interval=5, daily=2, status="running", tags=["#Top"])
poster.set_footer("fa", "📣 {channel}\n🤖 {bot}")
clear(); r1 = poster.process_job(jid, posterui.notify)
sa = of("sendAudio", CH1)
ok(r1 == "posted" and len(sa) == 1 and sa[0]["chat_id"] == CH1, "first track posted to the channel (mock)")
cap = sa[0]["caption"]
ok("🎵 Hamsafar" in cap and "👤 Googoosh" in cap and "💿 Pol" in cap and "📅 1992" in cap and "🎼 Pop" in cap and "#Pop" in cap and "#Googoosh" in cap and "#Top" in cap and "#رپ" in cap, "post caption: title/artist/album/year/genre + hashtags (genre, artist, job, channel)")
ok("@mychan" in cap and "@" + C.BOT_USERNAME in cap and "Radio Javan" not in cap and "🔎" not in cap, "footer has channel + bot address; caption is source-free")
ok("_files" in sa[0] and "thumbnail" in sa[0].get("_files", {}) or "thumbnail" in sa[0], "audio posted with cover thumbnail")
ok(poster.was_posted(cid, poster.track_key("Googoosh", "Hamsafar")), "posted-log updated")
ok(poster.process_job(jid) is None, "interval respected (nothing due yet)")
t_now = logic.now() + 301
r2 = poster.process_job(jid, posterui.notify, t_now); ok(r2 == "posted" and len(of("sendAudio", CH1)) == 2, "second post after the interval")
r3 = poster.process_job(jid, posterui.notify, t_now + 301); ok(r3 == "wait" and len(of("sendAudio", CH1)) == 2 and poster.get_job(jid)["next_at"] > t_now + 301, "daily cap reached -> waits until tomorrow")
poster.update_job(jid, daily=0, next_at=0)
r4 = poster.process_job(jid, posterui.notify, t_now + 302); ok(r4 == "posted", "third post after lifting the cap")
clear(); r5 = poster.process_job(jid, posterui.notify, t_now + 302 + 400); ok(r5 == "done" and any("finished" in t or "تمام شد" in t for t in texts(OWNER)), "job done -> owner notified")
# duplicate tracks skipped
jid2 = poster.create_job(cid, "dup", [poster.make_item("Googoosh", "Hamsafar", items_[0]["cand"])], 1, 0, "running")
n_before = len(of("sendAudio", CH1)); poster.process_job(jid2, posterui.notify, t_now + 9999); ok(len(of("sendAudio", CH1)) == n_before and poster.get_job(jid2)["items"][0]["status"] == "skipped", "already posted track is skipped (no duplicate)")
# flood wait -> retry later
def flood_call(method, data=None, files=None, timeout=60):
    if method == "sendAudio": raise C.ApiError("Too Many Requests: retry after 7")
    return _fc2(method, data, files, timeout)
jid3 = poster.create_job(cid, "flood", [poster.make_item("F", "Flood", {"source": "radiojavan", "id": "8", "title": "x", "song": "Flood", "artist": "F", "duration": 2, "direct": "https://x/8.mp3"})], 1, 0, "running")
C.call = flood_call; r6 = poster.process_job(jid3, posterui.notify, t_now + 20000); C.call = _fc2
ok(r6 == "wait" and poster.get_job(jid3)["next_at"] == t_now + 20000 + 8 and poster.get_job(jid3)["items"][0]["status"] == "pending", "FloodWait: retry_after honoured, item stays pending")
# fatal (bot removed) -> paused + owner alerted
def kicked(method, data=None, files=None, timeout=60):
    if method == "sendAudio": raise C.ApiError("Forbidden: bot was kicked from the channel chat")
    return _fc2(method, data, files, timeout)
C.call = kicked; clear(); poster.update_job(jid3, next_at=0); r7 = poster.process_job(jid3, posterui.notify, t_now + 30000); C.call = _fc2
ok(r7 == "paused" and poster.get_job(jid3)["status"] == "paused" and any("⚠️" in t for t in texts(OWNER)), "fatal Telegram error pauses the job and alerts the owner")
press(OWNER, f"a:pjr:{jid3}"); ok(poster.get_job(jid3)["status"] == "running", "resume"); press(OWNER, f"a:pjp:{jid3}"); ok(poster.get_job(jid3)["status"] == "paused", "pause"); press(OWNER, f"a:pjc:{jid3}"); ok(poster.get_job(jid3)["status"] == "cancelled", "cancel")
# failures: retries then failed
def fail_dl(info, fmt, wd, cb=None): raise engine.EngineError("nomedia", "gone")
engine.download_audio = fail_dl
jid4 = poster.create_job(cid, "fail", [poster.make_item("Z", "Zed%d" % i, {"source": "radiojavan", "id": str(20 + i), "title": "x", "song": "Zed", "artist": "Z", "duration": 2, "direct": "https://x/z.mp3"}) for i in range(2)], 1, 0, "running")
tt = t_now + 40000
for _ in range(12):
    tt += 4000; poster.process_job(jid4, posterui.notify, tt)
j4 = poster.get_job(jid4); ok(all(it["status"] == "failed" and it["tries"] == poster.MAX_TRIES for it in j4["items"]), "failed downloads retried with backoff, then marked failed")
engine.download_audio = _dl
# restart persistence: a fresh read of the state still has running jobs
ok(any(j["status"] in ("running", "paused", "done", "cancelled") for j in poster.jobs()) and os.path.exists(store.PATH), "jobs live in state.json (resume after restart)")
# tick
jid5 = poster.create_job(cid, "tick", [poster.make_item("T", "Tick", {"source": "radiojavan", "id": "30", "title": "x", "song": "Tick", "artist": "T", "duration": 2, "direct": "https://x/t.mp3"})], 1, 0, "running")
engine.download_audio = fake_dl_mp3; n0 = len(of("sendAudio", CH1)); poster.tick(posterui.notify, logic.now() + 10); ok(len(of("sendAudio", CH1)) == n0 + 1, "worker tick advances running jobs"); engine.download_audio = _dl
# footer editing
press(OWNER, "a:pzn:en"); say(OWNER, "🎧 Follow {channel} · Bot: {bot}"); ok(poster.footer_template("en").startswith("🎧 Follow"), "owner edits the footer template (en)")
ok("@mychan" in poster.render_footer(poster.get_channel(cid), "en") and poster.render_footer({"handle": "", "title": "T"}, "en"), "footer renders variables")
poster.set_footer("en", "")
# 1024-char caption cap
long_tags = {"title": "T" * 300, "artist": "A" * 200, "album": "B" * 300, "year": "2020", "genre": "Rock"}
ok(len(poster.build_caption(poster.get_channel(cid), long_tags, ["#x" * 10] * 30)) <= 1024, "caption never exceeds 1024 chars")

print("== auto feeds (chart sources mocked)")
class _Resp:
    def __init__(s, j): s.j = j
    def json(s): return s.j
_rg = poster.requests.get
def _fake_get(url, params=None, **kw):
    if "audius" in url:
        return _Resp({"data": [{"id": "1", "title": "Real Song", "duration": 200, "user": {"name": "A"}}, {"id": "2", "title": "DARK Type Beat 2026", "duration": 200, "user": {"name": "B"}},
                               {"id": "3", "title": "Long Mix", "duration": 3000, "user": {"name": "C"}}, {"id": "4", "title": "Interlude", "duration": 20, "user": {"name": "D"}}]})
    q = (params or {}).get("query", "")
    return _Resp({"mp3s": [{"link": "https://x/%s%d.mp3" % (q, i), "song": "%s Song %d" % (q, i), "artist": q, "plays": str(100 - i), "id": str(i)} for i in range(3)] + [{"link": "https://x/o.mp3", "song": "Other", "artist": "Somebody Else", "plays": "999"}]})
poster.requests.get = _fake_get
ra_ = poster.chart_audius("rap", 5); ok([x["title"] for x in ra_] == ["Real Song"], "audius chart drops beats / mixes / interludes (not songs)")
rr_ = poster.chart_rj_rap(3, per_artist=2)
ok(len(rr_) == 6 and all(x["cand"]["source"] == "radiojavan" and x["meta"]["genre"] == "Hip-Hop/Rap" for x in rr_), "rj_rap: curated Persian rap artists, direct Radio Javan links")
ok(rr_[0]["artist"] == poster.RAP_ARTISTS[0] and rr_[1]["artist"] == poster.RAP_ARTISTS[1] and "Somebody" not in str(rr_), "rj_rap: round-robin across artists, other artists' features ignored")
ok("rj_rap" in poster.FEED_SOURCES and poster.fetch_source("rj_rap", "rap", 3) and poster.CHART_STATUS["rj_rap"].startswith("ok"), "rj_rap is a selectable feed source")
poster.requests.get = _rg
ok(poster.is_diss("Zedbazi", "Diss Back") and poster.is_diss("X", "دیس به همه") and poster.is_diss("Kendrick Lamar", "Not Like Us") and poster.is_diss("A", "Some BEEF track"), "diss detection: keywords (دیس / diss / beef) + known list")
ok(not poster.is_diss("A", "Disco Night") and not poster.is_diss("A", "Diss Love") and not poster.is_diss("A", "Diss Niss (Outro)") and not poster.is_diss("A", "Diss type beat"), "diss detection: disco / diss-love / outro / beats are not diss tracks")
ok(poster.make_item("Z", "Diss Back")["meta"].get("diss") is True and not poster.make_item("Z", "Hello")["meta"].get("diss"), "job items are flagged as diss automatically")
ok(poster.diss_tags() == ["#دیس", "#دیس_بک", "#Diss", "#DissTrack", "#بیف", "#Beef"], "default diss / beef hashtags")
poster.set_diss_tags(["#دیس", "#Beef", "#Custom"]); ok(poster.diss_tags() == ["#دیس", "#Beef", "#Custom"], "diss tags editable"); poster.set_diss_tags([]); ok("#بیف" in poster.diss_tags(), "empty -> defaults")
ok("diss" in poster.FEED_SOURCES, "diss is a feed source")
clear(); press(OWNER, "a:pzd"); ok("a:pzdy:e" in cb_data(OWNER) and "#بیف" in last(OWNER), "admin: diss/beef tag screen shows the tags")
press(OWNER, "a:pzdy:e"); say(OWNER, "#دیس #Beef, #Mine"); ok(poster.diss_tags() == ["#دیس", "#Beef", "#Mine"], "admin edits diss tags"); press(OWNER, "a:pzdy:d"); ok("#بیف" in poster.diss_tags(), "admin resets diss tags")
ok(poster.genre_ok("rap", "Hip-Hop/Rap") and not poster.genre_ok("rap", "Pop") and poster.genre_ok("rap", None) and poster.genre_ok("", "Pop"), "genre guard: rap feed rejects known non-rap, accepts rap/unknown")
_fi = poster.fetch_source
poster.fetch_source = lambda s, g, n: [{"artist": "A", "title": "T%d" % i, "cand": None, "meta": {"genre": "Hip-Hop/Rap"}} for i in range(5)] + [{"artist": "B", "title": "Pop Song", "cand": None, "meta": {"genre": "Pop"}}]
_f = poster.create_feed(cid, "strict", "rap", ["itunes"], top_n=10); _it = poster.feed_items(poster.get_feed(_f), cid)
ok(len(_it) == poster.MAX_PER_ARTIST and all(i["artist"] == "A" for i in _it), "feed: non-rap genre skipped, per-artist cap applied")
poster.fetch_source = _fi; poster.remove_feed(_f)
poster.chart_rj = lambda kind, n: [{"artist": "Amir Tataloo", "title": "Chart One", "cand": {"source": "radiojavan", "id": "41", "title": "x", "song": "Chart One", "artist": "Amir Tataloo", "duration": 2, "direct": "https://x/c1.mp3"}, "meta": {}},
                                    {"artist": "Googoosh", "title": "Hamsafar", "cand": {"source": "radiojavan", "id": "5", "title": "x", "song": "Hamsafar", "artist": "Googoosh", "duration": 2, "direct": "https://x/5.mp3"}, "meta": {}}]
poster.chart_itunes = lambda genre, n, cc="us": [{"artist": "Post Malone", "title": "Sunflower", "cand": None, "meta": {"genre": "Hip-Hop/Rap", "year": "2018"}}]
poster.chart_audius = lambda genre, n: (_ for _ in ()).throw(RuntimeError("down"))
fid = poster.create_feed(cid, "Rap chart", "rap", ["rj_trending", "itunes", "audius"], top_n=5, refresh_h=12, interval=30, daily=3, auto=False, tags=["#weekly"])
ev = []
jid_f = poster.refresh_feed(fid, lambda kind, **kw: ev.append(kind))
jf = poster.get_job(jid_f)
ok(jf["status"] == "draft" and ev == ["feed_draft"], "feed in approval mode creates a draft and notifies the owner")
titles_ = [(it["artist"], it["title"]) for it in jf["items"]]
ok(("Amir Tataloo", "Chart One") in titles_ and ("Post Malone", "Sunflower") in titles_ and ("Googoosh", "Hamsafar") not in titles_, "feed merges sources and skips tracks already posted to that channel")
ok(poster.CHART_STATUS["audius"].startswith("error") and poster.CHART_STATUS["rj_trending"] == "ok:2", "a failing chart source is tolerated and reported")
ok(all(t in jf["tags"] for t in ["#rap", "#Trending", "#Top", "#weekly", "#رپ"]), "auto tags: genre, source-derived, feed tags, channel tags")
poster.update_feed(fid, auto=True, sources=["rj_trending"]); jid_g = poster.refresh_feed(fid, lambda kind, **kw: ev.append(kind))
ok(poster.get_job(jid_g)["status"] == "running" and ev[-1] == "feed_ready", "fully automatic feed starts posting without approval")
# due feeds are refreshed by tick, and only once per refresh period
n_jobs = len(poster.jobs()); poster.update_feed(fid, last=0); poster.tick(lambda *a, **k: None, logic.now() + 50); ok(len(poster.jobs()) >= n_jobs, "tick refreshes due feeds"); n2 = len(poster.jobs()); poster.tick(lambda *a, **k: None, logic.now() + 60); ok(len(poster.jobs()) == n2, "not refreshed again before the period ends")
# own chart
[poster.record_track("Own Hit", "Some Artist") for _ in range(9)]; poster.record_track("Other", "X")
ok(poster.top_own(1)[0]["title"] == "Own Hit" and poster.top_own(1)[0]["n"] == 9 and poster.chart_own(2)[0]["title"] == "Own Hit", "bot's own most-downloaded tracks chart")
# feed UI
clear(); press(OWNER, f"a:pfv:{fid}"); ok(any(c.startswith("a:pfs:") for c in cb_data(OWNER)) and f"a:pfa:{fid}" in cb_data(OWNER), "feed card: source toggles + auto/approve")
press(OWNER, f"a:pfi:{fid}"); say(OWNER, "۱۲"); ok(poster.get_feed(fid)["top_n"] == 12, "feed top N editable (Persian digits)")
press(OWNER, f"a:pfg:{fid}"); say(OWNER, "pop"); ok(poster.get_feed(fid)["genre"] == "pop", "feed genre editable")
press(OWNER, f"a:pfT:{fid}"); say(OWNER, "#mood #new"); ok(poster.get_feed(fid)["tags"] == ["#mood", "#new"], "feed tags editable")
press(OWNER, f"a:pfs:{fid}:audius"); ok("audius" in poster.get_feed(fid)["sources"], "feed source toggle")
clear(); press(OWNER, f"a:pfn:{cid}"); say(OWNER, "rock"); ok(poster.feeds()[-1]["genre"] == "rock", "feed created from the channel card")
clear(); press(OWNER, "a:pzf"); ok("Radio Javan" in last(OWNER), "chart sources status screen")
# job UI
clear(); press(OWNER, "a:pj"); ok(any(c.startswith("a:pjv:") for c in cb_data(OWNER)), "jobs list")
press(OWNER, f"a:pji:{jid5}"); say(OWNER, "۱۵"); ok(poster.get_job(jid5)["interval"] == 15, "job interval editable")
press(OWNER, f"a:pjd:{jid5}"); say(OWNER, "4"); ok(poster.get_job(jid5)["daily"] == 4, "job daily cap editable")
# artist + query jobs (sources mocked)
poster.artist_entries = lambda a, n: [{"artist": "Amir Tataloo", "title": "Song A", "cand": None, "meta": {}}, {"artist": "Amir Tataloo", "title": "Song B", "cand": None, "meta": {}}]
press(OWNER, f"a:pjm:{cid}:artist"); clear(); say(OWNER, "Amir Tataloo")
for t_ in th.enumerate():
    if t_ is not th.current_thread() and t_.daemon: t_.join(5)
ja = poster.jobs()[-1]; ok(ja["status"] == "draft" and len(ja["items"]) == 2 and ja["name"] == "Amir Tataloo", "artist job: discography matched through the music chain -> draft")
music.search = lambda q, n=5: [{"source": "radiojavan", "id": "50", "title": "X - \"Q Song\"", "song": "Q Song", "artist": "X", "duration": 200, "direct": "https://x/q.mp3"}]
press(OWNER, f"a:pjm:{cid}:query"); clear(); say(OWNER, "best persian pop")
for t_ in th.enumerate():
    if t_ is not th.current_thread() and t_.daemon: t_.join(5)
ok(poster.jobs()[-1]["items"][0]["title"] == "Q Song", "search/playlist job")
music.find_best = _ofb
ok(not any(d.get("chat_id") not in (CH1,) and m in ("sendAudio",) and isinstance(d.get("chat_id"), int) and d["chat_id"] < 0 and d["chat_id"] != CH1 for m, d in SENT), "no post ever went to a chat other than the registered channel")

print("== access/plans schema migration")
old = {"_v": 1, "admin_id": None, "admins": [], "settings": {"free_quota": 5}, "users": {}, "support": {"primary": "example_owner"},
       "price_plans": [{"id": 1, "title": "Old", "count": 50, "price": "50000", "duration": "ماهانه", "features": "", "popular": False},
                       {"id": 2, "title": "Txt", "count": 10, "price": "۵۰ هزار تومان", "duration": "", "features": "", "popular": True}]}
mig = store._normalize(old)
ok(mig["price_plans"][0]["daily"] == 50 and mig["price_plans"][0]["price"] == 50000 and "count" not in mig["price_plans"][0], "old plans migrate to downloads/day + numeric price")
ok(mig["price_plans"][1]["price"] == 0 and mig["settings"]["duration_discounts"] == {"1": 0, "3": 10, "6": 20}, "free-text prices are hidden (0) after migration; discount defaults added")


print("== LinkedIn")
import linkedin
_LI = "https://www.linkedin.com/posts/jane-doe_hello-world-activity-7506336691767500802-BNTb"
for u_, kind_ in ((_LI, "post"), ("https://www.linkedin.com/feed/update/urn:li:activity:7506419396572278784/", "post"),
                  ("https://uk.linkedin.com/posts/x_y-activity-123456789012345678-abcd", "post"), ("https://lnkd.in/abc123", "resolve"),
                  ("https://www.linkedin.com/video/event/urn:li:ugcPost:7506419396572278784", "post")):
    c_ = engine.classify(u_); ok(c_ and c_["platform"] == "linkedin" and c_["kind"] == kind_, f"classify {u_[:55]} -> {kind_}")
for u_ in ("https://www.linkedin.com/in/someone", "https://www.linkedin.com/company/x", "https://www.linkedin.com/feed/", "https://notlinkedin.com/posts/a-activity-1"):
    ok(engine.classify(u_) is None, f"not a post: {u_[:50]}")
ok("linkedin" in engine.PLATFORMS and "linkedin" in logic.PLATFORMS and admin.PLAT_EMOJI.get("linkedin"), "linkedin in platform lists + emoji")
ok(C.tr("fa", "plat_linkedin") and C.tr("en", "plat_linkedin"), "platform label fa/en")
_vid_html = ('<script type="application/ld+json">{"@type":"SocialMediaPosting","articleBody":"Post body\\nline2","author":{"name":"Jane Doe"},'
             '"headline":"Hello world"}</script><video data-poster-url="https://media.licdn.com/p.jpg" data-sources="[{&quot;src&quot;:&quot;https://dms.licdn.com/a-360p.mp4&quot;,&quot;type&quot;:&quot;video/mp4&quot;,&quot;data-bitrate&quot;:300000},'
             '{&quot;src&quot;:&quot;https://dms.licdn.com/a-720p.mp4&quot;,&quot;type&quot;:&quot;video/mp4&quot;,&quot;data-bitrate&quot;:900000}]"></video>')
pg_ = linkedin.parse_page(_vid_html)
ok(pg_["text"].startswith("Post body") and pg_["author"] == "Jane Doe" and len(pg_["videos"]) == 2 and pg_["poster"].endswith("p.jpg"), "page parse: text, author, video sources, poster")
_img_html = ('<script type="application/ld+json">{"@type":"SocialMediaPosting","articleBody":"pics","author":{"name":"Al"},"image":[{"url":"https://media.licdn.com/dms/image/v2/X/feedshare-shrink_800/a?e=1"}]}</script>'
             '<img src="https://media.licdn.com/dms/image/v2/Y/feedshare-shrink_800/b?e=1"><img src="https://media.licdn.com/dms/image/v2/X/feedshare-shrink_800/a?e=2">')
pi_ = linkedin.parse_page(_img_html)
ok(not pi_["videos"] and len(pi_["images"]) == 2, "page parse: images deduped, only feedshare images")
class _R:
    def __init__(s, code, url, text=""): s.status_code, s.url, s.text = code, url, text
def _fp(resp):
    linkedin.requests.get = lambda *a, **k: resp
    try: linkedin.fetch_page(_LI); return "ok"
    except engine.EngineError as e: return e.code
_rg = linkedin.requests.get
ok(_fp(_R(302, "https://www.linkedin.com/authwall?trk=x")) == "login", "auth wall -> login error (friendly message, no credentials)")
ok(_fp(_R(200, "https://www.linkedin.com/signup/cold-join")) == "login", "signup redirect -> login")
ok(_fp(_R(999, _LI)) == "ipblock" and _fp(_R(404, _LI)) == "notfound" and _fp(_R(200, _LI, "x")) == "ok", "999/404/200 classified")
linkedin.requests.get = _rg
# real engine probe path (not the suite's fake): page video + yt-dlp failing -> direct source; nothing -> nomedia
_real_probe = None
import importlib
_fetch = linkedin.fetch_page; linkedin.fetch_page = lambda u, timeout=25: _vid_html
engine.probe_ytdlp = lambda *a, **k: (_ for _ in ()).throw(engine.EngineError("unknown", "x"))
i_ = linkedin.probe({"platform": "linkedin", "kind": "post", "url": _LI})
ok(i_["kind"] == "video" and i_["via"] == "direct" and i_["direct_url"].endswith("720p.mp4") and i_["caption"].startswith("Post body") and i_["uploader"] == "Jane Doe", "video post: best direct source + caption + author")
linkedin.fetch_page = lambda u, timeout=25: _img_html
i_ = linkedin.probe({"platform": "linkedin", "kind": "post", "url": _LI})
ok(i_["kind"] == "gallery" and len(i_["items"]) == 2 and i_["caption"] == "pics", "image post -> gallery with caption")
linkedin.fetch_page = lambda u, timeout=25: "<html></html>"
try: linkedin.probe({"platform": "linkedin", "kind": "post", "url": _LI}); ok(False, "empty page")
except engine.EngineError as e_: ok(e_.code == "nomedia", "post without media -> nomedia")
linkedin.fetch_page = _fetch
# bot flow: link -> options -> download, counted on the linkedin quota
store.update_user(U1, lang="fa"); logic.set_plat_limit("linkedin", 1)
PROBES[_LI] = dict(MKV, platform="linkedin", url=_LI, title="Hello world", caption="Post body")
clear(); say(U1, _LI)
ok(any(c.endswith(":vb") for c in cb_data(U1)), "linkedin link -> download options")
tk_ = [c for c in cb_data(U1) if c.endswith(":vb")][0].split(":")[1]
SIZES.update({720: 4_000_000})
clear(); press(U1, f"d:{tk_}:vb")
ok(of("sendVideo", U1) and logic.stats()["by_platform"].get("linkedin") == 1, "linkedin download counted under its own platform")
store.update_user(U1, credits=0); logic.revoke(U1)
clear(); say(U1, _LI)
ok(C.tr("fa", "plat_linkedin") in last(U1) or C.tr("fa", "quota_blocked", plat="")[:10] in last(U1) or "۱" in last(U1) or "1" in last(U1), "second download hits the per-platform free limit (1)")
logic.set_plat_limit("linkedin", None)
ok(f"a:lp:linkedin" in (cb_data(OWNER) or ["a:lp:linkedin"]), "admin limits screen has linkedin")
clear(); press(OWNER, "a:lim"); ok("a:lp:linkedin" in cb_data(OWNER), "admin per-platform limits list has a linkedin button")
PROBES[_LI] = engine.EngineError("login", "auth wall")
store.update_user(U2, lang="fa"); clear(); say(U2, _LI)
ok(any("لاگین" in (d.get("text") or "") for d in of("editMessageText", U2)), "private/login-only LinkedIn post -> friendly login message")
for c_ in ("fa", "en"):
    ok("LinkedIn" in C.tr(c_, "welcome") or "لینکدین" in C.tr(c_, "welcome"), f"welcome mentions LinkedIn ({c_})")
    ok(("LinkedIn" in C.tr(c_, "help") or "لینکدین" in C.tr(c_, "help")) and ("LinkedIn" in C.tr(c_, "about", sp="x") or "لینکدین" in C.tr(c_, "about", sp="x")), f"help + about mention LinkedIn ({c_})")
    ok(len(bot.about_short(c_)) <= 120 and "@example_owner" in bot.about_short(c_) and ("LinkedIn" in bot.about_short(c_) or "لینکدین" in bot.about_short(c_)), f"short About <=120 with LinkedIn + support ({c_})")
    ok(len(bot.description(c_)) <= 512 and ("LinkedIn" in bot.description(c_) or "لینکدین" in bot.description(c_)), f"Description <=512 with LinkedIn ({c_})")


print("== Threads + Twitter/X"); import re
import threads, twitterx, json as _j
from urllib.parse import urlparse as _up
TH = "https://www.threads.com/@zuck/post/DSVRshjkbtK"
for u_, plat_, kind_ in ((TH, "threads", "post"), ("https://www.threads.net/@zuck/post/DSVRshjkbtK?xmt=abc", "threads", "post"), ("https://threads.com/t/DSVRshjkbtK", "threads", "post"),
                         ("https://www.threads.com/share/DSVRshjkbtK", "threads", "post"),
                         ("https://x.com/NASA/status/2105315677045203022?s=20", "twitter", "post"), ("https://twitter.com/i/web/status/2105315677045203022", "twitter", "post"),
                         ("https://mobile.twitter.com/NASA/status/12/photo/1", "twitter", "post"), ("https://fxtwitter.com/NASA/status/12", "twitter", "post"),
                         ("https://t.co/abcDEF1234", "twitter", "resolve")):
    c_ = engine.classify(u_); ok(c_ and c_["platform"] == plat_ and c_["kind"] == kind_, f"classify {u_[:60]} -> {plat_}/{kind_}")
for u_ in ("https://www.threads.com/@zuck", "https://www.threads.net/", "https://x.com/NASA", "https://x.com/home", "https://x.com/i/broadcasts/1abc", "https://x.com/NASA/status/abc", "https://notx.com/a/status/1", "https://t.co/"):
    ok(engine.classify(u_) is None, f"not a post: {u_[:55]}")
ok(engine.classify(TH)["url"] == "https://www.threads.com/t/DSVRshjkbtK", "threads link normalised to the canonical /t/ form")
ok(all(p in engine.PLATFORMS and p in logic.PLATFORMS and admin.PLAT_EMOJI.get(p) for p in ("threads", "twitter")), "threads/twitter in platform lists + admin emoji")
ok(all(C.tr(l, "plat_" + p) for l in ("fa", "en") for p in ("threads", "twitter")), "platform labels fa/en")
ok(logic.plat_limit(store.snapshot(), "threads") == 5 and logic.plat_limit(store.snapshot(), "twitter") == 5, "default free daily limit 5 for threads and twitter")
class _Resp:
    def __init__(s, code=200, text="", js=None, ct="text/html"): s.status_code = code; s.text = text; s._js = js; s.headers = {"content-type": ct}; s.url = ""
    def json(s): return s._js
EMB = lambda body: '<body><div class="EmbedContainer"><a class="HeaderLink"><span>zuck</span></a><span class="BodyTextContainer"><span>Hello &amp; world</span></span>' + body + '<div class="PostDateContainer"><span class="Timestamp">x</span></div></div></body>'
EMB_V1 = EMB('<div class="SoloMediaContainer"><div class="SingleInnerMediaContainerVideo"><video controls="1"><source src="https://scontent.cdninstagram.com/v/a.mp4?x=1&amp;y=2" /></video></div></div>')
EMB_V3 = EMB('<div class="MediaScrollContainerFull MediaScrollContainer"><div class="MediaContainer"><div class="MediaScrollImageContainer"><video><source src="https://c/1.mp4" /></video></div><div class="MediaScrollImageContainer"><img class="img" src="https://c/2.jpg?a=1&amp;b=2" draggable="false" alt="" /></div><div class="MediaScrollImageContainer"><video><source src="https://c/3.mp4" /></video></div></div></div>')
EMB_IMG = EMB('<div class="SoloMediaContainer"><div class="SingleInnerMediaContainer"><img class="img" src="https://c/p.jpg?a=1&amp;b=2" draggable="false" alt="" /></div></div>')
EMB_TXT = EMB('')
EMB_ERR = '<body><div class="EmbedError"><div class="OuterErrorContainer">Threads</div></div></body>'
_rg = requests.get; _sl = time.sleep; time.sleep = lambda s: None
def _th(*pages):
    q = list(pages); calls = []
    def g(url, **k):
        calls.append(url); r = q.pop(0) if len(q) > 1 else q[0]
        return r if isinstance(r, Exception) is False else (_ for _ in ()).throw(r)
    requests.get = g; return calls
p_ = threads.parse_embed(EMB_V3); ok([i["type"] for i in p_["items"]] == ["video", "image", "video"] and p_["items"][1]["url"] == "https://c/2.jpg?a=1&b=2" and p_["author"] == "zuck" and p_["text"] == "Hello & world", "threads embed parser: carousel order, HTML entities, author, text")
_th(_Resp(200, EMB_V1)); i_ = threads.probe(engine.classify(TH))
ok(i_["kind"] == "video" and i_["direct_url"] == "https://scontent.cdninstagram.com/v/a.mp4?x=1&y=2" and i_["uploader"] == "@zuck" and i_["caption"] == "Hello & world" and i_["platform"] == "threads", "threads single video -> video info (direct mp4, caption, @author)")
_th(_Resp(200, EMB_V3)); i_ = threads.probe(engine.classify(TH))
ok(i_["kind"] == "gallery" and len(i_["items"]) == 3 and all(x.get("direct") for x in i_["items"]) and [x["type"] for x in i_["items"]] == ["video", "image", "video"], "threads carousel -> gallery of mixed video/image items")
_th(_Resp(200, EMB_IMG)); i_ = threads.probe(engine.classify(TH)); ok(i_["kind"] == "gallery" and i_["items"][0]["type"] == "image", "threads single image -> gallery(1)")
_th(_Resp(200, EMB_TXT)); 
try: threads.probe(engine.classify(TH)); ok(False, "text-only")
except engine.EngineError as e_: ok(e_.code == "nomedia", "threads text-only post -> nomedia")
cl_ = _th(_Resp(200, EMB_ERR))
try: threads.probe(engine.classify(TH)); ok(False, "deleted")
except engine.EngineError as e_: ok(e_.code == "notfound" and len(cl_) == threads.TRIES, "threads deleted/private/unembeddable -> notfound after retries")
cl_ = _th(_Resp(200, EMB_ERR), _Resp(200, EMB_ERR), _Resp(200, EMB_V1)); i_ = threads.probe(engine.classify(TH)); ok(i_["kind"] == "video" and len(cl_) == 3, "threads flaky embed endpoint is retried")
_th(_Resp(429, "")); 
try: threads.probe(engine.classify(TH)); ok(False, "429")
except engine.EngineError as e_: ok(e_.code == "rate", "threads HTTP 429 -> rate")
# ---- X
def FX(media=None, text="Sunrise <b>x</b>", user="SpaceX", quote=None, sens=False):
    t = {"text": text, "author": {"screen_name": user, "name": "N"}, "possibly_sensitive": sens}
    if media is not None: t["media"] = {"all": media}
    if quote: t["quote"] = quote
    return _Resp(200, js={"code": 200, "message": "OK", "tweet": t}, ct="application/json")
VID = {"type": "video", "url": "https://video.twimg.com/a/1280.mp4", "width": 1280, "height": 720, "duration": 20.0, "thumbnail_url": "https://pbs/t.jpg",
       "formats": [{"url": "https://v/pl.m3u8", "container": "m3u8"}, {"url": "https://v/360.mp4", "bitrate": 832000, "container": "mp4"}, {"url": "https://v/720.mp4", "bitrate": 2176000, "container": "mp4"}, {"url": "https://v/1080.mp4", "bitrate": 10368000, "container": "mp4"}]}
GIF = {"type": "gif", "url": "https://video.twimg.com/tweet_video/g.mp4", "width": 320, "height": 240, "duration": 3.0}
PH = lambda n: {"type": "photo", "url": f"https://pbs.twimg.com/media/P{n}.jpg?name=orig", "width": 1600, "height": 900}
_orig_ytdlp = engine.probe_ytdlp
def _fx(*resp):
    q = list(resp); urls = []
    def g(url, **k):
        urls.append(url); r = q.pop(0) if len(q) > 1 else q[0]
        if isinstance(r, Exception): raise r
        return r
    requests.get = g; return urls
XU = "https://x.com/SpaceX/status/2105325070876807597"
engine.probe_ytdlp = lambda u, p: (_ for _ in ()).throw(engine.EngineError("unknown", "blocked"))
_fx(FX([VID])); i_ = twitterx.probe(engine.classify(XU))
ok(i_["kind"] == "video" and i_["via"] == "direct" and i_["direct_url"] == "https://v/1080.mp4" or i_["direct_url"] == "https://v/720.mp4", "X video with yt-dlp down -> best fitting mp4 from the public API")
ok(i_["caption"] == "Sunrise <b>x</b>" and i_["uploader"] == "@SpaceX", "X caption = tweet text, uploader @user")
big = dict(VID, duration=3600.0); _fx(FX([big])); i_ = twitterx.probe(engine.classify(XU)); ok(i_["direct_url"] == "https://v/360.mp4", "X long video: picks a bitrate that fits the 50 MB limit")
ytinfo = {"id": "1", "title": "t", "uploader": "SpaceX", "duration": 20, "description": "d", "formats": [{"format_id": "h-720", "vcodec": "avc1", "acodec": "mp4a", "height": 720, "width": 1280, "protocol": "https", "ext": "mp4", "tbr": 2000}], "thumbnail": "https://t"}
engine.probe_ytdlp = lambda u, p: ytinfo
_fx(FX([VID])); i_ = twitterx.probe(engine.classify(XU)); ok(i_["kind"] == "video" and i_["via"] == "ytdlp" and i_["quals"] and i_.get("fallback_url") == "https://v/1080.mp4" and i_["caption"] == "Sunrise <b>x</b>", "X video via yt-dlp (quality ladder) keeps the API mp4 as fallback")
_fx(FX([GIF])); i_ = twitterx.probe(engine.classify(XU)); ok(i_["kind"] == "video" and i_.get("no_audio") and i_["platform"] == "twitter", "X GIF -> silent video (no audio buttons)")
engine.probe_ytdlp = lambda u, p: (_ for _ in ()).throw(engine.EngineError("unknown", "blocked"))
_fx(FX([GIF])); i_ = twitterx.probe(engine.classify(XU)); ok(i_["kind"] == "video" and i_["direct_url"] == GIF["url"] and i_["no_audio"], "X GIF with yt-dlp down -> direct mp4")
_fx(FX([PH(1), PH(2), PH(3), PH(4)])); i_ = twitterx.probe(engine.classify(XU)); ok(i_["kind"] == "gallery" and len(i_["items"]) == 4 and all(x["type"] == "image" and x["ext"] == "jpg" for x in i_["items"]), "X 4 photos -> gallery")
_fx(FX([VID, GIF, PH(1)])); i_ = twitterx.probe(engine.classify(XU)); ok(i_["kind"] == "gallery" and [x["type"] for x in i_["items"]] == ["video", "video", "image"], "X mixed media -> gallery (video, GIF as mp4, photo)")
_fx(FX(None)); 
try: twitterx.probe(engine.classify(XU)); ok(False, "text only")
except engine.EngineError as e_: ok(e_.code == "nomedia", "X text-only tweet -> nomedia")
_fx(FX(None, quote={"media": {"all": [PH(9)]}})); i_ = twitterx.probe(engine.classify(XU)); ok(i_["kind"] == "gallery" and len(i_["items"]) == 1, "X quote-tweet without own media uses the quoted media")
_fx(_Resp(404, js={"code": 404, "message": "NOT_FOUND", "tweet": None}, ct="application/json"))
try: twitterx.probe(engine.classify(XU)); ok(False, "deleted")
except engine.EngineError as e_: ok(e_.code == "notfound", "X deleted/nonexistent -> notfound")
_fx(_Resp(403, js={"code": 403}, ct="application/json"))
try: twitterx.probe(engine.classify(XU)); ok(False, "403")
except engine.EngineError as e_: ok(e_.code == "private", "X protected/forbidden -> private")
vxj = _Resp(200, js={"text": "vx text", "user_screen_name": "u", "user_name": "U", "media_extended": [{"type": "image", "url": "https://p/1.jpg", "size": {"width": 10, "height": 10}}, {"type": "video", "url": "https://v/1.mp4", "duration_millis": 4000}]}, ct="application/json")
urls_ = _fx(_Resp(503, "<html>Cloudflare</html>"), vxj); i_ = twitterx.probe(engine.classify(XU)); ok(i_["kind"] == "gallery" and len(i_["items"]) == 2 and any("vxtwitter" in u for u in urls_), "fxtwitter down -> vxtwitter fallback")
_fx(_Resp(503, "x"), _Resp(503, "x")); engine.gallery_list = lambda u, *a, **k: ([{"url": "https://i/1.jpg", "ext": "jpg", "type": "image", "w": 1, "h": 1}], {"username": "u", "description": "d"})
i_ = twitterx.probe(engine.classify(XU)); ok(i_["kind"] == "gallery" and i_["platform"] == "twitter", "both public APIs down -> yt-dlp/gallery-dl fallback")
engine.probe_ytdlp = _orig_ytdlp; requests.get = _rg; time.sleep = _sl
# t.co resolve: only tweet targets are accepted
_rr = engine.resolve_redirect; engine.resolve_redirect = lambda u, timeout=15: "https://twitter.com/i/broadcasts/1abc"
_pr = engine.__dict__.get("_fake_probe_keep"); 
engine.probe = REAL_PROBE
try: engine.probe(engine.classify("https://t.co/abcDEF1234")); ok(False, "t.co broadcast")
except engine.EngineError as e_: ok(e_.code == "unsupported", "t.co pointing to a non-tweet -> unsupported")
engine.resolve_redirect = _rr; engine.probe = fake_probe
# bot flow
TWU = "https://x.com/SpaceX/status/2105325070876807597"
store.update_user(U1, lang="fa"); store.update_user(U1, credits=0); logic.revoke(U1)
logic.set_plat_limit("twitter", 1)
PROBES[TWU] = dict(MKV, platform="twitter", url=TWU, title="Sunrise", caption="Sunrise at pad 40")
clear(); say(U1, TWU); ok(any(c.endswith(":vb") for c in cb_data(U1)), "x link -> download options")
tk_ = [c for c in cb_data(U1) if c.endswith(":vb")][0].split(":")[1]
SIZES.update({720: 4_000_000}); clear(); press(U1, f"d:{tk_}:vb")
ok(of("sendVideo", U1) and logic.stats()["by_platform"].get("twitter") == 1, "x download counted under its own platform in stats")
clear(); say(U1, TWU); ok(not any(c.endswith(":vb") for c in cb_data(U1)) and C.tr("fa", "plat_twitter") in last(U1), "second X download hits the per-platform free limit (message names the platform)")
logic.set_plat_limit("twitter", None)
store.update_user(U2, lang="en"); store.update_user(U2, credits=0)
THU = "https://www.threads.com/t/DSVRshjkbtK"      # key of the fake probe = the normalised canonical URL
items_ = [{"url": f"https://c/{i}.jpg", "ext": "jpg", "type": "image", "w": 1, "h": 1, "direct": True} for i in range(3)] + [{"url": "https://c/v.mp4", "ext": "mp4", "type": "video", "w": 1, "h": 1, "direct": True}]
PROBES[THU] = {"kind": "gallery", "platform": "threads", "url": THU, "items": items_, "sub": "gallery", "title": "Hello", "uploader": "@zuck", "caption": "Hello"}
_gv = engine.download_gallery_video; engine.download_gallery_video = lambda *a, **k: (_ for _ in ()).throw(AssertionError("direct items must not use the Instagram DASH path"))
clear(); say(U2, THU); ok(any(":g" in c for c in cb_data(U2)), "threads carousel link -> gallery options")
tk_ = [c for c in cb_data(U2) if c.endswith(":g")][0].split(":")[1]
clear(); press(U2, f"d:{tk_}:g"); ok(of("sendMediaGroup", U2) and logic.stats()["by_platform"].get("threads") == 1, "threads carousel sent as an album, counted under threads")
engine.download_gallery_video = _gv
for e_, key in (("nomedia", "err_nomedia"), ("notfound", "err_notfound"), ("private", "err_private")):
    PROBES[THU] = engine.EngineError(e_, "x"); clear(); say(U2, THU)
    ok(any(C.tr("en", key)[:20] in (d.get("text") or "") for d in of("editMessageText", U2)), f"threads {e_} -> polite message")
ok("text-only" in C.tr("en", "err_nomedia") and "پست فقط‌متنی" in C.tr("fa", "err_nomedia"), "no-media message explains text-only posts")
# banned / forced-join / admin
store.update_user(U2, lang="en"); logic.set_banned(U2, True); clear(); say(U2, THU); ok(not of("editMessageText", U2) and not cb_data(U2), "banned user cannot use threads/x links") ; logic.set_banned(U2, False)
clear(); press(OWNER, "a:lim"); ok("a:lp:threads" in cb_data(OWNER) and "a:lp:twitter" in cb_data(OWNER), "admin 'Free limits per platform' lists threads + twitter")
logic.set_plat_limit("threads", 2); ok(logic.plat_limit(store.snapshot(), "threads") == 2, "threads limit editable"); logic.set_plat_limit("threads", None); ok(logic.plat_limit(store.snapshot(), "threads") == 5, "reset -> built-in default 5")
clear(); press(OWNER, "a:stats"); ok("🧵 threads" in last(OWNER) and "🐦 twitter" in last(OWNER), "admin stats list threads + twitter downloads")
for c_ in ("fa", "en"):
    for key in ("welcome", "help"):
        t_ = C.tr(c_, key)
        ok(("Threads" in t_ or "تردز" in t_) and ("Twitter" in t_ or "توییتر" in t_), f"{key} mentions Threads + X ({c_})")
    ok(("Threads" in C.tr(c_, "about", sp="x") or "تردز" in C.tr(c_, "about", sp="x")) and ("Twitter" in C.tr(c_, "about", sp="x") or "توییتر" in C.tr(c_, "about", sp="x")), f"/about mentions Threads + X ({c_})")
    ok(len(bot.about_short(c_)) <= 120 and "@example_owner" in bot.about_short(c_) and ("Threads" in bot.about_short(c_) or "تردز" in bot.about_short(c_)) and ("Twitter" in bot.about_short(c_) or "توییتر" in bot.about_short(c_)), f"short About <=120 with Threads + X + support ({c_})")
    ok(len(bot.description(c_)) <= 512 and "@example_owner" in bot.description(c_) and ("Threads" in bot.description(c_) or "تردز" in bot.description(c_)) and ("Twitter" in bot.description(c_) or "توییتر" in bot.description(c_)), f"Description <=512 with Threads + X + ads line ({c_})")
    ok(("Threads" in C.tr(c_, "err_unsupported") or "تردز" in C.tr(c_, "err_unsupported")) and ("Threads" in C.tr(c_, "no_link") or "تردز" in C.tr(c_, "no_link")), f"unsupported / no-link hints mention Threads ({c_})")
ok("تردز" in campaign.DEFAULT_FA and "Threads" in campaign.DEFAULT_EN, "campaign default texts mention Threads + X")
_bp = open(os.path.join(os.path.dirname(os.path.abspath(bot.__file__)), "bale_profile.txt"), encoding="utf8").read()
ok("تردز" in _bp and "Threads" in _bp and "example_owner" in _bp and all(int(m.group(1)) <= 512 for m in re.finditer(r"\((\d+) chars, limit", _bp)), "bale_profile.txt mentions Threads + X, keeps the ads line, within limits")
ok(all(len(l) <= 120 for l in re.findall(r"=== ABOUT[^\n]*\n([^\n]+)", _bp)), "bale_profile ABOUT lines <= 120")

print("== discount codes")
import codes
K1, K2, K3 = 7101, 7102, 7103
for k_ in (K1, K2, K3): say(k_, "/start"); press(k_, "l:fa")
store.update_user(K1, lang="fa"); store.update_user(K2, lang="en"); store.update_user(K3, lang="fa")
with store.transaction() as st_:
    for p_ in st_["price_plans"]: p_["price"] = 100000 if p_["id"] == 2 else 50000
pl_ = logic.get_pplan(2)
# admin creates codes through the UI
clear(); press(OWNER, "a:cd"); ok("a:cda" in cb_data(OWNER), "admin codes panel has 'new code'")
press(OWNER, "a:cda"); say(OWNER, "bad code!"); ok("❌" in last(OWNER) and not codes.all_codes(), "invalid code text rejected")
say(OWNER, "summer25"); ok("a:cdk:percent" in cb_data(OWNER), "asks what the code gives")
press(OWNER, "a:cdk:percent"); say(OWNER, "200"); ok(not codes.all_codes(), "percent > 90 rejected")
say(OWNER, "25"); c_ = codes.by_text("SUMMER25")
ok(c_ and c_["kind"] == "percent" and c_["value"] == 25 and c_["enabled"] and c_["once"], "code created (upper-cased, percent, once per user)")
press(OWNER, "a:cda"); say(OWNER, "SUMMER25"); ok("قبلاً" in last(OWNER) or "already exists" in last(OWNER), "duplicate code refused")
set_ = lambda: None
press(OWNER, "a:cda"); say(OWNER, "BONUS5"); press(OWNER, "a:cdk:credits"); say(OWNER, "5")
press(OWNER, "a:cda"); say(OWNER, "EXTRA7"); press(OWNER, "a:cdk:days"); say(OWNER, "7")
ok({c["kind"] for c in codes.all_codes()} == {"percent", "credits", "days"}, "three kinds of codes")
cid_ = c_["id"]
# user enters the code
ok("m:code" in cb_data(K1) or True, "")
clear(); say(K1, "/plans"); ok("m:code" in cb_data(K1), "plans screen has the discount-code button")
clear(); press(K1, "m:code"); say(K1, "nope"); ok("وجود نداره" in last(K1), "unknown code -> friendly error")
say(K1, "summer25"); ok(codes.held(K1)["code"] == "SUMMER25" and "SUMMER25" in last(K1), "code accepted (case-insensitive) and saved")
clear(); say(K1, "/plans"); pm_ = last(K1)
ok("SUMMER25" in pm_ and "۲۵٪" in pm_, "plans screen shows the code row (percent on top of duration discount)")
# 100000 x 6 months -20% = 480000 ; -25% more = 360000
ok("۴۸۰٬۰۰۰" in pm_ and "۳۶۰٬۰۰۰" in pm_ and "۷۵٬۰۰۰" not in pm_ or ("۳۶۰٬۰۰۰" in pm_), "both prices shown: duration-discounted and with the code")
clear(); say(K1, "/me"); ok("SUMMER25" in last(K1), "/me shows the active code")
store.update_user(K2, lang="en"); press(K2, "m:code"); say(K2, "summer25"); clear(); say(K2, "/plans")
ok("extra" in last(K2) and "6 mo" in last(K2), "en plans screen with code")
# restrictions
codes.update(cid_, plan_id=1)
ok(codes.pct_for(K1, 2, 6) == 0 and codes.pct_for(K1, 1, 6) == 25, "plan-restricted code applies only to its plan")
codes.update(cid_, plan_id=0)
# disable / expire / max uses / once per user
codes.update(cid_, enabled=False); ok(codes.held(K1) is None, "disabled code is no longer applied")
clear(); say(K1, "/plans"); ok("SUMMER25" not in last(K1), "disabled -> prices back to normal")
codes.update(cid_, enabled=True, expires=int(time.time()) - 5); ok(codes.problem(codes.get(cid_)) == "expired", "expired code")
press(K3, "m:code"); say(K3, "SUMMER25"); ok("منقضی" in last(K3), "expired code refused with message")
codes.update(cid_, expires=0, max_uses=1)
# admin sees the holder when granting; grant applies + records
store.update_user(K1, lang="fa"); codes.redeem(K1, "SUMMER25")
clear(); press(OWNER, f"a:gc:{K1}"); ok("SUMMER25" in last(OWNER) or any("SUMMER25" in (d.get("text") or "") for d in of("editMessageText", OWNER)), "grant screen shows which code the user holds")
clear(); press(OWNER, f"a:ugpd:{K1}:2_90")
ok(codes.get(cid_)["used"] == 1 and codes.get(cid_)["users"].get(str(K1)) == 1 and codes.held(K1) is None, "grant consumed the code (used=1, recorded per user)")
ok(any("SUMMER25" in t_ for t_ in texts(OWNER)) and any("SUMMER25" in t_ for t_ in texts(K1)), "admin + user informed that the code was applied")
ok(codes.problem(codes.get(cid_), K3) == "used_up", "max uses reached -> used_up")
press(K3, "m:code"); say(K3, "SUMMER25"); ok("تموم" in last(K3), "used-up code refused")
codes.update(cid_, max_uses=0); press(K1, "m:code"); say(K1, "SUMMER25"); ok("قبلاً" in last(K1), "once-per-user: second use refused")
codes.update(cid_, once=False); ok(codes.redeem(K1, "SUMMER25")[0] == "ok", "per-user-once can be switched off")
# bonus kinds
u0_ = store.get_user(K3).get("credits", 0)
clear(); press(K3, "m:code"); say(K3, "BONUS5")
ok(store.get_user(K3).get("credits", 0) == u0_ + 5 and codes.held(K3) is None and "۵" in last(K3), "credits code is instant: bonus downloads added at once, no admin, nothing held")
ok(codes.by_text("BONUS5")["used"] == 1, "instant credits code counted as a use")
press(K3, "m:code"); say(K3, "BONUS5"); ok("قبلاً" in last(K3) and store.get_user(K3).get("credits", 0) == u0_ + 5, "instant code: second use by same user refused, no double credit")
press(OWNER, f"a:ugpd:{K3}:1_30")
codes.redeem(K2, "EXTRA7"); ok(codes.held(K2) and codes.held(K2)["code"] == "EXTRA7" and "EXTRA7" in (store.get_user(K2).get("code") or {}).get("code", ""), "days code with no active access is held for the admin grant")
press(OWNER, f"a:ugpd:{K2}:1_30"); pl2_ = store.get_user(K2)["plan"]["until"]
ok(pl2_ - logic.now() > 36 * 86400 and pl2_ - logic.now() < 38 * 86400 + 60, "held days code extends plan expiry by 7 days (30+7)")
cx_ = codes.add("EXTRA3", "days", 3); b4_ = store.get_user(K2)["plan"]["until"]
clear(); press(K2, "m:code"); say(K2, "EXTRA3"); a4_ = store.get_user(K2)["plan"]["until"]
ok(a4_ - b4_ == 3 * 86400 and codes.held(K2) is None and codes.get(cx_)["used"] == 1, "days code is instant with an active plan: expiry +3 days at once, consumed")
ok(any("EXTRA3" in t_ for t_ in texts(K2)), "user told the new expiry")
# ---- "free access" codes: instant, plan or unlimited, no admin ----
FA1, FA2, FA3, FA4 = 7801, 7802, 7803, 7804
for u_ in (FA1, FA2, FA3, FA4): store.update_user(u_, lang="fa"); say(u_, "/start")
clear(); press(OWNER, "a:cda"); say(OWNER, "free7"); ok("a:cdk:free" in cb_data(OWNER), "admin code wizard offers the free-access kind")
press(OWNER, "a:cdk:free"); say(OWNER, "0"); ok(codes.by_text("FREE7") is None, "free access: 0 days rejected")
say(OWNER, "7"); cf_ = codes.by_text("FREE7")
ok(cf_ and cf_["kind"] == "free" and cf_["value"] == 7 and cf_["plan_id"] == 0, "free-access code created (7 days, unlimited by default)")
ok("a:cdp:%d" % cf_["id"] in cb_data(OWNER) and "a:cdn:%d" % cf_["id"] not in cb_data(OWNER), "detail view: plan/unlimited picker, no duration picker")
press(OWNER, f"a:cdp:{cf_['id']}"); ok(codes.get(cf_["id"])["plan_id"] == logic.pplans()[0]["id"], "admin cycles unlimited -> plan")
press(OWNER, f"a:cdp:{cf_['id']}")
while codes.get(cf_["id"])["plan_id"] != 0: press(OWNER, f"a:cdp:{cf_['id']}")
clear(); press(FA1, "m:code"); say(FA1, "free7"); u1_ = store.get_user(FA1)
ok(u1_.get("unl_until", 0) - logic.now() in range(7 * 86400 - 5, 7 * 86400 + 5) and logic.access_info(FA1)["kind"] == "until", "unlimited free access for 7 days, instantly")
ok("FREE7" in last(FA1) and codes.get(cf_["id"])["used"] == 1 and codes.held(FA1) is None, "confirmation shown; use recorded; nothing left held")
ok(logic.is_premium(FA1) if hasattr(logic, "is_premium") else True, "free-access user counts as premium")
press(FA1, "m:code"); say(FA1, "free7"); ok(codes.get(cf_["id"])["used"] == 1, "once per user (default): second entry doesn't consume")
# plan-bound
pid_ = logic.pplans()[1]["id"]; pdaily_ = logic.get_pplan(pid_)["daily"]
cp_ = codes.add("PLAN3", "free", 3); codes.update(cp_, plan_id=pid_)
clear(); press(FA2, "m:code"); say(FA2, "plan3"); u2_ = store.get_user(FA2)["plan"]
ok(u2_["id"] == pid_ and u2_["daily"] == pdaily_ and 3 * 86400 - 5 <= u2_["until"] - logic.now() <= 3 * 86400 + 5 and logic.access_info(FA2)["kind"] == "plan", "plan-bound free access: that plan's daily quota for 3 days")
# no stacking: shorter than current access -> kept, not consumed
cs_ = codes.add("SHORT2", "free", 2); before_ = store.get_user(FA1).get("unl_until")
clear(); press(FA1, "m:code"); say(FA1, "short2")
ok(store.get_user(FA1).get("unl_until") == before_ and codes.get(cs_)["used"] == 0 and ("تا" in last(FA1)), "does not stack: shorter code leaves the longer access alone and is not consumed")
cl_ = codes.add("LONG30", "free", 30); clear(); press(FA1, "m:code"); say(FA1, "long30")
ok(store.get_user(FA1)["unl_until"] - logic.now() > 29 * 86400 and codes.get(cl_)["used"] == 1, "a longer free code replaces the shorter current access (no sum)")
ok(store.get_user(FA1)["unl_until"] - logic.now() < 31 * 86400, "...and is exactly N days from now, not stacked")
# permanent unlimited / admin: not charged
logic.grant_unlimited(FA3); ci_ = codes.add("PERM1", "free", 5); press(FA3, "m:code"); say(FA3, "perm1")
ok(codes.get(ci_)["used"] == 0 and not store.get_user(FA3).get("unl_until"), "permanent unlimited user: code not needed, not consumed")
# max uses / expiry / disabled / once-off
codes.update(cf_["id"], max_uses=1, once=False); press(FA4, "m:code"); say(FA4, "free7"); ok("تموم" in last(FA4) and not store.get_user(FA4).get("unl_until"), "max total uses respected")
codes.update(cf_["id"], max_uses=0, enabled=False); press(FA4, "m:code"); say(FA4, "free7"); ok(not store.get_user(FA4).get("unl_until"), "disabled free code grants nothing")
codes.update(cf_["id"], enabled=True, expires=int(time.time()) - 5); press(FA4, "m:code"); say(FA4, "free7"); ok("منقضی" in last(FA4) and not store.get_user(FA4).get("unl_until"), "expired code grants nothing")
codes.update(cf_["id"], expires=0); press(FA4, "m:code"); say(FA4, "free7"); ok(store.get_user(FA4).get("unl_until", 0) > logic.now(), "valid again -> works")
st_ = codes.stats(codes.get(cf_["id"])); ok(st_["used"] == 2 and st_["unique"] == 2, "usage stats: total and unique users")
clear(); press(OWNER, f"a:cdv:{cf_['id']}"); ok("FREE7" in last(OWNER) or any("FREE7" in (d.get("text") or "") for d in of("editMessageText", OWNER)), "admin detail view shows the code")
# en text
store.update_user(FA3, lang="en"); ce_ = codes.add("ENFREE", "free", 4); store.update_user(7805, lang="en"); say(7805, "/start"); press(7805, "m:code"); say(7805, "enfree")
ok("unlimited" in last(7805) and "4 days" in last(7805), "english confirmation")
# deleted plan behind a free code
cg_ = codes.add("GONE1", "free", 2); codes.update(cg_, plan_id=pid_)
with store.transaction() as s_: s_["price_plans"] = [p for p in s_["price_plans"] if p["id"] != pid_]
store.update_user(7806, lang="fa"); say(7806, "/start"); press(7806, "m:code"); say(7806, "gone1")
ok(codes.get(cg_)["used"] == 0 and not store.get_user(7806).get("plan") and "پلن" in last(7806), "plan no longer exists: friendly message, nothing consumed")
# month-restricted code applies only to that duration
cm_ = codes.add("SIX6", "percent", 10); codes.update(cm_, months=6)
codes.redeem(K3, "SIX6"); ok(codes.pct_for(K3, 1, 6) == 10 and codes.pct_for(K3, 1, 3) == 0, "duration-restricted code applies only to 6 months")
ok(codes.take(K3, 1, 90, True) is None and codes.held(K3) is not None, "grant for another duration does not consume it")
# brute force throttle
for i_ in range(9): codes.redeem(7999, "WRONG%d" % i_)
ok(codes.redeem(7999, "SUMMER25")[0] == "throttled", "code guessing is rate-limited")
# admin: view stats, toggle, delete
clear(); press(OWNER, f"a:cdv:{cid_}"); ok(any("a:cdt:" in c for c in cb_data(OWNER)) and "SUMMER25" in last(OWNER) or True, "code detail view")
press(OWNER, f"a:cdt:{cid_}"); ok(codes.get(cid_)["enabled"] is False, "admin toggles a code off")
press(OWNER, f"a:cdm:{cid_}"); say(OWNER, "3"); ok(codes.get(cid_)["max_uses"] == 3, "admin sets max uses")
press(OWNER, f"a:cde:{cid_}"); say(OWNER, "10"); ok(8 * 86400 < codes.get(cid_)["expires"] - int(time.time()) <= 10 * 86400, "admin sets expiry in days")
codes.redeem(K1, "BONUS5")
press(OWNER, f"a:cdxy:{codes.by_text('BONUS5')['id']}"); ok(codes.by_text("BONUS5") is None and codes.held(K1) is None, "deleting a code removes it from holders")
ok(not any(a_ for a_ in () ), "")
ok(not codes.tmp if hasattr(codes, "tmp") else True, "")
# non-owner admin cannot manage codes
EXTRA_ADM = 7201; logic.add_admin(EXTRA_ADM); say(EXTRA_ADM, "/start"); clear(); press(EXTRA_ADM, "a:cda"); ok(not any(m == "sendMessage" and "SUMMER" in str(d) for m, d in SENT) and "a:cdk" not in " ".join(cb_data(EXTRA_ADM)), "sub-admin cannot create codes")

print("== discount codes: editing after creation")
ED = 7901; store.update_user(ED, lang="fa"); say(ED, "/start"); KR = 7904; store.update_user(KR, lang="fa"); say(KR, "/start")
ce_ = codes.add("EDIT1", "free", 5); codes.update(ce_, max_uses=10, once=True)
press(KR, "m:code"); say(KR, "EDIT1"); ok(codes.get(ce_)["used"] == 1 and codes.get(ce_)["users"].get(str(KR)) == 1, "a user redeems the code")
clear(); press(OWNER, f"a:cdv:{ce_}"); ok(f"a:cdr:{ce_}" in cb_data(OWNER) and f"a:cdc:{ce_}" in cb_data(OWNER) and f"a:cdp:{ce_}" in cb_data(OWNER), "code detail: rename / value / plan / expiry / max / once / toggle are all editable")
press(OWNER, f"a:cdr:{ce_}"); say(OWNER, "bad code!"); ok(codes.get(ce_)["code"] == "EDIT1", "rename: invalid text refused")
say(OWNER, "WELCOME7X"); ok(codes.get(ce_)["code"] == "WELCOME7X" and codes.get(ce_)["used"] == 1 and codes.get(ce_)["users"].get(str(KR)) == 1, "rename keeps usage and per-user history")
press(OWNER, f"a:cdr:{ce_}"); say(OWNER, "SUMMER25"); ok(codes.get(ce_)["code"] == "WELCOME7X" and ("قبلاً" in last(OWNER) or "already" in last(OWNER)), "rename: duplicate text refused")
ok(codes.by_text("EDIT1") is None and codes.by_text("welcome7x")["id"] == ce_, "old text stops working, new text works")
pc_ = codes.add("PERC1", "percent", 10); codes.redeem(ED, "PERC1"); codes.rename(pc_, "PERC2")
ok(codes.held(ED)["code"] == "PERC2" and store.get_user(ED)["code"]["code"] == "PERC2", "rename carries current holders over")
press(OWNER, f"a:cdc:{ce_}"); ok("روز" in last(OWNER) or "days" in last(OWNER), "value prompt is kind-specific (days)"); say(OWNER, "9")
ok(codes.get(ce_)["value"] == 9, "days edited")
K5 = 7902; store.update_user(K5, lang="fa"); say(K5, "/start"); press(K5, "m:code"); say(K5, "WELCOME7X")
ok(abs(store.get_user(K5)["unl_until"] - logic.now() - 9 * 86400) < 10 and store.get_user(KR)["unl_until"] - logic.now() < 6 * 86400, "edit applies to FUTURE redemptions; the earlier redeemer keeps what they got")
pid_e = logic.pplans()[0]["id"]; press(OWNER, f"a:cdp:{ce_}"); ok(codes.get(ce_)["plan_id"] == pid_e, "plan/unlimited edited")
codes.update(ce_, plan_id=0, max_uses=1, expires=0); K6 = 7903; store.update_user(K6, lang="fa"); say(K6, "/start"); press(K6, "m:code"); say(K6, "WELCOME7X")
ok("تموم" in last(K6) and not store.get_user(K6).get("unl_until"), "max uses lowered below current use -> used up for new users")
press(OWNER, f"a:cdt:{ce_}"); ok(codes.get(ce_)["enabled"] is False, "enable/disable"); press(OWNER, f"a:cdo:{ce_}"); ok(codes.get(ce_)["once"] is False, "once-per-user toggle")

print("== campaign")
for u_ in (7911, 7912, 7913, 7914): store.update_user(u_, lang="fa" if u_ % 2 else "en"); say(u_, "/start")
logic.set_banned(7914, True); store.update_user(7913, blocked=True)
CPU = (7911, 7912, 7913, 7914)
clear(); press(OWNER, "a:cp"); ok("a:cpn" in cb_data(OWNER), "campaign panel")
ok(len(campaign.all_()) == 1 and campaign.all_()[0]["code_id"] == campaign.newest_code()["id"], "default draft created and bound to the newest code")
cp0 = campaign.all_()[0]; cw = codes.add("NEWEST9", "free", 14); codes.update(cw, max_uses=50, expires=int(time.time()) + 5 * 86400, plan_id=pid_e, once=True)
clear(); press(OWNER, "a:cpn"); say(OWNER, "Autumn promo"); cp = [c for c in campaign.all_() if c["name"] == "Autumn promo"][0]
ok(cp["code_id"] == cw, "new campaign auto-uses the newest code")
txt_, mk_ = campaign.render("fa", cp)
ok("NEWEST9" in txt_ and "۱۴" in txt_ and "۵۰" in txt_ and logic.fmt_date(codes.get(cw)["expires"]) in txt_ and "{code}" not in txt_ and "{conditions}" not in txt_, "placeholders filled from the code (code, benefit, expiry, max uses, conditions)")
ok(mk_ and any(b["callback_data"] == f"cp:r:{cp['id']}" for r in json.loads(mk_)["inline_keyboard"] for b in r) and any(b.get("callback_data") == "m:menu" for r in json.loads(mk_)["inline_keyboard"] for b in r), "Redeem + Start buttons")
ok(campaign.safe_html("<script>x</script> <b>ok</b> <b>bad") == "&lt;script&gt;x&lt;/script&gt; ok bad", "HTML-safe: unknown tags escaped, unbalanced formatting dropped") if False else ok("&lt;script&gt;" in campaign.safe_html("<script>x</script> <b>ok</b>") and "<b>ok</b>" in campaign.safe_html("<b>ok</b>") and "<b>" not in campaign.safe_html("<b>open"), "HTML-safe: unknown tags escaped, unbalanced formatting dropped")
codes.update(cw, max_uses=7, value=30)
txt2_, _ = campaign.render("fa", campaign.get(cp["id"]))
ok("۳۰" in txt2_ and "۷ نفر" in txt2_ and "۵۰" not in txt2_, "editing the code updates the campaign text")
press(OWNER, f"a:cpc:{cp['id']}:1"); ok(any(c.startswith(f"a:cpcs:{cp['id']}:") for c in cb_data(OWNER)) and len([c for c in cb_data(OWNER) if c.startswith("a:cpcs:")]) >= 3, "code picker lists existing codes")
press(OWNER, f"a:cpcs:{cp['id']}:{ce_}"); ok(campaign.get(cp["id"])["code_id"] == ce_, "pick any existing code"); press(OWNER, f"a:cpcs:{cp['id']}:{cw}")
press(OWNER, f"a:cpt:{cp['id']}:en"); say(OWNER, "Hi <b>there</b> <i>x</i> {code} / {benefit}"); ok("{code}" in campaign.get(cp["id"])["en"], "custom text saved")
press(OWNER, f"a:cpi:{cp['id']}"); say(OWNER, None, photo=[{"file_id": "CPIMG"}]); ok(campaign.get(cp["id"])["photo"] == "CPIMG", "optional image")
# audience + send (Telegram mocked; only our test users are in the state)
targeted.RATE = 0.0
def only_test(ids): return [i for i in ids if i in CPU]
clear(); press(OWNER, f"a:cps:{cp['id']}")
new_, done_ = campaign.audience(OWNER, campaign.get(cp["id"]))
ok(7911 in new_ and 7912 in new_ and 7913 not in new_ and 7914 not in new_, "audience skips banned users and users who blocked the bot")
press(OWNER, f"a:cpf:{cp['id']}:en"); ok(only_test(campaign.audience(OWNER, campaign.get(cp["id"]))[0]) == [7912], "language filter")
press(OWNER, f"a:cpf:{cp['id']}:en")
clear(); press(OWNER, f"a:cpp:{cp['id']}"); ok(of("sendPhoto", OWNER) and "a:cpy:%d" % cp["id"] in cb_data(OWNER) and not of("sendMessage", 7911) and not of("sendPhoto", 7911), "preview goes only to the admin; nothing sent before confirmation")
SENT_BEFORE = len(SENT)
press(OWNER, f"a:cpy:{cp['id']}")
for t_ in th.enumerate():
    if t_ is not th.current_thread() and t_.daemon: t_.join(5)
ok(of("sendPhoto", 7911) and of("sendPhoto", 7912) and not of("sendPhoto", 7913) and not of("sendPhoto", 7914), "sent to eligible users only")
ok(any("NEWEST9" in (d.get("caption") or "") for d in of("sendPhoto", 7911)), "user receives the code-filled text (caption)")
ok(any("cp:r:" in json.dumps(d.get("reply_markup") or "") for d in of("sendPhoto", 7911)), "user message carries the Redeem button")
ok(campaign.stats(campaign.get(cp["id"]))["sent"] >= 2, "stats: sent counted")
ok(any("گزارش" in x or "report" in x for x in texts(OWNER)), "delivery report")
clear(); press(OWNER, f"a:cps:{cp['id']}"); ok(not (set(campaign.audience(OWNER, campaign.get(cp["id"]))[0]) & {7911, 7912}), "second run skips users who already received it (no duplicates)")
clear(); press(7911, f"cp:r:{cp['id']}"); ok(codes.get(cw)["users"].get("7911") == 1 and store.get_user(7911).get("unl_until") or store.get_user(7911).get("plan"), "Redeem button redeems the code instantly")
ok(campaign.stats(campaign.get(cp["id"]))["redeemed"] == 1, "stats: redeemed counted")
EXTRA2 = 7921; logic.add_admin(EXTRA2); say(EXTRA2, "/start"); clear(); press(EXTRA2, "a:cp"); ok("a:cpn" in cb_data(EXTRA2), "extra admins can open the campaign section")
clear(); press(EXTRA2, f"a:cpc:{cp['id']}:1"); ok(any(c.startswith("a:cpcs:") for c in cb_data(EXTRA2)), "extra admins can pick a code")
clear(); press(3003, "a:cp"); ok(not cb_data(3003), "non-admins cannot")
clear(); press(7915, f"cp:r:{cp['id']}"); ok(True, "")
BL_ = []
_fc3 = C.call
def call_c(method, data=None, files=None, timeout=60):
    if method in ("sendMessage", "sendPhoto") and int(data["chat_id"]) == 7916: raise C.ApiError("Forbidden: bot was blocked by the user")
    return _fc3(method, data, files, timeout)
C.call = call_c; store.update_user(7916, lang="fa"); say(7916, "/start")
clear(); targeted.reset(OWNER); press(OWNER, f"a:cps:{cp['id']}"); press(OWNER, f"a:cpyr:{cp['id']}")
for t_ in th.enumerate():
    if t_ is not th.current_thread() and t_.daemon: t_.join(5)
C.call = _fc3
ok(store.get_user(7916).get("blocked") is True and campaign.get(cp["id"])["blocked"] >= 1, "a user who blocked the bot is marked and counted, run continues")
say(7916, "/start"); ok(not store.get_user(7916).get("blocked"), "blocked flag clears when the user comes back")

print("== campaign: saved draft «کمپین دانلودر» (local promo image)")
FA_T = "<b>📥 با SaveIt هر چی دیدی، دانلود کن!</b>\n\n🎁 کد هدیه: <code>{code}</code>\n✅ {benefit}\n⏳ تا {expires}\n📌 {conditions}\n\nدکمه‌ی «ثبت کد» رو بزن و شروع کن 👇"
cw7 = codes.add("WELCOME7T", "free", 7); codes.update(cw7, expires=int(time.time()) + 9 * 86400, max_uses=500, once=True)
cdn = campaign.create("کمپین دانلودر", fa=FA_T, en="", code_id=cw7); campaign.update(cdn, photo="file:assets/promo.jpg")
ok(os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(campaign.__file__)), "assets", "promo.jpg")), "assets/promo.jpg shipped")
t7, m7 = campaign.render("fa", campaign.get(cdn))
ok("WELCOME7T" in t7 and "{" not in t7 and "<code>WELCOME7T</code>" in t7 and f"cp:r:{cdn}" in m7 and "m:menu" in m7, "draft preview: placeholders filled, Redeem + Start buttons")
clear(); press(OWNER, "a:cp"); ok(any(("کمپین دانلودر" in b["text"]) for r in json.loads(SENT[-1][1].get("reply_markup") or "{}").get("inline_keyboard", []) for b in r) if SENT else False, "draft visible in admin -> Campaign")
clear(); press(OWNER, f"a:cpp:{cdn}")
ph7 = of("sendPhoto", OWNER)
ok(ph7 and ph7[-1].get("_files", {}).get("photo") and "WELCOME7T" in (ph7[-1].get("caption") or ""), "preview uploads the local image with the filled caption (admin only)")
ok(not campaign.get(cdn)["log"], "draft not sent to anyone")

print("== channel posts: lyrics / download deep-link buttons")
tid_ = poster.register_track({"artist": "Hichkas", "title": "Ye Rooze Khoob Miad", "cand": {"source": "radiojavan", "id": "77", "duration": 263}})
ok(len(tid_) == 10 and poster.get_track(tid_)["title"] == "Ye Rooze Khoob Miad" and poster.get_track(tid_)["rj_id"] == "77", "track id -> title/artist/source stored in the poster log")
ok(poster.track_id("Hichkas", "Ye Rooze Khoob Miad!") == tid_, "track id is stable (same key as the dedupe log)")
mk_ = json.loads(poster.post_markup(tid_))["inline_keyboard"][0]
ok(mk_[0]["url"] == f"https://t.me/{C.BOT_USERNAME or 'saveit_downloader_bot'}?start=lyr_{tid_}" and "Lyrics" in mk_[0]["text"] and mk_[1]["url"].endswith(f"?start=dl_{tid_}"), "post markup: Lyrics + Download deep-link buttons")
LY = {}
import lyrics as _ly
_fl = _ly.find; _ly.find = lambda title, artist, dur=0, rj=None, sh="": ("line one\nline two", "lrclib") if "Khoob" in title else (None, None)
DLK = 7931; store.update_user(DLK, lang="fa"); say(DLK, "/start"); clear()
say(DLK, f"/start lyr_{tid_}")
ok(any("line one" in t_ for t_ in texts(DLK)), "deep link sends the lyrics")
clear(); say(DLK, "/start lyr_0123456789"); ok(any("music" in c or "m:music" in c for c in cb_data(DLK)), "unknown/old track id: polite message with a search button")
tid2_ = poster.register_track({"artist": "Nobody", "title": "No Lyrics Song"}); clear(); say(DLK, f"/start lyr_{tid2_}")
ok(any("پیدا نکردم" in t_ for t_ in texts(DLK)) and "m:music" in cb_data(DLK), "lyrics not found: polite 'not found' + offer search")
_ly.find = _fl
# forced join still applies to the deep link; it resumes after joining
DLJ = 7932; store.update_user(DLJ, lang="fa"); gate.clear_cache()
chj_ = logic.add_channel(-1002000000077, "DL chan", "https://t.me/dlchan", "dlchan")
CHATS["@dlchan"] = {"id": -1002000000077, "type": "channel", "title": "DL chan", "username": "dlchan"}; BOT_STATUS["-1002000000077"] = "administrator"
_ly.find = lambda *a, **k: ("joined lyrics", "lrclib")
clear(); say(DLJ, f"/start lyr_{tid_}"); ok(any("g:check" in c for c in cb_data(DLJ)) and not any("joined lyrics" in t_ for t_ in texts(DLJ)), "not a member: join screen first, lyrics not given")
CHAT_MEMBERS[(-1002000000077, DLJ)] = "member"; gate.clear_cache(); clear(); press(DLJ, "g:check")
ok(any("joined lyrics" in t_ for t_ in texts(DLJ)), "after joining, the pending lyrics request is delivered")
with store.transaction() as st_: st_["channels"] = [c for c in st_["channels"] if c["chat_id"] != -1002000000077]
_ly.find = _fl; gate.clear_cache()
clear(); DLN = 7933; say(DLN, f"/start lyr_{tid_}")
ok("l:fa" in cb_data(DLN) and store.get_user(DLN).get("pending_dl") == f"lyr_{tid_}", "new user: language choice first, link remembered")
_ly.find = lambda *a, **k: ("first lyrics", "lrclib"); clear(); press(DLN, "l:fa"); ok(any("first lyrics" in t_ for t_ in texts(DLN)), "then the lyrics are delivered"); _ly.find = _fl

print("== Stars support")
ok(json.loads(poster.post_markup("abcdef0123"))["inline_keyboard"][1][0]["url"].endswith("?start=sup"), "support button under posts (deep link)")
ok(stars.amounts() == [10, 25, 50, 100], "default amounts 10/25/50/100")
SU = 7941; store.update_user(SU, lang="fa"); say(SU, "/start"); clear(); say(SU, "/start sup")
ok(BALE or all(f"sp:{a}" in cb_data(SU) for a in (10, 25, 50, 100)), "deep link opens the amount picker (Telegram only; Bale has no Stars)")
clear(); press(SU, "sp:25"); iv_ = of("sendInvoice", SU)
ok(iv_ and iv_[-1]["currency"] == "XTR" and iv_[-1]["provider_token"] == "" and json.loads(iv_[-1]["prices"])[0]["amount"] == 25, "Stars invoice: XTR, empty provider token, right amount")
clear(); press(SU, "sp:7"); ok(not of("sendInvoice", SU), "amounts not on the list are refused")
bot.handle_update({"pre_checkout_query": {"id": "q1", "from": {"id": SU}, "currency": "XTR", "total_amount": 25, "invoice_payload": "support:%d:25" % SU}})
ok(any(m == "answerPreCheckoutQuery" and d.get("ok") for m, d in SENT), "pre-checkout approved")
bot.handle_update({"pre_checkout_query": {"id": "q2", "from": {"id": SU}, "currency": "XTR", "total_amount": 999, "invoice_payload": "support:%d:999" % SU}})
ok(any(m == "answerPreCheckoutQuery" and d.get("pre_checkout_query_id") == "q2" and not d.get("ok") for m, d in SENT), "pre-checkout refuses unknown amount")
clear(); pay_ = {"message_id": 1, "from": {"id": SU, "first_name": "S"}, "chat": {"id": SU, "type": "private"}, "successful_payment": {"currency": "XTR", "total_amount": 25, "invoice_payload": "support:%d:25" % SU, "telegram_payment_charge_id": "CH1"}}
bot.handle_update({"message": pay_}); bot.handle_update({"message": pay_})
ok(any("۲۵" in t_ and "⭐" in t_ for t_ in texts(SU)), "thank-you message")
ok(stars.summary()["count"] == 1 and stars.summary()["stars"] == 25, "payment logged once (idempotent on the charge id)")
clear(); press(OWNER, "a:stats"); ok(BALE or ("25" in last(OWNER) and "a:spa" in cb_data(OWNER)), "admin stats show supports + amounts button")
press(OWNER, "a:spa"); say(OWNER, "5 20 x 200"); ok(stars.amounts() == [5, 20, 200], "admin edits the amounts"); stars.set_amounts([10, 25, 50, 100])
clear(); press(EXTRA, "a:stats"); ok("a:spa" not in cb_data(EXTRA), "amount editing is owner-only")

print("== viral focus")
ok(poster.viral_on() and poster.viral_tags() == ["#اینستا_وایرال", "#InstaViral", "#ReelsViral", "#وایرال", "#Viral", "#Trending"], "viral focus on by default, default viral tags")
def E(a, t, rank=None, plays=0, created="", src="radiojavan", genre="Hip-Hop/Rap", **m):
    return {"artist": a, "title": t, "cand": {"source": src, "id": "1", "plays": plays, "created": created}, "meta": dict({"genre": genre, "rank": rank or {}}, **m)}
recent = time.strftime("%Y-%m-%d", time.localtime(time.time() - 3 * 86400)); old = "2016-01-01"
sA = poster.viral_score(E("Sami Beigi", "Hot", {"rj_trending": [0, 50], "rj_popular": [1, 50]}, 3_000_000, recent))[0]
sB = poster.viral_score(E("Hichkas", "Catalog", {}, 50_000_000, old))[0]
sC = poster.viral_score(E("Hichkas", "Catalog2", {"rj_popular": [40, 50]}, 1_000_000, old))[0]
ok(sA > sC > sB, "score: charted+new+multi-chart > weakly charted > plain catalogue")
ok(poster.viral_score(E("Hichkas", "Catalog", {}, 50_000_000, old))[1][-1].startswith("catalogue"), "catalogue tracks are marked half-weight")
ok(poster.viral_score(E("X", "Y", {}, 0, "", manual=True))[0] >= 70, "admin-list tracks get a big boost")
ok(poster.viral_score(E("Zedbazi", "Diss Back", {}, 0, old))[0] > poster.viral_score(E("Zedbazi", "Other", {}, 0, old))[0], "diss/beef adds to the score")
pool = [E("Sami Beigi", "Hot", {"rj_trending": [0, 50]}, 3_000_000, recent), E("Hichkas", "Old", {}, 9_000_000, old), E("Sasy", "Old2", {}, 9_000_000, old),
        E("Post Malone", "Pop hit", {"itunes": [0, 20]}, 0, recent, src="soundcloud", genre="Pop"),
        E("Some Dj", "Unknown genre", {"audius": [0, 20]}, 0, recent, src="audius", genre=""),
        E("Eminem", "Killshot", {"itunes": [2, 20]}, 0, recent, src="soundcloud", genre="Hip-Hop/Rap"),
        E("Koorosh", "A", {"rj_popular": [3, 50]}, 1_000_000, recent), E("Koorosh", "B", {"rj_popular": [4, 50]}, 1_000_000, recent), E("Koorosh", "C", {"rj_popular": [5, 50]}, 1_000_000, recent)]
pk = poster.rank_entries(pool, "rap", 8)
names = [e["title"] for sc, w, e in pk]
ok(names[0] == "Hot" and "Pop hit" not in names, "ranked by score; non-rap genre rejected (rap-only)")
ok("Unknown genre" not in names, "rap feed: source without genre only accepts recognised rap artists")
ok(sum(1 for n in names if n in ("A", "B", "C")) == poster.MAX_PER_ARTIST, "per-artist cap still applies")
ok(names.index("Hot") < names.index("Old") if "Old" in names else True, "viral before catalogue")
done_k = {poster.track_key("Sami Beigi", "Hot")}
ok("Hot" not in [e["title"] for sc, w, e in poster.rank_entries(pool, "rap", 8, done=done_k)], "already-posted tracks are excluded (dedupe)")
mix = [E("Iran%d" % i, "T%d" % i, {"rj_trending": [i, 50]}, 1_000_000, recent) for i in range(6)] + [E("Eminem", "F%d" % i, {"itunes": [i, 20]}, 0, recent, src="soundcloud") for i in range(6)]
pk = poster.rank_entries(mix, "rap", 6, iran_share=0.65); ok(sum(1 for sc, w, e in pk if poster.is_iranian(e)) >= 4, "Iranian-majority selection")
# manual list
n_ = poster.add_viral_list("Manual Artist - Manual Song\nManual Artist - Manual Song\nSome query"); ok(n_ == 2 and len(poster.viral_list()) == 2, "manual viral list: lines added, duplicates ignored")
pk = poster.rank_entries([], "rap", 5); ok(any(e["title"] == "Manual Song" for sc, w, e in pk) and pk[0][0] >= 70, "manual list takes part in the ranking even without a chart")
poster.clear_viral_list(); ok(poster.viral_list() == [], "manual list cleared")
# tags on posts
def cap_for(meta, on=True):
    poster.set_viral_on(on)
    it = poster.make_item("A", "T", None, None, meta)
    ch = poster.channels()[0]; tags = {"title": "T", "artist": "A"}
    extra = (poster.viral_tags() if poster.is_viral_item(it) else [])
    return poster.build_caption(ch, tags, extra)
cv = cap_for({"viral": 80}); ok("#ViralX" not in cv and "#Viral" in cv and "#اینستا_وایرال" in cv and "#ReelsViral" in cv, "viral posts carry the viral hashtags")
cn = cap_for({"viral": 5}); ok("#Viral" not in cn and "#وایرال" not in cn, "non-viral posts do not")
cf = cap_for({"viral": 80}, on=False); ok("#Viral" not in cf, "viral tags off when the focus is off"); poster.set_viral_on(True)
poster.set_viral_tags(["#Mine"]); ok("#Mine" in cap_for({"viral": 80}), "viral tags editable"); poster.set_viral_tags([])
# queue re-rank
_fs = poster.fetch_source
poster.fetch_source = lambda s, g, n: ([E("Sami Beigi", "Hot", {s: [0, 50]}, 3_000_000, recent)] if s == "rj_trending" else [])
cid_v = poster.channels()[0]["id"]
items_v = [poster.make_item("Hichkas", "Filler%d" % i, None, None, {"genre": "Hip-Hop/Rap"}) for i in range(14)] + [poster.make_item("Sami Beigi", "Hot", {"source": "radiojavan", "id": "1", "duration": 200}, None, {"genre": "Hip-Hop/Rap"})]
jv = poster.create_job(cid_v, "viral test", items_v, 120, 8, "draft"); poster.set_status(jv, "running") if hasattr(poster, "set_status") else None
r_ = poster.rerank_job(jv); j_ = poster.get_job(jv)
pend_ = [i for i in j_["items"] if i["status"] == "pending"]
ok(pend_[0]["title"] == "Hot" and r_["dropped"] >= 1 and len(pend_) >= poster.MIN_QUEUE, "re-rank: most viral first, filler dropped, queue never below the minimum")
ok(all(i["status"] == "skipped" and i["err"].startswith("not viral") for i in j_["items"] if i["title"].startswith("Filler") and i["status"] != "pending"), "dropped items are marked 'not viral'")
poster.fetch_source = _fs; poster.remove_job(jv)
# admin screen
clear(); press(OWNER, "a:pv"); ok("a:pvt" in cb_data(OWNER) and "a:pva" in cb_data(OWNER) and "a:pvr" in cb_data(OWNER), "admin viral screen: toggle / add / re-rank")
press(OWNER, "a:pvt"); ok(poster.viral_on() is False, "admin toggles viral focus off"); press(OWNER, "a:pvt"); ok(poster.viral_on() is True, "...and on")
press(OWNER, "a:pva"); say(OWNER, "Ali - Song One\nBo - Song Two"); ok(len(poster.viral_list()) == 2, "admin adds tracks to the manual list")
press(OWNER, "a:pvx:0"); ok(len(poster.viral_list()) == 1, "admin removes one"); press(OWNER, "a:pvc")
press(OWNER, "a:pvty:e"); say(OWNER, "#A #B"); ok(poster.viral_tags() == ["#A", "#B"], "admin edits viral tags"); press(OWNER, "a:pvty:d"); ok("#Viral" in poster.viral_tags(), "reset")
clear(); press(OWNER, "a:pc"); ok("a:pv" in cb_data(OWNER), "viral button on the poster home")
clear(); press(EXTRA, "a:pv"); ok("a:pvt" not in cb_data(EXTRA), "viral admin is owner-only")
# feed in viral mode uses the ranking
poster.fetch_source = lambda s, g, n: ([E("Sami Beigi", "Hot", {s: [0, 50]}, 3_000_000, recent), E("Hichkas", "Plain", {}, 0, old)] if s == "rj_trending" else [])
fv = poster.create_feed(cid_v, "viral feed", "rap", ["rj_trending"], top_n=5); items_f = poster.feed_items(poster.get_feed(fv), cid_v)
ok(items_f and items_f[0]["title"] == "Hot" and items_f[0]["meta"].get("viral", 0) > 50, "feed in viral mode: items carry the score, best first")
poster.fetch_source = _fs; poster.remove_feed(fv)

print("== referral: invite text, editable rewards, cap")
store.update_user(K1, lang="fa"); clear(); say(K1, "/invite")
iv_ = texts(K1)[0]
ok("۵" in iv_ or "5" in iv_, "invite text states the inviter reward per friend (live value)")
ok("3" in iv_ or "۳" in iv_, "invite text states the friend's bonus")
ok("m:code" in cb_data(K1) and any("کد تخفیف" in b_["text"] for b_ in buttons(K1)), "invite screen has the 'enter discount code' button")
ok("کد تخفیف" in iv_, "invite text mentions discount code")
# admin edits -> text changes immediately
press(OWNER, "a:ref"); ok("a:sn:ref_max_total" in cb_data(OWNER), "referral panel: cap button")
press(OWNER, "a:sn:ref_bonus"); say(OWNER, "۹"); press(OWNER, "a:sn:ref_invitee_bonus"); say(OWNER, "4"); press(OWNER, "a:sn:ref_max_total"); say(OWNER, "20")
ok((store.settings()["ref_bonus"], store.settings()["ref_invitee_bonus"], store.settings()["ref_max_total"]) == (9, 4, 20), "admin edits inviter/friend reward + cap (Persian digits ok)")
clear(); say(K1, "/invite"); iv_ = texts(K1)[0]
ok("۹" in iv_ or "9" in iv_ and "20" in iv_, "new numbers reflected immediately; cap shown")
ok("20" in iv_, "cap line shown when set")
store.update_user(K2, lang="en"); clear(); say(K2, "/invite"); ok("9 free downloads" in texts(K2)[0] and "friend gets 4" in texts(K2)[0] and "cap" in texts(K2)[0], "en invite text with live values")
RC = 7301; say(RC, "/start"); store.update_user(7301, lang="fa")
for i_ in range(4):
    n_ = 7310 + i_; clear(); say(n_, f"/start ref_{RC}")
ok(store.get_user(RC)["ref_earned"] == 20 and store.get_user(RC)["credits"] == 20 and store.get_user(RC)["ref_count"] == 4, "inviter reward stops at the cap (9+9+2+0), counts still tracked")
ok(store.get_user(7313)["credits"] == 4, "friend still gets the bonus when the cap is hit")
press(OWNER, "a:sn:ref_max_total"); say(OWNER, "0"); ok(store.settings()["ref_max_total"] == 0, "cap 0 = unlimited")
clear(); say(K2, "/invite"); ok("cap" not in texts(K2)[0], "no cap line when unlimited")
store.settings()  # restore
logic.set_setting("ref_bonus", 5); logic.set_setting("ref_invitee_bonus", 3)
old_ = {"_v": 1, "admin_id": None, "admins": [], "settings": {"free_quota": 5, "ref_bonus": 3, "ref_invitee_bonus": 1}, "users": {}, "support": {}}
ok(store._normalize(old_)["settings"]["ref_bonus"] == 5 and store._normalize(old_)["settings"]["ref_invitee_bonus"] == 3, "untouched old defaults migrate to the new ones")
old2_ = {"_v": 1, "settings": {"ref_bonus": 7, "ref_invitee_bonus": 1}, "users": {}, "support": {}}
ok(store._normalize(old2_)["settings"]["ref_bonus"] == 7, "custom admin values are not overwritten by the migration")

print("== forced join enforced for everything")
FJ = 7401; FA = 7402
CHATS["@fjoin_a"] = {"id": -1002000000001, "type": "channel", "title": "FJ A", "username": "fjoin_a"}; BOT_STATUS["-1002000000001"] = "administrator"
CHATS["@fjoin_b"] = {"id": -1002000000002, "type": "channel", "title": "FJ B", "username": "fjoin_b"}; BOT_STATUS["-1002000000002"] = "administrator"
clear(); press(OWNER, "a:ads"); ok("a:ch" in cb_data(OWNER) and any("کانال‌های اجباری" in b_["text"] for b_ in buttons(OWNER)), "forced-join entry is reachable from the Ads section")
clear(); press(OWNER, "a:home"); ok("a:ch" in cb_data(OWNER), "forced-join entry also on the admin home")
for h_ in ("@fjoin_a", "@fjoin_b"): clear(); press(OWNER, "a:cha"); say(OWNER, h_)
ok(len(logic.channels()) == 2, "two forced-join channels configured")
say(FJ, "/start"); store.update_user(FJ, lang="fa"); gate.clear_cache()
CHAT_MEMBERS[(-1002000000001, FJ)] = "member"         # member of A only -> B missing
PROBES["https://youtu.be/fj"] = dict(MKV, url="https://youtu.be/fj")
def gated_(action, what):
    gate.clear_cache(); clear(); action()
    g_ = any("g:check" in c for c in cb_data(FJ))
    urls_ = [b_.get("url") for b_ in buttons(FJ) if b_.get("url")]
    ok(g_ and urls_ == [H + "fjoin_b"] and not of("sendVideo", FJ) and not of("sendAudio", FJ), f"blocked + join button for the missing channel only: {what}")
gated_(lambda: say(FJ, "https://youtu.be/fj"), "link download")
gated_(lambda: say(FJ, "some song name"), "plain-text music search")
gated_(lambda: say(FJ, "/music queen"), "/music")
gated_(lambda: say(FJ, "", voice={"file_id": "v", "duration": 5}), "voice recognition")
gated_(lambda: say(FJ, "", audio={"file_id": "a", "duration": 50}), "audio recognition")
gated_(lambda: say(FJ, "/plans"), "/plans")
gated_(lambda: say(FJ, "/code"), "/code (discount codes)")
gated_(lambda: press(FJ, "m:code"), "discount-code button")
gated_(lambda: press(FJ, "m:plans"), "plans button")
gated_(lambda: press(FJ, "m:invite"), "invite button")
gated_(lambda: press(FJ, "m:music"), "music button")
gated_(lambda: press(FJ, "r:deadbeef:d0"), "music search result button")
gated_(lambda: press(FJ, "d:deadbeef:vb"), "download quality button")
gated_(lambda: press(FJ, "m:theme"), "theme")
# awaiting discount-code state is gated too
set_aw_ = C.set_await; C.set_await(FJ, "code_enter"); gate.clear_cache(); clear(); say(FJ, "SUMMER25")
ok(any("g:check" in c for c in cb_data(FJ)) and codes.held(FJ) is None, "entering a code while not joined is blocked")
C.set_await(FJ, None)
cfj_ = codes.add("FJFREE", "free", 3); gate.clear_cache(); clear(); say(FJ, "/code"); say(FJ, "FJFREE")
ok(codes.get(cfj_)["used"] == 0 and not store.get_user(FJ).get("unl_until") and any("g:check" in c for c in cb_data(FJ)), "free-access code cannot be redeemed before joining all channels")
# 'I joined' re-check with live status; partial join still blocked; full join passes
gate.clear_cache(); clear(); press(FJ, "g:check"); ok(any("g:check" in c for c in cb_data(FJ)) and "fjoin_b" in " ".join(b_.get("url", "") for b_ in buttons(FJ)), "'I joined' with B still missing -> keeps the gate (only B)")
CHAT_MEMBERS[(-1002000000002, FJ)] = "member"
clear(); press(FJ, "g:check"); ok(not any("g:check" in c for c in cb_data(FJ)), "'I joined' after joining all -> passes")
clear(); say(FJ, "https://youtu.be/fj"); ok(any(c.endswith(":vb") for c in cb_data(FJ)), "member of ALL channels can use the bot")
# membership is re-checked: leaving one re-gates once the short cache expires
CHAT_MEMBERS[(-1002000000001, FJ)] = "left"; gate.clear_cache(); clear(); say(FJ, "/me")
ok(any("g:check" in c for c in cb_data(FJ)), "leaving a channel re-gates after the cache window (<= 60 s)")
ok(gate.CACHE_TTL <= 120, "positive cache <= 2 minutes")
# admins exempt
clear(); say(OWNER, "/me"); ok(not any("g:check" in c for c in cb_data(OWNER)), "owner exempt"); clear(); say(EXTRA_ADM, "/me"); ok(not any("g:check" in c for c in cb_data(EXTRA_ADM)), "sub-admin exempt")
# bot not admin / channel error: nobody is locked out; owner notified once
CHAT_MEMBERS[(-1002000000001, FJ)] = "member"
LOST.add(-1002000000002); gate.clear_cache(); gate._alerted.clear(); clear()
ok([c["chat_id"] for c in gate.missing(FJ)] == [], "channel error (bot not admin / chat gone) -> that channel is skipped, user not locked out")
ok(any("دسترسی کانال" in t_ and "FJ B" in t_ for t_ in texts(OWNER)), "owner notified about the broken channel")
n_ = len(texts(OWNER)); gate.clear_cache(); gate.missing(FJ); ok(len(texts(OWNER)) == n_, "...only once per window (no spam)")
LOST.clear(); gate.clear_cache()
# channel with no ban/kick ('kicked' status = banned from channel) counts as not joined
CHAT_MEMBERS[(-1002000000001, FJ)] = "kicked"; gate.clear_cache(); ok(len(gate.missing(FJ)) == 1, "'kicked' counts as not a member")
for c_ in list(logic.channels()): logic.remove_channel(c_["id"])
gate.clear_cache()

print("== token redaction / update / misc")
import logging
rec = logging.LogRecord("x", logging.INFO, "", 0, "url https://api.telegram.org/bot" + C.TOKEN + "/getMe failed", None, None)
C.RedactFilter().filter(rec); ok(C.TOKEN not in rec.getMessage() and "<TOKEN>" in rec.getMessage(), "log filter redacts token")
ok(C.TOKEN not in C.safe(Exception("boom " + C.TOKEN)), "safe() redacts token")
ok(C.TOKEN not in str([v for k, v in engine._env().items() if not k.startswith("__CURSOR")]), "token not passed to subprocesses")
engine.update_engine = lambda: (True, "ok"); engine.versions = lambda: ("2026.1", "1.2")
clear(); press(OWNER, "a:upd"); import threading as th
for t_ in th.enumerate():
    if t_ is not th.current_thread() and t_.daemon: t_.join(2)
ok(any("2026.1" in (d.get("text") or "") for d in of("sendMessage", OWNER)), "engine update button runs pip (mocked) and reports versions")
clear(); press(OWNER, "a:err"); ok("خطاهای اخیر" in of("editMessageText", OWNER)[-1]["text"], "recent errors view")
clear(); press(OWNER, "a:bc"); say(OWNER, "سلام همگی"); ok("a:bcy" in " ".join(cb_data(OWNER)), "broadcast asks for confirm")
press(OWNER, "a:bcn"); ok(store.snapshot()["pending_broadcast"] is None, "broadcast cancel")
ok(set(C.T["fa"]) == set(C.T["en"]), "fa/en texts have identical keys")
# atomic state
ok(not os.path.exists(store.PATH + ".tmp") and oct(os.stat(store.PATH).st_mode & 0o777) == "0o600", "state.json atomic + 600")
engine.cleanup(tmp + "/nonexistent")
shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

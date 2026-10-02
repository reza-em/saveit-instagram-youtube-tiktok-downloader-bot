"""Bale adapter tests (mocked HTTP; no token, no network): Markdown conversion, request adaptation, claim code, skipping without a token,
per-platform files. Run: python3 test_bale.py   (the functional suite is run against Bale with: DL_PLATFORM=bale python3 test_offline.py)"""
import os, sys, json, subprocess, tempfile
os.environ["DL_PLATFORM"] = "bale"; os.environ["DL_BALE_BOT_TOKEN"] = "222:BALE-TEST-TOKEN-XYZ"
os.environ["DL_TELEGRAM_BOT_TOKEN"] = "111:TG-TEST-TOKEN-XYZ"
import store
tmp = tempfile.mkdtemp()
import plat, balefmt, core as C, logic
store.set_path(os.path.join(tmp, "state_bale.json"))
PASS = FAIL = 0
def ok(c, n):
    global PASS, FAIL
    if c: PASS += 1; print("  ok  ", n)
    else: FAIL += 1; print("  FAIL", n)

print("== platform config")
P = plat.PLAT
ok(P.name == "bale" and P.api.startswith("https://tapi.bale.ai/bot222:") and P.file_api.startswith("https://tapi.bale.ai/file/bot222:"), "Bale base URL tapi.bale.ai")
ok(P.state_path.endswith("state_bale.json") and P.lock_name == "bot_bale.lock" and P.tmp_name == "tmp_bale" and P.log_name == "bale.log", "own state / lock / tmp / log (separate user database per platform)")
ok(not P.styles and not P.profile and not P.poster and P.claim, "capabilities: no styles, no profile API, no poster, claim-code owner")
ok(P.upload_mb <= 50, "upload limit <= 50 MB")
import engine
ok(engine.TMP_ROOT.endswith("tmp_bale") and engine.SAFE_LIMIT <= 50 * 1000 * 1000, "engine: separate tmp dir, size limit from the platform")
ok(C.link("saveit_bot") == "https://ble.ir/saveit_bot", "public links use ble.ir")
ok(logic.clean_username("https://ble.ir/mychannel") == "mychannel" and logic.clean_username("@x_chan1") == "x_chan1", "ble.ir links accepted as channel addresses")

print("== Markdown conversion (no parse_mode on Bale)")
md = balefmt.to_md("<b>Title</b> by <i>Artist</i>\n<a href=\"https://x.com/a\">link</a> <code>c</code> a*b _x_")
ok("*Title*" in md and "_Artist_" in md and "[link](https://x.com/a)" in md and "<" not in md, "bold / italic / link converted, tags gone")
ok("a∗b" in md and "_Artist_" in md and "＿x＿" in md, "stray * and whitespace-adjacent _ neutralised; real italic kept")
ok(balefmt.to_md("x" * 5000, 4096).endswith("…") and len(balefmt.to_md("x" * 5000, 4096)) == 4096, "clipped to 4096")
ok("&lt;" not in balefmt.to_md("a &lt;b&gt; c") and "<b>" in balefmt.to_md("a &lt;b&gt; c"), "escaped HTML entities become literal characters")

print("== request adaptation")
d, f = C.bale_prepare("sendMessage", {"chat_id": 1, "text": "<b>Hi</b>", "parse_mode": "HTML", "disable_web_page_preview": True,
                                      "reply_markup": json.dumps({"inline_keyboard": [[{"text": "a", "callback_data": "x", "style": "primary"},
                                                                                      {"text": "b", "url": "https://t.me/example_owner"}]]})}, None)
rm = json.loads(d["reply_markup"])["inline_keyboard"][0]
ok("parse_mode" not in d and d["text"] == "*Hi*" and "disable_web_page_preview" not in d, "sendMessage: HTML -> Markdown, parse_mode removed")
ok("style" not in rm[0] and rm[1]["url"] == "https://ble.ir/example_owner", "buttons: style removed, t.me links -> ble.ir")
d, f = C.bale_prepare("sendAudio", {"chat_id": 1, "caption": "🎵 <b>T</b>", "parse_mode": "HTML", "thumbnail": "attach://thumbnail", "title": "T", "performer": "A"},
                      {"audio": ("a.mp3", b"1"), "thumbnail": ("t.jpg", b"2")})
ok(d["caption"].startswith("🎵 *T*") and "thumbnail" not in d and "thumbnail" not in f and "audio" in f, "sendAudio: thumbnail dropped, audio kept, caption converted")
d, f = C.bale_prepare("sendVideo", {"chat_id": 1, "caption": "c" * 2000, "parse_mode": "HTML", "supports_streaming": "true"}, {"video": ("v.mp4", b"1")})
ok(len(d["caption"]) <= 1024 and "supports_streaming" not in d, "caption clipped to 1024, streaming flag removed")
arr = [{"type": "photo", "media": "attach://f0", "caption": "<b>x</b>", "parse_mode": "HTML"}, {"type": "video", "media": "attach://f1", "supports_streaming": True}]
d, _ = C.bale_prepare("sendMediaGroup", {"chat_id": 1, "media": json.dumps(arr)}, {"f0": ("a", b""), "f1": ("b", b"")})
m = json.loads(d["media"])
ok(m[0]["caption"] == "*x*" and "parse_mode" not in m[0] and "supports_streaming" not in m[1], "media group: captions converted, flags removed")
d, _ = C.bale_prepare("sendMessage", {"chat_id": 1, "text": "50% *raw* [x](y)"}, None)
ok("*raw*" not in d["text"] and "[x](y)" not in d["text"], "plain (non-HTML) text cannot inject Markdown")

print("== transport behaviour (mocked HTTP)")
class R:
    def __init__(s, j, code=200): s._j, s.status_code = j, code
    def json(s): return s._j
calls = []
def fake_post(url, data=None, files=None, timeout=None):
    calls.append((url, data)); return R({"ok": True, "result": {"message_id": 7}})
C.sess.post = fake_post
C.call("sendMessage", {"chat_id": 5, "text": "<b>x</b>", "parse_mode": "HTML"})
ok(calls[-1][0].startswith("https://tapi.bale.ai/bot222:") and calls[-1][0].endswith("/sendMessage") and calls[-1][1]["text"] == "*x*", "request goes to tapi.bale.ai with Markdown text")
n = len(calls)
for meth in ("setMyName", "setMyDescription", "setMyShortDescription"):
    try: C.call(meth, {"name": "x"}); ok(False, meth)
    except C.ApiError: pass
ok(len(calls) == n, "name/description methods are never sent (501 on the real server -> @botfather); setMyCommands is")
C.call("answerCallbackQuery", {"callback_query_id": "1234"}); ok(len(calls) == n, "old-client callback ids (start with 1) are not answered")
C.call("answerCallbackQuery", {"callback_query_id": "9234"}); ok(len(calls) == n + 1, "normal callback ids are answered (Bale needs it)")
seq = [R({"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 1}}), R({"ok": True, "result": True})]
C.sess.post = lambda *a, **k: seq.pop(0)
import time as _t; _sl = _t.sleep; _t.sleep = lambda s: None
ok(C.call("sendMessage", {"chat_id": 1, "text": "x"}) is True, "429 retry_after honoured once"); _t.sleep = _sl
C.sess.post = lambda *a, **k: R({"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
try: C.call("getChat", {"chat_id": 1}); ok(False, "error")
except C.ApiError as e: ok("chat not found" in str(e), "API errors surface as ApiError")
ok(C.safe(Exception("x 222:BALE-TEST-TOKEN-XYZ y 111:TG-TEST-TOKEN-XYZ")).count("<TOKEN>") == 2, "both platform tokens are redacted from errors")
ok("Bale" in C.tr("en", "too_big") and "تلگرام" not in C.tr("fa", "too_big") and "بله" in C.tr("fa", "too_big"), "texts say Bale instead of Telegram")

print("== owner claim code (usernames are not trusted on Bale)")
import bot
sent = []
C.call = lambda m, d=None, files=None, timeout=60: (sent.append((m, d)), {"message_id": 1})[1]
code = logic.ensure_claim_code()
import re
ok(re.fullmatch(r"[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}", code or ""), "one-time code generated")
ok(logic.ensure_claim_code() == code, "same code until claimed (restart-safe)")
def say(uid, text, username=None):
    bot.handle_message({"chat": {"id": uid, "type": "private"}, "from": {"id": uid, "first_name": "U", "username": username, "language_code": "en"}, "text": text, "message_id": 9})
say(10, "/start", username="example_owner"); ok(store.admin_id() is None, "username @example_owner does NOT make anyone owner on Bale")
say(11, "/claim 0000-0000-0000"); ok(store.admin_id() is None, "wrong code refused")
sent.clear(); say(12, "/claim " + code.lower())
ok(store.admin_id() == 12 and any(m == "deleteMessage" for m, _ in sent), "correct code (any case) binds the owner and deletes the message holding it")
ok(store.settings().get("claim_code") is None and logic.try_claim(13, code) == "bad", "code is consumed")
store.set_path(os.path.join(tmp, "s2.json"))
code2 = logic.ensure_claim_code()
res = [logic.try_claim(20, "AAAA-AAAA-AAAA") for _ in range(6)]
ok(res[:5] == ["bad"] * 5 and res[5] == "throttled", "brute force limited (5 tries/hour per user)")
ok(oct(os.stat(store.PATH).st_mode & 0o777) == "0o600", "state file mode 600 (holds the claim code)")

print("== skip cleanly without a token / files")
env = {k: v for k, v in os.environ.items() if k not in ("DL_BALE_BOT_TOKEN", "DL_TELEGRAM_BOT_TOKEN")}
env["DL_PLATFORM"] = "bale"
r = subprocess.run([sys.executable, "bot.py"], cwd=os.path.dirname(os.path.abspath(__file__)), env=env, capture_output=True, text=True, timeout=60)
ok(r.returncode == 0 and "DL_BALE_BOT_TOKEN not set" in r.stderr, "bot.py without DL_BALE_BOT_TOKEN exits 0 with a message")
env["DL_PLATFORM"] = "telegram"
r = subprocess.run([sys.executable, "bot.py"], cwd=os.path.dirname(os.path.abspath(__file__)), env=env, capture_output=True, text=True, timeout=60)
ok(r.returncode == 1 and "DL_TELEGRAM_BOT_TOKEN" in r.stderr, "telegram without its token still fails loudly")
_pid_before = os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_bale.pid"))
r = subprocess.run(["bash", "run_bale.sh"], cwd=os.path.dirname(os.path.abspath(__file__)), env={k: v for k, v in env.items()}, capture_output=True, text=True, timeout=30)
ok(r.returncode == 0 and "not set" in r.stdout and os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_bale.pid")) == _pid_before, "run_bale.sh without token: nothing started, exit 0")
r = subprocess.run([sys.executable, "-c", "import plat"], env=dict(env, DL_PLATFORM="nope"), cwd=os.path.dirname(os.path.abspath(__file__)), capture_output=True, text=True)
ok(r.returncode == 1, "unknown DL_PLATFORM rejected")

print("== admin UI differences")
from core import kb
import admin, theme
ok(theme.STYLE_OK is False and "style" not in json.dumps(json.loads(kb([[C.btn("Download", "x"), C.btn("❌ Cancel", "y")]])) ), "no coloured buttons on Bale")
sent.clear(); admin.home(1, None, "en", 12)
ok("a:pc" not in json.dumps([d for m, d in sent]), "channel-poster button hidden on Bale (Telegram-only)")
print(f"\n{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)

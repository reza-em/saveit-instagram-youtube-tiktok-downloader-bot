#!/usr/bin/env python3
"""SaveIt — Telegram media downloader bot (long polling, plain requests)."""
import os, re, sys, json, time, logging, fcntl, hashlib, threading, shutil
import core as C
from plat import PLAT, IS_BALE
if not C.TOKEN:
    # Bale is optional: without its token the Bale process simply does not start (exit 0, nothing to restart)
    print("%s not set%s" % (PLAT.token_env, " - skipping the %s bot" % PLAT.label if IS_BALE else ""), file=sys.stderr)
    sys.exit(0 if IS_BALE else 1)
import store, logic, engine, gate, ui, jobs, admin, musicid, theme, poster, posterui, campaign, stars
from core import tr, esc, btn, kb, send, ApiError, set_await, u_awaiting
def call(*a, **k):
    return C.call(*a, **k)

log = logging.getLogger("bot")

# ------------------------------------------------------------------ bot profile (name / about / description)
NAME = "دانلودر اینستاگرام | یوتیوب | تیک‌تاک | SaveIt"      # keyword-rich (approved SEO rename); Telegram limit 64 chars
# Short "about" (BotFather limit: 120 chars) = a compact pitch + the support contact(s) (primary + backup when set).
ABOUT_HEADS = {
    "fa": ["📥 دانلود از اینستاگرام، یوتیوب، تیک‌تاک، تردز، توییتر، لینکدین، ساندکلود + تشخیص آهنگ 🎶",
           "📥 دانلود اینستاگرام، یوتیوب، تیک‌تاک، تردز، توییتر/ایکس، لینکدین + تشخیص آهنگ 🎶",
           "📥 دانلود اینستاگرام، یوتیوب، تیک‌تاک، تردز، توییتر + تشخیص آهنگ 🎶",
           "📥 دانلود یوتیوب، اینستاگرام، تیک‌تاک، تردز، توییتر و... + شناسایی آهنگ 🎶",
           "📥 دانلود از یوتیوب، اینستاگرام، تیک‌تاک و... + شناسایی آهنگ 🎶",
           "📥 لینک بفرست، فایل بگیر! + شناسایی آهنگ 🎶", "📥 دانلودر + شناسایی آهنگ 🎶", "📥 SaveIt"],
    "en": ["📥 Instagram, YouTube, TikTok, Threads, Twitter/X, LinkedIn, SoundCloud downloader + song ID 🎶",
           "📥 Download Instagram, YouTube, TikTok, Threads, Twitter/X, LinkedIn + song ID 🎶",
           "📥 Instagram, YouTube, TikTok, Threads, Twitter/X downloader + song ID 🎶",
           "📥 Media downloader + song recognition 🎶", "📥 Downloader + song ID 🎶", "📥 SaveIt"],
}
ABOUT_LIMIT = 120

def support_ids():
    sp = store.support()
    return [x for x in (sp.get("primary") or "example_owner", sp.get("backup") or "") if x]

def about_short(code):
    """Head + '💬 @primary @backup', always <= 120 chars; the support IDs are never cut, the head is shortened instead."""
    tail = " 💬 " + " ".join("@" + x for x in support_ids())
    for h in ABOUT_HEADS[code]:
        if len(h) + len(tail) <= ABOUT_LIMIT:
            return h + tail
    return tail.strip()[:ABOUT_LIMIT]

class _About(dict):
    def __getitem__(self, k): return about_short(k)
ABOUT = _About({"fa": None, "en": None})
DESC = {
    "fa": "📥 دانلودر اینستاگرام، یوتیوب، تیک‌تاک، تردز (Threads)، توییتر/ایکس (Twitter)، لینکدین، ساندکلود و اسپاتیفای — رایگان و سریع!\n🎬 لینک ریلز، پست، استوری، شورت، پست تردز یا توییت (ویدیو، گیف، عکس) رو بفرست تا دانلود کنم (بدون واترمارک)\n🎶 دانلود آهنگ، تشخیص آهنگ از ویس/کلیپ، متن آهنگ\n🖼 عکس پروفایل، عکس و ویدیو، حتی فقط صدا\n🎤 کانال رپ: @rythmbox_rap\nشروع کن با /start 💖",
    "en": "📥 Instagram, YouTube, TikTok, Threads, Twitter/X, LinkedIn, SoundCloud & Spotify downloader — free and fast!\n🎬 Send a reel, post, story, Short, Threads post or tweet (video, GIF, photos) and I'll download it (no watermark)\n🎶 Music download, song recognition from a voice/clip, lyrics\n🖼 Profile pictures, photos, videos, even audio only\n🎤 Rap channel: @rythmbox_rap\nPress /start to begin 💖",
}
AD_LINE = {"fa": "📢 برای تبلیغات به پشتیبانی پیام بدید: @{sp}", "en": "📢 For advertising, contact support: @{sp}"}

def description(code):
    """Bot description (limit 512 chars) = DESC + the advertising line with the *current* primary support contact."""
    sp = store.support().get("primary") or "example_owner"
    return DESC[code] + "\n" + AD_LINE[code].format(sp=sp)

COMMANDS = {
    "fa": [("start", "شروع 💖"), ("help", "راهنما ℹ️"), ("lang", "زبان 🌐"), ("plans", "پلن‌ها 💎"), ("me", "حساب من 👤"), ("invite", "دعوت دوستان 🎁"), ("music", "جستجوی آهنگ 🔎"), ("theme", "تم 🎨"), ("about", "درباره 💡")],
    "en": [("start", "Start 💖"), ("help", "Help ℹ️"), ("lang", "Language 🌐"), ("plans", "Plans 💎"), ("me", "My account 👤"), ("invite", "Invite friends 🎁"), ("music", "Search music 🔎"), ("theme", "Theme 🎨"), ("about", "About 💡")],
}

def setup_bale_profile():
    """Bale's Bot API implements setMyCommands only (setMyName / setMyDescription / setMyShortDescription answer 501 'Not Implemented'):
    the command list is set automatically (Persian, default language), the rest is in bale_profile.txt for @botfather."""
    sig = hashlib.sha256(json.dumps(COMMANDS["fa"], ensure_ascii=False).encode()).hexdigest()
    if store.snapshot().get("profile_sha") == sig:
        return True
    try:
        call("setMyCommands", {"commands": json.dumps([{"command": c, "description": d} for c, d in COMMANDS["fa"]], ensure_ascii=False)})
        with store.transaction() as st:
            st["profile_sha"] = sig
        log.info("Bale: command list set; name/about/description must be pasted in Bale's @botfather (see bale_profile.txt)")
        return True
    except ApiError as e:
        log.warning("Bale setMyCommands failed: %s", C.safe(e)[:100]); return False

_name_retry = []

def setup_profile():
    """setMyName / setMyDescription / setMyShortDescription for default + fa + en (idempotent: skipped if unchanged)."""
    sig = hashlib.sha256(json.dumps([NAME, about_short("fa"), about_short("en"), description("fa"), description("en"), COMMANDS], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if store.snapshot().get("profile_sha") == sig:
        log.info("bot profile already up to date"); return True
    ok = True
    for code in (None, "fa", "en"):
        extra = {"language_code": code} if code else {}
        fa_en = code or "en"
        steps = ([("setMyName", {"name": NAME})] if store.snapshot().get("profile_name") != NAME else []) + [
                 ("setMyShortDescription", {"short_description": about_short(fa_en)}),
                 ("setMyDescription", {"description": description(fa_en)}),
                 ("setMyCommands", {"commands": json.dumps([{"command": c, "description": d} for c, d in COMMANDS[fa_en]], ensure_ascii=False)})]
        for m, d in steps:
            try:
                call(m, dict(d, **extra))
            except ApiError as e:
                ok = False; log.warning("%s(%s) failed: %s", m, code or "default", C.safe(e)[:100])
                ra = re.search(r"retry after (\d+)", str(e), re.I)
                if m == "setMyName" and ra and not _name_retry:          # BotFather rate-limits renames: try again when the limit is over
                    _name_retry.append(1); delay = min(int(ra.group(1)) + 30, 7 * 86400)
                    t = threading.Timer(delay, lambda: (_name_retry.clear(), setup_profile())); t.daemon = True; t.start()
                    log.info("bot rename is rate-limited; will retry in %d s", delay)
    if ok:
        with store.transaction() as st:
            st["profile_sha"] = sig; st["profile_name"] = NAME
    log.info("bot profile set (ok=%s)", ok)
    return ok

# ------------------------------------------------------------------ gate / banned
def run_deeplink(chat_id, uid, lang):
    """Resume a pending `?start=lyr_<id>` / `dl_<id>` (set before language choice / forced-join). True if one was handled.
    The track list comes from the poster log, so links in old channel posts keep working."""
    arg = store.get_user(uid).get("pending_dl")
    if not arg: return False
    store.update_user(uid, pending_dl=None)
    if arg == "sup":
        stars.picker(chat_id, uid, lang); return True
    kind, tid = arg.split("_", 1)
    t = poster.get_track(tid)
    if not t:
        send(chat_id, tr(lang, "dl_unknown"), kb([[btn(tr(lang, "b_music"), "m:music")], [btn(tr(lang, "b_menu"), "m:menu")]])); return True
    if kind == "lyr":
        musicid.send_lyrics(chat_id, uid, lang, {"title": t["title"], "artist": t["artist"], "duration": t.get("duration") or 0, "rj_id": t.get("rj_id")})
    else:
        musicid.run_search(chat_id, uid, lang, ("%s %s" % (t["artist"], t["title"])).strip()[:120])
    return True

def allowed(chat_id, uid, lang):
    """Ban + forced-join checks. False means a message was already sent."""
    if logic.is_banned(uid) and not logic.is_admin(uid):
        send(chat_id, tr(lang, "banned")); return False
    return gate.check_or_gate(chat_id, uid, lang)

# ------------------------------------------------------------------ owner claim (Bale: usernames are not proof of identity)
def claim_owner(chat_id, uid, lang, msg, code):
    """/claim <one-time code printed in bale.log>: binds the owner's numeric id; the user's message is deleted; the code is consumed."""
    if store.admin_id():
        send(chat_id, tr(lang, "claim_done")); return
    st = logic.try_claim(uid, code)
    C.delete_msg(chat_id, msg.get("message_id"))            # never leave the code in the chat
    if st == "ok":
        log.info("owner claimed on %s: %s", PLAT.label, uid)
        send(chat_id, tr(lang, "a_bound", uid=uid))
    elif st == "throttled":
        send(chat_id, tr(lang, "claim_throttled"))
    else:
        send(chat_id, tr(lang, "claim_bad"))

# ------------------------------------------------------------------ messages
def handle_message(msg):
    if "from" not in msg or msg["from"].get("is_bot") or msg["chat"].get("type") != "private":
        return
    chat_id = msg["chat"]["id"]; tg = msg["from"]; uid = tg["id"]
    theme.set_user(uid)
    is_new, _ = logic.touch_user(tg)
    if not PLAT.claim and logic.bind_admin_if_needed(tg):
        log.info("owner bound to numeric id %s", uid)
        send(chat_id, tr(C.user_lang(uid, tg), "a_bound", uid=uid))
    lang = C.user_lang(uid, tg)
    text = (msg.get("text") or "").strip()
    aw, awd = u_awaiting(uid)
    is_admin = logic.is_admin(uid)

    if text.startswith("/"):
        parts = text.split(None, 1)
        cmd = parts[0].split("@")[0].lower(); arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd == "/claim" and PLAT.claim:
            claim_owner(chat_id, uid, lang, msg, arg); return
        if aw:
            set_await(uid, None)
            if cmd == "/cancel":
                send(chat_id, tr(lang, "cancelled"), ui.main_menu(lang, is_admin)); return
        if cmd == "/start":
            if is_new and arg.startswith("ref_") and arg[4:].isdigit():
                r = logic.try_referral(uid, int(arg[4:]))
                if r:
                    log.info("referral: %s invited %s", arg[4:], uid)
                    if r["bonus"] > 0:
                        send(int(arg[4:]), tr(C.user_lang(int(arg[4:])), "ref_joined_referrer", bonus=r["bonus"]))
                    if r["invitee_bonus"] > 0:
                        send(chat_id, tr(lang, "ref_joined_invitee", bonus=r["invitee_bonus"]))
            if arg == "sup" and stars.enabled():                      # "⭐ support" button under channel posts
                store.update_user(uid, pending_dl="sup")
                if store.get_user(uid).get("lang") in ("fa", "en"):
                    if allowed(chat_id, uid, lang): run_deeplink(chat_id, uid, lang)
                    return
            elif re.fullmatch(r"(lyr|dl)_[0-9a-f]{10}", arg):        # deep link from a channel post: lyrics / download of that track
                store.update_user(uid, pending_dl=arg)
                if store.get_user(uid).get("lang") in ("fa", "en"):
                    if allowed(chat_id, uid, lang): run_deeplink(chat_id, uid, lang)
                    return
            send(chat_id, tr(lang, "pick_lang"), ui.lang_menu()); return
        if cmd == "/lang":
            send(chat_id, tr(lang, "pick_lang"), ui.lang_menu()); return
        if cmd == "/cancel":
            send(chat_id, tr(lang, "cancelled"), ui.main_menu(lang, is_admin)); return
        if not allowed(chat_id, uid, lang): return
        if cmd == "/help": send(chat_id, ui.help_text(lang), ui.main_menu(lang, is_admin))
        elif cmd == "/music":
            if arg: musicid.run_search(chat_id, uid, lang, arg[:120])
            else: set_await(uid, "music_query"); send(chat_id, tr(lang, "mid_ask_query"))
        elif cmd == "/theme": ui.show_theme(chat_id, uid, lang)
        elif cmd == "/about": send(chat_id, ui.about_text(lang), ui.main_menu(lang, is_admin))
        elif cmd == "/plans": ui.show_plans(chat_id, uid, lang)
        elif cmd == "/me": ui.show_me(chat_id, uid, lang)
        elif cmd == "/invite": ui.show_invite(chat_id, uid, lang)
        elif cmd == "/code": ui.ask_code(chat_id, uid, lang)
        elif cmd == "/admin":
            if is_admin: admin.home(chat_id, None, lang, uid)
        else: send(chat_id, tr(lang, "unknown_cmd"))
        return

    if not allowed(chat_id, uid, lang): return
    if aw and is_admin:
        if admin.text(msg, uid, lang, aw, awd): return
    if aw == "music_query":
        set_await(uid, None)
        if 2 <= len(text) <= 120:
            musicid.run_search(chat_id, uid, lang, text)
        else:
            send(chat_id, tr(lang, "mid_ask_query"))
        return
    if aw == "code_enter":
        ui.code_entered(chat_id, uid, lang, text); return
    if aw == "profile_user":
        set_await(uid, None)
        user = engine.parse_username(text)
        if not user:
            c = engine.classify(text.strip()) if text.startswith("http") else None
            user = c["user"] if c and c["kind"] == "profile" else None
        if not user:
            send(chat_id, tr(lang, "bad_username")); return
        jobs.analyze_username(chat_id, uid, lang, user); return
    if not text:
        if musicid.handle_media(chat_id, uid, lang, msg): return
        return
    if "http" in text.lower() or "." in text and "/" in text:
        if jobs.handle_links(chat_id, uid, lang, text): return
    if text.startswith("@"):
        user = engine.parse_username(text)
        if user:
            jobs.analyze_username(chat_id, uid, lang, user); return
    if 2 <= len(text) <= 120 and "\n" not in text:      # plain text that is not a link/@username -> music search
        musicid.run_search(chat_id, uid, lang, text); return
    send(chat_id, tr(lang, "no_link"), ui.main_menu(lang, is_admin))

# ------------------------------------------------------------------ callbacks
def handle_callback(cq):
    cid = cq["id"]
    theme.set_user((cq.get("from") or {}).get("id"))
    data = cq.get("data") or ""
    try:
        call("answerCallbackQuery", {"callback_query_id": cid})
    except ApiError as e:
        log.warning("answerCallbackQuery: %s", C.safe(e)[:80])
    m = cq.get("message")
    if not m: return
    chat_id = m["chat"]["id"]; mid = m.get("message_id"); uid = cq["from"]["id"]
    logic.touch_user(cq["from"])
    if not PLAT.claim and logic.bind_admin_if_needed(cq["from"]):
        send(chat_id, tr(C.user_lang(uid, cq["from"]), "a_bound", uid=uid))
    lang = C.user_lang(uid, cq["from"]); is_admin = logic.is_admin(uid)
    if data.startswith("l:"):
        code = data[2:]
        if code not in ("fa", "en"): return
        store.update_user(uid, lang=code); gate.clear_cache(uid)
        send(chat_id, tr(code, "lang_set"))
        if allowed(chat_id, uid, code):
            if not run_deeplink(chat_id, uid, code): ui.send_welcome(chat_id, code, uid)
        return
    if data == "g:check":
        gate.clear_cache(uid)
        chans = gate.missing(uid, use_cache=False)
        if chans:
            send(chat_id, tr(lang, "gate_still"), gate.gate_markup(lang, chans))
        else:
            C.delete_msg(chat_id, mid)
            send(chat_id, tr(lang, "gate_ok"))
            if not run_deeplink(chat_id, uid, lang): ui.send_welcome(chat_id, lang, uid)
        return
    if not allowed(chat_id, uid, lang): return
    if data.startswith("a:"):
        if not is_admin:
            log.warning("non-admin %s tried admin callback", uid); return
        admin.callback(data, chat_id, mid, uid, lang); return
    if data.startswith("sp:") and data[3:].isdigit():
        stars.invoice(chat_id, uid, lang, int(data[3:])); return
    if data.startswith("cp:"):
        campaign.user_callback(chat_id, uid, lang, mid, data); return
    if data.startswith("d:"):
        jobs.handle_choice(chat_id, uid, lang, mid, data); return
    if data.startswith("r:"):
        musicid.handle_callback(chat_id, uid, lang, mid, data); return
    if data.startswith("ad:"):
        try: aid = int(data[3:])
        except ValueError: return
        ad = logic.ad_count(aid, "clicks")
        if ad and ad.get("url"):
            send(chat_id, tr(lang, "ad_click_msg", url=esc(ad["url"])), kb([[btn(ad.get("btn") or tr(lang, "b_ad_open"), url=ad["url"])]]))
        return
    if data == "m:how": send(chat_id, tr(lang, "how_send"), kb([[btn(tr(lang, "b_menu"), "m:menu")]]))
    elif data == "m:profile":
        set_await(uid, "profile_user"); send(chat_id, tr(lang, "ask_profile"))
    elif data == "m:music":
        set_await(uid, "music_query"); send(chat_id, tr(lang, "mid_ask_query"))
    elif data == "m:theme": ui.show_theme(chat_id, uid, lang)
    elif data.startswith("th:"):
        ui.theme_chosen(chat_id, uid, lang, mid, data[3:])
    elif data == "m:lang": send(chat_id, tr(lang, "pick_lang"), ui.lang_menu())
    elif data == "m:help": send(chat_id, ui.help_text(lang), ui.main_menu(lang, is_admin))
    elif data == "m:menu": set_await(uid, None); ui.show_menu(chat_id, lang, uid)
    elif data == "m:plans": ui.show_plans(chat_id, uid, lang)
    elif data == "m:me": ui.show_me(chat_id, uid, lang)
    elif data == "m:invite": ui.show_invite(chat_id, uid, lang)
    elif data == "m:code": ui.ask_code(chat_id, uid, lang)

# ------------------------------------------------------------------ document (cookies upload) messages are plain messages
def handle_update(up):
    try:
        if up.get("pre_checkout_query"):
            stars.pre_checkout(up["pre_checkout_query"])
        elif up.get("message") and up["message"].get("successful_payment"):
            stars.paid(up["message"])
        elif up.get("message"):
            handle_message(up["message"])
        elif up.get("callback_query"):
            handle_callback(up["callback_query"])
    except Exception as e:
        log.exception("handler error: %s", C.safe(e))
        try:
            chat = (up.get("message") or up.get("callback_query", {}).get("message") or {}).get("chat", {})
            if chat.get("id"):
                send(chat["id"], C.T["fa"]["unexpected"] + "\n" + C.T["en"]["unexpected"], html=False)
        except Exception:
            pass

def single_instance():
    fd = open(os.path.join(C.BASE, PLAT.lock_name), "a+")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another SaveIt (%s) instance is already running" % PLAT.label, file=sys.stderr); sys.exit(0)
    fd.seek(0); fd.truncate(); fd.write(str(os.getpid())); fd.flush()
    return fd

def reminder_tick():
    """Once per expiry (see logic.due_reminders) tell users their plan / unlimited access ends within 3 days."""
    for r in logic.due_reminders():
        lang = C.user_lang(r["uid"])
        d = logic.days_left(r["until"])
        try:
            C.tell_user(r["uid"], "u_expiring", name=esc(r["name"] or tr(lang, "rem_unl_name")), n=logic.fa_digits(d) if lang == "fa" else d,
                        date=logic.fmt_date(r["until"]))
        except Exception as e:
            log.warning("reminder failed: %s", C.safe(e)[:80])

def reminder_loop():
    while True:
        time.sleep(1800)
        try: reminder_tick()
        except Exception as e: log.warning("reminder loop: %s", C.safe(e)[:80])

C.profile_hook = lambda: threading.Thread(target=setup_profile, daemon=True).start()

def main():
    _lock = single_instance()
    C.setup_logging()
    me = call("getMe")
    C.BOT_USERNAME = me["username"]; C.BOT_ID = me["id"]
    log.info("SaveIt (%s) started: @%s", PLAT.label, C.BOT_USERNAME)
    engine.cleanup_all()
    log.info("ffmpeg: %s | ffprobe: %s", engine.ffmpeg_path() or "MISSING", shutil.which("ffprobe") or "MISSING (audio verification disabled)")
    if PLAT.profile:
        setup_profile()
    else:
        setup_bale_profile()
    threading.Thread(target=reminder_loop, daemon=True).start()
    if PLAT.poster:
        threading.Thread(target=poster.worker, args=(posterui.notify,), daemon=True).start()
    if PLAT.claim:
        logic.ensure_claim_code()
    try:
        wh = call("getWebhookInfo")
        if wh.get("url"):
            log.info("Webhook was set; deleting to use long polling"); call("deleteWebhook")
    except ApiError as e:
        log.warning("webhook check: %s", C.safe(e)[:80])
    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if not IS_BALE: params["allowed_updates"] = json.dumps(["message", "callback_query", "pre_checkout_query"])
            if offset: params["offset"] = offset
            updates = call("getUpdates", params, timeout=45)
        except ApiError as e:
            log.warning("getUpdates: %s", C.safe(e)[:100]); time.sleep(5); continue
        for up in updates:
            offset = up["update_id"] + 1
            handle_update(up)

if __name__ == "__main__":
    main()

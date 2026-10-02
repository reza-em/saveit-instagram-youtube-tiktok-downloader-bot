"""Telegram plumbing shared by all modules: API calls, redaction, i18n, keyboards."""
import os, sys, json, time, logging, re, threading
import requests
import plat, balefmt
from plat import PLAT, IS_BALE
import store, logic, theme
from texts import T

TOKEN = PLAT.token
API = PLAT.api
FILE_API = PLAT.file_api
BASE = os.path.dirname(os.path.abspath(__file__))
if IS_BALE:
    theme.STYLE_OK = False                # Bale has no InlineKeyboardButton.style
BOT_USERNAME = ""
BOT_ID = 0

class RedactFilter(logging.Filter):
    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if any(t in msg for t in plat.all_tokens()):
            for t in plat.all_tokens(): msg = msg.replace(t, "<TOKEN>")
            record.msg = msg; record.args = ()
        return True

def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for h in logging.getLogger().handlers:
        h.addFilter(RedactFilter())
    logging.getLogger("urllib3").setLevel(logging.WARNING)

log = logging.getLogger("bot")

def safe(e):
    s = str(e)
    for t in plat.all_tokens(): s = s.replace(t, "<TOKEN>")
    return s

sess = requests.Session()

class ApiError(Exception):
    def __init__(self, msg="", retry_after=None):
        super().__init__(msg); self.retry_after = retry_after

# ---------------- Bale adaptation (Bale's Bot API is Telegram-shaped but has no parse_mode / style / thumbnails ...) ----------------
BALE_NO_METHODS = ("setMyName", "setMyDescription", "setMyShortDescription")    # 501 "Not Implemented" on the real server -> set in Bale's @botfather (setMyCommands does work)
BALE_DROP_FIELDS = ("disable_web_page_preview", "supports_streaming", "allowed_updates")
BALE_LIMITS = {"text": 4096, "caption": 1024}

def bale_markup(rm):
    """reply_markup JSON: no 'style'; t.me links -> ble.ir."""
    try:
        j = json.loads(rm) if isinstance(rm, str) else rm
        for row in j.get("inline_keyboard", []):
            for b in row:
                b.pop("style", None)
                u = b.get("url")
                if u and u.startswith("https://t.me/") and "share/url" not in u:
                    b["url"] = "https://ble.ir/" + u[len("https://t.me/"):]
        return json.dumps(j)
    except Exception:
        return rm

def bale_prepare(method, data, files):
    """-> (data, files) adapted for Bale. HTML subset -> Markdown, no parse_mode, styles/thumbnails/streaming flags removed."""
    d = dict(data or {}); files = dict(files) if files else files
    html_mode = d.pop("parse_mode", None) == "HTML"
    for f in ("text", "caption"):
        if d.get(f) is not None:
            d[f] = balefmt.to_md(str(d[f]), BALE_LIMITS[f]) if html_mode else balefmt.plain_to_md(str(d[f]), BALE_LIMITS[f])
    for f in BALE_DROP_FIELDS: d.pop(f, None)
    if d.get("reply_markup"): d["reply_markup"] = bale_markup(d["reply_markup"])
    if method in ("sendAudio", "sendVideo", "sendDocument", "sendAnimation"):
        d.pop("thumbnail", None)
        if files: files.pop("thumbnail", None)
    if method == "sendMediaGroup" and d.get("media"):
        try:
            arr = json.loads(d["media"])
            for e in arr:
                hm = e.pop("parse_mode", None) == "HTML"
                if e.get("caption"):
                    e["caption"] = balefmt.to_md(e["caption"], 1024) if hm else balefmt.plain_to_md(e["caption"], 1024)
                e.pop("supports_streaming", None)
            d["media"] = json.dumps(arr, ensure_ascii=False)
        except Exception:
            pass
    return d, files

def _call(method, data=None, files=None, timeout=60, _retry=True):
    if IS_BALE:
        if method in BALE_NO_METHODS:
            raise ApiError("not supported on Bale: " + method)
        if method == "answerCallbackQuery" and str((data or {}).get("callback_query_id", "")).startswith("1"):
            return True                           # old Bale clients: answerCallbackQuery unsupported
        data, files = bale_prepare(method, data, files)
    try:
        r = sess.post(API + method, data=data, files=files, timeout=timeout)
    except requests.RequestException as e:
        raise ApiError("network: " + type(e).__name__)
    try:
        j = r.json()
    except Exception:
        raise ApiError(f"bad response HTTP {r.status_code}")
    if not j.get("ok"):
        ra = ((j.get("parameters") or {}).get("retry_after"))
        if _retry and (j.get("error_code") == 429 or ra) and ra and int(ra) <= 30 and not files:
            time.sleep(int(ra) + 1)
            return _call(method, data, files, timeout, _retry=False)
        raise ApiError(j.get("description", "unknown error"), ra)
    return j["result"]

def call_with_fallback(method, data=None, files=None, timeout=60):
    """Telegram call. If the API rejects coloured buttons ('style'), retry once without them and stop using styles."""
    try:
        return _call(method, data, files, timeout)
    except ApiError as e:
        rm = (data or {}).get("reply_markup")
        if rm and '"style"' in str(rm) and "style" in str(e).lower():
            theme.STYLE_OK = False
            log.warning("button styles rejected by the API; disabled for this process")
            d2 = dict(data); d2["reply_markup"] = theme.strip_styles(rm)
            if files:
                for v in files.values():
                    if hasattr(v[1], "seek"): v[1].seek(0)
            return _call(method, d2, files, timeout)
        raise

call = call_with_fallback

# ---------------- i18n ----------------
def tr(lang, key, **kw):
    s = T[lang][key]
    s = s.format(**kw) if kw else s
    if IS_BALE:                                   # same texts, platform name swapped
        s = s.replace("تلگرام", "بله").replace("Telegram", "Bale")
    return s

def link(username):
    """Public link of a user/channel/bot on the active platform (t.me or ble.ir)."""
    return PLAT.link(username)

def esc(t):
    return str(t if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def user_lang(uid, tg_user=None):
    lang = store.get_user(uid).get("lang")
    if lang in ("fa", "en"):
        return lang
    code = ((tg_user or {}).get("language_code") or "").lower()
    return "en" if code.startswith("en") else "fa"

def fmt_size(n):
    n = float(n or 0)
    return f"{n/1024:.0f} KB" if n < 1024 * 1024 else f"{n/1024/1024:.1f} MB"

_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

def parse_int(s, lo=0, hi=100000):
    s = (s or "").strip().translate(_DIG)
    if not re.fullmatch(r"\d{1,9}", s):
        return None
    n = int(s)
    return n if lo <= n <= hi else None

# ---------------- keyboards ----------------
def kb(rows):
    return json.dumps({"inline_keyboard": theme.decorate(rows)})

def btn(text, data=None, url=None):
    b = {"text": text}
    if url: b["url"] = url
    else: b["callback_data"] = data
    return b

def contact_btns(lang):
    sp = store.support()
    if sp.get("backup"):
        return [btn(tr(lang, "b_support1"), url=link(sp["primary"])),
                btn(tr(lang, "b_support2"), url=link(sp["backup"]))]
    return [btn(tr(lang, "b_contact"), url=link(sp["primary"]))]

profile_hook = None      # set by bot.py: re-applies the bot profile (description) after the support contact changes

def support_note(lang):
    sp = store.support()
    if sp.get("backup"):
        return tr(lang, "note_two", p=sp["primary"], b=sp["backup"])
    return tr(lang, "note_one", p=sp["primary"])

# ---------------- sending ----------------
def send(chat_id, text, markup=None, html=True, **extra):
    data = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if markup: data["reply_markup"] = markup
    if html: data["parse_mode"] = "HTML"
    data.update(extra)
    try:
        return call("sendMessage", data)
    except ApiError as e:
        log.warning("sendMessage failed: %s", safe(e))

def show(chat_id, msg_id, text, markup=None, html=True):
    """Edit a panel message in place if possible, else send a new one."""
    if msg_id:
        data = {"chat_id": chat_id, "message_id": msg_id, "text": text, "disable_web_page_preview": True}
        if markup: data["reply_markup"] = markup
        if html: data["parse_mode"] = "HTML"
        try:
            return call("editMessageText", data)
        except ApiError as e:
            if "not modified" in str(e).lower():
                return None
    return send(chat_id, text, markup, html)

def edit_text(chat_id, msg_id, text, markup=None):
    if not msg_id:
        return
    data = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if markup: data["reply_markup"] = markup
    try:
        call("editMessageText", data)
    except ApiError:
        pass

def delete_msg(chat_id, msg_id):
    if not msg_id:
        return
    try:
        call("deleteMessage", {"chat_id": chat_id, "message_id": msg_id})
    except ApiError:
        pass

def clear_kb(chat_id, msg_id):
    if not msg_id:
        return
    try:
        call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg_id,
                                        "reply_markup": json.dumps({"inline_keyboard": []})})
    except ApiError:
        pass

def tell_user(uid, key, **kw):
    send(uid, tr(user_lang(uid), key, **kw))

def alert_owner(text):
    o = store.admin_id()
    if o:
        send(o, text, html=False)

# -------- awaiting state helpers --------
def set_await(uid, kind, data=None):
    store.update_user(uid, awaiting=kind, await_data=data)

def u_awaiting(uid):
    u = store.get_user(uid)
    return u.get("awaiting"), (u.get("await_data") or {})

def uname_of(u):
    return ("@" + u["username"]) if u.get("username") else ""

def who_label(uid):
    u = store.get_user(uid)
    return f"{u.get('name') or uid} {uname_of(u)} ({uid})".replace("  ", " ")

"""Targeted admin messaging: pick users (ID/@username list, multi-select from the user list, filters), compose (text / photo /
any message copied as-is, e.g. forwarded content), preview, confirm, rate-limited send, delivery report (sent/blocked/failed).
Admins only (owner and extra admins). Banned users are always excluded. The wizard state is per admin and kept in memory."""
import re, time, threading, logging
import core as C, store, logic
from core import tr, esc, btn, kb, show, send, set_await, who_label

log = logging.getLogger("targeted")
S = {}                      # admin uid -> {"sel": set, "plan": bool, "expired": bool, "lang": None|"fa"|"en", "msg": {...}}
PAGE = 8
RATE = 0.05                 # seconds between sends (Telegram allows ~30 msgs/s; stay well below)
_lock = threading.Lock()

def st_(uid):
    return S.setdefault(uid, {"sel": set(), "plan": False, "expired": False, "lang": None, "msg": None, "sending": False, "all": False, "back": None})

def reset(uid):
    S.pop(uid, None)

def _matches(uid, u, s, t):
    if u.get("banned") or u.get("blocked"): return False          # banned users and users who blocked the bot are always skipped
    if s["lang"] and (u.get("lang") or "fa") != s["lang"]: return False
    return True

def recipients(admin_uid):
    s = st_(admin_uid); t = logic.now(); snap = store.snapshot()["users"]
    ids = set(s["sel"])
    if s.get("all"): ids |= {int(k) for k in snap}
    if s["plan"] or s["expired"]:
        for k, u in snap.items():
            pl = u.get("plan") or {}
            until = int(pl.get("until", 0) or 0); unl = int(u.get("unl_until", 0) or 0)
            active = (until > t) or (unl > t) or u.get("unlimited")
            expired = (pl and until <= t) or (unl and unl <= t and not u.get("unlimited"))
            if (s["plan"] and active) or (s["expired"] and expired and not active):
                ids.add(int(k))
    out = []
    for i in sorted(ids):
        u = snap.get(str(i))
        if u is not None and _matches(i, u, s, t):
            out.append(i)
    return out

def parse_targets(text):
    """'123, @name 456' -> (ids, unknown_tokens)."""
    ids, bad = [], []
    for tok in re.split(r"[\s,،;\n]+", text or ""):
        if not tok: continue
        uid = logic.find_user(tok)
        (ids.append(uid) if uid is not None else bad.append(tok))
    return ids, bad

# ---------------- screens ----------------
def menu(chat_id, mid, lang, admin_uid):
    s = st_(admin_uid); n = len(recipients(admin_uid))
    flag = lambda b: "✅" if b else "⬜"
    rows = [[btn(tr(lang, "tm_b_ids"), "a:tmi"), btn(tr(lang, "tm_b_pick"), "a:tmp:1")],
            [btn(f"{flag(s['plan'])} " + tr(lang, "tm_f_plan"), "a:tmf:plan"), btn(f"{flag(s['expired'])} " + tr(lang, "tm_f_expired"), "a:tmf:expired")],
            [btn(f"{flag(s['lang'] == 'fa')} فارسی", "a:tmf:fa"), btn(f"{flag(s['lang'] == 'en')} English", "a:tmf:en")],
            [btn(tr(lang, "tm_b_compose", n=n), "a:tmc"), btn(tr(lang, "tm_b_clear"), "a:tmx")],
            [btn(tr(lang, "a_back"), "a:bcm")]]
    show(chat_id, mid, tr(lang, "tm_menu", n=n, manual=len(s["sel"]), langf=(s["lang"] or tr(lang, "tm_all_langs"))), kb(rows))

def pick_page(chat_id, mid, lang, admin_uid, page):
    s = st_(admin_uid)
    rows_, page, pages, total = logic.users_page(page, PAGE)
    rows = []
    for uid, u in rows_:
        mark = "☑️" if uid in s["sel"] else "⬜"
        lbl = f"{mark} {'🚫 ' if u.get('banned') else ''}{u.get('name') or uid} {('@' + u['username']) if u.get('username') else ''}".strip()[:44]
        rows.append([btn(lbl, f"a:tmt:{uid}:{page}")])
    nav = []
    if page > 1: nav.append(btn("⬅️", f"a:tmp:{page - 1}"))
    nav.append(btn(f"{page}/{pages}", "a:noop"))
    if page < pages: nav.append(btn("➡️", f"a:tmp:{page + 1}"))
    rows.append(nav)
    rows.append([btn(tr(lang, "tm_done", n=len(s["sel"])), s.get("back") or "a:tm")])
    show(chat_id, mid, tr(lang, "tm_pick", n=len(s["sel"])), kb(rows))

def compose_prompt(chat_id, lang, admin_uid):
    if not recipients(admin_uid):
        send(chat_id, tr(lang, "tm_none")); return
    set_await(admin_uid, "a_tm_msg", {})
    send(chat_id, tr(lang, "tm_ask_msg"))

def save_message(msg, admin_uid):
    """Keep the admin's message as-is: text, photo(+caption) or anything else is copied later with copyMessage."""
    if msg.get("photo"):
        return {"kind": "photo", "photo": msg["photo"][-1]["file_id"], "caption": (msg.get("caption") or "")[:1024], "chat": msg["chat"]["id"], "id": msg["message_id"]}
    if msg.get("text") and not (msg.get("forward_origin") or msg.get("forward_from") or msg.get("forward_from_chat")):
        return {"kind": "text", "text": msg["text"][:4000], "chat": msg["chat"]["id"], "id": msg["message_id"]}
    return {"kind": "copy", "chat": msg["chat"]["id"], "id": msg["message_id"]}

def preview(chat_id, lang, admin_uid):
    m = st_(admin_uid)["msg"]; n = len(recipients(admin_uid))
    deliver(chat_id, m, preview=True)
    send(chat_id, tr(lang, "tm_confirm", n=n, rate=int(1 / max(RATE, 0.001))), kb([[btn(tr(lang, "yes"), "a:tmy"), btn(tr(lang, "no"), "a:tmn")]]))

def deliver(to, m, preview=False):
    """Send the saved message to one chat. Raises C.ApiError."""
    if m["kind"] == "text":
        return C.call("sendMessage", {"chat_id": to, "text": m["text"], "disable_web_page_preview": True})
    if m["kind"] == "photo":
        return C.call("sendPhoto", {"chat_id": to, "photo": m["photo"], "caption": m["caption"]})
    return C.call("copyMessage", {"chat_id": to, "from_chat_id": m["chat"], "message_id": m["id"]})

def classify_error(e):
    t = str(e).lower()
    if "blocked" in t or "deactivated" in t or "chat not found" in t or "user is deleted" in t or "forbidden" in t:
        return "blocked"
    return "failed"

def start_send(admin_chat, lang, admin_uid):
    s = st_(admin_uid)
    if not s.get("msg") or s.get("sending"):
        return False
    ids = recipients(admin_uid); m = s["msg"]; s["sending"] = True
    def job():
        ok = blocked = failed = 0
        for i in ids:
            for attempt in range(2):
                try:
                    deliver(i, m); ok += 1; break
                except C.ApiError as e:
                    m_ = re.search(r"retry after (\d+)", str(e), re.I)
                    if m_ and attempt == 0:
                        time.sleep(min(int(m_.group(1)) + 1, 30)); continue
                    if classify_error(e) == "blocked": blocked += 1
                    else: failed += 1
                    break
            time.sleep(RATE)
        S.pop(admin_uid, None)
        send(admin_chat, tr(lang, "tm_report", total=len(ids), ok=ok, blocked=blocked, failed=failed))
    threading.Thread(target=job, daemon=True).start()
    return True

# ---------------- callbacks / text ----------------
def callback(op, arg, arg2, chat_id, mid, uid, lang):
    s = st_(uid)
    if op == "tm": menu(chat_id, mid, lang, uid)
    elif op == "tmi": set_await(uid, "a_tm_ids", {}); send(chat_id, tr(lang, "tm_ask_ids"))
    elif op == "tmp": pick_page(chat_id, mid, lang, uid, int(arg or 1))
    elif op == "tmt":
        t = int(arg)
        s["sel"].symmetric_difference_update({t}); pick_page(chat_id, mid, lang, uid, int(arg2 or 1))
    elif op == "tmf":
        if arg in ("plan", "expired"): s[arg] = not s[arg]
        elif arg in ("fa", "en"): s["lang"] = None if s["lang"] == arg else arg
        menu(chat_id, mid, lang, uid)
    elif op == "tmx": reset(uid); menu(chat_id, mid, lang, uid)
    elif op == "tmc": compose_prompt(chat_id, lang, uid)
    elif op == "tmy":
        if start_send(chat_id, lang, uid): show(chat_id, mid, tr(lang, "a_bc_sending"))
        else: send(chat_id, tr(lang, "a_bc_none"))
    elif op == "tmn": reset(uid); show(chat_id, mid, tr(lang, "a_bc_cancel"), kb([[btn(tr(lang, "a_back"), "a:bcm")]]))

def text(msg, uid, lang, aw, data):
    chat_id = msg["chat"]["id"]; t = (msg.get("text") or "").strip()
    s = st_(uid)
    if aw == "a_tm_ids":
        ids, bad = parse_targets(t)
        if not ids and bad: send(chat_id, tr(lang, "a_not_found")); return True
        s["sel"].update(ids); set_await(uid, None)
        if bad: send(chat_id, tr(lang, "tm_unknown", items=esc(" ".join(bad)[:200])))
        menu(chat_id, None, lang, uid); return True
    if aw == "a_tm_msg":
        s["msg"] = save_message(msg, uid); set_await(uid, None)
        preview(chat_id, lang, uid); return True
    return False

"""Button themes: four seasons (emoji accents + optional Telegram button colours) applied in the shared keyboard builder.

* Users pick a theme (🎨 button / /theme); the admin sets the global default ('auto' = by Jalali season, today's date).
* `decorate(rows)` is called by core.kb(), so every screen (menus, admin, plans, search, recognition) follows the theme.
* Coloured buttons use InlineKeyboardButton.style ("primary" blue / "success" green / "danger" red, Bot API 9.x). If the API
  ever rejects it, core.call retries once without styles and `STYLE_OK` turns off for the process (graceful fallback).
"""
import time, threading, copy
import store

THEMES = {
    "fire":   {"emojis": ["🔥", "🧡", "❤️", "🌞"], "style": "danger",  "fa": "🔥 آتش (تابستان)", "en": "🔥 Fire (Summer)"},
    "winter": {"emojis": ["❄️", "⛄", "🩵", "💙"], "style": "primary", "fa": "❄️ برف (زمستان)", "en": "❄️ Snow (Winter)"},
    "spring": {"emojis": ["🌸", "🌷", "💚", "🦋"], "style": "success", "fa": "🌸 بهار", "en": "🌸 Spring"},
    "autumn": {"emojis": ["🍂", "🍁", "🟠", "🤎"], "style": None,      "fa": "🍂 پاییز", "en": "🍂 Autumn"},
}
ORDER = ("fire", "winter", "spring", "autumn")
STYLE_OK = True                     # flipped to False if Telegram rejects button styles
_ctx = threading.local()

def set_user(uid):
    _ctx.uid = uid

def cur_user():
    return getattr(_ctx, "uid", None)

# ---------- Jalali season ----------
def g2j(gy, gm, gd):
    g_d = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d[gm - 1]
    jy = -1595 + (33 * (days // 12053)); days %= 12053
    jy += 4 * (days // 1461); days %= 1461
    if days > 365:
        jy += (days - 1) // 365; days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31; jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30; jd = 1 + (days - 186) % 30
    return jy, jm, jd

def season_for(t=None):
    lt = time.localtime(t or time.time())
    jm = g2j(lt.tm_year, lt.tm_mon, lt.tm_mday)[1]
    return "spring" if jm <= 3 else "fire" if jm <= 6 else "autumn" if jm <= 9 else "winter"

def default_theme():
    d = store.settings().get("theme_default", "auto")
    return season_for() if d not in THEMES else d

def user_theme(uid):
    if uid is None:
        return default_theme()
    t = store.get_user(uid).get("theme")
    return t if t in THEMES else default_theme()

def set_user_theme(uid, key):
    if key in THEMES:
        store.update_user(uid, theme=key)
        return True
    return False

def set_default(key):
    if key != "auto" and key not in THEMES:
        return False
    with store.transaction() as st:
        st["settings"]["theme_default"] = key
    return True

def styles_enabled():
    return STYLE_OK and bool(store.settings().get("btn_style", True))

def toggle_styles():
    with store.transaction() as st:
        st["settings"]["btn_style"] = not st["settings"].get("btn_style", True)
        return st["settings"]["btn_style"]

# ---------- decoration ----------
_ALL_EMOJI = {e for t in THEMES.values() for e in t["emojis"]}
_NAV = ("⬅️", "🔙", "🏠", "🔄")
_BAD = ("❌", "🗑", "🚫", "⛔", "🧹")
_GOOD = ("✅", "⬇️", "✔️")

def _is_nav(b):
    cd = b.get("callback_data") or ""
    return b["text"].startswith(_NAV) or cd in ("m:menu", "a:home", "a:noop") or cd.endswith(":back")

def decorate(rows, uid=None):
    """Returns new rows with theme emoji accents (+ colours). Pure function of (rows, user theme)."""
    th = THEMES[user_theme(cur_user() if uid is None else uid)]
    use_style = styles_enabled()
    out, n = [], 0
    for row in rows:
        nr = []
        for i, b in enumerate(row):
            if not isinstance(b, dict) or "text" not in b:
                nr.append(b); continue
            b = dict(b); txt = b["text"]
            if len(txt) >= 4 and not any(txt.rstrip().endswith(e) for e in _ALL_EMOJI) and len(txt) <= 58 and not _is_nav(b):
                em = th["emojis"][n % 4]
                b["text"] = (em + " " + txt) if txt[0].isalnum() else (txt + " " + em)
            n += 1
            if use_style and "style" not in b and "url" not in b and not _is_nav(b):
                cd = b.get("callback_data") or ""
                if txt.startswith(_BAD) or cd.endswith(":x"):
                    b["style"] = "danger"
                elif txt.startswith(_GOOD):
                    b["style"] = "success"
                elif i == 0 and th["style"]:
                    b["style"] = th["style"]
            nr.append(b)
        out.append(nr)
    return out

def strip_styles(markup_json):
    """reply_markup JSON string -> same without any 'style' keys (fallback when the API rejects them)."""
    import json
    try:
        m = json.loads(markup_json)
        for row in m.get("inline_keyboard", []):
            for b in row:
                b.pop("style", None)
        return json.dumps(m)
    except Exception:
        return markup_json

def name(lang, key):
    return "🗓 " + ("خودکار (فصل جاری)" if lang == "fa" else "Auto (current season)") if key == "auto" else THEMES[key][lang]

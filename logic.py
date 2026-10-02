"""Business logic on top of store.py (no Telegram calls here)."""
import os
import time, copy, re
import store
from store import transaction

ADMIN_USERNAME = os.environ.get("OWNER_USERNAME", "example_owner").lower()
ACTIVE_WINDOW = 7 * 86400
MAX_PPLANS = 10
MAX_ADS = 30
MAX_ERRORS = 60
PLATFORMS = ("youtube", "instagram", "tiktok", "pinterest", "linkedin", "threads", "twitter", "soundcloud", "spotify", "music_id")
# built-in defaults that differ from the global free quota (music recognition is free/unlimited until the admin limits it)
PLAT_DEFAULTS = {"music_id": -1, "threads": 5, "twitter": 5}      # Threads / X: 5 free downloads per day each (admin-editable per platform)

def now():
    return int(time.time())

def today():
    return time.strftime("%Y-%m-%d", time.localtime())

def fmt_date(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

# ---------- users / admin ----------
def touch_user(tg_user):
    """Register/refresh a Telegram user. Returns (is_new, user_copy)."""
    uid = str(tg_user["id"])
    with transaction() as st:
        is_new = uid not in st["users"]
        u = st["users"].setdefault(uid, {})
        u.setdefault("first_seen", now())
        u["last_seen"] = now()
        u.pop("blocked", None)              # talking to the bot = not blocking it
        u["name"] = " ".join(x for x in [tg_user.get("first_name"), tg_user.get("last_name")] if x)[:64]
        un = (tg_user.get("username") or "").lower()
        if un:
            u["username"] = un
        else:
            u.pop("username", None)
        if tg_user.get("language_code"):
            u["language_code"] = tg_user["language_code"]
        return is_new, copy.deepcopy(u)

def bind_admin_if_needed(tg_user):
    """Auto-bind the owner numeric id the first time @example_owner writes. True if newly bound."""
    if (tg_user.get("username") or "").lower() != ADMIN_USERNAME:
        return False
    with transaction() as st:
        if st.get("admin_id"):
            return False
        st["admin_id"] = int(tg_user["id"])
        return True

# ---------- owner claim code (Bale) ----------
import secrets, threading as _th
_claim_tries = {}; _claim_lk = _th.Lock()

def ensure_claim_code():
    """No owner yet -> create a one-time code, store it (state file is mode 600) and print it ONLY to the platform log."""
    import logging
    with transaction() as st:
        if st.get("admin_id"):
            return None
        code = st["settings"].get("claim_code")
        if not code:
            h = secrets.token_hex(6).upper()
            code = st["settings"]["claim_code"] = "%s-%s-%s" % (h[:4], h[4:8], h[8:])
    logging.getLogger("claim").warning("CLAIM CODE: %s  (send /claim %s to the bot from the owner account)", code, code)
    return code

def try_claim(uid, code):
    t = time.time()
    with _claim_lk:
        a = [x for x in _claim_tries.get(int(uid), []) if t - x < 3600]
        allx = sum(len([y for y in v if t - y < 3600]) for v in _claim_tries.values())
        if len(a) >= 5 or allx >= 20:
            return "throttled"
        a.append(t); _claim_tries[int(uid)] = a
    with transaction() as st:
        real = st["settings"].get("claim_code")
        if st.get("admin_id") or not real or not secrets.compare_digest(str(code or "").strip().upper(), real):
            return "bad"
        st["admin_id"] = int(uid)
        st["settings"].pop("claim_code", None)
        return "ok"

def is_owner(uid):
    a = store.admin_id()
    return a is not None and int(uid) == int(a)

def is_extra_admin(uid):
    with transaction(write=False) as st:
        return int(uid) in [int(x) for x in st.get("admins", [])]

def is_admin(uid):
    return is_owner(uid) or is_extra_admin(uid)

def list_admins():
    with transaction(write=False) as st:
        return [int(x) for x in st.get("admins", [])]

def add_admin(uid):
    with transaction() as st:
        if st.get("admin_id") is not None and int(uid) == int(st["admin_id"]):
            return False
        if int(uid) not in [int(x) for x in st["admins"]]:
            st["admins"].append(int(uid))
        return True

def remove_admin(uid):
    with transaction() as st:
        n = len(st["admins"])
        st["admins"] = [int(x) for x in st["admins"] if int(x) != int(uid)]
        return len(st["admins"]) != n

def set_owner(new_uid):
    with transaction() as st:
        st["admin_id"] = int(new_uid)
        st["admins"] = [int(x) for x in st["admins"] if int(x) != int(new_uid)]

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")

def clean_username(s):
    s = (s or "").strip()
    for pre in ("https://t.me/", "http://t.me/", "t.me/", "https://ble.ir/", "http://ble.ir/", "ble.ir/", "@"):
        if s.lower().startswith(pre):
            s = s[len(pre):]
    return s if USERNAME_RE.match(s) else None

def set_support(slot, name):
    with transaction() as st:
        if slot == "primary" and not name:
            return False
        st["support"][slot] = name or ""
        return True

def find_user(q):
    q = (q or "").strip()
    st = store.snapshot()
    if q.lstrip("-").isdigit():
        return int(q) if q in st["users"] else None
    q = q.lstrip("@").lower()
    if not q:
        return None
    for uid, u in st["users"].items():
        if u.get("username") == q:
            return int(uid)
    return None

def is_banned(uid):
    return bool(store.get_user(uid).get("banned"))

def set_banned(uid, flag):
    store.update_user(uid, banned=True if flag else None)

# ---------- access / quota ----------
# Free daily quota is PER PLATFORM. Effective limit of a platform = admin override (settings.plat_limits[platform])
# or, when not overridden, the global default (settings.free_quota) -- counted separately for each platform.
#   limit  > 0 : that many free downloads per day on that platform
#   limit == 0 : platform blocked for free users (credits / unlimited plans / admins still work)
#   limit  < 0 : unlimited for everyone on that platform
# Spending order on a download: free daily quota of that platform first, then credit balance (credits work on every
# platform, also where the free limit is 0). Unlimited grants (forever / N days) and admins bypass all limits.
UNLIMITED = -1

def plat_limit(st, plat):
    v = st["settings"].get("plat_limits", {}).get(plat)
    if v is None:
        v = PLAT_DEFAULTS.get(plat)
    return int(st["settings"]["free_quota"]) if v is None else int(v)

def _access(st, uid, plat=None):
    u = st["users"].get(str(uid), {})
    fresh = u.get("free_day") == today()
    used_map = u.get("free_plat", {}) if fresh else {}
    credits = int(u.get("credits", 0))
    until = int(u.get("unl_until", 0) or 0)
    a = st.get("admin_id")
    if (a is not None and int(uid) == int(a)) or int(uid) in [int(x) for x in st.get("admins", [])]:
        kind = "admin"
    elif u.get("unlimited"):
        kind = "unlimited"
    elif until > now():
        kind = "until"
    elif _plan_active(u):
        kind = "plan"
    else:
        kind = "quota"
    plan = None
    if kind == "plan":
        pl = u["plan"]; daily = int(pl.get("daily") or 0)
        used = int(u.get("plan_used", 0)) if u.get("plan_day") == today() else 0
        plan = {"id": pl.get("id"), "title": pl.get("title", ""), "daily": daily, "until": int(pl["until"]), "used": used,
                "left": (None if daily < 0 else max(0, daily - used))}
    per = {}
    for p in PLATFORMS:
        lim = plat_limit(st, p); used = int(used_map.get(p, 0))
        per[p] = {"limit": lim, "used": used, "left": (None if lim < 0 else max(0, lim - used)),
                  "unlimited": lim < 0, "blocked": lim == 0}
    p = per.get(plat) if plat else None
    if p is None:      # generic view: best case over the download platforms (music recognition is not a download)
        dl = [x for k, x in per.items() if k != "music_id"]
        lefts = [x["left"] for x in dl]
        any_free = any(x["unlimited"] or (x["left"] or 0) > 0 for x in dl)
        free_left = None if any(x["unlimited"] for x in dl) else max(lefts or [0])
        free_total = int(st["settings"]["free_quota"])
    else:
        any_free = p["unlimited"] or (p["left"] or 0) > 0
        free_left, free_total = p["left"], p["limit"]
    plan_ok = bool(plan) and (plat == "music_id" or plan["left"] is None or plan["left"] > 0)
    # a plan whose daily downloads are used up falls back to the normal free quota / credits (a plan is never worse than free)
    return {"kind": kind, "free_left": free_left, "free_total": free_total, "credits": credits, "until": until,
            "plan": plan, "plan_ok": plan_ok,
            "platform": plat, "per": per, "blocked": bool(p and p["blocked"]), "unlimited_free": bool(p and p["unlimited"]),
            # music recognition never spends credits (credits are for downloads); it is free unless the admin limits it
            "can": kind in ("admin", "unlimited", "until") or plan_ok or any_free or (credits > 0 and plat != "music_id"),
            "premium": kind != "quota" or credits > 0}

def access_info(uid, plat=None):
    with transaction(write=False) as st:
        return _access(st, uid, plat)

def is_premium(uid):
    return access_info(uid)["premium"]

def consume(uid, platform=None):
    """Record one finished download on `platform`: spends that platform's free daily quota first, then credits.
    Returns False if not allowed."""
    with transaction() as st:
        acc = _access(st, uid, platform)
        if not acc["can"]:
            return False
        u = st["users"].setdefault(str(uid), {})
        if platform != "music_id":              # a recognition is not a download: no 'made' count, no ad tick
            u["made"] = int(u.get("made", 0)) + 1
            u["dl_since_ad"] = int(u.get("dl_since_ad", 0)) + 1
        if acc["kind"] == "plan" and acc["plan_ok"]:
            if platform != "music_id" and acc["plan"]["left"] is not None:      # a recognition/search never spends the plan
                if u.get("plan_day") != today():
                    u["plan_day"] = today(); u["plan_used"] = 0
                u["plan_used"] = int(u.get("plan_used", 0)) + 1
        elif acc["kind"] in ("quota", "plan") and not acc["unlimited_free"]:
            if (acc["free_left"] or 0) > 0:
                if u.get("free_day") != today():
                    u["free_day"] = today(); u["free_plat"] = {}
                fp = u.setdefault("free_plat", {})
                fp[platform] = int(fp.get(platform, 0)) + 1
            else:
                u["credits"] = int(u.get("credits", 0)) - 1
        if platform:
            d = st["stats"]["downloads"]
            d[platform] = int(d.get(platform, 0)) + 1
        return True

def set_plat_limit(platform, value):
    """value: int >= 0, -1 unlimited, None = remove override (use global default)."""
    if platform not in PLATFORMS:
        raise KeyError(platform)
    with transaction() as st:
        pl = st["settings"].setdefault("plat_limits", {})
        if value is None:
            pl.pop(platform, None)
        else:
            pl[platform] = int(value)

def add_credits(uid, n):
    with transaction() as st:
        u = st["users"].setdefault(str(uid), {})
        u["credits"] = max(0, int(u.get("credits", 0)) + int(n))
        return u["credits"]

def grant_unlimited(uid):
    with transaction() as st:
        st["users"].setdefault(str(uid), {})["unlimited"] = True

def _plan_active(u):
    pl = u.get("plan")
    return bool(pl) and int(pl.get("until", 0) or 0) > now()

def days_left(until):
    """Whole days remaining (rounded up, min 1 while still active, 0 when expired)."""
    d = int(until) - now()
    return 0 if d <= 0 else max(1, -(-d // 86400))

def add_days(uid, days):
    """Unlimited access for N more days; extending ADDS to the existing expiry."""
    with transaction() as st:
        u = st["users"].setdefault(str(uid), {})
        base = max(now(), int(u.get("unl_until", 0) or 0))
        u["unl_until"] = base + int(days) * 86400
        u["acc_from"] = now()
        return u["unl_until"]

# ---------- durations / discounts / plan grants ----------
MONTH_DAYS = 30                 # "1 month" = 30 days, 3 = 90, 6 = 180
DURATIONS = (1, 3, 6)           # months offered as buttons / price rows
MAX_DISCOUNT = 90

def discounts():
    d = store.settings().get("duration_discounts") or {}
    out = {}
    for m in DURATIONS:
        try: out[m] = max(0, min(MAX_DISCOUNT, int(d.get(str(m), store.DEFAULT_SETTINGS["duration_discounts"][str(m)]))))
        except (TypeError, ValueError): out[m] = 0
    return out

def set_discount(months, pct):
    if int(months) not in DURATIONS or not (0 <= int(pct) <= MAX_DISCOUNT):
        return False
    with transaction() as st:
        st["settings"].setdefault("duration_discounts", {})[str(int(months))] = int(pct)
    return True

def price_unit():
    return (store.settings().get("price_unit") or "").strip()

def set_price_unit(txt):
    with transaction() as st:
        st["settings"]["price_unit"] = str(txt or "").strip()[:20]

def price_table(p, disc=None):
    """Display-only prices for a plan: [{months, base, total, pct, per_month, best}] for 1/3/6 months (None if no price)."""
    price = int(p.get("price") or 0)
    if price <= 0:
        return None
    disc = disc or discounts()
    rows = []
    for m in DURATIONS:
        pct = int(disc.get(m, 0))
        total = int(round(price * m * (100 - pct) / 100.0))
        rows.append({"months": m, "base": price * m, "total": total, "pct": pct, "per_month": int(round(total / float(m)))})
    best = max(rows, key=lambda r: (r["pct"], r["months"]))
    for r in rows:
        r["best"] = r is best and r["pct"] > 0
    return rows

def grant_plan(uid, pid, days):
    """Plan + duration. Extends (adds to) an existing active plan's expiry, whichever plan it was; returns
    {"plan": snapshot, "until": ts, "extended": bool} or None."""
    p = get_pplan(pid)
    days = int(days)
    if not p or not p.get("daily") or int(p["daily"]) == 0 or days <= 0:
        return None
    with transaction() as st:
        u = st["users"].setdefault(str(uid), {})
        old = u.get("plan") or {}
        old_until = int(old.get("until", 0) or 0)
        ext = old_until > now()
        until = max(now(), old_until) + days * 86400
        u["plan"] = {"id": p["id"], "title": p["title"], "daily": int(p["daily"]), "until": until}
        u["acc_from"] = now()
        return {"plan": dict(u["plan"]), "until": until, "extended": ext}

# ---------- expiry reminders (once per expiry value, only for grants longer than the lead time) ----------
REMIND_BEFORE = 3 * 86400

def due_reminders(t=None):
    """Users whose plan / timed-unlimited access ends within 3 days and were not reminded for THAT expiry yet.
    Marks them as reminded (at most once per expiry; an extension creates a new expiry => a new reminder).
    Skips grants that were shorter than the lead time (the user was just told the date)."""
    t = t or now(); out = []
    with transaction() as st:
        for uid, u in st["users"].items():
            for kind, until, name in (("plan", int((u.get("plan") or {}).get("until", 0) or 0), (u.get("plan") or {}).get("title", "")),
                                      ("unl", int(u.get("unl_until", 0) or 0), "")):
                if not until or until <= t or until - t > REMIND_BEFORE:
                    continue
                if int(u.get("rem_" + kind, 0) or 0) == until:
                    continue
                u["rem_" + kind] = until
                if until - int(u.get("acc_from", 0) or 0) <= REMIND_BEFORE:
                    continue
                out.append({"uid": int(uid), "kind": kind, "name": name, "until": until})
    return out

def fa_digits(s):
    return str(s).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))

def fmt_num(n, lang):
    s = "{:,}".format(int(n))
    return fa_digits(s.replace(",", "٬")) if lang == "fa" else s

def fmt_pct(n, lang):
    return (fa_digits(int(n)) + "٪") if lang == "fa" else "%d%%" % int(n)

def revoke(uid):
    with transaction() as st:
        u = st["users"].setdefault(str(uid), {})
        for k in ("unlimited", "unl_until", "plan", "plan_day", "plan_used", "rem_plan", "rem_unl", "acc_from"):
            u.pop(k, None)
        u["credits"] = 0

# ---------- referrals ----------
def try_referral(new_uid, referrer_uid):
    with transaction() as st:
        s = st["settings"]
        if not s.get("ref_enabled") or int(new_uid) == int(referrer_uid):
            return None
        ref = st["users"].get(str(referrer_uid)); me = st["users"].get(str(new_uid))
        if ref is None or me is None or me.get("referred_by") or me.get("made"):
            return None
        rb, ib = int(s["ref_bonus"]), int(s["ref_invitee_bonus"])
        cap = int(s.get("ref_max_total") or 0)
        if cap > 0:                                   # inviter reward is capped in total; the friend still gets theirs
            rb = max(0, min(rb, cap - int(ref.get("ref_earned", 0))))
        me["referred_by"] = int(referrer_uid)
        me["credits"] = int(me.get("credits", 0)) + ib
        ref["credits"] = int(ref.get("credits", 0)) + rb
        ref["ref_count"] = int(ref.get("ref_count", 0)) + 1
        ref["ref_earned"] = int(ref.get("ref_earned", 0)) + rb
        return {"bonus": rb, "invitee_bonus": ib, "capped": cap > 0 and rb < int(s["ref_bonus"])}

# ---------- settings ----------
NUM_SETTINGS = ("free_quota", "ref_bonus", "ref_invitee_bonus", "ref_max_total", "ad_every", "max_tracks")

def set_setting(key, value):
    with transaction() as st:
        if key in NUM_SETTINGS:
            st["settings"][key] = int(value)
        elif key in ("ref_enabled", "ads_enabled"):
            st["settings"][key] = bool(value)
        else:
            raise KeyError(key)

def toggle_setting(key):
    with transaction() as st:
        st["settings"][key] = not st["settings"][key]
        return st["settings"][key]

# ---------- price plans ----------
def pplans():
    return store.snapshot()["price_plans"]

def get_pplan(pid):
    return next((p for p in pplans() if p["id"] == int(pid)), None)

def plan_ready(p):
    """A plan is shown to users only when it has a downloads-per-day number (or -1 = unlimited) and a monthly price."""
    d = p.get("daily")
    return bool(d) and int(d) != 0 and int(p.get("price") or 0) > 0

def add_pplan(title, daily, price=0, features="", popular=False):
    if daily is None or int(daily) == 0:
        return None
    with transaction() as st:
        if len(st["price_plans"]) >= MAX_PPLANS:
            return None
        pid = st["next_pplan_id"]; st["next_pplan_id"] = pid + 1
        st["price_plans"].append({"id": pid, "title": str(title).strip()[:60], "daily": max(-1, int(daily)),
                                  "price": max(0, int(price or 0)), "features": str(features).strip()[:200], "popular": bool(popular)})
        return pid

def update_pplan(pid, **kw):
    with transaction() as st:
        p = next((p for p in st["price_plans"] if p["id"] == int(pid)), None)
        if not p:
            return False
        if "daily" in kw and (kw["daily"] is None or int(kw["daily"]) == 0):
            return False
        for k, v in kw.items():
            if k == "title": p[k] = str(v).strip()[:60]
            elif k == "features": p[k] = str(v).strip()[:200]
            elif k == "daily": p["daily"] = max(-1, int(v))
            elif k == "price": p["price"] = max(0, int(v))
            elif k == "popular": p["popular"] = bool(v)
        return True

def toggle_popular(pid):
    with transaction() as st:
        p = next((p for p in st["price_plans"] if p["id"] == int(pid)), None)
        if not p: return None
        p["popular"] = not p.get("popular")
        return p["popular"]

def move_pplan(pid, delta):
    with transaction() as st:
        pl = st["price_plans"]
        i = next((i for i, p in enumerate(pl) if p["id"] == int(pid)), None)
        j = None if i is None else i + int(delta)
        if i is None or j < 0 or j >= len(pl):
            return False
        pl[i], pl[j] = pl[j], pl[i]
        return True

def remove_pplan(pid):
    with transaction() as st:
        n = len(st["price_plans"])
        st["price_plans"] = [p for p in st["price_plans"] if p["id"] != int(pid)]
        return len(st["price_plans"]) != n

def clear_pplans():
    with transaction() as st:
        st["price_plans"] = []

# ---------- ads ----------
AD_LIM = {"fa": 900, "en": 900, "url": 300, "btn": 40}

def ads():
    return store.snapshot()["ads"]

def get_ad(aid):
    return next((a for a in ads() if a["id"] == int(aid)), None)

def add_ad(fa, en, photo=None, url="", btn=""):
    with transaction() as st:
        if len(st["ads"]) >= MAX_ADS:
            return None
        aid = st["next_ad_id"]; st["next_ad_id"] = aid + 1
        st["ads"].append({"id": aid, "fa": fa[:900], "en": (en or fa)[:900], "photo": photo, "url": url[:300],
                          "btn": (btn or "🔗 باز کردن / Open")[:40], "enabled": True, "track": False,
                          "views": 0, "clicks": 0, "created": now()})
        return aid

def update_ad(aid, **kw):
    with transaction() as st:
        a = next((a for a in st["ads"] if a["id"] == int(aid)), None)
        if not a:
            return False
        for k, v in kw.items():
            if k in AD_LIM: a[k] = str(v)[:AD_LIM[k]]
            elif k in ("photo",): a[k] = v
            elif k in ("enabled", "track"): a[k] = bool(v)
        return True

def toggle_ad(aid, key="enabled"):
    with transaction() as st:
        a = next((a for a in st["ads"] if a["id"] == int(aid)), None)
        if not a: return None
        a[key] = not a.get(key)
        return a[key]

def remove_ad(aid):
    with transaction() as st:
        n = len(st["ads"])
        st["ads"] = [a for a in st["ads"] if a["id"] != int(aid)]
        return len(st["ads"]) != n

def ad_count(aid, what):
    with transaction() as st:
        a = next((a for a in st["ads"] if a["id"] == int(aid)), None)
        if a:
            a[what] = int(a.get(what, 0)) + 1
            return a
        return None

def ad_due(uid):
    """Return an ad dict to show after a finished download, or None. Resets the counter when it fires."""
    with transaction() as st:
        s = st["settings"]
        u = st["users"].get(str(uid))
        if not s.get("ads_enabled") or u is None:
            return None
        acc = _access(st, uid)
        if acc["premium"]:
            return None
        every = max(1, int(s.get("ad_every", 3)))
        if int(u.get("dl_since_ad", 0)) < every:
            return None
        live = [a for a in st["ads"] if a.get("enabled")]
        if not live:
            return None
        ad = min(live, key=lambda a: (int(a.get("views", 0)), a["id"]))   # round-robin by views
        u["dl_since_ad"] = 0
        return copy.deepcopy(ad)

def ad_recipients():
    """Users an ad broadcast goes to: everyone except premium/admin/banned users."""
    st = store.snapshot()
    out = []
    for uid, u in st["users"].items():
        if u.get("banned"):
            continue
        if _access(st, int(uid))["premium"]:
            continue
        out.append(int(uid))
    return out

# ---------- required channels ----------
def channels():
    return store.snapshot()["channels"]

def add_channel(chat_id, title, url, username=""):
    """chat_id: numeric id of the verified chat. Returns channel dict, or None if already present."""
    with transaction() as st:
        if any(int(c["chat_id"]) == int(chat_id) for c in st["channels"]):
            return None
        cid = st["next_ch_id"]; st["next_ch_id"] = cid + 1
        c = {"id": cid, "chat_id": int(chat_id), "title": (title or str(chat_id))[:80], "url": url,
             "username": username or ""}
        st["channels"].append(c)
        return copy.deepcopy(c)

def update_channel(cid, **kw):
    with transaction() as st:
        c = next((c for c in st["channels"] if c["id"] == int(cid)), None)
        if not c:
            return False
        c.update({k: v for k, v in kw.items() if k in ("title", "url", "username")})
        return True

def remove_channel(cid):
    with transaction() as st:
        n = len(st["channels"])
        st["channels"] = [c for c in st["channels"] if c["id"] != int(cid)]
        return len(st["channels"]) != n

# ---------- stats / errors ----------
def record_error(uid, platform, code, detail, url=""):
    with transaction() as st:
        st["stats"]["errors"] = int(st["stats"].get("errors", 0)) + 1
        st["errors"].append({"ts": now(), "uid": uid, "platform": platform or "-", "code": code,
                             "detail": (detail or "")[:300], "url": (url or "")[:120]})
        st["errors"] = st["errors"][-MAX_ERRORS:]

def record_link():
    with transaction() as st:
        st["stats"]["links"] = int(st["stats"].get("links", 0)) + 1

def recent_errors(n=10):
    return list(reversed(store.snapshot()["errors"][-n:]))

def stats():
    st = store.snapshot()
    t = now()
    users = st["users"]
    return {
        "users": len(users),
        "active": sum(1 for u in users.values() if t - int(u.get("last_seen", 0)) <= ACTIVE_WINDOW),
        "made": sum(int(u.get("made", 0)) for u in users.values()),
        "by_platform": dict(st["stats"]["downloads"]),
        "errors": int(st["stats"].get("errors", 0)),
        "links": int(st["stats"].get("links", 0)),
        "premium": sum(1 for uid in users if _access(st, int(uid))["kind"] in ("unlimited", "until", "plan")),
        "banned": sum(1 for u in users.values() if u.get("banned")),
        "refs": sum(int(u.get("ref_count", 0)) for u in users.values()),
        "sup_stars": sum(int(x.get("stars", 0)) for x in st.get("supports", [])),
        "sup_count": len(st.get("supports", [])),
    }

def top_referrers(n=5):
    st = store.snapshot()
    rows = [(int(uid), u) for uid, u in st["users"].items() if u.get("ref_count")]
    rows.sort(key=lambda r: -int(r[1]["ref_count"]))
    return rows[:n]

def users_page(page, per=8):
    st = store.snapshot()
    rows = sorted(st["users"].items(), key=lambda kv: -int(kv[1].get("last_seen", 0)))
    pages = max(1, (len(rows) + per - 1) // per)
    page = min(max(1, page), pages)
    return [(int(k), v) for k, v in rows[(page - 1) * per: page * per]], page, pages, len(rows)

def all_user_ids():
    return [int(k) for k, u in store.snapshot()["users"].items() if not u.get("banned")]

def set_cookie_meta(platform, meta):
    with transaction() as st:
        if meta is None:
            st["cookies"].pop(platform, None)
        else:
            st["cookies"][platform] = meta

def cookie_meta():
    return store.snapshot()["cookies"]

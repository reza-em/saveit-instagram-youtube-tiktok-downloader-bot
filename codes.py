"""Discount codes (manual payment, so codes only change what the plans screen shows and what the admin's grant records).

code = {id, code, kind: percent|credits|days|free, value, expires (ts, 0 = never), max_uses (0 = unlimited), once (per-user once),
        plan_id (0 = any plan), months (0 = any of 1/3/6), enabled, used, users: {uid: times}}
 - percent: HELD by the user; the plans screen shows prices with the extra % on top of the duration discount; consumed when the admin grants.
 - credits: INSTANT - the bonus downloads are added to the user's balance the moment the code is entered (no admin).
 - days: INSTANT when the user has an active plan / timed access (its expiry is extended by N days); with no active access the code is
   HELD and the days are added when the admin later grants a plan / timed access.
 - free ("free access"): INSTANT - N days of access (value = days; plan_id 0 = unlimited, else that plan's daily quota).
   Never stacks: it is applied only if it would end LATER than the user's current access (permanent/timed unlimited, plan); otherwise the
   code is kept unconsumed and the user is told until when they are already covered. Admins and permanent-unlimited users are never charged a use.
A use (used += 1, per-user count) is recorded when the code is actually applied."""
import re, time, threading
import store
from store import transaction

MAX_PCT = 90
MAX_FREE_DAYS = 3650
_attempts = {}; _lk = threading.Lock()
TRY_LIMIT, TRY_WINDOW = 8, 3600

def now(): return int(time.time())

def norm(s):
    s = (s or "").strip().translate(store._DIG).upper()
    return s if re.fullmatch(r"[A-Z0-9_\-]{3,20}", s) else ""

def _blank(code, kind, value):
    return {"code": code, "kind": kind, "value": int(value), "expires": 0, "max_uses": 0, "once": True, "plan_id": 0, "months": 0,
            "enabled": True, "used": 0, "users": {}, "created": now()}

def all_codes():
    return store.snapshot().get("codes", [])

def get(cid):
    return next((c for c in all_codes() if c["id"] == int(cid)), None)

def by_text(text):
    t = norm(text)
    return next((c for c in all_codes() if c["code"] == t), None) if t else None

def add(code, kind, value):
    code = norm(code)
    if not code or kind not in ("percent", "credits", "days", "free"):
        return None
    value = int(value)
    if value <= 0 or (kind == "percent" and value > MAX_PCT) or (kind == "free" and value > MAX_FREE_DAYS) or value > 100000:
        return None
    with transaction() as st:
        if any(c["code"] == code for c in st["codes"]):
            return 0
        cid = st["next_code_id"]; st["next_code_id"] = cid + 1
        c = _blank(code, kind, value); c["id"] = cid
        st["codes"].append(c)
        return cid

def update(cid, **kw):
    with transaction() as st:
        c = next((c for c in st["codes"] if c["id"] == int(cid)), None)
        if not c: return False
        for k, v in kw.items():
            if k in ("enabled", "once"): c[k] = bool(v)
            elif k in ("expires", "max_uses", "plan_id", "months"): c[k] = max(0, int(v))
            elif k == "value":
                v = int(v)
                if v <= 0 or (c["kind"] == "percent" and v > MAX_PCT) or (c["kind"] == "free" and v > MAX_FREE_DAYS): return False
                c["value"] = v
        return True

def rename(cid, new):
    """Change the code TEXT. Holders (kept by id) and usage history carry over. -> 'ok' | 'bad' | 'dup' | 'missing'."""
    n = norm(new)
    if not n: return "bad"
    with transaction() as st:
        c = next((c for c in st["codes"] if c["id"] == int(cid)), None)
        if not c: return "missing"
        if any(x["code"] == n and x["id"] != c["id"] for x in st["codes"]): return "dup"
        c["code"] = n
        for u in st["users"].values():
            if (u.get("code") or {}).get("id") == c["id"]: u["code"]["code"] = n
        return "ok"

def remove(cid):
    with transaction() as st:
        n = len(st["codes"]); st["codes"] = [c for c in st["codes"] if c["id"] != int(cid)]
        for u in st["users"].values():                       # nobody keeps holding a deleted code
            if (u.get("code") or {}).get("id") == int(cid): u.pop("code", None)
        return len(st["codes"]) != n

# ---------------------------------------------------------------- validity
def problem(c, uid=None, t=None):
    """None if usable, else: disabled | expired | used_up | already_used."""
    t = t or now()
    if not c.get("enabled"): return "disabled"
    if c.get("expires") and c["expires"] <= t: return "expired"
    if c.get("max_uses") and c.get("used", 0) >= c["max_uses"]: return "used_up"
    if uid is not None and c.get("once") and int(c.get("users", {}).get(str(uid), 0)) > 0: return "already_used"
    return None

def applies(c, plan_id=None, months=None):
    if c.get("plan_id") and plan_id is not None and int(c["plan_id"]) != int(plan_id): return False
    if c.get("months") and months is not None and int(c["months"]) != int(months): return False
    return True

def too_many_tries(uid, hit=False):
    t = time.time()
    with _lk:
        a = [x for x in _attempts.get(uid, []) if t - x < TRY_WINDOW]
        if hit: a.append(t)
        _attempts[uid] = a
        return len(a) >= TRY_LIMIT

# ---------------------------------------------------------------- user side
def _consume(st, cc, uid):
    cc["used"] = int(cc.get("used", 0)) + 1
    cc["users"][str(uid)] = int(cc["users"].get(str(uid), 0)) + 1
    u = st["users"].get(str(uid)) or {}
    if (u.get("code") or {}).get("id") == cc["id"]:
        u.pop("code", None)

def _active_until(st, u, uid):
    """(is_admin, permanent_unlimited, latest expiry among timed-unlimited / plan or 0)"""
    a = st.get("admin_id")
    is_admin = (a is not None and int(uid) == int(a)) or int(uid) in [int(x) for x in st.get("admins", [])]
    pl = u.get("plan") or {}
    t = now()
    exp = [x for x in (int(u.get("unl_until", 0) or 0), int(pl.get("until", 0) or 0)) if x > t]
    return is_admin, bool(u.get("unlimited")), (max(exp) if exp else 0)

def redeem(uid, text):
    """-> (status, code, info). status: ok | bad | disabled | expired | used_up | already_used | throttled | has_longer | has_unlimited | plan_gone.
    info (when ok): {"mode": "held"|"credits"|"days"|"free", "until": ts, "n": amount, "plan": title or None}"""
    if too_many_tries(uid):
        return "throttled", None, {}
    c = by_text(text)
    if not c:
        too_many_tries(uid, hit=True); return "bad", None, {}
    p = problem(c, uid)
    if p: return p, c, {}
    with transaction() as st:
        cc = next((x for x in st["codes"] if x["id"] == c["id"]), None)
        if not cc or problem(cc, uid):
            return (problem(cc, uid) if cc else "bad"), c, {}
        u = st["users"].setdefault(str(uid), {})
        kind = cc["kind"]; v = int(cc["value"]); t = now()
        if kind == "percent":
            u["code"] = {"id": cc["id"], "code": cc["code"], "ts": t}
            return "ok", cc, {"mode": "held"}
        is_admin, perm, cur = _active_until(st, u, uid)
        if kind == "credits":
            u["credits"] = int(u.get("credits", 0)) + v
            _consume(st, cc, uid)
            return "ok", cc, {"mode": "credits", "n": v}
        if kind == "days":
            if is_admin or perm:
                return "has_unlimited", cc, {}
            pl = u.get("plan") or {}
            if int(pl.get("until", 0) or 0) > t:
                pl["until"] = int(pl["until"]) + v * 86400; u["plan"] = pl; until = pl["until"]
            elif int(u.get("unl_until", 0) or 0) > t:
                u["unl_until"] = int(u["unl_until"]) + v * 86400; until = u["unl_until"]
            else:                                   # nothing to extend yet: keep the code until the admin grants something
                u["code"] = {"id": cc["id"], "code": cc["code"], "ts": t}
                return "ok", cc, {"mode": "held"}
            u["acc_from"] = t
            _consume(st, cc, uid)
            return "ok", cc, {"mode": "days", "n": v, "until": until}
        # kind == "free": instant access for N days; never shortens / stacks
        if is_admin or perm:
            return "has_unlimited", cc, {}
        until = t + v * 86400
        if cur >= until:
            return "has_longer", cc, {"until": cur}
        title = None
        if int(cc.get("plan_id") or 0) == 0:
            u["unl_until"] = until
        else:
            pp = next((x for x in st["price_plans"] if x["id"] == int(cc["plan_id"])), None)
            if not pp or not pp.get("daily") or int(pp["daily"]) == 0:
                return "plan_gone", cc, {}
            u["plan"] = {"id": pp["id"], "title": pp["title"], "daily": int(pp["daily"]), "until": until}
            title = pp["title"]; info_daily = int(pp["daily"])
        u["acc_from"] = t
        _consume(st, cc, uid)
        return "ok", cc, {"mode": "free", "n": v, "until": until, "plan": title,
                          "daily": (info_daily if title else None)}

def clear_user(uid):
    with transaction() as st:
        (st["users"].get(str(uid)) or {}).pop("code", None)

def held(uid):
    """The user's code if still held AND usable -> code dict, else None."""
    h = (store.get_user(uid) or {}).get("code")
    if not h: return None
    c = get(h["id"])
    return c if c and problem(c, uid) is None else None

def held_raw(uid):
    """(code, problem) for admin views; code None when nothing held / deleted."""
    h = (store.get_user(uid) or {}).get("code")
    c = get(h["id"]) if h else None
    return (c, problem(c, uid) if c else None)

def holders(cid):
    out = []
    for uid, u in store.snapshot()["users"].items():
        if (u.get("code") or {}).get("id") == int(cid): out.append(int(uid))
    return out

def pct_for(uid, plan_id=None, months=None):
    """Extra percent off for this user's held code applicable to (plan, months); 0 if none."""
    c = held(uid)
    return int(c["value"]) if c and c["kind"] == "percent" and applies(c, plan_id, months) else 0

def price_with_code(total, pct):
    return int(round(total * (100 - pct) / 100.0))

# ---------------------------------------------------------------- admin grant
def take(uid, plan_id=None, days=None, for_plan=True):
    """Called when the admin grants a plan / timed access to a code holder. Consumes the code if it applies.
    -> {"code","kind","value","extra_days","credits","pct","months"} or None (no code / not applicable / no longer valid)."""
    c = held(uid)
    if not c: return None
    months = (days // 30) if days and days % 30 == 0 else None
    if not applies(c, plan_id if for_plan else None, months):
        return None
    if c.get("months") and months is None: return None
    with transaction() as st:
        cc = next((x for x in st["codes"] if x["id"] == c["id"]), None)
        if not cc or problem(cc, uid): return None
        cc["used"] = int(cc.get("used", 0)) + 1
        cc["users"][str(uid)] = int(cc["users"].get(str(uid), 0)) + 1
        u = st["users"].get(str(uid)) or {}
        u.pop("code", None)
        r = {"code": cc["code"], "kind": cc["kind"], "value": cc["value"], "extra_days": 0, "credits": 0, "pct": 0, "months": months}
        if cc["kind"] == "days": r["extra_days"] = cc["value"]
        elif cc["kind"] == "credits":
            r["credits"] = cc["value"]; u["credits"] = int(u.get("credits", 0)) + cc["value"]
        else: r["pct"] = cc["value"]
        return r

def stats(c):
    return {"used": c.get("used", 0), "unique": len(c.get("users", {})), "holders": len(holders(c["id"]))}

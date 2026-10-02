"""Owner-only admin UI for discount codes (callbacks 'a:cd*'; awaiting states 'cd_*')."""
import time, logging
import core as C, store, logic, codes, ui
from core import tr, esc, btn, kb, show, send, set_await, parse_int

log = logging.getLogger("codesui")
OPS = {"cd", "cda", "cdk", "cdv", "cdt", "cdo", "cde", "cdm", "cdp", "cdn", "cdc", "cdx", "cdxy", "cdr"}

def _st(lang, c):
    p = codes.problem(c)
    return tr(lang, {None: "cd_st_on", "disabled": "cd_st_off", "expired": "cd_st_exp", "used_up": "cd_st_used"}[p])

def _what(lang, c):
    return ui.code_what(lang, c)

def panel(chat_id, mid, lang):
    cs = codes.all_codes()
    lines = "\n".join(tr(lang, "cd_line", st=_st(lang, c), code=c["code"], what=_what(lang, c), used=c["used"],
                         mx=("/%d" % c["max_uses"]) if c.get("max_uses") else "") for c in cs) or tr(lang, "cd_none")
    rows = [[btn(("🟢 " if codes.problem(c) is None else "⚪️ ") + c["code"], f"a:cdv:{c['id']}")] for c in cs[:30]]
    rows.append([btn(tr(lang, "cd_b_add"), "a:cda")]); rows.append([btn(tr(lang, "a_back"), "a:home")])
    show(chat_id, mid, tr(lang, "cd_mgr", lines=lines), kb(rows))

def view(chat_id, mid, lang, cid):
    c = codes.get(cid)
    if not c:
        panel(chat_id, mid, lang); return
    plan = logic.get_pplan(c["plan_id"]) if c.get("plan_id") else None
    exp = logic.fmt_date(c["expires"]) if c.get("expires") else tr(lang, "cd_never")
    hs = codes.holders(c["id"])
    hl = tr(lang, "cd_holders", who=", ".join(esc(C.who_label(h)) for h in hs[:10])) if hs else ""
    s = codes.stats(c)
    mtxt = tr(lang, "dur_%dm" % c["months"]) if c.get("months") in logic.DURATIONS else (tr(lang, "cd_any"))
    ptxt = esc(plan["title"]) if plan else tr(lang, "cd_unl_access" if c["kind"] == "free" else "cd_any")
    show(chat_id, mid, tr(lang, "cd_view", code=c["code"], what=_what(lang, c), st=_st(lang, c), exp=exp,
                          mx=c["max_uses"] or tr(lang, "cd_unl"), once=tr(lang, "yes_s" if c["once"] else "no_s"),
                          plan=ptxt, months=mtxt, used=s["used"], uniq=s["unique"], holders=s["holders"], hl=hl),
         kb([[btn(tr(lang, "cd_b_toggle", s=tr(lang, "yes_s" if c["enabled"] else "no_s")), f"a:cdt:{cid}"),
              btn(tr(lang, "cd_b_once", s=tr(lang, "yes_s" if c["once"] else "no_s")), f"a:cdo:{cid}")],
             [btn(tr(lang, "cd_b_exp"), f"a:cde:{cid}"), btn(tr(lang, "cd_b_max"), f"a:cdm:{cid}"), btn(tr(lang, "cd_b_val"), f"a:cdc:{cid}")],
             [btn(tr(lang, "cd_b_rename"), f"a:cdr:{cid}")],
             [btn(tr(lang, "cd_b_access" if c["kind"] == "free" else "cd_b_plan", p=ptxt)[:60], f"a:cdp:{cid}")]
             + ([] if c["kind"] == "free" else [btn(tr(lang, "cd_b_months", m=mtxt), f"a:cdn:{cid}")]),
             [btn(tr(lang, "cd_b_del"), f"a:cdx:{cid}"), btn(tr(lang, "a_back"), "a:cd")]]))

def callback(op, arg, arg2, prev, chat_id, mid, uid, lang):
    if op == "cd": panel(chat_id, mid, lang)
    elif op == "cda": set_await(uid, "cd_code"); send(chat_id, tr(lang, "cd_ask_code"))
    elif op == "cdk":
        code = prev.get("code")
        if arg not in ("percent", "credits", "days", "free") or not code: return
        set_await(uid, "cd_val", {"code": code, "kind": arg}); send(chat_id, tr(lang, "cd_ask_val_" + arg))
    elif op == "cdv": view(chat_id, mid, lang, int(arg))
    elif op == "cdt":
        c = codes.get(int(arg))
        if c: codes.update(c["id"], enabled=not c["enabled"])
        view(chat_id, mid, lang, int(arg))
    elif op == "cdo":
        c = codes.get(int(arg))
        if c: codes.update(c["id"], once=not c["once"])
        view(chat_id, mid, lang, int(arg))
    elif op == "cde": set_await(uid, "cd_exp", {"id": int(arg)}); send(chat_id, tr(lang, "cd_ask_exp"))
    elif op == "cdm": set_await(uid, "cd_max", {"id": int(arg)}); send(chat_id, tr(lang, "cd_ask_max"))
    elif op == "cdc":
        c = codes.get(int(arg))
        if c: set_await(uid, "cd_edit", {"id": c["id"]}); send(chat_id, tr(lang, "cd_ask_val_" + c["kind"]))
    elif op == "cdr": set_await(uid, "cd_ren", {"id": int(arg)}); send(chat_id, tr(lang, "cd_ask_code"))
    elif op == "cdp":                       # cycle: any -> plan1 -> plan2 ... -> any
        c = codes.get(int(arg))
        if c:
            ids = [0] + [p["id"] for p in logic.pplans() if not (c["kind"] == "free" and not (p.get("daily") or 0))]
            nxt = ids[(ids.index(c["plan_id"]) + 1) % len(ids)] if c["plan_id"] in ids else 0
            codes.update(c["id"], plan_id=nxt)
        view(chat_id, mid, lang, int(arg))
    elif op == "cdn":
        c = codes.get(int(arg))
        if c:
            ms = [0] + list(logic.DURATIONS)
            codes.update(c["id"], months=ms[(ms.index(c["months"]) + 1) % len(ms)] if c["months"] in ms else 0)
        view(chat_id, mid, lang, int(arg))
    elif op == "cdx":
        c = codes.get(int(arg))
        if c: show(chat_id, mid, tr(lang, "cd_confirm_del", code=c["code"]), kb([[btn(tr(lang, "yes"), f"a:cdxy:{arg}"), btn(tr(lang, "no"), f"a:cdv:{arg}")]]))
    elif op == "cdxy":
        codes.remove(int(arg)); send(chat_id, tr(lang, "cd_deleted")); panel(chat_id, None, lang)

def text(msg, uid, lang, aw, data):
    chat_id = msg["chat"]["id"]; t = (msg.get("text") or "").strip()
    if aw == "cd_code":
        code = codes.norm(t)
        if not code: send(chat_id, tr(lang, "cd_bad_code")); return True
        if codes.by_text(code): send(chat_id, tr(lang, "cd_dup")); return True
        set_await(uid, None, None); store.update_user(uid, await_data={"code": code})
        send(chat_id, tr(lang, "cd_pick_kind", code=code), kb([[btn(tr(lang, "cd_k_percent"), "a:cdk:percent")],
                                                               [btn(tr(lang, "cd_k_free"), "a:cdk:free")],
                                                               [btn(tr(lang, "cd_k_credits"), "a:cdk:credits"), btn(tr(lang, "cd_k_days"), "a:cdk:days")]]))
        return True
    if aw == "cd_val":
        n = parse_int(t, 1, {"percent": 90, "free": codes.MAX_FREE_DAYS}.get(data["kind"], 100000))
        if n is None: send(chat_id, tr(lang, "cd_bad_val")); return True
        cid = codes.add(data["code"], data["kind"], n); set_await(uid, None)
        if not cid: send(chat_id, tr(lang, "cd_dup")); return True
        send(chat_id, tr(lang, "cd_created")); view(chat_id, None, lang, cid); return True
    if aw == "cd_ren":
        r = codes.rename(data["id"], t)
        if r == "bad": send(chat_id, tr(lang, "cd_bad_code")); return True
        if r == "dup": send(chat_id, tr(lang, "cd_dup")); return True
        set_await(uid, None); send(chat_id, tr(lang, "cd_renamed")); view(chat_id, None, lang, data["id"]); return True
    if aw in ("cd_exp", "cd_max", "cd_edit"):
        n = parse_int(t, 0, 100000)
        if n is None: send(chat_id, tr(lang, "cd_bad_val")); return True
        cid = data["id"]; set_await(uid, None)
        if aw == "cd_exp": codes.update(cid, expires=(int(time.time()) + n * 86400) if n else 0)
        elif aw == "cd_max": codes.update(cid, max_uses=n)
        elif not codes.update(cid, value=n): send(chat_id, tr(lang, "cd_bad_val"))
        view(chat_id, None, lang, cid); return True
    return False

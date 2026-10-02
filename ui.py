"""User-facing screens: menus, welcome (with cached logo), plans, account, invite, quota-out, ads."""
import os, theme, re, hashlib, logging
from urllib.parse import quote
import core as C
import store, logic, codes
from core import tr, esc, btn, kb

log = logging.getLogger("ui")
LOGO = os.path.join(C.BASE, "assets", "logo.jpg")

def main_menu(lang, admin=False):
    rows = [[btn(tr(lang, "b_send"), "m:how"), btn(tr(lang, "b_music"), "m:music")],
            [btn(tr(lang, "b_profile"), "m:profile")],
            [btn(tr(lang, "b_plans"), "m:plans"), btn(tr(lang, "b_me"), "m:me")],
            [btn(tr(lang, "b_invite"), "m:invite"), btn(tr(lang, "b_lang"), "m:lang")],
            [btn(tr(lang, "b_help"), "m:help"), btn(tr(lang, "b_theme"), "m:theme")]]
    if admin:
        rows.append([btn(tr(lang, "b_admin"), "a:home")])
    return kb(rows)

def theme_rows(lang, cur, prefix="th:", with_auto=False, back="m:menu"):
    rows = []
    for k in theme.ORDER:
        rows.append([btn(("✅ " if k == cur else "") + theme.THEMES[k][lang], prefix + k)])
    if with_auto:
        rows.append([btn(("✅ " if cur == "auto" else "") + theme.name(lang, "auto"), prefix + "auto")])
    rows.append([btn(tr(lang, "b_menu") if back == "m:menu" else tr(lang, "a_back"), back)])
    return rows

def show_theme(chat_id, uid, lang, mid=None):
    cur = store.get_user(uid).get("theme") or ""
    txt = tr(lang, "theme_pick", cur=theme.THEMES[theme.user_theme(uid)][lang])
    C.show(chat_id, mid, txt, kb(theme_rows(lang, cur)))

def theme_chosen(chat_id, uid, lang, mid, key):
    if theme.set_user_theme(uid, key):
        theme.set_user(uid)
        C.show(chat_id, mid, tr(lang, "theme_set", name=theme.THEMES[key][lang]), kb(theme_rows(lang, key)))

def lang_menu():
    return kb([[btn("🇮🇷 فارسی", "l:fa"), btn("🇬🇧 English", "l:en")]])

def show_menu(chat_id, lang, uid=None):
    C.send(chat_id, tr(lang, "menu_title"), main_menu(lang, bool(uid) and logic.is_admin(uid)))

# ---------------- logo / welcome ----------------
def _logo_sha():
    try:
        with open(LOGO, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None

def send_welcome(chat_id, lang, uid):
    text = tr(lang, "welcome")
    markup = main_menu(lang, logic.is_admin(uid))
    sha = _logo_sha()
    if not sha:
        C.send(chat_id, text, markup); return
    with store.transaction(write=False) as st:
        logo = dict(st.get("logo") or {})
    fid = logo.get("file_id") if logo.get("sha") == sha else None    # new logo file => cache invalid
    data = {"chat_id": chat_id, "caption": text, "parse_mode": "HTML", "reply_markup": markup}
    if fid:
        try:
            C.call("sendPhoto", dict(data, photo=fid)); return
        except C.ApiError as e:
            log.warning("cached logo file_id rejected (%s); re-uploading", C.safe(e)[:60])
    try:
        with open(LOGO, "rb") as f:
            res = C.call("sendPhoto", data, files={"photo": ("logo.jpg", f, "image/jpeg")}, timeout=90)
        new = (res.get("photo") or [{}])[-1].get("file_id")
        if new:
            with store.transaction() as st:
                st["logo"] = {"sha": sha, "file_id": new}
    except C.ApiError as e:
        log.warning("logo send failed: %s", C.safe(e)[:80])
        C.send(chat_id, text, markup)

# ---------------- plans / account / invite ----------------
def feature_lines(features):
    parts = [x.strip(" •-\t") for x in re.split(r"[|;\n]+", features or "") if x.strip(" •-\t")]
    return parts[:8]

def unit(lang):
    return logic.price_unit() or tr(lang, "price_unit_default")

def daily_str(lang, daily):
    return tr(lang, "daily_unl") if int(daily) < 0 else tr(lang, "daily_n", n=logic.fa_digits(int(daily)) if lang == "fa" else int(daily))

def plan_card(lang, p, uid=None):
    """Display-only prices: monthly base, then 1/3/6-month totals with discount, percent saved, per-month price."""
    fl = feature_lines(p.get("features"))
    feats = ("\n" + "\n".join("✔️ " + esc(x) for x in fl)) if fl else ""
    u = unit(lang)
    rows = []
    for r in logic.price_table(p) or []:
        line = tr(lang, "pp_row", months=logic.fa_digits(r["months"]) if lang == "fa" else r["months"],
                  total=logic.fmt_num(r["total"], lang), unit=u)
        if r["pct"] > 0:
            line += " " + tr(lang, "pp_save", pct=logic.fmt_pct(r["pct"], lang))
        if r["months"] > 1:
            line += " " + tr(lang, "pp_permonth", v=logic.fmt_num(r["per_month"], lang), unit=u)
        if r["best"]:
            line += " " + tr(lang, "pp_best")
        rows.append(line)
    cp = []
    if uid is not None:
        for r in logic.price_table(p) or []:
            pct = codes.pct_for(uid, p.get("id"), r["months"])
            if pct:
                cp.append(tr(lang, "pp_code_row", code=codes.held(uid)["code"], pct=logic.fmt_pct(pct, lang),
                             months=logic.fa_digits(r["months"]) if lang == "fa" else r["months"],
                             total=logic.fmt_num(codes.price_with_code(r["total"], pct), lang), unit=u))
    if cp:
        rows += cp
    card = tr(lang, "plans_card", title=esc(p["title"]), daily=daily_str(lang, p["daily"]),
              price=logic.fmt_num(p["price"], lang), unit=u, rows="\n".join(rows), feats=feats)
    if p.get("popular"):
        card = tr(lang, "pp_popular") + "\n" + card + "\n" + tr(lang, "pp_popular_line")
    return card

def free_limit_lines(lang, sep="\n"):
    """Admin-configured free daily limits per platform (what every normal user gets per day)."""
    out = []
    with store.transaction(write=False) as st:
        lims = {p: logic.plat_limit(st, p) for p in logic.PLATFORMS}
    for pl, v in lims.items():
        val = tr(lang, "lim_unl") if v < 0 else (tr(lang, "lim_blocked") if v == 0 else str(v))
        out.append("%s %s" % (tr(lang, "plat_" + pl), val))
    return sep.join(out)

def plans_screen_text(lang, uid):
    ready = [p for p in logic.pplans() if logic.plan_ready(p)]
    parts = [tr(lang, "plans_title"), tr(lang, "plans_free", lines=free_limit_lines(lang))]
    parts += [plan_card(lang, p, uid) for p in ready]
    if not ready:
        parts.append(tr(lang, "plans_none"))
    parts.append(tr(lang, "plans_footer", uid=uid, note=C.support_note(lang)))
    return "\n\n━━━━━━━━━━\n\n".join(parts)

def show_plans(chat_id, uid, lang):
    rows = [C.contact_btns(lang), [btn(tr(lang, "b_code"), "m:code")]]
    if store.settings()["ref_enabled"]:
        rows.append([btn(tr(lang, "b_invite"), "m:invite")])
    rows.append([btn(tr(lang, "b_menu"), "m:menu")])
    C.send(chat_id, plans_screen_text(lang, uid), kb(rows))

def price_block(lang):
    plans = [p for p in logic.pplans() if logic.plan_ready(p)]
    if not plans:
        return ""
    lines = [tr(lang, "pp_head_user"), tr(lang, "pp_free_line", lines=free_limit_lines(lang, " · "))]
    for p in plans:
        lines.append(tr(lang, "pp_user_line", star="⭐ " if p.get("popular") else "", title=esc(p["title"]),
                        daily=daily_str(lang, p["daily"]), price=logic.fmt_num(p["price"], lang) + " " + unit(lang)))
    return "\n".join(lines) + "\n\n"

def about_text(lang):
    sp = store.support().get("primary") or "example_owner"
    return tr(lang, "about", sp=esc(sp))

def help_text(lang):
    import engine
    return tr(lang, "help", max_tracks=engine.max_tracks())

def limit_str(lang, p):
    """One platform's free-limit state for humans: '3/5', '♾', or '⛔'."""
    if p["unlimited"]: return tr(lang, "lim_unl")
    if p["blocked"]: return tr(lang, "lim_blocked")
    return "%d/%d" % (p["left"], p["limit"])

def per_platform_lines(lang, a, only_left=False):
    lines = []
    for pl in logic.PLATFORMS:
        p = a["per"][pl]
        if only_left and (pl == "music_id" or not (p["unlimited"] or (p["left"] or 0) > 0)):
            continue
        lines.append("%s — %s" % (tr(lang, "plat_" + pl), limit_str(lang, p)))
    return "\n".join(lines)

def show_upgrade(chat_id, uid, lang, platform=None):
    """Free quota of `platform` exhausted (or blocked): remaining per platform + plans + numeric ID + contact button(s)."""
    rows = [C.contact_btns(lang)]
    if store.settings()["ref_enabled"]:
        rows.append([btn(tr(lang, "b_invite"), "m:invite")])
    a = logic.access_info(uid, platform)
    if platform:
        p = a["per"][platform]
        head = tr(lang, "quota_blocked" if p["blocked"] else "quota_out_plat", plat=tr(lang, "plat_" + platform),
                  used=p["used"], limit=p["limit"])
    else:
        head = tr(lang, "quota_out_all")
    others = per_platform_lines(lang, a, only_left=True)
    left = (tr(lang, "quota_left_elsewhere", lines=others) + "\n\n") if others else ""
    C.send(chat_id, tr(lang, "quota_out", head=head, left=left, uid=uid, price=price_block(lang), note=C.support_note(lang)), kb(rows))

def show_me(chat_id, uid, lang):
    a = logic.access_info(uid); u = store.get_user(uid)
    lines = [tr(lang, "me_head")]
    if a["kind"] == "admin": lines.append(tr(lang, "me_admin"))
    elif a["kind"] == "unlimited": lines.append(tr(lang, "me_unl"))
    elif a["kind"] == "until": lines.append(tr(lang, "me_until", date=logic.fmt_date(a["until"]), left=logic.fa_digits(logic.days_left(a["until"])) if lang == "fa" else logic.days_left(a["until"])))
    elif a["kind"] == "plan":
        pl = a["plan"]; dl = logic.days_left(pl["until"])
        lines.append(tr(lang, "me_plan", title=esc(pl["title"]), daily=daily_str(lang, pl["daily"]), date=logic.fmt_date(pl["until"]),
                        left=logic.fa_digits(dl) if lang == "fa" else dl))
        if pl["left"] is not None:
            lines.append(tr(lang, "me_plan_today", n=pl["left"], total=pl["daily"]))
    else:
        lines.append(tr(lang, "me_free_head"))
        lines.append(per_platform_lines(lang, a))
    lines.append(tr(lang, "me_credits", n=a["credits"]))
    lines.append(tr(lang, "me_made", n=u.get("made", 0)))
    lines.append(tr(lang, "me_ref", n=u.get("ref_count", 0), b=u.get("ref_earned", 0)))
    hc = codes.held(uid)
    if hc:
        lines.append(tr(lang, "me_code", code=hc["code"], what=code_what(lang, hc)))
    lines.append(tr(lang, "me_id", uid=uid))
    cb = C.contact_btns(lang)
    rows = [[btn(tr(lang, "b_invite"), "m:invite"), btn(tr(lang, "b_code"), "m:code")], cb, [btn(tr(lang, "b_menu"), "m:menu")]]
    C.send(chat_id, "\n".join(lines), kb(rows))

def invite_share_url(link, body):
    if C.IS_BALE:                      # Bale has no t.me/share/url: a plain-link share button is not possible -> copy text
        return C.link(C.BOT_USERNAME)
    return "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote(body, safe="")

def code_what(lang, c):
    if c["kind"] == "free":
        p = logic.get_pplan(c["plan_id"]) if c.get("plan_id") else None
        return tr(lang, "code_what_free", v=logic.fa_digits(c["value"]) if lang == "fa" else c["value"],
                  what=(esc(p["title"]) if p else tr(lang, "cd_unl_access")))
    v = logic.fmt_pct(c["value"], lang) if c["kind"] == "percent" else (logic.fa_digits(c["value"]) if lang == "fa" else c["value"])
    return tr(lang, "code_what_" + c["kind"], v=v)

def code_scope(lang, c):
    parts = []
    if c.get("plan_id"):
        p = logic.get_pplan(c["plan_id"])
        parts.append(esc(p["title"]) if p else "#%s" % c["plan_id"])
    if c.get("months"):
        parts.append(tr(lang, "dur_%dm" % c["months"]) if c["months"] in logic.DURATIONS else "%s×30d" % c["months"])
    return tr(lang, "code_scope", scope=" · ".join(parts)) if parts else ""

def ask_code(chat_id, uid, lang):
    C.set_await(uid, "code_enter"); C.send(chat_id, tr(lang, "code_ask"))

def code_entered(chat_id, uid, lang, text):
    st, c, info = codes.redeem(uid, text)
    fa = lang == "fa"
    N = lambda x: logic.fa_digits(x) if fa else x
    nav = kb([[btn(tr(lang, "b_plans"), "m:plans"), btn(tr(lang, "b_me"), "m:me")], [btn(tr(lang, "b_menu"), "m:menu")]])
    if st == "ok":
        C.set_await(uid, None)
        mode = info["mode"]
        if mode == "free":
            d = logic.fmt_date(info["until"])
            if info.get("plan"):
                msg = tr(lang, "code_ok_free_plan", code=c["code"], n=N(info["n"]), title=esc(info["plan"]), daily=daily_str(lang, info["daily"]), date=d)
            else:
                msg = tr(lang, "code_ok_free_unl", code=c["code"], n=N(info["n"]), date=d)
        elif mode == "credits":
            msg = tr(lang, "code_ok_credits_now", code=c["code"], v=N(info["n"]))
        elif mode == "days":
            msg = tr(lang, "code_ok_days_now", code=c["code"], v=N(info["n"]), date=logic.fmt_date(info["until"]))
        else:                                                   # held: percent (prices) or days without active access
            msg = tr(lang, "code_ok_" + c["kind"], code=c["code"], pct=logic.fmt_pct(c["value"], lang), v=N(c["value"]))
            sc = code_scope(lang, c)
            msg += ("\n" + sc if sc else "") + "\n" + tr(lang, "code_pay_note")
        C.send(chat_id, msg, nav)
    elif st == "has_longer":
        C.set_await(uid, None)
        C.send(chat_id, tr(lang, "code_has_longer", date=logic.fmt_date(info["until"])), nav)
    else:
        if st in ("throttled", "has_unlimited", "plan_gone"): C.set_await(uid, None)
        C.send(chat_id, tr(lang, {"bad": "code_bad", "disabled": "code_disabled", "expired": "code_expired", "used_up": "code_used_up",
                                  "already_used": "code_already", "throttled": "code_throttled", "has_unlimited": "code_has_unlimited",
                                  "plan_gone": "code_plan_gone"}[st]))
    return st

def show_invite(chat_id, uid, lang):
    s = store.settings()
    if not s["ref_enabled"]:
        C.send(chat_id, tr(lang, "invite_off"), main_menu(lang)); return
    u = store.get_user(uid)
    link = C.link(C.BOT_USERNAME) + f"?start=ref_{uid}"
    inv = tr(lang, "invite_invitee_line2", b=s["ref_invitee_bonus"]) if s["ref_invitee_bonus"] > 0 else ""
    cap = int(s.get("ref_max_total") or 0)
    capl = tr(lang, "invite_cap_line", cap=cap) if cap > 0 else ""
    C.send(chat_id, tr(lang, "invite_text", link=link, bonus=s["ref_bonus"], invitee_line=inv, cap_line=capl,
                       n=u.get("ref_count", 0), earned=u.get("ref_earned", 0)) + "\n\n" + tr(lang, "invite_forward_hint"),
           kb([[btn(tr(lang, "b_code_enter"), "m:code")]]))
    body = tr(lang, "invite_share_text")
    rows = [[btn(tr(lang, "b_menu"), "m:menu")]]
    if not C.IS_BALE:                   # Bale has no share-URL endpoint: the ready-to-forward message is enough
        rows.insert(0, [btn(tr(lang, "b_share"), url=invite_share_url(link, body))])
    C.send(chat_id, esc(body) + "\n" + link, kb(rows))

# ---------------- ads ----------------
def ad_markup(ad, lang):
    if not ad.get("url"):
        return None
    label = ad.get("btn") or tr(lang, "b_ad_open")
    if ad.get("track"):
        return kb([[btn(label, f"ad:{ad['id']}")]])
    return kb([[btn(label, url=ad["url"])]])

def send_ad(chat_id, ad, lang, count_view=True):
    text = tr(lang, "ad_label") + "\n" + esc(ad["fa"] if lang == "fa" else ad["en"])
    markup = ad_markup(ad, lang)
    try:
        if ad.get("photo"):
            data = {"chat_id": chat_id, "photo": ad["photo"], "caption": text[:1024], "parse_mode": "HTML"}
            if markup: data["reply_markup"] = markup
            r = C.call("sendPhoto", data)
        else:
            r = C.send(chat_id, text, markup)
    except C.ApiError as e:
        log.warning("ad send failed: %s", C.safe(e)[:80]); return None
    if r is not None and count_view:
        logic.ad_count(ad["id"], "views")
    return r

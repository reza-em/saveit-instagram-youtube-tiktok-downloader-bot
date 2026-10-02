"""Admin panel (owner + extra admins). Callback data prefix 'a:'. Text/doc replies handled via awaiting states."""
import os, re, json, time, threading, logging
import core as C
import store, logic, engine, gate, ui, theme, targeted, posterui, codes, codesui, campaign, stars
from core import tr, esc, btn, kb, show, send, parse_int, who_label, uname_of, set_await

log = logging.getLogger("admin")
# what extra (non-owner) admins may do
EXTRA_OPS = {"home", "stats", "ul", "u", "gc", "noop", "grant", "revoke", "credits", "ugf", "ugd", "ugm", "uc", "ur", "urc", "ugp", "ugpd", "ugpc", "ban", "bn", "ubn"} | campaign.OPS | {"tmp", "tmt"}      # campaigns reuse the multi-select user picker; the rest of targeted messaging stays owner-only
EXTRA_AWAIT = {"a_target", "a_num", "cp_name", "cp_text", "cp_img", "cp_ids"}
PLAT_EMOJI = {"youtube": "▶️", "instagram": "📸", "tiktok": "🎵", "pinterest": "📌", "linkedin": "💼", "threads": "🧵", "twitter": "🐦", "soundcloud": "☁️", "spotify": "🎧", "music_id": "🎶"}

def back_row(lang, to="a:home"):
    return [btn(tr(lang, "a_back"), to)]

def home(chat_id, mid, lang, uid=None):
    if uid is not None and not logic.is_owner(uid):
        rows = [[btn(tr(lang, "a_b_stats"), "a:stats"), btn(tr(lang, "a_b_users"), "a:ul:1")],
                [btn(tr(lang, "a_b_grant"), "a:grant"), btn(tr(lang, "a_b_revoke"), "a:revoke")],
                [btn(tr(lang, "a_b_credits"), "a:credits"), btn(tr(lang, "a_b_ban"), "a:ban")],
                [btn(tr(lang, "a_b_campaign"), "a:cp"), btn(tr(lang, "a_b_bc"), "a:bcm")],
                [btn(tr(lang, "b_menu"), "m:menu")]]
        show(chat_id, mid, tr(lang, "a_title"), kb(rows)); return
    rows = [[btn(tr(lang, "a_b_stats"), "a:stats"), btn(tr(lang, "a_b_users"), "a:ul:1")],
            [btn(tr(lang, "a_b_grant"), "a:grant"), btn(tr(lang, "a_b_revoke"), "a:revoke")],
            [btn(tr(lang, "a_b_credits"), "a:credits"), btn(tr(lang, "a_b_ban"), "a:ban")],
            [btn(tr(lang, "a_b_prices"), "a:pp"), btn(tr(lang, "a_b_codes"), "a:cd")],
            [btn(tr(lang, "a_b_ads"), "a:ads"), btn(tr(lang, "a_b_channels"), "a:ch")],
            [btn(tr(lang, "a_b_cookies"), "a:ck")],
            [btn(tr(lang, "a_b_update"), "a:upd"), btn(tr(lang, "a_b_errors"), "a:err")],
            [btn(tr(lang, "a_b_limits"), "a:lim")],
            [btn(tr(lang, "a_b_settings"), "a:set"), btn(tr(lang, "a_b_ref"), "a:ref")],
            [btn(tr(lang, "a_b_bc"), "a:bcm"), btn(tr(lang, "a_b_campaign"), "a:cp")],
            [btn(tr(lang, "a_b_admins"), "a:ad")],
            [btn(tr(lang, "a_b_support"), "a:su")] + ([btn(tr(lang, "a_b_poster"), "a:pc")] if C.PLAT.poster else []),
            [btn(tr(lang, "b_menu"), "m:menu")]]
    show(chat_id, mid, tr(lang, "a_title"), kb(rows))

def stats(chat_id, mid, lang):
    s = logic.stats()
    plat = "\n".join(f"{PLAT_EMOJI.get(p, '•')} {p}: {s['by_platform'].get(p, 0)}" for p in logic.PLATFORMS)
    txt = tr(lang, "a_stats", plat=plat, **{k: v for k, v in s.items() if k != "by_platform"})
    if stars.enabled():
        txt += "\n\n" + tr(lang, "a_stats_sup", n=s["sup_count"], stars=s["sup_stars"], amounts=" / ".join(str(a) for a in stars.amounts()))
    show(chat_id, mid, txt, kb(([[btn(tr(lang, "sp_b_amounts"), "a:spa")]] if stars.enabled() and logic.is_owner(chat_id) else []) + [back_row(lang)]))

def users_list(chat_id, mid, lang, page):
    rows_, page, pages, total = logic.users_page(page)
    rows = []
    for uid, u in rows_:
        lbl = f"{'🚫 ' if u.get('banned') else ''}{u.get('name') or uid} {uname_of(u)}".strip()[:40]
        rows.append([btn(lbl, f"a:u:{uid}")])
    nav = []
    if page > 1: nav.append(btn("⬅️", f"a:ul:{page-1}"))
    if page < pages: nav.append(btn("➡️", f"a:ul:{page+1}"))
    if nav: rows.append(nav)
    rows.append(back_row(lang))
    show(chat_id, mid, tr(lang, "a_users", page=page, pages=pages, total=total), kb(rows))

def user_status(lang, a):
    return {"admin": tr(lang, "a_status_admin"), "unlimited": tr(lang, "a_status_unl"),
            "until": tr(lang, "a_status_until", date=logic.fmt_date(a["until"])),
            "plan": tr(lang, "a_status_plan", title=esc(a["plan"]["title"] if a.get("plan") else ""), date=logic.fmt_date(a["plan"]["until"]) if a.get("plan") else "",
                       left=logic.days_left(a["plan"]["until"]) if a.get("plan") else 0), "quota": tr(lang, "a_status_quota")}[a["kind"]]

def _dur_label(lang, m):
    return tr(lang, "dur_%dm" % m)

def quick_plan_rows(lang, uid):
    rows = []
    for p in logic.pplans():
        if p.get("daily") and int(p["daily"]) != 0:
            rows.append([btn(tr(lang, "pp_quick_plan", title=p["title"], daily=ui.daily_str(lang, p["daily"]))[:55], f"a:ugp:{uid}:{p['id']}")])
    return rows

def code_note(lang, t):
    """Line telling the admin which discount code (if any) the user holds."""
    c, prob = codes.held_raw(t)
    if not c: return ""
    if prob: return "\n\n" + tr(lang, "a_code_holds_bad", code=c["code"])
    return "\n\n" + tr(lang, "a_code_holds", code=c["code"], what=ui.code_what(lang, c), scope=ui.code_scope(lang, c))

def apply_code(chat_id, lang, t, pid, days, for_plan=True):
    """Admin granted a plan / timed access: consume the user's code when it applies and record/apply its bonus."""
    r = codes.take(t, pid, days, for_plan)
    if not r: return
    what = ui.code_what(lang, {"kind": r["kind"], "value": r["value"]})
    if r["extra_days"]:
        if for_plan:
            with store.transaction() as st:
                u = st["users"].setdefault(str(t), {}); pl = u.get("plan")
                if pl: pl["until"] = int(pl["until"]) + r["extra_days"] * 86400
        else:
            logic.add_days(t, r["extra_days"])
    send(chat_id, tr(lang, "a_code_bonus", code=r["code"], what=what))
    ul = C.user_lang(t)
    C.tell_user(t, "u_code_bonus", code=r["code"], what=ui.code_what(ul, {"kind": r["kind"], "value": r["value"]}))

def grant_choice(chat_id, mid, lang, t):
    rows = [[btn(tr(lang, "a_g_unl"), f"a:ugf:{t}")], [btn(tr(lang, "a_g_days"), f"a:ugd:{t}")],
            [btn(tr(lang, "a_g_credits"), f"a:uc:{t}")],
            [btn(tr(lang, "a_g_unl_m", d=_dur_label(lang, m)), f"a:ugm:{t}:{m}") for m in logic.DURATIONS]]
    qp = quick_plan_rows(lang, t)
    if qp:
        rows.append([btn(tr(lang, "pp_quick"), "a:noop")]); rows += qp
    rows.append(back_row(lang))
    show(chat_id, mid, tr(lang, "a_grant_choice", who=esc(who_label(t))) + code_note(lang, t), kb(rows))

def grant_duration(chat_id, mid, lang, t, pid):
    p = logic.get_pplan(pid)
    if not p or not p.get("daily"):
        send(chat_id, tr(lang, "pp_gone")); return
    rows = [[btn(_dur_label(lang, m), f"a:ugpd:{t}:{pid}_{m * logic.MONTH_DAYS}") for m in logic.DURATIONS],
            [btn(tr(lang, "a_g_custom_days"), f"a:ugpc:{t}:{pid}")], back_row(lang, f"a:gc:{t}")]
    show(chat_id, mid, tr(lang, "a_pick_duration", title=esc(p["title"]), daily=ui.daily_str(lang, p["daily"]), who=esc(who_label(t))), kb(rows))

def finish_plan_grant(chat_id, mid, lang, t, pid, days):
    r = logic.grant_plan(t, pid, days)
    if not r:
        send(chat_id, tr(lang, "pp_gone")); return
    pl = r["plan"]; d = logic.fmt_date(r["until"])
    show(chat_id, mid, tr(lang, "a_done_plan", title=esc(pl["title"]), n=days, who=esc(who_label(t)), date=d) + (tr(lang, "a_ext_note") if r["extended"] else ""),
         kb([back_row(lang)]))
    C.tell_user(t, "u_plan_ext" if r["extended"] else "u_plan", title=esc(pl["title"]), daily=ui.daily_str(C.user_lang(t), pl["daily"]),
                n=days, date=d, left=logic.days_left(r["until"]))
    apply_code(chat_id, lang, t, pid, days, True)

def user_card(chat_id, mid, lang, uid):
    u = store.get_user(uid)
    if not u:
        send(chat_id, tr(lang, "a_not_found")); return
    a = logic.access_info(uid)
    show(chat_id, mid, tr(lang, "a_user", who=esc(who_label(uid)), made=u.get("made", 0), free=ui.per_platform_lines(lang, a),
                          credits=a["credits"], status=user_status(lang, a),
                          ban=tr(lang, "a_ban_yes" if u.get("banned") else "a_ban_no")),
         kb([[btn(tr(lang, "a_b_grant"), f"a:gc:{uid}"), btn(tr(lang, "a_b_revoke"), f"a:ur:{uid}")],
             [btn(tr(lang, "a_b_unban1" if u.get("banned") else "a_b_ban1"), f"a:{'ubn' if u.get('banned') else 'bn'}:{uid}")],
             back_row(lang, "a:ul:1")]))

def settings(chat_id, mid, lang):
    s = store.settings()
    show(chat_id, mid, tr(lang, "a_settings", quota=s["free_quota"], ads=tr(lang, "on" if s["ads_enabled"] else "off"), every=s["ad_every"], tracks=s["max_tracks"]),
         kb([[btn(tr(lang, "a_s_quota"), "a:sn:free_quota"), btn(tr(lang, "a_s_maxtracks"), "a:sn:max_tracks")],
             [btn(tr(lang, "a_b_limits"), "a:lim"), btn(tr(lang, "a_b_theme"), "a:th")], back_row(lang)]))

def theme_panel(chat_id, mid, lang):
    cur = store.settings().get("theme_default", "auto")
    if cur != "auto" and cur not in theme.THEMES: cur = "auto"
    rows = ui.theme_rows(lang, cur, prefix="a:thd:", with_auto=True, back="a:set")
    if not C.IS_BALE:                       # Bale has no coloured buttons
        rows.insert(-1, [btn(tr(lang, "a_th_styles", s=tr(lang, "on" if theme.styles_enabled() else "off")), "a:ths")])
    show(chat_id, mid, tr(lang, "a_theme", cur=theme.name(lang, cur), now=theme.THEMES[theme.default_theme()][lang]), kb(rows))

def _lim_val(lang, v):
    return tr(lang, "lim_unl") if v < 0 else (tr(lang, "lim_blocked") if v == 0 else str(v))

def limits_panel(chat_id, mid, lang):
    s = store.settings()
    ov = s.get("plat_limits", {})
    with store.transaction(write=False) as st:
        vals = {p: logic.plat_limit(st, p) for p in logic.PLATFORMS}
    lines = "\n".join(tr(lang, "a_limit_line", plat=tr(lang, "plat_" + p), val=_lim_val(lang, vals[p]),
                          tag=("" if p in ov else tr(lang, "a_limit_default_tag"))) for p in logic.PLATFORMS)
    rows, row = [], []
    for p in logic.PLATFORMS:
        row.append(btn(f"{tr(lang, 'plat_' + p)} · {_lim_val(lang, vals[p])}"[:40], f"a:lp:{p}"))
        if len(row) == 2: rows.append(row); row = []
    if row: rows.append(row)
    rows.append([btn(tr(lang, "a_s_quota"), "a:sn:free_quota")])
    rows.append(back_row(lang))
    show(chat_id, mid, tr(lang, "a_limits", lines=lines, default=s["free_quota"]), kb(rows))

def limit_plat(chat_id, mid, lang, p):
    if p not in logic.PLATFORMS: return
    with store.transaction(write=False) as st:
        v = logic.plat_limit(st, p); own = p in st["settings"].get("plat_limits", {})
    show(chat_id, mid, tr(lang, "a_limit_plat", plat=tr(lang, "plat_" + p), val=_lim_val(lang, v),
                          tag=("" if own else tr(lang, "a_limit_default_tag"))),
         kb([[btn(tr(lang, "a_limit_set"), f"a:lps:{p}"), btn(tr(lang, "a_limit_unl"), f"a:lpu:{p}")],
             [btn(tr(lang, "a_limit_zero"), f"a:lpz:{p}"), btn(tr(lang, "a_limit_reset"), f"a:lpr:{p}")],
             back_row(lang, "a:lim")]))

def ref_panel(chat_id, mid, lang):
    s = store.settings()
    top = logic.top_referrers(5)
    lines = "\n".join(f"{i+1}. {esc(u.get('name') or uid)} {uname_of(u)} — {u['ref_count']}" for i, (uid, u) in enumerate(top)) or tr(lang, "a_ref_none")
    txt = tr(lang, "a_ref", s=tr(lang, "on" if s["ref_enabled"] else "off"), total=logic.stats()["refs"],
             rb=s["ref_bonus"], ib=s["ref_invitee_bonus"], top=lines,
             cap=(s.get("ref_max_total") or tr(lang, "cd_unl")))
    txt += tr(lang, "a_ref_hint")
    show(chat_id, mid, txt, kb([[btn(tr(lang, "a_s_reftoggle", s=tr(lang, "on" if s["ref_enabled"] else "off")), "a:rt")],
                                [btn(tr(lang, "a_s_rb"), "a:sn:ref_bonus"), btn(tr(lang, "a_s_ib"), "a:sn:ref_invitee_bonus")],
                                [btn(tr(lang, "a_s_rmax"), "a:sn:ref_max_total")],
                                back_row(lang)]))

def errors_panel(chat_id, mid, lang):
    errs = logic.recent_errors(10)
    lines = "\n\n".join(f"🕐 {logic.fmt_date(e['ts'])} · {esc(e['platform'])} · <b>{esc(e['code'])}</b> · uid {e['uid']}\n<code>{esc((e['detail'] or '')[-160:])}</code>"
                        for e in errs) or tr(lang, "a_errors_none")
    show(chat_id, mid, tr(lang, "a_errors", lines=lines[:3500]), kb([back_row(lang)]))

# ---------------- admins / support ----------------
def admins_panel(chat_id, mid, lang):
    owner = store.admin_id()
    lines = "\n".join("• " + esc(who_label(a)) for a in logic.list_admins()) or tr(lang, "ad_none")
    rows = [[btn(tr(lang, "ad_add"), "a:ada")]]
    for a in logic.list_admins():
        rows.append([btn(f"🗑 {store.get_user(a).get('name') or a} · {a}"[:55], f"a:adx:{a}")])
    rows.append([btn(tr(lang, "ad_owner"), "a:own")]); rows.append(back_row(lang))
    show(chat_id, mid, tr(lang, "ad_menu", owner=esc(who_label(owner)) if owner else "-", lines=lines), kb(rows))

def support_panel(chat_id, mid, lang):
    sp = store.support()
    txt = tr(lang, "su_menu", p=sp["primary"], b=("@" + sp["backup"]) if sp["backup"] else tr(lang, "su_none"))
    rows = [[btn(tr(lang, "su_b_primary"), "a:sup:primary"), btn(tr(lang, "su_b_backup"), "a:sup:backup")]]
    if sp["backup"]: rows.append([btn(tr(lang, "su_b_clear"), "a:suc")])
    rows.append(back_row(lang)); show(chat_id, mid, txt, kb(rows))

# ---------------- plans ----------------
def plan_line(lang, pos, p):
    price = (logic.fmt_num(p["price"], lang) + " " + ui.unit(lang)) if int(p.get("price") or 0) > 0 else "—"
    return tr(lang, "pp_line", pos=pos, star="⭐ " if p.get("popular") else "", title=esc(p["title"]),
              daily=ui.daily_str(lang, p["daily"]) if p.get("daily") else "❓", price=price,
              hid="" if logic.plan_ready(p) else tr(lang, "pp_hidden"))

def plans_panel(chat_id, mid, lang):
    plans = logic.pplans()
    lines = "\n".join(plan_line(lang, i + 1, p) for i, p in enumerate(plans)) or tr(lang, "pp_none")
    d = logic.discounts()
    disc = " · ".join("%s %s" % (_dur_label(lang, m), logic.fmt_pct(d[m], lang)) for m in logic.DURATIONS)
    rows = []
    for i, p in enumerate(plans):
        r = [btn(f"✏️ {p['title']}"[:30], f"a:ppe:{p['id']}")]
        if i > 0: r.append(btn("⬆️", f"a:ppu:{p['id']}"))
        if i < len(plans) - 1: r.append(btn("⬇️", f"a:ppd:{p['id']}"))
        r.append(btn("🗑", f"a:ppx:{p['id']}")); rows.append(r)
    rows.append([btn(tr(lang, "pp_add"), "a:ppa"), btn(tr(lang, "pp_clear"), "a:ppc")])
    rows.append([btn(tr(lang, "pp_b_disc"), "a:ppdc"), btn(tr(lang, "pp_b_unit"), "a:ppcu")])
    rows.append(back_row(lang))
    show(chat_id, mid, tr(lang, "pp_mgr", lines=lines, disc=disc, unit=ui.unit(lang)), kb(rows))

def disc_panel(chat_id, mid, lang):
    d = logic.discounts()
    rows = [[btn("%s · %s" % (_dur_label(lang, m), logic.fmt_pct(d[m], lang)), f"a:ppdm:{m}")] for m in logic.DURATIONS]
    rows.append([btn(tr(lang, "pp_disc_reset"), "a:ppdr")]); rows.append(back_row(lang, "a:pp"))
    show(chat_id, mid, tr(lang, "pp_disc_menu"), kb(rows))

def plan_edit(chat_id, mid, lang, pid):
    p = logic.get_pplan(pid)
    if not p:
        send(chat_id, tr(lang, "pp_gone")); return
    card = plan_line(lang, "•", p)
    if p.get("features"): card += "\n🌟 " + esc(p["features"])
    pop = tr(lang, "on" if p.get("popular") else "off")
    show(chat_id, mid, tr(lang, "pp_edit_menu", id=p["id"], card=card),
         kb([[btn(tr(lang, "pp_e_title"), f"a:ppet:{pid}"), btn(tr(lang, "pp_e_daily"), f"a:ppen:{pid}")],
             [btn(tr(lang, "pp_e_price"), f"a:ppep:{pid}"), btn(tr(lang, "pp_e_unl"), f"a:ppeu:{pid}")],
             [btn(tr(lang, "pp_e_feat"), f"a:ppef:{pid}"), btn(tr(lang, "pp_e_pop", s=pop), f"a:ppt:{pid}")],
             back_row(lang, "a:pp")]))

# ---------------- ads ----------------
def ad_preview(ad, n=60):
    return esc((ad["fa"] or ad["en"]).replace("\n", " ")[:n])

def ads_panel(chat_id, mid, lang):
    s = store.settings(); ads = logic.ads()
    lines = "\n".join(tr(lang, "ads_line", st="🟢" if a["enabled"] else "⚪️", id=a["id"], views=a["views"], clicks=a["clicks"],
                         preview=ad_preview(a)) for a in ads) or tr(lang, "ads_none")
    rows = [[btn(("🟢" if a["enabled"] else "⚪️") + f" #{a['id']} " + (a["fa"] or a["en"])[:20], f"a:ade:{a['id']}")] for a in ads]
    rows.append([btn(tr(lang, "ads_b_add"), "a:ada2"), btn(tr(lang, "ads_b_every"), "a:sn:ad_every")])
    rows.append([btn(tr(lang, "ads_b_toggle", s=tr(lang, "on" if s["ads_enabled"] else "off")), "a:adt")])
    if ads: rows.append([btn(tr(lang, "ads_b_bc"), "a:adb")])
    rows.append([btn(tr(lang, "a_b_forced") + f" ({len(logic.channels())})", "a:ch")])
    rows.append(back_row(lang))
    show(chat_id, mid, tr(lang, "ads_mgr", s=tr(lang, "on" if s["ads_enabled"] else "off"), every=s["ad_every"], lines=lines), kb(rows))

def ad_edit(chat_id, mid, lang, aid):
    a = logic.get_ad(aid)
    if not a:
        send(chat_id, tr(lang, "ads_gone")); return
    show(chat_id, mid, tr(lang, "ads_edit", id=a["id"], st="🟢" if a["enabled"] else "⚪️", views=a["views"], clicks=a["clicks"],
                          photo="✅" if a.get("photo") else "—", url=esc(a.get("url") or "—"), btn=esc(a.get("btn") or "—"),
                          track=tr(lang, "on" if a.get("track") else "off"), fa=esc(a["fa"]), en=esc(a["en"])),
         kb([[btn(tr(lang, "ads_b_fa"), f"a:adf:{aid}:fa"), btn(tr(lang, "ads_b_en"), f"a:adf:{aid}:en")],
             [btn(tr(lang, "ads_b_photo"), f"a:adf:{aid}:photo"), btn(tr(lang, "ads_b_url"), f"a:adf:{aid}:url")],
             [btn(tr(lang, "ads_b_btn"), f"a:adf:{aid}:btn"), btn(tr(lang, "ads_b_track", s=tr(lang, "on" if a.get("track") else "off")), f"a:adk:{aid}")],
             [btn(tr(lang, "ads_b_enabled", s=tr(lang, "on" if a["enabled"] else "off")), f"a:adn:{aid}")],
             [btn(tr(lang, "ads_b_preview"), f"a:adp:{aid}"), btn(tr(lang, "ads_b_del"), f"a:adx2:{aid}")],
             back_row(lang, "a:ads")]))

def ad_broadcast(admin_chat, lang, aid):
    ad = logic.get_ad(aid)
    ids = logic.ad_recipients()
    def job():
        ok = fail = 0
        for i in ids:
            r = ui.send_ad(i, ad, C.user_lang(i))
            if r: ok += 1
            else: fail += 1
            time.sleep(0.06)
        send(admin_chat, tr(lang, "a_bc_done", ok=ok, fail=fail))
    threading.Thread(target=job, daemon=True).start()

# ---------------- channels ----------------
def channels_panel(chat_id, mid, lang):
    chans = logic.channels()
    lines = "\n".join(f"{i+1}. {esc(c['title'])} · <code>{c['chat_id']}</code>" for i, c in enumerate(chans)) or tr(lang, "ch_none")
    if len(chans) > 5: lines += "\n\n" + tr(lang, "ch_many")
    rows = [[btn("🗑 " + c["title"][:40], f"a:chx:{c['id']}")] for c in chans]
    rows.append([btn(tr(lang, "ch_b_add"), "a:cha")]); rows.append(back_row(lang))
    show(chat_id, mid, tr(lang, "ch_mgr", lines=lines), kb(rows))

def verify_channel(ref):
    """ref: '@name' / 't.me/name' / numeric id. Returns (chat dict, None) or (None, error_key)."""
    ref = ref.strip()
    if re.fullmatch(r"-?\d{5,20}", ref):
        chat_ref = int(ref)
    else:
        u = logic.clean_username(ref)
        if not u:
            return None, "ch_err_notfound"
        chat_ref = "@" + u
    try:
        chat = C.call("getChat", {"chat_id": chat_ref}, timeout=20)
    except C.ApiError:
        return None, "ch_err_notfound"
    if chat.get("type") not in ("channel", "supergroup", "group"):
        return None, "ch_err_notfound"
    try:
        me = C.call("getChatMember", {"chat_id": chat["id"], "user_id": C.BOT_ID}, timeout=20)
    except C.ApiError:
        return None, "ch_err_notadmin"
    if me.get("status") not in ("administrator", "creator"):
        return None, "ch_err_notadmin"
    return chat, None

# ---------------- cookies / update ----------------
def cookies_panel(chat_id, mid, lang):
    meta = logic.cookie_meta(); lines = []
    for p in ("instagram", "youtube"):
        m = meta.get(p)
        lines.append(tr(lang, "ck_line_set", p=p, n=m["n"], date=logic.fmt_date(m["ts"])) if m and engine.cookie_path(p)
                     else tr(lang, "ck_line_none", p=p))
    rows = []
    for p in ("instagram", "youtube"):
        rows.append([btn(tr(lang, "ck_b_up", p=p), f"a:cku:{p}")] + ([btn(tr(lang, "ck_b_del", p=p), f"a:ckd:{p}")] if engine.cookie_path(p) else []))
    rows.append(back_row(lang))
    show(chat_id, mid, tr(lang, "ck_menu", lines="\n".join(lines)), kb(rows))

def handle_cookie_doc(msg, uid, lang, data):
    """Admin uploaded a document while awaiting cookies. Content is never logged."""
    chat_id = msg["chat"]["id"]; platform = data.get("platform")
    doc = msg.get("document")
    if not doc or platform not in engine.COOKIE_PLATFORMS:
        send(chat_id, tr(lang, "ck_need_doc")); return True
    try:
        if doc.get("file_size", 0) > 2 * 1024 * 1024:
            raise C.ApiError("too big")
        fi = C.call("getFile", {"file_id": doc["file_id"]})
        r = C.sess.get(C.FILE_API + fi["file_path"], timeout=60)
        content = r.content if r.status_code == 200 else b""
    except Exception:
        content = b""
    n = engine.validate_cookies(content, platform)
    C.delete_msg(chat_id, msg.get("message_id"))            # delete the upload either way
    if not n:
        send(chat_id, tr(lang, "ck_bad", p=platform)); return True
    engine.save_cookies(platform, content)
    logic.set_cookie_meta(platform, {"n": n, "ts": logic.now(), "by": uid})
    set_await(uid, None)
    send(chat_id, tr(lang, "ck_saved", p=platform, n=n))
    cookies_panel(chat_id, None, lang)
    return True

def do_update(chat_id, lang):
    send(chat_id, tr(lang, "up_running"))
    def job():
        ok, tail = engine.update_engine()
        if ok:
            y, g = engine.versions()
            send(chat_id, tr(lang, "up_done", y=esc(y), g=esc(g)))
        else:
            send(chat_id, tr(lang, "up_fail", d=esc(tail[-300:])))
    threading.Thread(target=job, daemon=True).start()

# ---------------- broadcast ----------------
def broadcast_job(admin_chat, lang, text):
    ids = logic.all_user_ids()
    def job():
        ok = fail = 0
        for i in ids:
            r = send(i, esc(text))
            if r: ok += 1
            else: fail += 1
            time.sleep(0.06)
        send(admin_chat, tr(lang, "a_bc_done", ok=ok, fail=fail))
    threading.Thread(target=job, daemon=True).start()

# ---------------- callbacks ----------------
def callback(data, chat_id, mid, uid, lang):
    p = data.split(":")
    op = p[1] if len(p) > 1 else ""
    arg = p[2] if len(p) > 2 else ""
    arg2 = p[3] if len(p) > 3 else ""
    prev = store.get_user(uid).get("await_data") or {}
    if not logic.is_owner(uid) and op not in EXTRA_OPS:
        send(chat_id, tr(lang, "a_denied")); return
    set_await(uid, None)
    try:
        _callback(op, arg, arg2, prev, chat_id, mid, uid, lang)
    except (ValueError, IndexError):
        log.warning("bad admin callback: %s", data[:30])

def _callback(op, arg, arg2, prev, chat_id, mid, uid, lang):
    if op == "noop": return
    if op == "home": home(chat_id, mid, lang, uid)
    elif op == "stats": stats(chat_id, mid, lang)
    elif op == "ul": users_list(chat_id, mid, lang, parse_int(arg, 1, 10**6) or 1)
    elif op == "u": user_card(chat_id, mid, lang, int(arg))
    elif op == "gc": grant_choice(chat_id, mid, lang, int(arg))
    elif op in ("grant", "revoke", "credits", "ban"):
        set_await(uid, "a_target", {"op": op}); send(chat_id, tr(lang, "a_ask_target"))
    elif op == "ugf":
        t = int(arg); logic.grant_unlimited(t)
        show(chat_id, mid, tr(lang, "a_done_grant", who=esc(who_label(t))), kb([back_row(lang)])); C.tell_user(t, "u_granted")
    elif op == "ugd":
        set_await(uid, "a_num", {"op": "days", "target": int(arg)}); send(chat_id, tr(lang, "a_ask_num", what=tr(lang, "a_what_days")))
    elif op == "uc":
        set_await(uid, "a_num", {"op": "credits", "target": int(arg)}); send(chat_id, tr(lang, "a_ask_num", what=tr(lang, "a_what_credits")))
    elif op == "ugm":
        t = int(arg); m = int(arg2)
        if m not in logic.DURATIONS: return
        until = logic.add_days(t, m * logic.MONTH_DAYS); d = logic.fmt_date(until)
        show(chat_id, mid, tr(lang, "a_done_days", n=m * logic.MONTH_DAYS, who=esc(who_label(t)), date=d), kb([back_row(lang)]))
        C.tell_user(t, "u_days", n=m * logic.MONTH_DAYS, date=d)
        apply_code(chat_id, lang, t, None, m * logic.MONTH_DAYS, False)
    elif op == "ugp": grant_duration(chat_id, mid, lang, int(arg), int(arg2))
    elif op == "ugpd":
        pid, days = arg2.split("_"); finish_plan_grant(chat_id, mid, lang, int(arg), int(pid), int(days))
    elif op == "ugpc":
        set_await(uid, "a_num", {"op": "plandays", "target": int(arg), "pid": int(arg2)}); send(chat_id, tr(lang, "a_ask_num", what=tr(lang, "a_what_days")))
    elif op == "ur":
        show(chat_id, mid, tr(lang, "a_confirm_revoke", who=esc(who_label(int(arg)))),
             kb([[btn(tr(lang, "yes"), f"a:urc:{arg}"), btn(tr(lang, "no"), f"a:u:{arg}")]]))
    elif op == "urc":
        t = int(arg); logic.revoke(t)
        show(chat_id, mid, tr(lang, "a_done_revoke", who=esc(who_label(t))), kb([back_row(lang)])); C.tell_user(t, "u_revoked")
    elif op in ("bn", "ubn"):
        t = int(arg)
        if logic.is_owner(t) or (logic.is_admin(t) and not logic.is_owner(uid)):
            return
        logic.set_banned(t, op == "bn")
        show(chat_id, mid, tr(lang, "a_done_ban" if op == "bn" else "a_done_unban", who=esc(who_label(t))), kb([back_row(lang)]))
    elif op == "set": settings(chat_id, mid, lang)
    elif op == "th": theme_panel(chat_id, mid, lang)
    elif op == "thd": theme.set_default(arg); theme_panel(chat_id, mid, lang)
    elif op == "ths": theme.toggle_styles(); theme_panel(chat_id, mid, lang)
    elif op == "lim": limits_panel(chat_id, mid, lang)
    elif op == "lp": limit_plat(chat_id, mid, lang, arg)
    elif op == "lps":
        if arg in logic.PLATFORMS:
            set_await(uid, "a_platlim", {"plat": arg}); send(chat_id, tr(lang, "a_limit_ask", plat=tr(lang, "plat_" + arg)))
    elif op in ("lpu", "lpz", "lpr"):
        if arg in logic.PLATFORMS:
            logic.set_plat_limit(arg, {"lpu": -1, "lpz": 0, "lpr": None}[op]); limits_panel(chat_id, mid, lang)
    elif op == "sn":
        if arg not in logic.NUM_SETTINGS: return
        set_await(uid, "a_setnum", {"key": arg}); send(chat_id, tr(lang, "a_s_ask_num"))
    elif op == "spa": set_await(uid, "a_sp_amounts", {}); send(chat_id, tr(lang, "sp_ask_amounts"))
    elif op == "ref": ref_panel(chat_id, mid, lang)
    elif op == "rt": logic.toggle_setting("ref_enabled"); ref_panel(chat_id, mid, lang)
    elif op == "err": errors_panel(chat_id, mid, lang)
    elif op == "ad": admins_panel(chat_id, mid, lang)
    elif op == "ada": set_await(uid, "a_ad_add"); send(chat_id, tr(lang, "ad_ask_add"))
    elif op == "adx":
        t = int(arg)
        if logic.remove_admin(t):
            send(chat_id, tr(lang, "ad_removed", who=esc(who_label(t)))); C.tell_user(t, "ad_notify_removed")
        admins_panel(chat_id, mid, lang)
    elif op == "own": set_await(uid, "a_owner"); send(chat_id, tr(lang, "ad_owner_ask"))
    elif op == "ownc":
        t = int(arg)
        if not logic.is_owner(uid) or t == uid: return
        logic.set_owner(t)
        send(chat_id, tr(lang, "ad_owner_done", who=esc(who_label(t))), ui.main_menu(lang, False)); C.tell_user(t, "ad_owner_notify_new")
    elif op == "su": support_panel(chat_id, mid, lang)
    elif op == "sup":
        if arg in ("primary", "backup"):
            set_await(uid, "a_su", {"slot": arg}); send(chat_id, tr(lang, "su_ask"))
    elif op == "suc": logic.set_support("backup", ""); (C.profile_hook and C.profile_hook()); send(chat_id, tr(lang, "su_cleared")); support_panel(chat_id, None, lang)
    # plans
    elif op == "pp": plans_panel(chat_id, mid, lang)
    elif op == "ppa":
        if len(logic.pplans()) >= logic.MAX_PPLANS: send(chat_id, tr(lang, "pp_full", n=logic.MAX_PPLANS)); return
        set_await(uid, "a_pp_title", {}); send(chat_id, tr(lang, "pp_ask_title"))
    elif op == "ppx": logic.remove_pplan(int(arg)); send(chat_id, tr(lang, "pp_deleted")); plans_panel(chat_id, mid, lang)
    elif op in ("ppu", "ppd"): logic.move_pplan(int(arg), -1 if op == "ppu" else 1); plans_panel(chat_id, mid, lang)
    elif op == "ppc":
        show(chat_id, mid, tr(lang, "pp_clear_confirm"), kb([[btn(tr(lang, "yes"), "a:ppcy"), btn(tr(lang, "no"), "a:pp")]]))
    elif op == "ppcy": logic.clear_pplans(); send(chat_id, tr(lang, "pp_cleared")); plans_panel(chat_id, mid, lang)
    elif op == "ppe": plan_edit(chat_id, mid, lang, int(arg))
    elif op == "ppt": logic.toggle_popular(int(arg)); plan_edit(chat_id, mid, lang, int(arg))
    elif op == "ppeu":
        logic.update_pplan(int(arg), daily=-1); send(chat_id, tr(lang, "pp_saved")); plan_edit(chat_id, None, lang, int(arg))
    elif op in ("ppet", "ppen", "ppep", "ppef"):
        key = {"ppet": "title", "ppen": "daily", "ppep": "price", "ppef": "features"}[op]
        set_await(uid, "a_pp_edit", {"id": int(arg), "field": key})
        send(chat_id, tr(lang, {"title": "pp_ask_title", "daily": "pp_ask_daily", "price": "pp_ask_price", "features": "pp_ask_feat"}[key]))
    elif op == "ppwu":     # wizard: unlimited downloads per day
        d = dict(prev)
        if "title" not in d: return
        d["daily"] = -1; set_await(uid, "a_pp_price", d); send(chat_id, tr(lang, "pp_ask_price"), kb([[btn(tr(lang, "pp_skip"), "a:pps:price")]]))
    elif op == "pps":
        d = dict(prev)
        if arg == "price": d["price"] = 0; set_await(uid, "a_pp_feat", d); send(chat_id, tr(lang, "pp_ask_feat"), kb([[btn(tr(lang, "pp_skip"), "a:pps:feat")]]))
        elif arg == "feat": d["features"] = ""; set_await(uid, "a_pp_pop", d); send(chat_id, tr(lang, "pp_ask_pop"), kb([[btn(tr(lang, "yes"), "a:ppp:1"), btn(tr(lang, "no"), "a:ppp:0")]]))
    elif op == "ppdc": disc_panel(chat_id, mid, lang)
    elif op == "ppdm":
        if int(arg) in logic.DURATIONS:
            set_await(uid, "a_disc", {"months": int(arg)}); send(chat_id, tr(lang, "pp_ask_disc", d=_dur_label(lang, int(arg))))
    elif op == "ppdr":
        for m, v in store.DEFAULT_SETTINGS["duration_discounts"].items(): logic.set_discount(int(m), v)
        send(chat_id, tr(lang, "pp_saved")); disc_panel(chat_id, None, lang)
    elif op == "ppcu":
        set_await(uid, "a_unit", {}); send(chat_id, tr(lang, "pp_ask_unit"))
    elif op == "ppp":
        d = dict(prev)
        if "title" not in d or "daily" not in d: return
        pid = logic.add_pplan(d["title"], d["daily"], d.get("price", 0), d.get("features", ""), arg == "1")
        send(chat_id, tr(lang, "pp_added" if pid else "pp_full", n=logic.MAX_PPLANS)); plans_panel(chat_id, None, lang)
    # ads
    elif op == "ads": ads_panel(chat_id, mid, lang)
    elif op == "adt": logic.toggle_setting("ads_enabled"); ads_panel(chat_id, mid, lang)
    elif op == "ada2": set_await(uid, "ad_fa", {}); send(chat_id, tr(lang, "ads_ask_fa"))
    elif op == "ade": ad_edit(chat_id, mid, lang, int(arg))
    elif op == "adn": logic.toggle_ad(int(arg), "enabled"); ad_edit(chat_id, mid, lang, int(arg))
    elif op == "adk": logic.toggle_ad(int(arg), "track"); ad_edit(chat_id, mid, lang, int(arg))
    elif op == "adx2":
        logic.remove_ad(int(arg)); send(chat_id, tr(lang, "ads_deleted")); ads_panel(chat_id, mid, lang)
    elif op == "adp":
        a = logic.get_ad(int(arg))
        if a: ui.send_ad(chat_id, a, lang, count_view=False)
    elif op == "adf":
        if arg2 not in ("fa", "en", "photo", "url", "btn"): return
        set_await(uid, "ad_edit", {"id": int(arg), "field": arg2})
        send(chat_id, tr(lang, {"fa": "ads_ask_fa", "en": "ads_ask_en", "photo": "ads_ask_photo", "url": "ads_ask_url", "btn": "ads_ask_btn"}[arg2]) + ("\n(- = حذف / clear)" if arg2 in ("photo", "url", "btn") else ""))
    elif op == "ads_skip":
        d = dict(prev)
        nxt = {"en": ("ad_photo", "ads_ask_photo"), "photo": ("ad_url", "ads_ask_url"), "url": ("ad_btn", "ads_ask_btn"), "btn": None}[arg]
        if arg == "en": d["en"] = d.get("fa", "")
        if nxt:
            set_await(uid, nxt[0], d); send(chat_id, tr(lang, nxt[1]), kb([[btn(tr(lang, "pp_skip"), "a:ads_skip:" + nxt[0][3:])]]))
        else:
            finish_ad(chat_id, lang, d)
    elif op == "adb":
        ads = logic.ads()
        if not ads: send(chat_id, tr(lang, "ads_none_ready")); return
        show(chat_id, mid, tr(lang, "ads_bc_pick"), kb([[btn(f"#{a['id']} " + (a['fa'] or a['en'])[:30], f"a:adbc:{a['id']}")] for a in ads] + [back_row(lang, "a:ads")]))
    elif op == "adbc":
        show(chat_id, mid, tr(lang, "ads_bc_confirm", id=int(arg), n=len(logic.ad_recipients())),
             kb([[btn(tr(lang, "yes"), f"a:adby:{arg}"), btn(tr(lang, "no"), "a:ads")]]))
    elif op == "adby":
        if logic.get_ad(int(arg)):
            show(chat_id, mid, tr(lang, "a_bc_sending")); ad_broadcast(chat_id, lang, int(arg))
    # channels
    elif op == "ch": channels_panel(chat_id, mid, lang)
    elif op == "cha": set_await(uid, "ch_add"); send(chat_id, tr(lang, "ch_ask"))
    elif op == "chx":
        logic.remove_channel(int(arg)); gate.clear_cache(); send(chat_id, tr(lang, "ch_removed")); channels_panel(chat_id, mid, lang)
    # cookies / engine
    elif op == "ck": cookies_panel(chat_id, mid, lang)
    elif op == "cku":
        if arg in engine.COOKIE_PLATFORMS:
            set_await(uid, "ck_up", {"platform": arg}); send(chat_id, tr(lang, "ck_ask", p=arg))
    elif op == "ckd":
        if arg in engine.COOKIE_PLATFORMS:
            engine.delete_cookies(arg); logic.set_cookie_meta(arg, None); send(chat_id, tr(lang, "ck_deleted", p=arg)); cookies_panel(chat_id, mid, lang)
    elif op == "upd": do_update(chat_id, lang)
    # broadcast
    elif op == "bcm":
        show(chat_id, mid, tr(lang, "tm_bc_menu"), kb([[btn(tr(lang, "tm_bc_all"), "a:bc")], [btn(tr(lang, "tm_bc_sel"), "a:tm")], [btn(tr(lang, "a_back"), "a:home")]]))
    elif op.startswith("tm"): targeted.callback(op, arg, arg2, chat_id, mid, uid, lang)
    elif op in campaign.OPS: campaign.callback(op, arg, arg2, prev, chat_id, mid, uid, lang)
    elif op in codesui.OPS: codesui.callback(op, arg, arg2, prev, chat_id, mid, uid, lang)
    elif op in posterui.OPS: posterui.callback(op, arg, arg2, prev, chat_id, mid, uid, lang)
    elif op == "bc": set_await(uid, "a_bc", {}); send(chat_id, tr(lang, "a_bc_ask"))
    elif op == "bcy":
        with store.transaction() as st:
            pb = st.get("pending_broadcast"); st["pending_broadcast"] = None
        if not pb: send(chat_id, tr(lang, "a_bc_none")); return
        show(chat_id, mid, tr(lang, "a_bc_sending")); broadcast_job(chat_id, lang, pb["text"])
    elif op == "bcn":
        with store.transaction() as st: st["pending_broadcast"] = None
        show(chat_id, mid, tr(lang, "a_bc_cancel"), kb([back_row(lang)]))

def finish_ad(chat_id, lang, d):
    aid = logic.add_ad(d.get("fa", ""), d.get("en", ""), d.get("photo"), d.get("url", ""), d.get("btn", ""))
    send(chat_id, tr(lang, "ads_added" if aid else "pp_full", n=logic.MAX_ADS))
    ads_panel(chat_id, None, lang)

def text(msg, uid, lang, aw, data):
    """Plain text/photo/doc reply while an awaiting-state is active. True if consumed."""
    chat_id = msg["chat"]["id"]; t = (msg.get("text") or msg.get("caption") or "").strip()
    if not logic.is_owner(uid) and aw not in EXTRA_AWAIT:
        set_await(uid, None); return False
    if aw == "ck_up":
        return handle_cookie_doc(msg, uid, lang, data)
    if aw.startswith("a_tm_"):
        return targeted.text(msg, uid, lang, aw, data)
    if aw.startswith("cp_"):
        return campaign.text(msg, uid, lang, aw, data)
    if aw.startswith("p_"):
        return posterui.text(msg, uid, lang, aw, data)
    if aw.startswith("cd_"):
        return codesui.text(msg, uid, lang, aw, data)
    if aw == "a_target":
        tg_ = logic.find_user(t)
        if tg_ is None: send(chat_id, tr(lang, "a_not_found")); return True
        op = data.get("op"); set_await(uid, None)
        if op == "grant": grant_choice(chat_id, None, lang, tg_)
        elif op == "revoke":
            send(chat_id, tr(lang, "a_confirm_revoke", who=esc(who_label(tg_))), kb([[btn(tr(lang, "yes"), f"a:urc:{tg_}"), btn(tr(lang, "no"), "a:home")]]))
        elif op == "ban": user_card(chat_id, None, lang, tg_)
        else: set_await(uid, "a_num", {"op": "credits", "target": tg_}); send(chat_id, tr(lang, "a_ask_num", what=tr(lang, "a_what_credits")))
        return True
    if aw == "a_num":
        n = parse_int(t, 1, 3650 if data["op"] in ("days", "plandays") else 100000)
        if n is None: send(chat_id, tr(lang, "a_bad_num")); return True
        tg_ = data["target"]; set_await(uid, None)
        if data["op"] == "plandays":
            finish_plan_grant(chat_id, None, lang, tg_, data["pid"], n); return True
        if data["op"] == "days":
            until = logic.add_days(tg_, n); d = logic.fmt_date(until)
            send(chat_id, tr(lang, "a_done_days", n=n, who=esc(who_label(tg_)), date=d), kb([back_row(lang)])); C.tell_user(tg_, "u_days", n=n, date=d)
        else:
            logic.add_credits(tg_, n)
            send(chat_id, tr(lang, "a_done_credits", n=n, who=esc(who_label(tg_))), kb([back_row(lang)])); C.tell_user(tg_, "u_credits", n=n)
        return True
    if aw == "a_platlim":
        n = parse_int(t, 0, 100000)
        if n is None: send(chat_id, tr(lang, "a_bad_num")); return True
        logic.set_plat_limit(data["plat"], n); set_await(uid, None)
        send(chat_id, tr(lang, "a_s_saved")); limits_panel(chat_id, None, lang); return True
    if aw == "a_sp_amounts":
        a = stars.parse_amounts(t)
        if not a: send(chat_id, tr(lang, "sp_ask_amounts")); return True
        stars.set_amounts(a); set_await(uid, None); send(chat_id, tr(lang, "a_s_saved")); stats(chat_id, None, lang); return True
    if aw == "a_setnum":
        n = parse_int(t, 1 if data["key"] in ("ad_every", "max_tracks") else 0, 50 if data["key"] == "max_tracks" else 100000)
        if n is None: send(chat_id, tr(lang, "a_bad_num")); return True
        logic.set_setting(data["key"], n); set_await(uid, None)
        send(chat_id, tr(lang, "a_s_saved"))
        {"ad_every": ads_panel, "ref_bonus": ref_panel, "ref_invitee_bonus": ref_panel, "ref_max_total": ref_panel}.get(data["key"], settings)(chat_id, None, lang); return True
    if aw == "a_ad_add":
        tg_ = logic.find_user(t)
        if tg_ is None: send(chat_id, tr(lang, "a_not_found")); return True
        set_await(uid, None)
        if not logic.add_admin(tg_): send(chat_id, tr(lang, "ad_is_owner")); return True
        send(chat_id, tr(lang, "ad_added", who=esc(who_label(tg_)))); C.tell_user(tg_, "ad_notify_new"); admins_panel(chat_id, None, lang); return True
    if aw == "a_owner":
        tg_ = logic.find_user(t)
        if tg_ is None: send(chat_id, tr(lang, "a_not_found")); return True
        set_await(uid, None)
        if logic.is_owner(tg_): send(chat_id, tr(lang, "ad_owner_same")); return True
        send(chat_id, tr(lang, "ad_owner_confirm", who=esc(who_label(tg_))), kb([[btn(tr(lang, "yes"), f"a:ownc:{tg_}"), btn(tr(lang, "no"), "a:ad")]])); return True
    if aw == "a_su":
        name = logic.clean_username(t)
        if not name: send(chat_id, tr(lang, "su_bad")); return True
        logic.set_support(data["slot"], name); set_await(uid, None); (C.profile_hook and C.profile_hook()); send(chat_id, tr(lang, "su_saved")); support_panel(chat_id, None, lang); return True
    if aw == "a_pp_title":
        if not t: return True
        data["title"] = t[:60]; set_await(uid, "a_pp_count", data); send(chat_id, tr(lang, "pp_ask_daily"), kb([[btn(tr(lang, "pp_e_unl"), "a:ppwu")]])); return True
    if aw == "a_pp_count":
        n = parse_int(t, 1, 100000)
        if n is None: send(chat_id, tr(lang, "a_bad_num")); return True
        data["daily"] = n; set_await(uid, "a_pp_price", data); send(chat_id, tr(lang, "pp_ask_price"), kb([[btn(tr(lang, "pp_skip"), "a:pps:price")]])); return True
    if aw == "a_pp_price":
        n = parse_int(t.replace(",", "").replace("٬", "").replace(" ", ""), 1, 10**9)
        if n is None: send(chat_id, tr(lang, "a_bad_num")); return True
        data["price"] = n; set_await(uid, "a_pp_feat", data); send(chat_id, tr(lang, "pp_ask_feat"), kb([[btn(tr(lang, "pp_skip"), "a:pps:feat")]])); return True
    if aw == "a_pp_feat":
        if not t: return True
        data["features"] = t[:200]; set_await(uid, "a_pp_pop", data)
        send(chat_id, tr(lang, "pp_ask_pop"), kb([[btn(tr(lang, "yes"), "a:ppp:1"), btn(tr(lang, "no"), "a:ppp:0")]])); return True
    if aw == "a_pp_pop":
        send(chat_id, tr(lang, "pp_ask_pop"), kb([[btn(tr(lang, "yes"), "a:ppp:1"), btn(tr(lang, "no"), "a:ppp:0")]])); return True
    if aw == "a_pp_edit":
        if not t: return True
        f = data["field"]
        if f == "daily":
            val = parse_int(t, 1, 100000)
            if val is None: send(chat_id, tr(lang, "a_bad_num")); return True
        elif f == "price":
            val = parse_int(t.replace(",", "").replace("٬", "").replace(" ", "") if t != "-" else "0", 0, 10**9)
            if val is None: send(chat_id, tr(lang, "a_bad_num")); return True
        elif f == "features": val = "" if t == "-" else t
        else: val = t
        ok_ = logic.update_pplan(data["id"], **{f: val}); set_await(uid, None)
        send(chat_id, tr(lang, "pp_saved" if ok_ else "pp_gone"))
        plan_edit(chat_id, None, lang, data["id"]) if ok_ else plans_panel(chat_id, None, lang); return True
    if aw == "a_disc":
        n = parse_int(t, 0, logic.MAX_DISCOUNT)
        if n is None: send(chat_id, tr(lang, "a_bad_num")); return True
        logic.set_discount(data["months"], n); set_await(uid, None); send(chat_id, tr(lang, "pp_saved")); disc_panel(chat_id, None, lang); return True
    if aw == "a_unit":
        if not t: return True
        logic.set_price_unit("" if t == "-" else t); set_await(uid, None); send(chat_id, tr(lang, "pp_saved")); plans_panel(chat_id, None, lang); return True
    # --- ads wizard
    if aw == "ad_fa":
        if msg.get("photo"): data["photo"] = msg["photo"][-1]["file_id"]
        if not t: send(chat_id, tr(lang, "ads_ask_fa")); return True
        data["fa"] = t[:900]; set_await(uid, "ad_en", data)
        send(chat_id, tr(lang, "ads_ask_en"), kb([[btn(tr(lang, "pp_skip"), "a:ads_skip:en")]])); return True
    if aw == "ad_en":
        if not t: return True
        data["en"] = t[:900]; nxt = "ad_url" if data.get("photo") else "ad_photo"
        set_await(uid, nxt, data)
        if nxt == "ad_photo": send(chat_id, tr(lang, "ads_ask_photo"), kb([[btn(tr(lang, "pp_skip"), "a:ads_skip:photo")]]))
        else: send(chat_id, tr(lang, "ads_ask_url"), kb([[btn(tr(lang, "pp_skip"), "a:ads_skip:url")]]))
        return True
    if aw == "ad_photo":
        if not msg.get("photo"): send(chat_id, tr(lang, "ads_ask_photo"), kb([[btn(tr(lang, "pp_skip"), "a:ads_skip:photo")]])); return True
        data["photo"] = msg["photo"][-1]["file_id"]; set_await(uid, "ad_url", data)
        send(chat_id, tr(lang, "ads_ask_url"), kb([[btn(tr(lang, "pp_skip"), "a:ads_skip:url")]])); return True
    if aw == "ad_url":
        if not re.match(r"^https?://\S+$", t): send(chat_id, tr(lang, "ads_bad_url")); return True
        data["url"] = t[:300]; set_await(uid, "ad_btn", data)
        send(chat_id, tr(lang, "ads_ask_btn"), kb([[btn(tr(lang, "pp_skip"), "a:ads_skip:btn")]])); return True
    if aw == "ad_btn":
        if not t: return True
        data["btn"] = t[:40]; set_await(uid, None); finish_ad(chat_id, lang, data); return True
    if aw == "ad_edit":
        f = data["field"]; aid = data["id"]
        if f == "photo":
            if msg.get("photo"): val = msg["photo"][-1]["file_id"]
            elif t == "-": val = None
            else: send(chat_id, tr(lang, "ads_ask_photo")); return True
        elif f == "url":
            if t == "-": val = ""
            elif re.match(r"^https?://\S+$", t): val = t
            else: send(chat_id, tr(lang, "ads_bad_url")); return True
        elif f == "btn": val = "" if t == "-" else t
        else:
            if not t: return True
            val = t
        logic.update_ad(aid, **{f: val}); set_await(uid, None)
        send(chat_id, tr(lang, "ads_saved")); ad_edit(chat_id, None, lang, aid); return True
    # --- channels
    if aw == "ch_add":
        chat, err = verify_channel(t)
        if err: send(chat_id, tr(lang, err)); return True
        if any(int(c["chat_id"]) == int(chat["id"]) for c in logic.channels()):
            send(chat_id, tr(lang, "ch_err_dup")); set_await(uid, None); return True
        uname = chat.get("username") or ""
        data = {"chat_id": chat["id"], "title": chat.get("title") or uname or str(chat["id"]), "username": uname}
        if uname:
            logic.add_channel(data["chat_id"], data["title"], C.link(uname), uname)
            set_await(uid, None); gate.clear_cache()
            send(chat_id, tr(lang, "ch_added", title=esc(data["title"]))); channels_panel(chat_id, None, lang)
        else:
            set_await(uid, "ch_url", data); send(chat_id, tr(lang, "ch_ask_url"))
        return True
    if aw == "ch_url":
        if not re.match(r"^https://(t\.me|ble\.ir)/\S+$", t): send(chat_id, tr(lang, "ch_ask_url")); return True
        logic.add_channel(data["chat_id"], data["title"], t, data.get("username", "")); set_await(uid, None); gate.clear_cache()
        send(chat_id, tr(lang, "ch_added", title=esc(data["title"]))); channels_panel(chat_id, None, lang); return True
    # --- broadcast
    if aw == "a_bc":
        if not t: return True
        with store.transaction() as st: st["pending_broadcast"] = {"text": t[:3500], "by": uid}
        set_await(uid, None)
        send(chat_id, tr(lang, "a_bc_confirm", text=esc(t[:3500]), n=len(logic.all_user_ids())), kb([[btn(tr(lang, "yes"), "a:bcy"), btn(tr(lang, "no"), "a:bcn")]]))
        return True
    return False

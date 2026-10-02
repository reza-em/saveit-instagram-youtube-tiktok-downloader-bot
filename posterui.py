"""Owner-only admin UI for the channel poster (callbacks 'a:pc*', 'a:pj*', 'a:pf*'; awaiting states 'p_*')."""
import re, threading, logging
import core as C, store, logic, poster
from core import tr, esc, btn, kb, show, send, set_await, parse_int

log = logging.getLogger("posterui")
OPS = {"pc", "pca", "pcl", "pcv", "pcx", "pcxy", "pcg", "pch", "pct", "pcL", "pj", "pjn", "pjt", "pjv", "pjs", "pjp", "pjr", "pjc", "pji", "pjd", "pjx", "pji2", "pjm", "pjl", "pf", "pfn", "pfv", "pfs", "pfa", "pfe", "pfx", "pfr", "pfg", "pfi", "pfd", "pfn2", "pfh", "pfT", "pfu", "pft", "pz", "pzf", "pzn", "pzy", "pzd", "pzdy", "pv", "pvt", "pvty", "pva", "pvc", "pvx", "pvr"}
PER_PAGE = 10
SRC_LABEL = {"rj_trending": "Radio Javan 🔥", "rj_popular": "Radio Javan ⭐", "rj_rap": "Radio Javan 🎤 Rap", "diss": "🔥 Diss / دیس", "audius": "Audius", "itunes": "Apple/iTunes", "soundcloud": "SoundCloud", "shazam": "Shazam", "own": "SaveIt"}
OWNER_ONLY_NOTE = True

def _back(lang, to="a:pc"):
    return [btn(tr(lang, "a_back"), to)]

def _owner():
    return store.admin_id()

def notify(kind, **kw):
    """Poster events -> owner (localized)."""
    o = _owner()
    if not o: return
    lang = C.user_lang(o)
    if kind == "job_done":
        j, c = kw["job"], kw["counts"]
        send(o, tr(lang, "pst_done", name=esc(j["name"]), ok=c.get("posted", 0), fail=c.get("failed", 0), skip=c.get("skipped", 0)),
             kb([[btn(tr(lang, "pst_b_view"), f"a:pjv:{j['id']}")]]))
    elif kind == "job_paused":
        j = kw["job"]
        send(o, tr(lang, "pst_paused", name=esc(j["name"]), err=esc(kw.get("err") or "")[:150]), kb([[btn(tr(lang, "pst_b_view"), f"a:pjv:{j['id']}")]]))
    elif kind == "feed_draft":
        send(o, tr(lang, "pst_feed_draft", name=esc(kw["feed"]["name"]), n=kw["n"]), kb([[btn(tr(lang, "pst_b_review"), f"a:pjv:{kw['job']}")]]))
    elif kind == "feed_ready":
        send(o, tr(lang, "pst_feed_auto", name=esc(kw["feed"]["name"]), n=kw["n"]), kb([[btn(tr(lang, "pst_b_view"), f"a:pjv:{kw['job']}")]]))

# ------------------------------------------------------------------ main
def panel(chat_id, mid, lang):
    chans = poster.channels(); js = poster.jobs(); fs = poster.feeds()
    running = sum(1 for j in js if j["status"] == "running")
    rows = [[btn(("✅ " if c.get("ok") else "⚠️ ") + f"{c['title'][:22]} · {c.get('label') or '—'}"[:40], f"a:pcl:{c['id']}")] for c in chans]
    rows.append([btn(tr(lang, "pc_add"), "a:pca")])
    rows.append([btn(tr(lang, "pst_b_jobs", n=len(js)), "a:pj"), btn(tr(lang, "pst_b_feeds", n=len(fs)), "a:pf")])
    rows.append([btn(tr(lang, "pst_b_footer"), "a:pz"), btn(tr(lang, "pst_b_charts"), "a:pzf")])
    rows.append([btn(tr(lang, "pst_b_diss"), "a:pzd"), btn(tr(lang, "pv_b_home", s=tr(lang, "on" if poster.viral_on() else "off")), "a:pv")])
    rows.append(_back(lang, "a:home"))
    show(chat_id, mid, tr(lang, "pst_home", nch=len(chans), running=running, total=len(js), feeds=len(fs)), kb(rows))

def viral_panel(chat_id, mid, lang):
    cfg = poster.viral_cfg(); lst = cfg["list"]
    lines = "\n".join(f"{i + 1}. {esc((x['artist'] + ' — ') if x['artist'] else '')}{esc(x['title'])}" for i, x in enumerate(lst[:25])) or tr(lang, "pv_list_empty")
    rows = [[btn(tr(lang, "pv_b_toggle", s=tr(lang, "on" if cfg["on"] else "off")), "a:pvt")],
            [btn(tr(lang, "pv_b_add"), "a:pva"), btn(tr(lang, "pv_b_clear"), "a:pvc")],
            [btn(tr(lang, "pst_b_edit") + " " + tr(lang, "pv_tags_short"), "a:pvty:e"), btn(tr(lang, "pst_b_default"), "a:pvty:d")],
            [btn(tr(lang, "pv_b_rerank"), "a:pvr")]]
    rows += [[btn("🗑 %d. %s" % (i + 1, (x["title"])[:28]), f"a:pvx:{i}")] for i, x in enumerate(lst[:10])]
    rows.append(_back(lang))
    show(chat_id, mid, tr(lang, "pv_home", s=tr(lang, "on" if cfg["on"] else "off"), tags=esc(" ".join(poster.viral_tags())), n=len(lst), lst=lines, vmin=poster.VIRAL_MIN, mq=poster.MIN_QUEUE), kb(rows))

def chan_card(chat_id, mid, lang, cid, recheck=False):
    c = poster.get_channel(cid)
    if not c:
        send(chat_id, tr(lang, "pc_gone")); return
    st = poster.check_admin(c["chat_id"]) if recheck else ("ok" if c.get("ok") else "notadmin")
    if recheck: poster.update_channel(cid, ok=(st == "ok"))
    txt = tr(lang, "pc_card", title=esc(c["title"]), handle=esc(poster.link_of(c) or "—"), label=esc(c.get("label") or "—"),
             tags=esc(" ".join(c.get("tags") or []) or "—"), fl=c.get("lang", "fa"), status=tr(lang, "pc_st_" + st), n=poster.posted_count(cid), id=c["chat_id"])
    rows = [[btn(tr(lang, "pc_b_label"), f"a:pcg:{cid}"), btn(tr(lang, "pc_b_handle"), f"a:pch:{cid}")],
            [btn(tr(lang, "pc_b_tags"), f"a:pct:{cid}"), btn(tr(lang, "pc_b_lang", l=c.get("lang", "fa")), f"a:pcL:{cid}")],
            [btn(tr(lang, "pc_b_verify"), f"a:pcv:{cid}"), btn(tr(lang, "pst_b_newjob"), f"a:pjn:{cid}")],
            [btn(tr(lang, "pst_b_newfeed"), f"a:pfn:{cid}"), btn("🗑", f"a:pcx:{cid}")], _back(lang)]
    show(chat_id, mid, txt, kb(rows))

# ------------------------------------------------------------------ jobs
def jobs_panel(chat_id, mid, lang):
    js = poster.jobs()
    rows = []
    for j in sorted(js, key=lambda x: -x["id"])[:15]:
        c = poster.counts(j); ch = poster.get_channel(j["channel_id"])
        rows.append([btn(f"{tr(lang, 'pst_s_' + j['status'])} #{j['id']} {j['name'][:16]} · {c['posted']}/{len(j['items'])}"[:50], f"a:pjv:{j['id']}")])
    rows.append(_back(lang))
    show(chat_id, mid, tr(lang, "pst_jobs", n=len(js)) if js else tr(lang, "pst_jobs_none"), kb(rows))

def job_view(chat_id, mid, lang, jid, page=1):
    j = poster.get_job(jid)
    if not j:
        send(chat_id, tr(lang, "pst_gone")); return
    ch = poster.get_channel(j["channel_id"]) or {"title": "?"}
    c = poster.counts(j); items = j["items"]
    pages = max(1, (len(items) + PER_PAGE - 1) // PER_PAGE); page = min(max(1, page), pages)
    lines, rows = [], []
    for i in range((page - 1) * PER_PAGE, min(len(items), page * PER_PAGE)):
        it = items[i]
        ic = {"pending": "⏳", "posted": "✅", "failed": "❌", "skipped": "⏭"}[it["status"]] if j["status"] != "draft" else ("☑️" if it.get("sel", True) else "⬜")
        src = poster_src(it)
        lines.append(f"{ic} {i + 1}. {esc(it['artist'] + ' — ' if it['artist'] else '')}{esc(it['title'])}" + (f" · {src}" if src else ""))
        if j["status"] == "draft":
            rows.append([btn(f"{ic} {i + 1}. {(it['artist'] + ' - ' if it['artist'] else '') + it['title']}"[:56], f"a:pjt:{jid}:{i}")])
    if pages > 1:
        nav = []
        if page > 1: nav.append(btn("⬅️", f"a:pjl:{jid}:{page - 1}"))
        nav.append(btn(f"{page}/{pages}", "a:noop"))
        if page < pages: nav.append(btn("➡️", f"a:pjl:{jid}:{page + 1}"))
        rows.append(nav)
    un = ("\n\n" + tr(lang, "pst_unmatched", items=esc("\n".join(j["unmatched"][:15])))) if j["unmatched"] else ""
    if j["status"] == "draft":
        rows.append([btn(tr(lang, "pst_b_start", n=sum(1 for it in items if it.get("sel", True))), f"a:pjs:{jid}"), btn(tr(lang, "pst_b_cancel"), f"a:pjc:{jid}")])
    else:
        ctl = []
        if j["status"] == "running": ctl.append(btn(tr(lang, "pst_b_pause"), f"a:pjp:{jid}"))
        if j["status"] == "paused": ctl.append(btn(tr(lang, "pst_b_resume"), f"a:pjr:{jid}"))
        if j["status"] in ("running", "paused"): ctl.append(btn(tr(lang, "pst_b_cancel"), f"a:pjc:{jid}"))
        if ctl: rows.append(ctl)
    rows.append([btn(tr(lang, "pst_b_interval"), f"a:pji:{jid}"), btn(tr(lang, "pst_b_daily"), f"a:pjd:{jid}")])
    if j["status"] != "running": rows.append([btn("🗑", f"a:pjx:{jid}")])
    rows.append(_back(lang, "a:pj"))
    head = tr(lang, "pst_job", id=j["id"], name=esc(j["name"]), ch=esc(ch["title"]), st=tr(lang, "pst_s_" + j["status"]), iv=j["interval"],
              daily=(j["daily"] or tr(lang, "pst_nolimit")), ok=c["posted"], pend=c["pending"], fail=c["failed"], skip=c["skipped"],
              err=(("\n⚠️ " + esc(j["last_err"])) if j.get("last_err") and j["status"] in ("paused", "running") else ""))
    show(chat_id, mid, head + "\n\n" + "\n".join(lines) + un, kb(rows))

def poster_src(it):
    import music
    c = it.get("cand") or {}
    return music.SHORT.get(c.get("source", ""), "") if c else "🔎"

def start_matching(chat_id, lang, cid, name, entries, unmatched0=(), tags=(), feed_id=None):
    """Run the provider chain on entries in a thread, then show the draft for approval."""
    m = send(chat_id, tr(lang, "pst_matching", n=len(entries)))
    smid = (m or {}).get("message_id")
    def job():
        last = [0]
        def prog(i, n):
            if i - last[0] >= 5 and smid:
                last[0] = i; C.edit_text(chat_id, smid, tr(lang, "pst_matching_p", i=i, n=n))
        try:
            items, bad = poster.match_entries(entries, prog)
        except Exception as e:
            send(chat_id, tr(lang, "pst_fail", d=esc(type(e).__name__))); return
        if not items:
            C.show(chat_id, smid, tr(lang, "pst_nothing", items=esc("\n".join(list(unmatched0) + bad)[:800])), kb([_back(lang)])); return
        ch = poster.get_channel(cid)
        jid = poster.create_job(cid, name, items, 30, 0, "draft", feed_id, list(unmatched0) + bad, tags)
        C.delete_msg(chat_id, smid)
        job_view(chat_id, None, lang, jid)
    threading.Thread(target=job, daemon=True).start()

# ------------------------------------------------------------------ feeds
def feeds_panel(chat_id, mid, lang):
    fs = poster.feeds()
    rows = []
    for f in fs:
        ch = poster.get_channel(f["channel_id"]) or {"title": "?"}
        rows.append([btn(f"{'🟢' if f['enabled'] else '⚪️'} #{f['id']} {f['name'][:18]} → {ch['title'][:14]} · top {f['top_n']}"[:55], f"a:pfv:{f['id']}")])
    rows.append(_back(lang))
    show(chat_id, mid, tr(lang, "pst_feeds", n=len(fs)) if fs else tr(lang, "pst_feeds_none"), kb(rows))

def feed_view(chat_id, mid, lang, fid):
    f = poster.get_feed(fid)
    if not f:
        send(chat_id, tr(lang, "pst_gone")); return
    ch = poster.get_channel(f["channel_id"]) or {"title": "?", "tags": []}
    srcs = ", ".join(SRC_LABEL[s] for s in f["sources"])
    tags = " ".join(poster.feed_tags(f, ch))
    rows = [[btn(("✅ " if s in f["sources"] else "⬜ ") + SRC_LABEL[s], f"a:pfs:{fid}:{s}")] for s in poster.FEED_SOURCES]
    rows += [[btn(tr(lang, "pf_b_genre"), f"a:pfg:{fid}"), btn(tr(lang, "pf_b_top"), f"a:pfi:{fid}")],
             [btn(tr(lang, "pf_b_refresh"), f"a:pfh:{fid}"), btn(tr(lang, "pst_b_interval"), f"a:pfd:{fid}")],
             [btn(tr(lang, "pst_b_daily"), f"a:pfn2:{fid}"), btn(tr(lang, "pf_b_tags"), f"a:pfT:{fid}")],
             [btn(tr(lang, "pf_b_auto", s=tr(lang, "on" if f["auto"] else "off")), f"a:pfa:{fid}"), btn(tr(lang, "pf_b_enabled", s=tr(lang, "on" if f["enabled"] else "off")), f"a:pfe:{fid}")],
             [btn(tr(lang, "pf_b_now"), f"a:pfr:{fid}"), btn("🗑", f"a:pfx:{fid}")], _back(lang, "a:pf")]
    show(chat_id, mid, tr(lang, "pf_card", id=f["id"], name=esc(f["name"]), ch=esc(ch["title"]), srcs=esc(srcs), genre=esc(f["genre"] or "—"), top=f["top_n"], rh=f["refresh_h"],
                          iv=f["interval"], daily=(f["daily"] or tr(lang, "pst_nolimit")), mode=tr(lang, "pf_auto" if f["auto"] else "pf_approve"), tags=esc(tags or "—")), kb(rows))

def charts_status(chat_id, mid, lang):
    lines = "\n".join(f"{SRC_LABEL[s]}: {poster.CHART_STATUS.get(s, '—')}" for s in poster.FEED_SOURCES)
    show(chat_id, mid, tr(lang, "pst_charts", lines=esc(lines)), kb([[btn(tr(lang, "pst_b_test"), "a:pzn")], _back(lang)]))

# ------------------------------------------------------------------ callbacks
def callback(op, arg, arg2, prev, chat_id, mid, uid, lang):
    if op == "pc": panel(chat_id, mid, lang)
    elif op == "pca": set_await(uid, "p_chan", {}); send(chat_id, tr(lang, "pc_ask"))
    elif op == "pcl": chan_card(chat_id, mid, lang, int(arg))
    elif op == "pcv": chan_card(chat_id, mid, lang, int(arg), recheck=True)
    elif op == "pcx": show(chat_id, mid, tr(lang, "pc_del_confirm"), kb([[btn(tr(lang, "yes"), f"a:pcxy:{arg}"), btn(tr(lang, "no"), f"a:pcl:{arg}")]]))
    elif op == "pcxy": poster.remove_channel(int(arg)); panel(chat_id, mid, lang)
    elif op == "pcL":
        c = poster.get_channel(int(arg))
        if c: poster.update_channel(int(arg), lang="en" if c.get("lang") == "fa" else "fa")
        chan_card(chat_id, mid, lang, int(arg))
    elif op in ("pcg", "pch", "pct"):
        set_await(uid, "p_chedit", {"id": int(arg), "f": {"pcg": "label", "pch": "handle", "pct": "tags"}[op]}); send(chat_id, tr(lang, "pc_ask_" + {"pcg": "label", "pch": "handle", "pct": "tags"}[op]))
    elif op == "pj": jobs_panel(chat_id, mid, lang)
    elif op == "pjn":
        rows = [
            [btn(tr(lang, "pj_t_artist"), f"a:pjm:{arg}:artist")], [btn(tr(lang, "pj_t_titles"), f"a:pjm:{arg}:titles")],
            [btn(tr(lang, "pj_t_query"), f"a:pjm:{arg}:query")], [btn(tr(lang, "pst_b_newfeed"), f"a:pfn:{arg}")], _back(lang, f"a:pcl:{arg}")]
        show(chat_id, mid, tr(lang, "pj_type"), kb(rows))
    elif op == "pjm":
        set_await(uid, "p_job", {"cid": int(arg), "type": arg2}); send(chat_id, tr(lang, "pj_ask_" + arg2))
    elif op == "pjv": job_view(chat_id, mid, lang, int(arg))
    elif op == "pjl": job_view(chat_id, mid, lang, int(arg), int(arg2 or 1))
    elif op == "pjt":
        poster.toggle_item(int(arg), int(arg2)); job_view(chat_id, mid, lang, int(arg), int(arg2) // PER_PAGE + 1)
    elif op == "pjs":
        n = poster.start_job(int(arg))
        if n is None: send(chat_id, tr(lang, "pst_gone"))
        else: send(chat_id, tr(lang, "pst_started", n=n)); job_view(chat_id, None, lang, int(arg))
    elif op == "pjp": poster.set_status(int(arg), "paused"); job_view(chat_id, mid, lang, int(arg))
    elif op == "pjr": poster.set_status(int(arg), "running"); job_view(chat_id, mid, lang, int(arg))
    elif op == "pjc": poster.set_status(int(arg), "cancelled"); job_view(chat_id, mid, lang, int(arg))
    elif op == "pjx": poster.remove_job(int(arg)); jobs_panel(chat_id, mid, lang)
    elif op in ("pji", "pjd"):
        set_await(uid, "p_jnum", {"id": int(arg), "f": "interval" if op == "pji" else "daily"}); send(chat_id, tr(lang, "pst_ask_" + ("interval" if op == "pji" else "daily")))
    elif op == "pf": feeds_panel(chat_id, mid, lang)
    elif op == "pfn":
        set_await(uid, "p_feed", {"cid": int(arg)}); send(chat_id, tr(lang, "pf_ask_genre"))
    elif op == "pfv": feed_view(chat_id, mid, lang, int(arg))
    elif op == "pfs":
        f = poster.get_feed(int(arg))
        if f and arg2 in poster.FEED_SOURCES:
            cur = list(f["sources"])
            (cur.remove(arg2) if arg2 in cur and len(cur) > 1 else (cur.append(arg2) if arg2 not in cur else None))
            poster.update_feed(int(arg), sources=cur)
        feed_view(chat_id, mid, lang, int(arg))
    elif op in ("pfa", "pfe"):
        f = poster.get_feed(int(arg))
        if f: poster.update_feed(int(arg), **{("auto" if op == "pfa" else "enabled"): not f["auto" if op == "pfa" else "enabled"]})
        feed_view(chat_id, mid, lang, int(arg))
    elif op in ("pfg", "pfi", "pfh", "pfd", "pfn2", "pfT"):
        f_ = {"pfg": "genre", "pfi": "top_n", "pfh": "refresh_h", "pfd": "interval", "pfn2": "daily", "pfT": "tags"}[op]
        set_await(uid, "p_fedit", {"id": int(arg), "f": f_}); send(chat_id, tr(lang, "pf_ask_" + f_))
    elif op == "pfx": poster.remove_feed(int(arg)); feeds_panel(chat_id, mid, lang)
    elif op == "pfr":
        send(chat_id, tr(lang, "pf_refreshing"))
        def job():
            jid = poster.refresh_feed(int(arg), notify)
            if not jid: send(chat_id, tr(lang, "pf_empty"))
        threading.Thread(target=job, daemon=True).start()
    elif op == "pz":
        rows = [[btn("🇮🇷 fa", "a:pzn:fa"), btn("🇬🇧 en", "a:pzn:en")], [btn(tr(lang, "pst_b_default"), "a:pzy")], _back(lang)]
        show(chat_id, mid, tr(lang, "pst_footer", fa=esc(poster.footer_template("fa")), en=esc(poster.footer_template("en"))), kb(rows))
    elif op == "pv": viral_panel(chat_id, mid, lang)
    elif op == "pvt": poster.set_viral_on(not poster.viral_on()); viral_panel(chat_id, mid, lang)
    elif op == "pvty":
        if arg == "e": set_await(uid, "p_vtags", {}); send(chat_id, tr(lang, "pv_ask_tags"))
        else: poster.set_viral_tags([]); send(chat_id, tr(lang, "pp_saved")); viral_panel(chat_id, None, lang)
    elif op == "pva": set_await(uid, "p_vadd", {}); send(chat_id, tr(lang, "pv_ask_add"))
    elif op == "pvc": poster.clear_viral_list(); viral_panel(chat_id, mid, lang)
    elif op == "pvx": poster.remove_viral_item(int(arg or 0)); viral_panel(chat_id, mid, lang)
    elif op == "pvr":
        send(chat_id, tr(lang, "pv_reranking"))
        def job():
            out = []
            for j in poster.jobs():
                if j["status"] in ("running", "paused"):
                    r = poster.rerank_job(j["id"])
                    if r: out.append("#%d: %s" % (j["id"], tr(lang, "pv_rerank_res", kept=r["kept"], dropped=r["dropped"])))
            send(chat_id, "\n".join(out) or tr(lang, "pv_none"))
        threading.Thread(target=job, daemon=True).start()
    elif op == "pzd":
        show(chat_id, mid, tr(lang, "pst_diss", tags=esc(" ".join(poster.diss_tags()))), kb([[btn(tr(lang, "pst_b_edit"), "a:pzdy:e"), btn(tr(lang, "pst_b_default"), "a:pzdy:d")], _back(lang)]))
    elif op == "pzdy":
        if arg == "e": set_await(uid, "p_diss", {}); send(chat_id, tr(lang, "pst_ask_diss"))
        else: poster.set_diss_tags([]); send(chat_id, tr(lang, "pp_saved")); callback("pzd", "", "", prev, chat_id, None, uid, lang)
    elif op == "pzf": charts_status(chat_id, mid, lang)
    elif op == "pzn":
        if arg in ("fa", "en"):
            set_await(uid, "p_footer", {"lang": arg}); send(chat_id, tr(lang, "pst_ask_footer"))
        else:
            send(chat_id, tr(lang, "pst_testing"))
            def job():
                for s in poster.FEED_SOURCES: poster.fetch_source(s, "", 3)
                charts_status(chat_id, None, lang)
            threading.Thread(target=job, daemon=True).start()
    elif op == "pzy":
        poster.set_footer("fa", ""); poster.set_footer("en", ""); send(chat_id, tr(lang, "pp_saved")); callback("pz", "", "", prev, chat_id, None, uid, lang)

# ------------------------------------------------------------------ text / forwarded
def forwarded_chat(msg):
    fo = msg.get("forward_origin") or {}
    return (fo.get("chat") if fo.get("type") == "channel" else None) or msg.get("forward_from_chat")

def text(msg, uid, lang, aw, data):
    chat_id = msg["chat"]["id"]; t = (msg.get("text") or msg.get("caption") or "").strip()
    if aw == "p_chan":
        fc = forwarded_chat(msg)
        chat, st = (None, "notfound")
        if fc: chat, st = poster.verify(str(fc["id"]))
        elif t: chat, st = poster.verify(t)
        if not chat: send(chat_id, tr(lang, "pc_err_notfound")); return True
        set_await(uid, None)
        handle = ("@" + chat["username"]) if chat.get("username") else ""
        cid = poster.add_channel(chat["id"], chat.get("title") or chat.get("username") or str(chat["id"]), handle, "", "fa")
        if cid is None: send(chat_id, tr(lang, "pc_dup")); return True
        if cid == 0: send(chat_id, tr(lang, "pc_full", n=poster.MAX_CHANNELS)); return True
        poster.update_channel(cid, ok=(st == "ok"))
        send(chat_id, tr(lang, "pc_added") + "\n" + tr(lang, "pc_st_" + st))
        set_await(uid, "p_chedit", {"id": cid, "f": "label"}); send(chat_id, tr(lang, "pc_ask_label")); return True
    if aw == "p_chedit":
        f = data["f"]
        if not t: return True
        if f == "tags": poster.update_channel(data["id"], tags=poster.parse_tags(t) if t != "-" else [])
        else: poster.update_channel(data["id"], **{f: ("" if t == "-" else (t if f == "label" else ("@" + logic.clean_username(t) if logic.clean_username(t) else t)))})
        set_await(uid, None); send(chat_id, tr(lang, "pp_saved")); chan_card(chat_id, None, lang, data["id"]); return True
    if aw == "p_job":
        if not t: return True
        set_await(uid, None); cid = data["cid"]; typ = data["type"]
        if typ == "artist":
            m = send(chat_id, tr(lang, "pst_searching", q=esc(t)))
            def job():
                ents = poster.artist_entries(t, poster.MAX_JOB_ITEMS)
                if not ents: send(chat_id, tr(lang, "pst_nothing", items="")); return
                start_matching(chat_id, lang, cid, t[:40], ents)
            threading.Thread(target=job, daemon=True).start()
        elif typ == "titles":
            pairs = poster.parse_titles(t)
            if not pairs: send(chat_id, tr(lang, "pst_nothing", items="")); return
            start_matching(chat_id, lang, cid, tr(lang, "pj_name_titles"), [{"artist": a, "title": ti, "cand": None, "meta": {}} for a, ti in pairs])
        else:
            def job():
                import music
                hits = music.search(t, 12)
                if not hits: send(chat_id, tr(lang, "pst_nothing", items="")); return
                ents = [{"artist": h.get("artist") or h.get("uploader") or "", "title": music.display_title(h), "cand": h, "meta": {}} for h in hits]
                start_matching(chat_id, lang, cid, t[:40], ents)
            threading.Thread(target=job, daemon=True).start()
        return True
    if aw == "p_jnum":
        n = parse_int(t, 0 if data["f"] == "daily" else 1, 10080)
        if n is None: send(chat_id, tr(lang, "a_bad_num")); return True
        poster.update_job(data["id"], **{data["f"]: n}); set_await(uid, None); send(chat_id, tr(lang, "pp_saved")); job_view(chat_id, None, lang, data["id"]); return True
    if aw == "p_feed":
        g = "" if t in ("-", "") else t.lower()
        set_await(uid, None)
        ch = poster.get_channel(data["cid"])
        fid = poster.create_feed(data["cid"], (g or tr(lang, "pf_name_all")) + " chart", g, ["rj_trending", "itunes", "audius"] if not (ch and ch.get("lang") == "en") else ["itunes", "audius", "soundcloud"], 10, 24, 60, 0, False)
        send(chat_id, tr(lang, "pf_created")); feed_view(chat_id, None, lang, fid); return True
    if aw == "p_fedit":
        f = data["f"]
        if f == "genre": poster.update_feed(data["id"], genre="" if t == "-" else t)
        elif f == "tags": poster.update_feed(data["id"], tags=poster.parse_tags(t) if t != "-" else [])
        else:
            n = parse_int(t, 0 if f == "daily" else 1, 10080)
            if n is None: send(chat_id, tr(lang, "a_bad_num")); return True
            poster.update_feed(data["id"], **{f: n})
        set_await(uid, None); send(chat_id, tr(lang, "pp_saved")); feed_view(chat_id, None, lang, data["id"]); return True
    if aw == "p_vtags":
        tags = poster.parse_tags(t)
        if not tags: send(chat_id, tr(lang, "pv_ask_tags")); return True
        poster.set_viral_tags(tags); set_await(uid, None); send(chat_id, tr(lang, "pp_saved")); viral_panel(chat_id, None, lang); return True
    if aw == "p_vadd":
        n = poster.add_viral_list(t); set_await(uid, None); send(chat_id, tr(lang, "pv_added", n=n)); viral_panel(chat_id, None, lang); return True
    if aw == "p_diss":
        tags = poster.parse_tags(t)
        if not tags: send(chat_id, tr(lang, "pst_ask_diss")); return True
        poster.set_diss_tags(tags); set_await(uid, None); send(chat_id, tr(lang, "pp_saved")); callback("pzd", "", "", {}, chat_id, None, uid, lang); return True
    if aw == "p_footer":
        if not t: return True
        poster.set_footer(data["lang"], "" if t == "-" else t); set_await(uid, None); send(chat_id, tr(lang, "pp_saved")); callback("pz", "", "", {}, chat_id, None, uid, lang); return True
    return False

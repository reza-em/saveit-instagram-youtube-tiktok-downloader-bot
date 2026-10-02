"""Campaign / promo section (owner + admins; callbacks 'a:cp*', user button 'cp:r:<id>', awaiting states 'cp_*').

campaign = {id, name, fa, en (HTML-safe templates with placeholders), photo (file_id|None), code_id (0 = none), redeem_btn, start_btn,
            created, log: {uid: ts} (who already got it - one delivery per user unless "resend to everyone"), blocked, failed, runs}
Placeholders are filled LIVE from the attached code (so editing the code updates the preview/next sends):
  {code} {benefit} {value} {expires} {max_uses} {remaining} {conditions}
Audience + delivery reuse the targeted-messaging module (filters, manual pick, recipient rules, rate, error classification)."""
import os, re, time, threading, logging
import core as C, store, logic, codes, ui, targeted
from core import tr, esc, btn, kb, show, send, set_await
from store import transaction

log = logging.getLogger("campaign")
MAX_TEXT = 3500
OPS = {"cp", "cpn", "cpv", "cpt", "cpi", "cpix", "cpc", "cpcs", "cpb", "cps", "cpf", "cpp", "cpy", "cpyr", "cpx", "cpxy", "cpr", "cpd", "cpz", "cpids", "cppick"}

DEFAULT_FA = ("🎁 <b>هدیه‌ی ویژه‌ی SaveIt</b>\n\n"
              "📥 دانلود از یوتیوب، اینستاگرام، تیک‌تاک، تردز، توییتر/ایکس، لینکدین، ساندکلود و اسپاتیفای — فقط لینک رو بفرست\n"
              "🎶 تشخیص آهنگ از ویس یا کلیپ\n"
              "👥 دعوت دوستان = دانلود هدیه\n"
              "🎤 کانال رپ: @rythmbox_rap\n\n"
              "🎟 کد هدیه: <code>{code}</code>\n🎁 {benefit}\n{conditions}\n\n"
              "همین الان دکمه‌ی «🎟 فعال‌سازی کد» رو بزن 👇")
DEFAULT_EN = ("🎁 <b>A special gift from SaveIt</b>\n\n"
              "📥 Download from YouTube, Instagram, TikTok, Threads, Twitter/X, LinkedIn, SoundCloud and Spotify — just send the link\n"
              "🎶 Song recognition from a voice note or clip\n"
              "👥 Invite friends = bonus downloads\n"
              "🎤 Rap channel: @rythmbox_rap\n\n"
              "🎟 Gift code: <code>{code}</code>\n🎁 {benefit}\n{conditions}\n\n"
              "Tap “🎟 Redeem code” right now 👇")

# ---------------------------------------------------------------- data
def _c(st): return st.setdefault("campaigns", [])

def all_():
    return store.snapshot().get("campaigns", [])

def get(cid):
    return next((c for c in all_() if c["id"] == int(cid)), None)

def newest_code():
    cs = codes.all_codes()
    return max(cs, key=lambda c: (c.get("created", 0), c["id"])) if cs else None

def create(name, fa=None, en=None, code_id=None):
    with transaction() as st:
        cid = st.get("next_campaign_id", 1); st["next_campaign_id"] = cid + 1
        _c(st).append({"id": cid, "name": str(name)[:40], "fa": fa if fa is not None else DEFAULT_FA, "en": en if en is not None else DEFAULT_EN,
                       "photo": None, "code_id": int(code_id or 0), "redeem_btn": True, "start_btn": True, "created": int(time.time()),
                       "log": {}, "blocked": 0, "failed": 0, "runs": 0})
        return cid

def update(cid, **kw):
    with transaction() as st:
        c = next((c for c in _c(st) if c["id"] == int(cid)), None)
        if not c: return False
        c.update(kw); return True

def remove(cid):
    with transaction() as st:
        n = len(_c(st)); st["campaigns"] = [c for c in _c(st) if c["id"] != int(cid)]
        return len(st["campaigns"]) != n

def ensure_default():
    """First visit: a ready draft that uses the newest existing code (nothing is sent)."""
    if all_() or (store.snapshot().get("campaign_seeded")): return
    nc = newest_code()
    with transaction() as st: st["campaign_seeded"] = True
    return create("Welcome gift", code_id=nc["id"] if nc else 0)

# ---------------------------------------------------------------- rendering
TAGS = ("b", "i", "u", "s", "code", "pre", "strong", "em")

def safe_html(t):
    """Escape everything, then re-allow a few formatting tags - but only if they are balanced (Telegram rejects broken HTML)."""
    e = esc(t or "")
    e = re.sub(r"&lt;(/?)(%s)&gt;" % "|".join(TAGS), r"<\1\2>", e, flags=re.I)
    for tg in TAGS:
        if len(re.findall(r"<%s>" % tg, e, re.I)) != len(re.findall(r"</%s>" % tg, e, re.I)):
            return re.sub(r"<(/?)(%s)>" % "|".join(TAGS), "", e, flags=re.I)
    return e

def _n(lang, x): return logic.fa_digits(x) if lang == "fa" else str(x)

def conditions(lang, c):
    L = []
    if c.get("once"): L.append(tr(lang, "cp_cond_once"))
    if c.get("max_uses"): L.append(tr(lang, "cp_cond_max", n=_n(lang, c["max_uses"])))
    if c.get("expires"): L.append(tr(lang, "cp_cond_exp", date=logic.fmt_date(c["expires"])))
    sc = ui.code_scope(lang, c)
    if sc: L.append(sc)
    return "\n".join("• " + x for x in L)

def fill(lang, template, c):
    t = safe_html(template)
    if c:
        rem = (c["max_uses"] - c.get("used", 0)) if c.get("max_uses") else None
        vals = {"code": esc(c["code"]), "benefit": esc(ui.code_what(lang, c)), "value": _n(lang, c["value"]),
                "expires": logic.fmt_date(c["expires"]) if c.get("expires") else tr(lang, "cd_never"),
                "max_uses": _n(lang, c["max_uses"]) if c.get("max_uses") else tr(lang, "cd_unl"),
                "remaining": _n(lang, max(0, rem)) if rem is not None else tr(lang, "cd_unl"), "conditions": esc(conditions(lang, c))}
    else:
        vals = {k: "—" for k in ("code", "benefit", "value", "expires", "max_uses", "remaining")}; vals["conditions"] = ""
    for k, v in vals.items(): t = t.replace("{" + k + "}", v)
    return t

def render(lang, cp):
    """-> (text, markup) for one recipient language (falls back to the other language's text)."""
    tpl = cp.get(lang) or cp.get("en" if lang == "fa" else "fa") or ""
    c = codes.get(cp["code_id"]) if cp.get("code_id") else None
    rows = []
    if c and cp.get("redeem_btn"): rows.append([btn(tr(lang, "cp_redeem"), f"cp:r:{cp['id']}")])
    if cp.get("start_btn"): rows.append([btn(tr(lang, "cp_start"), "m:menu")])
    return fill(lang, tpl, c)[:MAX_TEXT], (kb(rows) if rows else None)

def deliver(to, cp, lang):
    text, mk = render(lang, cp)
    if cp.get("photo"):
        ph = cp["photo"]; files = None
        if isinstance(ph, str) and ph.startswith("file:"):         # local image (e.g. assets/promo.jpg), uploaded on every send
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), ph[5:])
            ph = None
            if os.path.isfile(fp): files = {"photo": ("promo.jpg", open(fp, "rb"), "image/jpeg")}
        data = {"chat_id": to}
        if ph: data["photo"] = ph
        if files or ph:
            if len(text) <= 1000:
                data.update(caption=text, parse_mode="HTML")
                if mk: data["reply_markup"] = mk
                return C.call("sendPhoto", data, files) if files else C.call("sendPhoto", data)
            C.call("sendPhoto", data, files) if files else C.call("sendPhoto", data)
    d = {"chat_id": to, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if mk: d["reply_markup"] = mk
    return C.call("sendMessage", d)

def stats(cp):
    c = codes.get(cp["code_id"]) if cp.get("code_id") else None
    got = set(cp.get("log", {}))
    red = len(got & set((c or {}).get("users", {}))) if c else 0
    return {"sent": len(got), "blocked": cp.get("blocked", 0), "failed": cp.get("failed", 0), "redeemed": red}

# ---------------------------------------------------------------- audience / sending
def audience(uid, cp, include_done=False):
    ids = targeted.recipients(uid)
    done = set(cp.get("log", {}))
    return [i for i in ids if include_done or str(i) not in done], [i for i in ids if str(i) in done]

def start_send(chat_id, lang, uid, cid, include_done):
    cp = get(cid)
    if not cp or targeted.st_(uid).get("sending"): return False
    ids, _ = audience(uid, cp, include_done)
    if not ids: return False
    s = targeted.st_(uid); s["sending"] = True
    ul = {i: (store.get_user(i).get("lang") or "fa") for i in ids}
    def job():
        ok = blocked = failed = 0; buf = {}
        def flush():
            if buf:
                with transaction() as st:
                    c = next((x for x in _c(st) if x["id"] == int(cid)), None)
                    if c: c["log"].update(buf)
                buf.clear()
        try:
            for i in ids:
                for attempt in range(2):
                    try:
                        deliver(i, cp, ul[i] if ul[i] in ("fa", "en") else "fa"); ok += 1; buf[str(i)] = int(time.time()); break
                    except C.ApiError as e:
                        m_ = re.search(r"retry after (\d+)", str(e), re.I)
                        if m_ and attempt == 0:
                            time.sleep(min(int(m_.group(1)) + 1, 30)); continue
                        if targeted.classify_error(e) == "blocked":
                            blocked += 1; store.update_user(i, blocked=True)
                        else: failed += 1
                        break
                if len(buf) >= 20: flush()
                time.sleep(targeted.RATE)
        finally:
            flush()
            with transaction() as st:
                c = next((x for x in _c(st) if x["id"] == int(cid)), None)
                if c: c["blocked"] += blocked; c["failed"] += failed; c["runs"] += 1
            targeted.S.pop(uid, None)
        send(chat_id, tr(lang, "cp_report", name=esc(cp["name"]), total=len(ids), ok=ok, blocked=blocked, failed=failed))
    threading.Thread(target=job, daemon=True).start()
    return True

# ---------------------------------------------------------------- screens
def _st(lang, cp): return tr(lang, "cp_st_sent" if cp.get("log") else "cp_st_draft")

def panel(chat_id, mid, lang):
    ensure_default()
    cs = all_()
    lines = "\n".join(tr(lang, "cp_line", st=_st(lang, c), name=esc(c["name"]), sent=_n(lang, len(c.get("log", {})))) for c in cs) or tr(lang, "cp_none")
    rows = [[btn(("📤 " if c.get("log") else "📝 ") + c["name"][:30], f"a:cpv:{c['id']}")] for c in cs[:30]]
    rows.append([btn(tr(lang, "cp_b_new"), "a:cpn")]); rows.append([btn(tr(lang, "a_back"), "a:home")])
    show(chat_id, mid, tr(lang, "cp_home", lines=lines), kb(rows))

def view(chat_id, mid, lang, cid):
    cp = get(cid)
    if not cp: panel(chat_id, mid, lang); return
    c = codes.get(cp["code_id"]) if cp.get("code_id") else None
    s = stats(cp); yn = lambda b: tr(lang, "yes_s" if b else "no_s")
    cut = lambda t: esc((t or "—")[:500])
    show(chat_id, mid, tr(lang, "cp_view", name=esc(cp["name"]), st=_st(lang, cp), code=(esc(c["code"]) + " — " + esc(ui.code_what(lang, c))) if c else tr(lang, "cp_nocode"),
                          img=yn(cp.get("photo")), rb=yn(cp.get("redeem_btn")), sent=_n(lang, s["sent"]), blocked=_n(lang, s["blocked"]), failed=_n(lang, s["failed"]),
                          red=_n(lang, s["redeemed"]), fa=cut(cp.get("fa")), en=cut(cp.get("en"))),
         kb([[btn(tr(lang, "cp_b_fa"), f"a:cpt:{cid}:fa"), btn(tr(lang, "cp_b_en"), f"a:cpt:{cid}:en")],
             [btn(tr(lang, "cp_b_img"), f"a:cpi:{cid}")] + ([btn(tr(lang, "cp_b_imgx"), f"a:cpix:{cid}")] if cp.get("photo") else []),
             [btn(tr(lang, "cp_b_code"), f"a:cpc:{cid}:1"), btn(tr(lang, "cp_b_rb", s=yn(cp.get("redeem_btn"))), f"a:cpb:{cid}")],
             [btn(tr(lang, "cp_b_send"), f"a:cps:{cid}")],
             [btn(tr(lang, "cp_b_name"), f"a:cpr:{cid}"), btn(tr(lang, "cp_b_default"), f"a:cpz:{cid}"), btn(tr(lang, "cp_b_dup"), f"a:cpd:{cid}")],
             [btn(tr(lang, "cd_b_del"), f"a:cpx:{cid}"), btn(tr(lang, "a_back"), "a:cp")]]))

def pick_code(chat_id, mid, lang, cid, page):
    cs = sorted(codes.all_codes(), key=lambda c: -c["id"]); per = 8; pages = max(1, (len(cs) + per - 1) // per); page = max(1, min(page, pages))
    rows = [[btn(("🟢 " if codes.problem(c) is None else "⚪️ ") + f"{c['code']} · {ui.code_what(lang, c)}"[:44], f"a:cpcs:{cid}:{c['id']}")] for c in cs[(page - 1) * per: page * per]]
    nav = []
    if page > 1: nav.append(btn("⬅️", f"a:cpc:{cid}:{page - 1}"))
    if page < pages: nav.append(btn("➡️", f"a:cpc:{cid}:{page + 1}"))
    if nav: rows.append(nav)
    rows.append([btn(tr(lang, "cp_no_code"), f"a:cpcs:{cid}:0")]); rows.append([btn(tr(lang, "a_back"), f"a:cpv:{cid}")])
    show(chat_id, mid, tr(lang, "cp_pick_code"), kb(rows))

def audience_screen(chat_id, mid, lang, uid, cid):
    cp = get(cid)
    if not cp: return
    s = targeted.st_(uid); s["back"] = f"a:cps:{cid}"
    new, done = audience(uid, cp)
    fl = lambda b: "✅" if b else "⬜"
    rows = [[btn(f"{fl(s['all'])} " + tr(lang, "cp_f_all"), f"a:cpf:{cid}:all")],
            [btn(f"{fl(s['plan'])} " + tr(lang, "tm_f_plan"), f"a:cpf:{cid}:plan"), btn(f"{fl(s['expired'])} " + tr(lang, "tm_f_expired"), f"a:cpf:{cid}:expired")],
            [btn(f"{fl(s['lang'] == 'fa')} فارسی", f"a:cpf:{cid}:fa"), btn(f"{fl(s['lang'] == 'en')} English", f"a:cpf:{cid}:en")],
            [btn(tr(lang, "tm_b_ids"), f"a:cpids:{cid}"), btn(tr(lang, "tm_b_pick"), "a:tmp:1")],
            [btn(tr(lang, "cp_b_go", n=_n(lang, len(new))), f"a:cpp:{cid}")],
            [btn(tr(lang, "a_back"), f"a:cpv:{cid}")]]
    show(chat_id, mid, tr(lang, "cp_aud", name=esc(cp["name"]), new=_n(lang, len(new)), done=_n(lang, len(done)), manual=_n(lang, len(s["sel"]))), kb(rows))

def preview(chat_id, lang, uid, cid):
    cp = get(cid)
    if not cp: return
    new, done = audience(uid, cp)
    for l in ("fa", "en"):
        if cp.get(l):
            try: deliver(chat_id, cp, l)
            except C.ApiError as e: send(chat_id, tr(lang, "cp_bad_html", err=esc(C.safe(e)[:120])))
    rows = [[btn(tr(lang, "cp_b_yes", n=_n(lang, len(new))), f"a:cpy:{cid}")]]
    if done: rows.append([btn(tr(lang, "cp_b_yes_all", n=_n(lang, len(new) + len(done))), f"a:cpyr:{cid}")])
    rows.append([btn(tr(lang, "no"), f"a:cps:{cid}")])
    send(chat_id, tr(lang, "cp_confirm", n=_n(lang, len(new)), done=_n(lang, len(done)), rate=int(1 / max(targeted.RATE, 0.001))), kb(rows))

# ---------------------------------------------------------------- callbacks
def callback(op, arg, arg2, prev, chat_id, mid, uid, lang):
    if op == "cp": panel(chat_id, mid, lang)
    elif op == "cpn": set_await(uid, "cp_name", {"new": True}); send(chat_id, tr(lang, "cp_ask_name"))
    elif op == "cpv": view(chat_id, mid, lang, int(arg))
    elif op == "cpt":
        set_await(uid, "cp_text", {"id": int(arg), "l": arg2 if arg2 in ("fa", "en") else "fa"}); send(chat_id, tr(lang, "cp_ask_text"))
    elif op == "cpi": set_await(uid, "cp_img", {"id": int(arg)}); send(chat_id, tr(lang, "cp_ask_img"))
    elif op == "cpix": update(int(arg), photo=None); view(chat_id, mid, lang, int(arg))
    elif op == "cpc": pick_code(chat_id, mid, lang, int(arg), int(arg2 or 1))
    elif op == "cpcs": update(int(arg), code_id=int(arg2 or 0)); view(chat_id, mid, lang, int(arg))
    elif op == "cpb":
        cp = get(int(arg))
        if cp: update(cp["id"], redeem_btn=not cp["redeem_btn"])
        view(chat_id, mid, lang, int(arg))
    elif op == "cpr": set_await(uid, "cp_name", {"id": int(arg)}); send(chat_id, tr(lang, "cp_ask_name"))
    elif op == "cpz": update(int(arg), fa=DEFAULT_FA, en=DEFAULT_EN); view(chat_id, mid, lang, int(arg))
    elif op == "cpd":
        cp = get(int(arg))
        if cp:
            n = create(cp["name"] + " 2", cp["fa"], cp["en"], cp["code_id"]); update(n, photo=cp.get("photo"), redeem_btn=cp["redeem_btn"]); view(chat_id, None, lang, n)
    elif op == "cpx":
        cp = get(int(arg))
        if cp: show(chat_id, mid, tr(lang, "cp_confirm_del", name=esc(cp["name"])), kb([[btn(tr(lang, "yes"), f"a:cpxy:{arg}"), btn(tr(lang, "no"), f"a:cpv:{arg}")]]))
    elif op == "cpxy": remove(int(arg)); panel(chat_id, mid, lang)
    elif op == "cps":
        s = targeted.st_(uid)
        if not s.get("_cp") == int(arg):                       # entering this campaign's audience screen: start from "everyone"
            targeted.reset(uid); s = targeted.st_(uid); s["all"] = True; s["_cp"] = int(arg)
        audience_screen(chat_id, mid, lang, uid, int(arg))
    elif op == "cpf":
        s = targeted.st_(uid)
        if arg2 == "all":
            s["all"] = not s["all"]
            if s["all"]: s["plan"] = s["expired"] = False
        elif arg2 in ("plan", "expired"):
            s[arg2] = not s[arg2]
            if s[arg2]: s["all"] = False
        elif arg2 in ("fa", "en"): s["lang"] = None if s["lang"] == arg2 else arg2
        audience_screen(chat_id, mid, lang, uid, int(arg))
    elif op == "cpids": set_await(uid, "cp_ids", {"id": int(arg)}); send(chat_id, tr(lang, "tm_ask_ids"))
    elif op == "cpp": preview(chat_id, lang, uid, int(arg))
    elif op in ("cpy", "cpyr"):
        if start_send(chat_id, lang, uid, int(arg), op == "cpyr"): show(chat_id, mid, tr(lang, "a_bc_sending"))
        else: send(chat_id, tr(lang, "a_bc_none"))

def text(msg, uid, lang, aw, data):
    chat_id = msg["chat"]["id"]; t = (msg.get("text") or msg.get("caption") or "").strip()
    if aw == "cp_name":
        if not t: return True
        if data.get("new"):
            nc = newest_code(); cid = create(t, code_id=nc["id"] if nc else 0)
        else: cid = data["id"]; update(cid, name=t[:40])
        set_await(uid, None); view(chat_id, None, lang, cid); return True
    if aw == "cp_text":
        if not t: return True
        update(data["id"], **{data["l"]: t[:MAX_TEXT]}); set_await(uid, None); send(chat_id, tr(lang, "pp_saved")); view(chat_id, None, lang, data["id"]); return True
    if aw == "cp_img":
        if msg.get("photo"):
            update(data["id"], photo=msg["photo"][-1]["file_id"]); set_await(uid, None); view(chat_id, None, lang, data["id"])
        else: send(chat_id, tr(lang, "cp_ask_img"))
        return True
    if aw == "cp_ids":
        ids, bad = targeted.parse_targets(t)
        if not ids and bad: send(chat_id, tr(lang, "a_not_found")); return True
        s = targeted.st_(uid); s["sel"].update(ids); s["all"] = False; set_await(uid, None)
        if bad: send(chat_id, tr(lang, "tm_unknown", items=esc(" ".join(bad)[:200])))
        audience_screen(chat_id, None, lang, uid, data["id"]); return True
    return False

def user_callback(chat_id, uid, lang, mid, data):
    """'cp:r:<id>' - the Redeem button inside a campaign message: instant redeem of its attached code."""
    p = data.split(":")
    if len(p) < 3 or p[1] != "r": return
    try: cp = get(int(p[2]))
    except ValueError: return
    c = codes.get(cp["code_id"]) if cp and cp.get("code_id") else None
    if not c:
        send(chat_id, tr(lang, "code_bad")); return
    ui.code_entered(chat_id, uid, lang, c["code"])

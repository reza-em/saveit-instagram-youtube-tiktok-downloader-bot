"""Download jobs: per-user queue (1 active + small queue), global concurrency, probing, sending to Telegram."""
import os, json, time, threading, secrets, logging, collections, re
import core as C
import store, logic, engine, ui, theme
from core import tr, esc, btn, kb
from engine import EngineError

log = logging.getLogger("jobs")
GLOBAL_SLOTS = 2
USER_QUEUE_MAX = 3            # waiting jobs per user (in addition to the 1 active)
MAX_LINKS = 3                 # links handled per message
TOKEN_TTL = 3 * 3600
TOKEN_MAX = 600
LIMIT = engine.SAFE_LIMIT
_gsem = threading.Semaphore(GLOBAL_SLOTS)
_ulock = threading.Lock()
_users = {}                   # uid -> {"running": bool, "q": deque}
_tokens = {}                  # tok -> {"uid","chat","info","ts"}
_tlock = threading.Lock()
SYNC = False                  # tests: run submitted jobs inline

ERR_KEY = {"private": "err_private", "login": "err_login", "age": "err_age", "geo": "err_geo", "rate": "err_rate",
           "unsupported": "err_unsupported", "notfound": "err_notfound", "ipblock": "err_ipblock",
           "timeout": "err_timeout", "toolong": "err_toolong", "nomedia": "err_nomedia", "cookies_needed": "cookies_needed"}

# ---------------------------------------------------------------- queue
def submit(uid, fn):
    """Queue fn() for this user. Returns 'started' | 'queued' | 'full'."""
    with _ulock:
        u = _users.setdefault(uid, {"running": False, "q": collections.deque()})
        if u["running"]:
            if len(u["q"]) >= USER_QUEUE_MAX:
                return "full"
            u["q"].append(fn)
            return "queued"
        u["running"] = True
        u["q"].append(fn)
    if SYNC:
        _drain(uid)
    else:
        threading.Thread(target=_drain, args=(uid,), daemon=True).start()
    return "started"

def _drain(uid):
    while True:
        with _ulock:
            u = _users[uid]
            if not u["q"]:
                u["running"] = False
                return
            fn = u["q"].popleft()
        theme.set_user(uid)
        with _gsem:
            try:
                fn()
            except Exception as e:
                log.exception("job crashed: %s", C.safe(e))

def user_busy(uid):
    with _ulock:
        u = _users.get(uid)
        return bool(u and (u["running"] or u["q"]))

# ---------------------------------------------------------------- tokens
def put_token(uid, chat_id, info):
    now = time.time()
    with _tlock:
        for k in [k for k, v in _tokens.items() if now - v["ts"] > TOKEN_TTL]:
            _tokens.pop(k, None)
        while len(_tokens) >= TOKEN_MAX:
            _tokens.pop(min(_tokens, key=lambda k: _tokens[k]["ts"]), None)
        tok = secrets.token_hex(4)
        _tokens[tok] = {"uid": uid, "chat": chat_id, "info": info, "ts": now}
        return tok

def get_token(tok):
    with _tlock:
        return _tokens.get(tok)

# ---------------------------------------------------------------- helpers
def plat_label(lang, plat):
    return tr(lang, "plat_" + plat)

def fmt_dur(s):
    s = int(s or 0)
    return f"{s//3600}:{s%3600//60:02d}:{s%60:02d}" if s >= 3600 else f"{s//60}:{s%60:02d}"

def err_text(lang, e):
    if isinstance(e, EngineError):
        return tr(lang, ERR_KEY.get(e.code, "err_unknown"))
    return tr(lang, "err_unknown")

def menu_row(lang):
    return [btn(tr(lang, "b_menu"), "m:menu")]

def fail(chat_id, lang, uid, platform, e, status_mid=None, url=""):
    code = e.code if isinstance(e, EngineError) else "exception"
    logic.record_error(uid, platform, code, getattr(e, "detail", "") or str(e), url)
    log.info("job failed: platform=%s code=%s", platform, code)
    txt = err_text(lang, e)
    markup = kb([menu_row(lang)])
    if status_mid:
        C.show(chat_id, status_mid, txt, markup)
    else:
        C.send(chat_id, txt, markup)

class Progress:
    """Throttled 'downloading xx%' message edits."""
    def __init__(self, chat_id, mid, lang, every=2.5):
        self.chat, self.mid, self.lang, self.every, self.last = chat_id, mid, lang, every, 0.0
    def __call__(self, pct):
        now = time.time()
        if now - self.last < self.every:
            return
        self.last = now
        C.edit_text(self.chat, self.mid, tr(self.lang, "downloading", pct="%d%%" % pct))
    def stage(self, key):
        C.edit_text(self.chat, self.mid, tr(self.lang, key))

def after_success(chat_id, uid, lang, platform):
    logic.consume(uid, platform)
    ad = logic.ad_due(uid)
    if ad:
        ui.send_ad(chat_id, ad, lang)

def quota_ok(chat_id, uid, lang, platform=None):
    """Per-platform check: free daily quota of `platform` left, or credits, or unlimited/admin."""
    if logic.access_info(uid, platform)["can"]:
        return True
    ui.show_upgrade(chat_id, uid, lang, platform)
    return False

# ---------------------------------------------------------------- link intake
def handle_links(chat_id, uid, lang, text):
    """Called for a message containing URLs. Returns True if something was handled."""
    urls = engine.extract_urls(text)
    if not urls:
        return False
    cls = []
    for u in urls:
        c = engine.classify(u)
        if c:
            cls.append(c)
    if not cls:
        C.send(chat_id, tr(lang, "err_unsupported"), kb([menu_row(lang)]))
        return True
    if len(cls) > MAX_LINKS:
        C.send(chat_id, tr(lang, "too_many_links", n=MAX_LINKS))
        cls = cls[:MAX_LINKS]
    for c in cls:
        logic.record_link()
        if not quota_ok(chat_id, uid, lang, c["platform"]):     # don't waste a probe when the platform limit is already hit
            continue
        analyze(chat_id, uid, lang, c)
    return True

def analyze(chat_id, uid, lang, c):
    m = C.send(chat_id, tr(lang, "analyzing"))
    mid = (m or {}).get("message_id")
    def job():
        try:
            if c["kind"] == "profile":
                info = {"kind": "profile", "platform": "instagram", "user": c["user"], "url": c["url"]}
            else:
                info = engine.probe(c)
        except EngineError as e:
            return fail(chat_id, lang, uid, c["platform"], e, mid, c["url"])
        except Exception as e:
            return fail(chat_id, lang, uid, c["platform"], e, mid, c["url"])
        show_options(chat_id, uid, lang, info, mid)
    r = submit(uid, job)
    if r == "full":
        C.show(chat_id, mid, tr(lang, "busy_limit"), kb([menu_row(lang)]))
    elif r == "queued":
        C.edit_text(chat_id, mid, tr(lang, "queued"))

def analyze_username(chat_id, uid, lang, user):
    c = {"platform": "instagram", "kind": "profile", "user": user, "url": "https://www.instagram.com/%s/" % user}
    logic.record_link()
    if not quota_ok(chat_id, uid, lang, "instagram"):
        return
    analyze(chat_id, uid, lang, c)

# ---------------------------------------------------------------- option screens
def show_options(chat_id, uid, lang, info, mid):
    tok = put_token(uid, chat_id, info)
    rows = []
    kind = info["kind"]
    plat = info["platform"]
    if kind == "video":
        quals = info.get("quals") or {}
        meta = tr(lang, "meta_line", dur=fmt_dur(info.get("duration")), who=esc(info.get("uploader") or "—"))
        text = tr(lang, "opt_video_title", title=esc(info["title"])[:150], meta=meta)
        if quals:
            best = engine.pick_best_fit(quals, LIMIT)
            if best:
                rows.append([btn(tr(lang, "b_best", mb=50, q=best), f"d:{tok}:vb")])
            ladder = engine.ladder_options(list(quals))
            row, warn = [], False
            for q in ladder:
                s = quals[q]["size"]
                too_big = s is not None and s > LIMIT
                warn = warn or too_big
                size = (" · " + C.fmt_size(s)) if s else ""
                label = tr(lang, "b_q", q=q, size=size) + (" ⚠️" if too_big else "")
                row.append(btn(label, f"d:{tok}:v{q}"))
                if len(row) == 2:
                    rows.append(row); row = []
            if row: rows.append(row)
            if warn: text += tr(lang, "warn_big")
        else:
            rows.append([btn(tr(lang, "b_best_plain"), f"d:{tok}:vb")])
        if not info.get("no_audio"):
            rows.append([btn(tr(lang, "b_mp3"), f"d:{tok}:a3"), btn(tr(lang, "b_m4a"), f"d:{tok}:a4")])
    elif kind == "gallery":
        items = info["items"]; n = len(items)
        text = tr(lang, "opt_gallery_title", title=esc(info["title"])[:150], n=n)
        nphoto = sum(1 for i in items if i["type"] == "image")
        label = tr(lang, "b_all", n=n) if n > 1 else (tr(lang, "b_photos", n=1) if nphoto else tr(lang, "b_best_plain"))
        rows.append([btn(label, f"d:{tok}:g"), btn(tr(lang, "b_files", n=n), f"d:{tok}:f")])
        if 1 < n <= 12:
            row = []
            for i, it in enumerate(items):
                row.append(btn(("🖼 " if it["type"] == "image" else "🎬 ") + str(i + 1), f"d:{tok}:i{i}"))
                if len(row) == 4:
                    rows.append(row); row = []
            if row: rows.append(row)
    elif kind == "audio":
        meta = tr(lang, "meta_line", dur=fmt_dur(info.get("duration")), who=esc(info.get("uploader") or "—"))
        text = tr(lang, "opt_audio_title", title=esc(info["title"])[:150], meta=meta)
        if plat == "spotify":
            m = info.get("match") or {}
            text += tr(lang, "spotify_honest", src=esc(m.get("source_label") or "YouTube"), yt=esc((m.get("title") or "")[:100]))
        rows.append([btn(tr(lang, "b_mp3"), f"d:{tok}:a3"), btn(tr(lang, "b_m4a"), f"d:{tok}:a4")])
    elif kind == "collection":
        n = len(info["items"]); total = info.get("total") or n
        text = tr(lang, "opt_coll_title", title=esc(info["title"])[:150], who=esc(info.get("uploader") or "—"), n=n, total=total)
        if total > n:
            text += tr(lang, "coll_limited", n=n, total=total)
        if plat == "spotify":
            text += tr(lang, "spotify_honest_coll")
        rows.append([btn(tr(lang, "b_coll_mp3", n=n), f"d:{tok}:c3"), btn(tr(lang, "b_coll_m4a", n=n), f"d:{tok}:c4")])
    elif kind == "profile":
        text = tr(lang, "opt_profile_title", user=esc(info["user"]))
        rows.append([btn(tr(lang, "b_profile"), f"d:{tok}:p"), btn(tr(lang, "b_stories"), f"d:{tok}:s")])
    else:
        return
    rows.append(menu_row(lang))
    C.show(chat_id, mid, text, kb(rows))

# ---------------------------------------------------------------- callback entry
def handle_choice(chat_id, uid, lang, mid, data):
    """data = 'd:<tok>:<act>'."""
    parts = data.split(":")
    if len(parts) < 3:
        return
    tok, act = parts[1], parts[2]
    t = get_token(tok)
    if not t:
        C.send(chat_id, tr(lang, "expired"), kb([menu_row(lang)])); return
    if t["uid"] != uid:
        C.send(chat_id, tr(lang, "not_yours")); return
    info = t["info"]
    if act == "s":          # stories listing for a username (needs cookies) - a probe job, not a download
        def probe_job():
            try:
                c = {"platform": "instagram", "kind": "stories", "user": info["user"],
                     "url": "https://www.instagram.com/stories/%s/" % info["user"]}
                res = engine.probe(c)
            except EngineError as e:
                return fail(chat_id, lang, uid, "instagram", e, mid, info["url"])
            except Exception as e:
                return fail(chat_id, lang, uid, "instagram", e, mid, info["url"])
            show_options(chat_id, uid, lang, res, mid)
        C.edit_text(chat_id, mid, tr(lang, "analyzing"))
        r = submit(uid, probe_job)
        if r == "full": C.send(chat_id, tr(lang, "busy_limit"))
        return
    if not quota_ok(chat_id, uid, lang, info["platform"]):
        return
    def job():
        run_download(chat_id, uid, lang, mid, info, act)
    C.edit_text(chat_id, mid, tr(lang, "queued") if user_busy(uid) else tr(lang, "downloading", pct=""))
    r = submit(uid, job)
    if r == "full":
        C.send(chat_id, tr(lang, "busy_limit"), kb([menu_row(lang)]))

# ---------------------------------------------------------------- download + send
def run_download(chat_id, uid, lang, mid, info, act):
    if not quota_ok(chat_id, uid, lang, info["platform"]):      # re-check when the job actually starts (queued jobs)
        return
    wd = engine.new_workdir()
    prog = Progress(chat_id, mid, lang)
    try:
        prog.last = 0
        C.edit_text(chat_id, mid, tr(lang, "downloading", pct="0%"))
        plat = info["platform"]
        if info["kind"] == "video":
            if act.startswith("a"):
                ok = send_audio(chat_id, uid, lang, mid, info, "m4a" if act == "a4" else "mp3", wd, prog)
            else:
                q = None if act == "vb" else int(act[1:])
                ok = send_video(chat_id, uid, lang, mid, info, q, wd, prog)
        elif info["kind"] == "audio":
            ok = send_audio(chat_id, uid, lang, mid, info, "m4a" if act == "a4" else "mp3", wd, prog)
        elif info["kind"] == "collection":
            send_collection(chat_id, uid, lang, mid, info, "m4a" if act == "c4" else "mp3", wd, prog)
            ok = False              # per-track consume/ads are handled inside send_collection
        elif info["kind"] == "gallery":
            idx = int(act[1:]) if act.startswith("i") else None
            ok = send_gallery(chat_id, uid, lang, mid, info, wd, prog, only=idx, as_files=(act == "f"))
        elif info["kind"] == "profile":
            ok = send_profile(chat_id, uid, lang, mid, info, wd, prog)
        else:
            ok = False
        if ok:
            after_success(chat_id, uid, lang, plat)
    except EngineError as e:
        fail(chat_id, lang, uid, info["platform"], e, mid, info.get("url", ""))
    except C.ApiError as e:
        logic.record_error(uid, info["platform"], "upload", str(e), info.get("url", ""))
        C.show(chat_id, mid, tr(lang, "err_upload", d=esc(C.safe(e))[:120]), kb([menu_row(lang)]))
    except Exception as e:
        fail(chat_id, lang, uid, info["platform"], e, mid, info.get("url", ""))
    finally:
        engine.cleanup(wd)

CAP_LIMIT = 1024
MSG_LIMIT = 4096

def _esc_len(t):
    return len(esc(t))

def _cut_fit(text, budget):
    """Longest prefix of `text` whose HTML-escaped length <= budget, cut at a whitespace/newline when possible."""
    if _esc_len(text) <= budget:
        return text, ""
    lo, hi = 0, len(text)
    while lo < hi:                       # binary search on prefix length
        mid = (lo + hi + 1) // 2
        if _esc_len(text[:mid]) <= budget: lo = mid
        else: hi = mid - 1
    cut = text[:lo]
    k = max(cut.rfind("\n"), cut.rfind(" "))
    if k > lo * 0.6:
        cut = cut[:k]
    return cut.rstrip(), text[len(cut):].lstrip()

def _chunks(text, size=4000):
    out = []
    while text:
        part, text = _cut_fit(text, size)
        if not part:
            part, text = text[:size], text[size:]
        out.append(part)
    return out

def build_caption(lang, info, limit=CAP_LIMIT):
    """-> (caption_html <= limit, [followup_html, ...]). Original caption/description/title first, the small
    '🤖 @bot' signature always kept as the last line. Overflow is cut cleanly with '…' and the rest is sent as
    follow-up message(s) (each <= 4096). Everything is HTML-escaped; a YouTube title is shown in bold."""
    sig = "🤖 @" + C.BOT_USERNAME
    raw = (info.get("caption") or "").strip() or (info.get("title") or "").strip()
    head, body = "", raw
    if info.get("platform") == "youtube" and "\n\n" in raw:
        head, body = raw.split("\n\n", 1)
    def render(h, b_):
        parts = []
        if h: parts.append("<b>" + esc(h) + "</b>")
        if b_: parts.append(esc(b_))
        return "\n\n".join(parts)
    tail = "\n\n" + sig
    full = render(head, body)
    if not full:
        return sig, []
    if len(full) + len(tail) <= limit:
        return full + tail, []
    budget = limit - len(tail) - 1                      # 1 for the ellipsis
    if head:
        hb = budget // 2
        if _esc_len(head) > hb:
            h_fit, h_rest = _cut_fit(head, hb - len("<b></b>"))
            head = h_fit + "…"
            body = (h_rest + "\n\n" + body).strip() if h_rest else body
        budget -= len(esc(head)) + len("<b></b>") + 2
    b_fit, b_rest = _cut_fit(body, max(0, budget))
    cap = render(head, (b_fit + "…") if b_rest else b_fit)
    follow = [esc(t) for t in _chunks(("…" + b_rest) if b_rest else "", 3900)]
    return cap + tail, follow

def send_followups(chat_id, follow):
    for t in follow:
        C.send(chat_id, t, None)

def _caption(lang, info):
    return build_caption(lang, info if isinstance(info, dict) else {"title": info, "platform": ""})[0]

def _upload(method, field, path, data, timeout=900, name=None):
    with open(path, "rb") as f:
        return C.call(method, data, files={field: (name or os.path.basename(path), f)}, timeout=timeout)

def finish(chat_id, mid, lang):
    C.delete_msg(chat_id, mid)

def send_video(chat_id, uid, lang, mid, info, q, wd, prog):
    quals = info.get("quals") or {}
    if q is None:
        start = engine.pick_best_fit(quals, LIMIT) if quals else None
    else:
        start = q
    if quals and start is None:
        start = min(quals)                 # nothing fits by estimate: try the smallest, verify after download
    cands = [start]
    if quals and start is not None:
        cands += [x for x in sorted(quals, reverse=True) if x < start]
    requested = start
    tried = 0
    last_size = None
    for cand in cands:
        s = quals[cand]["size"] if cand in quals else None
        if s is not None and s > LIMIT and cand != cands[-1]:
            continue                       # estimate says it won't fit -> skip without downloading
        if tried >= 4:
            break
        tried += 1
        engine._clear_media(wd)
        path = engine.download_video(info, cand, wd, prog)
        size = os.path.getsize(path)
        last_size = size
        if size > LIMIT:
            continue
        prog.stage("uploading")
        meta = engine.probe_media(path)
        thumb = engine.make_thumb(path, wd)
        cap, follow = build_caption(lang, info)
        data = {"chat_id": chat_id, "caption": cap, "parse_mode": "HTML", "supports_streaming": "true"}
        for k in ("width", "height", "duration"):
            if meta.get(k): data[k] = meta[k]
        files = {"video": (os.path.basename(path), open(path, "rb"))}
        if thumb:
            files["thumbnail"] = ("thumb.jpg", open(thumb, "rb")); data["thumbnail"] = "attach://thumbnail"
        try:
            try:
                C.call("sendVideo", data, files=files, timeout=900)
            except C.ApiError:
                for fh in files.values(): fh[1].seek(0)
                data.pop("thumbnail", None); data.pop("supports_streaming", None)
                C.call("sendDocument", {"chat_id": chat_id, "caption": data["caption"], "parse_mode": "HTML"},
                       files={"document": files["video"]}, timeout=900)
        finally:
            for fh in files.values(): fh[1].close()
        send_followups(chat_id, follow)
        if info.get("_audio_missing"):
            C.send(chat_id, tr(lang, "no_audio_warn"))
        if cand != requested and cand is not None:
            C.send(chat_id, tr(lang, "fit_lower", q=cand))
        finish(chat_id, mid, lang)
        return True
    # nothing fits
    C.show(chat_id, mid, tr(lang, "too_big"), kb([[btn(tr(lang, "b_mp3"), f"d:{_retok(uid, chat_id, info)}:a3"),
                                                    btn(tr(lang, "b_m4a"), f"d:{_retok(uid, chat_id, info)}:a4")],
                                                   menu_row(lang)]))
    logic.record_error(uid, info["platform"], "too_big", "size %s" % last_size, info.get("url", ""))
    return False

def _retok(uid, chat_id, info):
    return put_token(uid, chat_id, info)

def _thumb_from_url(url, wd, name="cover.jpg", px=320, kb_max=200):
    """Download a cover image and shrink it to a Telegram-valid audio thumbnail (JPEG, <=200 KB, <=320 px). Best effort."""
    if not url:
        return None
    try:
        raw = os.path.join(wd, "cover_raw")
        engine.download_direct(url, raw, limit=8 * 1024 * 1024)
        from PIL import Image
        out = os.path.join(wd, name)
        with Image.open(raw) as im:
            im = im.convert("RGB"); im.thumbnail((px, px))
            for qlt in (88, 80, 70, 50):
                im.save(out, "JPEG", quality=qlt)
                if os.path.getsize(out) <= kb_max * 1024:
                    break
        os.remove(raw)
        return out
    except Exception as e:
        log.info("cover thumbnail skipped: %s", type(e).__name__)
        return None

def prepare_music_file(info, path, wd, show_source=True):
    """Music-chain audio: fill album/year/genre from free lookups, build the clean caption (title/artist/album/year/genre + the
    source actually used + SoundCloud description), embed ID3/M4A tags + cover, and register the 📝 lyrics button token."""
    import meta as MT
    try:
        tags = dict(info.get("tags") or {})
        tags.update(title=info["title"], artist=info.get("uploader") or "")
        MT.complete(tags)
        info["tags"] = tags
        m = info.get("match") or {}
        src = m.get("source_label")
        lines = MT.lines(tags, (src + " ▸ " + (m.get("title") or "")[:100]) if (src and show_source) else None)
        desc = ""
        if show_source and m.get("source") == "soundcloud" and (info.get("url") or "").startswith("http"):
            desc = MT.sc_description(info["url"])[:600]
        info["caption"] = "\n".join(lines) + (("\n\n" + desc) if desc else "")
        cover = _thumb_from_url(info.get("thumb"), wd, name="cover_tag.jpg", px=600, kb_max=900) or None
        path = MT.embed_tags(path, tags, cover, wd)
        if show_source:
            info["lyr_tok"] = put_token(info.get("_uid", 0), info.get("_chat", 0), {"kind": "recog", "platform": "music_id", "cands": [
                {"title": tags["title"], "artist": tags["artist"], "album": tags.get("album", ""), "year": tags.get("year", ""),
                 "genre": tags.get("genre", ""), "duration": info.get("duration") or 0, "cover": info.get("thumb"),
                 "lyrics": info.get("lyrics") or "", "rj_id": m.get("id") if m.get("source") == "radiojavan" else None}]})
    except Exception as e:
        log.info("music file prepare skipped: %s", type(e).__name__)
    return path

def deliver_audio(chat_id, uid, lang, mid, info, fmt_name, wd, prog):
    """Download + upload one audio file (title/performer/cover/caption). No status-message cleanup. -> True/False."""
    engine._clear_media(wd)
    info["_uid"], info["_chat"] = uid, chat_id
    path = engine.download_audio(info, fmt_name, wd, prog)
    note = None
    if os.path.getsize(path) > LIMIT:
        k = engine.fit_audio_bitrate(info.get("duration"))
        if not k:
            C.show(chat_id, mid, tr(lang, "audio_too_big"), kb([menu_row(lang)]))
            logic.record_error(uid, info["platform"], "audio_too_big", "", info.get("url", ""))
            return False
        path = engine.reencode_audio(path, k, wd)
        if os.path.getsize(path) > LIMIT:
            C.show(chat_id, mid, tr(lang, "audio_too_big"), kb([menu_row(lang)]))
            return False
        note = tr(lang, "fit_audio", k=k)
    prog.stage("uploading")
    if info.get("via") == "music":
        path = prepare_music_file(info, path, wd)
    cap, follow = build_caption(lang, info)
    data = {"chat_id": chat_id, "caption": cap, "parse_mode": "HTML",
            "title": info["title"][:64], "performer": (info.get("uploader") or "")[:64]}
    if info.get("duration"): data["duration"] = info["duration"]
    if info.get("via") == "music" and info.get("lyr_tok"):
        data["reply_markup"] = kb([[btn(tr(lang, "mid_b_lyrics"), "r:%s:l" % info["lyr_tok"])]])
    thumb = _thumb_from_url(info.get("thumb"), wd) if info["platform"] in ("soundcloud", "spotify") else None
    if thumb:
        with open(path, "rb") as fa, open(thumb, "rb") as ft:
            data["thumbnail"] = "attach://thumbnail"
            try:
                C.call("sendAudio", data, files={"audio": (os.path.basename(path), fa), "thumbnail": ("thumb.jpg", ft)}, timeout=900)
            except C.ApiError:
                data.pop("thumbnail", None); fa.seek(0)
                C.call("sendAudio", data, files={"audio": (os.path.basename(path), fa)}, timeout=900)
    else:
        _upload("sendAudio", "audio", path, data)
    send_followups(chat_id, follow)
    if note:
        C.send(chat_id, note)
    if info.get("via") == "music":
        try:
            import poster
            poster.record_track(info["title"], info.get("uploader") or "")
        except Exception:
            pass
    return True

def send_audio(chat_id, uid, lang, mid, info, fmt_name, wd, prog):
    if not deliver_audio(chat_id, uid, lang, mid, info, fmt_name, wd, prog):
        return False
    finish(chat_id, mid, lang)
    return True

def send_collection(chat_id, uid, lang, mid, info, fmt_name, wd, prog):
    """SoundCloud set / Spotify album or playlist: up to N tracks, one after another. Each delivered track spends one
    download on the platform (its free daily quota first, then credits); stops when the quota runs out."""
    plat, items = info["platform"], info["items"]
    done = skipped = 0
    stopped = False
    for n, it in enumerate(items, 1):
        if not logic.access_info(uid, plat)["can"]:
            stopped = True; break
        C.edit_text(chat_id, mid, tr(lang, "coll_progress", i=n, n=len(items)))
        try:
            ai = engine.resolve_item(info, it)
            if deliver_audio(chat_id, uid, lang, mid, ai, fmt_name, wd, prog):
                logic.consume(uid, plat); done += 1
            else:
                skipped += 1
        except EngineError as e:
            logic.record_error(uid, plat, e.code, getattr(e, "detail", ""), info.get("url", "")); skipped += 1
            log.info("collection item %d failed: %s", n, e.code)
        except C.ApiError as e:
            logic.record_error(uid, plat, "upload", str(e), info.get("url", "")); skipped += 1
        finally:
            engine._clear_media(wd)
    tail = tr(lang, "coll_done", done=done, skip_note=(tr(lang, "coll_skip_note", k=skipped) if skipped else ""))
    if done:
        ad = logic.ad_due(uid)
        if ad: ui.send_ad(chat_id, ad, lang)
    C.show(chat_id, mid, tail, kb([menu_row(lang)]))
    if stopped:
        ui.show_upgrade(chat_id, uid, lang, plat)
    return done > 0

def _prep_photo(path, wd):
    """Telegram photo limits: <=10 MB, w+h<=10000. Returns (path, ok_as_photo)."""
    try:
        from PIL import Image
        size = os.path.getsize(path)
        with Image.open(path) as im:
            w, h = im.size
            if size <= 10 * 1024 * 1024 and w + h <= 10000 and max(w, h) / max(1, min(w, h)) <= 20:
                return path, True
            if max(w, h) / max(1, min(w, h)) > 20:
                return path, False
            im = im.convert("RGB")
            im.thumbnail((4000, 4000))
            out = os.path.join(wd, "r_" + os.path.basename(path) + ".jpg")
            im.save(out, "JPEG", quality=88)
            return (out, True) if os.path.getsize(out) <= 10 * 1024 * 1024 else (path, False)
    except Exception:
        return path, True

def send_gallery(chat_id, uid, lang, mid, info, wd, prog, only=None, as_files=False):
    items = info["items"]
    idxs = [only] if only is not None and 0 <= only < len(items) else list(range(len(items)))
    paths = []
    silent = False
    for n, i in enumerate(idxs, 1):
        it = items[i]
        dst = os.path.join(wd, "%03d.%s" % (n, it["ext"]))
        if it.get("direct"):                       # Threads / X: plain file URLs from the page / public API
            engine.download_direct(it["url"], dst)
            paths.append((dst, it["type"]))
        elif it["type"] == "video":
            # Instagram serves video and audio as separate DASH streams; gallery-dl+yt-dlp merge them with ffmpeg
            # and the result is verified with ffprobe (falls back to the pre-merged file).
            src, au = engine.download_gallery_video(info, i, wd)
            final = os.path.join(wd, "%03d.mp4" % n)
            os.replace(src, final)
            silent = silent or (au is False)
            paths.append((final, "video"))
        else:
            engine.download_direct(it["url"], dst)
            paths.append((dst, it["type"]))
        prog(100.0 * n / len(idxs))
    prog.stage("uploading")
    cap, follow = build_caption(lang, info)
    sent = skipped = 0
    media = []   # (path, kind)
    for p, typ in paths:
        if typ == "video":
            if os.path.getsize(p) > LIMIT:
                skipped += 1; continue
            media.append((p, "video" if not as_files else "document"))
        elif as_files:
            media.append((p, "document"))
        else:
            pp, okp = _prep_photo(p, wd)
            media.append((pp, "photo" if okp else "document"))
    if not media:
        raise EngineError("toolong", "nothing fits")
    # group by compatibility: documents only with documents; photos+videos together
    groups = []
    for kind in ("photo+video", "document"):
        part = [m for m in media if (m[1] == "document") == (kind == "document")]
        for i in range(0, len(part), 10):
            groups.append(part[i:i + 10])
    first = True
    for g in groups:
        if len(g) == 1:
            p, k = g[0]
            meth = {"photo": "sendPhoto", "video": "sendVideo", "document": "sendDocument"}[k]
            d = {"chat_id": chat_id, "parse_mode": "HTML"}
            if first: d["caption"] = cap
            try:
                _upload(meth, k, p, d)
            except C.ApiError:
                if k != "document":
                    _upload("sendDocument", "document", p, d)
                else:
                    raise
        else:
            arr, files = [], {}
            for i, (p, k) in enumerate(g):
                name = "f%d" % i
                e = {"type": k, "media": "attach://" + name}
                if first and i == 0:
                    e["caption"] = cap; e["parse_mode"] = "HTML"
                if k == "video": e["supports_streaming"] = True
                arr.append(e); files[name] = (os.path.basename(p), open(p, "rb"))
            try:
                C.call("sendMediaGroup", {"chat_id": chat_id, "media": json.dumps(arr)}, files=files, timeout=900)
            finally:
                for fh in files.values(): fh[1].close()
        sent += len(g); first = False
    send_followups(chat_id, follow)
    if silent:
        C.send(chat_id, tr(lang, "no_audio_warn"))
    C.send(chat_id, tr(lang, "sent_n", n=sent) + ("" if not skipped else f"\n⚠️ {skipped}× >50MB"))
    finish(chat_id, mid, lang)
    return True

def send_profile(chat_id, uid, lang, mid, info, wd, prog):
    path, hires = engine.profile_picture(info["user"], wd)
    prog.stage("uploading")
    cap = "📸 @%s\n🤖 @%s" % (info["user"], C.BOT_USERNAME)
    pp, okp = _prep_photo(path, wd)
    if okp:
        try:
            _upload("sendPhoto", "photo", pp, {"chat_id": chat_id, "caption": cap})
        except C.ApiError:
            okp = False
    if hires or not okp:
        _upload("sendDocument", "document", path, {"chat_id": chat_id, "caption": cap if not okp else ""})
    if not hires:
        C.send(chat_id, tr(lang, "profile_lowres"))
    finish(chat_id, mid, lang)
    return True

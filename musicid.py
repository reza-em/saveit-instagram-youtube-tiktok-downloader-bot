"""Music recognition flow: voice/audio/video -> Shazam-like ID -> result card -> download via the Spotify-style YouTube match.

Quota decision (documented in README):
  * recognition uses its own platform key 'music_id' (free & unlimited by default; the admin can limit/close it in
    '⚙️ Free limits per platform'). It is charged only when a song was actually recognised, and never spends credits.
  * pressing ⬇️ Download is a normal audio download charged ONCE on the 'spotify' key (same pipeline: YouTube match).
"""
import os, re, logging, json
import core as C
import engine, logic, jobs, recognize, music
from core import tr, esc, btn, kb
from engine import EngineError

log = logging.getLogger("musicid")
MAX_CANDS = 5

def media_of(msg):
    """-> (kind, file_id, file_size) for a message carrying recognisable audio, else None."""
    for k in ("voice", "audio", "video_note", "video", "animation"):
        m = msg.get(k)
        if m and m.get("file_id"):
            return k, m["file_id"], int(m.get("file_size") or 0)
    d = msg.get("document")
    if d and d.get("file_id"):
        mime = (d.get("mime_type") or "").lower(); name = (d.get("file_name") or "").lower()
        if mime.startswith(("audio/", "video/")) or name.endswith((".mp3", ".m4a", ".ogg", ".oga", ".opus", ".wav", ".flac", ".aac", ".mp4", ".mov", ".mkv", ".webm", ".amr")):
            return "document", d["file_id"], int(d.get("file_size") or 0)
    return None

def _fetch(file_id, workdir):
    fi = C.call("getFile", {"file_id": file_id})
    if int(fi.get("file_size") or 0) > recognize.MAX_INPUT_BYTES:
        raise recognize.RecognizeError("toobig")
    r = C.sess.get(C.FILE_API + fi["file_path"], timeout=120)
    if r.status_code != 200 or len(r.content) > recognize.MAX_INPUT_BYTES:
        raise recognize.RecognizeError("toobig" if len(r.content) > recognize.MAX_INPUT_BYTES else "convert", "HTTP %s" % r.status_code)
    path = os.path.join(workdir, "in_" + os.path.basename(fi["file_path"]).replace("/", "_"))
    with open(path, "wb") as f:
        f.write(r.content)
    return path

# ------------------------------------------------------------------ entry points
def handle_media(chat_id, uid, lang, msg):
    """Called for a message with voice/audio/video. Returns True if handled."""
    mt = media_of(msg)
    if not mt:
        return False
    kind, fid, size = mt
    if size and size > recognize.MAX_INPUT_BYTES:
        C.send(chat_id, tr(lang, "mid_toobig"), kb([jobs.menu_row(lang)])); return True
    logic.record_link()
    if not jobs.quota_ok(chat_id, uid, lang, "music_id"):
        return True
    m = C.send(chat_id, tr(lang, "mid_listening"))
    mid = (m or {}).get("message_id")
    def job():
        wd = engine.new_workdir()
        try:
            src = _fetch(fid, wd)
            cands = recognize.recognize_media(src, wd, MAX_CANDS)
        except recognize.RecognizeError as e:
            logic.record_error(uid, "music_id", e.code, e.detail)
            key = {"toobig": "mid_toobig", "noaudio": "mid_noaudio", "backend": "mid_backend"}.get(e.code, "mid_convert")
            C.show(chat_id, mid, tr(lang, key), kb([jobs.menu_row(lang)])); return
        except Exception as e:
            return jobs.fail(chat_id, lang, uid, "music_id", e, mid)
        finally:
            engine.cleanup(wd)
        if not cands:
            logic.record_error(uid, "music_id", "nomatch", "")
            C.show(chat_id, mid, tr(lang, "mid_nomatch"), kb([jobs.menu_row(lang)])); return
        logic.consume(uid, "music_id")
        show_result(chat_id, uid, lang, mid, cands)
    r = jobs.submit(uid, job)
    if r == "full":
        C.show(chat_id, mid, tr(lang, "busy_limit"), kb([jobs.menu_row(lang)]))
    elif r == "queued":
        C.edit_text(chat_id, mid, tr(lang, "queued"))
    return True

def _tok_for(uid, chat_id, cands, q=None):
    return jobs.put_token(uid, chat_id, {"kind": "recog", "platform": "music_id", "cands": cands, "q": q})

def card_text(lang, c, header="mid_found"):
    meta = " · ".join(x for x in (c.get("album"), c.get("year"), c.get("genre")) if x)
    return tr(lang, header, title=esc(c["title"]), artist=esc(c["artist"]), meta=esc(meta)) if meta else \
        tr(lang, header + "_nometa", title=esc(c["title"]), artist=esc(c["artist"]))

def show_result(chat_id, uid, lang, mid, cands, header="mid_found"):
    """Recognition card for cands[0] (the recognised song). 🔎 other results = a music search for 'artist title'."""
    tok = _tok_for(uid, chat_id, cands[:1])
    c = cands[0]
    rows = [[btn(tr(lang, "mid_b_dl"), f"r:{tok}:d0"), btn(tr(lang, "mid_b_others"), f"r:{tok}:o")],
            [btn(tr(lang, "mid_b_lyrics"), f"r:{tok}:l"), btn(tr(lang, "mid_b_x"), f"r:{tok}:x")]]
    txt = card_text(lang, c, header)
    C.delete_msg(chat_id, mid)
    if c.get("cover"):
        try:
            C.call("sendPhoto", {"chat_id": chat_id, "photo": c["cover"], "caption": txt, "parse_mode": "HTML", "reply_markup": kb(rows)})
            return
        except C.ApiError:
            pass
    C.send(chat_id, txt, kb(rows))

def result_lines(hits):
    return "\n".join("%d. %s — %s%s · %s" % (i + 1, esc(music.display_title(h)), esc(h.get("artist") or h.get("uploader") or "—"),
                                          (" · " + music.fmt_dur(h.get("duration"))) if h.get("duration") else "",
                                          music.SHORT.get(h["source"], h["source"])) for i, h in enumerate(hits))

def show_hits(chat_id, uid, lang, mid, hits, q):
    """Search results as inline buttons (title, artist, duration, source) -> tap to download."""
    tok = _tok_for(uid, chat_id, hits, q)
    rows = []
    for i, h in enumerate(hits):
        lbl = "%d. %s — %s" % (i + 1, music.display_title(h), h.get("artist") or h.get("uploader") or "")
        d = music.fmt_dur(h.get("duration"))
        lbl = (lbl[:44] + (" · " + d if d else "") + " · " + music.SHORT.get(h["source"], ""))[:64]
        rows.append([btn(lbl, f"r:{tok}:d{i}")])
    rows.append([btn(tr(lang, "mid_b_x"), f"r:{tok}:x")])
    txt = tr(lang, "mid_search_res", q=esc(q)) + "\n\n" + result_lines(hits) + "\n\n" + tr(lang, "mid_search_note")
    if mid:
        C.show(chat_id, mid, txt, kb(rows))
    else:
        C.send(chat_id, txt, kb(rows))

def run_search(chat_id, uid, lang, q, charge=True):
    """Music search (Radio Javan / SoundCloud / Audius / Bandcamp, YouTube Music fallback). Charged on 'music_id' only when it finds something."""
    if not jobs.quota_ok(chat_id, uid, lang, "music_id"):
        return
    m = C.send(chat_id, tr(lang, "mid_searching", q=esc(q)))
    smid = (m or {}).get("message_id")
    def job():
        try:
            hits = music.search(q, MAX_CANDS)
        except Exception as e:
            return jobs.fail(chat_id, lang, uid, "music_id", e, smid)
        if not hits:
            C.show(chat_id, smid, tr(lang, "mid_search_none"), kb([jobs.menu_row(lang)])); return
        if charge:
            logic.consume(uid, "music_id")
        show_hits(chat_id, uid, lang, smid, hits, q)
    r = jobs.submit(uid, job)
    if r == "full":
        C.show(chat_id, smid, tr(lang, "busy_limit"), kb([jobs.menu_row(lang)]))
    elif r == "queued":
        C.edit_text(chat_id, smid, tr(lang, "queued"))

def start_download(chat_id, uid, lang, cand):
    """Recognised song (metadata) -> provider chain; search hit -> that exact source. The download is charged ONCE on the 'spotify' key."""
    if not jobs.quota_ok(chat_id, uid, lang, "spotify"):
        return
    m = C.send(chat_id, tr(lang, "mid_matching"))
    mid = (m or {}).get("message_id")
    def job():
        try:
            info = music.info_for_cand(cand) if cand.get("source") in music.SEARCHERS else music.audio_info(recognize.to_meta(cand))
        except EngineError as e:
            return jobs.fail(chat_id, lang, uid, "spotify", e, mid, "")
        except Exception as e:
            return jobs.fail(chat_id, lang, uid, "spotify", e, mid, "")
        jobs.run_download(chat_id, uid, lang, mid, info, "a3")
    r = jobs.submit(uid, job)
    if r == "full":
        C.show(chat_id, mid, tr(lang, "busy_limit"), kb([jobs.menu_row(lang)]))
    elif r == "queued":
        C.edit_text(chat_id, mid, tr(lang, "queued"))

LYR_MAX_MSGS = 3

def send_lyrics(chat_id, uid, lang, c):
    """📝 Lyrics: shazam text / Radio Javan / lrclib.net / lyrics.ovh; ≤4096-char messages, a .txt when very long; source attributed."""
    import lyrics
    m = C.send(chat_id, tr(lang, "lyr_searching"))
    smid = (m or {}).get("message_id")
    def job():
        try:
            text, src = lyrics.find(c["title"], c.get("artist") or "", c.get("duration") or 0, c.get("rj_id"), c.get("lyrics") or "")
        except Exception:
            text, src = None, None
        if not text:
            C.show(chat_id, smid, tr(lang, "lyr_none"), kb([[btn(tr(lang, "b_music"), "m:music")], jobs.menu_row(lang)])); return
        head = tr(lang, "lyr_head", title=esc(c["title"]), artist=esc(c.get("artist") or "—"))
        foot = tr(lang, "lyr_src", src=lyrics.LABELS.get(src, src))
        chunks = lyrics.split(text, 3700)
        C.delete_msg(chat_id, smid)
        if len(chunks) > LYR_MAX_MSGS:
            import tempfile
            fn = re.sub(r"[^\w\- ]+", "", "%s - %s" % (c.get("artist") or "", c["title"]), flags=re.U).strip()[:60] or "lyrics"
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, fn + ".txt")
                with open(p, "w", encoding="utf-8") as f:
                    f.write("%s - %s\n\n%s\n\n(%s)\n" % (c.get("artist") or "", c["title"], text, "source: " + lyrics.LABELS.get(src, src)))
                with open(p, "rb") as f:
                    C.call("sendDocument", {"chat_id": chat_id, "caption": (head + "\n\n" + foot)[:1024], "parse_mode": "HTML"}, files={"document": (fn + ".txt", f)}, timeout=120)
            return
        for i, ch in enumerate(chunks):
            body = esc(ch)
            if i == 0: body = head + "\n\n" + body
            if i == len(chunks) - 1: body += "\n\n" + foot
            C.send(chat_id, body)
    r = jobs.submit(uid, job)
    if r != "started":
        C.edit_text(chat_id, smid, tr(lang, "queued"))

def handle_callback(chat_id, uid, lang, mid, data):
    """data = 'r:<tok>:<act>' ; act: d<i> download candidate i, o other results (music search), x dismiss."""
    parts = data.split(":")
    if len(parts) < 3:
        return
    tok, act = parts[1], parts[2]
    t = jobs.get_token(tok)
    if not t:
        C.send(chat_id, tr(lang, "expired"), kb([jobs.menu_row(lang)])); return
    info = t["info"]
    if t["uid"] != uid:
        C.send(chat_id, tr(lang, "not_yours")); return
    cands = info.get("cands") or []
    if act == "x":
        C.delete_msg(chat_id, mid); return
    if act == "l" and cands:
        send_lyrics(chat_id, uid, lang, cands[0]); return
    if act == "o" and cands:
        c = cands[0]
        run_search(chat_id, uid, lang, ("%s %s" % (c.get("artist") or "", c["title"])).strip(), charge=False); return
    if act.startswith("d") and act[1:].isdigit() and int(act[1:]) < len(cands):
        start_download(chat_id, uid, lang, cands[int(act[1:])])

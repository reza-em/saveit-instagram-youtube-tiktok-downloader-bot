"""Forced-join channels: membership check (cached, fail-open on lost access) + the 'please join' screen."""
import time, threading, logging
import core as C
import store, logic
from core import tr, btn, kb

log = logging.getLogger("gate")
CACHE_TTL = 60          # positive results cached this long
NEG_TTL = 5             # negative results cached briefly (avoid hammering while user taps)
ALERT_EVERY = 6 * 3600
_cache = {}             # (uid, chat_id) -> (ok, ts)
_alerted = {}           # chat_id -> ts
_lk = threading.Lock()
OK_STATUS = ("member", "administrator", "creator")

def clear_cache(uid=None):
    with _lk:
        for k in [k for k in _cache if uid is None or k[0] == uid]:
            _cache.pop(k, None)

def _alert_lost(ch, why):
    now = time.time()
    with _lk:
        if now - _alerted.get(ch["chat_id"], 0) < ALERT_EVERY:
            return
        _alerted[ch["chat_id"]] = now
    log.warning("lost access to channel %s (%s)", ch["chat_id"], why[:80])
    for aid in [store.admin_id()] + logic.list_admins():
        if aid:
            C.send(aid, tr(C.user_lang(aid), "ch_lost", title=C.esc(ch["title"])), html=True)

def is_member(uid, ch, use_cache=True):
    """True/False; on any API trouble for this channel returns True (fail open) and alerts the owner."""
    key = (int(uid), int(ch["chat_id"]))
    now = time.time()
    if use_cache:
        with _lk:
            hit = _cache.get(key)
        if hit and now - hit[1] < (CACHE_TTL if hit[0] else NEG_TTL):
            return hit[0]
    try:
        m = C.call("getChatMember", {"chat_id": ch["chat_id"], "user_id": int(uid)}, timeout=20)
    except C.ApiError as e:
        s = str(e).lower()
        if s.startswith("network"):
            return True                      # transient: fail open, no alert
        if "user not found" in s or "participant_id_invalid" in s:
            ok = False
        elif "too many requests" in s:
            return True
        else:
            _alert_lost(ch, s)               # chat not found / bot not admin / kicked ...
            return True
    else:
        st = m.get("status")
        ok = st in OK_STATUS or (st == "restricted" and m.get("is_member"))
    with _lk:
        _cache[key] = (ok, now)
    return ok

def missing(uid, use_cache=True):
    """Channels the user still has to join ([] = all good). Admins bypass."""
    if logic.is_admin(uid):
        return []
    return [ch for ch in logic.channels() if not is_member(uid, ch, use_cache)]

def gate_markup(lang, chans):
    rows = [[btn(tr(lang, "ch_open", title=ch["title"])[:60], url=ch["url"])] for ch in chans]
    rows.append([btn(tr(lang, "b_joined"), "g:check")])
    return kb(rows)

def show_gate(chat_id, lang, chans, msg_id=None):
    C.show(chat_id, msg_id, tr(lang, "gate_text"), gate_markup(lang, chans))

def check_or_gate(chat_id, uid, lang):
    """True if the user may proceed; otherwise sends the gate screen and returns False."""
    if not logic.channels():
        return True
    chans = missing(uid)
    if not chans:
        return True
    show_gate(chat_id, lang, chans)
    return False

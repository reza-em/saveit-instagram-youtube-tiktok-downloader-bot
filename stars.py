"""Telegram Stars support (Telegram only; currency XTR, no provider token). Deep link `?start=sup` (button under channel posts) -> amount
picker -> sendInvoice -> pre_checkout_query (answered ok) -> successful_payment (thank-you + logged).  Amounts are editable in admin.
Log: state["supports"] = [{uid, stars, ts, charge}] (charge id kept so the owner can refund via refundStarPayment).  No digital goods are sold:
this is a voluntary tip / support, which is what the message says."""
import time, logging
import core as C, store, logic
from core import tr, esc, btn, kb, send, set_await, parse_int
from store import transaction

log = logging.getLogger("stars")
DEFAULT_AMOUNTS = [10, 25, 50, 100]
MAX_LOG = 5000

def enabled(): return not C.IS_BALE

def amounts():
    a = store.settings().get("star_amounts")
    return [int(x) for x in a] if a else list(DEFAULT_AMOUNTS)

def set_amounts(lst):
    lst = sorted({int(x) for x in lst if 1 <= int(x) <= 100000})[:6]
    with transaction() as st: st["settings"]["star_amounts"] = lst or list(DEFAULT_AMOUNTS)
    return amounts()

def parse_amounts(text):
    import re
    nums = [int(x) for x in re.findall(r"\d+", (text or "").translate(store._DIG))]
    return [n for n in nums if 1 <= n <= 100000][:6]

def picker(chat_id, uid, lang):
    if not enabled(): return
    rows = [[btn("⭐ %s" % a, f"sp:{a}") for a in amounts()[:3]], [btn("⭐ %s" % a, f"sp:{a}") for a in amounts()[3:6]]]
    rows = [r for r in rows if r] + [[btn(tr(lang, "b_menu"), "m:menu")]]
    send(chat_id, tr(lang, "sp_pick"), kb(rows))

def invoice(chat_id, uid, lang, n):
    if n not in amounts(): return
    try:
        C.call("sendInvoice", {"chat_id": chat_id, "title": tr(lang, "sp_title")[:32], "description": tr(lang, "sp_desc")[:255],
                               "payload": "support:%d:%d" % (uid, n), "provider_token": "", "currency": "XTR",
                               "prices": '[{"label": "%s", "amount": %d}]' % (tr(lang, "sp_label")[:30].replace('"', ""), n)})
    except C.ApiError as e:
        log.warning("sendInvoice failed: %s", C.safe(e)[:100]); send(chat_id, tr(lang, "sp_fail"))

def pre_checkout(q):
    """Always approve support payments for a valid payload/amount (must answer within 10 s)."""
    p = (q.get("invoice_payload") or "").split(":")
    ok = len(p) == 3 and p[0] == "support" and q.get("currency") == "XTR" and int(q.get("total_amount", 0)) in amounts()
    try:
        C.call("answerPreCheckoutQuery", {"pre_checkout_query_id": q["id"], "ok": bool(ok), **({} if ok else {"error_message": "Invalid amount"})}, timeout=10)
    except C.ApiError as e:
        log.warning("answerPreCheckoutQuery: %s", C.safe(e)[:80])

def paid(msg):
    sp = msg["successful_payment"]; uid = msg["from"]["id"]; lang = C.user_lang(uid, msg["from"])
    n = int(sp.get("total_amount", 0)); ch = sp.get("telegram_payment_charge_id", "")
    with transaction() as st:
        lg = st.setdefault("supports", [])
        if not any(x.get("charge") == ch for x in lg):           # idempotent (Telegram may redeliver)
            lg.append({"uid": uid, "stars": n, "ts": int(time.time()), "charge": ch}); del lg[:-MAX_LOG]
    send(msg["chat"]["id"], tr(lang, "sp_thanks", n=logic.fa_digits(n) if lang == "fa" else n), kb([[btn(tr(lang, "b_menu"), "m:menu")]]))
    log.info("support: %s Stars from %s", n, uid)

def summary():
    lg = store.snapshot().get("supports", [])
    t = int(time.time())
    return {"count": len(lg), "stars": sum(x["stars"] for x in lg), "donors": len({x["uid"] for x in lg}),
            "stars_30d": sum(x["stars"] for x in lg if t - x["ts"] < 30 * 86400)}

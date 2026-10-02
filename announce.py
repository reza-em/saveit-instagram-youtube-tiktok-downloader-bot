"""One-off announcement (once per user, ledger in state.json['announce'][key]); throttled, skips banned/blocked. Usage: python announce.py KEY [--dry]"""
import sys, time, re
import store, core as C, logic
from core import kb, btn
KEY = sys.argv[1]; DRY = "--dry" in sys.argv
TEXT = ("🎤 <b>کانال رپ ریتم‌باکس راه افتاد!</b>\n\n"
        "🔥 تازه‌ترین و بهترین‌های رپ ایرانی و خارجی، ترک‌های وایرال (ویروسی اینستاگرام) و دیس و دیس‌بک‌ها — هر روز با کاور و اطلاعات کامل.\n\n"
        "👇 از دکمه‌ی زیر وارد کانال شو و بعد «✅ عضو شدم» رو بزن تا به استفاده از ربات ادامه بدی.\n\n"
        "🎁 هدیه: کد <code>WELCOME7</code> رو وارد کن و ۷ روز دسترسی نامحدود رایگان بگیر.\n"
        "📣 @rythmbox_rap")
MARKUP = kb([[btn("🎤 ورود به کانال ریتم‌باکس", url="https://t.me/rythmbox_rap")],
             [btn("✅ عضو شدم", "g:check")], [btn("🎟 وارد کردن کد WELCOME7", "m:code")]])
users = [int(k) for k, u in store.snapshot()["users"].items() if not u.get("banned") and not u.get("blocked")]
done = set(store.snapshot().get("announce", {}).get(KEY, []))
ok = blocked = failed = skipped = 0
for uid in users:
    if uid in done: skipped += 1; continue
    if DRY: print("would send", uid); continue
    for attempt in range(2):
        try:
            C.call("sendMessage", {"chat_id": uid, "text": TEXT, "parse_mode": "HTML", "disable_web_page_preview": True, "reply_markup": MARKUP}); ok += 1
            with store.transaction() as st: st.setdefault("announce", {}).setdefault(KEY, []).append(uid)
            break
        except C.ApiError as e:
            s = str(e).lower(); m = re.search(r"retry after (\d+)", s)
            if m and attempt == 0: time.sleep(min(int(m.group(1)) + 1, 60)); continue
            if any(x in s for x in ("blocked", "deactivated", "chat not found", "forbidden")):
                blocked += 1; store.update_user(uid, blocked=True)
            else: failed += 1; print("fail", uid, C.safe(e)[:80])
            break
    time.sleep(0.05)
print("users", len(users), "sent", ok, "blocked", blocked, "failed", failed, "already-sent", skipped)

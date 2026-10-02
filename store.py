"""Persistent state: one JSON file (state.json), atomic writes, inter-process file lock."""
import os, json, time, fcntl, threading, copy, logging
from contextlib import contextmanager

log = logging.getLogger("store")
BASE = os.path.dirname(os.path.abspath(__file__))
import plat as _plat
PATH = _plat.PLAT.state_path

DEFAULT_SETTINGS = {
    "free_quota": 5,          # free downloads per user per day
    "ref_enabled": True,
    "ref_bonus": 5,           # downloads the inviter gets (per invited friend)
    "ref_invitee_bonus": 3,   # downloads the invited friend gets
    "ref_max_total": 0,       # cap on the total reward one inviter can earn from invites (0 = unlimited)
    "ads_enabled": True,
    "ad_every": 3,            # show an ad after every N downloads (non-premium users)
    "plat_limits": {},        # per-platform free daily limit overrides: {platform: N}; -1 = unlimited, 0 = blocked; missing = global default
    "max_tracks": 10,         # max tracks taken from a SoundCloud set / Spotify album or playlist
    "duration_discounts": {"1": 0, "3": 10, "6": 20},   # % off the monthly price when buying N months (display only)
    "price_unit": "",         # currency label shown after prices ("" = default: تومان / Toman)
}
DEFAULT_PLAN_TITLES = ("پایه / Basic", "اقتصادی / Economy", "پیشرفته / Advanced")
DEFAULT_PLAN_DAILY = (15, 40, 100)        # downloads per day; -1 = unlimited
DEFAULT_PLAN_POPULAR = (False, True, False)

_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_tl = threading.RLock()
_cur = None

def set_path(p):
    global PATH
    PATH = p

def _blank():
    return {"_v": 1, "admin_id": None, "admins": [], "settings": copy.deepcopy(DEFAULT_SETTINGS), "users": {},
            "support": {"primary": "example_owner", "backup": ""},
            "price_plans": [{"id": i + 1, "title": t, "daily": DEFAULT_PLAN_DAILY[i], "price": 0, "features": "",
                             "popular": DEFAULT_PLAN_POPULAR[i]} for i, t in enumerate(DEFAULT_PLAN_TITLES)],
            "next_pplan_id": 4,
            "ads": [], "next_ad_id": 1,
            "channels": [], "next_ch_id": 1,
            "stats": {"downloads": {}, "errors": 0, "links": 0}, "supports": [],
            "errors": [],
            "pending_broadcast": None,
            "codes": [], "next_code_id": 1, "campaigns": [], "next_campaign_id": 1,
            "poster": {"channels": [], "next_ch": 1, "jobs": [], "next_job": 1, "feeds": [], "next_feed": 1, "posted": {},
                       "footer": {}, "checked": 0},
            "logo": {}, "profile_sha": "", "cookies": {}}

def _normalize(st):
    b = _blank()
    for k, v in b.items():
        if k not in st:
            st[k] = v
    for k, v in DEFAULT_SETTINGS.items():
        st["settings"].setdefault(k, copy.deepcopy(v))
    st["support"].setdefault("primary", "example_owner")
    st["support"].setdefault("backup", "")
    dd = st["settings"].get("duration_discounts")
    if not isinstance(dd, dict):
        dd = st["settings"]["duration_discounts"] = {}
    for k, v in DEFAULT_SETTINGS["duration_discounts"].items():
        dd.setdefault(k, v)
    _migrate_plans(st)
    if not st["settings"].get("ref_defaults_v2"):       # one-time: untouched old defaults (3 / 1) -> the new higher defaults (5 / 3)
        if st["settings"].get("ref_bonus") == 3 and st["settings"].get("ref_invitee_bonus") == 1:
            st["settings"]["ref_bonus"], st["settings"]["ref_invitee_bonus"] = 5, 3
        st["settings"]["ref_defaults_v2"] = True
    return st

def _migrate_plans(st):
    """Old plans had a one-off credit 'count'; plans are now 'downloads per day' (+ a duration chosen at grant time).
    Idempotent: only plans without a 'daily' key are touched. Unset plans get the default daily numbers."""
    pl = st.get("price_plans") or []
    fresh = [p for p in pl if "daily" not in p]
    for p in fresh:
        cnt = p.pop("count", None)
        p.pop("duration", None)
        idx = p.get("id", 0) - 1
        if cnt:
            p["daily"] = int(cnt)
        elif 0 <= idx < len(DEFAULT_PLAN_TITLES) and p.get("title") == DEFAULT_PLAN_TITLES[idx]:
            p["daily"] = DEFAULT_PLAN_DAILY[idx]
            if DEFAULT_PLAN_POPULAR[idx] and not any(q.get("popular") for q in pl):
                p["popular"] = True
        else:
            p["daily"] = None
        p.setdefault("features", "")
    for p in pl:      # prices are integer amounts per month (0 = not set); old free-text prices are converted if they were plain numbers
        pr = p.get("price")
        if not isinstance(pr, int) or isinstance(pr, bool):
            digits = "".join(ch for ch in str(pr or "").translate(_DIG) if ch.isdigit())
            p["price"] = int(digits) if digits and len(digits) <= 9 and str(pr).translate(_DIG).replace(",", "").replace("٬", "").strip().isdigit() else 0

def _load():
    try:
        with open(PATH) as f:
            raw = json.load(f)
    except FileNotFoundError:
        return _blank()
    except Exception as e:
        bak = f"{PATH}.corrupt-{int(time.time())}"
        try:
            os.replace(PATH, bak)
        except OSError:
            pass
        log.error("state file unreadable (%s); moved to %s", type(e).__name__, bak)
        return _blank()
    if not isinstance(raw, dict):
        return _blank()
    return _normalize(raw)

def _save(st):
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, PATH)

@contextmanager
def transaction(write=True):
    """Exclusive read-modify-write. Nested use reuses the outer state. Never do network I/O inside."""
    global _cur
    with _tl:
        if _cur is not None:
            yield _cur
            return
        fd = open(PATH + ".lock", "a+")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            st = _load()
            _cur = st
            try:
                yield st
                if write:
                    _save(st)
            finally:
                _cur = None
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                fd.close()

def snapshot():
    with transaction(write=False) as st:
        return copy.deepcopy(st)

def get_user(uid):
    with transaction(write=False) as st:
        return copy.deepcopy(st["users"].get(str(uid), {}))

def update_user(uid, **kw):
    """Set keys (value None deletes the key). Returns updated user dict."""
    with transaction() as st:
        u = st["users"].setdefault(str(uid), {})
        for k, v in kw.items():
            if v is None:
                u.pop(k, None)
            else:
                u[k] = v
        return copy.deepcopy(u)

def settings():
    with transaction(write=False) as st:
        return dict(st["settings"])

def admin_id():
    with transaction(write=False) as st:
        return st.get("admin_id")

def support():
    with transaction(write=False) as st:
        return dict(st["support"])

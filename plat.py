"""Platform selection (no dependencies): env DL_PLATFORM=telegram|bale (default telegram).
One process per platform; each has its own state file / lock / tmp dir / log so users, plans, codes, settings and the admin
binding are per platform (ids differ). Everything else (engine, music chain, plans, codes, admin UI ...) is shared code."""
import os, sys

BASE = os.path.dirname(os.path.abspath(__file__))

class Plat:
    def __init__(self, name):
        self.name = name
        if name == "bale":
            self.label = "Bale"; self.token_env = "DL_BALE_BOT_TOKEN"
            self.api_base = "https://tapi.bale.ai"
            self.link_host = "ble.ir"
            suffix = "_bale"
            self.styles = False          # no InlineKeyboardButton.style
            self.profile = False         # no setMyName / setMyDescription / setMyCommands (set these in Bale's @botfather)
            self.poster = False          # channel auto-poster stays Telegram-only
            self.claim = True            # owner claims the bot with a one-time code (usernames are not proof of identity)
            self.markdown = True         # no parse_mode: everything is Markdown, we convert our HTML subset
            self.upload_mb = int(os.environ.get("DL_BALE_LIMIT_MB", "49"))
        else:
            self.label = "Telegram"; self.token_env = "DL_TELEGRAM_BOT_TOKEN"
            self.api_base = "https://api.telegram.org"
            self.link_host = "t.me"
            suffix = ""
            self.styles = True; self.profile = True; self.poster = True; self.claim = False; self.markdown = False
            self.upload_mb = 49
        self.token = os.environ.get(self.token_env, "")
        self.state_path = os.path.join(BASE, "state%s.json" % suffix)
        self.lock_name = "bot%s.lock" % suffix
        self.tmp_name = "tmp%s" % suffix
        self.log_name = "bale.log" if name == "bale" else "bot.log"

    @property
    def api(self): return f"{self.api_base}/bot{self.token}/"
    @property
    def file_api(self): return f"{self.api_base}/file/bot{self.token}/"
    def link(self, username): return f"https://{self.link_host}/{username}"

NAME = (os.environ.get("DL_PLATFORM") or "telegram").strip().lower()
if NAME not in ("telegram", "bale"):
    print("unknown DL_PLATFORM=%r (telegram|bale)" % NAME, file=sys.stderr); sys.exit(1)
PLAT = Plat(NAME)
IS_BALE = NAME == "bale"

def all_tokens():
    """Every platform's token (for log redaction no matter which platform runs)."""
    return [t for t in (os.environ.get("DL_TELEGRAM_BOT_TOKEN", ""), os.environ.get("DL_BALE_BOT_TOKEN", "")) if t]

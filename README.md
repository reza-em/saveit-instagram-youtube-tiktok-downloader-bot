# SaveIt — Instagram, YouTube & TikTok Downloader Bot for Telegram and Bale

> Telegram & Bale bot that downloads Instagram reels/stories, YouTube, TikTok (no watermark), Pinterest, LinkedIn, Threads and Twitter/X media. Persian + English, yt-dlp, gallery-dl, Python.

## 🌐 فارسی

ربات **دانلودر اینستاگرام، یوتیوب و تیک‌تاک** برای **تلگرام و بله**: لینک را بفرستید، فایل را بگیرید. دانلود ریلز، استوری، هایلایت، پست و عکس پروفایل اینستاگرام، یوتیوب (شورتس و ویدیو بلند)، تیک‌تاک بدون واترمارک، پینترست، لینکدین، تردز و توییتر (X). رابط فارسی و انگلیسی با دکمه‌های شیشه‌ای.

**کلیدواژه‌ها:** دانلودر اینستاگرام، دانلود از یوتیوب، دانلود تیک‌تاک بدون واترمارک، ربات دانلود تلگرام، ربات دانلود بله، دانلود ریلز، دانلود استوری اینستاگرام، دانلود پینترست، ربات تلگرام پایتون

## 🇬🇧 English

A **Telegram and Bale downloader bot** for **Instagram, YouTube and TikTok**: send a link, get the file. Supports Instagram reels, stories, highlights, posts and profile pictures; YouTube Shorts and long videos; TikTok without watermark; Pinterest, LinkedIn, Threads and Twitter/X. Bilingual (Persian/English) inline-button UI, quotas, owner panel, built on yt-dlp and gallery-dl.

**Keywords:** Instagram downloader bot, YouTube downloader Telegram bot, TikTok no watermark downloader, reels downloader, story downloader, Bale messenger bot, yt-dlp bot, gallery-dl, Python Telegram bot

## 🇷🇺 Русский

**Бот-загрузчик для Telegram и Bale**: скачивает видео и фото из **Instagram (Reels, Stories, посты), YouTube (Shorts), TikTok без водяного знака**, Pinterest, LinkedIn, Threads и Twitter/X. Отправьте ссылку — получите файл. Интерфейс на персидском и английском; основан на yt-dlp и gallery-dl.

**Ключевые слова:** скачать видео из Instagram, бот для скачивания YouTube, TikTok без водяного знака, загрузчик Reels, Telegram-бот на Python, бот Bale

## 🇩🇪 Deutsch

**Downloader-Bot für Telegram und Bale**: Link senden, Datei erhalten. Lädt **Instagram-Reels, Stories und Beiträge, YouTube (Shorts und lange Videos), TikTok ohne Wasserzeichen**, Pinterest, LinkedIn, Threads und Twitter/X herunter. Zweisprachige Oberfläche (Persisch/Englisch), basiert auf yt-dlp und gallery-dl.

**Stichwörter:** Instagram Downloader, YouTube Downloader Bot, TikTok ohne Wasserzeichen herunterladen, Telegram-Bot Python, Reels herunterladen, Bale Messenger Bot

---

## Features

- Instagram: posts, carousels, reels, stories, highlights, profile pictures
- YouTube Shorts and long videos, audio extraction, lyrics/music recognition helpers
- TikTok (no watermark when possible) + audio; Pinterest, LinkedIn, Threads, Twitter/X
- One process per platform (Telegram and Bale) sharing the same logic
- Per-user quotas, owner/admin panel, broadcast, channel poster tools
- Offline test-suite with mocked Telegram API (`test_offline.py`)

## Setup

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
export DL_TELEGRAM_BOT_TOKEN=...      # from @BotFather (Telegram)
export DL_BALE_BOT_TOKEN=...          # optional, from @botfather on Bale
export OWNER_USERNAME=your_telegram_username   # becomes admin on first /start
./run.sh            # Telegram supervisor loop (./run_bale.sh for Bale)
./venv/bin/python test_offline.py
```

> **Configuration note:** the owner/admin identity is read from environment variables (`OWNER_ID`, `OWNER_USERNAME`, `SUPPORT_USERNAME`) with the placeholder `example_owner`. Set them to your own values before running. Never commit bot tokens — they are read only from the environment.

## Usage

Open your bot in the messenger and send `/start`. See the detailed documentation below for commands, admin panel and platform-specific notes.

## License

Code released under the [MIT License](LICENSE).

---

## Detailed documentation

# SaveIt 📥 — Telegram media-downloader bot

Send a link → get the file. YouTube (Shorts + long), Instagram (posts, reels, carousels, stories, highlights, profile pictures),
TikTok (no-watermark when possible + audio), Pinterest (image or video pins), LinkedIn (public posts: video + images). Bilingual fa/en, cute inline-button UI.
Bot: <https://t.me/saveit_downloader_bot> · Owner: @example_owner (auto-bound to his numeric ID on first message).

## Run
```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt   # yt-dlp, gallery-dl, requests, Pillow, imageio-ffmpeg
export DL_TELEGRAM_BOT_TOKEN=...      # never printed; redacted in logs
./run.sh     # detached (setsid nohup) supervisor loop, auto-restart, single instance, logs -> bot.log
./stop.sh    # stop supervisor + bot
./venv/bin/python test_offline.py     # offline tests (mocked Telegram + mocked engine)
./venv/bin/python live_test.py        # live downloads from this machine (honest pass/fail per platform)
```
ffmpeg: system `/usr/bin/ffmpeg` is used; falls back to `imageio-ffmpeg`'s static binary. `node` is used by yt-dlp for YouTube JS challenges.

## Files
`bot.py` polling/routing/profile setup · `engine.py` yt-dlp + gallery-dl wrapper (subprocesses, timeouts, size caps, error classification)
· `jobs.py` per-user queue, quality ladder, upload · `admin.py` panel · `logic.py`/`store.py` state (atomic `state.json`, file lock, chmod 600)
· `gate.py` forced-join · `ui.py` screens/ads/logo · `texts.py` fa/en strings · `assets/logo.jpg` welcome logo (file_id cached in state; changing the file invalidates the cache). · `music.py` provider chain/search · `meta.py` metadata lookup + ID3 tags + hashtags · `lyrics.py` lyrics sources · `theme.py` four-season button themes · `poster.py`/`posterui.py` channel poster · `targeted.py` targeted messaging.

## Features
* Link(s) in a message (max 3) → platform detection → buttons: best ≤50 MB, 1080/720/480/360 (⚠️ if >50 MB), 🎵 MP3 / M4A. Images/carousels → media groups in chunks of 10 (or "as files"), single items pickable.
* **Telegram 50 MB limit**: sizes are estimated from formats; too-big qualities are skipped automatically, and if the real file is still >50 MB it retries lower. If nothing fits: friendly message + audio-only (audio re-encoded to a lower bitrate if needed). No local Bot API server.
* Instagram username / profile link → "📸 Profile picture" (gallery-dl full-size when possible; otherwise the small public og:image, and the user is told why). Stories/highlights need admin cookies; without them users get a polite "admin must enable it" message.
* Progress message (⏳ downloading → 📤 uploading), temp files cleaned, 1 active + 3 queued jobs per user, 2 global concurrent jobs, 15-min download timeout, 400 MB disk cap, 4 h duration cap.
* Friendly errors: private, login required, age-restricted, geo-blocked, rate-limited, IP-blocked, unsupported, not found, timeout, too long.
* **Monetization** → see *Plans, durations & discounts* below (plans = downloads/day, granted for 1/3/6 months or custom days; display-only prices with duration discounts; admins unlimited; credits/unlimited/N-days grants still exist).
* **Ads**: admin-managed (fa/en text, optional photo, optional URL button), enable/disable, every-N-downloads frequency (default 3), view counter and click counter (enable "count clicks" per ad → callback button that counts and replies with the URL; otherwise a plain URL button, views only). Never shown to premium/credit/admin users. Ad broadcast with confirm (non-premium users).
* **Required channels**: add by @username or numeric id (bot must be admin — verified with getChat/getChatMember), title + join URL, unlimited (≤5 recommended). Every feature is gated (commands, links, buttons); "✅ I joined" rechecks. 60 s cache; if the bot loses access to a channel it fails open for that channel and alerts the owner. Admins bypass.
* **Admin** `/admin`: stats (users, downloads per platform, errors), broadcast with confirm, prices, ads, channels, cookies, 🔄 engine update (`pip install -U yt-dlp gallery-dl`), daily-limit setting, ban/unban, recent errors, admins, support contacts, referrals.
* `/help` includes a bilingual notice: users are responsible for respecting content owners' rights.

## 💼 LinkedIn (`linkedin.py`)
Public `linkedin.com/posts/…`, `/feed/update/urn:li:activity:…`, `/video/…` links and `lnkd.in` short links (a short link that points to another site is answered as "unsupported"). No login, no credentials, anonymous GETs only.
Video: yt-dlp's LinkedIn extractor first; if it fails, the `<video data-sources>` mp4 of the public page (highest bitrate) is downloaded directly. Image posts: the post's `feedshare` images (up to 10) are sent as an album. Caption = post text (same caption rules as other platforms) + author. Private / login-only posts (auth wall redirect, 401/403) → friendly "login needed" message; 999/429 → "platform is limiting my server"; 404 → "not found". Own free-limit entry `linkedin` (Admin → Free limits) and own stats line.

## SoundCloud & Spotify
- **SoundCloud**: track links (`soundcloud.com/artist/track`, `on.soundcloud.com/...`) → MP3/M4A with title, artist, cover thumbnail and caption. Sets (`.../sets/...`) → first **N** tracks (`Settings → Max tracks`, default 10, max 50), each sent as its own audio file. Profiles/likes pages are not supported.
- **Spotify** (no credentials, no Spotify audio): Spotify tracks are DRM-protected, so the bot reads the *public* metadata (embed page JSON, oEmbed fallback: title, artists, duration, cover) and downloads the same song from a real **music source** via the provider chain below (no longer YouTube-first). If nothing scores confidently the bot says so instead of sending a random song. The caption shows the source that was used (`🔎 Radio Javan ▸ …`).
- Albums / playlists (first N tracks, same setting). Each delivered track costs one download; it stops when the platform's free quota is used up. `spotify.link` short links, `intl-xx` paths and `?si=` params are handled; artists / podcasts are not supported.

## Free daily quota — per platform
Admin panel → `⚙️ Free limits per platform` (owner only). One button per platform (YouTube, Instagram, TikTok, Pinterest, LinkedIn, SoundCloud, Spotify): **custom number**, **♾ unlimited**, **⛔ close (0)**, or **↩️ use global default**. The global default (`Settings → Daily quota`, initially 5) applies to every platform without its own value. Stored in `state.json` (`settings.plat_limits`; -1 = unlimited, 0 = closed).
Rules:
1. Every user has a separate counter **per platform per day** (resets at local midnight, `user.free_plat`).
2. On a download the platform's **free quota is spent first**, then **credits** (a credit works on *every* platform — also on ones where the free limit is 0 or used up).
3. **Unlimited grants** (forever / N days) and **admins** ignore all limits. Referral/manual grants add credits.
4. A platform set to ♾ is unlimited for everyone and doesn't consume counters.
5. When a link is sent for a platform whose limit is hit, the bot refuses *before* probing and shows: which platform, `used/limit`, the platforms where free downloads remain, plans, the user's numeric ID and the 💬 contact button(s). `0` shows a "closed for free users" message instead. `/me` and `/plans` list the remaining count per platform.
6. Album/playlist downloads check the quota before every track.

## 🎶 Music recognition (Shazam-like)
Send a **voice message, audio file, video / video note or animation** (≤ 20 MB, Telegram's bot download limit; also audio/video sent as a document). The bot converts up to two ~18 s windows to mono 44.1 kHz WAV with ffmpeg, asks the recogniser and shows a card (cover, title, artist, album/year) with **⬇️ Download / 🔎 Other results (up to 4 more) / ❌**. Download uses the provider chain (`music.py`, below) with the recognised title + artist + duration. Not recognised → friendly fa/en tips (10–20 s of clear music, less noise, no humming).
- **Backends** (`recognize.py`): `shazamio` (unofficial Shazam API, **no key**, default). Optional fallback **ACRCloud** only if `ACRCLOUD_HOST`, `ACRCLOUD_ACCESS_KEY`, `ACRCLOUD_ACCESS_SECRET` are set in the environment (has a free tier, needs a key; not required). Extra results, durations and text search use the keyless iTunes Search API (with a YouTube-search fallback).
- **Text search:** see *Music search & provider chain* below (plain text / `/music` / menu button; the card's 🔎 Other results uses the same search).
- **Quota decision:** recognition **and search** share the key **`music_id`** — free & unlimited by default; the admin can limit/close it under `⚙️ Free limits per platform → 🎶 Music recognition`. It is charged once per recognition / search that actually found something ("🔎 Other results" from a card is free), never spends credits, and doesn't count as a download or trigger ads. Pressing ⬇️ Download (card or search result) is a normal audio download charged **once** on the `spotify` key (per-platform free quota → credits, as usual). No double-charging.
- Files are downloaded into a per-job temp dir and removed afterwards. Requires `pip install shazamio` (+ `audioop-lts` on Python ≥ 3.13, both in requirements.txt).
- Limits: humming/singing isn't supported; local/Iranian songs are recognised less reliably; recognition depends on the unofficial Shazam endpoint (if it breaks, users get a "service unavailable" message).

## 📢 Advertising contact line
`/about` and the bot **Description** (both fa/en) end with "📢 For advertising, contact support: @<primary support>" (editable primary support contact, default `example_owner`). The 120-char BotFather **short about** is now a compact pitch + the support IDs: `… 💬 @<primary> @<backup>` (backup only when set under `Admin → Support contacts`; built by `bot.about_short()`, the head text is shortened automatically so the IDs are never cut and the whole text stays ≤120 chars; re-applied when the support contacts change). Example (fa, no backup, 94 chars): `📥 دانلود از یوتیوب، اینستاگرام، تیک‌تاک، ساندکلود و اسپاتیفای + شناسایی آهنگ 🎶 💬 @example_owner`.

## Admin: adding cookies (Instagram stories/highlights/private, YouTube restricted)
1. In a desktop browser logged in (preferably a **secondary** account) export cookies in Netscape format to `cookies.txt` (e.g. "Get cookies.txt LOCALLY" extension). No passwords are ever entered into the bot.
2. In the bot: `/admin` → 🍪 Cookies → 📤 Upload instagram (or youtube) → send the file **as a document**.
3. The bot validates it (Netscape lines for that platform), stores it `chmod 600` in `cookies/<platform>.txt`, never logs content, and deletes your upload message. 🗑 removes it.

## Limitations
* Telegram Bot API: 50 MB per upload for bots (10 MB for photos → larger are resized/sent as documents).
* Datacenter IPs: YouTube / Instagram / TikTok may block or challenge this server's IP; see results below. Cookies help for Instagram/YouTube but can expire or get the account flagged.
* Instagram without cookies: public posts/reels usually demand login from datacenter IPs; profile pictures only as a 100×100 thumbnail.
* yt-dlp breaks when sites change → use 🔄 Update engine (or `pip install -U`).
* Logo/profile picture of the bot itself cannot be set via Bot API (set in @BotFather); name/about/description are set automatically (default, fa, en).


## 🔎 Music search & provider chain (`music.py`)
- **Search UX:** any plain single-line text that isn't a link / @username (2–120 chars), or `/music [name]`, or the menu button `🔎 Search music` → up to 5 results as inline buttons (title — artist · duration · source: RJ = Radio Javan, SC = SoundCloud, Audius, BC = Bandcamp, YTM = YouTube Music) → tap to download that exact result. Longer / multi-line text still gets the "send a link" hint.
- **Chain for `artist - title` (recognised song or Spotify track):** Radio Javan → SoundCloud → Audius → Bandcamp (all queried in parallel; best candidate per source is tried in order of score) → **YouTube Music last** (only if no primary source has a confident match or all their downloads fail; uses yt-dlp, so admin YouTube cookies help with the bot check). If only bot-check failures remain the user gets the "server IP blocked" message rather than a wrong song.
- **Scoring:** title/artist token match, duration within max(15 s, 12 %) of the expected one (30 s previews and >20 min uploads rejected; downloaded files are re-checked), strong penalties for live / cover / remix / karaoke / edit / lyric(s) versions (unless the query itself asks for them), Persian normalisation (ي/ك/ZWNJ/diacritics), small source bonuses. Below `MIN_SCORE` → "no confident match".
- **Source reachability from this box (no keys, no login), tested 2026-09-30:**

| Source | Result |
|---|---|
| Radio Javan (`play.radiojavan.com/api/p/search`, direct 256 kbps MP3) | ✅ works, best for Persian songs (Latin spelling; Persian-script queries rarely hit) |
| SoundCloud (`scsearch`) | ✅ works; many *official* uploads are DRM-flagged and are skipped automatically; user uploads download fine |
| Audius (`api.audius.co`, full stream) | ✅ works, but catalogue is mostly indie/covers |
| Bandcamp (public autocomplete + yt-dlp) | ✅ works, mostly remixes/covers |
| YouTube Music (`ytmusicapi` search + yt-dlp) | search ✅, download often ❌ "Sign in to confirm you're not a bot" on this IP |
| Deezer API / iTunes preview host | ❌ 403 (and previews only) |
| Jamendo | ❌ needs a client_id |
| Ganja2music | ❌ reCAPTCHA; Melovaz, Musicfa, Nava, 1music, ahangimo, bia2music ❌ unreachable |

## Plans, durations & discounts
* **Plans** (`Admin → Prices & plans`): each plan = **downloads per day** (number or ♾) + **monthly price** (plain number; currency label editable, default تومان/Toman) + optional feature bullets + ⭐ popular flag. Defaults: پایه/Basic **15/day**, اقتصادی/Economy **40/day ⭐ (popular, with a persuasive line)**, پیشرفته/Advanced **100/day**. A plan stays **hidden from users until its price is set** (prices are display-only; payment is manual outside the bot).
* **Duration discounts** (`Prices & plans → 🏷`): admin-editable % per duration, stored in `settings.duration_discounts`; defaults **1 month 0 %, 3 months 10 %, 6 months 20 %**. The plans screen shows, per plan, the monthly price and for 1/3/6 months the total with discount, `🔥 N٪ تخفیف`, the per-month effective price and a 🏆 best-value mark on the best discount.
* **Granting** (`Grant access`): after picking a user — ♾ forever · N days · credits · **♾ unlimited 1/3/6 months** · or a **plan → duration (1/3/6 months or custom days)**. Access has an expiry timestamp (`user.plan.until` / `unl_until`); **extending adds to the existing expiry**; expired access silently reverts to the free quota (computed on every check — no cron needed). While a plan is active its **daily download count** is enforced (all platforms share the plan's per-day number; recognition/search never spend it); when it is used up the user falls back to the normal free quota/credits. `/me` shows the plan, downloads left today and **days remaining**.
* **Expiry reminder**: one message per expiry, when ≤3 days remain, only for grants longer than 3 days, never repeated; an extension creates a new expiry and therefore a new (single) reminder. Checked every 30 min.
* Old state files migrate automatically (old count-plans → downloads/day, free-text prices → hidden until re-entered).

## 🎨 Themes (four seasons) & coloured buttons
`🎨 Theme` button / `/theme`: 🔥 Fire (summer) · ❄️ Snow (winter) · 🌸 Spring · 🍂 Autumn. Remembered per user; the admin sets the **global default** (`Settings → 🎨 Default theme`, or *Auto* = by Jalali season). The shared keyboard builder (`core.kb → theme.decorate`) adds the theme's emoji accents to **every** inline keyboard (menus, admin, plans, search, recognition). Telegram's `InlineKeyboardButton.style` field (`primary` blue / `success` green / `danger` red, documented in the Bot API) is used for the first button of a row, ✅/⬇️ (green) and ❌/🗑 (red); if the API ever rejects it the call is retried once without styles and styles are disabled for the process (also a toggle in the admin theme panel). Verified against the live API: a valid style is accepted, an invalid one is rejected with *"Invalid button style specified"*.

## 🎼 Music metadata, ID3 tags & lyrics
Music downloads (recognition / Spotify / search) now carry **title, artist, album, year, genre** (missing fields are omitted): from Shazam (album/year/genre), Spotify metadata, source info (SoundCloud description is appended, Radio Javan/Audius info) and key-less lookups (**iTunes Search**, **MusicBrainz**; Deezer is 403 from this box). They go into the **caption** and are embedded as **ID3v2 (mp3) / MP4 tags + cover art** with ffmpeg (`meta.embed_tags`). `📝 Lyrics` button (recognition card and on every downloaded song): shazamio lyrics if present → **Radio Javan** (Persian, `/api/p/mp3?id=`) → **lrclib.net** → **lyrics.ovh**; ≤4096-char messages (max 3), a `.txt` file when longer, always attributed ("Lyrics source: …"), polite message when none is found. Lyrics are shown from third-party sources for personal use; the bot stores nothing.

## 📡 Channel poster (owner only: Admin → 📡 Channel poster)
* **Channels**: add by `@name`, t.me link, numeric id or a **forwarded channel post**; verified with `getChat` + `getChatMember` (🟢 admin & can post / 🟠 no post right / 🔴 not admin); per channel: label/genre, public address (printed in posts), standing hashtags, footer language. Bot address is appended automatically.
* **Jobs**: *all songs of an artist* (Radio Javan artist page — direct MP3 links sorted by plays — plus iTunes discography titles matched through the provider chain), *list of `artist - title` lines*, or a *search/playlist query*. Every job first shows a **preview** (☑️/⬜ per track, unmatched titles listed) for the owner to deselect/confirm; only then it starts.
* **Posts**: audio + cover thumbnail + ID3 tags, source-free caption `🎵 title 👤 artist 💿 album 📅 year 🎼 genre` + hashtags (genre, artist(s), year, job/feed source tags, channel tags) + **footer** (template editable per language, variables `{channel} {bot} {botlink} {label}`). Posted-log per channel prevents duplicates. Files > 50 MB are re-encoded to fit or failed.
* **Queue**: interval (minutes) between posts and optional **daily cap** per job (“N posts per day from the saved queue”), pause / resume / cancel, progress in the job view, completion + failure reports to the owner, 3 retries with backoff, `retry_after` honoured, fatal errors (bot removed / no rights) pause the job; **state is in `state.json`, so jobs resume after a restart** (worker thread ticks every 20 s).
* **Auto feeds** (*Channel card → Auto feed*): sources = **Radio Javan trending/popular**, **Audius trending per genre**, **Apple/iTunes top songs per genre**, SoundCloud search, Shazam charts, and **this bot's own most-downloaded tracks** (per-track counters are recorded from now on). Per feed: genre, sources, top N, refresh period, post interval, daily cap, extra tags, **fully automatic or approval mode** (draft sent to the owner). Auto tags come from genre, artist, year, source (`#Trending`, `#Top`…) and owner tag lists. Chart status screen: `Admin → Channel poster → 🛰`.
* **Reachable from this box (2026-09-30):** ✅ Radio Javan `mp3s?type=trending|popular` and artist pages · ✅ Audius trending (genre filter) · ✅ iTunes RSS `topsongs` (US; `ir` storefront → 400) · ✅ iTunes Search / MusicBrainz / lrclib.net / lyrics.ovh · ⚠️ SoundCloud has no public chart endpoint (search returns DJ mixes, used with a duration filter only) · ❌ Shazam charts (`FailedDecodeJson` from shazamio) · ❌ Deezer charts (403) · ❌ apple `rss.applemarketingtools.com` (301 only).
* **Not verified live:** posting into a real channel (none exists yet — all tests mock Telegram), real flood-wait behaviour, the Persian `ir` iTunes storefront.

## 🎯 Targeted messaging (Admin → 📣 Messaging → 🎯 Selected users)
Recipients = union of: an ID / `@username` list, users ticked in a paginated list (☑️ checkboxes), and filters (active plan / unlimited holders, expired plans, language fa/en). Banned users are always excluded. Then compose (text, photo with caption, or **any forwarded message** — sent with `copyMessage`), see the **preview** and recipient count, confirm, and the bot sends at ~20 msg/s (`RATE`, `retry_after` honoured) and reports **sent / blocked / failed**. Extra admins may use it too. The existing “all users” broadcast is unchanged. (Other bots in this workspace were not changed.)


## 🎟 Discount codes (`codes.py`, `codesui.py`; Admin → 🎟 Discount codes, owner only)
Payment is manual, so a code only changes what the user *sees* and what the admin's grant *applies*. Kinds: **percent** (1-90 %, applied on top of the duration discount; the plans screen shows both prices; held until the admin grants), **🎁 free access** (N days, instant), **bonus downloads** (instant) and **bonus days** (instant). Options: expiry (days from now), max uses, once-per-user, restriction to one plan and/or one duration (1/3/6 months), enable/disable/delete (deleting also removes it from holders), usage stats (used, unique users, current holders). 

**Instant redemption (no admin):** entering the code is all it takes - the Forced-join gate runs first, so the user must be in ALL required channels (non-members just get the join buttons; nothing is consumed).
- **Free access**: value = days (1-3650), plus *plan or unlimited* (admin cycles it in the code's detail view; plan-bound = that plan's daily quota, unlimited = no limit). The user gets `unl_until` / a plan entry for N days from now, is told the end date, and the use is recorded. Options: code's own expiry, max total uses, once-per-user, enable/disable, stats (used / unique users). No per-duration restriction (the duration is the value).
- **Bonus downloads**: added to the balance immediately. **Bonus days**: immediately extend the user's active plan / timed access by N days; with no active access the code is *held* and the days are added when the admin grants something (as before).
- **No stacking** for free access: it is applied only when it would end *later* than the user's current access (timed-unlimited or plan); then it *replaces* that access with N days from now (not added on top). If the current access already lasts longer, the code is **not consumed** and the user is told until when they are covered. Admins / permanent-unlimited users are never charged a use. A deleted plan behind a code gives a friendly message and consumes nothing.
- Percent codes stay "held" (they only discount the admin-approved purchase).

Users enter it via `🎟 Discount code` (plans screen, /me, invite screen, `/code`); their active code is shown in /me; wrong guesses are rate-limited (8/hour). The admin's grant screen shows which code the user holds; granting a plan/timed access that the code applies to consumes it, records it per user, adds the bonus and tells both sides.

## 🎁 Referrals
Invite screen states the live reward per invited friend and the friend's bonus (defaults raised to **+5 / +3**; old untouched 3/1 defaults migrate once, custom values are kept), mentions discount codes and has a `🎟 Enter discount code` button. Admin → 🎁 Referrals edits inviter reward, friend bonus and an optional **total reward cap per inviter** (0 = unlimited); texts reflect changes immediately. At the cap the inviter stops earning but the friend still gets the bonus.

## 📌 Forced-join channels (Admin → 📢 Ads → 📌 Forced-join, also on the admin home)
Everything a non-admin does is gated (`allowed()` in `bot.py`: every message incl. voice/audio, every callback, plain-text search, discount-code entry, plans, invite, downloads): the user must be a member of **all** configured channels (`getChatMember`, cached 60 s positive / 5 s negative); otherwise one URL button per *missing* channel + `✅ I joined` (re-checks live). Admins are exempt. If the bot lost access to a channel (not admin / chat gone) that channel is skipped (fail-open, nobody is locked out) and the owner is alerted at most once per 6 h.

## 🟦 Bale (same code base, `DL_PLATFORM=bale`) — running; `getMe`/`setMyCommands`/polling verified live
Pattern taken from `lumeh-shop-bot` (`transport.py`): **one process per platform**, shared logic. `plat.py` holds the platform config, `core.py` adapts every Bot-API call (`bale_prepare`), `balefmt.py` converts our HTML subset to Bale Markdown.

| | Telegram | Bale |
|---|---|---|
| token env | `DL_TELEGRAM_BOT_TOKEN` | `DL_BALE_BOT_TOKEN` (**missing ⇒ the Bale process exits 0 with a message; `run_bale.sh` starts nothing**) |
| start / stop | `run.sh` / `stop.sh` | `run_bale.sh` / `stop_bale.sh` |
| log / state / lock / tmp | `bot.log` / `state.json` / `bot.lock` / `tmp/` | `bale.log` / `state_bale.json` / `bot_bale.lock` / `tmp_bale/` |
| API base | api.telegram.org | `https://tapi.bale.ai/bot<token>/` (files `/file/bot<token>/`) |

**Users are separate per platform** (own state file: users, plans, codes, settings, channels, admin). Cookies (Instagram/YouTube) are shared on disk.
**Owner on Bale:** usernames are not trusted there, so `@example_owner` does *not* auto-bind. On first start a one-time code `XXXX-XXXX-XXXX` is stored in `state_bale.json` (mode 600) and printed **only** to `bale.log` (`grep "CLAIM CODE" bale.log`); send `/claim <code>` to the bot from the owner account → the message is deleted, the numeric id is bound, the code is consumed (5 tries/hour/user, 20/hour total). Extra admins are added from the admin panel as on Telegram.

What differs on Bale (docs.bale.ai):
- **No `parse_mode`** – all text is Markdown (`*bold*`, `_italic_`, `[t](url)`, marks need a space outside). HTML → Markdown conversion; `<code>/<pre>/<u>` degrade to plain text; plain texts are neutralised against accidental Markdown. Captions clipped to 1024.
- **No coloured buttons** (`style`): removed; the four-season emoji themes still apply. The "coloured buttons" admin toggle is hidden.
- **No thumbnails / `supports_streaming`** on sendAudio/sendVideo/sendDocument: dropped (cover art still embedded in the MP3 ID3 tags). `disable_web_page_preview` and `allowed_updates` are not sent.
- **Upload limit 50 MB** for bots (`sendVideo/Audio/Document`), so the quality fallback targets `DL_BALE_LIMIT_MB` (default 49). Photos by upload ≤10 MB. `getFile` ≤20 MB (recognition input limit unchanged).
- **Bot profile:** verified against the real server — `setMyName` / `setMyDescription` / `setMyShortDescription` (and their getters) answer **501 "Not Implemented"**; `setMyCommands` works and is set automatically at start. Name, about and description (fa+en, Bale wording) are in **`bale_profile.txt`** for pasting into Bale's `@botfather`; the logo is `bale_logo.jpg`. The in-bot `/about` is identical to Telegram's.
- **Links** are `ble.ir/<name>` (support buttons, invite link, forced-join buttons; `ble.ir` links are accepted when the admin adds a channel). Bale has no `t.me/share/url` → no "Share" button on the invite screen (the ready-to-forward message remains).
- **Forced-join** uses `getChatMember`/`getChat` (supported; the bot must be admin of the Bale channel). `answerCallbackQuery` is always sent except for ids starting with `1` (old clients).
- Texts say *Bale* instead of *Telegram* automatically. **Channel auto-poster is Telegram-only** (hidden on Bale). Broadcast/targeted messaging work but are subject to Bale's interaction-based rate limits (the 20 msg/s throttle is kept conservative).
- Rate limit `429 retry_after` is honoured once.

Tests: `python3 test_bale.py` (adapter, claim code, token-less skip) and the whole functional suite against the Bale build: `DL_PLATFORM=bale DL_BALE_BOT_TOKEN=x python3 test_offline.py`. **Verified live:** `getMe`, `getUpdates` long polling, `setMyCommands`, 501 on profile setters. **Untested live** (no real user has messaged it yet): everything that touches the real Bale servers – request/response formats of each send method, Markdown rendering, `getChatMember` on Bale channels, upload of 50 MB files, recognition file download, callback behaviour on old clients.

## Tests
`./venv/bin/python test_offline.py` (mocked Telegram/engine, ~635 checks (also run against the Bale build) incl. LinkedIn, discount codes, forced-join on every path, referral settings, plans/durations/discounts/reminders, themes, ID3 (real ffmpeg), lyrics, poster/feeds/targeted messaging (all Telegram + network mocked), music provider chain scoring/search UX, per-platform quota, SoundCloud/Spotify parsing + match scoring); `./venv/bin/python live_recognize_test.py` (live: Shazam recognition of real song clips as ogg voice note/mp4, YouTube match, text search), `./venv/bin/python live_music_test.py` (network: SoundCloud track/set, Spotify metadata + YouTube match + download). `./venv/bin/python live_music_chain_test.py` (live: chain + search for an English hit, Persian songs, Spotify link). `./venv/bin/python live_poster_test.py` (live, read-only: chart sources, artist discography, matching, metadata, lyrics — posts nothing).

### Channel setup example: @rythmbox_rap
`channel_assets/` holds the logo (`rhythmbox_logo.jpg`, original artwork), title, description and pinned welcome text used for the channel. Feed source `rj_rap` = most-played tracks of a curated list of Persian rap artists (`poster.RAP_ARTISTS`, Radio Javan has no genre filter); the Audius chart skips beats/mixes/remixes/interludes. Third-party songs: posting them is the channel owner's responsibility; captions make no ownership claims.

## 📣 Campaigns, code editing, channel extras (latest)
- **Discount codes are fully editable** (Admin → 🎟): text (unique; holders + usage history carry over), value/days, plan or unlimited, expiry, max uses, once-per-user, enable/disable. Edits apply to future redemptions only; past redeemers keep what they got.
- **📣 Campaign** (owner + admins; `campaign.py`): editable fa/en promo text (HTML-safe: only b/i/u/s/code, unbalanced tags dropped), optional image, optional attached code picked from a list (defaults to the newest code). Placeholders `{code} {benefit} {value} {expires} {max_uses} {remaining} {conditions}` are filled LIVE from the code, so editing the code updates the preview/next sends. Buttons: `🎟 Redeem code` (instant redeem) + `🚀 Start`. Audience = all / active plan / expired / language / ID list / picker, banned and blocked users skipped, one delivery per user (ledger; "resend to everyone" is explicit), ~20 msgs/s with retry_after handling, delivery report, per-campaign stats (sent, blocked, failed, redeemed). Nothing is sent without the preview + confirm step.
- **Channel posts** get buttons: `📝 Lyrics` and `⬇️ Download in bot` (deep links `?start=lyr_<id>` / `dl_<id>`; track ids live in the poster log) and `⭐ Support` (`?start=sup`). Deep links honour language choice and forced-join (resumed after joining).
- **⭐ Telegram Stars support** (Telegram only): amount picker (defaults 10/25/50/100, owner-editable), `sendInvoice` with currency `XTR`, `pre_checkout_query` approved, thank-you after `successful_payment`, idempotent log (charge id kept for refunds) shown in Admin → Stats. It is a voluntary tip; nothing is sold or locked.
- **Diss / beef tracks** in the poster: identified by TITLE keywords (دیس / diss / diss back / بیف / beef), a hand-made list (`DISS_KNOWN*`) and Radio Javan search; tags `#دیس #دیس_بک #Diss #DissTrack #بیف #Beef` editable in Admin → Poster → 🔥. Heuristic only: amateur/meme uploads are dropped unless the artist is on a rap-artist allow-list.
- **SEO**: bot name/About/Description are keyword-rich (Instagram/YouTube/TikTok downloader…); BotFather rate-limits renames, the bot retries automatically when the limit ends. Search ranking cannot be guaranteed.

## 🔥 Viral focus for @rythmbox_rap (Admin → 📡 Channel poster → 🔥 Viral focus; owner only)
**On by default.** The poster ranks candidates by a *viral score* and posts the highest first.
- **Sources** (all public/reachable): Radio Javan trending + popular (50 each), Radio Javan rap, diss/beef lists, Audius trending, iTunes hip-hop chart.
- **Score** = chart weight × (1 − position/size) + 12 per extra chart the track appears in + 70 if on the admin's manual viral list + 10 for diss/beef + plays (log, ≤20) + recency (≤28, newest first) + 6 Iranian. Uncharted ordinary catalogue tracks get ×0.5. Tracks under `VIRAL_MIN` are dropped where the queue stays ≥ `MIN_QUEUE`.
- **Kept rules**: rap only (genre guard + artist allow-lists `RAP_ARTISTS`/`IR_RAP_EXTRA`), dedupe, per-artist cap, Iranian share target ≈ 65 %.
- **Hashtags** `#اینستا_وایرال #InstaViral #ReelsViral #وایرال #Viral #Trending` are added to every viral post; editable in the viral screen (edit/reset).
- **Admin**: toggle, manual viral list (add/remove/clear), edit tags, "re-rank queue" (re-orders pending items, skips filler).
- **Frequency**: feed 1 = up to 8/day, min 2 h gap; Telegram flood limits respected.
- **Limits (honest)**: no access to real Instagram Reels / TikTok analytics. "Viral" = position on public charts (Radio Javan / Audius / iTunes) + plays + freshness + admin list. Radio Javan charts carry no genre, so rap is inferred from artist lists — heuristic, can miss new rappers (add them to the manual list) or let a non-rap track through. Iranian detection is by artist lists/Persian text. Charts are snapshots and refresh every 24 h.

### Saved campaign draft «کمپین دانلودر»
Stored as an unsent draft in both `state.json` and `state_bale.json`, bound to code `WELCOME7`, Redeem + Start buttons on, image `assets/promo.jpg`. A photo value of `file:<relative path>` means a local file that is uploaded on every send/preview (Telegram `file_id`s set from the admin UI still work). Nothing is sent until the admin confirms in Campaign → Send.

## 🧵 Threads & 🐦 Twitter/X (`threads.py`, `twitterx.py`) — public posts only, no login/cookies
Links: `threads.net` / `threads.com` (`/@user/post/CODE`, `/t/CODE`, `/share/CODE`) and `twitter.com` / `x.com` / `mobile.twitter.com` / `t.co` / fx/vxtwitter mirrors (`…/status/ID`, `/i/status/ID`). They run through the same flow as other platforms: forced-join, ban, per-platform free quota (admin → Free limits per platform, keys `threads` and `twitter`, default **5/day** each, editable), plan limits, stats, caption = original post text + `🤖 @bot`, fa/en strings.
- **X**: `api.fxtwitter.com` (public JSON: text, author, every photo / video / GIF with all mp4 bitrates) → `api.vxtwitter.com` → yt-dlp / gallery-dl as a last resort. A single video goes through yt-dlp (quality ladder, audio check) and falls back to the best mp4 from the API; GIFs are sent as silent mp4 (no audio buttons); photos / several media → album. Quote-tweets without own media use the quoted media. 
- **Threads**: neither yt-dlp nor gallery-dl supports Threads, so the media (videos, images, carousels) is read from Meta's public *embed* page `/t/CODE/embed` (anonymous GET, retried 4× because the endpoint is flaky).
- **Errors**: text-only post → "no media (text-only)"; deleted / private / not embeddable → "not found / may be private"; protected X account → private; HTTP 429 → try again later; `t.co` link that doesn't point to a tweet → unsupported.
- **Limits (honest)**: depends on third-party public services (fxtwitter/vxtwitter can rate-limit or go down; Meta can change the embed markup); no login means no protected/age-restricted/sensitive-gated content, no X Spaces/broadcasts, no Threads text-only posts; Threads does not give a quality ladder (one mp4 per video); files over the 50 MB bot limit are skipped/limited like elsewhere. `live_threads_x_test.py` re-checks everything against real public posts.
- Bot profile (About / Description / short description fa+en, `bale_profile.txt`, channel description + pinned welcome) now mention Threads (تردز) and Twitter/X (توییتر).


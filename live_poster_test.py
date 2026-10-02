"""Live (network) checks of the poster data sources. NEVER posts anywhere: Telegram is not touched, state.json is not used."""
import os, sys, tempfile, time
os.environ.setdefault("DL_TELEGRAM_BOT_TOKEN", "1:live-test")
import store
tmp = tempfile.mkdtemp(); store.set_path(os.path.join(tmp, "state.json"))
import poster, meta, lyrics, music, engine

def show(label, ok, extra=""):
    print(("  ok   " if ok else "  FAIL ") + label + (" — " + extra if extra else ""))

print("== chart sources")
for src in poster.FEED_SOURCES:
    r = poster.fetch_source(src, "rap" if src in ("audius", "itunes") else "", 5)
    show(src, bool(r), poster.CHART_STATUS.get(src, "") + (" · e.g. " + "%s - %s" % (r[0]["artist"], r[0]["title"]) if r else ""))
print("== artist discography (Radio Javan + iTunes)")
ents = poster.artist_entries("Amir Tataloo", 12)
show("artist entries", len(ents) >= 5, "%d entries; %d with direct RJ link; first: %s" % (len(ents), sum(1 for e in ents if e.get("cand")), ents[0]["title"] if ents else "-"))
print("== matching a few titles through the provider chain")
items, bad = poster.match_entries([{"artist": "Googoosh", "title": "Hamsafar", "cand": None, "meta": {}}, {"artist": "Shadmehr Aghili", "title": "Tars", "cand": None, "meta": {}},
                                   {"artist": "Nobody Atall", "title": "Zzzqx Unfindable Song", "cand": None, "meta": {}}])
for it in items: show("matched " + it["artist"] + " - " + it["title"], True, (it["cand"] or {}).get("source", "?"))
show("unmatched reported", len(bad) >= 1, str(bad))
print("== metadata + lyrics")
for t, a in (("Never Gonna Give You Up", "Rick Astley"), ("Hamsafar", "Googoosh")):
    m = meta.lookup(t, a); show("metadata %s" % t, bool(m), str({k: v for k, v in m.items() if k != "cover"}))
    txt, src = lyrics.find(t, a, 0); show("lyrics %s" % t, bool(txt), "%s (%d chars)" % (src, len(txt or "")))
print("== caption example")
ch = {"handle": "@mychan", "title": "My Channel", "tags": ["#Rap"], "lang": "fa"}
print(poster.build_caption(ch, {"title": "Hamsafar", "artist": "Googoosh", "album": "Pol", "year": "1992", "genre": "Pop"}, ["#Trending"]))

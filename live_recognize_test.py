"""Live music-recognition check from this box: download a well-known song via yt-dlp, cut clips, recognise, match, download."""
import os, sys, subprocess, logging
os.environ.setdefault("DL_TELEGRAM_BOT_TOKEN", "0:live-test")
import engine, recognize
logging.basicConfig(level=logging.INFO, format="%(message)s")
def show(t, v): print(("OK   " if v else "FAIL ") + t)
wd = engine.new_workdir()
try:
    SONGS = [("Queen - Bohemian Rhapsody", "fJ9rUzIMcZQ", "bohemian rhapsody", 60), ("Rick Astley - Never Gonna Give You Up", "dQw4w9WgXcQ", "never gonna give you up", 40),
             ("Michael Jackson - Billie Jean", "Zi_XLOBDo_Y", "billie jean", 50)]
    for name, vid, expect, off in SONGS:
        d = os.path.join(wd, vid); os.makedirs(d)
        subprocess.run([engine.PY, "-m", "yt_dlp", "--no-warnings", "--ignore-config", "-f", "ba", "-x", "--audio-format", "mp3", "-o", d + "/full.%(ext)s",
                        "https://www.youtube.com/watch?v=" + vid], capture_output=True, timeout=180)
        full = os.path.join(d, "full.mp3")
        if not os.path.isfile(full):
            print("SKIP %s: YouTube refused the sample download from this IP (bot check) - not a recogniser result" % name); continue
        # 1) a 15 s clip as a Telegram-style voice note (ogg/opus, 48k mono) - the most common real input
        voice = os.path.join(d, "voice.ogg")
        subprocess.run([engine.ffmpeg_path(), "-y", "-loglevel", "error", "-ss", str(off), "-t", "15", "-i", full, "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "32k", voice], check=True)
        cands = recognize.recognize_media(voice, d)
        show("%s [voice note ogg/opus, %d KB] -> %s" % (name, os.path.getsize(voice) // 1024, [(c["title"], c["artist"]) for c in cands[:3]]),
             bool(cands) and expect in cands[0]["title"].lower())
        if cands:
            print("     cover:", bool(cands[0]["cover"]), "duration:", cands[0]["duration"], "album:", cands[0]["album"], "| alternatives:", [(c["title"], c["artist"]) for c in cands[1:]])
        # 2) a video with an audio track (mp4) 
        vid_mp4 = os.path.join(d, "clip.mp4")
        subprocess.run([engine.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=20", "-ss", str(off), "-t", "20", "-i", full, "-shortest",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", vid_mp4], check=True)
        c2 = recognize.recognize_media(vid_mp4, d)
        show("%s [mp4 video clip] recognised" % name, bool(c2) and expect in c2[0]["title"].lower())
        # 3) YouTube match + download (Spotify-style pipeline)
        if cands:
            info = engine.spotify_audio_info(recognize.to_meta(cands[0]))
            print("     matched:", info["match"]["title"], "|", info["match"]["channel"], "| score", info["match"]["score"])
            engine._clear_media(wd)
            p = engine.download_audio(info, "m4a", wd)
            show("download of the matched audio (%d KB, audio stream: %s)" % (os.path.getsize(p) // 1024, engine.has_audio_stream(p)), os.path.getsize(p) > 50000)
    # negative: silence + noise must not be 'recognised'
    sil = os.path.join(wd, "noise.wav")
    subprocess.run([engine.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=d=15:c=pink:a=0.2", "-ac", "1", "-ar", "44100", sil], check=True)
    show("pink noise is not recognised", recognize.recognize_media(sil, wd) == [])
    # text search
    r = recognize.search_songs("Queen Bohemian Rhapsody")
    show("text search: %s" % [(c["title"], c["artist"]) for c in r[:3]], bool(r))
finally:
    engine.cleanup(wd)

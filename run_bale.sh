#!/bin/bash
# SaveIt on Bale: separate process (own log bale.log, state state_bale.json, lock bot_bale.lock, tmp dir tmp_bale).
# Token from env DL_BALE_BOT_TOKEN (never written anywhere). Without the token this script exits cleanly - nothing runs.
#   Start: DL_BALE_BOT_TOKEN=... ./run_bale.sh      Stop: ./stop_bale.sh
cd "$(dirname "$0")"
DIR="$(pwd)"
if [ -z "$DL_BALE_BOT_TOKEN" ]; then echo "DL_BALE_BOT_TOKEN not set - Bale bot not started (Telegram bot is unaffected)"; exit 0; fi
if [ "$1" != "--loop" ]; then
  if [ -f run_bale.pid ] && kill -0 "$(cat run_bale.pid)" 2>/dev/null; then
    echo "SaveIt-Bale already running (supervisor pid $(cat run_bale.pid))"; exit 0
  fi
  setsid nohup "$DIR/run_bale.sh" --loop >/dev/null 2>&1 < /dev/null &
  sleep 1; echo "SaveIt-Bale started (supervisor pid $(cat run_bale.pid 2>/dev/null)); logs: $DIR/bale.log"; exit 0
fi
exec 9>run_bale.lock
flock -n 9 || exit 0
echo $$ > run_bale.pid
umask 077
export DL_PLATFORM=bale
PY="$DIR/venv/bin/python"; [ -x "$PY" ] || PY=python3
trap 'rm -f run_bale.pid; exit 0' TERM INT
while true; do
  "$PY" bot.py >> bale.log 2>&1
  echo "$(date '+%F %T') bale bot exited ($?), restarting in 5s" >> bale.log
  sleep 5
done

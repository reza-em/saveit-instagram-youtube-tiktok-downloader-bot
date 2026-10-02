#!/bin/bash
# SaveIt launcher: detached auto-restart loop, single instance. Token comes from the environment (DL_TELEGRAM_BOT_TOKEN).
cd "$(dirname "$0")"
DIR="$(pwd)"
if [ "$1" != "--loop" ]; then
  if [ -f run.pid ] && kill -0 "$(cat run.pid)" 2>/dev/null; then
    echo "SaveIt already running (supervisor pid $(cat run.pid))"; exit 0
  fi
  setsid nohup "$DIR/run.sh" --loop >/dev/null 2>&1 < /dev/null &
  sleep 1; echo "SaveIt started (supervisor pid $(cat run.pid 2>/dev/null)); logs: $DIR/bot.log"; exit 0
fi
exec 9>run.lock
flock -n 9 || exit 0          # only one supervisor, ever
echo $$ > run.pid
PY="$DIR/venv/bin/python"; [ -x "$PY" ] || PY=python3
trap 'rm -f run.pid; exit 0' TERM INT
while true; do
  "$PY" bot.py >> bot.log 2>&1
  echo "$(date '+%F %T') bot exited ($?), restarting in 5s" >> bot.log
  sleep 5
done

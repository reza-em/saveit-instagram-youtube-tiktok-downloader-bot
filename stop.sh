#!/bin/bash
# Stop the SaveIt supervisor loop and the bot process.
cd "$(dirname "$0")"
if [ -f run.pid ]; then
  P=$(cat run.pid); kill "$P" 2>/dev/null
fi
if [ -f bot.lock ]; then
  B=$(cat bot.lock); [ -n "$B" ] && kill "$B" 2>/dev/null
fi
sleep 1
rm -f run.pid
echo "SaveIt stopped"

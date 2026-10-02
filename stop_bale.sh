#!/bin/bash
# Stop the SaveIt-Bale supervisor loop and bot process.
cd "$(dirname "$0")"
[ -f run_bale.pid ] && kill "$(cat run_bale.pid)" 2>/dev/null
[ -f bot_bale.lock ] && { B=$(cat bot_bale.lock); [ -n "$B" ] && kill "$B" 2>/dev/null; }
rm -f run_bale.pid
echo "SaveIt-Bale stopped"

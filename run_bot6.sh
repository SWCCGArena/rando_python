#!/bin/bash
# Bot Pair C - Joiner: rando6 on port 5006
# Joins tables created by rando5 (prefix "BotC")
cd /mnt/ubuntu-lv/swccg/gemp/rando_cal_working/new_rando
source venv/bin/activate
export GEMP_USERNAME=rando6
export GEMP_PASSWORD=battmann
export BOT_PORT=5006
export BOT_JOINER_MODE=true
export LOCAL_FAST_MODE=true

# Only join tables with this prefix (matches BotC from rando5)
export BOT_JOINER_TARGET="BotC"

# Strategy config (use baseline.json by default, or set to experimental.json for testing)
export STRATEGY_CONFIG="${STRATEGY_CONFIG:-configs/baseline.json}"

# Fixed Light Side deck for reproducible testing
export FIXED_DECK_NAME="light_baseline"

# Stop after N games (0 = unlimited)
export MAX_GAMES=1

python app.py

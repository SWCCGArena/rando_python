#!/bin/bash
# Bot Pair E - Joiner: rando10 on port 5010
# Joins tables created by rando9 (prefix "BotE")
cd /mnt/ubuntu-lv/swccg/gemp/rando_cal_working/new_rando
source venv/bin/activate
export GEMP_USERNAME=rando10
export GEMP_PASSWORD=battmann
export BOT_PORT=5010
export BOT_JOINER_MODE=true
export LOCAL_FAST_MODE=true

# Only join tables with this prefix (matches BotE from rando9)
export BOT_JOINER_TARGET="BotE"

# Strategy config (use baseline.json by default, or set to experimental.json for testing)
export STRATEGY_CONFIG="${STRATEGY_CONFIG:-configs/baseline.json}"

# Fixed Light Side deck for reproducible testing
export FIXED_DECK_NAME="light_baseline"

# Stop after N games (0 = unlimited)
export MAX_GAMES=1

python app.py

#!/bin/bash
# Bot Pair 2 - Joiner: randogre on port 5004
# Joins tables created by randored (prefix "BotB")
cd /mnt/ubuntu-lv/swccg/gemp/rando_cal_working/new_rando
source venv/bin/activate
export GEMP_USERNAME=randogre
export GEMP_PASSWORD=battmann
export BOT_PORT=5004
export BOT_JOINER_MODE=true
export LOCAL_FAST_MODE=true

# Only join tables with this prefix (matches BotB from randored)
export BOT_JOINER_TARGET="BotB"

# Strategy config (use baseline.json by default, or set to experimental.json for testing)
export STRATEGY_CONFIG="${STRATEGY_CONFIG:-configs/baseline.json}"

# Fixed Light Side deck for reproducible testing
export FIXED_DECK_NAME="light_baseline"

# Stop after N games (0 = unlimited)
export MAX_GAMES=1

python app.py

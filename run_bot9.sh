#!/bin/bash
# Bot Pair E - Creator: rando9 on port 5009
# Creates tables with prefix "BotE" that rando10 will join
cd /mnt/ubuntu-lv/swccg/gemp/rando_cal_working/new_rando
source venv/bin/activate
export GEMP_USERNAME=rando9
export GEMP_PASSWORD=battmann
export BOT_PORT=5009
export LOCAL_FAST_MODE=true

# Table prefix for this bot pair (joiner will look for this prefix)
export BOT_TABLE_PREFIX="BotE"

# Strategy config (use baseline.json by default, or set to experimental.json for testing)
export STRATEGY_CONFIG="${STRATEGY_CONFIG:-configs/baseline.json}"

# Fixed Dark Side deck for reproducible testing
export FIXED_DECK_NAME="dark_baseline"

# Stop after N games (0 = unlimited)
export MAX_GAMES=1

python app.py

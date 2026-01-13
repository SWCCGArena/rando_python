#!/bin/bash
# Bot Pair 1 - Creator: rando_cal on port 5001
# Creates tables with prefix "BotA" that randoblu will join
cd /mnt/ubuntu-lv/swccg/gemp/rando_cal_working/new_rando
source venv/bin/activate
export GEMP_USERNAME=rando_cal
export GEMP_PASSWORD=battmann
export BOT_PORT=5001
export LOCAL_FAST_MODE=true

# Table prefix for this bot pair (joiner will look for this prefix)
export BOT_TABLE_PREFIX="BotA"

# Strategy config (use baseline.json by default, or set to experimental.json for testing)
export STRATEGY_CONFIG="${STRATEGY_CONFIG:-configs/baseline.json}"

# Fixed Dark Side deck for reproducible testing
export FIXED_DECK_NAME="dark_baseline"

# Stop after N games (0 = unlimited)
export MAX_GAMES=1

python app.py

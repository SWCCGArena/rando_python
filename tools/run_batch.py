#!/usr/bin/env python3
"""
Run a batch of bot-vs-bot games for measurement.

Usage:
    # Run 20 games sequentially (1 game at a time)
    python tools/run_batch.py --games 20

    # Run 20 games with 2 parallel bot pairs (10 rounds of 2 games each)
    python tools/run_batch.py --games 20 --parallel 2

    # Run 50 games with 5 parallel bot pairs (10 rounds of 5 games each)
    python tools/run_batch.py --games 50 --parallel 5

    # Compare two configs (same config for both bots in each pair)
    python tools/run_batch.py --games 20 --config1 baseline.json --config2 experimental.json

    # A/B testing Monte Carlo (creator uses config1, joiner uses config2)
    python tools/run_batch.py --games 20 --config1 tuned_v2_mc.json --config2 tuned_v2.json --ab-test

Bot Pairs (5 total for parallel=5):
    Pair A: rando_cal (creator, port 5001) vs randoblu (joiner, port 5002) - Table BotA
    Pair B: randored (creator, port 5003) vs randogre (joiner, port 5004) - Table BotB
    Pair C: rando5 (creator, port 5005) vs rando6 (joiner, port 5006) - Table BotC
    Pair D: rando11 (creator, port 5007) vs rando8 (joiner, port 5008) - Table BotD
    Pair E: rando9 (creator, port 5009) vs rando10 (joiner, port 5010) - Table BotE

A/B Testing Mode (--ab-test):
    In A/B test mode, configs are assigned to test effectiveness:
    - Pairs A, C, E: creator (config1) vs joiner (config2)
    - Pairs B, D: creator (config2) vs joiner (config1)
    This ensures both sides (Dark/Light) get to test each config for fair comparison.

This script:
1. Starts bot pairs (each pair has a creator and joiner)
2. Waits for games to complete
3. Collects results into results/{timestamp}/
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

# Paths
SCRIPT_DIR = Path(__file__).parent
NEW_RANDO_DIR = SCRIPT_DIR.parent
LOGS_DIR = NEW_RANDO_DIR / "logs"
RESULTS_DIR = NEW_RANDO_DIR / "results"
CONFIGS_DIR = NEW_RANDO_DIR / "configs"
VENV_PYTHON = NEW_RANDO_DIR / "venv" / "bin" / "python"


# Bot pair configurations (5 pairs for up to 5 parallel games)
BOT_PAIRS = {
    'A': {
        'creator': {
            'username': 'rando_cal',
            'port': '5001',
            'table_prefix': 'BotA',
        },
        'joiner': {
            'username': 'randoblu',
            'port': '5002',
            'joiner_target': 'BotA',
        },
    },
    'B': {
        'creator': {
            'username': 'randored',
            'port': '5003',
            'table_prefix': 'BotB',
        },
        'joiner': {
            'username': 'randogre',
            'port': '5004',
            'joiner_target': 'BotB',
        },
    },
    'C': {
        'creator': {
            'username': 'rando5',
            'port': '5005',
            'table_prefix': 'BotC',
        },
        'joiner': {
            'username': 'rando6',
            'port': '5006',
            'joiner_target': 'BotC',
        },
    },
    'D': {
        'creator': {
            'username': 'rando11',
            'port': '5007',
            'table_prefix': 'BotD',
        },
        'joiner': {
            'username': 'rando8',
            'port': '5008',
            'joiner_target': 'BotD',
        },
    },
    'E': {
        'creator': {
            'username': 'rando9',
            'port': '5009',
            'table_prefix': 'BotE',
        },
        'joiner': {
            'username': 'rando10',
            'port': '5010',
            'joiner_target': 'BotE',
        },
    },
}


def get_creator_env(pair_id: str, creator_config_path: str, dark_deck: Optional[str]) -> dict:
    """Get environment for creator bot."""
    pair = BOT_PAIRS[pair_id]
    env = os.environ.copy()
    env.update({
        'GEMP_USERNAME': pair['creator']['username'],
        'GEMP_PASSWORD': 'battmann',
        'BOT_PORT': pair['creator']['port'],
        'BOT_TABLE_PREFIX': pair['creator']['table_prefix'],
        'LOCAL_FAST_MODE': 'true',
        'STRATEGY_CONFIG': creator_config_path,
        'MAX_GAMES': '1',
        # CRITICAL: Force localhost to prevent accidental production connections
        'GEMP_SERVER_URL': 'http://localhost/gemp-swccg-server/',
    })
    # Only set FIXED_DECK_NAME if specified (allows bot to pick from my decks)
    if dark_deck:
        env['FIXED_DECK_NAME'] = dark_deck
    return env


def get_joiner_env(pair_id: str, joiner_config_path: str, light_deck: Optional[str]) -> dict:
    """Get environment for joiner bot."""
    pair = BOT_PAIRS[pair_id]
    env = os.environ.copy()
    env.update({
        'GEMP_USERNAME': pair['joiner']['username'],
        'GEMP_PASSWORD': 'battmann',
        'BOT_PORT': pair['joiner']['port'],
        'BOT_JOINER_MODE': 'true',
        'BOT_JOINER_TARGET': pair['joiner']['joiner_target'],
        'LOCAL_FAST_MODE': 'true',
        'STRATEGY_CONFIG': joiner_config_path,
        'MAX_GAMES': '1',
        # CRITICAL: Force localhost to prevent accidental production connections
        'GEMP_SERVER_URL': 'http://localhost/gemp-swccg-server/',
    })
    # Only set FIXED_DECK_NAME if specified (allows bot to pick from my decks)
    if light_deck:
        env['FIXED_DECK_NAME'] = light_deck
    return env


def get_ab_test_configs(pair_id: str, config1_path: str, config2_path: str) -> tuple:
    """
    Get config paths for A/B testing.

    In A/B test mode:
    - Pairs A, C, E (odd): creator uses config1, joiner uses config2
    - Pairs B, D (even): creator uses config2, joiner uses config1

    This ensures both Dark and Light sides test each config for fair comparison.

    Returns:
        (creator_config_path, joiner_config_path)
    """
    # Odd pairs (A=0, C=2, E=4) use config1 for creator
    # Even pairs (B=1, D=3) use config2 for creator
    pair_index = list(BOT_PAIRS.keys()).index(pair_id)
    if pair_index % 2 == 0:
        return (config1_path, config2_path)  # A, C, E: config1 vs config2
    else:
        return (config2_path, config1_path)  # B, D: config2 vs config1


def wait_for_game_logs(pair_id: str, start_time: float, timeout: int = 600) -> tuple:
    """
    Wait for new game log files to appear for a specific bot pair.

    Returns:
        (creator_log, joiner_log) paths or (None, None) if timeout
    """
    pair = BOT_PAIRS[pair_id]
    creator_username = pair['creator']['username']
    joiner_username = pair['joiner']['username']

    # Get logs that existed before we started
    initial_logs = {str(p) for p in LOGS_DIR.glob("*_vs_*_*.log") if p.stat().st_mtime < start_time}

    deadline = start_time + timeout
    while time.time() < deadline:
        current_logs = set(LOGS_DIR.glob("*_vs_*_*.log"))
        new_logs = [l for l in current_logs if str(l) not in initial_logs and l.stat().st_mtime >= start_time]

        # Look for logs from our specific bot pair
        creator_logs = [l for l in new_logs if l.name.startswith(f'{creator_username}_')]
        joiner_logs = [l for l in new_logs if l.name.startswith(f'{joiner_username}_')]

        if creator_logs and joiner_logs:
            # Return the most recent of each
            creator_logs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            joiner_logs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            return (creator_logs[0], joiner_logs[0])

        time.sleep(1)

    return (None, None)


def run_single_game(pair_id: str, creator_config_path: str, game_num: int,
                    dark_deck: Optional[str] = 'dark_baseline',
                    light_deck: Optional[str] = 'light_baseline',
                    joiner_config_path: str = None,
                    stagger_delay: float = 0) -> dict:
    """
    Run a single game using a specific bot pair.

    Args:
        pair_id: Bot pair ID ('A', 'B', 'C', 'D', or 'E')
        creator_config_path: Config path for creator bot
        game_num: Game number in batch
        dark_deck: Dark side deck name (None = bot picks from my decks)
        light_deck: Light side deck name (None = bot picks from my decks)
        joiner_config_path: Config path for joiner bot (if None, uses creator_config_path)
        stagger_delay: Seconds to wait before starting this pair (for parallel runs)

    Returns:
        Dict with game results
    """
    if joiner_config_path is None:
        joiner_config_path = creator_config_path

    pair = BOT_PAIRS[pair_id]
    creator_name = pair['creator']['username']
    joiner_name = pair['joiner']['username']

    print(f"  [Pair {pair_id}] Game {game_num}: {creator_name} vs {joiner_name}")

    # Stagger start to avoid login/table creation conflicts on GEMP server
    if stagger_delay > 0:
        time.sleep(stagger_delay)

    start_time = time.time()

    # Start creator bot (use venv python for Flask/dependencies)
    creator_proc = subprocess.Popen(
        [str(VENV_PYTHON), 'app.py'],
        cwd=NEW_RANDO_DIR,
        env=get_creator_env(pair_id, creator_config_path, dark_deck),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait a bit for creator to create table
    time.sleep(3)

    # Start joiner bot (use venv python for Flask/dependencies)
    joiner_proc = subprocess.Popen(
        [str(VENV_PYTHON), 'app.py'],
        cwd=NEW_RANDO_DIR,
        env=get_joiner_env(pair_id, joiner_config_path, light_deck),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for game to complete
    creator_log, joiner_log = wait_for_game_logs(pair_id, start_time, timeout=600)

    # Terminate bots
    creator_proc.terminate()
    joiner_proc.terminate()

    for proc in [creator_proc, joiner_proc]:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if creator_log is None or joiner_log is None:
        print(f"  [Pair {pair_id}] ERROR: Game did not complete in time")
        return {
            'pair_id': pair_id,
            'game_num': game_num,
            'completed': False,
            'error': 'timeout',
            'creator': creator_name,
            'joiner': joiner_name,
        }

    # Determine winner from log filename
    creator_won = '_win' in creator_log.name
    joiner_won = '_win' in joiner_log.name

    duration = time.time() - start_time
    winner = creator_name if creator_won else joiner_name
    print(f"  [Pair {pair_id}] Result: {winner} won ({duration:.1f}s)")

    return {
        'pair_id': pair_id,
        'game_num': game_num,
        'completed': True,
        'creator_log': str(creator_log),
        'joiner_log': str(joiner_log),
        'creator_won': creator_won,
        'joiner_won': joiner_won,
        'winner': 'creator' if creator_won else 'joiner',
        'winner_name': winner,
        'creator': creator_name,
        'joiner': joiner_name,
        'duration': duration,
    }


def run_parallel_round(pairs: list, config1_path: str, config2_path: str, game_nums: list,
                       dark_deck: str, light_deck: str, ab_test: bool = False) -> list:
    """
    Run games in parallel using multiple bot pairs.

    Args:
        pairs: List of pair IDs to use (e.g., ['A', 'B', 'C', 'D', 'E'])
        config1_path: Path to config1 (used for creator in normal mode, or A/B assignment)
        config2_path: Path to config2 (used for joiner in A/B mode)
        game_nums: List of game numbers corresponding to each pair
        ab_test: If True, assign configs based on A/B testing scheme

    Returns:
        List of game results
    """
    results = []

    # Stagger delay between pairs to avoid GEMP server login/table conflicts
    # Each pair starts 2 seconds after the previous one
    STAGGER_SECONDS = 2

    with ThreadPoolExecutor(max_workers=len(pairs)) as executor:
        futures = {}
        for idx, (pair_id, game_num) in enumerate(zip(pairs, game_nums)):
            if ab_test:
                creator_cfg, joiner_cfg = get_ab_test_configs(pair_id, config1_path, config2_path)
            else:
                creator_cfg, joiner_cfg = config1_path, config1_path

            # Stagger start times: pair A=0s, B=2s, C=4s, D=6s, E=8s
            stagger_delay = idx * STAGGER_SECONDS

            future = executor.submit(
                run_single_game, pair_id, creator_cfg, game_num, dark_deck, light_deck, joiner_cfg, stagger_delay
            )
            futures[future] = (pair_id, game_num)

        for future in as_completed(futures):
            result = future.result()
            results.append(result)

    return results


def run_batch(num_games: int, config1: str, config2: str,
              parallel: int = 1,
              dark_deck: Optional[str] = 'dark_baseline',
              light_deck: Optional[str] = 'light_baseline',
              ab_test: bool = False) -> dict:
    """
    Run a batch of games.

    Args:
        num_games: Number of games to run
        config1: Path to config for bot1 (relative to configs/)
        config2: Path to config for bot2 (relative to configs/)
        ab_test: If True, run in A/B testing mode
        parallel: Number of games to run in parallel (1 or 2)
        dark_deck: Name of Dark Side deck (None = bot picks from my decks)
        light_deck: Name of Light Side deck (None = bot picks from my decks)

    Returns:
        Dict with batch results
    """
    # Resolve config paths
    config1_path = str(CONFIGS_DIR / config1)
    config2_path = str(CONFIGS_DIR / config2)

    if not Path(config1_path).exists():
        print(f"ERROR: Config not found: {config1_path}")
        sys.exit(1)
    if not Path(config2_path).exists():
        print(f"ERROR: Config not found: {config2_path}")
        sys.exit(1)

    # Create results directory
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    result_dir = RESULTS_DIR / timestamp
    result_dir.mkdir(parents=True, exist_ok=True)
    games_dir = result_dir / "games"
    games_dir.mkdir(exist_ok=True)

    # Copy config files into results directory for reproducibility
    configs_backup_dir = result_dir / "configs"
    configs_backup_dir.mkdir(exist_ok=True)
    shutil.copy(config1_path, configs_backup_dir / f"bot1_{config1}")
    shutil.copy(config2_path, configs_backup_dir / f"bot2_{config2}")

    # Determine which bot pairs to use
    available_pairs = list(BOT_PAIRS.keys())[:parallel]

    print(f"\nStarting batch: {num_games} games")
    print(f"  Parallel bot pairs: {parallel} ({', '.join(available_pairs)})")
    if ab_test:
        print(f"  A/B Test Mode: ENABLED")
        print(f"    Config1: {config1}")
        print(f"    Config2: {config2}")
        for pair_id in available_pairs:
            creator_cfg, joiner_cfg = get_ab_test_configs(pair_id, config1, config2)
            print(f"    Pair {pair_id}: creator={creator_cfg}, joiner={joiner_cfg}")
    else:
        print(f"  Config: {config1}")
    print(f"  Dark deck: {dark_deck or 'random (from my decks)'}")
    print(f"  Light deck: {light_deck or 'random (from my decks)'}")
    print(f"  Results directory: {result_dir}")

    results = {
        'config1': config1,
        'config2': config2,
        'ab_test': ab_test,
        'num_games': num_games,
        'parallel': parallel,
        'timestamp': timestamp,
        'dark_deck': dark_deck,
        'light_deck': light_deck,
        'games': [],
        'creator_wins': 0,  # Dark side wins
        'joiner_wins': 0,   # Light side wins
        'config1_wins': 0,  # Wins by config1 (in A/B mode)
        'config2_wins': 0,  # Wins by config2 (in A/B mode)
        'errors': 0,
    }

    game_num = 1
    while game_num <= num_games:
        # Determine how many games to run in this round
        games_this_round = min(parallel, num_games - game_num + 1)
        pairs_this_round = available_pairs[:games_this_round]
        game_nums_this_round = list(range(game_num, game_num + games_this_round))

        print(f"\n--- Round {(game_num - 1) // parallel + 1} (games {game_nums_this_round}) ---")

        if games_this_round > 1:
            # Run in parallel
            round_results = run_parallel_round(
                pairs_this_round, config1_path, config2_path, game_nums_this_round,
                dark_deck, light_deck, ab_test
            )
        else:
            # Run single game
            pair_id = pairs_this_round[0]
            if ab_test:
                creator_cfg, joiner_cfg = get_ab_test_configs(pair_id, config1_path, config2_path)
            else:
                creator_cfg, joiner_cfg = config1_path, config1_path
            round_results = [run_single_game(
                pair_id, creator_cfg, game_num,
                dark_deck, light_deck, joiner_cfg
            )]

        # Process results
        for game_result in round_results:
            results['games'].append(game_result)

            if game_result['completed']:
                if game_result['creator_won']:
                    results['creator_wins'] += 1
                else:
                    results['joiner_wins'] += 1

                # Track config wins for A/B testing
                if ab_test:
                    pair_id = game_result['pair_id']
                    creator_cfg, joiner_cfg = get_ab_test_configs(pair_id, config1, config2)
                    if game_result['creator_won']:
                        winning_config = creator_cfg
                    else:
                        winning_config = joiner_cfg
                    game_result['winning_config'] = winning_config
                    if winning_config == config1:
                        results['config1_wins'] += 1
                    else:
                        results['config2_wins'] += 1

                # Move logs to results directory
                for log_key in ['creator_log', 'joiner_log']:
                    if log_key in game_result:
                        src = Path(game_result[log_key])
                        if src.exists():
                            dst = games_dir / src.name
                            shutil.move(str(src), str(dst))
                            game_result[log_key] = str(dst)

                            # Also move related decision and gamestate logs
                            base_name = src.stem
                            for suffix in ['_decisions.log', '_gamestate.xml']:
                                related_log = src.parent / f"{base_name}{suffix}"
                                if related_log.exists():
                                    shutil.move(str(related_log), str(games_dir / related_log.name))
            else:
                results['errors'] += 1

        game_num += games_this_round

        # Brief pause between rounds
        if game_num <= num_games:
            time.sleep(2)

    # Save results summary
    summary_path = result_dir / "summary.json"
    with open(summary_path, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n=== Batch Complete ===")
    print(f"Dark Side (creator) wins: {results['creator_wins']}")
    print(f"Light Side (joiner) wins: {results['joiner_wins']}")
    print(f"Errors: {results['errors']}")
    if results['creator_wins'] + results['joiner_wins'] > 0:
        dark_pct = results['creator_wins'] / (results['creator_wins'] + results['joiner_wins']) * 100
        print(f"Dark Side win rate: {dark_pct:.1f}%")

    # Show config wins for A/B testing
    if ab_test and results['config1_wins'] + results['config2_wins'] > 0:
        print(f"\n=== A/B Test Results ===")
        print(f"{config1} wins: {results['config1_wins']}")
        print(f"{config2} wins: {results['config2_wins']}")
        config1_pct = results['config1_wins'] / (results['config1_wins'] + results['config2_wins']) * 100
        print(f"{config1} win rate: {config1_pct:.1f}%")

    print(f"\nResults saved to: {result_dir}")

    return results


def main():
    parser = argparse.ArgumentParser(description='Run a batch of bot-vs-bot games')
    parser.add_argument('--games', type=int, default=20,
                        help='Number of games to run (default: 20)')
    parser.add_argument('--config1', default='baseline.json',
                        help='Config file for bots (default: baseline.json)')
    parser.add_argument('--config2', default='baseline.json',
                        help='Config file for bots (default: baseline.json)')
    parser.add_argument('--parallel', type=int, default=2, choices=[1, 2, 3, 4, 5],
                        help='Number of games to run in parallel (1-5, default: 2)')
    parser.add_argument('--dark-deck', default='dark_baseline',
                        help='Dark Side deck name (default: dark_baseline, use "random" for bot\'s my decks)')
    parser.add_argument('--light-deck', default='light_baseline',
                        help='Light Side deck name (default: light_baseline, use "random" for bot\'s my decks)')
    parser.add_argument('--random-decks', action='store_true',
                        help='Let bots pick random decks from their "my decks" instead of fixed decks')
    parser.add_argument('--ab-test', action='store_true',
                        help='A/B testing mode: config1 for creator, config2 for joiner (alternates by pair)')

    args = parser.parse_args()

    # Handle random decks option
    dark_deck = None if args.random_decks or args.dark_deck == 'random' else args.dark_deck
    light_deck = None if args.random_decks or args.light_deck == 'random' else args.light_deck

    run_batch(
        args.games,
        args.config1,
        args.config2,
        parallel=args.parallel,
        dark_deck=dark_deck,
        light_deck=light_deck,
        ab_test=args.ab_test,
    )


if __name__ == '__main__':
    main()

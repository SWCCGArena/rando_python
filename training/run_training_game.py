#!/usr/bin/env python3
"""
Training Game Runner - Run single game for neural network training.

Runs a single game between neural bot (training mode) and rules bot,
collects the trajectory, and optionally trains the network.

Usage:
    # Test a single game (neural vs rules)
    python training/run_training_game.py --test

    # Run training games (neural vs rules, train after each batch)
    python training/run_training_game.py --games 20 --train

    # Use GPU for training (inference still on CPU for bot process)
    python training/run_training_game.py --games 20 --train --device cuda
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot pairs for training (use different ports than production to avoid conflicts)
# 5 pairs allow up to 5 parallel games
BOT_PAIRS = [
    # (creator_username, joiner_username, creator_port, joiner_port, table_prefix)
    ("rando_cal", "randoblu", 6001, 6002, "TrainA"),
    ("randored", "randogre", 6003, 6004, "TrainB"),
    ("rando5", "rando6", 6005, 6006, "TrainC"),
    ("rando11", "rando8", 6007, 6008, "TrainD"),
    ("rando9", "rando10", 6009, 6010, "TrainE"),
]

# Paths
PROJECT_DIR = Path(__file__).parent.parent
CONFIGS_DIR = PROJECT_DIR / "configs"
TRAJECTORY_DIR = PROJECT_DIR / "training_data" / "trajectories"
MODELS_DIR = PROJECT_DIR / "models"


def run_single_game(
    neural_config: str = "neural.json",
    rules_config: str = "production.json",
    neural_is_creator: bool = True,
    dark_deck: str = "dark_baseline",
    light_deck: str = "light_baseline",
    timeout: int = 600,
    pair_index: int = 0,
    stagger_delay: float = 0,
) -> Dict:
    """
    Run a single training game.

    Args:
        neural_config: Config file for neural bot
        rules_config: Config file for rules bot
        neural_is_creator: If True, neural bot creates table (plays Dark)
        dark_deck: Dark side deck name
        light_deck: Light side deck name
        timeout: Game timeout in seconds
        pair_index: Which bot pair to use (0-4)
        stagger_delay: Seconds to wait before starting (for parallel runs)

    Returns:
        Dict with game result info
    """
    pair = BOT_PAIRS[pair_index % len(BOT_PAIRS)]
    creator_user, joiner_user, creator_port, joiner_port, table_prefix = pair

    # Determine which bot uses which config
    if neural_is_creator:
        creator_config = neural_config
        joiner_config = rules_config
        neural_user = creator_user
    else:
        creator_config = rules_config
        joiner_config = neural_config
        neural_user = joiner_user

    logger.info(f"[Pair {pair_index}] Starting game: {creator_user} vs {joiner_user}")

    # Stagger start to avoid GEMP server conflicts
    if stagger_delay > 0:
        time.sleep(stagger_delay)

    # Environment for bots
    base_env = os.environ.copy()
    base_env['GEMP_SERVER_URL'] = os.environ.get('GEMP_SERVER_URL', 'http://localhost/gemp-swccg-server/')
    base_env['LOCAL_FAST_MODE'] = 'true'
    base_env['MAX_GAMES'] = '1'
    base_env['DARK_DECK'] = dark_deck
    base_env['LIGHT_DECK'] = light_deck

    # Creator environment (neural if neural_is_creator)
    creator_env = base_env.copy()
    creator_env['GEMP_USERNAME'] = creator_user
    creator_env['BOT_PORT'] = str(creator_port)
    creator_env['BOT_TABLE_PREFIX'] = table_prefix
    creator_env['STRATEGY_CONFIG'] = str(CONFIGS_DIR / creator_config)

    if neural_is_creator:
        creator_env['NEURAL_TRAINING_MODE'] = '1'
        creator_env['NEURAL_DEVICE'] = 'cpu'

    # Joiner environment - must use JOINER_MODE and JOINER_TARGET
    joiner_env = base_env.copy()
    joiner_env['GEMP_USERNAME'] = joiner_user
    joiner_env['BOT_PORT'] = str(joiner_port)
    joiner_env['BOT_JOINER_MODE'] = 'true'  # Critical: tells bot to join, not create
    joiner_env['BOT_JOINER_TARGET'] = table_prefix  # Table prefix to look for
    joiner_env['STRATEGY_CONFIG'] = str(CONFIGS_DIR / joiner_config)

    if not neural_is_creator:
        joiner_env['NEURAL_TRAINING_MODE'] = '1'
        joiner_env['NEURAL_DEVICE'] = 'cpu'

    result = {
        'neural_user': neural_user,
        'neural_is_creator': neural_is_creator,
        'completed': False,
        'neural_won': False,
        'duration': 0,
        'error': None,
    }

    try:
        # Start creator bot
        # Note: Use DEVNULL for stdout/stderr to avoid deadlock from pipe buffer filling
        logger.info(f"Starting creator bot: {creator_user} on port {creator_port}")
        creator_proc = subprocess.Popen(
            [sys.executable, 'app.py'],
            cwd=str(PROJECT_DIR),
            env=creator_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for table creation
        time.sleep(3)

        # Start joiner bot
        logger.info(f"Starting joiner bot: {joiner_user} on port {joiner_port}")
        joiner_proc = subprocess.Popen(
            [sys.executable, 'app.py'],
            cwd=str(PROJECT_DIR),
            env=joiner_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for game to complete
        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check if either process ended
            creator_ret = creator_proc.poll()
            joiner_ret = joiner_proc.poll()

            if creator_ret is not None or joiner_ret is not None:
                logger.info("Bot process ended, checking logs...")
                break

            # Check for game log files for THIS specific bot pair (indicates game completed)
            # Match either creator or joiner in the filename to avoid cross-matching other games
            log_files = list((PROJECT_DIR / "logs").glob(f"{creator_user}_*_vs_*.log"))
            log_files.extend((PROJECT_DIR / "logs").glob(f"{joiner_user}_*_vs_*.log"))
            recent_logs = [f for f in log_files if f.stat().st_mtime > start_time]

            if recent_logs:
                # Check if any show win/loss
                for log_file in recent_logs:
                    if '_win.' in log_file.name or '_loss.' in log_file.name:
                        logger.info(f"Game completed: {log_file.name}")
                        result['completed'] = True

                        # Determine winner
                        if neural_user in log_file.name:
                            result['neural_won'] = '_win.' in log_file.name
                        else:
                            result['neural_won'] = '_loss.' in log_file.name

                        break

                if result['completed']:
                    break

            time.sleep(2)

        result['duration'] = time.time() - start_time

        # Terminate processes
        for proc in [creator_proc, joiner_proc]:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)

    except Exception as e:
        result['error'] = str(e)
        logger.error(f"Game failed: {e}")

    logger.info(f"Game result: completed={result['completed']}, "
               f"neural_won={result['neural_won']}, "
               f"duration={result['duration']:.1f}s")

    return result


def collect_trajectories() -> List:
    """Collect all trajectory files from training directory."""
    from engine.neural_planner.trajectory_io import load_trajectories_from_dir

    TRAJECTORY_DIR.mkdir(parents=True, exist_ok=True)
    return load_trajectories_from_dir(TRAJECTORY_DIR)


def train_on_trajectories(
    trajectories: List,
    device: str = 'cuda',
    save_path: str = 'models/deploy_planner.pt',
) -> Dict:
    """
    Train the neural network on collected trajectories.

    Args:
        trajectories: List of GameTrajectory objects
        device: 'cuda' or 'cpu'
        save_path: Path to save updated model

    Returns:
        Training metrics
    """
    if not trajectories:
        logger.warning("No trajectories to train on")
        return {}

    import torch
    from engine.neural_planner.network import DeployPolicyNetwork
    from engine.neural_planner.trainer import PPOTrainer, PPOConfig
    from engine.neural_planner.experience import ExperienceBatch

    # Create or load network
    network = DeployPolicyNetwork()
    if os.path.exists(save_path):
        try:
            network.load_state_dict(torch.load(save_path, map_location=device))
            logger.info(f"Loaded existing model from {save_path}")
        except Exception as e:
            logger.warning(f"Could not load model: {e}")

    # Create trainer
    config = PPOConfig(device=device)
    trainer = PPOTrainer(network, config)

    # Create batch from trajectories
    batch = ExperienceBatch.from_trajectories(trajectories)
    if len(batch) == 0:
        logger.warning("No experiences in trajectories")
        return {}

    batch.normalize_advantages()

    logger.info(f"Training on {len(batch)} experiences from {len(trajectories)} games")

    # Train
    metrics = trainer.update(
        states=batch.states,
        actions=batch.actions,
        action_masks=batch.action_masks,
        old_log_probs=batch.log_probs,
        returns=batch.returns,
        advantages=batch.advantages,
    )

    # Save updated model
    trainer.save_model_only(save_path)
    logger.info(f"Saved updated model to {save_path}")

    return metrics


def clear_trajectories():
    """Clear old trajectory files."""
    if TRAJECTORY_DIR.exists():
        for f in TRAJECTORY_DIR.glob('*.json'):
            f.unlink()
        logger.info("Cleared trajectory files")


def run_parallel_games(
    num_games: int,
    neural_config: str = "neural.json",
    rules_config: str = "production.json",
    timeout: int = 600,
    stagger_seconds: float = 2.0,
) -> List[Dict]:
    """
    Run multiple games in parallel using different bot pairs.

    Args:
        num_games: Number of games to run (max 5 for 5 bot pairs)
        neural_config: Config file for neural bot
        rules_config: Config file for rules bot
        timeout: Game timeout in seconds
        stagger_seconds: Seconds between starting each game

    Returns:
        List of game result dicts
    """
    max_parallel = len(BOT_PAIRS)
    num_games = min(num_games, max_parallel)

    logger.info(f"Starting {num_games} parallel games across {num_games} bot pairs")

    results = []

    with ThreadPoolExecutor(max_workers=num_games) as executor:
        futures = {}

        for i in range(num_games):
            # Alternate sides for balance
            neural_is_creator = (i % 2 == 0)

            future = executor.submit(
                run_single_game,
                neural_config=neural_config,
                rules_config=rules_config,
                neural_is_creator=neural_is_creator,
                timeout=timeout,
                pair_index=i,
                stagger_delay=i * stagger_seconds,
            )
            futures[future] = i

        for future in as_completed(futures):
            game_idx = futures[future]
            try:
                result = future.result()
                result['pair_index'] = game_idx
                results.append(result)
                logger.info(f"Game {game_idx} completed: won={result.get('neural_won')}")
            except Exception as e:
                logger.error(f"Game {game_idx} failed: {e}")
                results.append({'completed': False, 'error': str(e), 'pair_index': game_idx})

    return results


def main():
    parser = argparse.ArgumentParser(description='Run neural training games')
    parser.add_argument('--test', action='store_true',
                       help='Run a single test game')
    parser.add_argument('--games', type=int, default=1,
                       help='Number of games to run')
    parser.add_argument('--train', action='store_true',
                       help='Train after collecting games')
    parser.add_argument('--device', type=str, default='cuda',
                       choices=['cuda', 'cpu'], help='Device for training')
    parser.add_argument('--neural-config', type=str, default='neural.json',
                       help='Config file for neural bot')
    parser.add_argument('--rules-config', type=str, default='production.json',
                       help='Config file for rules bot')
    parser.add_argument('--timeout', type=int, default=600,
                       help='Game timeout in seconds')
    parser.add_argument('--clear', action='store_true',
                       help='Clear old trajectories before starting')
    parser.add_argument('--parallel', type=int, default=0,
                       help='Run games in parallel (max 5 bot pairs)')

    args = parser.parse_args()

    if args.clear:
        clear_trajectories()

    # Run games - parallel or sequential
    results = []
    wins = 0

    if args.parallel > 0:
        # Run in parallel batches
        games_remaining = args.games
        batch_num = 0
        while games_remaining > 0:
            batch_num += 1
            batch_size = min(args.parallel, games_remaining, len(BOT_PAIRS))
            logger.info(f"\n=== Parallel Batch {batch_num} ({batch_size} games) ===")

            batch_results = run_parallel_games(
                num_games=batch_size,
                neural_config=args.neural_config,
                rules_config=args.rules_config,
                timeout=args.timeout,
            )

            for result in batch_results:
                results.append(result)
                if result.get('neural_won'):
                    wins += 1

            games_remaining -= batch_size

            completed_so_far = sum(1 for r in results if r.get('completed'))
            if completed_so_far > 0:
                logger.info(f"Running total: {wins}/{completed_so_far} neural wins "
                           f"({100*wins/completed_so_far:.0f}%)")
    else:
        # Sequential execution
        for i in range(args.games):
            logger.info(f"\n=== Game {i+1}/{args.games} ===")

            # Alternate who is creator (Dark side) for balance
            neural_is_creator = (i % 2 == 0)

            result = run_single_game(
                neural_config=args.neural_config,
                rules_config=args.rules_config,
                neural_is_creator=neural_is_creator,
                timeout=args.timeout,
            )

            results.append(result)
            if result.get('neural_won'):
                wins += 1

            if result.get('completed'):
                logger.info(f"Running total: {wins}/{len(results)} neural wins "
                           f"({100*wins/len(results):.0f}%)")

    # Summary
    completed = sum(1 for r in results if r.get('completed'))
    logger.info(f"\n=== Summary ===")
    logger.info(f"Completed: {completed}/{len(results)}")
    logger.info(f"Neural wins: {wins}/{completed} ({100*wins/completed:.0f}%)" if completed > 0 else "No games completed")

    # Train if requested
    if args.train:
        trajectories = collect_trajectories()
        if trajectories:
            metrics = train_on_trajectories(
                trajectories,
                device=args.device,
            )
            logger.info(f"Training metrics: {metrics}")
        else:
            logger.warning("No trajectories found for training")


if __name__ == '__main__':
    main()

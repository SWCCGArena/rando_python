#!/usr/bin/env python3
"""
Neural Deploy Planner Training Script

Runs training games against the rules-based bot and trains the neural network.
Shows live progress with key metrics to monitor convergence.

Usage:
    # Run training with default settings
    python training/train_neural.py

    # Run 100 games total, 5 games per batch
    python training/train_neural.py --games 100 --batch-size 5

    # Use GPU for training
    python training/train_neural.py --device cuda

    # Continue from existing model
    python training/train_neural.py --resume

Key Metrics to Watch:
    - Win Rate: Should trend upward from ~50% (random) toward 60-70%+
    - Policy Loss: Should decrease then stabilize around 0.5-2.0
    - Value Loss: Should decrease as predictions improve
    - Entropy: Should slowly decrease (more confident decisions)
    - Avg Game Length: May change as strategies evolve
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from training.run_training_game import run_single_game, run_parallel_games, collect_trajectories, clear_trajectories

# Suppress verbose logging from submodules
logging.getLogger('training.run_training_game').setLevel(logging.WARNING)
logging.getLogger('engine.neural_planner').setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Paths
PROJECT_DIR = Path(__file__).parent.parent
MODELS_DIR = PROJECT_DIR / "models"
LOGS_DIR = PROJECT_DIR / "training_logs"
CHECKPOINT_DIR = MODELS_DIR / "checkpoints"


class TrainingStats:
    """Track training statistics over time."""

    def __init__(self, window_size: int = 20):
        self.window_size = window_size

        # Game results (rolling window)
        self.recent_wins = deque(maxlen=window_size)
        self.recent_lengths = deque(maxlen=window_size)

        # All-time stats
        self.total_games = 0
        self.total_wins = 0
        self.total_experiences = 0

        # Training metrics history
        self.loss_history = []
        self.value_loss_history = []
        self.entropy_history = []

        # Timing
        self.start_time = time.time()
        self.games_per_hour = 0

    def record_game(self, won: bool, game_length: int, num_experiences: int):
        """Record a completed game."""
        self.total_games += 1
        self.total_wins += 1 if won else 0
        self.total_experiences += num_experiences

        self.recent_wins.append(1 if won else 0)
        self.recent_lengths.append(game_length)

        # Update games per hour
        elapsed = time.time() - self.start_time
        if elapsed > 0:
            self.games_per_hour = self.total_games / (elapsed / 3600)

    def record_training(self, metrics: Dict):
        """Record training metrics."""
        if 'policy_loss' in metrics:
            self.loss_history.append(metrics['policy_loss'])
        if 'value_loss' in metrics:
            self.value_loss_history.append(metrics['value_loss'])
        if 'entropy' in metrics:
            self.entropy_history.append(metrics['entropy'])

    @property
    def win_rate(self) -> float:
        """Overall win rate."""
        if self.total_games == 0:
            return 0.0
        return self.total_wins / self.total_games

    @property
    def recent_win_rate(self) -> float:
        """Win rate over recent games."""
        if len(self.recent_wins) == 0:
            return 0.0
        return sum(self.recent_wins) / len(self.recent_wins)

    @property
    def avg_game_length(self) -> float:
        """Average game length (recent)."""
        if len(self.recent_lengths) == 0:
            return 0.0
        return sum(self.recent_lengths) / len(self.recent_lengths)

    @property
    def avg_policy_loss(self) -> float:
        """Average recent policy loss."""
        if len(self.loss_history) == 0:
            return 0.0
        n = min(3, len(self.loss_history))
        return sum(self.loss_history[-n:]) / n

    @property
    def avg_value_loss(self) -> float:
        """Average recent value loss."""
        if len(self.value_loss_history) == 0:
            return 0.0
        n = min(3, len(self.value_loss_history))
        return sum(self.value_loss_history[-n:]) / n

    @property
    def avg_entropy(self) -> float:
        """Average recent entropy."""
        if len(self.entropy_history) == 0:
            return 0.0
        n = min(3, len(self.entropy_history))
        return sum(self.entropy_history[-n:]) / n

    def get_trend(self, values: List[float], window: int = 5) -> str:
        """Get trend direction for a metric."""
        if len(values) < window * 2:
            return "~"  # Not enough data

        old_avg = sum(values[-window*2:-window]) / window
        new_avg = sum(values[-window:]) / window

        diff = new_avg - old_avg
        if abs(diff) < 0.01:
            return "→"  # Stable
        elif diff > 0:
            return "↑"  # Increasing
        else:
            return "↓"  # Decreasing


def print_header():
    """Print training header."""
    print("\n" + "=" * 70)
    print("  NEURAL DEPLOY PLANNER TRAINING")
    print("=" * 70)
    print("""
What to watch for:
  - Win Rate: Should trend upward from ~50% toward 60%+
  - Policy Loss: Should decrease then stabilize (0.5-2.0 is normal)
  - Value Loss: Should decrease (better predictions)
  - Entropy: Should slowly decrease (more confident)

Press Ctrl+C to stop training gracefully.
""")
    print("=" * 70 + "\n")


def print_progress(stats: TrainingStats, batch_num: int, total_batches: int):
    """Print current progress."""
    elapsed = time.time() - stats.start_time
    elapsed_str = str(timedelta(seconds=int(elapsed)))

    # Update games per hour
    if elapsed > 0:
        stats.games_per_hour = stats.total_games / (elapsed / 3600)

    # Estimate time remaining
    if stats.total_games > 0 and batch_num > 0:
        games_remaining = (total_batches - batch_num) * (stats.total_games / batch_num)
        time_per_game = elapsed / stats.total_games
        eta = timedelta(seconds=int(games_remaining * time_per_game))
        eta_str = str(eta)
    else:
        eta_str = "calculating..."

    # Win rate trend
    win_rates = [sum(list(stats.recent_wins)[:i+1]) / (i+1)
                 for i in range(len(stats.recent_wins))]
    win_trend = stats.get_trend(win_rates) if len(win_rates) > 10 else "~"

    print(f"\n{'─' * 70}")
    print(f"  Batch {batch_num}/{total_batches} | "
          f"Games: {stats.total_games} | "
          f"Time: {elapsed_str} | "
          f"ETA: {eta_str}")
    print(f"{'─' * 70}")

    # Win rate bar
    wr = stats.recent_win_rate
    bar_width = 30
    filled = int(wr * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    print(f"  Win Rate (last {len(stats.recent_wins)}): [{bar}] {wr*100:.1f}% {win_trend}")
    print(f"  Overall Win Rate: {stats.win_rate*100:.1f}% ({stats.total_wins}/{stats.total_games})")

    # Other metrics
    print(f"\n  Avg Game Length: {stats.avg_game_length:.1f} turns")
    print(f"  Total Experiences: {stats.total_experiences}")
    print(f"  Games/Hour: {stats.games_per_hour:.1f}")

    # Training metrics
    if stats.loss_history:
        policy_trend = stats.get_trend(stats.loss_history)
        value_trend = stats.get_trend(stats.value_loss_history)
        entropy_trend = stats.get_trend(stats.entropy_history)

        print(f"\n  Policy Loss: {stats.avg_policy_loss:.4f} {policy_trend}")
        print(f"  Value Loss:  {stats.avg_value_loss:.4f} {value_trend}")
        print(f"  Entropy:     {stats.avg_entropy:.4f} {entropy_trend}")


def print_batch_result(game_num: int, won: bool, duration: float,
                       num_experiences: int, neural_side: str):
    """Print single game result."""
    result = "WIN " if won else "LOSS"
    side = "Dark" if neural_side == "creator" else "Light"
    print(f"    Game {game_num}: {result} ({side}) - "
          f"{duration:.1f}s, {num_experiences} exp")


def train_batch(
    trajectories: List,
    device: str = 'cuda',
    model_path: str = 'models/deploy_planner.pt',
) -> Dict:
    """Train on collected trajectories and return metrics."""
    if not trajectories:
        return {}

    import torch
    from engine.neural_planner.network import DeployPolicyNetwork
    from engine.neural_planner.trainer import PPOTrainer, PPOConfig
    from engine.neural_planner.experience import ExperienceBatch

    # Create or load network
    network = DeployPolicyNetwork()
    if os.path.exists(model_path):
        try:
            network.load_state_dict(torch.load(model_path, map_location=device))
        except Exception as e:
            logger.warning(f"Could not load model: {e}")

    network = network.to(device)

    # Create trainer
    config = PPOConfig(device=device)
    trainer = PPOTrainer(network, config)

    # Create batch from trajectories
    batch = ExperienceBatch.from_trajectories(trajectories)
    if len(batch) == 0:
        return {}

    batch.normalize_advantages()

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
    trainer.save_model_only(model_path)

    return metrics


def save_checkpoint(stats: TrainingStats, batch_num: int, model_path: str):
    """Save a training checkpoint."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    checkpoint_path = CHECKPOINT_DIR / f"checkpoint_{timestamp}_batch{batch_num}.pt"

    # Copy current model to checkpoint
    import shutil
    if os.path.exists(model_path):
        shutil.copy(model_path, checkpoint_path)

    # Save stats
    stats_path = CHECKPOINT_DIR / f"stats_{timestamp}_batch{batch_num}.json"
    with open(stats_path, 'w') as f:
        json.dump({
            'batch_num': batch_num,
            'total_games': stats.total_games,
            'total_wins': stats.total_wins,
            'win_rate': stats.win_rate,
            'total_experiences': stats.total_experiences,
            'loss_history': stats.loss_history[-20:],
            'value_loss_history': stats.value_loss_history[-20:],
            'entropy_history': stats.entropy_history[-20:],
        }, f, indent=2)

    logger.info(f"Saved checkpoint: {checkpoint_path.name}")


def main():
    parser = argparse.ArgumentParser(
        description='Train neural deploy planner',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--games', type=int, default=50,
                        help='Total number of games to run (default: 50)')
    parser.add_argument('--batch-size', type=int, default=5,
                        help='Games per training batch (default: 5)')
    parser.add_argument('--device', type=str, default='cuda',
                        choices=['cuda', 'cpu'],
                        help='Device for training (default: cuda)')
    parser.add_argument('--model-path', type=str,
                        default='models/deploy_planner.pt',
                        help='Path to model weights')
    parser.add_argument('--checkpoint-interval', type=int, default=5,
                        help='Save checkpoint every N batches (default: 5)')
    parser.add_argument('--resume', action='store_true',
                        help='Resume from existing model (default: start fresh)')
    parser.add_argument('--clear', action='store_true',
                        help='Clear old trajectories before starting')
    parser.add_argument('--timeout', type=int, default=300,
                        help='Game timeout in seconds (default: 300)')
    parser.add_argument('--parallel', type=int, default=5,
                        help='Number of games to run in parallel (default: 5, max 5)')

    args = parser.parse_args()

    # Setup
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if args.clear:
        clear_trajectories()

    # Initialize stats
    stats = TrainingStats(window_size=min(20, args.games))
    total_batches = (args.games + args.batch_size - 1) // args.batch_size

    print_header()

    if not args.resume and os.path.exists(args.model_path):
        print(f"Note: Starting fresh (use --resume to continue from existing model)")

    parallel_count = min(args.parallel, 5)  # Max 5 bot pairs
    print(f"Training plan: {args.games} games in {total_batches} batches ({parallel_count} parallel)")
    print(f"Device: {args.device}")
    print(f"Model: {args.model_path}")
    print()

    try:
        game_num = 0
        for batch_num in range(1, total_batches + 1):
            batch_start = time.time()
            games_this_batch = min(args.batch_size, args.games - game_num)

            print(f"\n  Running batch {batch_num} ({games_this_batch} games, {min(games_this_batch, parallel_count)} parallel)...")

            # Clear trajectories from previous batch
            clear_trajectories()

            # Run games in batch - parallel or sequential
            if parallel_count > 1 and games_this_batch > 1:
                # Run games in parallel sub-batches
                games_remaining = games_this_batch
                while games_remaining > 0:
                    parallel_this_round = min(parallel_count, games_remaining)
                    results = run_parallel_games(
                        num_games=parallel_this_round,
                        neural_config='neural.json',
                        rules_config='production.json',
                        timeout=args.timeout,
                    )

                    for result in results:
                        game_num += 1
                        won = result.get('neural_won', False)
                        completed = result.get('completed', False)
                        if completed:
                            stats.total_games += 1
                            stats.total_wins += 1 if won else 0
                            stats.recent_wins.append(1 if won else 0)
                            stats.recent_lengths.append(6)  # Approximate
                            result_str = "WIN " if won else "LOSS"
                            print(f"    Game {game_num}: {result_str} (pair_{result.get('pair_index', 0)}) - "
                                  f"{result.get('duration', 0):.1f}s")
                        else:
                            print(f"    Game {game_num}: FAILED - {result.get('error', 'timeout')}")

                    games_remaining -= parallel_this_round
            else:
                # Sequential execution
                for i in range(games_this_batch):
                    game_num += 1

                    # Alternate sides for balance
                    neural_is_creator = (game_num % 2 == 1)

                    result = run_single_game(
                        neural_config='neural.json',
                        rules_config='production.json',
                        neural_is_creator=neural_is_creator,
                        timeout=args.timeout,
                    )

                    if result.get('completed'):
                        # Load trajectory to get experience count
                        trajectories = collect_trajectories()
                        num_exp = sum(len(t.experiences) for t in trajectories) if trajectories else 0

                        stats.record_game(
                            won=result.get('neural_won', False),
                            game_length=6,  # Approximate
                            num_experiences=num_exp,
                        )

                        print_batch_result(
                            game_num,
                            result.get('neural_won', False),
                            result.get('duration', 0),
                            num_exp,
                            "creator" if neural_is_creator else "joiner"
                        )
                    else:
                        print(f"    Game {game_num}: FAILED - {result.get('error', 'timeout')}")

            # Collect and train on batch trajectories
            trajectories = collect_trajectories()
            if trajectories:
                total_exp = sum(len(t.experiences) for t in trajectories)
                stats.total_experiences += total_exp  # Update experience count from trajectories
                print(f"\n  Training on {len(trajectories)} trajectories ({total_exp} experiences)...")
                metrics = train_batch(
                    trajectories,
                    device=args.device,
                    model_path=args.model_path,
                )
                stats.record_training(metrics)

                if metrics:
                    print(f"    Policy Loss: {metrics.get('policy_loss', 0):.4f}, "
                          f"Value Loss: {metrics.get('value_loss', 0):.4f}, "
                          f"Entropy: {metrics.get('entropy', 0):.4f}")

            # Show progress
            print_progress(stats, batch_num, total_batches)

            # Checkpoint
            if batch_num % args.checkpoint_interval == 0:
                save_checkpoint(stats, batch_num, args.model_path)

    except KeyboardInterrupt:
        print("\n\nTraining interrupted by user.")
        save_checkpoint(stats, batch_num, args.model_path)

    # Final summary
    print("\n" + "=" * 70)
    print("  TRAINING COMPLETE")
    print("=" * 70)
    print(f"\n  Total Games: {stats.total_games}")
    print(f"  Total Wins: {stats.total_wins}")
    print(f"  Final Win Rate: {stats.win_rate*100:.1f}%")
    print(f"  Total Experiences: {stats.total_experiences}")

    if stats.loss_history:
        print(f"\n  Final Policy Loss: {stats.avg_policy_loss:.4f}")
        print(f"  Final Value Loss: {stats.avg_value_loss:.4f}")
        print(f"  Final Entropy: {stats.avg_entropy:.4f}")

    elapsed = time.time() - stats.start_time
    print(f"\n  Total Time: {timedelta(seconds=int(elapsed))}")
    print(f"  Model saved to: {args.model_path}")
    print("=" * 70 + "\n")


if __name__ == '__main__':
    main()

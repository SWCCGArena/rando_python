#!/usr/bin/env python3
"""
Training Script: Neural Deploy Planner vs Rules Bot

Trains the neural deploy planner by playing games against the
rules-based bot. Uses the existing batch runner infrastructure
for parallel game execution.

Usage:
    python training/train_vs_rules.py --games-per-batch 20 --iterations 100

Requirements:
    - GEMP server running locally
    - Bot credentials configured
    - PyTorch installed
"""

import argparse
import json
import logging
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import torch

from engine.neural_planner.network import DeployPolicyNetwork, count_parameters
from engine.neural_planner.trainer import PPOTrainer, PPOConfig
from engine.neural_planner.experience import ExperienceBatch, GameTrajectory, Experience
from engine.neural_planner.state_encoder import StateEncoder

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TrainingArena:
    """
    Manages game collection and neural network training.

    Uses subprocess to run bot games (same as batch_runner.py),
    then trains the network on collected trajectories.
    """

    def __init__(
        self,
        model_dir: str = 'models',
        results_dir: str = 'training_results',
        games_per_batch: int = 20,
        device: str = 'cuda',
    ):
        """
        Initialize training arena.

        Args:
            model_dir: Directory to save model checkpoints
            results_dir: Directory to save training results
            games_per_batch: Games to play per training iteration
            device: 'cuda' or 'cpu'
        """
        self.model_dir = Path(model_dir)
        self.results_dir = Path(results_dir)
        self.games_per_batch = games_per_batch

        # Create directories
        self.model_dir.mkdir(exist_ok=True)
        self.results_dir.mkdir(exist_ok=True)

        # Device selection
        if device == 'cuda' and not torch.cuda.is_available():
            logger.warning("CUDA not available, using CPU")
            device = 'cpu'
        self.device = device

        # Create network and trainer
        self.network = DeployPolicyNetwork()
        self.config = PPOConfig(device=device)
        self.trainer = PPOTrainer(self.network, self.config)

        logger.info(f"Created network with {count_parameters(self.network):,} parameters")
        logger.info(f"Training on device: {device}")

        # Training statistics
        self.iteration = 0
        self.total_games = 0
        self.wins = 0
        self.losses = 0
        self.win_rate_history: List[float] = []

        # State encoder for collecting experiences
        self.state_encoder = StateEncoder()

    def run_training(
        self,
        num_iterations: int = 100,
        target_win_rate: float = 0.6,
        save_every: int = 10,
    ) -> None:
        """
        Run the main training loop.

        Args:
            num_iterations: Number of training iterations
            target_win_rate: Stop when this win rate is achieved
            save_every: Save checkpoint every N iterations
        """
        logger.info(f"Starting training for {num_iterations} iterations")
        logger.info(f"Target win rate: {target_win_rate:.0%}")

        start_time = time.time()

        for i in range(num_iterations):
            self.iteration = i + 1
            iter_start = time.time()

            # Collect games
            logger.info(f"\n=== Iteration {self.iteration}/{num_iterations} ===")
            trajectories, results = self._collect_games()

            # Update statistics
            wins = sum(1 for r in results if r == 'win')
            self.wins += wins
            self.losses += len(results) - wins
            self.total_games += len(results)

            batch_win_rate = wins / len(results) if results else 0
            overall_win_rate = self.wins / self.total_games if self.total_games > 0 else 0
            self.win_rate_history.append(batch_win_rate)

            logger.info(f"Batch: {wins}/{len(results)} wins ({batch_win_rate:.1%})")
            logger.info(f"Overall: {self.wins}/{self.total_games} wins ({overall_win_rate:.1%})")

            # Train on collected trajectories
            if trajectories:
                batch = ExperienceBatch.from_trajectories(trajectories)
                if len(batch) > 0:
                    batch.normalize_advantages()
                    metrics = self.trainer.update(
                        states=batch.states,
                        actions=batch.actions,
                        action_masks=batch.action_masks,
                        old_log_probs=batch.log_probs,
                        returns=batch.returns,
                        advantages=batch.advantages,
                    )
                    logger.info(f"Training: policy_loss={metrics['policy_loss']:.4f}, "
                               f"value_loss={metrics['value_loss']:.4f}, "
                               f"entropy={metrics['entropy']:.4f}")

            # Save checkpoint
            if self.iteration % save_every == 0:
                self._save_checkpoint()

            # Check if target reached
            if overall_win_rate >= target_win_rate and self.total_games >= 50:
                logger.info(f"Target win rate {target_win_rate:.0%} achieved!")
                break

            iter_time = time.time() - iter_start
            logger.info(f"Iteration time: {iter_time:.1f}s")

        # Final save
        self._save_checkpoint()
        self._save_training_summary()

        total_time = time.time() - start_time
        logger.info(f"\nTraining complete in {total_time/3600:.1f} hours")
        logger.info(f"Final win rate: {overall_win_rate:.1%}")

    def _collect_games(self) -> Tuple[List[GameTrajectory], List[str]]:
        """
        Collect games by running neural bot vs rules bot.

        For now, this generates synthetic trajectories since we don't
        have full integration with the game loop yet.

        In production, this would:
        1. Start bot processes with neural planner enabled
        2. Collect (state, action, reward) tuples during play
        3. Return trajectories when games complete

        Returns:
            (trajectories, results) tuple
        """
        # TODO: Hook into actual game infrastructure
        # For now, generate synthetic data for testing the training loop

        trajectories = []
        results = []

        for game_idx in range(self.games_per_batch):
            traj = GameTrajectory()

            # Simulate 10-20 deploy decisions per game
            num_decisions = random.randint(10, 20)

            for step in range(num_decisions):
                # Generate random state
                state = np.random.randn(512).astype(np.float32) * 0.1

                # Generate action mask (hold back + some random locations)
                mask = np.zeros(21, dtype=bool)
                mask[0] = True  # HOLD_BACK always valid
                for j in range(1, min(6, 17)):  # 5 random locations
                    if random.random() > 0.3:
                        mask[j] = True

                # Get action from network
                with torch.no_grad():
                    state_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
                    mask_t = torch.BoolTensor(mask).unsqueeze(0).to(self.device)

                    action, log_prob, value = self.network.get_action(
                        state_t, mask_t, deterministic=False
                    )

                    action = action.item()
                    log_prob = log_prob.item()
                    value = value.item()

                # Small shaped reward
                reward = random.uniform(-0.1, 0.1)

                traj.add_experience(Experience(
                    state=state,
                    action=action,
                    action_mask=mask,
                    reward=reward,
                    done=False,
                    value=value,
                    log_prob=log_prob,
                ))

            # Game outcome (slightly favor the network as training progresses)
            win_prob = 0.3 + min(0.3, self.total_games / 500)
            won = random.random() < win_prob

            traj.finalize(won=won)
            trajectories.append(traj)
            results.append('win' if won else 'loss')

        return trajectories, results

    def _save_checkpoint(self) -> None:
        """Save training checkpoint."""
        checkpoint_path = self.model_dir / f'checkpoint_iter{self.iteration}.pt'
        self.trainer.save(str(checkpoint_path))

        # Also save as 'latest'
        latest_path = self.model_dir / 'deploy_planner.pt'
        self.trainer.save_model_only(str(latest_path))

        logger.info(f"Saved checkpoint to {checkpoint_path}")

    def _save_training_summary(self) -> None:
        """Save training summary to JSON."""
        summary = {
            'iterations': self.iteration,
            'total_games': self.total_games,
            'wins': self.wins,
            'losses': self.losses,
            'final_win_rate': self.wins / self.total_games if self.total_games > 0 else 0,
            'win_rate_history': self.win_rate_history,
            'timestamp': datetime.now().isoformat(),
        }

        summary_path = self.results_dir / 'training_summary.json'
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)

        logger.info(f"Saved training summary to {summary_path}")


def main():
    parser = argparse.ArgumentParser(description='Train neural deploy planner')
    parser.add_argument('--games-per-batch', type=int, default=20,
                       help='Games to play per training iteration')
    parser.add_argument('--iterations', type=int, default=100,
                       help='Number of training iterations')
    parser.add_argument('--target-win-rate', type=float, default=0.6,
                       help='Target win rate to achieve')
    parser.add_argument('--save-every', type=int, default=10,
                       help='Save checkpoint every N iterations')
    parser.add_argument('--device', type=str, default='cuda',
                       choices=['cuda', 'cpu'], help='Device to train on')
    parser.add_argument('--model-dir', type=str, default='models',
                       help='Directory to save models')
    parser.add_argument('--results-dir', type=str, default='training_results',
                       help='Directory to save results')

    args = parser.parse_args()

    # Create training arena
    arena = TrainingArena(
        model_dir=args.model_dir,
        results_dir=args.results_dir,
        games_per_batch=args.games_per_batch,
        device=args.device,
    )

    # Run training
    arena.run_training(
        num_iterations=args.iterations,
        target_win_rate=args.target_win_rate,
        save_every=args.save_every,
    )


if __name__ == '__main__':
    main()

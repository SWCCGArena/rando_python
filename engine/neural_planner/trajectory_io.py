"""
Trajectory I/O for distributed training.

Saves and loads game trajectories to/from files.
This enables a simple training flow:
1. Bot saves trajectory to file at game end
2. Training script collects trajectory files
3. Training script trains on collected trajectories
4. Training script updates model weights file
5. Bot loads new weights for next game
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import numpy as np

from .experience import Experience, GameTrajectory

logger = logging.getLogger(__name__)

# Default trajectory directory
DEFAULT_TRAJECTORY_DIR = Path('training_data/trajectories')


def save_trajectory(
    trajectory: GameTrajectory,
    output_dir: Path = DEFAULT_TRAJECTORY_DIR,
    prefix: str = 'traj',
) -> Optional[Path]:
    """
    Save a game trajectory to a JSON file.

    Args:
        trajectory: Completed game trajectory
        output_dir: Directory to save trajectory
        prefix: Filename prefix

    Returns:
        Path to saved file, or None if save failed
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)

        # Create filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        result = 'win' if trajectory.won else 'loss'
        filename = f'{prefix}_{timestamp}_{result}.json'
        filepath = output_dir / filename

        # Convert trajectory to serializable format
        data = {
            'won': trajectory.won,
            'final_reward': trajectory.final_reward,
            'opponent_type': trajectory.opponent_type,
            'game_length': trajectory.game_length,
            'my_side': trajectory.my_side,
            'num_experiences': len(trajectory.experiences),
            'experiences': [
                {
                    'state': exp.state.tolist(),
                    'action': exp.action,
                    'action_mask': exp.action_mask.tolist(),
                    'reward': exp.reward,
                    'done': exp.done,
                    'value': exp.value,
                    'log_prob': exp.log_prob,
                    'turn': exp.turn,
                    'phase': exp.phase,
                }
                for exp in trajectory.experiences
            ],
        }

        with open(filepath, 'w') as f:
            json.dump(data, f)

        logger.info(f"Saved trajectory to {filepath}")
        return filepath

    except Exception as e:
        logger.error(f"Failed to save trajectory: {e}")
        return None


def load_trajectory(filepath: Path) -> Optional[GameTrajectory]:
    """
    Load a game trajectory from a JSON file.

    Args:
        filepath: Path to trajectory file

    Returns:
        GameTrajectory or None if load failed
    """
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)

        trajectory = GameTrajectory(
            won=data['won'],
            final_reward=data['final_reward'],
            opponent_type=data.get('opponent_type', 'rules'),
            game_length=data.get('game_length', 0),
            my_side=data.get('my_side', ''),
        )

        for exp_data in data['experiences']:
            exp = Experience(
                state=np.array(exp_data['state'], dtype=np.float32),
                action=exp_data['action'],
                action_mask=np.array(exp_data['action_mask'], dtype=bool),
                reward=exp_data['reward'],
                done=exp_data['done'],
                value=exp_data['value'],
                log_prob=exp_data['log_prob'],
                turn=exp_data.get('turn', 0),
                phase=exp_data.get('phase', ''),
            )
            trajectory.experiences.append(exp)

        return trajectory

    except Exception as e:
        logger.error(f"Failed to load trajectory from {filepath}: {e}")
        return None


def load_trajectories_from_dir(
    directory: Path = DEFAULT_TRAJECTORY_DIR,
    pattern: str = 'traj_*.json',
    limit: Optional[int] = None,
) -> List[GameTrajectory]:
    """
    Load all trajectories from a directory.

    Args:
        directory: Directory containing trajectory files
        pattern: Glob pattern for trajectory files
        limit: Maximum number to load (None = all)

    Returns:
        List of GameTrajectory objects
    """
    trajectories = []

    if not directory.exists():
        logger.warning(f"Trajectory directory does not exist: {directory}")
        return trajectories

    files = sorted(directory.glob(pattern))
    if limit:
        files = files[:limit]

    for filepath in files:
        traj = load_trajectory(filepath)
        if traj:
            trajectories.append(traj)

    logger.info(f"Loaded {len(trajectories)} trajectories from {directory}")
    return trajectories


def cleanup_old_trajectories(
    directory: Path = DEFAULT_TRAJECTORY_DIR,
    keep_last: int = 1000,
) -> int:
    """
    Remove old trajectory files, keeping only the most recent.

    Args:
        directory: Directory containing trajectory files
        keep_last: Number of recent files to keep

    Returns:
        Number of files deleted
    """
    if not directory.exists():
        return 0

    files = sorted(directory.glob('traj_*.json'))

    if len(files) <= keep_last:
        return 0

    to_delete = files[:-keep_last]
    deleted = 0

    for filepath in to_delete:
        try:
            filepath.unlink()
            deleted += 1
        except Exception as e:
            logger.warning(f"Failed to delete {filepath}: {e}")

    logger.info(f"Cleaned up {deleted} old trajectory files")
    return deleted

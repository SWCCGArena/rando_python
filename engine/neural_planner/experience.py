"""
Experience collection dataclasses for reinforcement learning.

Stores (state, action, reward, ...) tuples for PPO training.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import numpy as np

from .state_encoder import STATE_DIM, NUM_ACTIONS


@dataclass
class Experience:
    """
    Single step experience for training.

    Represents one decision point in a game with all information
    needed for PPO updates.
    """
    # State at decision time
    state: np.ndarray  # [STATE_DIM] state features

    # Action taken
    action: int  # Action index (0-20)
    action_mask: np.ndarray  # [21] valid action mask

    # Reward received (may be shaped or sparse)
    reward: float

    # Episode termination
    done: bool  # True if game ended after this action

    # Value estimates (for GAE)
    value: float  # Critic's value estimate at this state
    log_prob: float  # Log probability of the action taken

    # Additional metadata for debugging
    turn: int = 0
    phase: str = ""


@dataclass
class GameTrajectory:
    """
    Complete trajectory for one game.

    Contains all experiences from a single game, plus
    game-level metadata for reward attribution.
    """
    # All experiences in order
    experiences: List[Experience] = field(default_factory=list)

    # Game outcome
    won: bool = False
    final_reward: float = 0.0  # +1 win, -1 loss, 0 draw

    # Metadata
    opponent_type: str = "rules"  # "rules" or "self"
    game_length: int = 0  # Number of turns
    my_side: str = ""  # "dark" or "light"

    def add_experience(self, exp: Experience) -> None:
        """Add an experience to the trajectory."""
        self.experiences.append(exp)

    def finalize(self, won: bool) -> None:
        """
        Finalize trajectory with game outcome.

        Sets final reward and propagates to last experience.
        """
        self.won = won
        self.final_reward = 1.0 if won else -1.0
        self.game_length = len(self.experiences)

        # Mark last experience as terminal
        if self.experiences:
            self.experiences[-1].done = True
            # Add final game outcome to last experience reward
            self.experiences[-1].reward += self.final_reward

    def compute_returns(
        self,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
    ) -> tuple:
        """
        Compute returns and advantages using GAE.

        Args:
            gamma: Discount factor
            gae_lambda: GAE lambda parameter

        Returns:
            (returns, advantages) as numpy arrays
        """
        n = len(self.experiences)
        if n == 0:
            return np.array([]), np.array([])

        returns = np.zeros(n, dtype=np.float32)
        advantages = np.zeros(n, dtype=np.float32)

        # Compute GAE backwards
        last_gae = 0.0
        last_value = 0.0  # Bootstrap value (0 for terminal states)

        for t in reversed(range(n)):
            exp = self.experiences[t]

            if exp.done:
                next_value = 0.0
                last_gae = 0.0
            else:
                next_value = self.experiences[t + 1].value if t + 1 < n else last_value

            # TD error
            delta = exp.reward + gamma * next_value - exp.value

            # GAE
            last_gae = delta + gamma * gae_lambda * last_gae
            advantages[t] = last_gae

            # Returns = advantages + values
            returns[t] = advantages[t] + exp.value

        return returns, advantages


@dataclass
class ExperienceBatch:
    """
    Batch of experiences for training.

    Collated from multiple trajectories with computed returns/advantages.
    """
    states: np.ndarray  # [batch, STATE_DIM]
    actions: np.ndarray  # [batch]
    action_masks: np.ndarray  # [batch, 21]
    log_probs: np.ndarray  # [batch]
    returns: np.ndarray  # [batch]
    advantages: np.ndarray  # [batch]
    values: np.ndarray  # [batch]

    @classmethod
    def from_trajectories(
        cls,
        trajectories: List[GameTrajectory],
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
    ) -> 'ExperienceBatch':
        """
        Create batch from multiple game trajectories.

        Args:
            trajectories: List of completed game trajectories
            gamma: Discount factor for return computation
            gae_lambda: GAE lambda for advantage estimation

        Returns:
            ExperienceBatch ready for training
        """
        all_states = []
        all_actions = []
        all_masks = []
        all_log_probs = []
        all_returns = []
        all_advantages = []
        all_values = []

        for traj in trajectories:
            if not traj.experiences:
                continue

            # Compute returns and advantages
            returns, advantages = traj.compute_returns(gamma, gae_lambda)

            # Collect data
            for i, exp in enumerate(traj.experiences):
                all_states.append(exp.state)
                all_actions.append(exp.action)
                all_masks.append(exp.action_mask)
                all_log_probs.append(exp.log_prob)
                all_returns.append(returns[i])
                all_advantages.append(advantages[i])
                all_values.append(exp.value)

        # Stack into arrays
        return cls(
            states=np.stack(all_states) if all_states else np.zeros((0, STATE_DIM)),
            actions=np.array(all_actions, dtype=np.int64),
            action_masks=np.stack(all_masks) if all_masks else np.zeros((0, NUM_ACTIONS)),
            log_probs=np.array(all_log_probs, dtype=np.float32),
            returns=np.array(all_returns, dtype=np.float32),
            advantages=np.array(all_advantages, dtype=np.float32),
            values=np.array(all_values, dtype=np.float32),
        )

    def __len__(self) -> int:
        return len(self.actions)

    def normalize_advantages(self) -> None:
        """Normalize advantages to have zero mean and unit variance."""
        if len(self.advantages) > 1:
            mean = self.advantages.mean()
            std = self.advantages.std() + 1e-8
            self.advantages = (self.advantages - mean) / std

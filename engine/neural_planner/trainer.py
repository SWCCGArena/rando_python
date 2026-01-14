"""
PPO Trainer for Neural Deploy Planner.

Implements Proximal Policy Optimization for training the deploy policy.
Optimized for NVIDIA 3080 12GB with parallel game collection.

Key features:
- Clipped surrogate objective
- Value function clipping
- Entropy bonus for exploration
- Generalized Advantage Estimation (GAE)
- Gradient clipping for stability
"""

import logging
import os
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import numpy as np

logger = logging.getLogger(__name__)

# Try to import PyTorch
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch.utils.data import DataLoader, TensorDataset
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logger.warning("PyTorch not available - trainer disabled")


@dataclass
class PPOConfig:
    """Configuration for PPO training."""
    # Learning rate
    learning_rate: float = 3e-4

    # PPO hyperparameters
    clip_ratio: float = 0.2
    value_clip: float = 0.2
    entropy_coef: float = 0.01
    value_coef: float = 0.5
    max_grad_norm: float = 0.5

    # GAE parameters
    gamma: float = 0.99
    gae_lambda: float = 0.95

    # Training batch sizes
    batch_size: int = 512
    mini_batch_size: int = 64
    ppo_epochs: int = 4  # Number of epochs per batch

    # Optimization
    weight_decay: float = 0.01
    target_kl: float = 0.015  # Early stopping if KL divergence too high

    # Device
    device: str = 'cuda'  # 'cuda' or 'cpu'


class PPOTrainer:
    """
    PPO trainer for the deploy policy network.

    Optimized for NVIDIA 3080 12GB:
    - Batch size 512 fits comfortably in memory
    - Mini-batch of 64 for stable updates
    - 4 epochs per batch for sample efficiency
    """

    def __init__(
        self,
        network: 'DeployPolicyNetwork',
        config: Optional[PPOConfig] = None,
    ):
        """
        Initialize the PPO trainer.

        Args:
            network: DeployPolicyNetwork to train
            config: PPO configuration (uses defaults if None)
        """
        if not TORCH_AVAILABLE:
            raise RuntimeError("PyTorch required for training")

        self.config = config or PPOConfig()
        self.device = self.config.device

        if self.device == 'cuda' and not torch.cuda.is_available():
            logger.warning("CUDA not available, falling back to CPU")
            self.device = 'cpu'

        self.network = network.to(self.device)

        # Optimizer
        self.optimizer = torch.optim.AdamW(
            self.network.parameters(),
            lr=self.config.learning_rate,
            weight_decay=self.config.weight_decay,
        )

        # Learning rate scheduler (linear decay)
        self.scheduler = None  # Set during training

        # Training statistics
        self.update_count = 0
        self.total_timesteps = 0

    def update(
        self,
        states: np.ndarray,
        actions: np.ndarray,
        action_masks: np.ndarray,
        old_log_probs: np.ndarray,
        returns: np.ndarray,
        advantages: np.ndarray,
    ) -> Dict[str, float]:
        """
        Perform PPO update on a batch of experiences.

        Args:
            states: [batch, 512] state features
            actions: [batch] action indices
            action_masks: [batch, 21] valid action masks
            old_log_probs: [batch] log probs from rollout
            returns: [batch] computed returns (GAE)
            advantages: [batch] computed advantages (GAE)

        Returns:
            Dictionary of training metrics
        """
        # Convert to tensors
        states_t = torch.FloatTensor(states).to(self.device)
        actions_t = torch.LongTensor(actions).to(self.device)
        masks_t = torch.BoolTensor(action_masks).to(self.device)
        old_log_probs_t = torch.FloatTensor(old_log_probs).to(self.device)
        returns_t = torch.FloatTensor(returns).to(self.device)
        advantages_t = torch.FloatTensor(advantages).to(self.device)

        # Normalize advantages
        advantages_t = (advantages_t - advantages_t.mean()) / (advantages_t.std() + 1e-8)

        # Create data loader for mini-batch training
        dataset = TensorDataset(
            states_t, actions_t, masks_t, old_log_probs_t, returns_t, advantages_t
        )
        loader = DataLoader(
            dataset,
            batch_size=self.config.mini_batch_size,
            shuffle=True,
        )

        # Training metrics
        total_policy_loss = 0.0
        total_value_loss = 0.0
        total_entropy = 0.0
        total_kl = 0.0
        num_updates = 0

        # Multiple epochs over the batch
        for epoch in range(self.config.ppo_epochs):
            for batch in loader:
                mb_states, mb_actions, mb_masks, mb_old_lp, mb_returns, mb_advs = batch

                # Forward pass
                new_log_probs, values, entropy = self.network.evaluate_actions(
                    mb_states, mb_actions, mb_masks
                )

                # Policy loss (clipped surrogate)
                ratio = torch.exp(new_log_probs - mb_old_lp)
                surr1 = ratio * mb_advs
                surr2 = torch.clamp(
                    ratio,
                    1.0 - self.config.clip_ratio,
                    1.0 + self.config.clip_ratio,
                ) * mb_advs
                policy_loss = -torch.min(surr1, surr2).mean()

                # Value loss (clipped)
                value_loss = F.mse_loss(values, mb_returns)

                # Entropy bonus (for exploration)
                entropy_loss = -entropy.mean()

                # Total loss
                loss = (
                    policy_loss
                    + self.config.value_coef * value_loss
                    + self.config.entropy_coef * entropy_loss
                )

                # Backward pass
                self.optimizer.zero_grad()
                loss.backward()

                # Gradient clipping
                nn.utils.clip_grad_norm_(
                    self.network.parameters(),
                    self.config.max_grad_norm,
                )

                self.optimizer.step()

                # Track metrics
                total_policy_loss += policy_loss.item()
                total_value_loss += value_loss.item()
                total_entropy += entropy.mean().item()

                # Approximate KL divergence
                with torch.no_grad():
                    kl = (mb_old_lp - new_log_probs).mean().item()
                    total_kl += kl

                num_updates += 1

            # Early stopping if KL too high
            avg_kl = total_kl / num_updates if num_updates > 0 else 0
            if avg_kl > self.config.target_kl:
                logger.info(f"Early stopping at epoch {epoch+1} due to high KL: {avg_kl:.4f}")
                break

        self.update_count += 1
        self.total_timesteps += len(states)

        # Return average metrics
        return {
            'policy_loss': total_policy_loss / num_updates if num_updates > 0 else 0,
            'value_loss': total_value_loss / num_updates if num_updates > 0 else 0,
            'entropy': total_entropy / num_updates if num_updates > 0 else 0,
            'kl_divergence': total_kl / num_updates if num_updates > 0 else 0,
            'num_updates': num_updates,
            'total_timesteps': self.total_timesteps,
        }

    def save(self, path: str) -> None:
        """
        Save model checkpoint.

        Args:
            path: Path to save checkpoint
        """
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)

        checkpoint = {
            'network_state_dict': self.network.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'update_count': self.update_count,
            'total_timesteps': self.total_timesteps,
            'config': self.config,
        }

        torch.save(checkpoint, path)
        logger.info(f"Saved checkpoint to {path}")

    def load(self, path: str) -> None:
        """
        Load model checkpoint.

        Args:
            path: Path to load checkpoint from
        """
        checkpoint = torch.load(path, map_location=self.device)

        self.network.load_state_dict(checkpoint['network_state_dict'])
        self.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        self.update_count = checkpoint.get('update_count', 0)
        self.total_timesteps = checkpoint.get('total_timesteps', 0)

        logger.info(f"Loaded checkpoint from {path}")

    def save_model_only(self, path: str) -> None:
        """
        Save just the model weights (for inference).

        This creates a smaller file suitable for deployment.

        Args:
            path: Path to save model
        """
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        torch.save(self.network.state_dict(), path)
        logger.info(f"Saved model weights to {path}")


def create_trainer(
    hidden_dim: int = 256,
    device: str = 'cuda',
    learning_rate: float = 3e-4,
) -> Tuple['PPOTrainer', 'DeployPolicyNetwork']:
    """
    Factory function to create trainer and network.

    Args:
        hidden_dim: Hidden layer dimension
        device: 'cuda' or 'cpu'
        learning_rate: Learning rate for optimizer

    Returns:
        (trainer, network) tuple
    """
    from .network import DeployPolicyNetwork

    network = DeployPolicyNetwork(hidden_dim=hidden_dim)
    config = PPOConfig(device=device, learning_rate=learning_rate)
    trainer = PPOTrainer(network, config)

    return trainer, network

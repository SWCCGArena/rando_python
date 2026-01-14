"""
Neural Network Architecture for Deploy Planner.

Enhanced Actor-Critic architecture with attention mechanisms:
- Separate encoders for global state, locations, and cards
- Location self-attention to learn spatial relationships
- Card self-attention to learn card relationships
- Cross-attention for cards to attend to locations (where should each card go?)
- Combined representation for policy and value heads

This architecture explicitly models the structure of the decision:
"Which of my cards should I deploy to which location?"
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple, Optional

# Match state encoder dimensions
STATE_DIM = 640
NUM_ACTIONS = 21

# Architecture constants
GLOBAL_FEATURES = 64
NUM_LOCATIONS = 16
LOCATION_FEATURES = 24
NUM_CARDS = 8
CARD_FEATURES = 20
HAND_AGGREGATE_FEATURES = 32

# Default hidden dimension (larger for more capacity)
HIDDEN_DIM = 384


class DeployPolicyNetwork(nn.Module):
    """
    Enhanced Actor-Critic network with structured attention.

    Architecture:
    1. Global Encoder: MLP on global game state features
    2. Location Encoder: Embed + self-attention over 16 locations
    3. Card Encoder: Embed + self-attention over 8 cards in hand
    4. Cross-Attention: Cards attend to locations (learn card-location matching)
    5. Aggregation: Pool representations and combine
    6. Policy/Value heads: Output action logits and state value

    Input: [batch, 640] state tensor
    Output: [batch, 21] action logits, [batch, 1] state value
    """

    def __init__(
        self,
        state_dim: int = STATE_DIM,
        action_dim: int = NUM_ACTIONS,
        hidden_dim: int = HIDDEN_DIM,
        num_heads: int = 4,
        dropout: float = 0.1,
    ):
        """
        Initialize the network.

        Args:
            state_dim: Dimension of state input (default 640)
            action_dim: Number of possible actions (default 21)
            hidden_dim: Hidden layer dimension (default 384)
            num_heads: Number of attention heads (default 4)
            dropout: Dropout rate for training (default 0.1)
        """
        super().__init__()

        self.state_dim = state_dim
        self.action_dim = action_dim
        self.hidden_dim = hidden_dim

        # === Global State Encoder ===
        # Processes: global features [0:64] + hand aggregate [448:480]
        global_input_dim = GLOBAL_FEATURES + HAND_AGGREGATE_FEATURES
        self.global_encoder = nn.Sequential(
            nn.Linear(global_input_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
        )

        # === Location Encoder ===
        # Each location is a token with 24 features
        self.location_embed = nn.Sequential(
            nn.Linear(LOCATION_FEATURES, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
        )

        # Learnable position embeddings for locations
        self.location_pos_embed = nn.Parameter(torch.randn(NUM_LOCATIONS, hidden_dim) * 0.02)

        # Self-attention over locations
        self.location_attention = nn.MultiheadAttention(
            embed_dim=hidden_dim,
            num_heads=num_heads,
            dropout=dropout,
            batch_first=True,
        )
        self.location_norm = nn.LayerNorm(hidden_dim)

        # === Card Encoder ===
        # Each card is a token with 20 features
        self.card_embed = nn.Sequential(
            nn.Linear(CARD_FEATURES, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
        )

        # Learnable position embeddings for card slots (sorted by power)
        self.card_pos_embed = nn.Parameter(torch.randn(NUM_CARDS, hidden_dim) * 0.02)

        # Self-attention over cards
        self.card_attention = nn.MultiheadAttention(
            embed_dim=hidden_dim,
            num_heads=num_heads,
            dropout=dropout,
            batch_first=True,
        )
        self.card_norm = nn.LayerNorm(hidden_dim)

        # === Cross-Attention: Cards attend to Locations ===
        # This learns "where should each card go?"
        self.cross_attention = nn.MultiheadAttention(
            embed_dim=hidden_dim,
            num_heads=num_heads,
            dropout=dropout,
            batch_first=True,
        )
        self.cross_norm = nn.LayerNorm(hidden_dim)

        # === Aggregation ===
        # Pool location and card representations
        self.location_pool = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
        )
        self.card_pool = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
        )

        # === Combination Layer ===
        # Combines global + pooled locations + pooled cards
        combine_dim = hidden_dim + (hidden_dim // 2) * 2  # global + loc_pool + card_pool
        self.combine = nn.Sequential(
            nn.Linear(combine_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
        )

        # === Policy Head (Actor) ===
        self.policy = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Linear(hidden_dim // 2, action_dim),
        )

        # === Value Head (Critic) ===
        self.value = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Linear(hidden_dim // 2, 1),
        )

        # Initialize weights
        self._init_weights()

    def _init_weights(self):
        """Initialize network weights for stable training."""
        for module in self.modules():
            if isinstance(module, nn.Linear):
                nn.init.orthogonal_(module.weight, gain=1.0)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)

        # Smaller initialization for output layers
        nn.init.orthogonal_(self.policy[-1].weight, gain=0.01)
        nn.init.orthogonal_(self.value[-1].weight, gain=1.0)

    def _parse_state(self, state: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Parse flat state tensor into structured components.

        Args:
            state: [batch, 640] flat state tensor

        Returns:
            global_features: [batch, 96] global + hand aggregate
            location_features: [batch, 16, 24] per-location features
            card_features: [batch, 8, 20] per-card features
        """
        batch_size = state.shape[0]

        # Global features [0:64]
        global_feat = state[:, :GLOBAL_FEATURES]

        # Location features [64:448] -> reshape to [batch, 16, 24]
        loc_start = GLOBAL_FEATURES
        loc_end = loc_start + NUM_LOCATIONS * LOCATION_FEATURES
        location_feat = state[:, loc_start:loc_end].view(batch_size, NUM_LOCATIONS, LOCATION_FEATURES)

        # Hand aggregate features [448:480]
        agg_start = loc_end
        agg_end = agg_start + HAND_AGGREGATE_FEATURES
        hand_agg = state[:, agg_start:agg_end]

        # Per-card features [480:640] -> reshape to [batch, 8, 20]
        card_start = agg_end
        card_feat = state[:, card_start:].view(batch_size, NUM_CARDS, CARD_FEATURES)

        # Combine global and hand aggregate
        global_combined = torch.cat([global_feat, hand_agg], dim=-1)

        return global_combined, location_feat, card_feat

    def forward(
        self,
        state: torch.Tensor,
        action_mask: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Forward pass with structured attention.

        Args:
            state: [batch, 640] state tensor
            action_mask: [batch, 21] boolean mask (True = valid action)

        Returns:
            policy_logits: [batch, 21] pre-softmax action logits
            value: [batch, 1] state value estimate
        """
        # Parse state into components
        global_feat, location_feat, card_feat = self._parse_state(state)

        # === Encode Global State ===
        global_enc = self.global_encoder(global_feat)  # [batch, hidden]

        # === Encode Locations with Self-Attention ===
        loc_embedded = self.location_embed(location_feat)  # [batch, 16, hidden]
        loc_embedded = loc_embedded + self.location_pos_embed  # Add position info

        # Self-attention over locations
        loc_attn, _ = self.location_attention(loc_embedded, loc_embedded, loc_embedded)
        loc_enc = self.location_norm(loc_embedded + loc_attn)  # [batch, 16, hidden]

        # === Encode Cards with Self-Attention ===
        card_embedded = self.card_embed(card_feat)  # [batch, 8, hidden]
        card_embedded = card_embedded + self.card_pos_embed  # Add position info

        # Self-attention over cards
        card_attn, _ = self.card_attention(card_embedded, card_embedded, card_embedded)
        card_enc = self.card_norm(card_embedded + card_attn)  # [batch, 8, hidden]

        # === Cross-Attention: Cards attend to Locations ===
        # Query: cards, Key/Value: locations
        # This learns which locations each card should consider
        cross_attn, _ = self.cross_attention(card_enc, loc_enc, loc_enc)
        card_loc_enc = self.cross_norm(card_enc + cross_attn)  # [batch, 8, hidden]

        # === Pool Representations ===
        # Mean pooling over locations and cards
        loc_pooled = self.location_pool(loc_enc.mean(dim=1))  # [batch, hidden//2]
        card_pooled = self.card_pool(card_loc_enc.mean(dim=1))  # [batch, hidden//2]

        # === Combine All Features ===
        combined = torch.cat([global_enc, loc_pooled, card_pooled], dim=-1)
        features = self.combine(combined)  # [batch, hidden]

        # === Policy and Value Outputs ===
        logits = self.policy(features)  # [batch, action_dim]
        value = self.value(features)  # [batch, 1]

        # Apply action mask
        if action_mask is not None:
            logits = logits.masked_fill(~action_mask, float('-inf'))

        return logits, value

    def get_action(
        self,
        state: torch.Tensor,
        action_mask: Optional[torch.Tensor] = None,
        deterministic: bool = False,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Sample an action from the policy.

        Args:
            state: [batch, 640] state tensor
            action_mask: [batch, 21] boolean mask
            deterministic: If True, return argmax action. If False, sample.

        Returns:
            action: [batch] selected action indices
            log_prob: [batch] log probability of selected actions
            value: [batch] state value estimates
        """
        logits, value = self.forward(state, action_mask)

        # Clamp logits for numerical stability
        logits = torch.clamp(logits, min=-20, max=20)

        # Convert to probabilities with numerical stability
        stable_logits = torch.where(
            torch.isinf(logits),
            torch.full_like(logits, -1e9),
            logits
        )
        probs = F.softmax(stable_logits, dim=-1)

        # Clamp probabilities
        probs = torch.clamp(probs, min=1e-8, max=1.0)
        probs = probs / probs.sum(dim=-1, keepdim=True)

        if deterministic:
            action = probs.argmax(dim=-1)
        else:
            dist = torch.distributions.Categorical(probs)
            action = dist.sample()

        # Calculate log probability
        log_prob = torch.log(probs + 1e-8)
        log_prob = log_prob.gather(1, action.unsqueeze(-1)).squeeze(-1)

        return action, log_prob, value.squeeze(-1)

    def evaluate_actions(
        self,
        state: torch.Tensor,
        action: torch.Tensor,
        action_mask: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Evaluate actions for PPO training.

        Args:
            state: [batch, 640] state tensor
            action: [batch] action indices that were taken
            action_mask: [batch, 21] boolean mask

        Returns:
            log_prob: [batch] log probability of the actions
            value: [batch] state value estimates
            entropy: [batch] entropy of the action distribution
        """
        logits, value = self.forward(state, action_mask)

        # Clamp logits
        logits = torch.clamp(logits, min=-20, max=20)

        # Stable softmax
        stable_logits = torch.where(
            torch.isinf(logits),
            torch.full_like(logits, -1e9),
            logits
        )
        probs = F.softmax(stable_logits, dim=-1)
        probs = torch.clamp(probs, min=1e-8, max=1.0)
        probs = probs / probs.sum(dim=-1, keepdim=True)

        # Log probabilities
        log_probs = torch.log(probs + 1e-8)
        action_log_prob = log_probs.gather(1, action.unsqueeze(-1)).squeeze(-1)

        # Entropy
        entropy = -(probs * log_probs).sum(dim=-1)

        # Clamp outputs
        action_log_prob = torch.clamp(action_log_prob, min=-20, max=0)
        entropy = torch.clamp(entropy, min=0, max=20)

        return action_log_prob, value.squeeze(-1), entropy

    def get_value(self, state: torch.Tensor) -> torch.Tensor:
        """
        Get state value estimate only (for GAE calculation).

        Args:
            state: [batch, 640] state tensor

        Returns:
            value: [batch] state value estimates
        """
        _, value = self.forward(state)
        return value.squeeze(-1)


def count_parameters(model: nn.Module) -> int:
    """Count trainable parameters in a model."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def get_model_size_mb(model: nn.Module) -> float:
    """Get model size in megabytes."""
    param_size = sum(p.numel() * p.element_size() for p in model.parameters())
    buffer_size = sum(b.numel() * b.element_size() for b in model.buffers())
    return (param_size + buffer_size) / (1024 * 1024)


if __name__ == "__main__":
    # Quick test
    print("Testing Enhanced DeployPolicyNetwork")
    print("=" * 50)

    model = DeployPolicyNetwork()
    print(f"Parameters: {count_parameters(model):,}")
    print(f"Model size: {get_model_size_mb(model):.2f} MB")

    # Test forward pass
    state = torch.randn(4, STATE_DIM)
    mask = torch.ones(4, NUM_ACTIONS, dtype=torch.bool)
    mask[:, 5:10] = False  # Mask out some actions

    logits, value = model(state, mask)
    print(f"\nForward pass:")
    print(f"  Logits shape: {logits.shape}")
    print(f"  Value shape: {value.shape}")

    # Test action sampling
    action, log_prob, val = model.get_action(state, mask)
    print(f"\nAction sampling:")
    print(f"  Action shape: {action.shape}")
    print(f"  Log prob shape: {log_prob.shape}")

    # Benchmark inference speed
    import time

    model.eval()
    times = []
    for _ in range(100):
        start = time.perf_counter()
        with torch.no_grad():
            model(state, mask)
        times.append((time.perf_counter() - start) * 1000)

    print(f"\nInference speed: {sum(times)/len(times):.2f}ms avg")
    print("=" * 50)

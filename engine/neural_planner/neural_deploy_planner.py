"""
Neural Deploy Planner - Drop-in replacement for rules-based DeployPhasePlanner.

Uses a neural network trained via PPO to make deployment decisions.
Designed for:
- Training on powerful hardware (GPU)
- Inference on low-resource machines (CPU only, 4GB RAM)

Key features:
- Same interface as DeployPhasePlanner: create_plan(board_state) -> DeploymentPlan
- Confidence-based fallback to rules-based planner
- ONNX export support for optimized CPU inference
"""

import logging
import os
from typing import Any, Optional, Tuple

import numpy as np

from engine.deploy_planner import DeploymentPlan, DeployStrategy

from .state_encoder import StateEncoder, NUM_ACTIONS
from .action_decoder import ActionDecoder

logger = logging.getLogger(__name__)

# Try to import PyTorch - may not be available on production server
try:
    import torch
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logger.warning("PyTorch not available - neural planner will use fallback")


class NeuralDeployPlanner:
    """
    Neural network-based deploy planner.

    Drop-in replacement for DeployPhasePlanner that:
    1. Uses same interface: create_plan(board_state) -> DeploymentPlan
    2. Returns same DeploymentPlan/DeploymentInstruction structures
    3. Can fall back to rules-based planner if confidence is low

    Example usage:
        planner = NeuralDeployPlanner(model_path='models/deploy_planner.pt')
        plan = planner.create_plan(board_state)
    """

    def __init__(
        self,
        model_path: str = 'models/deploy_planner.pt',
        fallback_planner: Any = None,
        confidence_threshold: float = 0.3,
        use_cpu: bool = True,
    ):
        """
        Initialize neural deploy planner.

        Args:
            model_path: Path to trained PyTorch model (.pt file)
            fallback_planner: Optional rules-based planner for low-confidence fallback
            confidence_threshold: Minimum confidence to use neural decision (0-1)
            use_cpu: Force CPU inference (recommended for production)
        """
        self.model_path = model_path
        self.fallback_planner = fallback_planner
        self.confidence_threshold = confidence_threshold
        self.use_cpu = use_cpu

        # Initialize components
        self.state_encoder = StateEncoder()
        self.action_decoder = ActionDecoder()

        # Track current plan state (matches DeployPhasePlanner interface)
        self.current_plan: Optional[DeploymentPlan] = None
        self._plan_turn: int = -1
        self._plan_phase: str = ""

        # Load network if available
        self.network = None
        self.device = 'cpu'
        self._load_model()

    def _load_model(self) -> None:
        """Load the neural network model."""
        if not TORCH_AVAILABLE:
            logger.warning("PyTorch not available, neural planner disabled")
            return

        if not os.path.exists(self.model_path):
            logger.info(f"Model not found at {self.model_path}, neural planner will use random policy")
            return

        try:
            # Import network class
            from .network import DeployPolicyNetwork

            # Determine device
            if self.use_cpu:
                self.device = 'cpu'
            else:
                self.device = 'cuda' if torch.cuda.is_available() else 'cpu'

            # Load model
            self.network = DeployPolicyNetwork()
            state_dict = torch.load(self.model_path, map_location=self.device)
            self.network.load_state_dict(state_dict)
            self.network.to(self.device)
            self.network.eval()

            logger.info(f"Loaded neural deploy planner from {self.model_path} (device={self.device})")

        except Exception as e:
            logger.error(f"Failed to load neural model: {e}")
            self.network = None

    def create_plan(self, board_state: Any) -> DeploymentPlan:
        """
        Create deployment plan using neural network.

        Interface matches DeployPhasePlanner.create_plan() exactly.

        Args:
            board_state: BoardState or MockBoardState object

        Returns:
            DeploymentPlan with strategy and instructions
        """
        # Check if we can reuse cached plan (same turn/phase)
        turn = getattr(board_state, 'turn_number', 0)
        phase = getattr(board_state, 'current_phase', '')

        if self.current_plan and turn == self._plan_turn and phase == self._plan_phase:
            return self.current_plan

        # Generate new plan
        if self.network is None or not TORCH_AVAILABLE:
            # No neural network - use fallback or random
            return self._fallback_plan(board_state)

        try:
            action, confidence = self._get_neural_action(board_state)

            # Check confidence threshold
            if confidence < self.confidence_threshold:
                logger.info(
                    f"🧠 Neural confidence {confidence:.2f} < {self.confidence_threshold}, "
                    f"using fallback"
                )
                return self._fallback_plan(board_state)

            # Decode action to plan
            plan = self.action_decoder.decode(
                action=action,
                board_state=board_state,
                confidence=confidence,
            )

            # Cache plan
            self.current_plan = plan
            self._plan_turn = turn
            self._plan_phase = phase

            logger.info(f"🧠 Neural plan: {plan.strategy.value} (confidence={confidence:.2f})")
            return plan

        except Exception as e:
            logger.error(f"Neural planning failed: {e}")
            return self._fallback_plan(board_state)

    def _get_neural_action(self, board_state: Any) -> Tuple[int, float]:
        """
        Run neural network forward pass to get action.

        Returns:
            (action_index, confidence) tuple
        """
        import torch

        # Encode state
        state_np = self.state_encoder.encode(board_state)
        mask_np = self.state_encoder.get_action_mask(board_state)

        # Convert to tensors
        state_t = torch.FloatTensor(state_np).unsqueeze(0).to(self.device)
        mask_t = torch.BoolTensor(mask_np).unsqueeze(0).to(self.device)

        # Forward pass
        with torch.no_grad():
            logits, value = self.network(state_t, mask_t)
            probs = F.softmax(logits, dim=-1)

            # Get best action
            action = probs.argmax(dim=-1).item()
            confidence = probs[0, action].item()

        return action, confidence

    def _fallback_plan(self, board_state: Any) -> DeploymentPlan:
        """Use fallback planner or create a default hold plan."""
        if self.fallback_planner:
            return self.fallback_planner.create_plan(board_state)

        # Default to hold back
        return DeploymentPlan(
            strategy=DeployStrategy.HOLD_BACK,
            reason="Neural: no model loaded, holding back",
            instructions=[],
            hold_back_cards=set(),
            target_locations=[],
            total_force_available=getattr(board_state, 'force_pile', 0),
            force_reserved_for_battle=1,
            force_to_spend=0,
        )

    # =========================================================================
    # DeployPhasePlanner Interface Methods
    # These match the interface expected by DeployEvaluator
    # =========================================================================

    def get_card_score(
        self,
        blueprint_id: str,
        current_force: int,
        available_blueprint_ids: set,
    ) -> Tuple[float, str]:
        """
        Get score for deploying a specific card.

        Called by DeployEvaluator to score individual deploy actions
        against the current plan.

        Args:
            blueprint_id: Card blueprint ID
            current_force: Current force available
            available_blueprint_ids: Set of cards that can be deployed

        Returns:
            (score, reason) tuple
        """
        if self.current_plan is None:
            return 0.0, "No plan available"

        if self.current_plan.strategy == DeployStrategy.HOLD_BACK:
            return -100.0, "Plan: HOLD_BACK"

        # Check if card is in plan
        instruction = self.current_plan.get_instruction_for_card(blueprint_id)
        if instruction:
            return 50.0, f"Plan: deploy {instruction.card_name} to {instruction.target_location_name}"

        # Card not in plan
        if self.current_plan.force_allow_extras:
            return 0.0, "Not in plan, but extras allowed"

        return -20.0, "Not in plan"

    def record_deployment(self, blueprint_id: str) -> None:
        """
        Record that a card was deployed.

        Called by DeployEvaluator after a successful deployment.

        Args:
            blueprint_id: Card that was deployed
        """
        if self.current_plan is None:
            return

        # Remove instruction for this card
        self.current_plan.instructions = [
            inst for inst in self.current_plan.instructions
            if inst.card_blueprint_id != blueprint_id
        ]
        self.current_plan.deployments_made += 1

    def should_hold_back(self) -> bool:
        """Check if current plan is HOLD_BACK."""
        if self.current_plan is None:
            return False
        return self.current_plan.strategy == DeployStrategy.HOLD_BACK

    def get_plan_summary(self) -> str:
        """Get human-readable summary of current plan."""
        if self.current_plan is None:
            return "No plan"

        plan = self.current_plan
        lines = [f"Strategy: {plan.strategy.value}"]
        lines.append(f"Reason: {plan.reason}")

        if plan.instructions:
            lines.append(f"Instructions ({len(plan.instructions)}):")
            for inst in plan.instructions[:5]:  # Limit to first 5
                lines.append(f"  - {inst.card_name} -> {inst.target_location_name}")

        return "\n".join(lines)

    def reset(self) -> None:
        """Reset planner state (for new game)."""
        self.current_plan = None
        self._plan_turn = -1
        self._plan_phase = ""


def create_neural_planner(
    model_path: str = 'models/deploy_planner.pt',
    fallback_to_rules: bool = True,
) -> NeuralDeployPlanner:
    """
    Factory function to create a neural deploy planner.

    Args:
        model_path: Path to trained model
        fallback_to_rules: If True, create rules-based fallback

    Returns:
        Configured NeuralDeployPlanner instance
    """
    fallback = None
    if fallback_to_rules:
        from engine.deploy_planner import DeployPhasePlanner
        import config
        fallback = DeployPhasePlanner(
            deploy_threshold=config.DEPLOY_THRESHOLD,
            battle_force_reserve=config.BATTLE_FORCE_RESERVE,
        )

    return NeuralDeployPlanner(
        model_path=model_path,
        fallback_planner=fallback,
    )

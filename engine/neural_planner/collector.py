"""
Experience Collector for Neural Deploy Planner Training.

Hooks into the game loop to collect (state, action, reward) tuples
during actual gameplay against the rules-based bot.

Usage:
    collector = ExperienceCollector(network, device='cuda')

    # During game, when deploy decision is made:
    collector.record_decision(board_state, action, log_prob, value)

    # At game end:
    trajectory = collector.finalize_game(won=True)
"""

import logging
from typing import Any, List, Optional
import numpy as np

from .state_encoder import StateEncoder
from .experience import Experience, GameTrajectory
from .rewards import RewardShaper

logger = logging.getLogger(__name__)

# Try to import PyTorch
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


class ExperienceCollector:
    """
    Collects experiences during gameplay for training.

    Integrates with NeuralDeployPlanner to record every deploy
    decision made during a game.
    """

    def __init__(self, network=None, device: str = 'cpu'):
        """
        Initialize the experience collector.

        Args:
            network: DeployPolicyNetwork for getting actions
            device: 'cuda' or 'cpu'
        """
        self.network = network
        self.device = device
        self.state_encoder = StateEncoder()
        self.reward_shaper = RewardShaper()

        # Current game trajectory
        self.current_trajectory: Optional[GameTrajectory] = None
        self.game_active = False

        # Last state for reward computation
        self._last_board_state = None

    def start_game(self, my_side: str = '') -> None:
        """
        Start collecting for a new game.

        Args:
            my_side: 'dark' or 'light'
        """
        self.current_trajectory = GameTrajectory(my_side=my_side)
        self.game_active = True
        self.reward_shaper.reset()
        self._last_board_state = None
        logger.debug("Started experience collection for new game")

    def record_decision(
        self,
        board_state: Any,
        action: int,
        log_prob: float,
        value: float,
        action_mask: np.ndarray,
    ) -> None:
        """
        Record a deploy decision.

        Called by NeuralDeployPlanner after making a decision.

        Args:
            board_state: Current BoardState
            action: Action taken (0-20)
            log_prob: Log probability of action
            value: Critic's value estimate
            action_mask: Valid action mask
        """
        if not self.game_active or self.current_trajectory is None:
            return

        # Encode state
        state = self.state_encoder.encode(board_state)

        # Compute shaped reward based on state change
        reward = self.reward_shaper.compute_reward(
            board_state=board_state,
            action=action,
            game_result=None,
        )

        # Create experience
        exp = Experience(
            state=state,
            action=action,
            action_mask=action_mask.copy(),
            reward=reward,
            done=False,
            value=value,
            log_prob=log_prob,
            turn=getattr(board_state, 'turn_number', 0),
            phase=getattr(board_state, 'current_phase', ''),
        )

        self.current_trajectory.add_experience(exp)
        self._last_board_state = board_state

        logger.debug(f"Recorded experience: action={action}, reward={reward:.3f}, "
                    f"turn={exp.turn}")

    def finalize_game(self, won: bool) -> Optional[GameTrajectory]:
        """
        Finalize the current game and return the trajectory.

        Args:
            won: True if we won the game

        Returns:
            Completed GameTrajectory or None if no game was active
        """
        if not self.game_active or self.current_trajectory is None:
            return None

        self.current_trajectory.finalize(won=won)
        trajectory = self.current_trajectory

        logger.info(f"Finalized game: won={won}, "
                   f"experiences={len(trajectory.experiences)}, "
                   f"final_reward={trajectory.final_reward}")

        # Reset for next game
        self.current_trajectory = None
        self.game_active = False
        self._last_board_state = None

        return trajectory

    def cancel_game(self) -> None:
        """Cancel current game without saving trajectory."""
        self.current_trajectory = None
        self.game_active = False
        self._last_board_state = None
        self.reward_shaper.reset()


class TrainingNeuralPlanner:
    """
    Neural deploy planner with experience collection for training.

    Wraps NeuralDeployPlanner to record experiences during gameplay.
    """

    def __init__(
        self,
        model_path: str = 'models/deploy_planner.pt',
        fallback_planner: Any = None,
        confidence_threshold: float = 0.3,
        device: str = 'cpu',
        collect_experiences: bool = True,
    ):
        """
        Initialize training-enabled neural planner.

        Args:
            model_path: Path to model weights
            fallback_planner: Rules-based fallback planner
            confidence_threshold: Min confidence for neural decisions
            device: 'cuda' or 'cpu'
            collect_experiences: If True, record experiences for training
        """
        from .neural_deploy_planner import NeuralDeployPlanner
        from .network import DeployPolicyNetwork

        self.device = device
        self.collect_experiences = collect_experiences

        # Create network
        self.network = DeployPolicyNetwork()
        if TORCH_AVAILABLE:
            self.network = self.network.to(device)

            # Load weights if available
            import os
            if os.path.exists(model_path):
                try:
                    state_dict = torch.load(model_path, map_location=device)
                    self.network.load_state_dict(state_dict)
                    logger.info(f"Loaded model from {model_path}")
                except Exception as e:
                    logger.warning(f"Could not load model: {e}")

        # Create base planner
        self.planner = NeuralDeployPlanner(
            model_path=model_path,
            fallback_planner=fallback_planner,
            confidence_threshold=confidence_threshold,
            use_cpu=(device == 'cpu'),
        )
        # Replace network with our shared one
        self.planner.network = self.network

        # Experience collector
        self.collector = ExperienceCollector(
            network=self.network,
            device=device,
        )

        # State encoder for getting state/mask before decision
        self.state_encoder = StateEncoder()

    def start_game(self, my_side: str = '') -> None:
        """Start collecting experiences for a new game."""
        if self.collect_experiences:
            self.collector.start_game(my_side)
        self.planner.reset()

    def create_plan(self, board_state: Any):
        """
        Create deployment plan, recording experience if in training mode.
        """
        from .state_encoder import NUM_ACTIONS
        import torch.nn.functional as F

        # Get state and mask before decision
        state = self.state_encoder.encode(board_state)
        action_mask = self.state_encoder.get_action_mask(board_state)

        # Make decision
        plan = self.planner.create_plan(board_state)

        # If collecting and we used neural (not fallback), record experience
        if self.collect_experiences and TORCH_AVAILABLE and self.network is not None:
            # Determine which action was taken based on plan strategy
            action = self._plan_to_action(plan, board_state)

            # Get log_prob and value from network
            with torch.no_grad():
                state_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
                mask_t = torch.BoolTensor(action_mask).unsqueeze(0).to(self.device)

                logits, value = self.network(state_t, mask_t)
                probs = F.softmax(logits, dim=-1)
                log_prob = torch.log(probs[0, action] + 1e-8).item()
                value = value[0, 0].item()

            self.collector.record_decision(
                board_state=board_state,
                action=action,
                log_prob=log_prob,
                value=value,
                action_mask=action_mask,
            )

        return plan

    def finalize_game(self, won: bool) -> Optional[GameTrajectory]:
        """Finalize game and return trajectory for training."""
        if self.collect_experiences:
            return self.collector.finalize_game(won)
        return None

    def _plan_to_action(self, plan, board_state: Any) -> int:
        """Convert a DeploymentPlan back to action index."""
        from ..deploy_planner import DeployStrategy

        if plan.strategy == DeployStrategy.HOLD_BACK:
            return 0  # ACTION_HOLD_BACK

        if plan.strategy == DeployStrategy.DEPLOY_LOCATIONS:
            return 17  # ACTION_DEPLOY_LOCATION_CARD

        # For other strategies, try to determine target location
        if plan.instructions:
            inst = plan.instructions[0]
            target_loc_id = inst.target_location_id

            # Find location index
            locations = getattr(board_state, 'locations', [])
            for i, loc in enumerate(locations):
                if loc and getattr(loc, 'card_id', None) == target_loc_id:
                    return 1 + i  # ACTION_DEPLOY_LOC_START + index

        # Default based on strategy
        if plan.strategy == DeployStrategy.REINFORCE:
            return 20  # ACTION_REINFORCE_BEST
        elif plan.strategy == DeployStrategy.ESTABLISH:
            return 18  # ACTION_ESTABLISH_GROUND (default)

        return 0  # Fallback to HOLD_BACK

    # Delegate other methods to base planner
    def get_card_score(self, *args, **kwargs):
        return self.planner.get_card_score(*args, **kwargs)

    def record_deployment(self, *args, **kwargs):
        return self.planner.record_deployment(*args, **kwargs)

    def should_hold_back(self):
        return self.planner.should_hold_back()

    def get_plan_summary(self):
        return self.planner.get_plan_summary()

    def reset(self):
        return self.planner.reset()

    @property
    def current_plan(self):
        return self.planner.current_plan

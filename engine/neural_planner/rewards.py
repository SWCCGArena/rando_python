"""
Reward Shaping for Neural Deploy Planner Training.

Provides intermediate rewards during training to speed up learning.
The primary signal is win/loss, but shaped rewards help guide
early exploration.

Reward signals:
- Game win/loss: +1 / -1 (primary signal)
- Drain gap improvement: +0.1 per icon
- Power advantage gained: +0.05 per power
- Bleed location stopped: +0.2 per location
- Force efficiency: -0.02 for wasted force at turn end
"""

from typing import Any, Dict
import logging

logger = logging.getLogger(__name__)


class RewardShaper:
    """
    Computes shaped rewards for training.

    Tracks state between calls to compute deltas.
    """

    def __init__(self):
        """Initialize the reward shaper."""
        self.prev_state: Dict[str, Any] = {}
        self.turn_start_force: int = 0

    def reset(self) -> None:
        """Reset state for new game."""
        self.prev_state = {}
        self.turn_start_force = 0

    def compute_reward(
        self,
        board_state: Any,
        action: int,
        game_result: str = None,
    ) -> float:
        """
        Compute shaped reward for a deployment decision.

        Args:
            board_state: Current BoardState after action
            action: Action that was taken (0-20)
            game_result: 'win', 'loss', 'draw', or None if game continues

        Returns:
            Shaped reward value
        """
        # Terminal reward dominates
        if game_result is not None:
            if game_result == 'win':
                return 1.0
            elif game_result == 'loss':
                return -1.0
            else:
                return 0.0

        # Extract current state
        current = self._extract_state(board_state)

        # If no previous state, just save and return 0
        if not self.prev_state:
            self.prev_state = current
            return 0.0

        # Compute deltas
        reward = 0.0

        # Drain gap improvement: +0.1 per icon gained
        drain_delta = current['drain_gap'] - self.prev_state['drain_gap']
        reward += 0.1 * drain_delta

        # Power advantage: +0.05 per power gained
        power_delta = current['power_advantage'] - self.prev_state['power_advantage']
        reward += 0.05 * power_delta

        # Bleed locations reduced: +0.2 per location no longer bleeding
        bleed_delta = self.prev_state['bleed_count'] - current['bleed_count']
        reward += 0.2 * bleed_delta

        # Contested locations: +0.1 for each new contested location
        # (means we're fighting for control)
        contest_delta = current['contested_count'] - self.prev_state['contested_count']
        if contest_delta > 0:
            reward += 0.1 * contest_delta

        # Force efficiency penalty: discourage wasting force
        # Small penalty for ending turn with high force pile
        # (means we could have deployed more)
        # Only apply at turn end (phase changes)
        if current['phase'] != self.prev_state.get('phase', ''):
            force_wasted = max(0, current['force_pile'] - 2)  # Keep 2 for effects
            reward -= 0.02 * force_wasted

        # Save current state
        self.prev_state = current

        return reward

    def _extract_state(self, board_state: Any) -> Dict[str, Any]:
        """Extract relevant state for reward computation."""
        locations = getattr(board_state, 'locations', [])
        my_side = getattr(board_state, 'my_side', 'dark')

        # Calculate drain gap and control stats
        our_drain = 0
        their_drain = 0
        contested_count = 0
        bleed_count = 0

        for i, loc in enumerate(locations):
            if loc is None:
                continue

            my_power = self._safe_call(board_state, 'my_power_at_location', 0, i)
            their_power = self._safe_call(board_state, 'their_power_at_location', 0, i)
            my_icons = self._get_icons(board_state, loc, i, my_side, is_mine=True)
            their_icons = self._get_icons(board_state, loc, i, my_side, is_mine=False)

            i_control = my_power > 0 and their_power == 0
            they_control = their_power > 0 and my_power == 0
            contested = my_power > 0 and their_power > 0

            if contested:
                contested_count += 1

            if i_control and their_icons > 0:
                our_drain += their_icons
            if they_control and my_icons > 0:
                their_drain += my_icons
                bleed_count += 1

        drain_gap = our_drain - their_drain

        # Power advantage
        my_power = self._safe_call(board_state, 'total_my_power', 0)
        their_power = self._safe_call(board_state, 'total_their_power', 0)
        power_advantage = my_power - their_power

        return {
            'drain_gap': drain_gap,
            'power_advantage': power_advantage,
            'contested_count': contested_count,
            'bleed_count': bleed_count,
            'force_pile': getattr(board_state, 'force_pile', 0),
            'phase': getattr(board_state, 'current_phase', ''),
            'turn': getattr(board_state, 'turn_number', 0),
        }

    def _get_icons(
        self,
        board_state: Any,
        loc: Any,
        idx: int,
        my_side: str,
        is_mine: bool,
    ) -> int:
        """Get force icons at a location."""
        if is_mine:
            result = self._safe_call(board_state, 'my_icons_at_location', None, idx)
            if result is not None:
                return result
            icons_str = getattr(loc, 'my_icons', '')
        else:
            result = self._safe_call(board_state, 'their_icons_at_location', None, idx)
            if result is not None:
                return result
            icons_str = getattr(loc, 'their_icons', '')

        if isinstance(icons_str, int):
            return icons_str
        if isinstance(icons_str, str) and icons_str.isdigit():
            return int(icons_str)
        return 0

    def _safe_call(self, obj: Any, method_name: str, default: Any, *args):
        """Safely call a method or return default."""
        method = getattr(obj, method_name, None)
        if callable(method):
            try:
                return method(*args)
            except Exception:
                return default
        return default


def compute_game_reward(won: bool, turn_count: int = 0) -> float:
    """
    Compute final game reward.

    Args:
        won: True if we won the game
        turn_count: Number of turns the game lasted (optional)

    Returns:
        Final reward value
    """
    base_reward = 1.0 if won else -1.0

    # Optional: small bonus for quicker wins, small penalty for quick losses
    # This encourages efficient play
    if turn_count > 0:
        if won and turn_count < 10:
            base_reward += 0.1  # Bonus for quick win
        elif not won and turn_count < 10:
            base_reward -= 0.1  # Extra penalty for quick loss

    return base_reward

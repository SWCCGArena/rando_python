"""
Force Activation Evaluator

Handles INTEGER decision types for force activation.
Determines the optimal amount of force to activate based on:
- Current force pile
- Reserve deck size
- Turn number and strategy

Ported from C# BotAIHelper.ForceToActivate() and RankActivateForceAction()
"""

import logging
from typing import List, Optional
from .base import ActionEvaluator, DecisionContext, EvaluatedAction, ActionType

logger = logging.getLogger(__name__)


class ForceActivationEvaluator(ActionEvaluator):
    """
    Evaluates force activation decisions (INTEGER type).

    Returns a single EvaluatedAction with the amount of force to activate
    as the action_id (as a string).
    """

    def __init__(self):
        super().__init__("ForceActivation")

    def can_evaluate(self, context: DecisionContext) -> bool:
        """Check if this is a force activation decision (or any INTEGER decision)"""
        # Handle all INTEGER decisions - force activation is the most common
        # but we should handle any INTEGER decision to avoid loops
        return context.decision_type == 'INTEGER'

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Determine optimal INTEGER response.

        For force activation: Calculate optimal amount based on game state.
        For opponent activation: Usually allow max (let them waste force).
        For other INTEGER: Default to max value.

        Returns a single action with the integer value.
        """
        bs = context.board_state
        text_lower = context.decision_text.lower()

        # Parse min/max from extra data (should be passed by decision handler)
        min_val = context.extra.get('min', 0)
        max_val = context.extra.get('max', 0)

        # Ensure we have valid bounds
        if max_val == 0:
            # Fallback: use default from XML if available
            max_val = context.extra.get('defaultValue', 1)
            logger.warning(f"No max value found, using fallback: {max_val}")

        # Special case: "allow opponent to activate" - just let them activate max
        if 'allow opponent to activate' in text_lower or 'opponent to activate' in text_lower:
            action = EvaluatedAction(
                action_id=str(max_val),
                action_type=ActionType.ACTIVATE,
                score=50.0,
                display_text=f"Allow opponent to activate {max_val} force"
            )
            action.add_reasoning("Allowing opponent max activation (they'll waste force)")
            return [action]

        if not bs:
            # No board state - use max value
            action = EvaluatedAction(
                action_id=str(max_val),
                action_type=ActionType.ACTIVATE,
                score=50.0,
                display_text=f"INTEGER response: {max_val} (no board state)"
            )
            action.add_reasoning("No board state available, defaulting to max")
            return [action]

        # Standard force activation logic
        if 'force to activate' in text_lower or 'activate force' in text_lower:
            # Calculate optimal amount using board state logic
            amount = self._calculate_activation_amount(bs, max_val)
        else:
            # Unknown INTEGER decision - use max value
            amount = max_val
            logger.info(f"Unknown INTEGER decision, using max: {amount}")

        # Ensure amount is within bounds
        amount = max(min_val, min(amount, max_val))

        # Build action with reasoning
        action = EvaluatedAction(
            action_id=str(amount),
            action_type=ActionType.ACTIVATE,
            score=50.0,  # Base score
            display_text=f"Activate {amount} of {max_val} force"
        )

        # Add reasoning based on decision factors
        if bs.force_pile > 12:
            action.add_reasoning(f"Force pile high ({bs.force_pile}) - conserving", 0.0)

        reserve_total = bs.total_reserve_force()
        if reserve_total <= 20:
            action.add_reasoning(f"Reserve low ({reserve_total}) - saving for destiny", 0.0)

        if amount == max_val:
            action.add_reasoning("Activating full amount available", 10.0)
        elif amount == 0:
            action.add_reasoning("Skipping activation this turn", -10.0)
        else:
            action.add_reasoning(f"Activating partial ({amount}/{max_val})", 0.0)

        # Track that we're activating this amount
        if hasattr(bs, 'force_activated_this_turn'):
            bs.force_activated_this_turn += amount

        return [action]

    def _calculate_activation_amount(self, bs, max_available: int) -> int:
        """
        Calculate optimal force activation amount.

        Ported from C# BotAIHelper.ForceToActivate():
        - If we already have lots of force (>12), only activate a few more
        - If reserve is running low, leave some for destiny draws
        """
        amount = max_available
        current_force = bs.force_pile
        reserve_size = bs.total_reserve_force() if hasattr(bs, 'total_reserve_force') else (
            bs.reserve_deck + bs.used_pile + bs.force_pile
        )

        # If we already have plenty of force, only activate a little more
        if current_force > 12:
            force_activated = getattr(bs, 'force_activated_this_turn', 0)
            amount = max(0, 2 - force_activated)
            logger.debug(f"Force > 12 ({current_force}), limiting to {amount} more")

        # If reserve is running low, leave some for destiny draws
        if reserve_size <= amount:
            amount = max(0, reserve_size - 3)
            logger.debug(f"Reserve low ({reserve_size}), limiting to {amount}")

        return amount

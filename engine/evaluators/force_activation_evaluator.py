"""
Force Activation Evaluator

Handles INTEGER decision types for force activation.
Determines the optimal amount of force to activate based on:
- Current force pile
- Reserve deck size
- Turn number and strategy
- Future turn planning (save for expensive cards)
- Late-game life force preservation

Ported from C# BotAIHelper.ForceToActivate() and RankActivateForceAction()
"""

import logging
from typing import List, Optional
from .base import ActionEvaluator, DecisionContext, EvaluatedAction, ActionType
from ..strategy_config import get_config

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIG-DRIVEN PARAMETERS
# =============================================================================

def _get_force_config(key: str, default):
    """Get force activation strategy config value."""
    return get_config().get('force_activation_strategy', key, default)

# Late-game thresholds
def get_late_game_life_force() -> int:
    return _get_force_config('late_game_life_force', 12)

def get_critical_life_force() -> int:
    return _get_force_config('critical_life_force', 6)

# Force activation limits
def get_max_force_pile() -> int:
    return _get_force_config('max_force_pile', 25)

def get_reserve_for_destiny_contested() -> int:
    return _get_force_config('reserve_for_destiny_contested', 4)

def get_reserve_for_destiny_safe() -> int:
    return _get_force_config('reserve_for_destiny_safe', 1)


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

        # Log config values being used for this decision
        logger.info(f"📊 FORCE CONFIG: max_force_pile={get_max_force_pile()}, "
                   f"reserve_contested={get_reserve_for_destiny_contested()}, "
                   f"reserve_safe={get_reserve_for_destiny_safe()}, "
                   f"critical_life={get_critical_life_force()}")

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
            # EARLY GAME AGGRESSION: Turns 1-3, activate maximum to build resources
            if context.turn_number <= 3:
                amount = max_val
                logger.info(f"🚀 Early game (turn {context.turn_number}) - activating max force: {amount}")
            else:
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

        Rules (in priority order):
        1. Reserve cards for destiny draws:
           - Keep 3 cards in reserve deck normally
           - Keep 2 cards if total cards < 10 (endgame)
        2. Cap force pile at 20 max
        3. Late-game preservation when life force is critical

        Example: 15 cards in reserve, max activation 14, force pile 0
        -> Should only activate 12 (15 - 3 = 12)

        Example: Force pile is 10, max activation 14
        -> Should only activate 10 (to reach cap of 20)
        """
        from ..card_loader import get_card

        amount = max_available
        current_force = bs.force_pile
        reserve_deck = getattr(bs, 'reserve_deck', 20)
        used_pile = getattr(bs, 'used_pile', 0)
        force_pile = getattr(bs, 'force_pile', 0)
        hand_size = getattr(bs, 'hand_size', 7)

        # Total life force (reserve + used + force pile)
        life_force = reserve_deck + used_pile + force_pile

        # === RULE 1: RESERVE CARDS FOR DESTINY DRAWS ===
        # This is the MOST important rule - we need cards in reserve deck
        # to draw destiny during battles
        # Reserve more if there are contested locations (potential battles)
        has_contested = self._has_contested_locations(bs)
        if has_contested:
            reserve_needed = get_reserve_for_destiny_contested()
            logger.debug(f"🎲 Contested locations found - reserving {reserve_needed} cards")
        else:
            reserve_needed = get_reserve_for_destiny_safe()
            logger.debug(f"🎲 No contested locations - reserving {reserve_needed} card")

        # Calculate max we can activate while keeping reserve
        max_from_reserve = max(0, reserve_deck - reserve_needed)
        if max_from_reserve < amount:
            logger.info(f"🎲 Reserving {reserve_needed} cards for destiny. Reserve deck: {reserve_deck}, limiting activation from {amount} to {max_from_reserve}")
            amount = max_from_reserve

        # === RULE 2: CAP FORCE PILE AT MAX ===
        # Never have more than get_max_force_pile() in force pile
        force_room = get_max_force_pile() - current_force
        if force_room < amount:
            logger.info(f"🎲 Capping force pile at {get_max_force_pile()}. Current: {current_force}, limiting activation from {amount} to {force_room}")
            amount = max(0, force_room)

        # === RULE 3: LATE GAME PRESERVATION ===
        # When life force is critically low, minimize activation to preserve destiny draws
        if life_force < get_critical_life_force():
            # Only activate enough to do ONE action, preserve rest for destiny
            emergency_amount = min(amount, max(1, 6 - current_force))
            if emergency_amount < amount:
                logger.info(f"⚠️ CRITICAL life force ({life_force}), limiting activation to {emergency_amount}")
                amount = emergency_amount
            return amount

        # NOTE: Late game reserve logic removed - now using simple contested/safe logic
        # (1 card if safe, 2 cards if contested) which is already applied in RULE 1

        # === CONSIDER HAND CONTENTS ===
        # Check if we have expensive cards that need saving for
        max_deploy_cost = 0
        if hasattr(bs, 'cards_in_hand'):
            for card in bs.cards_in_hand:
                if card.blueprint_id:
                    metadata = get_card(card.blueprint_id)
                    if metadata and metadata.deploy_value:
                        max_deploy_cost = max(max_deploy_cost, metadata.deploy_value)

        # If we have expensive cards and need more force, try to activate enough
        # (but still respect the rules above)
        if max_deploy_cost > current_force and max_deploy_cost <= life_force:
            force_needed = max_deploy_cost - current_force
            if force_needed > amount:
                logger.debug(f"Expensive card (cost {max_deploy_cost}), need {force_needed} but limited to {amount}")

        # If we already have plenty of force, only activate a little more
        if current_force > 12:
            force_activated = getattr(bs, 'force_activated_this_turn', 0)
            conservative_amount = max(0, 2 - force_activated)
            if conservative_amount < amount:
                logger.debug(f"Force > 12 ({current_force}), limiting to {conservative_amount} more")
                amount = conservative_amount

        # === HAND SIZE CONSIDERATION ===
        # If hand is small and we have plenty of force, don't over-activate
        # (save cards in reserve deck for drawing later)
        if hand_size <= 4 and current_force >= 8:
            if amount > 2:
                logger.debug(f"Small hand ({hand_size}), enough force ({current_force}), limiting to 2")
                amount = 2

        return amount

    def _has_contested_locations(self, bs) -> bool:
        """
        Check if there are any contested locations on the board.

        A location is contested if both players have power there.
        """
        if not hasattr(bs, 'locations') or not bs.locations:
            return False

        for i, loc in enumerate(bs.locations):
            my_power = bs.my_power_at_location(i)
            their_power = bs.their_power_at_location(i)
            if my_power > 0 and their_power > 0:
                return True

        return False

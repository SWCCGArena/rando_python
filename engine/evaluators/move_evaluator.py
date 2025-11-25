"""
Move Evaluator

Handles movement decisions.
Ported from C# AICACHandler.cs RankMoveAction

Decision factors:
- Power differential at current location (fleeing)
- Power differential at destination
- Spreading out vs consolidating
- Adjacent location analysis
- Strategic retreat from dangerous locations (from GameStrategy)
"""

import logging
from typing import List, Optional
from .base import ActionEvaluator, DecisionContext, EvaluatedAction, ActionType
from ..game_strategy import GameStrategy, ThreatLevel

logger = logging.getLogger(__name__)

# Rank deltas (from C# BotAIHelper)
VERY_GOOD_DELTA = 999.0
GOOD_DELTA = 10.0
BAD_DELTA = -10.0
VERY_BAD_DELTA = -999.0

# Move thresholds (from C# AICACHandler)
POWER_DIFF_FOR_FLEE = 2  # Their power advantage to trigger flee
POWER_DIFF_FOR_BUILDUP = 12  # Our power advantage before spreading
OVERKILL_THRESHOLD = 4  # Our power advantage considered "overkill" for movement purposes
CONTEST_POWER_MARGIN = 2  # How much extra power we want when contesting


class MoveEvaluator(ActionEvaluator):
    """
    Evaluates movement decisions.

    Considers:
    - Running away from superior forces
    - Spreading out when we have power advantage
    - Moving to locations with icons
    - Strategic retreat from dangerous/retreat threat levels
    """

    def __init__(self):
        super().__init__("Move")
        self.pending_move_card_ids = set()  # Track cards we already tried moving

    def _get_game_strategy(self, context: DecisionContext) -> Optional[GameStrategy]:
        """Get GameStrategy from board_state's strategy_controller"""
        if context.board_state and context.board_state.strategy_controller:
            return context.board_state.strategy_controller.game_strategy
        return None

    def can_evaluate(self, context: DecisionContext) -> bool:
        """Handle CARD_ACTION_CHOICE with move actions"""
        if context.decision_type not in ['CARD_ACTION_CHOICE', 'ACTION_CHOICE']:
            return False

        # Check if any action is a move action
        move_keywords = ["Move using", "Shuttle", "Docking bay transit", "Transport",
                        "Take off", "Land", "Move to", "Move from"]
        for action_text in context.action_texts:
            if any(kw in action_text for kw in move_keywords):
                return True

        return False

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Evaluate movement options"""
        actions = []
        bs = context.board_state
        game_strategy = self._get_game_strategy(context)

        move_keywords = ["Move using", "Shuttle", "Docking bay transit", "Transport",
                        "Take off", "Land"]

        for i, action_id in enumerate(context.action_ids):
            action_text = context.action_texts[i] if i < len(context.action_texts) else ""

            if not any(kw in action_text for kw in move_keywords):
                continue

            action = EvaluatedAction(
                action_id=action_id,
                action_type=ActionType.MOVE,
                score=0.0,
                display_text=action_text
            )

            card_id = context.card_ids[i] if i < len(context.card_ids) else None

            # Check if we already tried moving this card
            if card_id and card_id in self.pending_move_card_ids:
                action.add_reasoning("Already tried moving this card", VERY_BAD_DELTA)
                actions.append(action)
                continue

            if bs and card_id:
                card = bs.cards_in_play.get(card_id)
                if card:
                    loc_idx = card.location_index
                    self._rank_move_from_location(action, bs, loc_idx, card_id, game_strategy)
                else:
                    action.add_reasoning("Card not found in play", BAD_DELTA)
            else:
                action.add_reasoning("No board state or card ID", 0.0)

            actions.append(action)

        return actions

    def _rank_move_from_location(self, action: EvaluatedAction, board_state, loc_idx: int,
                                   card_id: str, game_strategy: Optional[GameStrategy] = None):
        """
        Rank moving from a specific location.

        Ported from C# AICACHandler.RankMoveAction
        Enhanced with strategic retreat logic from GameStrategy.
        """
        if loc_idx < 0 or loc_idx >= len(board_state.locations):
            action.add_reasoning("Invalid location index", BAD_DELTA)
            return

        my_power = board_state.my_power_at_location(loc_idx)
        their_power = board_state.their_power_at_location(loc_idx)
        my_card_count = board_state.my_card_count_at_location(loc_idx) if hasattr(board_state, 'my_card_count_at_location') else 0

        power_diff = my_power - their_power

        # Use GameStrategy threat assessment for retreat decisions
        if game_strategy:
            threat_level = game_strategy.get_location_threat(loc_idx)

            if threat_level == ThreatLevel.RETREAT:
                # Definitely should retreat - we're badly outmatched
                action.add_reasoning(f"Strategic retreat - badly outmatched ({power_diff})", VERY_GOOD_DELTA)
                return
            elif threat_level == ThreatLevel.DANGEROUS:
                # Should consider retreating
                action.add_reasoning(f"Dangerous location - retreat recommended ({power_diff})", GOOD_DELTA * 2)
                return
            elif game_strategy.is_location_dangerous(loc_idx):
                # Location is marked dangerous in strategy
                action.add_reasoning("Retreating from danger zone", GOOD_DELTA)
                return

        # Flee logic: their power > our power + 2
        if their_power - my_power > POWER_DIFF_FOR_FLEE and their_power > 0:
            action.add_reasoning(f"Enemy stronger ({their_power} vs {my_power}) - flee", GOOD_DELTA)
            return

        # Spread out logic: we have massive power advantage
        if power_diff >= POWER_DIFF_FOR_BUILDUP and my_card_count >= 3:
            action.add_reasoning(f"Strong presence ({my_power}) - spread out", GOOD_DELTA * power_diff / 10)
            return

        # PROACTIVE CONTEST: If we have overkill here, check if we should move to contest
        # an adjacent location where opponent has presence
        if power_diff >= OVERKILL_THRESHOLD:
            contest_opportunity = self._find_contest_opportunity(board_state, loc_idx, my_power)
            if contest_opportunity:
                adj_idx, adj_their_power, can_overpower = contest_opportunity
                if can_overpower:
                    # We can move and still overpower them - good move!
                    bonus = GOOD_DELTA * 2 + (power_diff - OVERKILL_THRESHOLD) * 0.5
                    action.add_reasoning(
                        f"Contest adjacent loc {adj_idx} (they have {adj_their_power}, we have overkill +{power_diff})",
                        bonus
                    )
                    return
                else:
                    # We could contest but might not overpower - still consider it
                    action.add_reasoning(
                        f"Could contest loc {adj_idx} (they have {adj_their_power})",
                        GOOD_DELTA
                    )
                    return

        # Check adjacent locations for spreading (when opponent has 0 power here)
        if their_power == 0 and my_power >= POWER_DIFF_FOR_BUILDUP and my_card_count >= 1:
            safe_to_spread = self._check_adjacent_locations(board_state, loc_idx)
            if safe_to_spread:
                action.add_reasoning("Adjacent locations clear - spread", GOOD_DELTA)
            else:
                action.add_reasoning("Adjacent locations not safe", BAD_DELTA)
            return

        # Default: not a good time to move
        action.add_reasoning("No good reason to move", BAD_DELTA)

    def _find_contest_opportunity(self, board_state, loc_idx: int, our_power_here: int):
        """
        Find an adjacent location where opponent has presence that we could contest.

        Returns: (adjacent_idx, their_power, can_overpower) or None
        """
        # Get the card we're considering moving - estimate its power contribution
        # For simplicity, assume moving card has ~3-5 power (average character)
        estimated_move_power = 4

        best_opportunity = None
        best_score = 0

        # Check adjacent locations
        for adj_idx in [loc_idx - 1, loc_idx + 1]:
            if adj_idx < 0 or adj_idx >= len(board_state.locations):
                continue

            their_power = board_state.their_power_at_location(adj_idx)
            our_power_there = board_state.my_power_at_location(adj_idx)

            # Only interested if opponent has presence there
            if their_power <= 0:
                continue

            # Could we overpower them if we moved?
            # We'd add our power to existing power there
            potential_power = our_power_there + estimated_move_power
            can_overpower = potential_power > their_power + CONTEST_POWER_MARGIN

            # Score this opportunity - prefer locations where we can decisively win
            score = their_power  # Higher opponent power = more valuable to contest
            if can_overpower:
                score += 10
            if our_power_there == 0:
                # We have no presence - establishing presence is valuable
                score += 5

            if score > best_score:
                best_score = score
                best_opportunity = (adj_idx, their_power, can_overpower)

        return best_opportunity

    def _check_adjacent_locations(self, board_state, loc_idx: int) -> bool:
        """Check if adjacent locations are safe to move to"""
        # Check left
        if loc_idx > 0:
            left_their_power = board_state.their_power_at_location(loc_idx - 1)
            if left_their_power == 0:
                return True

        # Check right
        if loc_idx < len(board_state.locations) - 1:
            right_their_power = board_state.their_power_at_location(loc_idx + 1)
            if right_their_power == 0:
                return True

        return False

    def reset_pending_moves(self):
        """Reset pending move tracking (call at turn start)"""
        self.pending_move_card_ids.clear()

    def track_move(self, card_id: str):
        """Track that we tried moving this card"""
        self.pending_move_card_ids.add(card_id)

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
        self._last_turn_number = -1

    def reset_for_new_game(self):
        """Reset all state for a new game"""
        self.pending_move_card_ids.clear()
        self._last_turn_number = -1

    def _get_game_strategy(self, context: DecisionContext) -> Optional[GameStrategy]:
        """Get GameStrategy from board_state's strategy_controller"""
        if context.board_state and context.board_state.strategy_controller:
            return context.board_state.strategy_controller.game_strategy
        return None

    def can_evaluate(self, context: DecisionContext) -> bool:
        """Handle CARD_ACTION_CHOICE with move actions during OUR turn only"""
        if context.decision_type not in ['CARD_ACTION_CHOICE', 'ACTION_CHOICE']:
            return False

        # CRITICAL: Only evaluate move decisions during OUR turn
        # During opponent's turn, we can't initiate moves
        if context.board_state and not context.board_state.is_my_turn:
            logger.debug(f"🚶 MoveEvaluator skipping - not our turn")
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

        # Reset pending move tracking at the start of each turn
        if context.turn_number != self._last_turn_number:
            self.reset_pending_moves()
            self._last_turn_number = context.turn_number

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
        # The worse the disadvantage, the more we want to flee!
        # BUT: Check if fleeing is actually beneficial (destination analysis)
        if their_power - my_power > POWER_DIFF_FOR_FLEE and their_power > 0:
            disadvantage = their_power - my_power

            # Analyze flee options to see if it's worth it
            loc = board_state.locations[loc_idx] if loc_idx < len(board_state.locations) else None
            is_space = loc.is_space if loc else False
            flee_analysis = board_state.analyze_flee_options(loc_idx, is_space)

            # Check movement cost - can we afford to move everyone?
            if not flee_analysis['can_afford']:
                movement_cost = flee_analysis['movement_cost']
                action.add_reasoning(f"Want to flee but can't afford ({movement_cost} Force needed, have {board_state.force_pile})", BAD_DELTA)
                logger.info(f"🚫 Can't afford to flee: need {movement_cost} Force, have {board_state.force_pile}")
                return

            # Check if destination is actually better
            if flee_analysis['can_flee']:
                best_dest = flee_analysis['best_destination']
                if best_dest is not None:
                    dest_their_power = board_state.their_power_at_location(best_dest)

                    if dest_their_power >= their_power:
                        # Destination is WORSE or same - don't flee into more trouble!
                        action.add_reasoning(f"Destination has {dest_their_power} enemies (same or worse) - don't flee!", BAD_DELTA * 2)
                        logger.info(f"🚫 Not fleeing: destination has {dest_their_power} enemies vs {their_power} here")
                        return
                    elif dest_their_power > 0:
                        # Destination has some enemies but fewer
                        if disadvantage >= 6:
                            action.add_reasoning(f"FLEEING to location with fewer enemies ({dest_their_power} vs {their_power})", VERY_GOOD_DELTA)
                            logger.info(f"🏃 Fleeing from {their_power} enemies to {dest_their_power}")
                        else:
                            action.add_reasoning(f"Fleeing to location with fewer enemies ({dest_their_power} vs {their_power})", GOOD_DELTA * 2)
                        return
                    else:
                        # Destination is empty - great!
                        if disadvantage >= 6:
                            action.add_reasoning(f"FLEEING to EMPTY location (escaping {their_power} enemies!)", VERY_GOOD_DELTA)
                            logger.info(f"🏃 Fleeing from {their_power} enemies to empty location!")
                        elif disadvantage >= 4:
                            action.add_reasoning(f"Fleeing to empty location (escaping {their_power})", GOOD_DELTA * 3)
                        else:
                            action.add_reasoning(f"Moving to empty location (enemy has {their_power})", GOOD_DELTA)
                        return

            # Can't find valid flee destination
            reason = flee_analysis['reason']
            action.add_reasoning(f"Want to flee but no good destination: {reason}", BAD_DELTA)
            return

        # Spread out / contest logic: we have power advantage
        # BUT we must check if spreading is actually viable:
        # - How much force do we have to move cards?
        # - Can we move ENOUGH power to establish (6) or contest (beat enemy + margin)?
        if power_diff >= OVERKILL_THRESHOLD and my_card_count >= 2:
            spread_analysis = self._analyze_spread_viability(
                board_state, loc_idx, my_power, my_card_count
            )

            if spread_analysis['viable']:
                reason = spread_analysis['reason']
                score = spread_analysis['score']
                action.add_reasoning(reason, score)
                return
            else:
                # Can't meaningfully spread - explain why
                reason = spread_analysis['reason']
                action.add_reasoning(f"Can't spread: {reason}", BAD_DELTA)
                return

        # Default: not a good time to move
        action.add_reasoning("No good reason to move", BAD_DELTA)

    def _analyze_spread_viability(self, board_state, loc_idx: int,
                                     our_power_here: int, our_card_count: int) -> dict:
        """
        Analyze if spreading out from this location is viable.

        A move is viable only if we can:
        - Afford to move cards (1 force per card)
        - Move ENOUGH power to be useful at the destination:
          - Empty location: need 6+ power to establish
          - Contested location: need to beat enemy + 4 power margin

        Returns dict with:
            viable: bool
            reason: str
            score: float (if viable)
        """
        ESTABLISH_THRESHOLD = 6  # Power needed to establish presence
        CONTEST_MARGIN = 4  # Extra power needed to safely contest

        force_available = board_state.force_pile
        if force_available < 1:
            return {'viable': False, 'reason': 'no force to move'}

        # Estimate power per card (rough average)
        avg_power_per_card = our_power_here / max(our_card_count, 1)

        # How many cards can we afford to move?
        max_cards_to_move = min(force_available, our_card_count - 1)  # Leave at least 1 card
        if max_cards_to_move < 1:
            return {'viable': False, 'reason': 'not enough force or cards'}

        max_moveable_power = int(max_cards_to_move * avg_power_per_card)

        # Analyze adjacent locations
        best_opportunity = None
        best_score = 0

        for adj_idx in [loc_idx - 1, loc_idx + 1]:
            if adj_idx < 0 or adj_idx >= len(board_state.locations):
                continue

            their_power = board_state.their_power_at_location(adj_idx)
            our_power_there = board_state.my_power_at_location(adj_idx)
            potential_power = our_power_there + max_moveable_power

            # Skip if we already have good presence
            if our_power_there >= ESTABLISH_THRESHOLD and their_power == 0:
                continue

            if their_power == 0:
                # Empty location - can we establish?
                if potential_power >= ESTABLISH_THRESHOLD:
                    # Great - can establish!
                    score = GOOD_DELTA * 2
                    cards_needed = max(1, int((ESTABLISH_THRESHOLD - our_power_there) / avg_power_per_card + 0.5))
                    opportunity = {
                        'adj_idx': adj_idx,
                        'their_power': 0,
                        'action': 'establish',
                        'cards_needed': cards_needed,
                        'score': score,
                        'reason': f"Can establish at empty location (move {cards_needed} cards, {int(cards_needed * avg_power_per_card)} power)"
                    }
                    if score > best_score:
                        best_score = score
                        best_opportunity = opportunity
            else:
                # Contested - can we beat them with margin?
                power_needed = their_power + CONTEST_MARGIN
                if potential_power >= power_needed:
                    # Can contest!
                    score = GOOD_DELTA * 3 + their_power / 2  # Bonus for contesting stronger enemies
                    cards_needed = max(1, int((power_needed - our_power_there) / avg_power_per_card + 0.5))
                    cards_needed = min(cards_needed, max_cards_to_move)
                    opportunity = {
                        'adj_idx': adj_idx,
                        'their_power': their_power,
                        'action': 'contest',
                        'cards_needed': cards_needed,
                        'score': score,
                        'reason': f"Can contest loc with {their_power} enemies (move {cards_needed}+ cards)"
                    }
                    if score > best_score:
                        best_score = score
                        best_opportunity = opportunity

        if best_opportunity:
            return {
                'viable': True,
                'reason': best_opportunity['reason'],
                'score': best_opportunity['score']
            }

        # No viable opportunity - explain why
        # Check what the adjacent locations have
        adjacent_powers = []
        for adj_idx in [loc_idx - 1, loc_idx + 1]:
            if 0 <= adj_idx < len(board_state.locations):
                their_power = board_state.their_power_at_location(adj_idx)
                our_power = board_state.my_power_at_location(adj_idx)
                adjacent_powers.append((adj_idx, their_power, our_power))

        if not adjacent_powers:
            return {'viable': False, 'reason': 'no adjacent locations'}

        # Find the issue
        for adj_idx, their_power, our_power in adjacent_powers:
            if their_power > 0:
                power_needed = their_power + CONTEST_MARGIN
                if max_moveable_power < power_needed - our_power:
                    return {
                        'viable': False,
                        'reason': f"need {power_needed - our_power} power to contest {their_power} enemies, can only move {max_moveable_power}"
                    }
            elif our_power >= ESTABLISH_THRESHOLD:
                # Already established there
                continue
            else:
                if max_moveable_power < ESTABLISH_THRESHOLD - our_power:
                    return {
                        'viable': False,
                        'reason': f"need {ESTABLISH_THRESHOLD - our_power} power to establish, can only move {max_moveable_power}"
                    }

        return {'viable': False, 'reason': 'no good adjacent locations'}

    def reset_pending_moves(self):
        """Reset pending move tracking (call at turn start)"""
        self.pending_move_card_ids.clear()

    def track_move(self, card_id: str):
        """Track that we tried moving this card"""
        self.pending_move_card_ids.add(card_id)

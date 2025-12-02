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
        if context.board_state and not context.board_state.is_my_turn():
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

        # Sanitize negative power values (GEMP may send -1 for empty/invalid)
        if their_power < 0:
            logger.debug(f"⚠️ Negative enemy power ({their_power}) at loc {loc_idx} - treating as 0")
            their_power = 0

        power_diff = my_power - their_power

        # Calculate threat level FRESH from current power values
        # (Don't use stale cached values from game_strategy - those are outdated after deploy phase)
        if game_strategy and their_power > 0:
            # Get thresholds from config
            favorable = game_strategy._get_config('BATTLE_FAVORABLE_THRESHOLD', 4)
            danger = game_strategy._get_config('BATTLE_DANGER_THRESHOLD', -6)

            # Calculate fresh threat level
            if power_diff >= favorable + 4:  # CRUSH
                threat_level = ThreatLevel.CRUSH
            elif power_diff >= favorable:  # FAVORABLE
                threat_level = ThreatLevel.FAVORABLE
            elif power_diff >= -favorable:  # RISKY (contested)
                threat_level = ThreatLevel.RISKY
            elif power_diff >= danger:  # DANGEROUS
                threat_level = ThreatLevel.DANGEROUS
            else:  # RETREAT
                threat_level = ThreatLevel.RETREAT

            logger.debug(f"🏃 Fresh threat level at loc {loc_idx}: power_diff={power_diff}, threat={threat_level.value}")

            if threat_level == ThreatLevel.RETREAT:
                # Definitely should retreat - we're badly outmatched
                action.add_reasoning(f"Strategic retreat - badly outmatched ({power_diff})", VERY_GOOD_DELTA)
                return
            elif threat_level == ThreatLevel.DANGEROUS:
                # Should consider retreating
                action.add_reasoning(f"Dangerous location - retreat recommended ({power_diff})", GOOD_DELTA * 2)
                return
            elif threat_level in [ThreatLevel.CRUSH, ThreatLevel.FAVORABLE]:
                # We have the advantage - don't retreat!
                action.add_reasoning(f"Power advantage ({power_diff}) - stay and fight!", BAD_DELTA * 2)
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
        # - We must RETAIN control after moving - don't spread if it would leave us below establish threshold
        ESTABLISH_THRESHOLD = 6  # Power needed to control uncontested location

        # Only spread if we have EXCESS power beyond what's needed for control
        power_needed_to_stay = max(their_power + OVERKILL_THRESHOLD, ESTABLISH_THRESHOLD)
        excess_power = my_power - power_needed_to_stay

        if excess_power >= 2 and my_card_count >= 2:  # Need some excess to spread
            logger.debug(f"🔍 Spread check: my_power={my_power}, power_needed={power_needed_to_stay}, excess={excess_power}")
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

        Prioritizes locations with opponent icons (enables force drains!).

        Returns dict with:
            viable: bool
            reason: str
            score: float (if viable)
        """
        ESTABLISH_THRESHOLD = 6  # Power needed to establish presence
        CONTEST_MARGIN = 4  # Extra power needed to safely contest
        ICON_BONUS = 15.0  # Score bonus per opponent icon (force drain value)

        force_available = board_state.force_pile
        if force_available < 1:
            return {'viable': False, 'reason': 'no force to move'}

        # Get opponent power at source location to know how much we need to retain
        their_power_here = board_state.their_power_at_location(loc_idx)
        if their_power_here < 0:
            their_power_here = 0

        # Calculate how much power we must retain at source
        power_to_retain = max(their_power_here + CONTEST_MARGIN, ESTABLISH_THRESHOLD)

        # Estimate power per card (rough average)
        avg_power_per_card = our_power_here / max(our_card_count, 1)

        # Calculate how many cards we can move while retaining enough power
        power_we_can_spare = our_power_here - power_to_retain
        if power_we_can_spare < 2:
            return {'viable': False, 'reason': f'need {power_to_retain} power to retain control, only have {our_power_here}'}

        # How many cards can we move? (limited by: force, spare power, leaving at least 1 card)
        cards_by_force = force_available
        cards_by_power = int(power_we_can_spare / avg_power_per_card) if avg_power_per_card > 0 else 0
        cards_by_count = our_card_count - 1  # Leave at least 1 card

        max_cards_to_move = min(cards_by_force, cards_by_power, cards_by_count)
        if max_cards_to_move < 1:
            return {'viable': False, 'reason': f'not enough cards to spare (force={cards_by_force}, power={cards_by_power}, count={cards_by_count})'}

        max_moveable_power = int(max_cards_to_move * avg_power_per_card)

        # Analyze adjacent locations
        best_opportunity = None
        best_score = 0

        # Use proper adjacency check from board_state
        adjacent_locs = board_state.find_adjacent_locations(loc_idx)
        logger.debug(f"🔍 Spread analysis from loc {loc_idx}: adjacent={adjacent_locs}, my_power={our_power_here}, max_moveable={max_moveable_power}")

        for adj_idx in adjacent_locs:
            their_power = board_state.their_power_at_location(adj_idx)
            our_power_there = board_state.my_power_at_location(adj_idx)
            potential_power = our_power_there + max_moveable_power

            # Get opponent icons at this location (enables force drains!)
            their_icons = 0
            if adj_idx < len(board_state.locations):
                adj_loc = board_state.locations[adj_idx]
                their_icons_str = adj_loc.their_icons or ""
                if their_icons_str and their_icons_str != "0":
                    try:
                        their_icons = int(their_icons_str.replace("*", "").strip() or "0")
                    except ValueError:
                        their_icons = 0

            logger.debug(f"   Adj loc {adj_idx}: their_power={their_power}, our_power={our_power_there}, their_icons={their_icons}")

            # Treat negative power as 0 (GEMP may send -1 for invalid/empty)
            if their_power < 0:
                logger.warning(f"⚠️ Negative enemy power ({their_power}) at loc {adj_idx} - treating as 0")
                their_power = 0

            # Skip if we already have good presence
            if our_power_there >= ESTABLISH_THRESHOLD and their_power == 0:
                continue

            if their_power == 0:
                # Empty location - can we establish?
                if potential_power >= ESTABLISH_THRESHOLD:
                    # Base score for establishing
                    score = GOOD_DELTA * 2
                    # Add bonus for opponent icons (force drain potential!)
                    icon_score = their_icons * ICON_BONUS
                    score += icon_score
                    cards_needed = max(1, int((ESTABLISH_THRESHOLD - our_power_there) / avg_power_per_card + 0.5))

                    reason = f"Can establish at empty location (move {cards_needed} cards, {int(cards_needed * avg_power_per_card)} power)"
                    if their_icons > 0:
                        reason += f" - {their_icons} opponent icon(s) = force drain!"

                    opportunity = {
                        'adj_idx': adj_idx,
                        'their_power': 0,
                        'their_icons': their_icons,
                        'action': 'establish',
                        'cards_needed': cards_needed,
                        'score': score,
                        'reason': reason
                    }
                    if score > best_score:
                        best_score = score
                        best_opportunity = opportunity
            else:
                # Contested - can we beat them with margin?
                power_needed = their_power + CONTEST_MARGIN
                if potential_power >= power_needed:
                    # Base score for contesting (+ bonus for contesting stronger enemies)
                    score = GOOD_DELTA * 3 + their_power / 2
                    # Add bonus for opponent icons (force drain potential!)
                    icon_score = their_icons * ICON_BONUS
                    score += icon_score
                    cards_needed = max(1, int((power_needed - our_power_there) / avg_power_per_card + 0.5))
                    cards_needed = min(cards_needed, max_cards_to_move)

                    reason = f"Can contest loc with {their_power} enemies (move {cards_needed}+ cards)"
                    if their_icons > 0:
                        reason += f" - {their_icons} opponent icon(s) = force drain!"

                    opportunity = {
                        'adj_idx': adj_idx,
                        'their_power': their_power,
                        'their_icons': their_icons,
                        'action': 'contest',
                        'cards_needed': cards_needed,
                        'score': score,
                        'reason': reason
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
        # Check what the adjacent locations have (reuse adjacent_locs from earlier)
        if not adjacent_locs:
            return {'viable': False, 'reason': 'no adjacent locations'}

        # Find the issue
        for adj_idx in adjacent_locs:
            their_power = max(0, board_state.their_power_at_location(adj_idx))  # Treat negative as 0
            our_power = board_state.my_power_at_location(adj_idx)
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

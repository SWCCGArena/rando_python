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
- OFFENSIVE ATTACKS from uncontested strongholds (NEW)
"""

import logging
from typing import List, Optional, Tuple
from .base import ActionEvaluator, DecisionContext, EvaluatedAction, ActionType
from ..game_strategy import GameStrategy, ThreatLevel
from ..strategy_config import get_config

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIG-DRIVEN PARAMETERS
# =============================================================================

def _get_move_config(key: str, default):
    """Get move strategy config value."""
    return get_config().get('move_strategy', key, default)

def _get_weight(key: str, default: float) -> float:
    """Get evaluator weight."""
    return get_config().get_weight('move', key, default)

# Rank deltas
def get_very_good_delta() -> float:
    return _get_weight('very_good_delta', 150.0)

def get_good_delta() -> float:
    return _get_weight('good_delta', 10.0)

def get_bad_delta() -> float:
    return _get_weight('bad_delta', -10.0)

def get_very_bad_delta() -> float:
    return _get_weight('very_bad_delta', -150.0)

# Move thresholds
def get_power_diff_for_flee() -> int:
    return _get_move_config('power_diff_for_flee', 2)

def get_power_diff_for_buildup() -> int:
    return _get_move_config('power_diff_for_buildup', 12)

def get_overkill_threshold() -> int:
    return _get_move_config('overkill_threshold', 4)

def get_contest_power_margin() -> int:
    return _get_move_config('contest_power_margin', 2)

# Offensive attack thresholds
def get_attack_power_advantage() -> int:
    return _get_move_config('attack_power_advantage', 4)

def get_attack_min_power() -> int:
    return _get_move_config('attack_min_power', 6)

def get_attack_score_base() -> float:
    return _get_move_config('attack_score_base', 50.0)

def get_attack_crush_bonus() -> float:
    return _get_move_config('attack_crush_bonus', 25.0)

# Inline constants (for establish/contest logic)
def get_establish_threshold() -> int:
    return _get_move_config('establish_threshold', 6)

def get_contest_margin() -> int:
    return _get_move_config('contest_margin', 4)

def get_icon_bonus() -> float:
    return _get_move_config('icon_bonus', 15.0)


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
                action.add_reasoning("Already tried moving this card", get_very_bad_delta())
                actions.append(action)
                continue

            if bs and card_id:
                card = bs.cards_in_play.get(card_id)
                if card:
                    loc_idx = card.location_index
                    self._rank_move_from_location(action, bs, loc_idx, card_id, game_strategy)
                else:
                    action.add_reasoning("Card not found in play", get_bad_delta())
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
        Enhanced with offensive attack logic for moving from strongholds.
        """
        if loc_idx < 0 or loc_idx >= len(board_state.locations):
            action.add_reasoning("Invalid location index", get_bad_delta())
            return

        my_power = board_state.my_power_at_location(loc_idx)
        their_power_raw = board_state.their_power_at_location(loc_idx)
        my_card_count = board_state.my_card_count_at_location(loc_idx) if hasattr(board_state, 'my_card_count_at_location') else 0

        # IMPORTANT: -1 means "no cards present" vs 0 meaning "cards with 0 power"
        # This distinction matters for attack decisions
        their_has_cards = their_power_raw >= 0
        their_power = max(0, their_power_raw)  # For calculations, treat as 0

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
                action.add_reasoning(f"Strategic retreat - badly outmatched ({power_diff})", get_very_good_delta())
                return
            elif threat_level == ThreatLevel.DANGEROUS:
                # Should consider retreating
                action.add_reasoning(f"Dangerous location - retreat recommended ({power_diff})", get_good_delta() * 2)
                return
            elif threat_level in [ThreatLevel.CRUSH, ThreatLevel.FAVORABLE]:
                # We have the advantage - don't retreat!
                action.add_reasoning(f"Power advantage ({power_diff}) - stay and fight!", get_bad_delta() * 2)
                return

        # Flee logic: their power > our power + 2
        # The worse the disadvantage, the more we want to flee!
        # BUT: Check if fleeing is actually beneficial (destination analysis)
        if their_power - my_power > get_power_diff_for_flee() and their_power > 0:
            disadvantage = their_power - my_power

            # Analyze flee options to see if it's worth it
            loc = board_state.locations[loc_idx] if loc_idx < len(board_state.locations) else None
            is_space = loc.is_space if loc else False
            flee_analysis = board_state.analyze_flee_options(loc_idx, is_space)

            # Check movement cost - can we afford to move everyone?
            if not flee_analysis['can_afford']:
                movement_cost = flee_analysis['movement_cost']
                action.add_reasoning(f"Want to flee but can't afford ({movement_cost} Force needed, have {board_state.force_pile})", get_bad_delta())
                logger.info(f"🚫 Can't afford to flee: need {movement_cost} Force, have {board_state.force_pile}")
                return

            # Check if destination is actually better
            if flee_analysis['can_flee']:
                best_dest = flee_analysis['best_destination']
                if best_dest is not None:
                    dest_their_power = board_state.their_power_at_location(best_dest)

                    if dest_their_power >= their_power:
                        # Destination is WORSE or same - don't flee into more trouble!
                        action.add_reasoning(f"Destination has {dest_their_power} enemies (same or worse) - don't flee!", get_bad_delta() * 2)
                        logger.info(f"🚫 Not fleeing: destination has {dest_their_power} enemies vs {their_power} here")
                        return
                    elif dest_their_power > 0:
                        # Destination has some enemies but fewer
                        if disadvantage >= 6:
                            action.add_reasoning(f"FLEEING to location with fewer enemies ({dest_their_power} vs {their_power})", get_very_good_delta())
                            logger.info(f"🏃 Fleeing from {their_power} enemies to {dest_their_power}")
                        else:
                            action.add_reasoning(f"Fleeing to location with fewer enemies ({dest_their_power} vs {their_power})", get_good_delta() * 2)
                        return
                    else:
                        # Destination is empty - great!
                        if disadvantage >= 6:
                            action.add_reasoning(f"FLEEING to EMPTY location (escaping {their_power} enemies!)", get_very_good_delta())
                            logger.info(f"🏃 Fleeing from {their_power} enemies to empty location!")
                        elif disadvantage >= 4:
                            action.add_reasoning(f"Fleeing to empty location (escaping {their_power})", get_good_delta() * 3)
                        else:
                            action.add_reasoning(f"Moving to empty location (enemy has {their_power})", get_good_delta())
                        return

            # Can't find valid flee destination
            reason = flee_analysis['reason']
            action.add_reasoning(f"Want to flee but no good destination: {reason}", get_bad_delta())
            return

        # =================================================================
        # OFFENSIVE ATTACK LOGIC (NEW)
        # =================================================================
        # If we're at an uncontested location with significant power,
        # look for adjacent enemy locations we can attack and crush.
        # This makes the bot more aggressive and interactive!
        #
        # Key scenario: 12 power at Location A (uncontested), enemy has 7 power
        # at adjacent Location B. We should ATTACK by moving to crush them.
        # =================================================================

        if not their_has_cards and my_power >= get_attack_min_power() and my_card_count >= 2:
            # We're at an uncontested location with a stronghold - look for attack targets
            attack_analysis = self._analyze_attack_opportunity(
                board_state, loc_idx, my_power, my_card_count
            )

            if attack_analysis['viable']:
                reason = attack_analysis['reason']
                score = attack_analysis['score']
                action.add_reasoning(reason, score)
                logger.info(f"⚔️ ATTACK opportunity: {reason} (score={score})")
                return

        # Spread out / contest logic: we have power advantage
        # BUT we must check if spreading is actually viable:
        # - How much force do we have to move cards?
        # - Can we move ENOUGH power to establish (6) or contest (beat enemy + margin)?
        # - We must RETAIN control after moving - don't spread if it would leave us below establish threshold
        establish_threshold = get_establish_threshold()

        # Only spread if we have EXCESS power beyond what's needed for control
        power_needed_to_stay = max(their_power + get_overkill_threshold(), establish_threshold)
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
                action.add_reasoning(f"Can't spread: {reason}", get_bad_delta())
                return

        # Default: not a good time to move
        action.add_reasoning("No good reason to move", get_bad_delta())

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
        establish_threshold = get_establish_threshold()
        contest_margin = get_contest_margin()
        icon_bonus = get_icon_bonus()

        force_available = board_state.force_pile
        if force_available < 1:
            return {'viable': False, 'reason': 'no force to move'}

        # Get opponent power at source location to know how much we need to retain
        their_power_here = board_state.their_power_at_location(loc_idx)
        if their_power_here < 0:
            their_power_here = 0

        # Calculate how much power we must retain at source
        power_to_retain = max(their_power_here + contest_margin, establish_threshold)

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
            their_power_raw = board_state.their_power_at_location(adj_idx)
            our_power_there = board_state.my_power_at_location(adj_idx)
            potential_power = our_power_there + max_moveable_power

            # IMPORTANT: -1 means "no cards present" vs 0 meaning "cards with 0 power"
            their_has_cards = their_power_raw >= 0
            their_power = max(0, their_power_raw)  # For calculations

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

            logger.debug(f"   Adj loc {adj_idx}: their_power={their_power_raw} (has_cards={their_has_cards}), our_power={our_power_there}, their_icons={their_icons}")

            # Skip if we already have good presence
            if our_power_there >= establish_threshold and their_power == 0:
                continue

            # Empty location (no cards OR 0-power cards)
            if their_power == 0:
                # Empty location - can we establish?
                if potential_power >= establish_threshold:
                    # Base score for establishing
                    score = get_good_delta() * 2
                    # Add bonus for opponent icons (force drain potential!)
                    icon_score = their_icons * icon_bonus
                    score += icon_score
                    cards_needed = max(1, int((establish_threshold - our_power_there) / avg_power_per_card + 0.5))

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
                power_needed = their_power + contest_margin
                if potential_power >= power_needed:
                    # Base score for contesting (+ bonus for contesting stronger enemies)
                    score = get_good_delta() * 3 + their_power / 2
                    # Add bonus for opponent icons (force drain potential!)
                    icon_score = their_icons * icon_bonus
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
                power_needed = their_power + contest_margin
                if max_moveable_power < power_needed - our_power:
                    return {
                        'viable': False,
                        'reason': f"need {power_needed - our_power} power to contest {their_power} enemies, can only move {max_moveable_power}"
                    }
            elif our_power >= establish_threshold:
                # Already established there
                continue
            else:
                if max_moveable_power < establish_threshold - our_power:
                    return {
                        'viable': False,
                        'reason': f"need {establish_threshold - our_power} power to establish, can only move {max_moveable_power}"
                    }

        return {'viable': False, 'reason': 'no good adjacent locations'}

    def _analyze_attack_opportunity(self, board_state, loc_idx: int,
                                     our_power_here: int, our_card_count: int) -> dict:
        """
        Analyze if we can attack an adjacent enemy position from our stronghold.

        This is OFFENSIVE logic - we're at an uncontested location and looking
        to move to ATTACK enemies at adjacent locations.

        Key criteria:
        - We have significantly more power than them
        - We can afford to move enough cards
        - Moving would give us power advantage at the target
        - We retain some presence at our source (optional but preferred)

        Returns dict with:
            viable: bool
            reason: str
            score: float (if viable)
            target_idx: int (if viable)
        """
        establish_threshold = get_establish_threshold()
        icon_bonus = get_icon_bonus()

        force_available = board_state.force_pile
        if force_available < 1:
            return {'viable': False, 'reason': 'no force to move'}

        # Estimate power per card
        avg_power_per_card = our_power_here / max(our_card_count, 1)

        # How many cards can we move? (limited by force and count)
        # For attacks, we might want to move ALL cards if target is juicy enough
        max_cards_to_move = min(force_available, our_card_count)
        max_moveable_power = int(max_cards_to_move * avg_power_per_card)

        # Also calculate conservative move (leave some behind)
        cards_to_leave = 1 if our_card_count > 2 else 0
        conservative_cards = min(force_available, our_card_count - cards_to_leave)
        conservative_power = int(conservative_cards * avg_power_per_card)

        # Find adjacent locations with enemies
        adjacent_locs = board_state.find_adjacent_locations(loc_idx)
        logger.debug(f"⚔️ Attack analysis from loc {loc_idx}: adjacent={adjacent_locs}, "
                    f"my_power={our_power_here}, max_moveable={max_moveable_power}")

        best_attack = None
        best_score = 0

        for adj_idx in adjacent_locs:
            their_power_raw = board_state.their_power_at_location(adj_idx)

            # -1 means no cards, 0 means 0-power cards present
            their_has_cards = their_power_raw >= 0
            their_power = max(0, their_power_raw)

            # Skip empty locations (no one to attack) - use spread logic for those
            if not their_has_cards or their_power == 0:
                continue

            our_power_there = board_state.my_power_at_location(adj_idx)

            # Get opponent icons (affects score - attacking high-icon locations is valuable)
            their_icons = 0
            if adj_idx < len(board_state.locations):
                adj_loc = board_state.locations[adj_idx]
                their_icons_str = adj_loc.their_icons or ""
                if their_icons_str and their_icons_str != "0":
                    try:
                        their_icons = int(their_icons_str.replace("*", "").strip() or "0")
                    except ValueError:
                        their_icons = 0

            logger.debug(f"   Attack target loc {adj_idx}: their_power={their_power}, "
                        f"our_power={our_power_there}, their_icons={their_icons}")

            # Calculate attack scenarios

            # Scenario 1: All-in attack (move everyone)
            all_in_power = our_power_there + max_moveable_power
            all_in_advantage = all_in_power - their_power

            # Scenario 2: Conservative attack (leave some behind)
            conservative_total = our_power_there + conservative_power
            conservative_advantage = conservative_total - their_power

            # Determine if attack is viable
            # Need at least get_attack_power_advantage() to make it worth it
            if conservative_advantage >= get_attack_power_advantage():
                # Good attack with conservative approach
                cards_needed = max(1, int((their_power + get_attack_power_advantage() - our_power_there) / avg_power_per_card + 0.5))
                cards_needed = min(cards_needed, conservative_cards)

                # Calculate score
                score = get_attack_score_base()
                # Bonus for crushing attacks (2x their power)
                if conservative_total >= their_power * 2:
                    score += get_attack_crush_bonus()
                    crush_text = "CRUSH "
                else:
                    crush_text = ""
                # Bonus for opponent icons (force drain denial + potential flip)
                score += their_icons * icon_bonus
                # Bonus for bigger enemy forces (more impactful win)
                score += their_power / 2

                reason = (f"{crush_text}ATTACK {their_power} enemies with {conservative_total} power "
                         f"(move {cards_needed} cards, +{conservative_advantage} advantage)")
                if their_icons > 0:
                    reason += f" - deny {their_icons} icon drain!"

                attack = {
                    'target_idx': adj_idx,
                    'their_power': their_power,
                    'their_icons': their_icons,
                    'our_total_power': conservative_total,
                    'advantage': conservative_advantage,
                    'cards_needed': cards_needed,
                    'all_in': False,
                    'score': score,
                    'reason': reason
                }

                if score > best_score:
                    best_score = score
                    best_attack = attack

            elif all_in_advantage >= get_attack_power_advantage():
                # Only viable with all-in attack - riskier but consider it
                cards_needed = max_cards_to_move

                score = get_attack_score_base() - 10  # Slight penalty for all-in
                if all_in_power >= their_power * 2:
                    score += get_attack_crush_bonus()
                    crush_text = "CRUSH "
                else:
                    crush_text = ""
                score += their_icons * icon_bonus
                score += their_power / 2

                reason = (f"{crush_text}ALL-IN ATTACK {their_power} enemies with {all_in_power} power "
                         f"(move ALL {cards_needed} cards, +{all_in_advantage} advantage)")
                if their_icons > 0:
                    reason += f" - deny {their_icons} icon drain!"

                attack = {
                    'target_idx': adj_idx,
                    'their_power': their_power,
                    'their_icons': their_icons,
                    'our_total_power': all_in_power,
                    'advantage': all_in_advantage,
                    'cards_needed': cards_needed,
                    'all_in': True,
                    'score': score,
                    'reason': reason
                }

                if score > best_score:
                    best_score = score
                    best_attack = attack

        if best_attack:
            return {
                'viable': True,
                'reason': best_attack['reason'],
                'score': best_attack['score'],
                'target_idx': best_attack['target_idx']
            }

        return {'viable': False, 'reason': 'no attackable enemies at adjacent locations'}

    def reset_pending_moves(self):
        """Reset pending move tracking (call at turn start)"""
        self.pending_move_card_ids.clear()

    def track_move(self, card_id: str):
        """Track that we tried moving this card"""
        self.pending_move_card_ids.add(card_id)

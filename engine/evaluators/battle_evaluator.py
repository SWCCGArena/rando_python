"""
Battle Evaluator

Handles battle initiation decisions.
Ported from C# AICACHandler.cs RankBattleAction

Decision factors:
- Power differential (my power - their power)
- Ability test (ability >= 5 or card count >= 3)
- Reserve deck (need cards for destiny draws)
- Strategic threat assessment from GameStrategy
- Opponent weapon presence (increases required advantage)

Enhanced with threat levels (conservative thresholds to account for attrition):
- CRUSH: Power advantage 8+ -> definitely battle
- FAVORABLE: Power advantage 6-7 -> battle recommended
- MARGINAL: Power advantage 4-5 -> battle if no weapons
- RISKY: Power diff 0 to +3 -> avoid unless necessary
- DANGEROUS: Power disadvantage -> avoid/retreat
"""

import logging
from typing import List, Optional
from .base import ActionEvaluator, DecisionContext, EvaluatedAction, ActionType
from ..game_strategy import GameStrategy, ThreatLevel
from ..strategy_profile import get_current_profile, StrategyMode
from ..strategy_config import get_config
from ..card_loader import get_card
from ..deck_tracker import get_deck_tracker

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIG-DRIVEN PARAMETERS
# =============================================================================

def _get_battle_config(key: str, default):
    """Get battle strategy config value."""
    return get_config().get('battle_strategy', key, default)

def _get_weight(key: str, default: float) -> float:
    """Get evaluator weight."""
    return get_config().get_weight('battle', key, default)

# Rank deltas
def get_very_good_delta() -> float:
    return _get_weight('very_good_delta', 150.0)

def get_good_delta() -> float:
    return _get_weight('good_delta', 10.0)

def get_bad_delta() -> float:
    return _get_weight('bad_delta', -10.0)

def get_very_bad_delta() -> float:
    return _get_weight('very_bad_delta', -150.0)

# Battle thresholds
def get_power_diff_for_battle() -> int:
    return _get_battle_config('power_diff_for_battle', 4)

def get_ability_test_high() -> int:
    return _get_battle_config('ability_test_high', 4)

def get_ability_test_low() -> int:
    return _get_battle_config('ability_test_low', 3)


# =============================================================================
# DESTINY PROBABILITY ASSESSMENT
# =============================================================================

def assess_destiny_quality() -> dict:
    """
    Assess our destiny draw quality using the DeckTracker.

    Returns a dict with:
    - expected_destiny: float (average destiny value we expect to draw)
    - high_destiny_prob: float (probability of drawing destiny >= 5)
    - has_knowledge: bool (whether we have direct knowledge vs estimate)
    - destiny_advantage: float (how much our destiny quality should adjust battle threshold)
      - Positive = good destiny, can be more aggressive
      - Negative = poor destiny, should be more conservative
    """
    tracker = get_deck_tracker()

    if not tracker.deck_loaded:
        # No deck tracking - use neutral estimates
        return {
            'expected_destiny': 3.0,  # Assume average
            'high_destiny_prob': 0.25,  # Assume ~25% high destiny
            'has_knowledge': False,
            'destiny_advantage': 0.0,
        }

    expected = tracker.expected_destiny()
    high_prob = tracker.probability_destiny_at_least(5)
    has_knowledge = tracker.knowledge_state.value != 'unknown'

    # Calculate destiny advantage:
    # - Average expected destiny is ~3.0 for balanced decks
    # - Above 4.0 = excellent (can be aggressive)
    # - Below 2.5 = poor (should be conservative)
    #
    # Adjust battle threshold:
    # - +2 power equivalent for expected_destiny >= 4.5
    # - +1 power equivalent for expected_destiny >= 3.5
    # - -1 power equivalent for expected_destiny < 2.5
    # - -2 power equivalent for expected_destiny < 2.0

    if expected >= 4.5:
        destiny_advantage = 2.0
    elif expected >= 3.5:
        destiny_advantage = 1.0
    elif expected >= 2.5:
        destiny_advantage = 0.0
    elif expected >= 2.0:
        destiny_advantage = -1.0
    else:
        destiny_advantage = -2.0

    # Boost advantage if we have direct knowledge (we KNOW what's coming)
    if has_knowledge:
        if destiny_advantage > 0:
            destiny_advantage *= 1.5  # Even more confident when we know
        elif destiny_advantage < 0:
            destiny_advantage *= 1.5  # Even more cautious when we know it's bad

    # High destiny probability also contributes
    # 50%+ chance of high destiny = bonus
    # <15% chance = penalty
    if high_prob >= 0.5:
        destiny_advantage += 0.5
    elif high_prob < 0.15:
        destiny_advantage -= 0.5

    return {
        'expected_destiny': expected,
        'high_destiny_prob': high_prob,
        'has_knowledge': has_knowledge,
        'destiny_advantage': destiny_advantage,
    }


class BattleEvaluator(ActionEvaluator):
    """
    Evaluates battle initiation decisions.

    Considers:
    - Power differential at location
    - Ability requirements
    - Reserve deck status (for destiny draws)
    - Strategic threat assessment from GameStrategy
    - Opponent weapons at location (lightsabers, blasters, etc.)
    """

    def __init__(self):
        super().__init__("Battle")

    def _count_opponent_weapons_at_location(self, board_state, loc_idx: int) -> tuple[int, list[str], float]:
        """
        Count opponent weapons at a location and calculate total threat.

        Weapon threat is based on wielder's ability:
        - Higher ability = more accurate weapon fire
        - Lightsaber on Luke (ability 6) is more dangerous than blaster on trooper (ability 1)

        Returns:
            Tuple of (weapon_count, list of weapon names, total_threat_score)
        """
        weapon_count = 0
        weapon_names = []
        total_threat = 0.0

        if loc_idx >= len(board_state.locations):
            return 0, [], 0.0

        loc = board_state.locations[loc_idx]
        if not loc:
            return 0, [], 0.0

        # Check each opponent card at the location
        for card in loc.their_cards:
            # Get wielder's ability (default 1 if unknown)
            wielder_ability = getattr(card, 'ability', None) or 1

            # Check attached cards (weapons are usually attached to characters)
            for attached in card.attached_cards:
                if attached.card_type and attached.card_type.lower() == "weapon":
                    weapon_count += 1
                    weapon_names.append(attached.card_title or attached.blueprint_id)

                    # Calculate weapon threat based on wielder ability
                    # Base threat: 5 (any weapon is dangerous)
                    # Ability bonus: wielder_ability * 3 (higher ability = better accuracy)
                    # So: ability 1 = 8 threat, ability 6 = 23 threat
                    weapon_threat = 5.0 + (wielder_ability * 3.0)
                    total_threat += weapon_threat
                    logger.debug(f"   Weapon {attached.card_title}: wielder ability {wielder_ability}, threat {weapon_threat}")

        return weapon_count, weapon_names, total_threat

    def _get_game_strategy(self, context: DecisionContext) -> Optional[GameStrategy]:
        """Get GameStrategy from board_state's strategy_controller"""
        if context.board_state and context.board_state.strategy_controller:
            return context.board_state.strategy_controller.game_strategy
        return None

    def can_evaluate(self, context: DecisionContext) -> bool:
        """Handle CARD_ACTION_CHOICE with battle initiation during OUR turn only"""
        if context.decision_type not in ['CARD_ACTION_CHOICE', 'ACTION_CHOICE']:
            return False

        # CRITICAL: Only evaluate battle initiation during OUR turn
        # During opponent's turn, we can't initiate battles - we're defending
        if context.board_state and not context.board_state.is_my_turn():
            logger.debug(f"⚔️ BattleEvaluator skipping - not our turn")
            return False

        # Check if any action is a battle action (case-insensitive, includes variants)
        # Matches: "Initiate battle", "Initiate battle for free", etc.
        for action_text in context.action_texts:
            if "initiate battle" in action_text.lower():
                return True

        # Also check decision text for battle context
        decision_lower = (context.decision_text or "").lower()
        if "battle action" in decision_lower:
            return True

        return False

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Evaluate battle initiation options"""
        actions = []
        bs = context.board_state
        game_strategy = self._get_game_strategy(context)

        for i, action_id in enumerate(context.action_ids):
            action_text = context.action_texts[i] if i < len(context.action_texts) else ""
            action_lower = action_text.lower()

            # Match any battle initiation action (includes "for free" variants)
            if "initiate battle" not in action_lower:
                continue

            action = EvaluatedAction(
                action_id=action_id,
                action_type=ActionType.BATTLE,
                score=0.0,
                display_text=action_text
            )

            # BONUS: "for free" is always better than paying!
            if "for free" in action_lower:
                action.add_reasoning("Battle for FREE - no force cost!", get_good_delta() * 3)

            if bs:
                # Get card ID to determine location
                card_id = context.card_ids[i] if i < len(context.card_ids) else None

                if card_id:
                    # Find the location
                    card = bs.cards_in_play.get(card_id)
                    if card and card.location_index >= 0:
                        loc_idx = card.location_index
                        logger.debug(f"⚔️ Battle location found via card {card_id}: index {loc_idx}")
                        self._rank_battle_at_location(action, bs, loc_idx, game_strategy)
                    elif bs.locations:
                        # Fallback: Find a CONTESTED location (where we both have cards)
                        # Don't just pick the first location!
                        contested_idx = None
                        for idx, loc in enumerate(bs.locations):
                            if loc and loc.my_cards and loc.their_cards:
                                contested_idx = idx
                                logger.debug(f"⚔️ Battle location found via contested search: {loc.site_name} (idx {idx})")
                                break

                        if contested_idx is not None:
                            self._rank_battle_at_location(action, bs, contested_idx, game_strategy)
                        else:
                            # No contested location - this shouldn't happen during battle
                            logger.warning(f"⚔️ No contested location found for battle!")
                            action.add_reasoning("No contested location for battle", 0.0)
                    else:
                        action.add_reasoning("No location info", 0.0)
                else:
                    # No card ID - search for contested location
                    if bs.locations:
                        contested_idx = None
                        for idx, loc in enumerate(bs.locations):
                            if loc and loc.my_cards and loc.their_cards:
                                contested_idx = idx
                                logger.debug(f"⚔️ Battle location (no card_id): {loc.site_name} (idx {idx})")
                                break

                        if contested_idx is not None:
                            self._rank_battle_at_location(action, bs, contested_idx, game_strategy)
                        else:
                            logger.warning(f"⚔️ No card_id and no contested location!")
                            action.add_reasoning("No card ID or contested location", 0.0)
                    else:
                        action.add_reasoning("No card ID for battle location", 0.0)
            else:
                # No board state - be cautious
                action.add_reasoning("No board state - cautious", get_bad_delta())

            actions.append(action)

        return actions

    def _rank_battle_at_location(self, action: EvaluatedAction, board_state, loc_idx: int,
                                   game_strategy: Optional[GameStrategy] = None):
        """
        Rank a battle at a specific location.

        Ported from C# AICACHandler.RankBattleAction
        Enhanced with threat level assessment from GameStrategy.
        Enhanced with dynamic strategy profiles for game-state-aware decisions.
        """
        # Log config values being used for this decision
        logger.info(f"📊 BATTLE CONFIG: power_diff_for_battle={get_power_diff_for_battle()}, "
                   f"ability_test_high={get_ability_test_high()}, ability_test_low={get_ability_test_low()}, "
                   f"good_delta={get_good_delta()}, very_good_delta={get_very_good_delta()}")

        # Get strategy profile for risk tolerance adjustment
        profile = get_current_profile(board_state)
        risk_tolerance = profile.risk_tolerance  # Negative = need MORE advantage, positive = accept disadvantage
        battle_multiplier = profile.battle_multiplier

        # =================================================================
        # DROID CONTROL CHECK: Cannot initiate battle with only droids
        # In SWCCG, you need "presence" (ability > 0) to initiate battles.
        # Droids have ability = 0 and don't provide presence.
        # =================================================================
        if board_state.is_droid_only_at_location(loc_idx, is_mine=True):
            action.add_reasoning("Droids only - cannot initiate battle (no presence)", get_very_bad_delta())
            logger.info(f"⚔️ DROID ONLY at location {loc_idx} - cannot initiate battle!")
            return

        # Apply strategy-based reasoning
        if profile.mode == StrategyMode.DESPERATION:
            action.add_reasoning("DESPERATION: Taking risky battles!", 15.0)
        elif profile.mode == StrategyMode.AGGRESSIVE:
            action.add_reasoning("AGGRESSIVE: More willing to fight", 8.0)
        elif profile.mode == StrategyMode.DEFENSIVE:
            action.add_reasoning("DEFENSIVE: Being picky about battles", -10.0)
        elif profile.mode == StrategyMode.CRUSHING:
            action.add_reasoning("CRUSHING: Only fight sure wins", -20.0)

        my_power = board_state.my_power_at_location(loc_idx)
        their_power = board_state.their_power_at_location(loc_idx)
        my_ability = board_state.my_ability_at_location(loc_idx)
        my_card_count = board_state.my_card_count_at_location(loc_idx) if hasattr(board_state, 'my_card_count_at_location') else 0
        their_card_count = board_state.their_card_count_at_location(loc_idx) if hasattr(board_state, 'their_card_count_at_location') else 0

        # Apply risk_tolerance: positive = accept more risk, negative = need more advantage
        # This effectively shifts our perception of the power differential
        power_diff = my_power - their_power + risk_tolerance
        ability_test = (my_ability >= get_ability_test_high() or my_card_count >= get_ability_test_low())

        # Get location name for logging
        loc = board_state.locations[loc_idx] if loc_idx < len(board_state.locations) else None
        loc_name = loc.site_name if loc else f"location #{loc_idx}"
        is_space = loc.is_space if loc else False

        # Detect opponent weapons at this location (with ability-based threat scoring)
        weapon_count, weapon_names, weapon_threat = self._count_opponent_weapons_at_location(board_state, loc_idx)

        # Log the battle analysis for debugging
        logger.info(f"⚔️ BATTLE ANALYSIS at {loc_name}:")
        logger.info(f"   Power: {my_power} (me) vs {their_power} (them) = diff {power_diff}")
        logger.info(f"   Cards: {my_card_count} (me) vs {their_card_count} (them)")
        logger.info(f"   Ability: {my_ability}, ability test: {ability_test}")
        if weapon_count > 0:
            logger.info(f"   ⚠️ WEAPONS: {weapon_count} opponent weapons (threat={weapon_threat:.0f}): {', '.join(weapon_names)}")

        # Check if this location is marked for fleeing in the deploy plan
        # Note: Flee status is tracked on LocationAnalysis.should_flee during plan creation
        if hasattr(board_state, 'current_deploy_plan') and board_state.current_deploy_plan:
            plan = board_state.current_deploy_plan
            # Check if any location analysis marked this location for fleeing
            if hasattr(plan, 'target_locations'):
                for analysis in plan.target_locations:
                    if hasattr(analysis, 'should_flee') and analysis.should_flee:
                        if hasattr(analysis, 'location_index') and analysis.location_index == loc_idx:
                            logger.warning(f"   ⚠️ FLEE PLAN EXISTS for this location! Should flee, not battle!")
                            action.add_reasoning(f"FLEE PLAN EXISTS - should flee, not battle!", get_very_bad_delta())
                            return

        # =================================================================
        # OPPONENT DAMAGE CANCEL AWARENESS (Houjix/Ghhhk - 24-28% of decks)
        # If opponent has interrupt cards and we're winning big, they might
        # cancel our battle damage. Apply a risk factor.
        # =================================================================
        damage_cancel_risk = 0.0
        their_hand = getattr(board_state, 'their_hand_size', 0)
        if their_hand > 0 and power_diff >= 6:
            # High probability opponent has interrupts - discount expected damage
            # Don't make us avoid battle, just temper expectations
            damage_cancel_risk = -5.0  # Modest penalty
            action.add_reasoning(
                f"Opponent may have damage cancel ({their_hand} cards in hand)",
                damage_cancel_risk
            )
            logger.debug(f"⚔️ Damage cancel risk: opponent has {their_hand} cards, power_diff={power_diff}")

        # Check reserve deck for destiny draws
        reserve_count = board_state.reserve_deck if hasattr(board_state, 'reserve_deck') else 10

        if reserve_count <= 0:
            action.add_reasoning("No reserve cards for destiny - avoid battle", get_very_bad_delta())
            return

        # =================================================================
        # SPECIAL CASE: 1-on-1 battle where we have less power
        # This is ALWAYS bad - we'll lose our character and gain nothing
        # Should flee instead if possible
        # =================================================================
        if my_card_count == 1 and their_card_count == 1 and power_diff < 0:
            # Check if we can flee (loc and is_space already defined above)
            flee_analysis = board_state.analyze_flee_options(loc_idx, is_space)

            if flee_analysis['can_flee'] and flee_analysis['can_afford']:
                action.add_reasoning(
                    f"1-on-1 LOSING BATTLE ({my_power} vs {their_power}) - FLEE INSTEAD!",
                    get_very_bad_delta()
                )
                logger.info(f"🏃 1-on-1 losing battle: {my_power} vs {their_power} - should flee!")
            else:
                # Can't flee - this is a bad situation but battle might be forced
                reason = flee_analysis.get('reason', 'unknown')
                action.add_reasoning(
                    f"1-on-1 losing ({my_power} vs {their_power}), can't flee ({reason}) - bad situation",
                    get_bad_delta() * 3
                )
                logger.warning(f"⚔️ 1-on-1 losing battle, can't flee: {reason}")
            return

        # =================================================================
        # MASSIVELY OUTPOWERED CHECK - NEVER INITIATE SUICIDAL BATTLES
        # If opponent has 10+ power advantage, this is a guaranteed loss.
        # No amount of destiny luck will save us. This is the "2 power
        # character vs 20 power army" scenario players complain about.
        # =================================================================
        massive_deficit_threshold = -10  # 10+ power disadvantage
        if power_diff <= massive_deficit_threshold:
            action.add_reasoning(
                f"SUICIDAL BATTLE: {my_power} power vs {their_power} (deficit {power_diff}) - NEVER INITIATE!",
                get_very_bad_delta() * 3  # -450 penalty
            )
            logger.warning(f"🚫 BLOCKED BATTLE at {loc_name}: {my_power} vs {their_power} - massively outpowered!")
            return

        # Also block if we have less than 4 power total - we're too weak to battle
        if my_power < 4:
            action.add_reasoning(
                f"TOO WEAK TO BATTLE: Only {my_power} power - don't initiate!",
                get_very_bad_delta() * 2  # -300 penalty
            )
            logger.warning(f"🚫 BLOCKED BATTLE at {loc_name}: Only {my_power} power - too weak to initiate!")
            return

        # Calculate threat level FRESH from current power values
        # (Don't use stale cached values from game_strategy - those are outdated after deploy phase)
        # CONSERVATIVE: Account for weapons which can swing battles before they resolve
        # NOTE: game_strategy is optional - use defaults if not available
        if their_power > 0:
            # Get thresholds from config (but use more conservative values)
            if game_strategy:
                favorable = game_strategy._get_config('BATTLE_FAVORABLE_THRESHOLD', 4)
                danger = game_strategy._get_config('BATTLE_DANGER_THRESHOLD', -6)
            else:
                # Default thresholds when game_strategy is not available
                favorable = 4
                danger = -6

            # Calculate effective power diff accounting for weapons (using threat-based scoring)
            # weapon_threat is based on wielder ability: ability 6 = 23 threat, ability 1 = 8 threat
            # Convert threat to effective power penalty: ~10 threat = ~1 power disadvantage
            weapon_power_penalty = weapon_threat / 5.0  # Higher ability weapons are more impactful

            # =================================================================
            # DESTINY PROBABILITY ASSESSMENT (DeckTracker-based)
            # Use actual deck composition to estimate our destiny draw quality.
            # This gives us real probability-based advantage instead of generic estimates.
            #
            # If we CAN'T draw destiny (ability < 4), we're at massive disadvantage
            # because they'll add destiny to their total and we won't.
            # =================================================================
            destiny_variance_penalty = 0.0
            destiny_quality = assess_destiny_quality()

            # Check if opponent can draw battle destiny
            their_ability = board_state.their_ability_at_location(loc_idx) if hasattr(board_state, 'their_ability_at_location') else 4
            they_can_draw_destiny = their_ability >= 4 or their_card_count >= 3

            # Check if WE can draw battle destiny
            we_can_draw_destiny = my_ability >= 4 or my_card_count >= 3

            if they_can_draw_destiny and not we_can_draw_destiny:
                # CRITICAL: They draw destiny, we don't!
                # Expected swing: ~3.5 in their favor (their average destiny vs our 0)
                destiny_variance_penalty = 5.0
                action.add_reasoning(
                    f"DANGER: They draw destiny (ability {their_ability}), we can't (ability {my_ability})!",
                    -25.0
                )
                logger.warning(f"⚔️ DESTINY DISADVANTAGE: they draw (ability {their_ability}), we don't ({my_ability})")
            elif they_can_draw_destiny and we_can_draw_destiny:
                # Both draw - use DeckTracker to assess our destiny quality
                # destiny_advantage: positive = our destiny is above average, negative = below
                expected_destiny = destiny_quality['expected_destiny']
                high_prob = destiny_quality['high_destiny_prob']
                destiny_advantage = destiny_quality['destiny_advantage']
                has_knowledge = destiny_quality['has_knowledge']

                # Base variance penalty (assumes average opponent destiny of ~3.0)
                # Reduce penalty based on our destiny advantage
                destiny_variance_penalty = 3.0 - destiny_advantage

                # Log the destiny assessment
                knowledge_str = " [KNOWN]" if has_knowledge else ""
                if destiny_advantage >= 1.5:
                    action.add_reasoning(
                        f"🎯 Excellent destiny (E={expected_destiny:.1f}, P(5+)={high_prob:.0%}){knowledge_str} - battle confident!",
                        20.0
                    )
                    logger.info(f"⚔️ DESTINY ADVANTAGE: E[destiny]={expected_destiny:.1f}, P(5+)={high_prob:.0%}, adv={destiny_advantage:+.1f}{knowledge_str}")
                elif destiny_advantage >= 0.5:
                    action.add_reasoning(
                        f"🎯 Good destiny (E={expected_destiny:.1f}, P(5+)={high_prob:.0%}){knowledge_str}",
                        10.0
                    )
                    logger.info(f"⚔️ Good destiny: E[destiny]={expected_destiny:.1f}, P(5+)={high_prob:.0%}{knowledge_str}")
                elif destiny_advantage <= -1.5:
                    action.add_reasoning(
                        f"⚠️ Poor destiny (E={expected_destiny:.1f}, P(5+)={high_prob:.0%}){knowledge_str} - need bigger margin!",
                        -15.0
                    )
                    logger.warning(f"⚔️ POOR DESTINY: E[destiny]={expected_destiny:.1f}, P(5+)={high_prob:.0%}, adv={destiny_advantage:+.1f}{knowledge_str}")
                elif destiny_advantage <= -0.5:
                    action.add_reasoning(
                        f"⚠️ Below-average destiny (E={expected_destiny:.1f}){knowledge_str} - be cautious",
                        -8.0
                    )
                    logger.debug(f"⚔️ Below-average destiny: E[destiny]={expected_destiny:.1f}{knowledge_str}")
                else:
                    logger.debug(f"⚔️ Average destiny: E[destiny]={expected_destiny:.1f}, penalty={destiny_variance_penalty:.1f}")

            elif not they_can_draw_destiny and we_can_draw_destiny:
                # We draw, they don't - this is great! Use our actual expected destiny
                expected_destiny = destiny_quality['expected_destiny']
                # Our destiny draw is pure advantage (they get 0)
                destiny_variance_penalty = -expected_destiny  # Negative = bonus for us
                action.add_reasoning(
                    f"We draw destiny (E={expected_destiny:.1f}), they can't - major advantage!",
                    15.0 + expected_destiny * 3
                )

            effective_diff = power_diff - weapon_power_penalty - destiny_variance_penalty

            # Calculate fresh threat level with RAISED thresholds
            # Account for destiny variance - need MORE margin to be safe
            # Old: CRUSH at 8, FAVORABLE at 6
            # New: CRUSH at 10, STRONG at 8, FAVORABLE at 6, need +4 minimum for battle
            if effective_diff >= 10:  # CRUSH - overwhelming even with bad destiny
                threat_level = ThreatLevel.CRUSH
            elif effective_diff >= 8:  # Very strong - safe to battle
                threat_level = ThreatLevel.FAVORABLE
            elif effective_diff >= 6:  # Strong - probably safe
                threat_level = ThreatLevel.FAVORABLE
            elif effective_diff >= 4:  # Moderate - some risk
                threat_level = ThreatLevel.RISKY
            elif effective_diff >= 0:  # Even/slight - risky
                threat_level = ThreatLevel.RISKY
            elif effective_diff >= danger:  # DANGEROUS
                threat_level = ThreatLevel.DANGEROUS
            else:  # RETREAT
                threat_level = ThreatLevel.RETREAT

            logger.info(f"⚔️ Battle analysis at loc {loc_idx}: power_diff={power_diff}, "
                       f"weapon_penalty={weapon_power_penalty:.1f}, destiny_penalty={destiny_variance_penalty:.1f}, "
                       f"effective_diff={effective_diff:.1f}, threat={threat_level.value}")

            # Apply weapon penalty to score (ability-based: high ability weapons hurt more)
            if weapon_count > 0:
                action.add_reasoning(
                    f"Opponent has {weapon_count} weapon(s) (threat={weapon_threat:.0f}) - dangerous!",
                    -weapon_threat  # Use threat score directly as penalty
                )

            if threat_level == ThreatLevel.CRUSH:
                action.add_reasoning(f"Overwhelming advantage (+{power_diff}, effective +{effective_diff:.0f}) - crush them!", get_very_good_delta())
                return
            elif threat_level == ThreatLevel.FAVORABLE:
                if effective_diff >= 8:
                    action.add_reasoning(f"Strong battle odds (+{power_diff}, effective +{effective_diff:.0f})", get_good_delta() * 3)
                else:
                    action.add_reasoning(f"Good battle odds (+{power_diff}, effective +{effective_diff:.0f})", get_good_delta() * 2)
                return
            elif threat_level == ThreatLevel.RISKY:
                # Marginal advantage - risky due to attrition and destiny variance
                if effective_diff >= 4:
                    action.add_reasoning(f"Moderate advantage (+{power_diff}) but destiny risk - proceed cautiously", get_good_delta())
                else:
                    action.add_reasoning(f"Marginal advantage (+{power_diff}) - risky, attrition likely", get_good_delta() * 0.3)
                return
            elif threat_level == ThreatLevel.DANGEROUS:
                action.add_reasoning(f"Dangerous odds (+{power_diff}, effective {effective_diff:.0f}) - avoid battle", get_bad_delta() * 2)
                return
            elif threat_level == ThreatLevel.RETREAT:
                action.add_reasoning(f"Terrible odds (+{power_diff}) - definitely avoid!", get_very_bad_delta())
                return

        # =================================================================
        # ATTRITION IMMUNITY CHECK
        # Characters with "immune to attrition < X" survive battles better.
        # If our key characters are immune to expected attrition, we're safer.
        # =================================================================
        immunity_bonus = 0.0
        extra_destiny_bonus = 0.0
        expected_attrition = max(0, their_power - my_power)  # Rough estimate

        # Config values for immunity scoring
        immunity_bonus_high = _get_weight('immunity_bonus_high', 25.0)
        immunity_bonus_low = _get_weight('immunity_bonus_low', 15.0)
        immunity_ratio_high = get_config().get('gametext_abilities', 'immunity_ratio_high', 0.5)
        immunity_ratio_low = get_config().get('gametext_abilities', 'immunity_ratio_low', 0.25)
        extra_destiny_weight = _get_weight('extra_destiny_bonus', 20.0)

        if loc and loc.my_cards:
            immune_power = 0
            total_power = 0
            total_extra_destiny = 0

            for card in loc.my_cards:
                card_meta = get_card(card.blueprint_id) if hasattr(card, 'blueprint_id') else None
                if card_meta and card_meta.is_character:
                    card_power = card_meta.power_value
                    total_power += card_power

                    # Check attrition immunity
                    if card_meta.has_attrition_immunity:
                        threshold = card_meta.immune_attrition_threshold
                        if threshold > expected_attrition:
                            immune_power += card_power
                            logger.debug(f"   🛡️ {card_meta.title} immune to attrition < {threshold} (expected ~{expected_attrition})")

                    # Check extra destiny draws
                    if card_meta.draws_extra_destiny > 0:
                        total_extra_destiny += card_meta.draws_extra_destiny
                        logger.debug(f"   🎲 {card_meta.title} draws {card_meta.draws_extra_destiny} extra destiny")

            # Immunity bonus
            if immune_power > 0 and total_power > 0:
                immunity_ratio = immune_power / total_power
                if immunity_ratio >= immunity_ratio_high:
                    immunity_bonus = immunity_bonus_high
                    action.add_reasoning(f"Most characters immune to attrition ({immune_power}/{total_power} power)", immunity_bonus)
                elif immunity_ratio >= immunity_ratio_low:
                    immunity_bonus = immunity_bonus_low
                    action.add_reasoning(f"Some characters immune to attrition ({immune_power}/{total_power} power)", immunity_bonus)

            # Extra destiny bonus - more destiny draws = better battle outcomes
            if total_extra_destiny > 0:
                extra_destiny_bonus = total_extra_destiny * extra_destiny_weight
                action.add_reasoning(f"Draw {total_extra_destiny} extra battle destiny", extra_destiny_bonus)

        # Fallback: Battle logic when GameStrategy not available
        # CONSERVATIVE THRESHOLDS: A +2 power advantage can still result in losing
        # all your characters due to destiny-based attrition. Require higher margins.
        # Weapon presence requires even higher advantage (weapons fire before battle resolves).

        # Calculate effective power diff accounting for weapons and abilities
        # Each weapon's threat is based on wielder ability - high ability = more dangerous
        weapon_power_penalty = weapon_threat / 5.0  # Higher ability weapons are more impactful
        ability_bonus = (immunity_bonus + extra_destiny_bonus) / 10.0  # Convert bonuses to effective power

        # Apply destiny variance penalty (fallback: assume both can draw destiny)
        # Both draw destiny - account for variance (bad luck = ~3 point swing)
        destiny_variance = 3.0
        effective_diff = power_diff - weapon_power_penalty + ability_bonus - destiny_variance

        if weapon_count > 0:
            action.add_reasoning(
                f"Opponent has {weapon_count} weapon(s) (threat={weapon_threat:.0f}) - dangerous!",
                -weapon_threat  # Use threat score directly as penalty
            )

        # RAISED THRESHOLDS: Account for destiny variance
        # Old: crush at 8, strong at 6, good at 4
        # New: crush at 10, strong at 8, good at 6, moderate at 4
        if effective_diff >= 10:
            # Overwhelming advantage even with destiny variance - definitely battle
            action.add_reasoning(f"Crushing advantage (+{power_diff}, effective +{effective_diff:.0f}) - BATTLE!", get_very_good_delta())
        elif effective_diff >= 8:
            # Very strong advantage - highly recommended
            action.add_reasoning(f"Strong advantage (+{power_diff}, effective +{effective_diff:.0f}) - battle!", 80.0)
        elif effective_diff >= 6:
            # Good odds - battle recommended
            action.add_reasoning(f"Good advantage (+{power_diff}, effective +{effective_diff:.0f}) - battle!", 60.0)
        elif effective_diff >= 4:
            # Moderate advantage - some risk but okay
            if weapon_count > 0:
                action.add_reasoning(f"Moderate advantage (+{power_diff}) with weapons present - risky!", 30.0)
            else:
                action.add_reasoning(f"Moderate advantage (+{power_diff}) - acceptable risk", 45.0)
        elif effective_diff >= 2:
            # Marginal advantage - risky, attrition likely
            if weapon_count > 0:
                action.add_reasoning(f"Marginal advantage (+{power_diff}) with weapons present - risky!", 15.0)
            else:
                action.add_reasoning(f"Marginal advantage (+{power_diff}) - risky battle", 25.0)
        elif effective_diff >= 0:
            # Even or slight advantage - avoid unless forced
            if ability_test and weapon_count == 0:
                action.add_reasoning(f"Even fight ({power_diff}) with good ability - risky", 10.0)
            else:
                action.add_reasoning(f"Even fight ({power_diff}) - avoid, attrition likely", get_bad_delta())
        elif effective_diff >= -2:
            # Close fight but losing - avoid
            action.add_reasoning(f"Close fight ({power_diff}) - avoid, will take losses", get_bad_delta() * 2)
        elif effective_diff >= -5:
            # Disadvantage - avoid unless desperate
            action.add_reasoning(f"Losing battle ({power_diff}) - AVOID!", get_bad_delta() * 3)
        else:
            # Severe disadvantage (-6 or worse)
            # BUT: Check if we can actually flee! If not, battling now is better
            # than letting opponent build up even more power (loc and is_space defined above)
            flee_analysis = board_state.analyze_flee_options(loc_idx, is_space)

            if flee_analysis['can_flee'] and flee_analysis['can_afford']:
                # We CAN flee - so avoid battle, we'll escape in move phase
                best_dest = flee_analysis['best_destination']
                dest_their_power = board_state.their_power_at_location(best_dest) if best_dest is not None else 0

                if dest_their_power < their_power:
                    # Destination is better - definitely avoid battle and flee
                    action.add_reasoning(f"SEVERE DISADVANTAGE ({power_diff}) - will flee to safer location!", get_very_bad_delta())
                    logger.info(f"🏃 Will flee from {my_power} vs {their_power} to location with {dest_their_power} enemies")
                else:
                    # Destination is worse or same - might as well battle here
                    action.add_reasoning(f"Disadvantage ({power_diff}) but no better escape - reluctant battle", get_bad_delta() * 2)
                    logger.info(f"⚔️ No good escape (dest has {dest_their_power} enemies), battling here")
            else:
                # Can't flee - battling now is better than letting them build up
                reason = flee_analysis['reason']
                action.add_reasoning(f"Disadvantage ({power_diff}) but CAN'T FLEE ({reason}) - forced battle", get_bad_delta())
                logger.info(f"⚔️ Can't flee: {reason}. Battling at disadvantage is better than waiting.")

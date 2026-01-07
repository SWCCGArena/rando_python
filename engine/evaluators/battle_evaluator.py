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

logger = logging.getLogger(__name__)

# Rank deltas (from C# BotAIHelper) - normalized for better decision nuance
VERY_GOOD_DELTA = 150.0  # Reduced from 999 - crushing advantage still high but comparable
GOOD_DELTA = 10.0
BAD_DELTA = -10.0
VERY_BAD_DELTA = -150.0  # Reduced from -999 - bad odds still avoided but can be overridden

# Battle thresholds (from C# AICACHandler)
POWER_DIFF_FOR_BATTLE = 4  # Acceptable power disadvantage
ABILITY_TEST_HIGH = 4  # Ability needed for battle (C# uses >= 4)
ABILITY_TEST_LOW = 3  # Card count alternative to ability


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
                action.add_reasoning("Battle for FREE - no force cost!", GOOD_DELTA * 3)

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
                action.add_reasoning("No board state - cautious", BAD_DELTA)

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
        # Get strategy profile for risk tolerance adjustment
        profile = get_current_profile(board_state)
        risk_tolerance = profile.risk_tolerance  # Negative = need MORE advantage, positive = accept disadvantage
        battle_multiplier = profile.battle_multiplier

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
        ability_test = (my_ability >= ABILITY_TEST_HIGH or my_card_count >= ABILITY_TEST_LOW)

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
                            action.add_reasoning(f"FLEE PLAN EXISTS - should flee, not battle!", VERY_BAD_DELTA)
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
            action.add_reasoning("No reserve cards for destiny - avoid battle", VERY_BAD_DELTA)
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
                    VERY_BAD_DELTA
                )
                logger.info(f"🏃 1-on-1 losing battle: {my_power} vs {their_power} - should flee!")
            else:
                # Can't flee - this is a bad situation but battle might be forced
                reason = flee_analysis.get('reason', 'unknown')
                action.add_reasoning(
                    f"1-on-1 losing ({my_power} vs {their_power}), can't flee ({reason}) - bad situation",
                    BAD_DELTA * 3
                )
                logger.warning(f"⚔️ 1-on-1 losing battle, can't flee: {reason}")
            return

        # Calculate threat level FRESH from current power values
        # (Don't use stale cached values from game_strategy - those are outdated after deploy phase)
        # CONSERVATIVE: Account for weapons which can swing battles before they resolve
        if game_strategy and their_power > 0:
            # Get thresholds from config (but use more conservative values)
            favorable = game_strategy._get_config('BATTLE_FAVORABLE_THRESHOLD', 4)
            danger = game_strategy._get_config('BATTLE_DANGER_THRESHOLD', -6)

            # Calculate effective power diff accounting for weapons (using threat-based scoring)
            # weapon_threat is based on wielder ability: ability 6 = 23 threat, ability 1 = 8 threat
            # Convert threat to effective power penalty: ~10 threat = ~1 power disadvantage
            weapon_power_penalty = weapon_threat / 5.0  # Higher ability weapons are more impactful
            effective_diff = power_diff - weapon_power_penalty

            # Calculate fresh threat level with RAISED thresholds
            # Old: CRUSH at favorable+4 (8), FAVORABLE at favorable (4)
            # New: CRUSH at 8, FAVORABLE at 6, need +4 minimum for battle
            if effective_diff >= 8:  # CRUSH - overwhelming
                threat_level = ThreatLevel.CRUSH
            elif effective_diff >= 6:  # FAVORABLE - strong advantage
                threat_level = ThreatLevel.FAVORABLE
            elif effective_diff >= 4:  # Still worth fighting
                threat_level = ThreatLevel.FAVORABLE
            elif effective_diff >= 0:  # RISKY - marginal, avoid if possible
                threat_level = ThreatLevel.RISKY
            elif effective_diff >= danger:  # DANGEROUS
                threat_level = ThreatLevel.DANGEROUS
            else:  # RETREAT
                threat_level = ThreatLevel.RETREAT

            logger.debug(f"⚔️ Fresh threat level at loc {loc_idx}: power_diff={power_diff}, "
                        f"weapon_threat={weapon_threat:.0f}, effective_diff={effective_diff:.1f}, threat={threat_level.value}")

            # Apply weapon penalty to score (ability-based: high ability weapons hurt more)
            if weapon_count > 0:
                action.add_reasoning(
                    f"Opponent has {weapon_count} weapon(s) (threat={weapon_threat:.0f}) - dangerous!",
                    -weapon_threat  # Use threat score directly as penalty
                )

            if threat_level == ThreatLevel.CRUSH:
                action.add_reasoning(f"Overwhelming advantage (+{power_diff}) - crush them!", VERY_GOOD_DELTA)
                return
            elif threat_level == ThreatLevel.FAVORABLE:
                if effective_diff >= 6:
                    action.add_reasoning(f"Strong battle odds (+{power_diff})", GOOD_DELTA * 3)
                else:
                    action.add_reasoning(f"Good battle odds (+{power_diff})", GOOD_DELTA * 2)
                return
            elif threat_level == ThreatLevel.RISKY:
                # Marginal advantage - risky due to attrition
                action.add_reasoning(f"Marginal advantage ({power_diff}) - risky, attrition likely", GOOD_DELTA * 0.5)
                return
            elif threat_level == ThreatLevel.DANGEROUS:
                action.add_reasoning(f"Dangerous odds ({power_diff}) - avoid battle", BAD_DELTA * 2)
                return
            elif threat_level == ThreatLevel.RETREAT:
                action.add_reasoning(f"Terrible odds ({power_diff}) - definitely avoid!", VERY_BAD_DELTA)
                return

        # Fallback: Battle logic when GameStrategy not available
        # CONSERVATIVE THRESHOLDS: A +2 power advantage can still result in losing
        # all your characters due to destiny-based attrition. Require higher margins.
        # Weapon presence requires even higher advantage (weapons fire before battle resolves).

        # Calculate effective power diff accounting for weapons (using threat-based scoring)
        # Each weapon's threat is based on wielder ability - high ability = more dangerous
        weapon_power_penalty = weapon_threat / 5.0  # Higher ability weapons are more impactful
        effective_diff = power_diff - weapon_power_penalty

        if weapon_count > 0:
            action.add_reasoning(
                f"Opponent has {weapon_count} weapon(s) (threat={weapon_threat:.0f}) - dangerous!",
                -weapon_threat  # Use threat score directly as penalty
            )

        if effective_diff >= 8:
            # Overwhelming advantage - definitely battle
            action.add_reasoning(f"Crushing advantage (+{power_diff}) - BATTLE!", VERY_GOOD_DELTA)
        elif effective_diff >= 6:
            # Strong advantage - highly recommended
            action.add_reasoning(f"Strong advantage (+{power_diff}) - battle!", 80.0)
        elif effective_diff >= 4:
            # Good odds - battle recommended
            action.add_reasoning(f"Good advantage (+{power_diff}) - battle!", 60.0)
        elif effective_diff >= 2:
            # Marginal advantage - risky, attrition likely
            if weapon_count > 0:
                action.add_reasoning(f"Marginal advantage (+{power_diff}) with weapons present - risky!", 20.0)
            else:
                action.add_reasoning(f"Marginal advantage (+{power_diff}) - risky battle", 35.0)
        elif effective_diff >= 0:
            # Even or slight advantage - avoid unless forced
            if ability_test and weapon_count == 0:
                action.add_reasoning(f"Even fight ({power_diff}) with good ability - risky", 15.0)
            else:
                action.add_reasoning(f"Even fight ({power_diff}) - avoid, attrition likely", BAD_DELTA)
        elif effective_diff >= -2:
            # Close fight but losing - avoid
            action.add_reasoning(f"Close fight ({power_diff}) - avoid, will take losses", BAD_DELTA * 2)
        elif effective_diff >= -5:
            # Disadvantage - avoid unless desperate
            action.add_reasoning(f"Losing battle ({power_diff}) - AVOID!", BAD_DELTA * 3)
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
                    action.add_reasoning(f"SEVERE DISADVANTAGE ({power_diff}) - will flee to safer location!", VERY_BAD_DELTA)
                    logger.info(f"🏃 Will flee from {my_power} vs {their_power} to location with {dest_their_power} enemies")
                else:
                    # Destination is worse or same - might as well battle here
                    action.add_reasoning(f"Disadvantage ({power_diff}) but no better escape - reluctant battle", BAD_DELTA * 2)
                    logger.info(f"⚔️ No good escape (dest has {dest_their_power} enemies), battling here")
            else:
                # Can't flee - battling now is better than letting them build up
                reason = flee_analysis['reason']
                action.add_reasoning(f"Disadvantage ({power_diff}) but CAN'T FLEE ({reason}) - forced battle", BAD_DELTA)
                logger.info(f"⚔️ Can't flee: {reason}. Battling at disadvantage is better than waiting.")

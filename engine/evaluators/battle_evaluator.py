"""
Battle Evaluator

Handles battle initiation decisions.
Ported from C# AICACHandler.cs RankBattleAction

Decision factors:
- Power differential (my power - their power)
- Ability test (ability >= 5 or card count >= 3)
- Reserve deck (need cards for destiny draws)
- Strategic threat assessment from GameStrategy

Enhanced with threat levels:
- CRUSH: Power advantage 6+ -> definitely battle
- FAVORABLE: Power advantage 2-5 -> battle recommended
- RISKY: Power diff -2 to +2 -> preemptive battle (contested is dangerous)
- DANGEROUS: Power disadvantage 2-6 -> avoid unless necessary
- RETREAT: Power disadvantage 6+ -> don't battle, consider retreat
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
    """

    def __init__(self):
        super().__init__("Battle")

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
        """
        my_power = board_state.my_power_at_location(loc_idx)
        their_power = board_state.their_power_at_location(loc_idx)
        my_ability = board_state.my_ability_at_location(loc_idx)
        my_card_count = board_state.my_card_count_at_location(loc_idx) if hasattr(board_state, 'my_card_count_at_location') else 0
        their_card_count = board_state.their_card_count_at_location(loc_idx) if hasattr(board_state, 'their_card_count_at_location') else 0

        power_diff = my_power - their_power
        ability_test = (my_ability >= ABILITY_TEST_HIGH or my_card_count >= ABILITY_TEST_LOW)

        # Get location name for logging
        loc = board_state.locations[loc_idx] if loc_idx < len(board_state.locations) else None
        loc_name = loc.site_name if loc else f"location #{loc_idx}"
        is_space = loc.is_space if loc else False

        # Log the battle analysis for debugging
        logger.info(f"⚔️ BATTLE ANALYSIS at {loc_name}:")
        logger.info(f"   Power: {my_power} (me) vs {their_power} (them) = diff {power_diff}")
        logger.info(f"   Cards: {my_card_count} (me) vs {their_card_count} (them)")
        logger.info(f"   Ability: {my_ability}, ability test: {ability_test}")

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

            logger.debug(f"⚔️ Fresh threat level at loc {loc_idx}: power_diff={power_diff}, threat={threat_level.value}")

            if threat_level == ThreatLevel.CRUSH:
                action.add_reasoning(f"Overwhelming advantage (+{power_diff}) - crush them!", VERY_GOOD_DELTA)
                return
            elif threat_level == ThreatLevel.FAVORABLE:
                action.add_reasoning(f"Good battle odds (+{power_diff})", GOOD_DELTA * 2)
                return
            elif threat_level == ThreatLevel.RISKY:
                # Contested locations are threats - better to battle now than let them reinforce
                action.add_reasoning(f"Contested location - preemptive battle", GOOD_DELTA)
                return
            elif threat_level == ThreatLevel.DANGEROUS:
                action.add_reasoning(f"Dangerous odds ({power_diff}) - avoid battle", BAD_DELTA * 2)
                return
            elif threat_level == ThreatLevel.RETREAT:
                action.add_reasoning(f"Terrible odds ({power_diff}) - definitely avoid!", VERY_BAD_DELTA)
                return

        # Fallback: Battle logic when GameStrategy not available
        # IMPORTANT: When we have a power advantage, we should ALMOST ALWAYS battle!
        # Battles are how we win the game - force drains are based on control.
        # Scores need to be HIGH (50+) to beat PassEvaluator's "save force" bonuses.

        if power_diff >= 6:
            # Overwhelming advantage - definitely battle
            action.add_reasoning(f"Crushing advantage (+{power_diff}) - BATTLE!", VERY_GOOD_DELTA)
        elif power_diff >= 4:
            # Strong advantage - highly recommended
            action.add_reasoning(f"Strong advantage (+{power_diff}) - battle!", 80.0)
        elif power_diff >= 2:
            # Good odds - battle recommended (score high enough to beat Pass)
            action.add_reasoning(f"Good advantage (+{power_diff}) - battle!", 60.0)
        elif power_diff >= 0:
            # Even fight - still worth battling to contest
            action.add_reasoning(f"Even fight ({power_diff}) - battle to contest", 40.0)
        elif power_diff >= -2:
            # Close fight - only battle if we have good ability for destiny
            if ability_test:
                action.add_reasoning(f"Close fight ({power_diff}) but good ability ({my_ability}) - risky battle", GOOD_DELTA)
            else:
                action.add_reasoning(f"Close fight ({power_diff}) without ability - avoid", BAD_DELTA)
        elif power_diff >= -5:
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

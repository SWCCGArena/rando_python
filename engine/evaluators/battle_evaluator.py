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
        """Handle CARD_ACTION_CHOICE with battle actions"""
        if context.decision_type not in ['CARD_ACTION_CHOICE', 'ACTION_CHOICE']:
            return False

        # Check if any action is a battle action
        for action_text in context.action_texts:
            if action_text == "Initiate battle":
                return True

        return False

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Evaluate battle initiation options"""
        actions = []
        bs = context.board_state
        game_strategy = self._get_game_strategy(context)

        for i, action_id in enumerate(context.action_ids):
            action_text = context.action_texts[i] if i < len(context.action_texts) else ""

            if action_text != "Initiate battle":
                continue

            action = EvaluatedAction(
                action_id=action_id,
                action_type=ActionType.BATTLE,
                score=0.0,
                display_text=action_text
            )

            if bs:
                # Get card ID to determine location
                card_id = context.card_ids[i] if i < len(context.card_ids) else None

                if card_id:
                    # Find the location
                    card = bs.cards_in_play.get(card_id)
                    if card:
                        loc_idx = card.location_index
                        self._rank_battle_at_location(action, bs, loc_idx, game_strategy)
                    else:
                        # Use first location as fallback
                        if bs.locations:
                            self._rank_battle_at_location(action, bs, 0, game_strategy)
                        else:
                            action.add_reasoning("No location info", 0.0)
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

        power_diff = my_power - their_power
        ability_test = (my_ability >= ABILITY_TEST_HIGH or my_card_count >= ABILITY_TEST_LOW)

        # Check reserve deck for destiny draws
        reserve_count = board_state.reserve_deck if hasattr(board_state, 'reserve_deck') else 10

        if reserve_count <= 0:
            action.add_reasoning("No reserve cards for destiny - avoid battle", VERY_BAD_DELTA)
            return

        # Use GameStrategy threat assessment if available
        if game_strategy:
            threat_level = game_strategy.get_location_threat(loc_idx)

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

        # Fallback: Original C# battle logic
        # Battle logic from C# RankBattleAction
        # Check conditions in order from C#
        if power_diff >= -POWER_DIFF_FOR_BATTLE and ability_test:
            # diff >= -4 with ability test passed
            action.add_reasoning(f"Power diff {power_diff} with ability {my_ability} - good chance", GOOD_DELTA)
        elif power_diff > POWER_DIFF_FOR_BATTLE or (ability_test and power_diff >= 0):
            # diff > 4 OR (ability test and diff >= 0) -> can crush
            action.add_reasoning(f"Power diff {power_diff} - can crush opponent", GOOD_DELTA)
        elif power_diff > 2:
            # diff > 2 but failed ability test - risky
            action.add_reasoning(f"Power diff {power_diff} - risky without ability, trying anyway", GOOD_DELTA)
        else:
            # No good conditions met - avoid battle
            action.add_reasoning(f"Power diff {power_diff} - avoid battle", BAD_DELTA)

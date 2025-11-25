"""
Draw Evaluator

Handles card draw decisions.
Ported from C# AICACHandler.cs RankDrawAction

Decision factors:
- Hand size (target ~7-8 cards, soft cap 12, hard cap 16)
- Reserve deck status (don't deck out)
- Force pile available
- Strategy (deploy vs hold)
- Force generation deficit (draw to find locations if low gen)
"""

import logging
from typing import List, Optional
from .base import ActionEvaluator, DecisionContext, EvaluatedAction, ActionType
from ..game_strategy import GameStrategy, HAND_SOFT_CAP, HAND_HARD_CAP

logger = logging.getLogger(__name__)

# Rank deltas (from C# BotAIHelper)
VERY_GOOD_DELTA = 999.0
GOOD_DELTA = 10.0
BAD_DELTA = -10.0
VERY_BAD_DELTA = -999.0

# Draw thresholds (from C# AICACHandler)
TARGET_HAND_SIZE = 7  # Target hand size before extra draw penalties
MAX_HAND_SIZE = HAND_HARD_CAP  # Hard cap on hand size (from GameStrategy)
LOW_RESERVE_THRESHOLD = 6  # Stop drawing when reserve is low
SMALL_HAND_THRESHOLD = 5  # Hand size to consider drawing
AGGRESSIVE_FORCE_THRESHOLD = 10  # Force pile for aggressive draw

# Dynamic hand size thresholds
DECK_SIZE_FOR_FULL_HAND = 12  # Below this combined deck size, reduce max hand
FORCE_RESERVE_TURN_THRESHOLD = 4  # Start reserving force after this turn
SMALL_HAND_FOR_RESERVE = 6  # Hand size threshold for force reservation


class DrawEvaluator(ActionEvaluator):
    """
    Evaluates card draw decisions.

    Considers:
    - Current hand size (soft cap 12, hard cap 16)
    - Reserve deck status
    - Force availability
    - Current strategy
    - Force generation deficit (draw to find locations)
    """

    def __init__(self):
        super().__init__("Draw")

    def _get_game_strategy(self, context: DecisionContext) -> Optional[GameStrategy]:
        """Get GameStrategy from board_state's strategy_controller"""
        if context.board_state and context.board_state.strategy_controller:
            return context.board_state.strategy_controller.game_strategy
        return None

    def can_evaluate(self, context: DecisionContext) -> bool:
        """Handle CARD_ACTION_CHOICE with draw actions"""
        if context.decision_type not in ['CARD_ACTION_CHOICE', 'ACTION_CHOICE']:
            return False

        # Check if any action is a draw action
        for action_text in context.action_texts:
            if action_text == "Draw card into hand from Force Pile":
                return True

        return False

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Evaluate draw card options"""
        actions = []
        bs = context.board_state
        game_strategy = self._get_game_strategy(context)

        for i, action_id in enumerate(context.action_ids):
            action_text = context.action_texts[i] if i < len(context.action_texts) else ""

            if action_text != "Draw card into hand from Force Pile":
                continue

            action = EvaluatedAction(
                action_id=action_id,
                action_type=ActionType.DRAW,
                score=0.0,
                display_text=action_text
            )

            if bs:
                self._rank_draw_action(action, bs, game_strategy)
            else:
                # No board state - be neutral
                action.add_reasoning("No board state - neutral draw", 0.0)

            actions.append(action)

        return actions

    def _rank_draw_action(self, action: EvaluatedAction, board_state,
                          game_strategy: Optional[GameStrategy] = None):
        """
        Rank the draw action based on game state.

        Ported from C# AICACHandler.RankDrawAction
        Enhanced with GameStrategy hand size caps and force gen awareness.
        """
        hand_size = board_state.hand_size if hasattr(board_state, 'hand_size') else 7
        reserve = board_state.total_reserve_force() if hasattr(board_state, 'total_reserve_force') else 20
        force_pile = board_state.force_pile if hasattr(board_state, 'force_pile') else 5
        turn_number = board_state.turn_number if hasattr(board_state, 'turn_number') else 1

        # === DYNAMIC MAX HAND SIZE ===
        # If we're running low on cards in deck, reduce effective max hand size
        # Every card below 12 in combined piles reduces max hand by 1
        effective_max_hand = MAX_HAND_SIZE
        if reserve < DECK_SIZE_FOR_FULL_HAND:
            deck_deficit = DECK_SIZE_FOR_FULL_HAND - reserve
            effective_max_hand = max(6, MAX_HAND_SIZE - deck_deficit)  # Floor at 6
            if hand_size >= effective_max_hand:
                penalty = BAD_DELTA * 2 * (hand_size - effective_max_hand + 1)
                action.add_reasoning(
                    f"Low deck ({reserve}) - effective max hand {effective_max_hand}",
                    penalty
                )

        # === FORCE RESERVATION FOR OPPONENT'S TURN ===
        # After turn 4, keep some force for reactions/battles
        if turn_number >= FORCE_RESERVE_TURN_THRESHOLD:
            # Reserve 1 force if hand < 6, reserve 2 if hand >= 6
            force_to_reserve = 1 if hand_size < SMALL_HAND_FOR_RESERVE else 2

            if force_pile <= force_to_reserve:
                action.add_reasoning(
                    f"Turn {turn_number}: reserve {force_to_reserve} force for reactions",
                    BAD_DELTA * 1.5
                )

        # C# Logic 1: Don't draw if low reserve (avoid decking)
        # badActionDelta * (6 - reserve)
        if reserve <= LOW_RESERVE_THRESHOLD:
            penalty = BAD_DELTA * (LOW_RESERVE_THRESHOLD - reserve)
            action.add_reasoning(f"Low reserve ({reserve}) - avoid drawing", penalty)

        # C# Logic 2: Draw if hand is smaller than target and enough reserve
        if hand_size < TARGET_HAND_SIZE and reserve > 10 and force_pile > 1:
            action.add_reasoning(f"Hand size {hand_size} < {TARGET_HAND_SIZE} - draw to fill", GOOD_DELTA)

        # C# Logic 3: Draw if hand is very small
        if hand_size <= SMALL_HAND_THRESHOLD and reserve > 4 and force_pile > 1:
            action.add_reasoning(f"Small hand ({hand_size}) - draw cards", GOOD_DELTA)

        # C# Logic 4: Aggressive draw if we have lots of force (YOLO)
        if force_pile > AGGRESSIVE_FORCE_THRESHOLD:
            action.add_reasoning(f"High force pile ({force_pile}) - YOLO draw", GOOD_DELTA)

        # C# Logic 5: On Hold strategy but hand is weak, still draw
        if force_pile > 5 and hand_size <= 4:
            action.add_reasoning("Weak hand - draw even on hold", GOOD_DELTA)

        # Strategic hand size management (soft cap 12, hard cap 16)
        if game_strategy:
            # Apply GameStrategy hand size penalty
            hand_penalty = game_strategy.get_hand_size_penalty(hand_size)
            if hand_penalty < 0:
                action.add_reasoning(f"Hand size {hand_size} above soft cap", hand_penalty)

            # Force generation deficit - draw to find locations
            if game_strategy.should_prioritize_drawing_for_locations(hand_size):
                action.add_reasoning(f"Low force gen ({game_strategy.my_force_generation}) - draw for locations", GOOD_DELTA)
        else:
            # Fallback: C# Logic 6: Penalty for hand over max size
            # Use effective_max_hand which accounts for low deck
            if hand_size >= effective_max_hand:
                overflow = hand_size - effective_max_hand
                action.add_reasoning(f"Hand full ({hand_size}/{effective_max_hand}) - avoid drawing", BAD_DELTA * overflow)
            elif hand_size >= HAND_SOFT_CAP:
                overflow = hand_size - HAND_SOFT_CAP
                action.add_reasoning(f"Hand getting full ({hand_size})", BAD_DELTA * overflow * 0.5)

        # C# Logic 7: Save last force
        if force_pile == 1:
            action.add_reasoning("Last force - save it", BAD_DELTA)

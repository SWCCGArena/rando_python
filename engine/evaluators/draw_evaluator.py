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
- Future turn planning (save force for expensive cards)
- Late-game life force preservation
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

# Late-game thresholds
LATE_GAME_LIFE_FORCE = 12  # Below this, be more strategic about draws
CRITICAL_LIFE_FORCE = 6  # Below this, minimize draws
EXPENSIVE_CARD_THRESHOLD = 8  # Cards costing this much need force saving


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

        # Check if decision text mentions "Draw" - this is the primary trigger
        decision_lower = (context.decision_text or "").lower()
        if "draw" in decision_lower and "action" in decision_lower:
            logger.info(f"🎴 DrawEvaluator triggered by decision text: '{context.decision_text}'")
            logger.info(f"   Action texts available: {context.action_texts}")
            return True

        # Also check if any action is a draw action (case-insensitive, flexible matching)
        for action_text in context.action_texts:
            action_lower = action_text.lower()
            if "draw" in action_lower:
                logger.info(f"🎴 DrawEvaluator triggered by action text: '{action_text}'")
                return True

        return False

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Evaluate draw card options"""
        actions = []
        bs = context.board_state
        game_strategy = self._get_game_strategy(context)

        for i, action_id in enumerate(context.action_ids):
            action_text = context.action_texts[i] if i < len(context.action_texts) else ""

            # Flexible matching for draw actions (case-insensitive)
            # Match any action containing "draw" (e.g., "Draw", "Draw card", "Draw card into hand from Force Pile")
            action_lower = action_text.lower()
            if "draw" not in action_lower:
                continue

            logger.info(f"🎴 Evaluating draw action: '{action_text}' (id={action_id})")

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
        Enhanced with:
        - GameStrategy hand size caps and force gen awareness
        - Future turn planning (save force for expensive cards)
        - Late-game life force preservation

        CRITICAL: When life force is low, reduce max hand size proportionally.
        Having 12 cards in hand but only 2 force to spend is TERRIBLE.
        """
        from ..card_loader import get_card

        hand_size = board_state.hand_size if hasattr(board_state, 'hand_size') else 7
        reserve_deck = board_state.reserve_deck if hasattr(board_state, 'reserve_deck') else 20
        used_pile = board_state.used_pile if hasattr(board_state, 'used_pile') else 0
        force_pile = board_state.force_pile if hasattr(board_state, 'force_pile') else 5
        turn_number = board_state.turn_number if hasattr(board_state, 'turn_number') else 1

        # Total reserve force (old method) - just reserve deck
        reserve = board_state.total_reserve_force() if hasattr(board_state, 'total_reserve_force') else reserve_deck

        # === CRITICAL: LIFE FORCE BASED HAND LIMIT ===
        # Total remaining life force = cards that can still circulate
        # (reserve deck + used pile + force pile)
        # Hand cards are STUCK until played, so don't count them
        remaining_life_force = reserve_deck + used_pile + force_pile

        # Get force generation for future turn planning
        force_generation = 1  # Default: we generate 1 ourselves
        if game_strategy:
            force_generation = game_strategy.my_force_generation
        elif hasattr(board_state, 'my_force_generation'):
            force_generation = board_state.my_force_generation

        # === LATE GAME LIFE FORCE PRESERVATION ===
        # When life force is critically low, be very conservative
        if remaining_life_force < CRITICAL_LIFE_FORCE:
            action.add_reasoning(
                f"CRITICAL life force ({remaining_life_force}) - minimize draws",
                VERY_BAD_DELTA * 0.8  # Strong penalty but not absolute
            )
            # Still allow draws if hand is truly empty
            if hand_size >= 2:
                return

        # Late game - be more strategic
        if remaining_life_force < LATE_GAME_LIFE_FORCE:
            # Penalty scales with how low life force is
            penalty_scale = (LATE_GAME_LIFE_FORCE - remaining_life_force) / LATE_GAME_LIFE_FORCE
            action.add_reasoning(
                f"Late game ({remaining_life_force} life force) - draw carefully",
                BAD_DELTA * 2 * penalty_scale
            )

        # When remaining life force drops below MAX_HAND_SIZE (16),
        # reduce max hand proportionally.
        effective_max_hand = MAX_HAND_SIZE
        if remaining_life_force < MAX_HAND_SIZE:
            effective_max_hand = max(2, remaining_life_force)  # Floor at 2
            logger.debug(f"Life force {remaining_life_force} < {MAX_HAND_SIZE}: effective max hand = {effective_max_hand}")

        # If hand already exceeds effective max, STRONGLY penalize drawing
        if hand_size >= effective_max_hand:
            penalty = VERY_BAD_DELTA  # -999 to strongly discourage
            action.add_reasoning(
                f"CRITICAL: Hand {hand_size} >= life force limit {effective_max_hand} (only {remaining_life_force} cards left!)",
                penalty
            )
            return

        # === FUTURE TURN PLANNING: EXPENSIVE CARDS ===
        # Check if we have expensive cards worth saving force for
        max_deployable_cost = 0
        affordable_cards_count = 0
        expensive_card_in_hand = False

        if hasattr(board_state, 'cards_in_hand'):
            for card in board_state.cards_in_hand:
                if card.blueprint_id:
                    metadata = get_card(card.blueprint_id)
                    if metadata and metadata.deploy_value:
                        deploy_cost = metadata.deploy_value
                        max_deployable_cost = max(max_deployable_cost, deploy_cost)
                        if deploy_cost >= EXPENSIVE_CARD_THRESHOLD:
                            expensive_card_in_hand = True
                        if force_pile >= deploy_cost:
                            affordable_cards_count += 1

        # If we have expensive cards (Executor costs 15+), save force across turns
        if expensive_card_in_hand and max_deployable_cost > force_pile:
            # We need to accumulate force - don't draw!
            force_deficit = max_deployable_cost - force_pile
            turns_to_save = (force_deficit + force_generation - 1) // max(1, force_generation)

            # Only save if it's achievable (within ~3 turns)
            if turns_to_save <= 3 and remaining_life_force >= max_deployable_cost:
                action.add_reasoning(
                    f"Saving for expensive card (cost {max_deployable_cost}, need {force_deficit} more, ~{turns_to_save} turns)",
                    BAD_DELTA * 2
                )

        # If we have stuff to deploy but couldn't afford it, save force
        if affordable_cards_count == 0 and hand_size > 3 and force_pile < 6:
            action.add_reasoning(
                f"No affordable cards (hand {hand_size}, force {force_pile}) - save force for next turn",
                BAD_DELTA * 1.5
            )

        # === BASELINE: DRAW TOWARDS SOFT CAP ===
        # Give a baseline bonus for drawing when below soft cap (12).
        # This ensures drawing beats Pass (default 5.0) when hand is small.
        # CRITICAL: This bonus must be HIGH ENOUGH to beat Pass even after
        # "save force" penalties are applied. Penalties can total -25 or more!
        # So we need baseline to be at least 30+ for small hands.
        if hand_size < HAND_SOFT_CAP and remaining_life_force >= LATE_GAME_LIFE_FORCE:
            # Bonus scales with how far below cap we are
            cards_below_cap = HAND_SOFT_CAP - hand_size
            # Use exponential scaling: smaller hands get MUCH stronger bonus
            # This ensures drawing beats Pass even after force-saving penalties
            # hand=8: +16, hand=6: +24, hand=4: +40, hand=2: +60
            baseline_bonus = 8.0 * cards_below_cap
            # Minimum of 30 to beat Pass (5) + typical penalties (-25)
            baseline_bonus = max(30.0, baseline_bonus)
            action.add_reasoning(
                f"Hand {hand_size} below soft cap {HAND_SOFT_CAP} - need cards!",
                baseline_bonus
            )
            logger.info(f"🎴 Draw baseline bonus: hand {hand_size} < soft cap {HAND_SOFT_CAP}, +{baseline_bonus}")

        # === FORCE RESERVATION FOR OPPONENT'S TURN ===
        # After turn 4, keep some force for reactions/battles
        # Also check if we have cards on contested locations
        force_to_reserve = 1 if hand_size < SMALL_HAND_FOR_RESERVE else 2

        # Reserve more force if we have presence at contested locations
        if hasattr(board_state, 'locations') and board_state.locations:
            contested_locations = sum(
                1 for loc in board_state.locations
                if loc and hasattr(loc, 'both_present') and loc.both_present
            )
            if contested_locations > 0:
                force_to_reserve = max(force_to_reserve, 2 + contested_locations)

        if turn_number >= FORCE_RESERVE_TURN_THRESHOLD:
            if force_pile <= force_to_reserve:
                action.add_reasoning(
                    f"Turn {turn_number}: reserve {force_to_reserve} force for reactions/battles",
                    BAD_DELTA * 1.5
                )

        # C# Logic 1: Don't draw if low reserve (avoid decking)
        if reserve <= LOW_RESERVE_THRESHOLD:
            penalty = BAD_DELTA * (LOW_RESERVE_THRESHOLD - reserve)
            action.add_reasoning(f"Low reserve ({reserve}) - avoid drawing", penalty)

        # C# Logic 2: Draw if hand is smaller than target and enough reserve
        # But only if we're not in late game conservation mode
        if hand_size < TARGET_HAND_SIZE and reserve > 10 and force_pile > 1:
            if remaining_life_force >= LATE_GAME_LIFE_FORCE:
                action.add_reasoning(f"Hand size {hand_size} < {TARGET_HAND_SIZE} - draw to fill", GOOD_DELTA)

        # C# Logic 3: Draw if hand is very small (even in late game, need options)
        if hand_size <= SMALL_HAND_THRESHOLD and reserve > 4 and force_pile > 1:
            action.add_reasoning(f"Small hand ({hand_size}) - draw cards", GOOD_DELTA)

        # C# Logic 4: Aggressive draw only if we have good life force
        if force_pile > AGGRESSIVE_FORCE_THRESHOLD and remaining_life_force >= LATE_GAME_LIFE_FORCE:
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
            # But only if we're not critically low on life force
            if remaining_life_force >= LATE_GAME_LIFE_FORCE:
                if game_strategy.should_prioritize_drawing_for_locations(hand_size):
                    action.add_reasoning(f"Low force gen ({game_strategy.my_force_generation}) - draw for locations", GOOD_DELTA)
        else:
            # Fallback: C# Logic 6: Penalty for hand over max size
            if hand_size >= effective_max_hand:
                overflow = hand_size - effective_max_hand
                action.add_reasoning(f"Hand full ({hand_size}/{effective_max_hand}) - avoid drawing", BAD_DELTA * overflow)
            elif hand_size >= HAND_SOFT_CAP:
                overflow = hand_size - HAND_SOFT_CAP
                action.add_reasoning(f"Hand getting full ({hand_size})", BAD_DELTA * overflow * 0.5)

        # C# Logic 7: Save last force
        if force_pile == 1:
            action.add_reasoning("Last force - save it", BAD_DELTA)

"""
Strategy Controller

Manages game strategy state including:
- Battle Order Rules tracking (force drain costs)
- Turn strategy (Deploy vs Hold)
- Game-wide strategic planning via GameStrategy

Ported from C# AIStrategyController.cs

NOTE: Location checking via cardInfo network calls has been REMOVED.
Battle Order is now detected directly from cards_in_play (SIDE_OF_TABLE zone).
This eliminates expensive network calls that were causing rate limiting issues.
"""

import logging
from typing import Optional, TYPE_CHECKING

from .game_strategy import GameStrategy
from .priority_cards import (
    is_priority_card,
    is_priority_card_by_title,
    get_protection_score,
    get_protection_score_by_title,
    get_priority_card,
)

if TYPE_CHECKING:
    from .board_state import BoardState

logger = logging.getLogger(__name__)


class StrategyController:
    """
    Tracks game strategy state.

    Battle Order is now detected directly from board_state.is_under_battle_order()
    instead of expensive cardInfo network calls.

    Ported from C# AIStrategyController.
    """

    def __init__(self, my_side: str = "dark", config=None):
        """
        Initialize strategy controller.

        Args:
            my_side: "dark" or "light"
            config: Optional config object with AI settings
        """
        self.my_side = my_side
        self.config = config
        self.under_battle_order_rules = False
        self.has_shields_to_play = True
        self.offered_concede_this_game = False

        # Strategy tracking
        self.last_decision_reason = "I haven't made any decisions yet."

        # Game-wide strategy coordinator (pass config for live updates)
        self.game_strategy = GameStrategy(my_side, config)

        logger.info(f"StrategyController initialized for {my_side} side")

    def setup(self):
        """Reset strategy state for new game"""
        self.under_battle_order_rules = False
        self.has_shields_to_play = True
        self.offered_concede_this_game = False

        # Reset game strategy
        self.game_strategy.reset()

        logger.info("Strategy controller reset for new game")

    def start_new_turn(self, turn_number: int = 0):
        """Called at start of each turn to reset per-turn tracking"""
        # Update game strategy for new turn
        self.game_strategy.start_new_turn(turn_number)

        logger.debug(f"Strategy controller: turn {turn_number} started")

    def update_battle_order_from_board(self, board_state: 'BoardState'):
        """
        Update Battle Order rules state by checking the board state directly.

        This checks if either player has Battle Order (Dark) or Battle Plan (Light)
        deployed to their side_of_table, avoiding expensive cardInfo network calls.

        Called at the start of each turn and when cards are deployed.

        Args:
            board_state: Current board state
        """
        was_under_battle_order = self.under_battle_order_rules
        self.under_battle_order_rules = board_state.is_under_battle_order()

        # Log state changes
        if self.under_battle_order_rules and not was_under_battle_order:
            card = board_state.get_battle_order_card()
            card_name = card.card_title if card else "Battle Order/Plan"
            logger.info(f"⚠️  Now under Battle Order rules ({card_name}) - force drains cost +3!")
        elif was_under_battle_order and not self.under_battle_order_rules:
            logger.info("✅ No longer under Battle Order rules")

    def is_avoid_using_card(self, card_title: str) -> bool:
        """
        Check if a card should be avoided (bad cards to play).

        Ported from C# AIStrategyController.IsAvoidUsingCard
        """
        if "Wokling" in card_title:
            return True
        if "Anger, Fear, Aggression" in card_title or "Knowledge And Defense" in card_title:
            return True
        return False

    def is_high_value_card(
        self,
        card_type: str,
        card_title: str,
        blueprint_id: Optional[str] = None
    ) -> bool:
        """
        Check if a card is high value (should be protected).

        Enhanced from C# AIStrategyController.IsHighValueCard to use
        the priority cards system.

        Args:
            card_type: Card type string (e.g., "Interrupt", "Effect")
            card_title: Card title
            blueprint_id: Optional blueprint ID for precise lookup

        Returns:
            True if card should be protected from loss
        """
        # Check priority cards system first (most accurate)
        if blueprint_id and is_priority_card(blueprint_id):
            return True

        # Fall back to title-based check
        if card_title and is_priority_card_by_title(card_title):
            return True

        # Legacy checks (from C# code)
        if card_title:
            if "Ghhhk" in card_title or "Sense" in card_title or "Alter" in card_title:
                return True

        # Non-interrupts/effects/weapons are generally high value
        if card_type not in ["Interrupt", "Effect", "Weapon"]:
            return True

        return False

    def get_card_protection_score(
        self,
        blueprint_id: Optional[str] = None,
        card_title: Optional[str] = None
    ) -> float:
        """
        Get the protection score for a card (how much to penalize losing it).

        Args:
            blueprint_id: Card blueprint ID
            card_title: Card title (fallback if no blueprint)

        Returns:
            Protection score (0-100, higher = more protected)
        """
        if blueprint_id:
            score = get_protection_score(blueprint_id)
            if score > 0:
                return score

        if card_title:
            return get_protection_score_by_title(card_title)

        return 0.0

    def update_strategy(self, board_state: 'BoardState'):
        """
        Update game strategy based on current board state.

        Called periodically (typically at start of each turn) to recalculate
        strategic priorities.
        """
        self.game_strategy.update_from_board_state(board_state)

    def get_strategy_status(self) -> dict:
        """Get combined status from both controllers"""
        status = {
            'under_battle_order_rules': self.under_battle_order_rules,
        }
        status.update(self.game_strategy.get_status())
        return status

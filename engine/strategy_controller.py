"""
Strategy Controller

Manages game strategy state including:
- Battle Order Rules tracking (force drain costs)
- Location checking during Control phase
- Turn strategy (Deploy vs Hold)
- Game-wide strategic planning via GameStrategy

Ported from C# AIStrategyController.cs
"""

import logging
import re
from typing import Optional, List, Set, TYPE_CHECKING
from dataclasses import dataclass, field

from .game_strategy import GameStrategy

if TYPE_CHECKING:
    from .board_state import BoardState, LocationInPlay

logger = logging.getLogger(__name__)

# Max location checks per turn (network optimization)
MAX_LOCATION_CHECKS_PER_TURN = 5


@dataclass
class LocationCheckResult:
    """Results from a location cardInfo check"""
    card_id: str
    my_drain_amount: str = ""
    their_drain_amount: str = ""
    my_icons: str = ""
    their_icons: str = ""
    has_battle_order: bool = False


class StrategyController:
    """
    Tracks game strategy state and manages location checking.

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

        # Location check tracking
        self._locations_checked_this_turn: Set[str] = set()
        self._locations_checked_ever: Set[str] = set()
        self._checks_this_turn = 0

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
        self._locations_checked_this_turn.clear()
        self._locations_checked_ever.clear()
        self._checks_this_turn = 0

        # Reset game strategy
        self.game_strategy.reset()

        logger.info("Strategy controller reset for new game")

    def start_new_turn(self, turn_number: int = 0):
        """Called at start of each turn to reset per-turn tracking"""
        self._locations_checked_this_turn.clear()
        self._checks_this_turn = 0

        # Update game strategy for new turn
        self.game_strategy.start_new_turn(turn_number)

        logger.debug(f"Strategy controller: turn {turn_number} started")

    def get_locations_to_check(self, board_state: 'BoardState') -> List['LocationInPlay']:
        """
        Get list of locations that should be checked this turn.

        Returns up to MAX_LOCATION_CHECKS_PER_TURN locations, prioritizing:
        1. Locations with cards present (either player)
        2. Locations not yet checked this game

        Args:
            board_state: Current board state

        Returns:
            List of LocationInPlay to check
        """
        if self._checks_this_turn >= MAX_LOCATION_CHECKS_PER_TURN:
            logger.debug("Already at max location checks for this turn")
            return []

        locations_to_check = []
        remaining_checks = MAX_LOCATION_CHECKS_PER_TURN - self._checks_this_turn

        # Prioritize locations with cards present
        for loc in board_state.locations:
            if len(locations_to_check) >= remaining_checks:
                break

            # Skip locations already checked this turn
            if loc.card_id in self._locations_checked_this_turn:
                continue

            # Check if any cards are at this location
            has_cards = len(loc.my_cards) > 0 or len(loc.their_cards) > 0

            if has_cards:
                # Prioritize unchecked locations, but allow rechecking
                if loc.card_id not in self._locations_checked_ever:
                    locations_to_check.insert(0, loc)  # Prioritize never-checked
                else:
                    locations_to_check.append(loc)

        return locations_to_check[:remaining_checks]

    def process_location_check(self, card_id: str, html_response: str) -> LocationCheckResult:
        """
        Process the HTML response from a cardInfo call.

        Parses:
        - Force drain amounts (Dark/Light)
        - Force icons (Dark/Light)
        - Battle Order rules

        Args:
            card_id: The location's card ID
            html_response: Raw HTML from cardInfo endpoint

        Returns:
            LocationCheckResult with parsed data
        """
        result = LocationCheckResult(card_id=card_id)

        # Mark as checked
        self._locations_checked_this_turn.add(card_id)
        self._locations_checked_ever.add(card_id)
        self._checks_this_turn += 1

        if not html_response:
            return result

        # Clean up HTML tags for easier parsing
        # Format is like: <div>Force drain amount (Dark): 2</div>
        clean = html_response.replace("<br>", "").replace("</br>", "").replace("</div>", "")

        # Split on <div to get each section
        sections = clean.split("<div")

        contained_battle_order = False

        for section in sections:
            # Force drain amount (Dark)
            if section.startswith(">Force drain amount (Dark): "):
                value = section.split(':')[1].strip()
                if self.my_side == "dark":
                    result.my_drain_amount = value
                else:
                    result.their_drain_amount = value

            # Force drain amount (Light)
            elif section.startswith(">Force drain amount (Light): "):
                value = section.split(':')[1].strip()
                if self.my_side == "light":
                    result.my_drain_amount = value
                else:
                    result.their_drain_amount = value

            # Force icons (Dark)
            elif section.startswith(">Force icons (Dark): "):
                value = section.split(':')[1].strip()
                if self.my_side == "dark":
                    result.my_icons = value
                else:
                    result.their_icons = value

            # Force icons (Light)
            elif section.startswith(">Force icons (Light): "):
                value = section.split(':')[1].strip()
                if self.my_side == "light":
                    result.my_icons = value
                else:
                    result.their_icons = value

            # Battle Order Rules (Dark side initiates Force drain for +X)
            if "Dark side initiates" in section and "Force drain for +" in section:
                if self.my_side == "dark":
                    contained_battle_order = True
                    result.has_battle_order = True

            if "Light side initiates" in section and "Force drain for +" in section:
                if self.my_side == "light":
                    contained_battle_order = True
                    result.has_battle_order = True

        # Update global Battle Order state
        if contained_battle_order:
            if not self.under_battle_order_rules:
                logger.info("⚠️  Now under Battle Order rules - force drains cost extra!")
            self.under_battle_order_rules = True
        else:
            if self.under_battle_order_rules:
                logger.info("✅ No longer under Battle Order rules")
            self.under_battle_order_rules = False

        logger.debug(f"Location check {card_id}: drain={result.my_drain_amount}, icons={result.my_icons}, battle_order={contained_battle_order}")

        return result

    def update_location_with_check(self, location: 'LocationInPlay', result: LocationCheckResult):
        """
        Update a LocationInPlay with data from a cardInfo check.

        Args:
            location: The location to update
            result: The check result
        """
        location.my_drain_amount = result.my_drain_amount
        location.my_icons = result.my_icons
        location.their_icons = result.their_icons

        logger.debug(f"Updated location {location.site_name}: drain={result.my_drain_amount}, icons={result.my_icons}")

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

    def is_high_value_card(self, card_type: str, card_title: str) -> bool:
        """
        Check if a card is high value (should be protected).

        Ported from C# AIStrategyController.IsHighValueCard
        """
        # Ghhhk, Sense, Alter are high value
        if "Ghhhk" in card_title or "Sense" in card_title or "Alter" in card_title:
            return True
        # Non-interrupts/effects/weapons are generally high value
        if card_type not in ["Interrupt", "Effect", "Weapon"]:
            return True
        return False

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
            'locations_checked_this_turn': len(self._locations_checked_this_turn),
        }
        status.update(self.game_strategy.get_status())
        return status

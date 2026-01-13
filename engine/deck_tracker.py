"""
DeckTracker - Card Tracking System for Strategic Play

Tracks cards throughout a game to enable:
1. Probability estimation (P of drawing character, ship, high destiny, etc.)
2. Direct knowledge when bot has seen specific cards (e.g., searching reserve)
3. Expected destiny calculations for battle decisions

Knowledge Model:
- "Unknown" cards: We know they exist but not their location in reserve
- "Known" cards: We've seen specific cards in reserve deck (e.g., during a search)
- Knowledge is INVALIDATED when reserve deck changes (activate, recirculate, shuffle)
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Tuple, TYPE_CHECKING
from collections import defaultdict
from enum import Enum

from engine.card_loader import get_card, Card

if TYPE_CHECKING:
    from engine.board_state import BoardState

logger = logging.getLogger(__name__)


class KnowledgeState(Enum):
    """State of our knowledge about the reserve deck."""
    UNKNOWN = "unknown"           # Only probabilistic estimates available
    PARTIAL = "partial"           # We've seen some cards (e.g., top N during a search)
    COMPLETE = "complete"         # We've seen entire reserve (rare)


@dataclass
class CardStats:
    """Statistics about a card type for probability calculations."""
    blueprint_id: str
    name: str
    card_type: str              # "Character", "Starship", "Location", etc.
    destiny: int
    deploy_cost: int
    power: int
    forfeit: int
    is_unique: bool


@dataclass
class ZoneContents:
    """Contents of a card zone, tracking counts by blueprint_id."""
    cards: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, blueprint_id: str, count: int = 1) -> None:
        self.cards[blueprint_id] += count

    def remove(self, blueprint_id: str, count: int = 1) -> bool:
        if self.cards[blueprint_id] >= count:
            self.cards[blueprint_id] -= count
            if self.cards[blueprint_id] == 0:
                del self.cards[blueprint_id]
            return True
        return False

    def count(self, blueprint_id: str) -> int:
        return self.cards.get(blueprint_id, 0)

    def total(self) -> int:
        return sum(self.cards.values())

    def clear(self) -> None:
        self.cards.clear()

    def copy(self) -> 'ZoneContents':
        new = ZoneContents()
        new.cards = defaultdict(int, self.cards)
        return new

    def __contains__(self, blueprint_id: str) -> bool:
        return self.cards.get(blueprint_id, 0) > 0


class DeckTracker:
    """
    Tracks card locations and calculates probabilities throughout a game.

    Zones tracked:
    - deck_list: Original deck composition (immutable)
    - hand: Cards currently in hand
    - in_play: Cards on the table
    - lost_pile: Cards lost this game
    - used_pile: Cards in used pile
    - force_pile: Cards in force pile (usually unknown composition)
    - reserve_deck: Cards in reserve deck (calculated as remainder)

    Knowledge tracking:
    - known_reserve_order: When we've seen reserve cards, their exact order
    - knowledge_state: How much we know about reserve
    - last_knowledge_turn: When knowledge was acquired
    """

    def __init__(self):
        # Original deck (loaded once at game start)
        self.deck_list: Dict[str, int] = {}  # blueprint_id -> count
        self.deck_loaded = False

        # Current zone contents
        self.hand = ZoneContents()
        self.in_play = ZoneContents()
        self.lost_pile = ZoneContents()
        self.used_pile = ZoneContents()
        self.force_pile_count = 0  # Usually don't know exact contents

        # Direct knowledge of reserve deck
        self.known_reserve_order: List[str] = []  # Cards we've seen, in order (top first)
        self.knowledge_state = KnowledgeState.UNKNOWN
        self.last_knowledge_turn = 0

        # Card metadata cache
        self._card_stats: Dict[str, CardStats] = {}

        # Tracking state
        self.current_turn = 0
        self.my_side: Optional[str] = None

    # =========================================================================
    # INITIALIZATION
    # =========================================================================

    def load_deck(self, deck_path: str, my_side: str) -> bool:
        """
        Load deck list from file at game start.

        Args:
            deck_path: Path to deck file
            my_side: "dark" or "light"

        Returns:
            True if loaded successfully
        """
        self.my_side = my_side.lower()
        self.deck_list.clear()
        self._card_stats.clear()

        try:
            with open(deck_path, 'r') as f:
                content = f.read()

            # Parse deck file - format varies, handle common formats
            cards_loaded = self._parse_deck_file(content)

            if cards_loaded > 0:
                self.deck_loaded = True
                logger.info(f"📚 DeckTracker: Loaded {cards_loaded} cards from deck")
                self._cache_card_stats()
                return True
            else:
                logger.warning(f"📚 DeckTracker: No cards found in deck file")
                return False

        except FileNotFoundError:
            logger.warning(f"📚 DeckTracker: Deck file not found: {deck_path}")
            return False
        except Exception as e:
            logger.error(f"📚 DeckTracker: Error loading deck: {e}")
            return False

    def _parse_deck_file(self, content: str) -> int:
        """Parse deck file content and populate deck_list."""
        cards_loaded = 0

        # Check if XML format
        if content.strip().startswith('<?xml') or '<deck>' in content:
            return self._parse_xml_deck(content)

        # Plain text format: "count blueprint_id" or just "blueprint_id"
        for line in content.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('//'):
                continue

            parts = line.split()
            if not parts:
                continue

            try:
                count = int(parts[0])
                blueprint_id = parts[1] if len(parts) > 1 else None
            except ValueError:
                count = 1
                blueprint_id = parts[0]

            if blueprint_id:
                blueprint_id = blueprint_id.strip()
                self.deck_list[blueprint_id] = self.deck_list.get(blueprint_id, 0) + count
                cards_loaded += count

        return cards_loaded

    def _parse_xml_deck(self, content: str) -> int:
        """Parse XML format deck file."""
        import xml.etree.ElementTree as ET

        cards_loaded = 0
        try:
            root = ET.fromstring(content)

            # Find all card elements
            for card_elem in root.findall('.//card'):
                blueprint_id = card_elem.get('blueprintId')
                if blueprint_id:
                    self.deck_list[blueprint_id] = self.deck_list.get(blueprint_id, 0) + 1
                    cards_loaded += 1

        except ET.ParseError as e:
            logger.error(f"📚 XML parse error: {e}")

        return cards_loaded

    def load_deck_from_list(self, cards: List[str], my_side: str) -> None:
        """Load deck from a list of blueprint_ids."""
        self.my_side = my_side.lower()
        self.deck_list.clear()

        for blueprint_id in cards:
            self.deck_list[blueprint_id] = self.deck_list.get(blueprint_id, 0) + 1

        self.deck_loaded = True
        self._cache_card_stats()
        logger.info(f"📚 DeckTracker: Loaded {len(cards)} cards from list")

    def _cache_card_stats(self) -> None:
        """Cache card statistics for quick lookups."""
        for blueprint_id in self.deck_list:
            if blueprint_id in self._card_stats:
                continue

            card = get_card(blueprint_id)
            if card:
                # Handle potential string values for numeric fields
                def safe_int(val, default=0):
                    if val is None:
                        return default
                    if isinstance(val, int):
                        return val
                    if isinstance(val, str):
                        try:
                            return int(val)
                        except ValueError:
                            return default
                    return default

                self._card_stats[blueprint_id] = CardStats(
                    blueprint_id=blueprint_id,
                    name=card.title or blueprint_id,
                    card_type=card.card_type or "Unknown",
                    destiny=safe_int(card.destiny),
                    deploy_cost=safe_int(card.deploy),
                    power=safe_int(card.power),
                    forfeit=safe_int(card.forfeit),
                    is_unique=card.is_unique
                )

    # =========================================================================
    # ZONE TRANSITIONS
    # =========================================================================

    def card_drawn(self, blueprint_id: str, from_force_pile: bool = False) -> None:
        """Card was drawn into hand."""
        self.hand.add(blueprint_id)
        if from_force_pile:
            self.force_pile_count = max(0, self.force_pile_count - 1)
        else:
            # Drawn from reserve - invalidate order knowledge if we had it
            self._invalidate_top_knowledge()
        logger.debug(f"📚 Draw: {blueprint_id} -> hand")

    def card_deployed(self, blueprint_id: str) -> None:
        """Card was deployed from hand to table."""
        if self.hand.remove(blueprint_id):
            self.in_play.add(blueprint_id)
            logger.debug(f"📚 Deploy: {blueprint_id} hand -> in_play")
        else:
            # Might be deployed directly (not from hand)
            self.in_play.add(blueprint_id)
            logger.debug(f"📚 Deploy (direct): {blueprint_id} -> in_play")

    def card_lost(self, blueprint_id: str, from_zone: str = "in_play") -> None:
        """Card was lost (sent to lost pile)."""
        if from_zone == "hand":
            self.hand.remove(blueprint_id)
        elif from_zone == "in_play":
            self.in_play.remove(blueprint_id)
        self.lost_pile.add(blueprint_id)
        logger.debug(f"📚 Lost: {blueprint_id} {from_zone} -> lost_pile")

    def card_used(self, blueprint_id: str, from_zone: str = "in_play") -> None:
        """Card was placed in used pile."""
        if from_zone == "hand":
            self.hand.remove(blueprint_id)
        elif from_zone == "in_play":
            self.in_play.remove(blueprint_id)
        self.used_pile.add(blueprint_id)
        logger.debug(f"📚 Used: {blueprint_id} {from_zone} -> used_pile")

    def force_activated(self, count: int) -> None:
        """Force was activated (cards moved from reserve to force pile)."""
        self.force_pile_count += count
        # This invalidates our reserve knowledge since cards moved
        self._invalidate_reserve_knowledge("Force activated")

    def force_recirculated(self) -> None:
        """Used pile was recirculated under reserve deck."""
        # Used pile goes under reserve - invalidates reserve order knowledge
        self.used_pile.clear()
        self._invalidate_reserve_knowledge("Recirculated")

    def deck_shuffled(self) -> None:
        """Reserve deck was shuffled."""
        self._invalidate_reserve_knowledge("Deck shuffled")

    # =========================================================================
    # KNOWLEDGE TRACKING
    # =========================================================================

    def observe_reserve_cards(self, cards: List[str], is_complete: bool = False) -> None:
        """
        Record cards seen when looking at reserve deck (e.g., during a search).

        Args:
            cards: List of blueprint_ids seen, in order (top first)
            is_complete: True if we saw the entire reserve
        """
        self.known_reserve_order = cards.copy()
        self.knowledge_state = KnowledgeState.COMPLETE if is_complete else KnowledgeState.PARTIAL
        self.last_knowledge_turn = self.current_turn
        logger.info(f"📚 Reserve observed: {len(cards)} cards {'(complete)' if is_complete else '(partial)'}")

    def _invalidate_reserve_knowledge(self, reason: str) -> None:
        """Invalidate direct knowledge of reserve deck contents."""
        if self.knowledge_state != KnowledgeState.UNKNOWN:
            logger.debug(f"📚 Reserve knowledge invalidated: {reason}")
        self.known_reserve_order.clear()
        self.knowledge_state = KnowledgeState.UNKNOWN

    def _invalidate_top_knowledge(self) -> None:
        """Invalidate knowledge of top card(s) after drawing."""
        if self.known_reserve_order:
            self.known_reserve_order.pop(0)
            if not self.known_reserve_order:
                self.knowledge_state = KnowledgeState.UNKNOWN

    # =========================================================================
    # CALCULATED PROPERTIES
    # =========================================================================

    def get_remaining_in_reserve(self) -> Dict[str, int]:
        """
        Calculate cards remaining in reserve deck (probabilistic).

        Returns dict of blueprint_id -> estimated count remaining.
        """
        remaining = dict(self.deck_list)

        # Subtract known locations
        for blueprint_id, count in self.hand.cards.items():
            remaining[blueprint_id] = max(0, remaining.get(blueprint_id, 0) - count)
        for blueprint_id, count in self.in_play.cards.items():
            remaining[blueprint_id] = max(0, remaining.get(blueprint_id, 0) - count)
        for blueprint_id, count in self.lost_pile.cards.items():
            remaining[blueprint_id] = max(0, remaining.get(blueprint_id, 0) - count)
        for blueprint_id, count in self.used_pile.cards.items():
            remaining[blueprint_id] = max(0, remaining.get(blueprint_id, 0) - count)

        # Remove zeros
        return {k: v for k, v in remaining.items() if v > 0}

    def get_reserve_count(self) -> int:
        """Get estimated number of cards in reserve deck."""
        remaining = self.get_remaining_in_reserve()
        # Subtract force pile (which came from reserve)
        total = sum(remaining.values()) - self.force_pile_count
        return max(0, total)

    # =========================================================================
    # PROBABILITY CALCULATIONS
    # =========================================================================

    def probability_draw_type(self, card_type: str) -> float:
        """
        Calculate probability that next draw is of given type.

        Args:
            card_type: "Character", "Starship", "Vehicle", "Location", etc.

        Returns:
            Probability 0.0 to 1.0
        """
        # If we have direct knowledge, use it
        if self.knowledge_state != KnowledgeState.UNKNOWN and self.known_reserve_order:
            top_card = self.known_reserve_order[0]
            stats = self._card_stats.get(top_card)
            if stats:
                return 1.0 if stats.card_type == card_type else 0.0

        # Otherwise use probabilistic estimate
        remaining = self.get_remaining_in_reserve()
        remaining_total = sum(remaining.values())
        if remaining_total <= 0:
            return 0.0

        type_count = 0
        for blueprint_id, count in remaining.items():
            stats = self._card_stats.get(blueprint_id)
            if stats and stats.card_type == card_type:
                type_count += count

        return type_count / remaining_total

    def probability_draw_deployable(self, max_cost: int = 99) -> float:
        """
        Calculate probability that next draw is a deployable card within cost.

        Args:
            max_cost: Maximum deploy cost to consider

        Returns:
            Probability 0.0 to 1.0
        """
        if self.knowledge_state != KnowledgeState.UNKNOWN and self.known_reserve_order:
            top_card = self.known_reserve_order[0]
            stats = self._card_stats.get(top_card)
            if stats:
                deployable = stats.card_type in ("Character", "Starship", "Vehicle", "Device", "Weapon")
                affordable = stats.deploy_cost <= max_cost
                return 1.0 if (deployable and affordable) else 0.0

        remaining = self.get_remaining_in_reserve()
        remaining_total = sum(remaining.values())
        if remaining_total <= 0:
            return 0.0

        deployable_count = 0
        deployable_types = {"Character", "Starship", "Vehicle", "Device", "Weapon"}

        for blueprint_id, count in remaining.items():
            stats = self._card_stats.get(blueprint_id)
            if stats and stats.card_type in deployable_types and stats.deploy_cost <= max_cost:
                deployable_count += count

        return deployable_count / remaining_total

    def probability_destiny_at_least(self, min_destiny: int) -> float:
        """
        Calculate probability that next destiny draw is >= min_destiny.

        Args:
            min_destiny: Minimum destiny value needed

        Returns:
            Probability 0.0 to 1.0
        """
        if self.knowledge_state != KnowledgeState.UNKNOWN and self.known_reserve_order:
            top_card = self.known_reserve_order[0]
            stats = self._card_stats.get(top_card)
            if stats:
                return 1.0 if stats.destiny >= min_destiny else 0.0

        remaining = self.get_remaining_in_reserve()
        remaining_total = sum(remaining.values())
        if remaining_total <= 0:
            return 0.0

        good_destiny_count = 0
        for blueprint_id, count in remaining.items():
            stats = self._card_stats.get(blueprint_id)
            if stats and stats.destiny >= min_destiny:
                good_destiny_count += count

        return good_destiny_count / remaining_total

    def expected_destiny(self) -> float:
        """
        Calculate expected value of next destiny draw.

        Returns:
            Expected destiny value
        """
        if self.knowledge_state != KnowledgeState.UNKNOWN and self.known_reserve_order:
            top_card = self.known_reserve_order[0]
            stats = self._card_stats.get(top_card)
            if stats:
                return float(stats.destiny)

        # Get cards in reserve + force pile (not in hand/play/lost/used)
        remaining = self.get_remaining_in_reserve()
        remaining_total = sum(remaining.values())

        if remaining_total <= 0:
            return 0.0

        # Calculate average destiny of the pool (reserve + force pile)
        # Since force pile is a random sample from this pool, the expected
        # destiny of cards in reserve equals the pool average.
        destiny_sum = 0.0
        for blueprint_id, count in remaining.items():
            stats = self._card_stats.get(blueprint_id)
            if stats:
                destiny_sum += stats.destiny * count

        return destiny_sum / remaining_total

    def get_destiny_distribution(self) -> Dict[int, float]:
        """
        Get probability distribution of destiny values.

        Returns:
            Dict of destiny_value -> probability
        """
        remaining = self.get_remaining_in_reserve()
        remaining_total = sum(remaining.values())
        if remaining_total <= 0:
            return {}

        distribution: Dict[int, float] = defaultdict(float)
        for blueprint_id, count in remaining.items():
            stats = self._card_stats.get(blueprint_id)
            if stats:
                distribution[stats.destiny] += count / remaining_total

        return dict(distribution)

    # =========================================================================
    # STRATEGIC QUERIES
    # =========================================================================

    def should_draw_for_type(self, card_type: str, threshold: float = 0.3) -> Tuple[bool, float]:
        """
        Should we draw if we need a specific card type?

        Args:
            card_type: Type we're looking for
            threshold: Minimum probability to recommend drawing

        Returns:
            (should_draw, probability)
        """
        prob = self.probability_draw_type(card_type)
        return (prob >= threshold, prob)

    def get_hand_composition(self) -> Dict[str, int]:
        """Get count of each card type in hand."""
        composition: Dict[str, int] = defaultdict(int)
        for blueprint_id, count in self.hand.cards.items():
            stats = self._card_stats.get(blueprint_id)
            if stats:
                composition[stats.card_type] += count
        return dict(composition)

    def get_remaining_composition(self) -> Dict[str, int]:
        """Get count of each card type remaining in reserve."""
        remaining = self.get_remaining_in_reserve()
        composition: Dict[str, int] = defaultdict(int)
        for blueprint_id, count in remaining.items():
            stats = self._card_stats.get(blueprint_id)
            if stats:
                composition[stats.card_type] += count
        return dict(composition)

    def count_remaining_by_type(self, card_type: str) -> int:
        """Count how many cards of a type remain in reserve."""
        remaining = self.get_remaining_in_reserve()
        count = 0
        for blueprint_id, num in remaining.items():
            stats = self._card_stats.get(blueprint_id)
            if stats and stats.card_type == card_type:
                count += num
        return count

    def get_top_card_if_known(self) -> Optional[CardStats]:
        """Get the top card of reserve if we have direct knowledge."""
        if self.knowledge_state != KnowledgeState.UNKNOWN and self.known_reserve_order:
            return self._card_stats.get(self.known_reserve_order[0])
        return None

    # =========================================================================
    # STATE MANAGEMENT
    # =========================================================================

    def on_turn_start(self, turn: int) -> None:
        """Called at the start of each turn."""
        self.current_turn = turn

    def sync_with_board_state(self, board_state: 'BoardState') -> None:
        """
        Sync tracker with current board state.

        This updates our tracking based on what we can observe.
        """
        # Update in_play based on board_state.cards_in_play
        # This handles cases where we missed events
        pass  # TODO: Implement full sync

    def get_summary(self) -> str:
        """Get human-readable summary of tracking state."""
        remaining = self.get_remaining_in_reserve()
        reserve_count = self.get_reserve_count()

        lines = [
            f"📚 DeckTracker Summary:",
            f"   Hand: {self.hand.total()} cards",
            f"   In Play: {self.in_play.total()} cards",
            f"   Lost: {self.lost_pile.total()} cards",
            f"   Used: {self.used_pile.total()} cards",
            f"   Force Pile: ~{self.force_pile_count} cards",
            f"   Reserve: ~{reserve_count} cards",
            f"   Knowledge: {self.knowledge_state.value}",
        ]

        if self.knowledge_state != KnowledgeState.UNKNOWN:
            lines.append(f"   Known top cards: {len(self.known_reserve_order)}")

        # Composition
        comp = self.get_remaining_composition()
        if comp:
            comp_str = ", ".join(f"{k}:{v}" for k, v in sorted(comp.items()))
            lines.append(f"   Remaining: {comp_str}")

        # Key probabilities
        lines.append(f"   P(Character): {self.probability_draw_type('Character'):.1%}")
        lines.append(f"   P(Starship): {self.probability_draw_type('Starship'):.1%}")
        lines.append(f"   E[Destiny]: {self.expected_destiny():.1f}")

        return "\n".join(lines)


# Singleton instance for the current game
_tracker: Optional[DeckTracker] = None

# Base path for deck files
DECK_BASE_PATH = "/mnt/ubuntu-lv/swccg/gemp/rando_cal_working/decks"


def get_deck_tracker() -> DeckTracker:
    """Get the global deck tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = DeckTracker()
    return _tracker


def reset_deck_tracker() -> DeckTracker:
    """Reset the tracker for a new game."""
    global _tracker
    _tracker = DeckTracker()
    return _tracker


def find_deck_file(deck_name: str) -> Optional[str]:
    """
    Find the deck file path for a given deck name.

    Args:
        deck_name: Name of the deck (e.g., "dark_baseline", "Astrogation Chart 1234")

    Returns:
        Path to deck file if found, None otherwise
    """
    import os

    # Try exact match with .txt
    path = os.path.join(DECK_BASE_PATH, f"{deck_name}.txt")
    if os.path.exists(path):
        return path

    # Try without extension (in case already has it)
    if deck_name.endswith('.txt'):
        path = os.path.join(DECK_BASE_PATH, deck_name)
        if os.path.exists(path):
            return path

    # Try case-insensitive search
    try:
        for filename in os.listdir(DECK_BASE_PATH):
            if filename.lower() == f"{deck_name.lower()}.txt":
                return os.path.join(DECK_BASE_PATH, filename)
    except OSError:
        pass

    return None


def initialize_deck_tracker(deck_name: str, my_side: str) -> bool:
    """
    Initialize the deck tracker for a new game.

    Args:
        deck_name: Name of the deck being used
        my_side: "dark" or "light"

    Returns:
        True if deck was loaded successfully
    """
    global _tracker
    _tracker = DeckTracker()

    deck_path = find_deck_file(deck_name)
    if deck_path:
        success = _tracker.load_deck(deck_path, my_side)
        if success:
            logger.info(f"📚 DeckTracker initialized with {deck_name} ({sum(_tracker.deck_list.values())} cards)")
            return True
        else:
            logger.warning(f"📚 DeckTracker: Failed to load deck from {deck_path}")
    else:
        logger.warning(f"📚 DeckTracker: Deck file not found for '{deck_name}'")

    return False

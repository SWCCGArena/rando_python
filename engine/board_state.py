"""
Board State Tracker

Tracks the complete game state from XML events, including:
- Cards in play (at locations, attached, in hand)
- Locations and their properties
- Force piles and resource zones
- Power and ability at each location
- Game phase and turn tracking

Ported from Unity C# AIBoardStateTracker.cs
"""

from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)

# Import card_loader lazily to avoid circular imports
_card_loader = None

def _get_card_metadata(blueprint_id: str):
    """Lazy-load card metadata to avoid circular imports"""
    global _card_loader
    if _card_loader is None:
        from . import card_loader as cl
        _card_loader = cl
    return _card_loader.get_card(blueprint_id)


@dataclass
class CardInPlay:
    """
    Represents a single card instance in play.
    Tracks location, owner, attachments, and metadata.
    """
    card_id: str                                    # Unique instance ID (e.g., "temp15")
    blueprint_id: str                               # Card template ID (e.g., "1_45")
    zone: str                                       # WHERE card is: AT_LOCATION, ATTACHED, HAND, etc.
    owner: str                                      # Player name who owns this card
    location_index: int = -1                        # Index in locations list (if at location)
    target_card_id: Optional[str] = None           # If attached, parent card ID
    attached_cards: List['CardInPlay'] = field(default_factory=list)  # Cards attached to THIS card

    # Card metadata (populated from card JSON)
    card_title: str = ""
    card_type: str = ""
    power: int = 0
    ability: int = 0
    deploy: int = 0

    def __repr__(self):
        return f"CardInPlay({self.blueprint_id}@{self.zone}, owner={self.owner}, loc={self.location_index})"

    def load_metadata(self):
        """Load card metadata from JSON database"""
        if self.blueprint_id:
            metadata = _get_card_metadata(self.blueprint_id)
            if metadata:
                self.card_title = metadata.title or ""
                self.card_type = metadata.card_type or ""
                self.power = metadata.power_value or 0
                self.ability = metadata.ability_value or 0
                self.deploy = metadata.deploy_value or 0


@dataclass
class LocationInPlay:
    """
    Represents a location on the board.
    Tracks cards at this location for both players.
    """
    card_id: str                                    # Unique instance ID of location card
    blueprint_id: str                               # Location card template ID
    owner: str                                      # Player who controls the location
    location_index: int                             # Position on board (0, 1, 2, 3, etc.)
    system_name: str = ""                           # System name (e.g., "Yavin 4")
    site_name: str = ""                             # Full location name (e.g., "Yavin 4: Massassi Throne Room")

    # Location type (for deployment restrictions)
    is_site: bool = False                           # True if this is a specific site
    is_space: bool = False                          # True if space location (starships only)
    is_ground: bool = False                         # True if ground location (characters/vehicles)

    # Cards at this location
    my_cards: List[CardInPlay] = field(default_factory=list)      # Cards I deployed here
    their_cards: List[CardInPlay] = field(default_factory=list)   # Opponent cards here
    attached_cards: List[CardInPlay] = field(default_factory=list) # Devices/weapons on location

    # Force icons and drain info
    my_icons: str = ""                              # Force icons I control
    their_icons: str = ""                           # Force icons opponent controls
    my_drain_amount: str = ""                       # My force drain bonus
    my_drain_cost: str = ""                         # Cost to drain force

    def __repr__(self):
        # Show site name if it's a site, otherwise show system name
        display_name = self.site_name if self.is_site and self.site_name else (self.system_name or self.blueprint_id)
        return f"LocationInPlay({display_name}@{self.location_index}, my={len(self.my_cards)}, their={len(self.their_cards)})"


class BoardState:
    """
    Complete game state tracker.

    Maintains:
    - All cards in play (by card ID)
    - All locations on the board
    - Force piles and zones for both players
    - Power and ability at each location
    - Current phase and turn info
    """

    def __init__(self, my_player_name: str):
        self.my_player_name = my_player_name
        self.opponent_name: Optional[str] = None
        self.my_side: Optional[str] = None  # "light" or "dark"

        # Strategy controller reference (set by app.py)
        self.strategy_controller = None

        # Core tracking structures
        self.cards_in_play: Dict[str, CardInPlay] = {}  # All cards keyed by card_id
        self.locations: List[LocationInPlay] = []        # Board locations by index
        self.cards_in_hand: List[CardInPlay] = []        # My hand

        # My zones
        self.force_pile: int = 0         # Force available to activate
        self.used_pile: int = 0          # Force already used
        self.reserve_deck: int = 0       # Cards in reserve deck
        self.lost_pile: int = 0          # Cards lost
        self.out_of_play: int = 0        # Out of play count
        self.hand_size: int = 0          # Hand count (from server)
        self.sabacc_hand: int = 0        # Sabacc hand size

        # Opponent's zones
        self.their_force_pile: int = 0
        self.their_used_pile: int = 0
        self.their_reserve_deck: int = 0
        self.their_lost_pile: int = 0
        self.their_out_of_play: int = 0
        self.their_hand_size: int = 0
        self.their_sabacc_hand: int = 0

        # Force generation (icons * available activation)
        self.dark_generation: int = 0
        self.light_generation: int = 0

        # Power at locations (keyed by location index)
        self.dark_power_at_locations: Dict[int, int] = {}
        self.light_power_at_locations: Dict[int, int] = {}

        # Game state
        self.current_phase: str = ""      # DEPLOY, BATTLE, MOVE, DRAW, CONTROL
        self.current_turn_player: str = ""
        self.turn_number: int = 0          # Current turn number
        self.activation: int = 0           # Max force that can be activated this turn
        self.force_activated_this_turn: int = 0  # Force already activated this turn
        self.in_battle: bool = False       # True during battles/duels/attacks
        self.current_battle_location: int = -1  # Location index where battle is occurring (-1 if none)

        # Game result tracking (from message events)
        self.game_winner: Optional[str] = None  # Player name who won, or None if game ongoing
        self.game_win_reason: Optional[str] = None  # "Conceded", "Life Force depleted", etc.

    def clear(self):
        """Reset all state (for new game)"""
        self.cards_in_play.clear()
        self.locations.clear()
        self.cards_in_hand.clear()
        self.dark_power_at_locations.clear()
        self.light_power_at_locations.clear()
        self.force_pile = 0
        self.used_pile = 0
        self.reserve_deck = 0
        self.lost_pile = 0
        self.out_of_play = 0
        self.current_phase = ""
        self.current_turn_player = ""
        self.turn_number = 0
        self.activation = 0
        self.force_activated_this_turn = 0
        self.in_battle = False
        self.game_winner = None
        self.game_win_reason = None
        logger.info("🗑️  Board state cleared")

    # ========== Card Management ==========
    # Ported from C# AIBoardStateTracker.UpdateCardsInPlayMap()

    def update_cards_in_play(self, card_id: str, target_card_id: Optional[str],
                             blueprint_id: Optional[str], zone: str, owner: str,
                             location_index: int):
        """
        Update card tracking - the main entry point for all card state changes.

        Ported from C# AIBoardStateTracker.UpdateCardsInPlayMap()

        This handles:
        - Creating new cards
        - Moving existing cards between zones/locations
        - Attaching/detaching cards
        - Proper cleanup of old positions
        """
        if zone == "AT_LOCATION":
            self._handle_card_at_location(card_id, blueprint_id, zone, owner, location_index)
        elif zone == "ATTACHED":
            self._handle_card_attached(card_id, target_card_id, blueprint_id, zone, owner)
        elif zone == "LOCATIONS":
            # Locations are handled separately via add_location()
            pass
        elif zone == "HAND" and owner == self.my_player_name:
            self._handle_card_in_hand(card_id, blueprint_id, zone, owner)
        else:
            # Other zones (SIDE_OF_TABLE, FORCE_PILE, etc.)
            self._handle_card_other_zone(card_id, blueprint_id, zone, owner)

    def _handle_card_at_location(self, card_id: str, blueprint_id: Optional[str],
                                  zone: str, owner: str, location_index: int):
        """Handle cards deployed at board locations (AT_LOCATION zone)"""
        self._ensure_location_exists(location_index)
        location = self.locations[location_index]

        # Get or create the card
        card = self.cards_in_play.get(card_id)
        is_new_card = card is None
        if is_new_card:
            card = CardInPlay(
                card_id=card_id,
                blueprint_id=blueprint_id or "",
                zone=zone,
                owner=owner,
                location_index=location_index
            )
            self.cards_in_play[card_id] = card
        else:
            # Card exists - clean up old position first
            self._detach_card_from_parent(card)
            self._remove_card_from_location(card)

        # Update card properties
        card.card_id = card_id
        card.location_index = location_index
        card.zone = zone
        card.owner = owner
        card.target_card_id = None  # Clear attachment
        if blueprint_id:
            card.blueprint_id = blueprint_id

        # Load metadata for new cards or if blueprint changed
        if is_new_card or (blueprint_id and not card.card_title):
            card.load_metadata()

        # Add to location's card list
        if owner == self.opponent_name:
            if card not in location.their_cards:
                location.their_cards.append(card)
        else:
            if card not in location.my_cards:
                location.my_cards.append(card)

        logger.debug(f"➕ Card {card_id} ({card.card_title or blueprint_id}) at location {location_index}")

    def _handle_card_attached(self, card_id: str, target_card_id: str,
                               blueprint_id: Optional[str], zone: str, owner: str):
        """Handle cards attached to other cards (ATTACHED zone)"""
        # Get or create the card
        card = self.cards_in_play.get(card_id)
        is_new_card = card is None
        if is_new_card:
            card = CardInPlay(
                card_id=card_id,
                blueprint_id=blueprint_id or "",
                zone=zone,
                owner=owner,
                location_index=-1
            )
            self.cards_in_play[card_id] = card
        else:
            # Card exists - clean up old position first
            self._detach_card_from_parent(card)
            self._remove_card_from_location(card)

        # Get target card (what we're attaching to)
        target = self.cards_in_play.get(target_card_id)
        if target is None:
            logger.warning(f"Attaching {card_id} to non-existent target {target_card_id}")
            return

        # Update card properties
        card.card_id = card_id
        card.zone = zone
        card.owner = owner
        card.target_card_id = target_card_id
        if blueprint_id:
            card.blueprint_id = blueprint_id

        # Load metadata for new cards
        if is_new_card or (blueprint_id and not card.card_title):
            card.load_metadata()

        # Bidirectional attachment (C# behavior)
        if card not in target.attached_cards:
            target.attached_cards.append(card)

        # Inherit location index from target card
        card.location_index = target.location_index

        logger.info(f"📎 Card {card_id} ({card.card_title or blueprint_id}) ATTACHED to {target_card_id} ({target.card_title})")
        logger.info(f"   Target {target_card_id} now has {len(target.attached_cards)} attached cards")

    def _handle_card_in_hand(self, card_id: str, blueprint_id: Optional[str],
                              zone: str, owner: str):
        """Handle cards in YOUR hand (HAND zone, only for your cards)"""
        # Get or create the card
        card = self.cards_in_play.get(card_id)
        is_new_card = card is None
        if is_new_card:
            card = CardInPlay(
                card_id=card_id,
                blueprint_id=blueprint_id or "",
                zone=zone,
                owner=owner,
                location_index=-1
            )
            self.cards_in_play[card_id] = card
        else:
            # Card exists - clean up old position first
            self._detach_card_from_parent(card)
            self._remove_card_from_location(card)

        # Update card properties
        card.card_id = card_id
        if blueprint_id:
            card.blueprint_id = blueprint_id
        card.zone = zone
        card.owner = owner
        card.target_card_id = None

        # Load metadata for new cards
        if is_new_card or (blueprint_id and not card.card_title):
            card.load_metadata()

        # Add to hand list
        if card not in self.cards_in_hand:
            self.cards_in_hand.append(card)

        logger.debug(f"➕ Card {card_id} ({card.card_title or blueprint_id}) in hand")

    def _handle_card_other_zone(self, card_id: str, blueprint_id: Optional[str],
                                 zone: str, owner: str):
        """Handle cards in other zones (SIDE_OF_TABLE, FORCE_PILE, etc.)"""
        # Get or create the card
        card = self.cards_in_play.get(card_id)
        is_new_card = card is None
        if is_new_card:
            card = CardInPlay(
                card_id=card_id,
                blueprint_id=blueprint_id or "",
                zone=zone,
                owner=owner,
                location_index=-999  # Special marker for "not at a location"
            )
            self.cards_in_play[card_id] = card

        # Update card properties
        card.card_id = card_id
        if blueprint_id:
            card.blueprint_id = blueprint_id
        card.zone = zone
        card.owner = owner
        card.location_index = -999
        card.target_card_id = None

        # Load metadata for new cards
        if is_new_card or (blueprint_id and not card.card_title):
            card.load_metadata()

        logger.debug(f"➕ Card {card_id} ({card.card_title or blueprint_id}) in zone {zone}")

    def _detach_card_from_parent(self, card: CardInPlay):
        """Remove card from its parent's attached_cards list (bidirectional cleanup)"""
        if card.target_card_id:
            parent = self.cards_in_play.get(card.target_card_id)
            if parent and card in parent.attached_cards:
                parent.attached_cards.remove(card)
            card.target_card_id = None

    def _remove_card_from_location(self, card: CardInPlay):
        """Remove card from its current location's card lists"""
        if card.location_index >= 0 and card.location_index < len(self.locations):
            location = self.locations[card.location_index]
            if location:
                if card in location.my_cards:
                    location.my_cards.remove(card)
                if card in location.their_cards:
                    location.their_cards.remove(card)

        # Also remove from hand if present
        if card in self.cards_in_hand:
            self.cards_in_hand.remove(card)

    def add_card_to_play(self, card: CardInPlay):
        """
        Simple wrapper - adds a card using update_cards_in_play.
        For backwards compatibility with existing code.
        """
        self.update_cards_in_play(
            card_id=card.card_id,
            target_card_id=card.target_card_id,
            blueprint_id=card.blueprint_id,
            zone=card.zone,
            owner=card.owner,
            location_index=card.location_index
        )

    def remove_card(self, card_id: str):
        """
        Remove a card from play completely.

        Ported from C# AIBoardStateTracker.ParseRemoveCardEvent()

        Note: For LOCATIONS, this CLEARS the slot (sets cardId="") but doesn't
        remove it from the list - index positions are preserved.
        """
        card = self.cards_in_play.get(card_id)
        if not card:
            logger.warning(f"Tried to remove non-existent card: {card_id}")
            return

        # Check if this is a location card
        for i, loc in enumerate(self.locations):
            if loc and loc.card_id == card_id:
                # Clear the location slot instead of removing (C# behavior)
                loc.card_id = ""
                loc.blueprint_id = ""
                loc.site_name = ""
                loc.system_name = f"Empty Location {i}"
                logger.debug(f"➖ Cleared location slot {i} (was {card_id})")
                # Still remove from cards_in_play
                del self.cards_in_play[card_id]
                return

        # Clean up attachments and location references
        self._detach_card_from_parent(card)
        self._remove_card_from_location(card)

        # Remove cards attached to this card
        for attached in list(card.attached_cards):
            self._detach_card_from_parent(attached)

        # Remove from tracking
        del self.cards_in_play[card_id]
        logger.debug(f"➖ Removed card: {card_id}")

    def update_card(self, card_id: str, zone: Optional[str] = None,
                   location_index: Optional[int] = None,
                   target_card_id: Optional[str] = None):
        """
        Update an existing card's position/zone.

        Wrapper for MCIP events which don't provide blueprintId.
        """
        card = self.cards_in_play.get(card_id)
        if not card:
            logger.warning(f"Tried to update non-existent card: {card_id}")
            return

        # Use existing blueprint_id since MCIP doesn't provide it
        self.update_cards_in_play(
            card_id=card_id,
            target_card_id=target_card_id,
            blueprint_id=card.blueprint_id,  # Keep existing
            zone=zone or card.zone,
            owner=card.owner,
            location_index=location_index if location_index is not None else card.location_index
        )

    # ========== Location Management ==========

    def _ensure_location_exists(self, index: int):
        """Ensure locations list is large enough for this index"""
        while len(self.locations) <= index:
            self.locations.append(None)

        # If the location at this index is None, create a placeholder
        if self.locations[index] is None:
            self.locations[index] = LocationInPlay(
                card_id="",  # Empty string = placeholder (matches C# logic)
                blueprint_id="unknown",
                owner="unknown",
                location_index=index,
                system_name=f"Location {index}"
            )

    def add_location(self, location: LocationInPlay):
        """
        Add a location to the board.

        Ported from C# AIBoardStateTracker.UpdateCardsInPlayMap() LOCATIONS zone handling.

        Key insight: When a new location is added at an index that already has a real
        location, we INSERT (shift existing locations right), not REPLACE.
        Only empty placeholders (cardId == "") get replaced.
        """
        index = location.location_index

        # Check if we can reuse an existing slot
        reuse_slot = False
        if index < len(self.locations) and self.locations[index] is not None:
            existing = self.locations[index]
            if existing.card_id == "" or existing.card_id.startswith("temp_location_"):
                # Empty placeholder - reuse this slot
                reuse_slot = True
                # Transfer any cards that were placed here before the location was defined
                location.my_cards = existing.my_cards
                location.their_cards = existing.their_cards
                location.attached_cards = existing.attached_cards
                if existing.my_cards or existing.their_cards:
                    logger.debug(f"📦 Preserved {len(existing.my_cards)} + {len(existing.their_cards)} cards from placeholder")

        if reuse_slot:
            # Replace the placeholder
            self.locations[index] = location
            logger.info(f"🏛️  Added location {index}: {location.site_name or location.system_name or location.blueprint_id} (card_id={location.card_id})")
        elif index >= len(self.locations):
            # Index is beyond list - extend and add
            while len(self.locations) < index:
                self.locations.append(None)
            self.locations.append(location)
            logger.info(f"🏛️  Added location {index}: {location.site_name or location.system_name or location.blueprint_id} (card_id={location.card_id})")
        else:
            # INSERT at index, shifting existing locations right (C# behavior)
            self.locations.insert(index, location)
            logger.info(f"🏛️  Inserted location {index}: {location.site_name or location.system_name or location.blueprint_id} (card_id={location.card_id})")

            # Update location_index for all shifted locations and their cards
            for i in range(index + 1, len(self.locations)):
                shifted_loc = self.locations[i]
                if shifted_loc and shifted_loc.card_id:
                    old_index = shifted_loc.location_index
                    shifted_loc.location_index = i
                    logger.debug(f"  📍 Shifted location '{shifted_loc.site_name or shifted_loc.system_name}' from index {old_index} -> {i}")

                    # Update cards at this location to have the new index
                    for card in shifted_loc.my_cards + shifted_loc.their_cards:
                        card.location_index = i

                    # Update the location card in cards_in_play
                    if shifted_loc.card_id in self.cards_in_play:
                        self.cards_in_play[shifted_loc.card_id].location_index = i

        # Also add to cards_in_play map (location cards have locationIndex = -999 in C#)
        card = CardInPlay(
            card_id=location.card_id,
            blueprint_id=location.blueprint_id,
            zone="LOCATIONS",
            owner=location.owner,
            location_index=-999  # Match C# behavior - location cards don't have a "location_index"
        )
        self.cards_in_play[location.card_id] = card

    def get_location_by_card_id(self, card_id: str) -> Optional[LocationInPlay]:
        """Find a location by its card ID"""
        for loc in self.locations:
            if loc and loc.card_id == card_id:
                return loc
        return None

    # ========== Power/Ability Queries ==========

    def my_power_at_location(self, location_index: int) -> int:
        """Get my power at a specific location index"""
        if self.my_side == "dark":
            return self.dark_power_at_locations.get(location_index, 0)
        else:
            return self.light_power_at_locations.get(location_index, 0)

    def their_power_at_location(self, location_index: int) -> int:
        """Get opponent's power at a specific location index"""
        if self.my_side == "dark":
            return self.light_power_at_locations.get(location_index, 0)
        else:
            return self.dark_power_at_locations.get(location_index, 0)

    def total_my_power(self) -> int:
        """Sum of my power across all locations (only positive values)"""
        total = 0
        for i in range(len(self.locations)):
            if self.locations[i]:  # Only count actual locations
                power = self.my_power_at_location(i)
                # Only count positive power (negative values are force icons)
                if power > 0:
                    total += power
        return total

    def total_their_power(self) -> int:
        """Sum of opponent's power across all locations (only positive values)"""
        total = 0
        for i in range(len(self.locations)):
            if self.locations[i]:  # Only count actual locations
                power = self.their_power_at_location(i)
                # Only count positive power (negative values are force icons)
                if power > 0:
                    total += power
        return total

    def my_ability_at_location(self, location_index: int) -> int:
        """
        Calculate my ability at a location.
        TODO: Implement full ability calculation from card stats.
        For now, count cards as rough estimate.
        """
        if location_index >= len(self.locations) or not self.locations[location_index]:
            return 0
        return len(self.locations[location_index].my_cards)

    # ========== Resource Queries ==========

    def can_afford(self, cost: int) -> bool:
        """Check if I have enough Force to afford a cost"""
        return self.force_pile >= cost

    def force_advantage(self) -> int:
        """Force differential (my force - their force)"""
        return self.force_pile - self.their_force_pile

    def power_advantage(self) -> int:
        """Power differential (my power - their power)"""
        return self.total_my_power() - self.total_their_power()

    def reserve_deck_low(self) -> bool:
        """Check if reserve deck is running low"""
        return self.reserve_deck <= 14

    # ========== Status Methods ==========

    def is_my_turn(self) -> bool:
        """Check if it's my turn"""
        return self.current_turn_player == self.my_player_name

    @property
    def phase(self) -> str:
        """Alias for current_phase for backward compatibility"""
        return self.current_phase

    def total_reserve_force(self) -> int:
        """
        Total force in reserve (reserve deck + used pile + force pile).
        Used to determine if we're running low on resources.
        """
        return self.reserve_deck + self.used_pile + self.force_pile

    def force_to_activate(self, max_available: int) -> int:
        """
        Calculate how much force to activate this turn.

        Ported from C# BotAIHelper.ForceToActivate():
        - If we already have lots of force (>12), only activate a few more
        - If reserve is running low, leave some in reserve for destiny draws

        Args:
            max_available: Maximum force we can activate

        Returns:
            Amount of force we should activate
        """
        amount = max_available
        current_force = self.force_pile
        reserve_size = self.total_reserve_force()

        # If we already have plenty of force, only activate a little more
        if current_force > 12:
            amount = max(0, 2 - self.force_activated_this_turn)
            logger.debug(f"Force > 12, limiting to {amount} more")

        # If reserve is running low, leave some for destiny draws
        if reserve_size <= amount:
            amount = max(0, reserve_size - 3)
            logger.debug(f"Reserve low ({reserve_size}), limiting to {amount}")

        return amount

    def __repr__(self):
        return (f"BoardState(locations={len(self.locations)}, "
                f"cards_in_play={len(self.cards_in_play)}, "
                f"hand={len(self.cards_in_hand)}, "
                f"force={self.force_pile}, "
                f"power={self.total_my_power()})")

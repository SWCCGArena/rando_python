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

# =============================================================================
# BATTLE ORDER / BATTLE PLAN CARD IDs
# =============================================================================
# When these cards are deployed to either player's side_of_table, force drains
# cost +3 unless player occupies a battleground site AND battleground system.
#
# Dark Side: Battle Order (forces Light to pay +3 for drains unless occupying both)
# Light Side: Battle Plan (forces Dark to pay +3 for drains unless occupying both)
#
# Either side can deploy their version to trigger these rules globally.
# =============================================================================

# Dark Side Battle Order cards
BATTLE_ORDER_DARK = {
    "8_118",   # Battle Order (Effect - Endor)
    "13_54",   # Battle Order (Defensive Shield - Reflections 3)
    "12_129",  # Battle Order & First Strike (Effect - Coruscant)
}

# Light Side Battle Plan cards (same effect as Dark's Battle Order)
BATTLE_PLAN_LIGHT = {
    "8_35",    # Battle Plan (Effect - Endor)
    "13_8",    # Battle Plan (Defensive Shield - Reflections 3)
    "12_41",   # Battle Plan & Draw Their Fire (Effect - Coruscant)
}

# All cards that trigger Battle Order rules
ALL_BATTLE_ORDER_CARDS = BATTLE_ORDER_DARK | BATTLE_PLAN_LIGHT


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
    forfeit: int = 0

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
                self.forfeit = metadata.forfeit_value or 0


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

        # Deploy planner reference (set by brain when creating plan)
        # Used to display the current plan in admin UI
        self.deploy_plan_summary = None

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

        # Battle damage tracking (from GS events during damage segment)
        self.dark_attrition_remaining: int = 0  # Attrition Dark side must satisfy
        self.dark_damage_remaining: int = 0     # Battle damage Dark side must satisfy
        self.light_attrition_remaining: int = 0 # Attrition Light side must satisfy
        self.light_damage_remaining: int = 0    # Battle damage Light side must satisfy

        # Weapons segment tracking - cards that have been "hit" this battle
        # Cleared at battle end. Hit cards shouldn't be targeted again.
        self.hit_cards: Set[str] = set()

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
        self.hit_cards.clear()
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
        if location_index < 0 or location_index >= len(self.locations) or not self.locations[location_index]:
            return 0
        return len(self.locations[location_index].my_cards)

    def my_card_count_at_location(self, location_index: int) -> int:
        """Count my cards at a location (characters, vehicles, starships)"""
        if location_index < 0 or location_index >= len(self.locations) or not self.locations[location_index]:
            return 0
        return len(self.locations[location_index].my_cards)

    def their_card_count_at_location(self, location_index: int) -> int:
        """Count opponent's cards at a location (characters, vehicles, starships)"""
        if location_index < 0 or location_index >= len(self.locations) or not self.locations[location_index]:
            return 0
        return len(self.locations[location_index].their_cards)

    def get_location_by_index(self, location_index: int) -> Optional['LocationInPlay']:
        """Get a LocationInPlay by its index, or None if invalid/empty."""
        if location_index < 0 or location_index >= len(self.locations):
            return None
        return self.locations[location_index]

    def my_power_from_cards(self, location_index: int) -> int:
        """
        Calculate my power at a location by summing card power values.

        More reliable than power dictionaries which may be stale.
        """
        if location_index < 0 or location_index >= len(self.locations) or not self.locations[location_index]:
            return 0
        total = 0
        for card in self.locations[location_index].my_cards:
            if card.power and card.power > 0:
                total += card.power
        return total

    def their_power_from_cards(self, location_index: int) -> int:
        """
        Calculate opponent's power at a location by summing card power values.

        More reliable than power dictionaries which may be stale.
        """
        if location_index < 0 or location_index >= len(self.locations) or not self.locations[location_index]:
            return 0
        total = 0
        for card in self.locations[location_index].their_cards:
            if card.power and card.power > 0:
                total += card.power
        return total

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

    # ========== Weapons Segment Hit Tracking ==========

    def mark_card_hit(self, card_id: str) -> None:
        """
        Mark a card as 'hit' during the weapons segment.
        Hit cards shouldn't be targeted again - it's wasteful.
        """
        self.hit_cards.add(card_id)
        logger.info(f"🎯 Marked card {card_id} as HIT")

    def is_card_hit(self, card_id: str) -> bool:
        """Check if a card has already been hit this battle."""
        return card_id in self.hit_cards

    def clear_hit_cards(self) -> None:
        """Clear hit tracking at battle end."""
        if self.hit_cards:
            logger.debug(f"Clearing {len(self.hit_cards)} hit cards")
            self.hit_cards.clear()

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

    def their_total_life_force(self) -> int:
        """
        Opponent's total life force (reserve deck + used pile + force pile).
        """
        return self.their_reserve_deck + self.their_used_pile + self.their_force_pile

    def should_concede(self) -> tuple[bool, str]:
        """
        Determine if we should concede the game.

        Smarter concede conditions:
        1. IMMEDIATE: Battle damage exceeds remaining life force (fatal damage)
        2. Total life force < 6 (nearly depleted)
        3. Can't afford to deploy any cards in hand with available force
           (available = total_lifeforce - 3, reserving 3 for draw phase)
        4. No contested locations where a battle could turn things around

        Returns:
            Tuple of (should_concede, reason)
        """
        from .card_loader import get_card

        my_life = self.total_reserve_force()
        their_life = self.their_total_life_force()

        # ==============================================================
        # IMMEDIATE CHECK: Fatal battle damage
        # If we're in a battle and damage exceeds our life force, concede NOW
        # This prevents making opponents wait while we process inevitable loss
        # ==============================================================
        my_side = getattr(self, 'my_side', 'dark')
        if my_side == 'dark':
            my_damage = self.dark_damage_remaining
            my_attrition = self.dark_attrition_remaining
        else:
            my_damage = self.light_damage_remaining
            my_attrition = self.light_attrition_remaining

        # Total damage we need to lose from life force
        # (attrition can be satisfied by forfeiting cards, but battle damage goes to life force)
        total_fatal_damage = my_damage
        if total_fatal_damage > 0 and total_fatal_damage >= my_life:
            reason = f"Fatal battle damage! {total_fatal_damage} damage >= {my_life} life force remaining"
            logger.info(f"🏳️ FATAL DAMAGE: {reason}")
            return True, reason

        # Also check if attrition is so high we can't survive
        # Even if we forfeit all cards, leftover attrition goes to life force
        # Rough estimate: if attrition > (cards_in_play * 3) + my_life, we're doomed
        my_cards_in_play = sum(1 for c in self.cards_in_play.values()
                               if c.owner == self.my_player_name)
        max_forfeit_value = my_cards_in_play * 3  # Rough estimate of forfeit value
        leftover_attrition = max(0, my_attrition - max_forfeit_value)
        total_life_damage = my_damage + leftover_attrition
        if total_life_damage > 0 and total_life_damage >= my_life:
            reason = f"Unsurvivable damage! {my_damage} damage + ~{leftover_attrition} leftover attrition >= {my_life} life force"
            logger.info(f"🏳️ UNSURVIVABLE: {reason}")
            return True, reason

        # Don't concede if we still have reasonable life force
        if my_life >= 6:
            return False, ""

        # Calculate force we would have available after activation
        # Bot reserves ~3 force for draw phase, so activatable = my_life - 3
        # But also consider current force pile
        force_after_activation = max(self.force_pile, my_life - 3)
        if force_after_activation < 0:
            force_after_activation = 0

        # Check if we can afford ANY deployable card in hand
        can_afford_deployment = False
        cheapest_deploy_cost = float('inf')

        for card in self.cards_in_hand:
            if card.blueprint_id:
                metadata = get_card(card.blueprint_id)
                if metadata and metadata.deploy_value and metadata.deploy_value > 0:
                    deploy_cost = metadata.deploy_value
                    cheapest_deploy_cost = min(cheapest_deploy_cost, deploy_cost)

                    # Can we afford this card after activating?
                    if force_after_activation >= deploy_cost:
                        can_afford_deployment = True
                        break

        # Check if there are any contested locations where a battle could happen
        # A battle opportunity means we have presence at a location with enemy presence
        has_battle_opportunity = False
        for loc in self.locations:
            if loc:
                loc_index = getattr(loc, 'location_index', -1)
                if loc_index >= 0:
                    my_power = self.my_power_at_location(loc_index)
                    their_power = self.their_power_at_location(loc_index)
                    # Both have presence = contested = battle opportunity
                    if my_power > 0 and their_power > 0:
                        has_battle_opportunity = True
                        logger.debug(f"🏳️ Battle opportunity at {getattr(loc, 'site_name', 'unknown')}: "
                                    f"my_power={my_power}, their_power={their_power}")
                        break

        # Concede if:
        # - Low life force (< 6)
        # - Can't afford any deployments
        # - No battle opportunities to turn things around
        if my_life < 6 and not can_afford_deployment and not has_battle_opportunity:
            if cheapest_deploy_cost == float('inf'):
                deploy_info = "no deployable cards in hand"
            else:
                deploy_info = f"cheapest card costs {cheapest_deploy_cost}, only {force_after_activation} force available"
            reason = (f"Life force critical ({my_life}), {deploy_info}, "
                      f"no battle opportunities")
            logger.info(f"🏳️ Concede check: {reason}")
            return True, reason

        # Also concede if life force is extremely low (< 3) even if we could deploy
        # something small - game is essentially over
        if my_life < 3:
            life_difference = their_life - my_life
            if life_difference >= 5 and not has_battle_opportunity:
                reason = f"Life force nearly depleted ({my_life}), opponent ahead by {life_difference}, no battle chances"
                logger.info(f"🏳️ Concede check: {reason}")
                return True, reason

        return False, ""

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

    # ========== Deployable Power Calculation ==========

    def total_hand_deployable_ground_power(self, card_id_to_ignore: str = "") -> int:
        """
        Calculate total power we can deploy to GROUND locations this turn.

        Uses a power-maximizing approach: prioritize high-power cards to maximize
        the total power we can deploy within our force budget.

        Considers:
        - Characters and vehicles (not starships)
        - Force available to pay deploy costs
        - Ignores "pure pilots" (low power pilots we should save for ships)

        Args:
            card_id_to_ignore: Card ID to exclude (e.g., the card we're evaluating)

        Returns:
            Total deployable ground power given our force budget
        """
        from .card_loader import get_card

        deployable = []
        for card in self.cards_in_hand:
            if card.card_id == card_id_to_ignore:
                continue
            metadata = get_card(card.blueprint_id) if card.blueprint_id else None
            if not metadata:
                continue

            # Skip starships - they don't deploy to ground
            if metadata.is_starship:
                continue

            # Skip "pure pilots" - low power pilots we should save for piloting ships
            # C# logic: isPilot && !isWarrior && power <= 4, OR isWarrior && isPilot && power <= 3
            if metadata.is_pilot and not metadata.is_warrior and metadata.power_value <= 4:
                continue
            if metadata.is_warrior and metadata.is_pilot and metadata.power_value <= 3:
                continue

            deployable.append({
                'power': metadata.power_value or 0,
                'cost': metadata.deploy_value or 0,
                'name': metadata.title
            })

        # Reserve 1 force for other actions
        available_force = self.force_pile - 1
        if available_force <= 0:
            return 0

        # Use power-maximizing approach: Sort by power (highest first)
        # This ensures we prioritize deploying our best cards
        deployable.sort(key=lambda x: x['power'], reverse=True)

        # Greedy selection: pick highest power cards that fit
        total_power = 0
        cost_so_far = 0

        for card in deployable:
            if cost_so_far + card['cost'] <= available_force:
                total_power += card['power']
                cost_so_far += card['cost']

        # If the greedy approach didn't get us to threshold, try a smarter approach
        # Use 0/1 knapsack dynamic programming for small sets
        if total_power < 6 and len(deployable) <= 10:
            # Try to maximize power within budget using DP
            dp_power = self._knapsack_max_power(deployable, available_force)
            if dp_power > total_power:
                total_power = dp_power

        return total_power

    def _knapsack_max_power(self, cards: list, budget: int) -> int:
        """
        Use dynamic programming to find maximum deployable power within budget.
        This is the 0/1 knapsack problem: maximize power subject to cost <= budget.
        """
        if not cards or budget <= 0:
            return 0

        # DP table: dp[i] = max power achievable with exactly i force spent
        # Use budget + 1 to handle 0 to budget inclusive
        max_budget = min(budget + 1, 50)  # Cap to avoid huge arrays
        dp = [0] * max_budget

        for card in cards:
            cost = card['cost']
            power = card['power']
            if cost > budget:
                continue
            # Process in reverse to avoid using same card twice
            for i in range(max_budget - 1, cost - 1, -1):
                if dp[i - cost] + power > dp[i]:
                    dp[i] = dp[i - cost] + power

        return max(dp)

    def total_hand_deployable_space_power(self, card_id_to_ignore: str = "") -> int:
        """
        Calculate total power we can deploy to SPACE locations this turn.

        Uses power-maximizing approach to prioritize high-power starships.

        Considers:
        - Starships only (with permanent pilot or that we can pilot)
        - Force available to pay deploy costs

        Args:
            card_id_to_ignore: Card ID to exclude (e.g., the card we're evaluating)

        Returns:
            Total deployable space power given our force budget
        """
        from .card_loader import get_card

        deployable = []
        for card in self.cards_in_hand:
            if card.card_id == card_id_to_ignore:
                continue
            metadata = get_card(card.blueprint_id) if card.blueprint_id else None
            if not metadata:
                continue

            # Only starships deploy to space
            if not metadata.is_starship:
                continue

            # Skip unpiloted starships (they have 0 power)
            if not metadata.has_permanent_pilot:
                # TODO: Check if we have a pilot that could pilot this
                continue

            deployable.append({
                'power': metadata.power_value or 0,
                'cost': metadata.deploy_value or 0,
                'name': metadata.title
            })

        # Reserve 1 force
        available_force = self.force_pile - 1
        if available_force <= 0:
            return 0

        # Power-maximizing: sort by power (highest first)
        deployable.sort(key=lambda x: x['power'], reverse=True)

        # Greedy selection: pick highest power cards that fit
        total_power = 0
        cost_so_far = 0

        for card in deployable:
            if cost_so_far + card['cost'] <= available_force:
                total_power += card['power']
                cost_so_far += card['cost']

        # Use knapsack for small sets if needed
        if total_power < 6 and len(deployable) <= 10:
            dp_power = self._knapsack_max_power(deployable, available_force)
            if dp_power > total_power:
                total_power = dp_power

        return total_power

    def total_hand_deployable_power(self, include_activation: bool = False) -> int:
        """
        Calculate total power we can deploy this turn (ground + space).

        Args:
            include_activation: If True, include force we can still activate

        Returns:
            Total deployable power
        """
        return (self.total_hand_deployable_ground_power() +
                self.total_hand_deployable_space_power())

    # ========== Flee/Movement Analysis ==========

    def get_system_name(self, loc_idx: int) -> str:
        """
        Extract the system name from a location.

        Examples:
        - "Naboo: Swamp" -> "Naboo"
        - "Tatooine: Mos Eisley" -> "Tatooine"
        - "Coruscant" (system card) -> "Coruscant"
        """
        if loc_idx < 0 or loc_idx >= len(self.locations):
            return ""

        loc = self.locations[loc_idx]
        if not loc:
            return ""

        # Try site_name first (e.g., "Naboo: Swamp")
        name = loc.site_name or loc.system_name or ""

        # Extract system prefix (before the colon)
        if ":" in name:
            return name.split(":")[0].strip()

        # No colon - might be a system card itself
        return name.strip()

    def find_same_system_locations(self, loc_idx: int) -> List[int]:
        """
        Find other locations in the same system.

        For ground movement, you can only move between locations in the same
        system (e.g., "Naboo: Swamp" -> "Naboo: Theed Palace Throne Room").

        Returns list of location indices in the same system (excluding current).
        """
        system_name = self.get_system_name(loc_idx)
        if not system_name:
            return []

        same_system = []
        for i, loc in enumerate(self.locations):
            if i == loc_idx:
                continue
            if loc and self.get_system_name(i) == system_name:
                same_system.append(i)

        return same_system

    def find_adjacent_locations(self, loc_idx: int) -> List[int]:
        """
        Find locations adjacent to this one (index +/- 1 AND same system).

        In SWCCG, ground locations are only adjacent if:
        1. They are physically next to each other on the board (index +/- 1)
        2. They are in the SAME system (e.g., both "Tatooine:" locations)

        A Naboo location is NEVER adjacent to a Tatooine location, even if
        they happen to be next to each other on the board.

        Returns list of valid adjacent location indices.
        """
        adjacent = []

        # Get our system name for comparison
        our_system = self.get_system_name(loc_idx)
        if not our_system:
            return adjacent

        # Check left
        if loc_idx > 0 and loc_idx - 1 < len(self.locations):
            left_loc = self.locations[loc_idx - 1]
            if left_loc:
                left_system = self.get_system_name(loc_idx - 1)
                # Must be in the same system to be adjacent
                if left_system == our_system:
                    adjacent.append(loc_idx - 1)

        # Check right
        if loc_idx + 1 < len(self.locations):
            right_loc = self.locations[loc_idx + 1]
            if right_loc:
                right_system = self.get_system_name(loc_idx + 1)
                # Must be in the same system to be adjacent
                if right_system == our_system:
                    adjacent.append(loc_idx + 1)

        return adjacent

    def find_hyperspeed_destinations(self, loc_idx: int) -> List[int]:
        """
        Find space locations reachable by hyperspeed from current location.

        Starships can move to systems where parsec difference <= hyperspeed.
        Returns list of valid destination location indices (space systems only).
        """
        if loc_idx < 0 or loc_idx >= len(self.locations):
            return []

        current_loc = self.locations[loc_idx]
        if not current_loc or not current_loc.is_space:
            return []

        # Get parsec of current system from card metadata
        current_metadata = _get_card_metadata(current_loc.blueprint_id) if current_loc.blueprint_id else None
        if not current_metadata:
            return []

        current_parsec = current_metadata.parsec
        if current_parsec is None:
            return []

        # Try to parse parsec as int
        try:
            current_parsec = int(current_parsec)
        except (ValueError, TypeError):
            return []

        # Get our starships' hyperspeed at this location
        max_hyperspeed = 0
        for card in current_loc.my_cards:
            card_meta = _get_card_metadata(card.blueprint_id) if card.blueprint_id else None
            if card_meta and card_meta.is_starship:
                try:
                    hs = int(card_meta.hyperspeed) if card_meta.hyperspeed else 0
                    max_hyperspeed = max(max_hyperspeed, hs)
                except (ValueError, TypeError):
                    pass

        if max_hyperspeed == 0:
            return []

        # Find all space systems within hyperspeed range
        destinations = []
        for idx, loc in enumerate(self.locations):
            if idx == loc_idx:
                continue
            if not loc or not loc.is_space:
                continue

            dest_metadata = _get_card_metadata(loc.blueprint_id) if loc.blueprint_id else None
            if not dest_metadata:
                continue

            dest_parsec = dest_metadata.parsec
            if dest_parsec is None:
                continue

            try:
                dest_parsec = int(dest_parsec)
            except (ValueError, TypeError):
                continue

            # Check if within hyperspeed range
            parsec_diff = abs(dest_parsec - current_parsec)
            if parsec_diff <= max_hyperspeed:
                destinations.append(idx)

        return destinations

    def my_character_count_at_location(self, loc_idx: int) -> int:
        """
        Count my characters at a location (for movement cost calculation).

        Movement costs 1 Force per character moved.
        """
        if loc_idx < 0 or loc_idx >= len(self.locations):
            return 0

        loc = self.locations[loc_idx]
        if not loc:
            return 0

        count = 0
        for card in loc.my_cards:
            metadata = _get_card_metadata(card.blueprint_id) if card.blueprint_id else None
            if metadata and metadata.is_character:
                count += 1

        return count

    def my_starship_count_at_location(self, loc_idx: int) -> int:
        """
        Count my starships at a location (for space movement cost).

        Movement costs 1 Force per starship moved.
        """
        if loc_idx < 0 or loc_idx >= len(self.locations):
            return 0

        loc = self.locations[loc_idx]
        if not loc:
            return 0

        count = 0
        for card in loc.my_cards:
            metadata = _get_card_metadata(card.blueprint_id) if card.blueprint_id else None
            if metadata and metadata.is_starship:
                count += 1

        return count

    def analyze_flee_options(self, loc_idx: int, is_space: bool = False) -> dict:
        """
        Analyze flee viability from a location.

        Returns a dict with:
        - can_flee: bool - Whether fleeing is possible
        - flee_destinations: List of (loc_idx, their_power, our_power) tuples
        - best_destination: loc_idx of best place to flee to (or None)
        - movement_cost: Force required to move all our units
        - can_afford: bool - Whether we have enough Force
        - reason: str - Explanation

        Args:
            loc_idx: Location index we're considering fleeing from
            is_space: True if this is a space location (use hyperspeed rules)
        """
        result = {
            'can_flee': False,
            'flee_destinations': [],
            'best_destination': None,
            'movement_cost': 0,
            'can_afford': False,
            'reason': "Unknown"
        }

        if loc_idx < 0 or loc_idx >= len(self.locations):
            result['reason'] = "Invalid location"
            return result

        loc = self.locations[loc_idx]
        if not loc:
            result['reason'] = "Location not found"
            return result

        # Calculate movement cost
        if is_space:
            unit_count = self.my_starship_count_at_location(loc_idx)
        else:
            unit_count = self.my_character_count_at_location(loc_idx)

        result['movement_cost'] = unit_count
        result['can_afford'] = self.force_pile >= unit_count

        if unit_count == 0:
            result['reason'] = "No units to move"
            return result

        if not result['can_afford']:
            result['reason'] = f"Can't afford to move {unit_count} units (have {self.force_pile} Force)"
            return result

        # Find flee destinations
        if is_space:
            # Space movement uses hyperspeed/parsec rules
            destinations = self.find_hyperspeed_destinations(loc_idx)
        else:
            # Ground movement - same system, adjacent locations
            same_system = self.find_same_system_locations(loc_idx)
            adjacent = self.find_adjacent_locations(loc_idx)
            # Must be both in same system AND adjacent
            destinations = [d for d in adjacent if d in same_system]

        if not destinations:
            if is_space:
                result['reason'] = "No valid flee destinations (no systems within hyperspeed range)"
            else:
                result['reason'] = "No valid flee destinations (no same-system adjacent locations)"
            return result

        # Analyze each destination
        current_their_power = self.their_power_at_location(loc_idx)
        current_my_power = self.my_power_at_location(loc_idx)

        for dest_idx in destinations:
            dest_their_power = self.their_power_at_location(dest_idx)
            dest_our_power = self.my_power_at_location(dest_idx)
            result['flee_destinations'].append((dest_idx, dest_their_power, dest_our_power))

        # Find best destination (where opponent is weakest)
        best_dest = None
        best_advantage = float('-inf')

        for dest_idx, dest_their_power, dest_our_power in result['flee_destinations']:
            # After moving, we'd add our power there
            # For simplicity, assume we move all power
            potential_our_power = dest_our_power + current_my_power
            advantage = potential_our_power - dest_their_power

            if advantage > best_advantage:
                best_advantage = advantage
                best_dest = dest_idx

        result['best_destination'] = best_dest
        result['can_flee'] = best_dest is not None

        if best_dest is not None:
            dest_their = self.their_power_at_location(best_dest)
            if dest_their > current_their_power:
                result['reason'] = f"Can flee but destination has more enemies ({dest_their} vs {current_their_power})"
            elif dest_their == 0:
                result['reason'] = f"Can flee to empty location (idx {best_dest})"
            else:
                result['reason'] = f"Can flee to location with fewer enemies ({dest_their} vs {current_their_power})"

        return result

    # ========== Battle Order Detection ==========

    def is_under_battle_order(self) -> bool:
        """
        Check if either player has Battle Order/Battle Plan deployed.

        This directly checks cards in SIDE_OF_TABLE zone for Battle Order
        (Dark) or Battle Plan (Light) cards, avoiding expensive cardInfo
        network calls.

        When under Battle Order rules, force drains cost +3 Force unless
        the draining player occupies both a battleground site AND a
        battleground system.

        Returns:
            True if Battle Order/Plan is in play (either player)
        """
        for card in self.cards_in_play.values():
            if card.zone == "SIDE_OF_TABLE":
                if card.blueprint_id in ALL_BATTLE_ORDER_CARDS:
                    logger.debug(f"⚔️ Battle Order detected: {card.card_title or card.blueprint_id} "
                                f"(owner: {card.owner})")
                    return True
        return False

    def get_battle_order_card(self) -> Optional[CardInPlay]:
        """
        Get the Battle Order/Plan card in play, if any.

        Returns:
            The Battle Order/Plan CardInPlay, or None
        """
        for card in self.cards_in_play.values():
            if card.zone == "SIDE_OF_TABLE":
                if card.blueprint_id in ALL_BATTLE_ORDER_CARDS:
                    return card
        return None

    def __repr__(self):
        return (f"BoardState(locations={len(self.locations)}, "
                f"cards_in_play={len(self.cards_in_play)}, "
                f"hand={len(self.cards_in_hand)}, "
                f"force={self.force_pile}, "
                f"power={self.total_my_power()})")

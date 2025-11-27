"""
Card Loader

Loads Star Wars CCG card metadata from JSON files.
Provides lookup by blueprint ID (gempId) for card information.
"""

import json
import logging
from typing import Dict, Optional
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Card:
    """
    Represents a SWCCG card with all metadata.
    """
    # Core identity
    blueprint_id: str                   # gempId (e.g., "7_163")
    title: str                          # Card name
    side: str                           # "Dark" or "Light"
    card_type: str                      # "Character", "Location", "Effect", etc.
    sub_type: Optional[str] = None      # "Droid", "System", etc.

    # Combat stats (as strings, may be "*", "X", or numeric)
    power: Optional[str] = None
    ability: Optional[str] = None
    deploy: Optional[str] = None
    forfeit: Optional[str] = None
    destiny: Optional[str] = None

    # Location/Space stats
    parsec: Optional[str] = None
    system_orbits: Optional[str] = None

    # Starship/Vehicle stats
    hyperspeed: Optional[str] = None
    landspeed: Optional[str] = None
    maneuver: Optional[str] = None
    armor: Optional[str] = None

    # Force icons
    light_side_icons: int = 0
    dark_side_icons: int = 0

    # Game text and lore
    gametext: str = ""
    lore: str = ""

    # Characteristics and keywords
    characteristics: list = field(default_factory=list)
    icons: list = field(default_factory=list)

    # Relationships
    matching: Optional[str] = None
    counterpart: Optional[str] = None

    # Rarity and set info
    rarity: Optional[str] = None
    set_number: Optional[str] = None

    # Special flags
    is_unique: bool = False             # Title starts with "•"
    is_defensive_shield: bool = False   # "Defensive Shield" in gametext

    # Parsed numeric values (for easy access)
    @property
    def power_value(self) -> int:
        """Get power as integer (0 if not numeric)"""
        try:
            return int(self.power) if self.power and self.power.isdigit() else 0
        except (ValueError, AttributeError):
            return 0

    @property
    def ability_value(self) -> int:
        """Get ability as integer (0 if not numeric)"""
        try:
            return int(self.ability) if self.ability and self.ability.isdigit() else 0
        except (ValueError, AttributeError):
            return 0

    @property
    def deploy_value(self) -> int:
        """Get deploy cost as integer (0 if not numeric)"""
        try:
            return int(self.deploy) if self.deploy and self.deploy.isdigit() else 0
        except (ValueError, AttributeError):
            return 0

    @property
    def forfeit_value(self) -> int:
        """Get forfeit as integer (0 if not numeric)"""
        try:
            return int(self.forfeit) if self.forfeit and self.forfeit.isdigit() else 0
        except (ValueError, AttributeError):
            return 0

    @property
    def is_character(self) -> bool:
        return self.card_type == "Character"

    @property
    def is_starship(self) -> bool:
        return self.card_type == "Starship"

    @property
    def is_vehicle(self) -> bool:
        return self.card_type == "Vehicle"

    @property
    def is_location(self) -> bool:
        return self.card_type == "Location"

    @property
    def is_effect(self) -> bool:
        return self.card_type == "Effect"

    @property
    def is_interrupt(self) -> bool:
        return self.card_type == "Interrupt"

    @property
    def is_weapon(self) -> bool:
        return self.card_type == "Weapon"

    @property
    def is_device(self) -> bool:
        return self.card_type == "Device"

    @property
    def is_droid(self) -> bool:
        """Check if card is a droid (Character with Droid subtype)"""
        return self.is_character and self.sub_type and 'droid' in self.sub_type.lower()

    @property
    def provides_presence(self) -> bool:
        """
        Check if this card provides 'presence' at a location.

        In SWCCG, presence requires a character with ability > 0.
        Droids (ability = 0) do NOT provide presence on their own.
        Without presence you cannot:
        - Prevent opponent's force drains
        - Initiate battles
        - Control the location
        """
        if not self.is_character:
            return False
        # Characters with ability > 0 provide presence
        # Droids typically have ability = 0
        return self.ability_value > 0

    # Icon-based properties (from icons field)
    @property
    def is_pilot(self) -> bool:
        """
        Check if card is a pilot CHARACTER (has Pilot icon and is a Character).

        Note: Starships/vehicles with permanent pilots also have the pilot icon,
        but they are NOT pilots themselves - use has_permanent_pilot for those.
        """
        if not self.is_character:
            return False
        return any('pilot' in str(icon).lower() for icon in self.icons)

    @property
    def is_warrior(self) -> bool:
        """Check if card has Warrior icon"""
        return any('warrior' in str(icon).lower() for icon in self.icons)

    @property
    def has_permanent_pilot(self) -> bool:
        """
        Check if starship/vehicle has a permanent pilot.

        On starships and vehicles, the "pilot" icon means the ship has a
        permanent pilot built-in (e.g., "Boba Fett In Slave I" has pilot icon).
        Ships without this icon (e.g., "Jabba's Sail Barge") need an external pilot.
        """
        if not (self.is_starship or self.is_vehicle):
            return False
        return any('pilot' in str(icon).lower() for icon in self.icons)

    @property
    def is_interior(self) -> bool:
        """Check if location has Interior icon (ground location)"""
        return any('interior' in str(icon).lower() for icon in self.icons)

    @property
    def is_exterior(self) -> bool:
        """Check if location has Exterior icon (ground location)"""
        return any('exterior' in str(icon).lower() for icon in self.icons)

    @property
    def has_planet_icon(self) -> bool:
        """Check if location has Planet icon (ground deployment for sites)"""
        return any('planet' in str(icon).lower() for icon in self.icons)

    @property
    def has_space_icon(self) -> bool:
        """Check if location has Space icon or Starship icon (indicates space location)"""
        return any(icon_name in str(icon).lower() for icon in self.icons
                   for icon_name in ['space', 'starship'])

    @property
    def is_docking_bay(self) -> bool:
        """Check if this is a docking bay (allows both space and ground)"""
        # Check title or icons for docking bay
        if self.title and 'docking bay' in self.title.lower():
            return True
        return any('docking' in str(icon).lower() for icon in self.icons)

    @property
    def is_starship_site(self) -> bool:
        """Check if this is a starship site (space location that is a site)"""
        # Starship sites are on starships and are space locations
        if self.title:
            title_lower = self.title.lower()
            # Starship names followed by site names (e.g., "Executor: Docking Bay")
            starship_prefixes = ['executor:', 'home one:', 'death star:', 'super star destroyer:',
                                 'star destroyer:', 'blockade runner:', 'millennium falcon:']
            return any(prefix in title_lower for prefix in starship_prefixes)
        return False

    def __repr__(self):
        return f"Card({self.title} [{self.blueprint_id}], {self.card_type}, {self.side})"


class CardDatabase:
    """
    Loads and provides access to card metadata.
    """

    def __init__(self, card_json_dir: str = "/opt/gemp/rando_cal_working/swccg-card-json"):
        self.card_json_dir = Path(card_json_dir)
        self.cards: Dict[str, Card] = {}  # Keyed by blueprint_id (gempId)
        self._loaded = False

    def load(self):
        """Load all card data from JSON files"""
        if self._loaded:
            logger.debug("Cards already loaded, skipping")
            return

        logger.info(f"Loading card data from {self.card_json_dir}")

        # Load Dark side cards
        dark_path = self.card_json_dir / "Dark.json"
        if dark_path.exists():
            self._load_json_file(dark_path, "Dark")
        else:
            logger.error(f"Dark.json not found at {dark_path}")

        # Load Light side cards
        light_path = self.card_json_dir / "Light.json"
        if light_path.exists():
            self._load_json_file(light_path, "Light")
        else:
            logger.error(f"Light.json not found at {light_path}")

        self._loaded = True
        logger.info(f"✅ Loaded {len(self.cards)} cards total")

    def _load_json_file(self, file_path: Path, side: str):
        """Load cards from a single JSON file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            cards_data = data.get('cards', [])
            loaded_count = 0

            for card_data in cards_data:
                card = self._parse_card(card_data, side)
                if card:
                    self.cards[card.blueprint_id] = card
                    loaded_count += 1

            logger.info(f"Loaded {loaded_count} {side} cards from {file_path.name}")

        except Exception as e:
            logger.error(f"Error loading {file_path}: {e}", exc_info=True)

    def _parse_card(self, card_data: dict, side: str) -> Optional[Card]:
        """Parse a single card from JSON"""
        try:
            blueprint_id = card_data.get('gempId', '')
            if not blueprint_id:
                return None

            # Get front face data
            front = card_data.get('front', {})
            if not front:
                return None

            title = front.get('title', 'Unknown')
            is_unique = title.startswith('•')

            # Parse icons for force generation
            # Force icon counts are explicit fields in the JSON
            icons = front.get('icons', [])
            light_icons = front.get('lightSideIcons', 0) or 0
            dark_icons = front.get('darkSideIcons', 0) or 0

            # Check for defensive shield
            gametext = front.get('gametext', '')
            is_defensive_shield = 'Defensive Shield' in gametext

            card = Card(
                blueprint_id=blueprint_id,
                title=title,
                side=side,
                card_type=front.get('type', 'Unknown'),
                sub_type=front.get('subType'),

                # Stats
                power=front.get('power'),
                ability=front.get('ability'),
                deploy=front.get('deploy'),
                forfeit=front.get('forfeit'),
                destiny=front.get('destiny'),

                # Location/Space
                parsec=front.get('parsec'),
                system_orbits=front.get('systemOrbits'),

                # Starship/Vehicle
                hyperspeed=front.get('hyperspeed'),
                landspeed=front.get('landspeed'),
                maneuver=front.get('maneuver'),
                armor=front.get('armor'),

                # Force icons
                light_side_icons=light_icons,
                dark_side_icons=dark_icons,

                # Text
                gametext=gametext,
                lore=front.get('lore', ''),

                # Characteristics
                characteristics=front.get('characteristics', []),
                icons=icons,

                # Relationships
                matching=card_data.get('matching'),
                counterpart=card_data.get('counterpart'),

                # Metadata
                rarity=card_data.get('rarity'),
                set_number=card_data.get('set'),

                # Flags
                is_unique=is_unique,
                is_defensive_shield=is_defensive_shield,
            )

            return card

        except Exception as e:
            logger.warning(f"Error parsing card {card_data.get('gempId', 'unknown')}: {e}")
            return None

    def get_card(self, blueprint_id: str) -> Optional[Card]:
        """
        Get a card by its blueprint ID (gempId).

        Args:
            blueprint_id: The gempId (e.g., "7_163")

        Returns:
            Card object or None if not found
        """
        if not self._loaded:
            self.load()

        return self.cards.get(blueprint_id)

    def get_card_title(self, blueprint_id: str) -> str:
        """Get card title by blueprint ID (returns blueprint ID if not found)"""
        card = self.get_card(blueprint_id)
        return card.title if card else blueprint_id

    def search_by_title(self, title: str) -> list[Card]:
        """Search for cards by title (case-insensitive partial match)"""
        if not self._loaded:
            self.load()

        title_lower = title.lower()
        return [card for card in self.cards.values()
                if title_lower in card.title.lower()]


# Global card database instance
_card_db: Optional[CardDatabase] = None


def get_card_database() -> CardDatabase:
    """Get the global card database instance (lazy loaded)"""
    global _card_db
    if _card_db is None:
        _card_db = CardDatabase()
        _card_db.load()
    return _card_db


def get_card(blueprint_id: str) -> Optional[Card]:
    """Convenience function to get a card by blueprint ID"""
    return get_card_database().get_card(blueprint_id)


def get_card_title(blueprint_id: str) -> str:
    """Convenience function to get a card title"""
    return get_card_database().get_card_title(blueprint_id)

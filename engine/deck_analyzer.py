"""
Deck Analyzer - Parse deck XML files and categorize cards for archetype detection.

Analyzes deck composition to enable strategic decision-making based on
what cards are in the deck (space-focused, ground swarm, mains, etc.)
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from engine.card_loader import get_card, Card

logger = logging.getLogger(__name__)

# Patterns for detecting card categories
TROOPER_PATTERNS = [
    "trooper", "stormtrooper", "clone trooper", "rebel trooper",
    "first order stormtrooper", "snowtrooper", "sandtrooper",
    "scout trooper", "death trooper"
]

JEDI_SITH_PATTERNS = [
    "jedi", "sith", "with lightsaber", "padawan", "master",
    "darth", "emperor", "yoda", "luke", "anakin", "obi-wan",
    "mace windu", "qui-gon", "ahsoka", "kylo", "rey"
]


@dataclass
class DeckComposition:
    """Analyzed composition of a deck."""
    deck_name: str
    side: str  # "Dark" or "Light"

    # Card lists by type (blueprint_ids)
    characters: List[str] = field(default_factory=list)
    starships: List[str] = field(default_factory=list)
    vehicles: List[str] = field(default_factory=list)
    locations_ground: List[str] = field(default_factory=list)
    locations_space: List[str] = field(default_factory=list)
    weapons: List[str] = field(default_factory=list)
    effects: List[str] = field(default_factory=list)
    interrupts: List[str] = field(default_factory=list)
    other: List[str] = field(default_factory=list)

    # Card name lists (for key card identification)
    character_names: List[str] = field(default_factory=list)
    starship_names: List[str] = field(default_factory=list)

    # Derived metrics
    total_cards: int = 0
    ship_count: int = 0
    vehicle_count: int = 0
    ground_character_count: int = 0
    pilot_count: int = 0
    trooper_count: int = 0
    unique_character_count: int = 0
    jedi_sith_count: int = 0

    # Location metrics
    ground_location_count: int = 0
    space_location_count: int = 0
    total_ground_icons: int = 0
    total_space_icons: int = 0

    # High-value characters (ability >= 3 or power >= 5)
    high_value_characters: List[str] = field(default_factory=list)


class DeckAnalyzer:
    """Analyzes deck composition from XML files."""

    # Default decks directory
    DEFAULT_DECKS_DIR = Path("/mnt/ubuntu-lv/swccg/gemp/rando_cal_working/decks")

    def __init__(self, decks_dir: Optional[Path] = None):
        """
        Initialize the deck analyzer.

        Args:
            decks_dir: Directory containing deck XML files.
                      Defaults to /mnt/ubuntu-lv/swccg/gemp/rando_cal_working/decks/
        """
        self.decks_dir = decks_dir or self.DEFAULT_DECKS_DIR

    def analyze_deck_file(self, deck_path: str) -> Optional[DeckComposition]:
        """
        Parse deck XML file and categorize all cards.

        Args:
            deck_path: Path to deck XML file

        Returns:
            DeckComposition with categorized cards and metrics
        """
        path = Path(deck_path)
        if not path.exists():
            logger.warning(f"Deck file not found: {deck_path}")
            return None

        try:
            content = path.read_text()
            deck_name = path.stem  # filename without extension
            return self.analyze_deck_xml(content, deck_name)
        except Exception as e:
            logger.error(f"Error parsing deck file {deck_path}: {e}")
            return None

    def analyze_deck_by_name(self, deck_name: str) -> Optional[DeckComposition]:
        """
        Find and analyze a deck by name.

        Args:
            deck_name: Name of the deck (e.g., "dark_baseline" or "Astrogation Chart 0000")

        Returns:
            DeckComposition or None if not found
        """
        # Try exact match first
        deck_path = self.decks_dir / f"{deck_name}.txt"
        if deck_path.exists():
            return self.analyze_deck_file(str(deck_path))

        # Try case-insensitive search
        for path in self.decks_dir.glob("*.txt"):
            if path.stem.lower() == deck_name.lower():
                return self.analyze_deck_file(str(path))

        logger.warning(f"Deck not found: {deck_name}")
        return None

    def analyze_deck_xml(self, xml_content: str, deck_name: str = "unknown") -> DeckComposition:
        """
        Parse deck XML string and categorize all cards.

        Args:
            xml_content: XML string content of deck file
            deck_name: Name to assign to the deck

        Returns:
            DeckComposition with categorized cards and metrics
        """
        composition = DeckComposition(deck_name=deck_name, side="unknown")

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            logger.error(f"XML parse error for deck {deck_name}: {e}")
            return composition

        # Find all card elements (both in-deck and outside-deck)
        card_elements = root.findall(".//card")

        for card_elem in card_elements:
            blueprint_id = card_elem.get("blueprintId")
            title = card_elem.get("title", "")

            if not blueprint_id:
                continue

            composition.total_cards += 1
            self._categorize_card(blueprint_id, title, composition)

        # Determine side from cards
        composition.side = self._determine_side(composition)

        # Log summary
        logger.info(f"📊 Deck Analysis: {deck_name}")
        logger.info(f"   Side: {composition.side}")
        logger.info(f"   Total cards: {composition.total_cards}")
        logger.info(f"   Characters: {len(composition.characters)} "
                   f"(pilots: {composition.pilot_count}, troopers: {composition.trooper_count}, "
                   f"jedi/sith: {composition.jedi_sith_count})")
        logger.info(f"   Ships: {composition.ship_count}, Vehicles: {composition.vehicle_count}")
        logger.info(f"   Locations: {composition.ground_location_count} ground, "
                   f"{composition.space_location_count} space")
        logger.info(f"   Icons: {composition.total_ground_icons} ground, "
                   f"{composition.total_space_icons} space")

        return composition

    def _categorize_card(self, blueprint_id: str, title: str, composition: DeckComposition):
        """
        Look up card in CardDatabase and categorize it.

        Args:
            blueprint_id: Card's blueprint ID (gempId)
            title: Card title from deck XML
            composition: DeckComposition to update
        """
        card = get_card(blueprint_id)

        if card is None:
            # Card not found in database, categorize by title patterns
            composition.other.append(blueprint_id)
            return

        title_lower = title.lower() if title else card.title.lower()

        # Categorize by card type
        if card.is_character:
            composition.characters.append(blueprint_id)
            composition.character_names.append(card.title)
            self._analyze_character(card, title_lower, composition)

        elif card.is_starship:
            composition.starships.append(blueprint_id)
            composition.starship_names.append(card.title)
            composition.ship_count += 1

        elif card.is_vehicle:
            composition.vehicles.append(blueprint_id)
            composition.vehicle_count += 1

        elif card.is_location:
            self._analyze_location(card, composition)

        elif card.is_weapon:
            composition.weapons.append(blueprint_id)

        elif card.is_effect:
            composition.effects.append(blueprint_id)

        elif card.card_type == "Interrupt":
            composition.interrupts.append(blueprint_id)

        else:
            composition.other.append(blueprint_id)

    def _analyze_character(self, card: Card, title_lower: str, composition: DeckComposition):
        """Analyze a character card for special categories."""

        # Check if pilot
        if card.is_pilot:
            composition.pilot_count += 1

        # Check if trooper (generic troops)
        is_trooper = any(pattern in title_lower for pattern in TROOPER_PATTERNS)
        if is_trooper:
            composition.trooper_count += 1

        # Check if unique (named character)
        # Uniques typically have specific names, not generic titles
        is_generic = is_trooper or any(generic in title_lower for generic in [
            "guard", "officer", "pilot", "gunner", "crew", "technician"
        ])
        if not is_generic:
            composition.unique_character_count += 1

        # Check if Jedi/Sith
        is_jedi_sith = any(pattern in title_lower for pattern in JEDI_SITH_PATTERNS)
        if is_jedi_sith:
            composition.jedi_sith_count += 1

        # Check if high-value character
        power = card.power_value
        ability = card.ability_value
        if ability >= 3 or power >= 5:
            composition.high_value_characters.append(card.title)

        # Count ground characters (non-pilots or characters that can deploy to ground)
        # Most characters can deploy to ground, so count all non-starfighter-only
        composition.ground_character_count += 1

    def _analyze_location(self, card: Card, composition: DeckComposition):
        """Analyze a location card for domain and icons."""

        # Determine if space or ground location
        sub_type = (card.sub_type or "").lower()
        is_space = "system" in sub_type or "sector" in sub_type

        if is_space:
            composition.locations_space.append(card.blueprint_id)
            composition.space_location_count += 1
            # Add icons
            composition.total_space_icons += card.light_side_icons + card.dark_side_icons
        else:
            composition.locations_ground.append(card.blueprint_id)
            composition.ground_location_count += 1
            # Add icons
            composition.total_ground_icons += card.light_side_icons + card.dark_side_icons

    def _determine_side(self, composition: DeckComposition) -> str:
        """Determine deck side (Dark/Light) from card data."""

        # Check a sample of cards
        dark_count = 0
        light_count = 0

        for bp_id in (composition.characters + composition.starships)[:10]:
            card = get_card(bp_id)
            if card:
                if card.side == "Dark":
                    dark_count += 1
                elif card.side == "Light":
                    light_count += 1

        if dark_count > light_count:
            return "Dark"
        elif light_count > dark_count:
            return "Light"
        return "unknown"


# Convenience function
def analyze_deck(deck_name: str, decks_dir: Optional[Path] = None) -> Optional[DeckComposition]:
    """
    Convenience function to analyze a deck by name.

    Args:
        deck_name: Name of the deck file (without .txt extension)
        decks_dir: Optional custom decks directory

    Returns:
        DeckComposition or None if deck not found
    """
    analyzer = DeckAnalyzer(decks_dir)
    return analyzer.analyze_deck_by_name(deck_name)

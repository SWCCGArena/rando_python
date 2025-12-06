"""
Objective Handler

Handles SWCCG objective card requirements for starting card selection.
Each objective has specific cards that must be deployed at game start.

When the bot receives an ARBITRARY_CARDS decision during "Play starting cards"
phase, this handler identifies which cards are required by the objective.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set

logger = logging.getLogger(__name__)

# =============================================================================
# OBJECTIVE REQUIREMENTS DATABASE
# Each entry: objective_blueprint_id -> list of required card patterns
#
# Patterns can be:
# - Exact blueprint_id (e.g., "221_54" for Clone Command Center)
# - "title:" prefix for title match (e.g., "title:Cloning Cylinders")
# - "characteristic:" prefix (e.g., "characteristic:clone army battleground")
# =============================================================================

OBJECTIVE_REQUIREMENTS: Dict[str, List[str]] = {
    # ==========================================================================
    # LIGHT SIDE OBJECTIVES
    # ==========================================================================

    # Hunt For The Droid General / He's A Coward (221_67)
    # Deploy a [Clone Army] battleground, Clone Command Center (to same planet),
    # Cloning Cylinders, and Grievous Will Run And Hide.
    "221_67": [
        "211_42",  # Kamino: Clone Birthing Center
        "221_54",  # Clone Command Center
        "211_53",  # Cloning Cylinders
        "221_65",  # Grievous Will Run And Hide
    ],

    # Massassi Base Operations / One In A Million (111_4)
    # Deploy Yavin 4 system and Yavin 4: Docking Bay.
    "111_4": [
        "1_296",  # Yavin 4 (system)
        "1_297",  # Yavin 4: Docking Bay
    ],

    # Yavin 4 Base Operations / The Time To Fight Is Now (208_26)
    # Deploy Yavin 4 system and Massassi War Room.
    "208_26": [
        "1_296",  # Yavin 4 (system)
        "1_139",  # Massassi War Room
    ],

    # City In The Clouds / You Truly Belong Here With Us (301_2)
    # Deploy Bespin system and a Cloud City battleground site.
    "301_2": [
        "5_164",  # Bespin (system)
        "title:Cloud City",  # Any Cloud City battleground
    ],

    # Quiet Mining Colony / Independent Operation (109_4)
    # Deploy Bespin system and one Cloud City battleground site.
    "109_4": [
        "5_164",  # Bespin (system)
        "title:Cloud City",  # Any Cloud City battleground
    ],

    # Mind What You Have Learned / Save You It Can (V) (225_53)
    # Deploy Beldon's Corridor, Yoda's Hut, [CC] No Disintegrations!, and Patience.
    "225_53": [
        "225_40",  # Beldon's Corridor
        "4_89",   # Yoda's Hut
        "4_28",   # No Disintegrations!
        "title:Patience",  # Patience
    ],

    # We Have A Plan / They Will Be Lost And Confused (14_52)
    # Deploy Theed Palace Throne Room, Theed Palace Hallway, and Theed Palace Courtyard.
    "14_52": [
        "12_174",  # Theed Palace Throne Room
        "14_112",  # Theed Palace Hallway
        "12_172",  # Theed Palace Courtyard
    ],

    # Agents In The Court / No Love For The Empire (112_1)
    # Deploy Hutt Trade Route and a Jabba's Palace site.
    "112_1": [
        "112_9",  # Hutt Trade Route
        "title:Jabba's Palace",  # Any Jabba's Palace site
    ],

    # My Kind Of Scum / Fearless And Inventive (112_15)
    # Deploy Desert Heart and a Jabba's Palace site.
    "112_15": [
        "112_20",  # Desert Heart
        "title:Jabba's Palace",  # Any Jabba's Palace site
    ],

    # He Is The Chosen One / He Will Bring Balance (208_25)
    # Deploy Anakin's Funeral Pyre, Ewok Village, and I Feel The Conflict.
    "208_25": [
        "217_34",  # Anakin's Funeral Pyre
        "8_163",   # Ewok Village
        "9_34",    # I Feel The Conflict
    ],

    # They Have No Idea We're Coming / Until We Win (209_29)
    # Deploy Scarif system, Data Vault (with Stardust there), and Massassi War Room.
    "209_29": [
        "216_13",  # Scarif (system)
        "209_25",  # Data Vault
        "1_139",   # Massassi War Room
    ],

    # The Galaxy May Need A Legend / We Need Luke Skywalker (211_36)
    # Deploy Ahch-To system and any [Episode VII] battleground.
    "211_36": [
        "211_48",  # Ahch-To (system)
    ],

    # Old Allies / We Need Your Help (204_32)
    # Deploy Jakku system and Niima Outpost Shipyard (with [Ep VII] Falcon there).
    "204_32": [
        "204_51",  # Jakku (system)
        "204_27",  # Niima Outpost Shipyard
    ],

    # Zero Hour / Liberation Of Lothal (219_48)
    # Deploy Lothal system and a Lothal site.
    "219_48": [
        "219_10",  # Lothal (system)
        "title:Lothal:",  # Any Lothal site
    ],

    # Twin Suns Of Tatooine / Well Trained In The Jedi Arts (301_4)
    # Deploy Tatooine system and a non-Jabba's Palace Tatooine site.
    "301_4": [
        "1_127",  # Tatooine (Light system)
        "title:Tatooine:",  # Any Tatooine site (non-Jabba's Palace)
    ],

    # ==========================================================================
    # DARK SIDE OBJECTIVES
    # ==========================================================================

    # Agents Of Black Sun / Vengeance Of The Dark Prince (10_29)
    # Deploy Imperial City (with Xizor there) and Coruscant system.
    "10_29": [
        "7_277",   # Imperial City
        "200_144",  # Coruscant (system)
    ],

    # Carbon Chamber Testing / My Favorite Decoration (7_296)
    # Deploy Carbonite Chamber, Carbonite Chamber Console, and Security Tower.
    "7_296": [
        "5_166",  # Carbonite Chamber
        "5_107",  # Carbonite Chamber Console
        "5_172",  # Security Tower
    ],

    # Court Of The Vile Gangster / I Shall Enjoy Watching You Die (110_6)
    # Deploy Audience Chamber, Great Pit Of Carkoon, and Dungeon.
    "110_6": [
        "6_162",  # Audience Chamber
        "6_170",  # Great Pit Of Carkoon
        "6_164",  # Dungeon
    ],

    # Watch Your Step / This Place Can Be A Little Rough (10_26)
    # Deploy Cantina, Docking Bay 94, and Tatooine System.
    "10_26": [
        "1_290",  # Tatooine: Cantina (Dark)
        "1_291",  # Tatooine: Docking Bay 94 (Dark)
        "1_289",  # Tatooine (Dark system)
    ],

    # Invasion / In Complete Control (14_113)
    # Deploy Naboo system with Blockade Flagship there, swamp, and Droid Racks.
    "14_113": [
        "12_169",  # Naboo (Dark system)
        "14_111",  # Blockade Flagship
        "14_96",   # Droid Racks
        "title:Swamp",  # Swamp location
    ],

    # Hunt Down And Destroy The Jedi (V) (213_31)
    # Deploy Vader's Castle, [Set 13] Visage Of The Emperor, and a [CC] site.
    "213_31": [
        "209_50",  # Vader's Castle
        "213_16",  # Visage Of The Emperor (V)
        "title:Cloud City",  # Any Cloud City site with 1 dark icon
    ],

    # Shadow Collective / You Know Who I Answer To (213_32)
    # Deploy Maul's Chambers.
    "213_32": [
        "213_23",  # Maul's Chambers
    ],

    # I Want That Map / And Now You'll Give It To Me (208_57)
    # Deploy Tuanul Village, any other [Episode VII] location, and I Will Finish.
    "208_57": [
        "204_53",  # Tuanul Village
        "208_40",  # I Will Finish What You Started
    ],

    # The Shield Will Be Down In Moments / Imperial Troops Have Entered (222_14)
    # Deploy 5th Marker, 4th Marker, 1st Marker, and Prepare For A Surface Attack.
    "222_14": [
        "3_148",  # Hoth: Ice Plains (5th Marker)
        "3_149",  # Hoth: North Ridge (4th Marker)
        "222_9",  # Hoth: Main Power Generators (1st Marker) [Set 22]
        "13_82",  # Prepare For A Surface Attack
    ],

    # The Shield Will Be Down In Moments (AI) (222_30)
    # Same requirements as above
    "222_30": [
        "3_148",  # Hoth: Ice Plains (5th Marker)
        "3_149",  # Hoth: North Ridge (4th Marker)
        "222_9",  # Hoth: Main Power Generators (1st Marker) [Set 22]
        "13_82",  # Prepare For A Surface Attack
    ],

    # The First Order Reigns / The Resistance Is Doomed (225_32)
    # Deploy Crait and D'Qar systems, Supremacy: Bridge, and Tracked Fleet.
    "225_32": [
        "225_15",  # Crait (system)
        "211_19",  # D'Qar (system)
        "225_28",  # Supremacy: Bridge
        "225_34",  # Tracked Fleet
    ],
}


@dataclass
class ObjectiveHandler:
    """
    Handles objective card requirements for starting card selection.

    During "Play starting cards" phase, this helps identify which cards
    should be selected from the reserve deck to properly start the objective.
    """

    objective_blueprint_id: Optional[str] = None
    required_cards: List[str] = field(default_factory=list)
    deployed_requirements: Set[str] = field(default_factory=set)

    def set_objective(self, blueprint_id: str) -> None:
        """
        Set the active objective and load its requirements.

        Args:
            blueprint_id: The blueprint ID of the objective card
        """
        self.objective_blueprint_id = blueprint_id
        self.required_cards = OBJECTIVE_REQUIREMENTS.get(blueprint_id, [])
        self.deployed_requirements = set()

        if self.required_cards:
            logger.info(f"🎯 Objective {blueprint_id} loaded with {len(self.required_cards)} requirements")
        else:
            logger.debug(f"No special requirements for objective {blueprint_id}")

    def score_starting_card(self, blueprint_id: str, card_title: str) -> float:
        """
        Score a card for starting card selection based on objective requirements.

        Args:
            blueprint_id: The blueprint ID of the card to evaluate
            card_title: The title of the card

        Returns:
            Score bonus (0 if not required, positive if required)
        """
        if not self.required_cards:
            return 0.0

        # Check if this card matches any requirement
        for req in self.required_cards:
            # Skip already deployed requirements
            if req in self.deployed_requirements:
                continue

            if self._matches_requirement(blueprint_id, card_title, req):
                logger.info(f"🎯 {card_title} matches objective requirement: {req}")
                self.deployed_requirements.add(req)
                return 200.0  # High bonus for objective-required cards

        return 0.0

    def _matches_requirement(self, blueprint_id: str, card_title: str, requirement: str) -> bool:
        """
        Check if a card matches an objective requirement.

        Args:
            blueprint_id: The card's blueprint ID
            card_title: The card's title
            requirement: The requirement pattern

        Returns:
            True if the card matches the requirement
        """
        # Exact blueprint match
        if requirement == blueprint_id:
            return True

        # Title prefix match
        if requirement.startswith("title:"):
            req_title = requirement[6:].lower()  # Remove "title:" prefix
            if req_title in card_title.lower():
                return True

        # Characteristic match (for battleground types, etc.)
        if requirement.startswith("characteristic:"):
            # This would require card metadata lookup
            # For now, skip characteristic matching
            pass

        return False

    def get_remaining_requirements(self) -> List[str]:
        """Get list of requirements not yet deployed."""
        return [req for req in self.required_cards if req not in self.deployed_requirements]

    def is_objective_started(self) -> bool:
        """Check if all objective requirements have been deployed."""
        if not self.required_cards:
            return True
        return len(self.deployed_requirements) >= len(self.required_cards)

    def reset(self) -> None:
        """Reset handler for a new game."""
        self.objective_blueprint_id = None
        self.required_cards = []
        self.deployed_requirements = set()


# Global singleton instance
_objective_handler: Optional[ObjectiveHandler] = None


def get_objective_handler() -> ObjectiveHandler:
    """Get the global objective handler instance."""
    global _objective_handler
    if _objective_handler is None:
        _objective_handler = ObjectiveHandler()
    return _objective_handler


def reset_objective_handler() -> None:
    """Reset the objective handler for a new game."""
    global _objective_handler
    if _objective_handler is not None:
        _objective_handler.reset()

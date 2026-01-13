"""
Archetype Detector - Convert deck composition into strategic archetype and goals.

Analyzes DeckComposition to determine what strategy the deck is built for,
and provides corresponding strategic goals and scoring weights.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

from engine.deck_analyzer import DeckComposition

logger = logging.getLogger(__name__)


class DeckArchetype(Enum):
    """Strategic archetypes that decks can be classified as."""
    SPACE_CONTROL = "space_control"      # Dominate space locations with ships
    GROUND_SWARM = "ground_swarm"        # Spread troops across ground locations
    MAINS = "mains"                      # Protect key powerful characters
    DRAIN_RACE = "drain_race"            # Establish presence, avoid battles, win on drains
    BALANCED = "balanced"                # No strong lean either way


@dataclass
class StrategicGoals:
    """Goals and weights derived from deck archetype."""

    archetype: DeckArchetype
    primary_domain: str  # "space", "ground", or "both"

    # Target objectives
    target_location_count: int = 3        # How many locations to try to control
    key_cards: List[str] = field(default_factory=list)  # Cards to protect/prioritize

    # Behavioral settings
    avoid_battles_unless_favorable: bool = False  # If True, need bigger advantage to battle
    battle_advantage_required: int = 2            # Min power advantage to initiate battle
    save_force_threshold: int = 0                 # Don't spend if force would drop below this

    # Scoring multipliers (1.0 = neutral)
    space_deploy_multiplier: float = 1.0   # >1 = prefer space deploys
    ground_deploy_multiplier: float = 1.0  # >1 = prefer ground deploys
    battle_aggression: float = 1.0         # <1 = defensive, >1 = aggressive

    # Domain-specific bonuses added to plan scores
    space_location_bonus: int = 0    # Bonus for deploying to space
    ground_location_bonus: int = 0   # Bonus for deploying to ground

    def __str__(self) -> str:
        return (f"StrategicGoals(archetype={self.archetype.value}, "
                f"domain={self.primary_domain}, "
                f"space_mult={self.space_deploy_multiplier:.2f}, "
                f"ground_mult={self.ground_deploy_multiplier:.2f})")


# Archetype-specific goal templates
ARCHETYPE_GOALS = {
    DeckArchetype.SPACE_CONTROL: StrategicGoals(
        archetype=DeckArchetype.SPACE_CONTROL,
        primary_domain="space",
        target_location_count=3,
        avoid_battles_unless_favorable=False,
        battle_advantage_required=2,
        space_deploy_multiplier=1.3,
        ground_deploy_multiplier=0.8,
        battle_aggression=1.1,
        space_location_bonus=30,
        ground_location_bonus=0,
    ),
    DeckArchetype.GROUND_SWARM: StrategicGoals(
        archetype=DeckArchetype.GROUND_SWARM,
        primary_domain="ground",
        target_location_count=4,  # Swarm wants to spread
        avoid_battles_unless_favorable=False,
        battle_advantage_required=1,  # More willing to battle
        space_deploy_multiplier=0.8,
        ground_deploy_multiplier=1.3,
        battle_aggression=1.2,  # Aggressive with numbers
        space_location_bonus=0,
        ground_location_bonus=30,
    ),
    DeckArchetype.MAINS: StrategicGoals(
        archetype=DeckArchetype.MAINS,
        primary_domain="both",
        target_location_count=2,  # Quality over quantity
        avoid_battles_unless_favorable=True,
        battle_advantage_required=3,  # Protect key characters
        space_deploy_multiplier=1.0,
        ground_deploy_multiplier=1.0,
        battle_aggression=0.8,  # More cautious
        space_location_bonus=10,
        ground_location_bonus=10,
        save_force_threshold=3,  # Keep force for responses
    ),
    DeckArchetype.DRAIN_RACE: StrategicGoals(
        archetype=DeckArchetype.DRAIN_RACE,
        primary_domain="both",
        target_location_count=4,  # Spread for drains
        avoid_battles_unless_favorable=True,
        battle_advantage_required=4,  # Really avoid battles
        space_deploy_multiplier=1.0,
        ground_deploy_multiplier=1.0,
        battle_aggression=0.6,  # Very defensive
        space_location_bonus=15,
        ground_location_bonus=15,
    ),
    DeckArchetype.BALANCED: StrategicGoals(
        archetype=DeckArchetype.BALANCED,
        primary_domain="both",
        target_location_count=3,
        avoid_battles_unless_favorable=False,
        battle_advantage_required=2,
        space_deploy_multiplier=1.0,
        ground_deploy_multiplier=1.0,
        battle_aggression=1.0,
        space_location_bonus=0,
        ground_location_bonus=0,
    ),
}


class ArchetypeDetector:
    """Detects deck archetype from composition and provides strategic goals."""

    def detect(self, composition: DeckComposition) -> Tuple[DeckArchetype, StrategicGoals]:
        """
        Analyze deck composition and return archetype with strategic goals.

        Detection rules (evaluated in priority order):
        1. SPACE_CONTROL: ship_count >= 4 AND (pilot_count >= 6 OR space_locs > ground_locs)
        2. GROUND_SWARM: trooper_count >= 4 OR (characters >= 15 AND unique_ratio < 0.5)
        3. MAINS: jedi_sith_count >= 2 OR high_value_characters >= 5
        4. DRAIN_RACE: total_icons >= 8 AND low combat capability
        5. BALANCED: default

        Args:
            composition: DeckComposition from DeckAnalyzer

        Returns:
            Tuple of (DeckArchetype, StrategicGoals)
        """
        archetype = self._detect_archetype(composition)
        goals = self._create_goals(archetype, composition)

        logger.info(f"🎯 Archetype Detection: {composition.deck_name}")
        logger.info(f"   Archetype: {archetype.value}")
        logger.info(f"   Primary domain: {goals.primary_domain}")
        logger.info(f"   Space multiplier: {goals.space_deploy_multiplier:.2f}")
        logger.info(f"   Ground multiplier: {goals.ground_deploy_multiplier:.2f}")
        logger.info(f"   Key cards: {goals.key_cards[:3]}...")

        return archetype, goals

    def _detect_archetype(self, comp: DeckComposition) -> DeckArchetype:
        """Apply detection rules to determine archetype."""

        # Rule 1: SPACE_CONTROL
        # Strong ship presence with pilots OR space location preference
        space_focused = (
            comp.ship_count >= 4 and
            (comp.pilot_count >= 6 or comp.space_location_count > comp.ground_location_count)
        )
        if space_focused:
            logger.debug(f"Detected SPACE_CONTROL: ships={comp.ship_count}, "
                        f"pilots={comp.pilot_count}, space_locs={comp.space_location_count}")
            return DeckArchetype.SPACE_CONTROL

        # Rule 2: GROUND_SWARM
        # Many troopers OR high character count with low unique ratio
        total_chars = len(comp.characters)
        unique_ratio = comp.unique_character_count / max(1, total_chars)
        ground_swarm = (
            comp.trooper_count >= 4 or
            (total_chars >= 12 and unique_ratio < 0.6)
        )
        if ground_swarm:
            logger.debug(f"Detected GROUND_SWARM: troopers={comp.trooper_count}, "
                        f"chars={total_chars}, unique_ratio={unique_ratio:.2f}")
            return DeckArchetype.GROUND_SWARM

        # Rule 3: MAINS
        # Strong Force users or many high-value characters
        high_value_count = len(comp.high_value_characters)
        mains_focused = (
            comp.jedi_sith_count >= 2 or
            high_value_count >= 5
        )
        if mains_focused:
            logger.debug(f"Detected MAINS: jedi_sith={comp.jedi_sith_count}, "
                        f"high_value={high_value_count}")
            return DeckArchetype.MAINS

        # Rule 4: DRAIN_RACE
        # High icon count but low combat cards (few weapons, interrupts)
        total_icons = comp.total_ground_icons + comp.total_space_icons
        combat_cards = len(comp.weapons) + len([i for i in comp.interrupts])
        drain_focused = (
            total_icons >= 8 and
            combat_cards < 5 and
            comp.ship_count < 3
        )
        if drain_focused:
            logger.debug(f"Detected DRAIN_RACE: icons={total_icons}, combat={combat_cards}")
            return DeckArchetype.DRAIN_RACE

        # Rule 5: BALANCED (default)
        logger.debug("Detected BALANCED: no strong archetype signals")
        return DeckArchetype.BALANCED

    def _create_goals(self, archetype: DeckArchetype, comp: DeckComposition) -> StrategicGoals:
        """Create strategic goals based on archetype and deck composition."""

        # Start with template goals
        template = ARCHETYPE_GOALS[archetype]
        goals = StrategicGoals(
            archetype=archetype,
            primary_domain=template.primary_domain,
            target_location_count=template.target_location_count,
            avoid_battles_unless_favorable=template.avoid_battles_unless_favorable,
            battle_advantage_required=template.battle_advantage_required,
            space_deploy_multiplier=template.space_deploy_multiplier,
            ground_deploy_multiplier=template.ground_deploy_multiplier,
            battle_aggression=template.battle_aggression,
            space_location_bonus=template.space_location_bonus,
            ground_location_bonus=template.ground_location_bonus,
            save_force_threshold=template.save_force_threshold,
        )

        # Identify key cards to protect
        goals.key_cards = self._identify_key_cards(comp, archetype)

        # Adjust goals based on specific composition
        self._adjust_goals_for_composition(goals, comp)

        return goals

    def _identify_key_cards(self, comp: DeckComposition, archetype: DeckArchetype) -> List[str]:
        """Identify cards that should be protected/prioritized."""

        key_cards = []

        if archetype == DeckArchetype.MAINS:
            # High-value characters are key
            key_cards.extend(comp.high_value_characters[:5])

        elif archetype == DeckArchetype.SPACE_CONTROL:
            # Named ships are key
            key_cards.extend(comp.starship_names[:3])
            # Best pilots
            for name in comp.character_names:
                if len(key_cards) >= 5:
                    break
                # Add pilots with matching ships
                key_cards.append(name)

        else:
            # Default: top high-value characters
            key_cards.extend(comp.high_value_characters[:3])

        return key_cards

    def _adjust_goals_for_composition(self, goals: StrategicGoals, comp: DeckComposition):
        """Fine-tune goals based on specific deck composition."""

        # If deck has very few ships, reduce space preference even for space archetype
        if comp.ship_count < 2 and goals.primary_domain == "space":
            goals.space_deploy_multiplier = max(1.0, goals.space_deploy_multiplier - 0.2)
            logger.debug("Reduced space multiplier due to low ship count")

        # If deck has many troopers, increase aggression
        if comp.trooper_count >= 6:
            goals.battle_aggression = min(1.5, goals.battle_aggression + 0.2)
            goals.battle_advantage_required = max(0, goals.battle_advantage_required - 1)
            logger.debug("Increased aggression due to high trooper count")

        # If deck has many high-value characters, increase caution
        if len(comp.high_value_characters) >= 6:
            goals.save_force_threshold = max(goals.save_force_threshold, 2)
            goals.battle_advantage_required = max(goals.battle_advantage_required, 2)
            logger.debug("Increased caution due to many high-value characters")


# Convenience function
def detect_archetype(composition: DeckComposition) -> Tuple[DeckArchetype, StrategicGoals]:
    """
    Convenience function to detect archetype from deck composition.

    Args:
        composition: DeckComposition from DeckAnalyzer

    Returns:
        Tuple of (DeckArchetype, StrategicGoals)
    """
    detector = ArchetypeDetector()
    return detector.detect(composition)

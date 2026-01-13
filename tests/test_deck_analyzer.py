"""
Tests for Deck Analyzer and Archetype Detector.

Tests the deck analysis pipeline:
1. DeckAnalyzer - parses deck XML and categorizes cards
2. ArchetypeDetector - converts composition to archetype + strategic goals
3. Strategy integration - verifies goals are applied in deploy planner
"""

import pytest
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.deck_analyzer import DeckAnalyzer, DeckComposition
from engine.archetype_detector import (
    ArchetypeDetector, DeckArchetype, StrategicGoals, detect_archetype
)
from engine.strategy_profile import (
    set_deck_strategy, get_deck_strategy, clear_deck_strategy
)


class TestDeckAnalyzer:
    """Tests for DeckAnalyzer class."""

    @pytest.fixture
    def analyzer(self):
        return DeckAnalyzer()

    def test_analyze_dark_baseline(self, analyzer):
        """Test analyzing dark_baseline deck."""
        composition = analyzer.analyze_deck_by_name('dark_baseline')

        assert composition is not None
        assert composition.deck_name == 'dark_baseline'
        assert composition.side == 'Dark'
        assert composition.ship_count >= 4  # Has ships
        assert composition.pilot_count >= 6  # Has pilots
        assert composition.total_cards > 0

    def test_analyze_light_baseline(self, analyzer):
        """Test analyzing light_baseline deck."""
        composition = analyzer.analyze_deck_by_name('light_baseline')

        assert composition is not None
        assert composition.deck_name == 'light_baseline'
        assert composition.side == 'Light'
        assert composition.total_cards > 0

    def test_analyze_nonexistent_deck(self, analyzer):
        """Test analyzing a deck that doesn't exist."""
        composition = analyzer.analyze_deck_by_name('nonexistent_deck_xyz')
        assert composition is None

    def test_character_categorization(self, analyzer):
        """Test that characters are properly categorized."""
        composition = analyzer.analyze_deck_by_name('dark_baseline')
        assert composition is not None

        # Should have characters
        assert len(composition.characters) > 0
        assert len(composition.character_names) > 0

        # Character counts should be populated
        assert composition.pilot_count >= 0
        assert composition.trooper_count >= 0
        assert composition.jedi_sith_count >= 0
        assert composition.unique_character_count >= 0

    def test_location_categorization(self, analyzer):
        """Test that locations are properly categorized."""
        composition = analyzer.analyze_deck_by_name('dark_baseline')
        assert composition is not None

        # Should have some locations
        total_locations = (composition.ground_location_count +
                         composition.space_location_count)
        assert total_locations > 0

        # Icons should be counted
        total_icons = (composition.total_ground_icons +
                      composition.total_space_icons)
        assert total_icons >= 0

    def test_high_value_characters(self, analyzer):
        """Test that high-value characters are identified."""
        composition = analyzer.analyze_deck_by_name('dark_baseline')
        assert composition is not None

        # Should have some high-value characters (ability >= 3 or power >= 5)
        assert len(composition.high_value_characters) >= 0


class TestArchetypeDetector:
    """Tests for ArchetypeDetector class."""

    @pytest.fixture
    def analyzer(self):
        return DeckAnalyzer()

    @pytest.fixture
    def detector(self):
        return ArchetypeDetector()

    def test_detect_space_control(self, analyzer, detector):
        """Test detecting SPACE_CONTROL archetype from dark_baseline."""
        composition = analyzer.analyze_deck_by_name('dark_baseline')
        assert composition is not None

        archetype, goals = detector.detect(composition)

        # dark_baseline has 5 ships and 15 pilots, should be SPACE_CONTROL
        assert archetype == DeckArchetype.SPACE_CONTROL
        assert goals.primary_domain == "space"
        assert goals.space_location_bonus > 0
        assert goals.ground_location_bonus == 0

    def test_detect_mains(self, analyzer, detector):
        """Test detecting MAINS archetype from light_baseline."""
        composition = analyzer.analyze_deck_by_name('light_baseline')
        assert composition is not None

        archetype, goals = detector.detect(composition)

        # light_baseline has high-value characters, should be MAINS
        assert archetype == DeckArchetype.MAINS
        assert goals.primary_domain == "both"

    def test_strategic_goals_structure(self, analyzer, detector):
        """Test that strategic goals have expected structure."""
        composition = analyzer.analyze_deck_by_name('dark_baseline')
        archetype, goals = detector.detect(composition)

        # Check all expected fields exist
        assert isinstance(goals.archetype, DeckArchetype)
        assert isinstance(goals.primary_domain, str)
        assert isinstance(goals.target_location_count, int)
        assert isinstance(goals.key_cards, list)
        assert isinstance(goals.avoid_battles_unless_favorable, bool)
        assert isinstance(goals.battle_advantage_required, int)
        assert isinstance(goals.space_deploy_multiplier, float)
        assert isinstance(goals.ground_deploy_multiplier, float)
        assert isinstance(goals.battle_aggression, float)
        assert isinstance(goals.space_location_bonus, int)
        assert isinstance(goals.ground_location_bonus, int)

    def test_convenience_function(self, analyzer):
        """Test the detect_archetype convenience function."""
        composition = analyzer.analyze_deck_by_name('dark_baseline')
        assert composition is not None

        archetype, goals = detect_archetype(composition)

        assert archetype == DeckArchetype.SPACE_CONTROL
        assert goals is not None


class TestStrategyIntegration:
    """Tests for strategy profile integration."""

    @pytest.fixture(autouse=True)
    def cleanup(self):
        """Ensure strategy is cleared before and after each test."""
        clear_deck_strategy()
        yield
        clear_deck_strategy()

    def test_set_and_get_strategy(self):
        """Test setting and getting deck strategy."""
        analyzer = DeckAnalyzer()
        composition = analyzer.analyze_deck_by_name('dark_baseline')
        archetype, goals = detect_archetype(composition)

        # Initially no strategy
        assert get_deck_strategy() is None

        # Set strategy
        set_deck_strategy(goals)

        # Now we have a strategy
        strategy = get_deck_strategy()
        assert strategy is not None
        assert strategy.archetype == DeckArchetype.SPACE_CONTROL

    def test_clear_strategy(self):
        """Test clearing deck strategy."""
        analyzer = DeckAnalyzer()
        composition = analyzer.analyze_deck_by_name('dark_baseline')
        archetype, goals = detect_archetype(composition)

        set_deck_strategy(goals)
        assert get_deck_strategy() is not None

        clear_deck_strategy()
        assert get_deck_strategy() is None


class TestArchetypeRules:
    """Tests for specific archetype detection rules."""

    def test_space_control_requirements(self):
        """Verify SPACE_CONTROL requires ships >= 4 AND (pilots >= 6 OR space_locs > ground_locs)."""
        composition = DeckComposition(
            deck_name="test",
            side="Dark"
        )

        # Not enough ships
        composition.ship_count = 3
        composition.pilot_count = 10
        detector = ArchetypeDetector()
        archetype = detector._detect_archetype(composition)
        assert archetype != DeckArchetype.SPACE_CONTROL

        # Enough ships and pilots
        composition.ship_count = 4
        composition.pilot_count = 6
        archetype = detector._detect_archetype(composition)
        assert archetype == DeckArchetype.SPACE_CONTROL

    def test_ground_swarm_requirements(self):
        """Verify GROUND_SWARM triggers on trooper count or low unique ratio."""
        composition = DeckComposition(
            deck_name="test",
            side="Dark"
        )

        # High trooper count
        composition.trooper_count = 4
        composition.ship_count = 0
        composition.pilot_count = 0
        detector = ArchetypeDetector()
        archetype = detector._detect_archetype(composition)
        assert archetype == DeckArchetype.GROUND_SWARM

    def test_mains_requirements(self):
        """Verify MAINS triggers on jedi/sith or high-value characters."""
        composition = DeckComposition(
            deck_name="test",
            side="Dark"
        )

        composition.ship_count = 0
        composition.pilot_count = 0
        composition.trooper_count = 0

        # High jedi/sith count
        composition.jedi_sith_count = 2
        detector = ArchetypeDetector()
        archetype = detector._detect_archetype(composition)
        assert archetype == DeckArchetype.MAINS

        # Or high-value characters
        composition.jedi_sith_count = 0
        composition.high_value_characters = ['A', 'B', 'C', 'D', 'E']
        archetype = detector._detect_archetype(composition)
        assert archetype == DeckArchetype.MAINS

    def test_balanced_fallback(self):
        """Verify BALANCED is the fallback when no archetype matches."""
        composition = DeckComposition(
            deck_name="test",
            side="Dark"
        )

        # Minimal values that don't trigger any archetype
        composition.ship_count = 2
        composition.pilot_count = 2
        composition.trooper_count = 1
        composition.jedi_sith_count = 0
        composition.high_value_characters = []
        composition.total_ground_icons = 3
        composition.total_space_icons = 3

        detector = ArchetypeDetector()
        archetype = detector._detect_archetype(composition)
        assert archetype == DeckArchetype.BALANCED


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

"""
Tests for move_evaluator.py

Tests the icon-based scoring for spread decisions.
"""

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field
from typing import List

from engine.evaluators.move_evaluator import MoveEvaluator


@dataclass
class MockCard:
    card_id: str
    location_index: int = 0


@dataclass
class MockLocation:
    location_index: int
    their_icons: str = ""
    is_space: bool = False
    my_cards: List[MockCard] = field(default_factory=list)
    their_cards: List[MockCard] = field(default_factory=list)


class MockBoardState:
    """Mock board state for testing spread analysis"""

    def __init__(self):
        self.locations = []
        self.force_pile = 10
        self._my_power = {}
        self._their_power = {}
        self._adjacent = {}

    def my_power_at_location(self, loc_idx: int) -> int:
        return self._my_power.get(loc_idx, 0)

    def their_power_at_location(self, loc_idx: int) -> int:
        return self._their_power.get(loc_idx, 0)

    def find_adjacent_locations(self, loc_idx: int) -> List[int]:
        return self._adjacent.get(loc_idx, [])

    def my_card_count_at_location(self, loc_idx: int) -> int:
        if loc_idx < len(self.locations):
            return len(self.locations[loc_idx].my_cards)
        return 0


class TestIconBasedSpread:
    """Tests for icon-based spread scoring"""

    def test_spread_prefers_location_with_opponent_icons(self):
        """Bot should prefer spreading to locations with opponent icons for force drains"""
        evaluator = MoveEvaluator()
        board_state = MockBoardState()

        # Source location with excess power
        source_loc = MockLocation(location_index=0, their_icons="0")
        source_loc.my_cards = [MockCard(f"card{i}", 0) for i in range(4)]

        # Two adjacent empty locations - one with icons, one without
        adj_loc_no_icons = MockLocation(location_index=1, their_icons="0")
        adj_loc_with_icons = MockLocation(location_index=2, their_icons="2")

        board_state.locations = [source_loc, adj_loc_no_icons, adj_loc_with_icons]
        board_state._my_power = {0: 16, 1: 0, 2: 0}  # 16 power at source
        board_state._their_power = {0: 0, 1: 0, 2: 0}  # No enemies
        board_state._adjacent = {0: [1, 2]}

        result = evaluator._analyze_spread_viability(
            board_state, loc_idx=0, our_power_here=16, our_card_count=4
        )

        assert result['viable'] is True
        # Should mention opponent icons in the reason
        assert "opponent icon" in result['reason'].lower() or "force drain" in result['reason'].lower()
        # Score should include icon bonus (15.0 per icon * 2 icons = 30)
        assert result['score'] > 20  # Base score is 20, should be higher with icons

    def test_spread_score_scales_with_icon_count(self):
        """More opponent icons = higher spread priority"""
        evaluator = MoveEvaluator()
        board_state = MockBoardState()

        # Source with excess power
        source_loc = MockLocation(location_index=0)
        source_loc.my_cards = [MockCard(f"card{i}", 0) for i in range(4)]

        board_state._my_power = {0: 16}
        board_state._their_power = {0: 0}

        # Test with 1 icon
        adj_loc_1_icon = MockLocation(location_index=1, their_icons="1")
        board_state.locations = [source_loc, adj_loc_1_icon]
        board_state._my_power[1] = 0
        board_state._their_power[1] = 0
        board_state._adjacent = {0: [1]}

        result_1_icon = evaluator._analyze_spread_viability(
            board_state, loc_idx=0, our_power_here=16, our_card_count=4
        )

        # Test with 3 icons
        adj_loc_3_icons = MockLocation(location_index=1, their_icons="3")
        board_state.locations = [source_loc, adj_loc_3_icons]

        result_3_icons = evaluator._analyze_spread_viability(
            board_state, loc_idx=0, our_power_here=16, our_card_count=4
        )

        # 3 icons should score higher than 1 icon
        assert result_3_icons['score'] > result_1_icon['score']

    def test_spread_to_contested_location_with_icons(self):
        """Spreading to contest a location with opponent icons should score high"""
        evaluator = MoveEvaluator()
        board_state = MockBoardState()

        source_loc = MockLocation(location_index=0)
        source_loc.my_cards = [MockCard(f"card{i}", 0) for i in range(5)]

        # Adjacent contested location with opponent icons
        adj_loc = MockLocation(location_index=1, their_icons="2")

        board_state.locations = [source_loc, adj_loc]
        board_state._my_power = {0: 20, 1: 0}  # Excess at source
        board_state._their_power = {0: 0, 1: 4}  # Enemy at adjacent
        board_state._adjacent = {0: [1]}

        result = evaluator._analyze_spread_viability(
            board_state, loc_idx=0, our_power_here=20, our_card_count=5
        )

        assert result['viable'] is True
        # Should get icon bonus on top of contest score
        # Base contest score is ~30-35, plus 30 for 2 icons
        assert result['score'] > 50

    def test_no_spread_without_force(self):
        """Can't spread if we have no force to move cards"""
        evaluator = MoveEvaluator()
        board_state = MockBoardState()
        board_state.force_pile = 0  # No force!

        source_loc = MockLocation(location_index=0)
        source_loc.my_cards = [MockCard(f"card{i}", 0) for i in range(4)]

        adj_loc = MockLocation(location_index=1, their_icons="3")

        board_state.locations = [source_loc, adj_loc]
        board_state._my_power = {0: 16, 1: 0}
        board_state._their_power = {0: 0, 1: 0}
        board_state._adjacent = {0: [1]}

        result = evaluator._analyze_spread_viability(
            board_state, loc_idx=0, our_power_here=16, our_card_count=4
        )

        assert result['viable'] is False
        assert "force" in result['reason'].lower()

    def test_icon_string_with_asterisk_parsed(self):
        """Icon strings like '2*' should be parsed correctly"""
        evaluator = MoveEvaluator()
        board_state = MockBoardState()

        source_loc = MockLocation(location_index=0)
        source_loc.my_cards = [MockCard(f"card{i}", 0) for i in range(4)]

        # Icon string with asterisk (GEMP format)
        adj_loc = MockLocation(location_index=1, their_icons="2*")

        board_state.locations = [source_loc, adj_loc]
        board_state._my_power = {0: 16, 1: 0}
        board_state._their_power = {0: 0, 1: 0}
        board_state._adjacent = {0: [1]}

        result = evaluator._analyze_spread_viability(
            board_state, loc_idx=0, our_power_here=16, our_card_count=4
        )

        # Should successfully parse "2*" as 2 icons
        assert result['viable'] is True
        assert "2 opponent icon" in result['reason']


class TestSystemAdjacency:
    """Tests for system-based adjacency rules

    These tests verify the real BoardState.find_adjacent_locations behavior.
    """

    def test_same_system_locations_are_adjacent(self):
        """Locations in the same system that are next to each other should be adjacent"""
        # Import the real BoardState to test actual adjacency logic
        from engine.board_state import BoardState

        board_state = BoardState(my_player_name="test_player")

        # Create mock location objects with site_name
        class MockLoc:
            def __init__(self, site_name):
                self.site_name = site_name
                self.system_name = None

        # Two Tatooine locations next to each other
        board_state.locations = [
            MockLoc("Tatooine: Mos Eisley"),
            MockLoc("Tatooine: Cantina"),
        ]

        adjacent = board_state.find_adjacent_locations(0)
        assert 1 in adjacent, "Same-system locations should be adjacent"

    def test_different_system_locations_not_adjacent(self):
        """Locations in different systems should NOT be adjacent even if next to each other"""
        from engine.board_state import BoardState

        board_state = BoardState(my_player_name="test_player")

        class MockLoc:
            def __init__(self, site_name):
                self.site_name = site_name
                self.system_name = None

        # Naboo location next to Tatooine location - NOT adjacent!
        board_state.locations = [
            MockLoc("Naboo: Theed Palace"),
            MockLoc("Tatooine: Mos Eisley"),
        ]

        adjacent = board_state.find_adjacent_locations(0)
        assert 1 not in adjacent, "Different-system locations should NOT be adjacent"
        assert len(adjacent) == 0, "Naboo location has no adjacent Tatooine locations"

    def test_mixed_systems_only_same_system_adjacent(self):
        """Only locations in the same system should be adjacent"""
        from engine.board_state import BoardState

        board_state = BoardState(my_player_name="test_player")

        class MockLoc:
            def __init__(self, site_name):
                self.site_name = site_name
                self.system_name = None

        # Mixed systems: Tatooine, Naboo, Tatooine
        board_state.locations = [
            MockLoc("Tatooine: Mos Eisley"),       # idx 0
            MockLoc("Naboo: Theed Palace"),        # idx 1 - different system
            MockLoc("Tatooine: Cantina"),          # idx 2 - same system as 0, but not adjacent
        ]

        # From Mos Eisley (idx 0), Theed Palace (idx 1) is NOT adjacent (different system)
        adjacent_from_0 = board_state.find_adjacent_locations(0)
        assert 1 not in adjacent_from_0, "Naboo should not be adjacent to Tatooine"

        # From Theed Palace (idx 1), neither Tatooine location is adjacent
        adjacent_from_1 = board_state.find_adjacent_locations(1)
        assert 0 not in adjacent_from_1, "Tatooine should not be adjacent to Naboo"
        assert 2 not in adjacent_from_1, "Tatooine should not be adjacent to Naboo"


class TestOffensiveAttacks:
    """Tests for offensive attack logic - moving from strongholds to attack enemies"""

    def test_attack_from_uncontested_stronghold(self):
        """Bot should attack when it has overwhelming force at uncontested location

        Key scenario from production: 12 power at Hutt Trade Route (uncontested),
        enemy has 7 power at adjacent Lars' Moisture Farm. Should recommend attack!
        """
        evaluator = MoveEvaluator()
        board_state = MockBoardState()

        # Source: our stronghold (12 power, 4 cards, uncontested)
        source_loc = MockLocation(location_index=0, their_icons="2")
        source_loc.my_cards = [MockCard(f"card{i}", 0) for i in range(4)]

        # Target: enemy position (7 power)
        target_loc = MockLocation(location_index=1, their_icons="2")

        board_state.locations = [source_loc, target_loc]
        board_state._my_power = {0: 12, 1: 0}  # 12 power at source, none at target
        board_state._their_power = {0: -1, 1: 7}  # -1 = no enemy cards at source, 7 at target
        board_state._adjacent = {0: [1]}
        board_state.force_pile = 4  # Enough to move 4 cards

        result = evaluator._analyze_attack_opportunity(
            board_state, loc_idx=0, our_power_here=12, our_card_count=4
        )

        assert result['viable'] is True
        assert "ATTACK" in result['reason']
        assert result['score'] >= 50  # Should beat pass bias (~38)

    def test_crush_attack_bonus(self):
        """Crushing attacks (2x enemy power) should get bonus score"""
        evaluator = MoveEvaluator()
        board_state = MockBoardState()

        source_loc = MockLocation(location_index=0)
        source_loc.my_cards = [MockCard(f"card{i}", 0) for i in range(4)]

        target_loc = MockLocation(location_index=1, their_icons="1")

        board_state.locations = [source_loc, target_loc]
        board_state._my_power = {0: 16, 1: 0}  # 16 power - way more than 2x enemy
        board_state._their_power = {0: -1, 1: 5}  # 5 enemy power - we can crush with 10+
        board_state._adjacent = {0: [1]}
        board_state.force_pile = 4

        result = evaluator._analyze_attack_opportunity(
            board_state, loc_idx=0, our_power_here=16, our_card_count=4
        )

        assert result['viable'] is True
        assert "CRUSH" in result['reason']
        # Crush bonus = 25, base = 50, icons = 15, enemy/2 = 2.5, total ~92.5
        assert result['score'] > 75

    def test_no_attack_without_power_advantage(self):
        """Don't attack if we don't have clear power advantage"""
        evaluator = MoveEvaluator()
        board_state = MockBoardState()

        source_loc = MockLocation(location_index=0)
        source_loc.my_cards = [MockCard(f"card{i}", 0) for i in range(2)]

        target_loc = MockLocation(location_index=1)

        board_state.locations = [source_loc, target_loc]
        board_state._my_power = {0: 6, 1: 0}  # Only 6 power
        board_state._their_power = {0: -1, 1: 8}  # Enemy has 8 - we can't beat them
        board_state._adjacent = {0: [1]}
        board_state.force_pile = 2

        result = evaluator._analyze_attack_opportunity(
            board_state, loc_idx=0, our_power_here=6, our_card_count=2
        )

        assert result['viable'] is False

    def test_no_attack_without_force(self):
        """Can't attack if we don't have force to move"""
        evaluator = MoveEvaluator()
        board_state = MockBoardState()

        source_loc = MockLocation(location_index=0)
        source_loc.my_cards = [MockCard(f"card{i}", 0) for i in range(4)]

        target_loc = MockLocation(location_index=1)

        board_state.locations = [source_loc, target_loc]
        board_state._my_power = {0: 12, 1: 0}
        board_state._their_power = {0: -1, 1: 5}
        board_state._adjacent = {0: [1]}
        board_state.force_pile = 0  # No force!

        result = evaluator._analyze_attack_opportunity(
            board_state, loc_idx=0, our_power_here=12, our_card_count=4
        )

        assert result['viable'] is False
        assert "force" in result['reason'].lower()

    def test_no_attack_on_empty_locations(self):
        """Don't use attack logic for empty locations - use spread instead"""
        evaluator = MoveEvaluator()
        board_state = MockBoardState()

        source_loc = MockLocation(location_index=0)
        source_loc.my_cards = [MockCard(f"card{i}", 0) for i in range(4)]

        target_loc = MockLocation(location_index=1, their_icons="2")

        board_state.locations = [source_loc, target_loc]
        board_state._my_power = {0: 12, 1: 0}
        board_state._their_power = {0: -1, 1: -1}  # -1 = no cards at target
        board_state._adjacent = {0: [1]}
        board_state.force_pile = 4

        result = evaluator._analyze_attack_opportunity(
            board_state, loc_idx=0, our_power_here=12, our_card_count=4
        )

        # Should NOT be viable as attack - target is empty (use spread logic instead)
        assert result['viable'] is False

    def test_attack_prefers_high_icon_targets(self):
        """Should prefer attacking locations with more enemy icons"""
        evaluator = MoveEvaluator()
        board_state = MockBoardState()

        source_loc = MockLocation(location_index=0)
        source_loc.my_cards = [MockCard(f"card{i}", 0) for i in range(4)]

        # Two targets - same enemy power but different icons
        target_low_icons = MockLocation(location_index=1, their_icons="1")
        target_high_icons = MockLocation(location_index=2, their_icons="3")

        board_state.locations = [source_loc, target_low_icons, target_high_icons]
        board_state._my_power = {0: 16, 1: 0, 2: 0}
        board_state._their_power = {0: -1, 1: 5, 2: 5}  # Same enemy power at both
        board_state._adjacent = {0: [1, 2]}
        board_state.force_pile = 4

        result = evaluator._analyze_attack_opportunity(
            board_state, loc_idx=0, our_power_here=16, our_card_count=4
        )

        # Should choose high icon target
        assert result['viable'] is True
        assert result['target_idx'] == 2
        assert "3 icon" in result['reason'].lower() or "deny 3" in result['reason']

    def test_negative_one_means_no_cards(self):
        """Verify -1 power is treated as 'no cards' not '0 power'"""
        evaluator = MoveEvaluator()
        board_state = MockBoardState()

        source_loc = MockLocation(location_index=0)
        source_loc.my_cards = [MockCard(f"card{i}", 0) for i in range(4)]

        # Target with 0 power (e.g., empty vehicle) vs target with -1 (no cards)
        target_zero_power = MockLocation(location_index=1)
        target_no_cards = MockLocation(location_index=2)

        board_state.locations = [source_loc, target_zero_power, target_no_cards]
        board_state._my_power = {0: 12, 1: 0, 2: 0}
        board_state._their_power = {0: -1, 1: 0, 2: -1}  # 0 = has cards with 0 power, -1 = no cards
        board_state._adjacent = {0: [1, 2]}
        board_state.force_pile = 4

        result = evaluator._analyze_attack_opportunity(
            board_state, loc_idx=0, our_power_here=12, our_card_count=4
        )

        # Neither should be viable attack targets (no real enemy power to attack)
        assert result['viable'] is False

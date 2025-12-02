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

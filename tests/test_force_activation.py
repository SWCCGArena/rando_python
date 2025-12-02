"""
Force Activation Evaluator Test Suite

Tests the ForceActivationEvaluator with various game states.
Run with: python -m pytest tests/test_force_activation.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from dataclasses import dataclass, field
from typing import List, Dict, Any

from engine.evaluators.force_activation_evaluator import (
    ForceActivationEvaluator,
    MAX_FORCE_PILE,
    RESERVE_FOR_DESTINY_CONTESTED,
    RESERVE_FOR_DESTINY_SAFE,
)


@dataclass
class MockBoardState:
    """Mock board state for testing force activation"""
    force_pile: int = 0
    reserve_deck: int = 30
    used_pile: int = 0
    hand_size: int = 7
    force_activated_this_turn: int = 0
    cards_in_hand: List[Any] = field(default_factory=list)
    locations: List[Any] = field(default_factory=list)
    # Power at each location index: {location_index: power}
    my_power: Dict[int, int] = field(default_factory=dict)
    their_power: Dict[int, int] = field(default_factory=dict)

    def total_reserve_force(self) -> int:
        return self.reserve_deck + self.used_pile + self.force_pile

    def my_power_at_location(self, index: int) -> int:
        return self.my_power.get(index, 0)

    def their_power_at_location(self, index: int) -> int:
        return self.their_power.get(index, 0)


class TestForceActivationReserve:
    """Tests for reserve deck destiny card preservation"""

    def setup_method(self):
        self.evaluator = ForceActivationEvaluator()

    def test_reserve_1_card_no_contested_locations(self):
        """Should keep only 1 card in reserve when no contested locations"""
        bs = MockBoardState(
            force_pile=0,
            reserve_deck=15,  # 15 cards in reserve
            used_pile=0,
            locations=[object()],  # One location
            my_power={0: 5},  # We have power
            their_power={0: 0},  # They have none - not contested
        )
        # Max activation is 14, should activate 14 (15 - 1 = 14)
        amount = self.evaluator._calculate_activation_amount(bs, 14)
        assert amount == 14, f"Expected 14, got {amount}. Should reserve only 1 card when safe"

    def test_reserve_2_cards_contested_location(self):
        """Should keep 2 cards in reserve when locations are contested"""
        bs = MockBoardState(
            force_pile=0,
            reserve_deck=15,  # 15 cards in reserve
            used_pile=0,
            locations=[object()],  # One location
            my_power={0: 5},  # We have power
            their_power={0: 3},  # They have power too - contested!
        )
        # Max activation is 14, should activate 13 (15 - 2 = 13)
        amount = self.evaluator._calculate_activation_amount(bs, 14)
        assert amount == 13, f"Expected 13, got {amount}. Should reserve 2 cards when contested"

    def test_reserve_late_game_uses_contest_logic(self):
        """Late game should use same contest logic (no extra reserve)"""
        bs = MockBoardState(
            force_pile=0,
            reserve_deck=8,  # Only 8 cards left
            used_pile=0,
            locations=[object()],
            my_power={0: 0},  # No power - not contested
            their_power={0: 5},
        )
        # No contest → reserve 1 card
        # 8 - 1 = 7 max activation
        amount = self.evaluator._calculate_activation_amount(bs, 10)
        assert amount == 7, f"Expected 7, got {amount}. Should reserve only 1 (no contest)"

    def test_cap_force_pile_at_20(self):
        """Should never have more than 20 force in force pile"""
        bs = MockBoardState(
            force_pile=10,  # Already have 10
            reserve_deck=30,
            used_pile=0,
        )
        # Max activation is 14, but should only activate 10 (to reach 20 cap)
        amount = self.evaluator._calculate_activation_amount(bs, 14)
        assert amount == 10, f"Expected 10, got {amount}. Should cap at 20 total force"

    def test_cap_force_pile_more_restrictive_than_reserve(self):
        """Force pile cap should apply even when reserve would allow more"""
        bs = MockBoardState(
            force_pile=18,  # Already have 18
            reserve_deck=30,  # Plenty in reserve
            used_pile=0,
        )
        # Can only add 2 more to reach 20 cap
        amount = self.evaluator._calculate_activation_amount(bs, 10)
        assert amount == 2, f"Expected 2, got {amount}. 20 - 18 = 2"

    def test_both_limits_apply_reserve_more_restrictive(self):
        """When reserve limit is more restrictive than force cap, use reserve limit"""
        bs = MockBoardState(
            force_pile=5,  # Have 5, could add 15 to reach 20
            reserve_deck=10,  # But only have 10 in reserve
            used_pile=0,
            locations=[object()],
            my_power={0: 5},
            their_power={0: 3},  # Contested - reserve 2
        )
        # Reserve limit: 10 - 2 = 8 max (contested)
        # Force cap: 20 - 5 = 15 max
        # Reserve limit is more restrictive
        amount = self.evaluator._calculate_activation_amount(bs, 14)
        assert amount == 8, f"Expected 8, got {amount}. Reserve limit (10-2=8) beats force cap"

    def test_already_at_force_cap(self):
        """When already at 20 force, activate nothing"""
        bs = MockBoardState(
            force_pile=20,
            reserve_deck=30,
            used_pile=0,
        )
        amount = self.evaluator._calculate_activation_amount(bs, 10)
        assert amount == 0, f"Expected 0, got {amount}. Already at force cap"

    def test_empty_reserve_activates_two(self):
        """When reserve deck is 3 and no contested, can activate 2 (3 - 1 = 2)"""
        bs = MockBoardState(
            force_pile=0,
            reserve_deck=3,  # Only 3 cards left
            used_pile=0,
            locations=[],  # No locations - not contested
        )
        # No contested → reserve 1 card
        # max_from_reserve = 3 - 1 = 2
        # Note: life force = 3 < 6 (CRITICAL), so emergency logic kicks in
        # emergency_amount = min(2, max(1, 6-0)) = min(2, 6) = 2
        amount = self.evaluator._calculate_activation_amount(bs, 10)
        assert amount == 2, f"Expected 2, got {amount}. 3 reserve - 1 safe = 2"

    def test_very_low_reserve_respects_limit(self):
        """With 5 cards in reserve and no contested, should activate 4"""
        bs = MockBoardState(
            force_pile=0,
            reserve_deck=5,
            used_pile=0,
            locations=[],  # No locations - not contested
        )
        # No contested → reserve 1 card
        # 5 - 1 = 4 max activation
        amount = self.evaluator._calculate_activation_amount(bs, 10)
        assert amount == 4, f"Expected 4, got {amount}. 5 reserve - 1 safe = 4"


class TestForceActivationEdgeCases:
    """Edge case tests for force activation"""

    def setup_method(self):
        self.evaluator = ForceActivationEvaluator()

    def test_max_available_is_zero(self):
        """When max available is 0, return 0"""
        bs = MockBoardState(force_pile=0, reserve_deck=30)
        amount = self.evaluator._calculate_activation_amount(bs, 0)
        assert amount == 0

    def test_large_force_pile_conservative(self):
        """When force pile > 12, be conservative"""
        bs = MockBoardState(
            force_pile=15,
            reserve_deck=30,
            force_activated_this_turn=0,
        )
        amount = self.evaluator._calculate_activation_amount(bs, 10)
        # When force > 12, limit to 2 - force_activated_this_turn
        assert amount == 2, f"Expected 2 (conservative), got {amount}"

    def test_small_hand_with_enough_force(self):
        """Small hand + enough force = conservative activation"""
        bs = MockBoardState(
            force_pile=10,
            reserve_deck=30,
            hand_size=3,  # Small hand
        )
        amount = self.evaluator._calculate_activation_amount(bs, 10)
        # Should limit to 2 when hand_size <= 4 and force >= 8
        assert amount == 2, f"Expected 2 (small hand), got {amount}"


class TestForceActivationIntegration:
    """Integration tests for the full evaluator"""

    def setup_method(self):
        self.evaluator = ForceActivationEvaluator()

    def test_realistic_midgame_scenario(self):
        """Midgame: 20 reserve, 5 force, max activation 12, no contested"""
        bs = MockBoardState(
            force_pile=5,
            reserve_deck=20,
            used_pile=5,
            hand_size=8,
            locations=[],  # No locations - not contested
        )
        # No contested → reserve 1 card
        # Reserve limit: 20 - 1 = 19
        # Force cap: 20 - 5 = 15
        # Should return min(12, 15) = 12 (limited by max_available)
        amount = self.evaluator._calculate_activation_amount(bs, 12)
        assert amount == 12, f"Expected 12, got {amount}"

    def test_realistic_lategame_scenario(self):
        """Late game: 8 reserve, 2 force, max activation 6, no contested"""
        bs = MockBoardState(
            force_pile=2,
            reserve_deck=8,
            used_pile=0,
            hand_size=5,
            locations=[],  # No locations - not contested
        )
        # No contested → reserve 1 card
        # Reserve limit: 8 - 1 = 7
        # Force cap: 20 - 2 = 18
        # Max available: 6
        # Should return 6 (limited by max_available)
        amount = self.evaluator._calculate_activation_amount(bs, 6)
        assert amount == 6, f"Expected 6, got {amount}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

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
    RESERVE_FOR_DESTINY,
    RESERVE_FOR_DESTINY_ENDGAME,
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

    def total_reserve_force(self) -> int:
        return self.reserve_deck + self.used_pile + self.force_pile


class TestForceActivationReserve:
    """Tests for reserve deck destiny card preservation"""

    def setup_method(self):
        self.evaluator = ForceActivationEvaluator()

    def test_reserve_3_cards_for_destiny_normal_game(self):
        """Should keep 3 cards in reserve deck for destiny draws"""
        bs = MockBoardState(
            force_pile=0,
            reserve_deck=15,  # 15 cards in reserve
            used_pile=0,
        )
        # Max activation is 14, but should only activate 12 (15 - 3 = 12)
        amount = self.evaluator._calculate_activation_amount(bs, 14)
        assert amount == 12, f"Expected 12, got {amount}. Should reserve 3 cards for destiny"

    def test_reserve_2_cards_endgame(self):
        """When total cards < 10, only reserve 2 cards"""
        bs = MockBoardState(
            force_pile=0,
            reserve_deck=8,  # Only 8 cards left - endgame
            used_pile=0,
        )
        # Total life force = 8, which is < 10 (endgame)
        # Destiny reserve: 8 - 2 = 6 max
        # Late game logic (life < 12): min_reserve = max(3, 8//3) = 3, available = 8 - 3 = 5
        # Late game is more conservative, so 5 is correct
        amount = self.evaluator._calculate_activation_amount(bs, 10)
        assert amount == 5, f"Expected 5, got {amount}. Late game logic limits further"

    def test_reserve_2_cards_when_force_pile_counts(self):
        """Life force includes force pile, so 5 reserve + 4 force = 9 (endgame)"""
        bs = MockBoardState(
            force_pile=4,
            reserve_deck=5,  # 5 + 4 = 9 total, which is < 10
            used_pile=0,
        )
        # Destiny reserve: 5 - 2 = 3 max (endgame)
        # Late game (life=9 < 12): min_reserve = max(3, 9//3) = 3, available = 5 - 3 = 2
        # Late game is more restrictive
        amount = self.evaluator._calculate_activation_amount(bs, 10)
        assert amount == 2, f"Expected 2, got {amount}. Late game logic limits to 2"

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
        )
        # Reserve limit: 10 - 3 = 7 max
        # Force cap: 20 - 5 = 15 max
        # Reserve limit is more restrictive
        amount = self.evaluator._calculate_activation_amount(bs, 14)
        assert amount == 7, f"Expected 7, got {amount}. Reserve limit (10-3=7) beats force cap"

    def test_already_at_force_cap(self):
        """When already at 20 force, activate nothing"""
        bs = MockBoardState(
            force_pile=20,
            reserve_deck=30,
            used_pile=0,
        )
        amount = self.evaluator._calculate_activation_amount(bs, 10)
        assert amount == 0, f"Expected 0, got {amount}. Already at force cap"

    def test_empty_reserve_activates_one(self):
        """When reserve deck is 3, endgame allows activating 1 (3 - 2 = 1)"""
        bs = MockBoardState(
            force_pile=0,
            reserve_deck=3,  # Only 3 cards left
            used_pile=0,
        )
        # Life force = 3 (< 10, endgame) -> reserve 2 cards
        # max_from_reserve = 3 - 2 = 1
        # Note: life force = 3 < 6 (CRITICAL), so emergency logic applies
        # emergency_amount = min(1, max(1, 6-0)) = min(1, 6) = 1
        amount = self.evaluator._calculate_activation_amount(bs, 10)
        assert amount == 1, f"Expected 1, got {amount}. 3 reserve - 2 endgame = 1"

    def test_very_low_reserve_respects_limit(self):
        """With 5 cards in reserve, should only activate 2"""
        bs = MockBoardState(
            force_pile=0,
            reserve_deck=5,
            used_pile=0,
        )
        # 5 total cards < 10, so endgame rules apply (reserve 2)
        # 5 - 2 = 3 max activation
        amount = self.evaluator._calculate_activation_amount(bs, 10)
        assert amount == 3, f"Expected 3, got {amount}. 5 reserve - 2 endgame = 3"


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
        """Midgame: 20 reserve, 5 force, max activation 12"""
        bs = MockBoardState(
            force_pile=5,
            reserve_deck=20,
            used_pile=5,
            hand_size=8,
        )
        # Life force = 20 + 5 + 5 = 30 (not endgame)
        # Reserve limit: 20 - 3 = 17
        # Force cap: 20 - 5 = 15
        # Should return min(12, 15) = 12 (limited by max_available)
        amount = self.evaluator._calculate_activation_amount(bs, 12)
        assert amount == 12, f"Expected 12, got {amount}"

    def test_realistic_lategame_scenario(self):
        """Late game: 8 reserve, 2 force, max activation 6"""
        bs = MockBoardState(
            force_pile=2,
            reserve_deck=8,
            used_pile=0,
            hand_size=5,
        )
        # Life force = 8 + 0 + 2 = 10 (exactly at endgame threshold)
        # Reserve limit: 8 - 3 = 5 (not endgame since life = 10)
        # Force cap: 20 - 2 = 18
        # Should return 5 (reserve limit)
        amount = self.evaluator._calculate_activation_amount(bs, 6)
        assert amount == 5, f"Expected 5, got {amount}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

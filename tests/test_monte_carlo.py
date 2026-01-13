#!/usr/bin/env python3
"""
Tests for Monte Carlo simulation in deploy planning.

Tests:
- SimulationResult and ExpectedValue data structures
- MonteCarloSimulator initialization with config
- Location importance categorization
- 2-turn simulation outcomes
- Expected value calculation
- Histogram building
"""

import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.monte_carlo import (
    MonteCarloSimulator,
    SimulationResult,
    ExpectedValue,
    TrialOutcome,
)


# Mock classes for testing
@dataclass
class MockLocationAnalysis:
    card_id: str
    name: str
    my_power: int
    their_power: int
    my_icons: int
    their_icons: int


@dataclass
class MockDeployInstruction:
    target_location_id: Optional[str]
    power_contribution: int
    card_name: str
    card_blueprint_id: str


@dataclass
class MockDeploymentPlan:
    instructions: List[MockDeployInstruction]


@dataclass
class MockBoardState:
    my_force_available: int = 10


class TestDataStructures:
    """Tests for data classes."""

    def test_trial_outcome_creation(self):
        """Test TrialOutcome dataclass."""
        outcome = TrialOutcome(
            power_margin=5,
            we_control=True,
            barrier_killed=False,
            opponent_battled=False,
            turn_resolved=2
        )
        assert outcome.power_margin == 5
        assert outcome.we_control is True
        assert outcome.barrier_killed is False
        assert outcome.opponent_battled is False
        assert outcome.turn_resolved == 2

    def test_simulation_result_creation(self):
        """Test SimulationResult dataclass."""
        result = SimulationResult(
            win_rate=0.75,
            avg_power_margin=3.5,
            worst_case=-2,
            best_case=8,
            percentile_10_margin=0,
            barrier_losses=1,
            opponent_battled_count=3,
            histogram={-2: 1, 0: 2, 3: 5, 5: 3, 8: 1}
        )
        assert result.win_rate == 0.75
        assert result.avg_power_margin == 3.5
        assert result.worst_case == -2
        assert result.best_case == 8
        assert result.percentile_10_margin == 0
        assert result.barrier_losses == 1

    def test_expected_value_creation(self):
        """Test ExpectedValue dataclass."""
        ev = ExpectedValue(
            raw_score=100.0,
            win_rate=0.8,
            resilience=0.95,
            early_battle_factor=0.9,
            barrier_factor=0.98,
            final_score=66.86,
            histogram={2: 5, 4: 10, 6: 5}
        )
        assert ev.raw_score == 100.0
        assert ev.win_rate == 0.8
        assert ev.final_score == 66.86


class TestMonteCarloSimulatorInit:
    """Tests for simulator initialization."""

    def test_default_config(self):
        """Test simulator with no config uses defaults."""
        sim = MonteCarloSimulator()
        assert sim.n_simulations == 20
        assert sim.barrier_prob == 0.08
        assert sim.power_response['high'] == (4, 6)
        assert sim.power_response['medium'] == (3, 5)
        assert sim.power_response['low'] == (2, 4)

    def test_custom_config(self):
        """Test simulator with custom config."""
        config = {
            'n_simulations': 50,
            'barrier_probability': 0.1,
            'power_response_high': [5, 8],
            'power_response_medium': [3, 6],
            'power_response_low': [1, 3],
        }
        sim = MonteCarloSimulator(config)
        assert sim.n_simulations == 50
        assert sim.barrier_prob == 0.1
        assert sim.power_response['high'] == (5, 8)
        assert sim.power_response['medium'] == (3, 6)
        assert sim.power_response['low'] == (1, 3)

    def test_partial_config(self):
        """Test simulator with partial config uses defaults for missing."""
        config = {'n_simulations': 30}
        sim = MonteCarloSimulator(config)
        assert sim.n_simulations == 30
        assert sim.barrier_prob == 0.08  # default


class TestLocationImportance:
    """Tests for location importance categorization."""

    def test_high_importance(self):
        """2+ icons should be high importance."""
        sim = MonteCarloSimulator()
        assert sim._get_location_importance(2) == 'high'
        assert sim._get_location_importance(3) == 'high'
        assert sim._get_location_importance(5) == 'high'

    def test_medium_importance(self):
        """1 icon should be medium importance."""
        sim = MonteCarloSimulator()
        assert sim._get_location_importance(1) == 'medium'

    def test_low_importance(self):
        """0 icons should be low importance."""
        sim = MonteCarloSimulator()
        assert sim._get_location_importance(0) == 'low'
        assert sim._get_location_importance(-1) == 'low'  # Edge case


class TestReinforcement:
    """Tests for reinforcement calculation."""

    def test_calculate_reinforcement_basic(self):
        """Test basic reinforcement calculation."""
        sim = MonteCarloSimulator()
        hand = [
            {'blueprint_id': 'a', 'power': 5, 'deploy_cost': 3},
            {'blueprint_id': 'b', 'power': 3, 'deploy_cost': 2},
            {'blueprint_id': 'c', 'power': 2, 'deploy_cost': 1},
        ]
        # With 5 force: deploy 5-power (cost 3, force_left=2) + 3-power (cost 2, force_left=0) = 8
        reinforcement = sim._calculate_reinforcement(hand, force_available=5)
        assert reinforcement == 8

    def test_calculate_reinforcement_limited_force(self):
        """Test reinforcement limited by force."""
        sim = MonteCarloSimulator()
        hand = [
            {'blueprint_id': 'a', 'power': 8, 'deploy_cost': 6},
            {'blueprint_id': 'b', 'power': 4, 'deploy_cost': 4},
        ]
        # With 5 force: can't afford 8-power (cost 6), can afford 4-power (cost 4)
        reinforcement = sim._calculate_reinforcement(hand, force_available=5)
        assert reinforcement == 4

    def test_calculate_reinforcement_truly_limited(self):
        """Test reinforcement when truly can't afford anything."""
        sim = MonteCarloSimulator()
        hand = [
            {'blueprint_id': 'a', 'power': 8, 'deploy_cost': 6},
            {'blueprint_id': 'b', 'power': 4, 'deploy_cost': 4},
        ]
        # With 3 force, can't afford either
        reinforcement = sim._calculate_reinforcement(hand, force_available=3)
        assert reinforcement == 0

    def test_calculate_reinforcement_empty_hand(self):
        """Test reinforcement with empty hand."""
        sim = MonteCarloSimulator()
        reinforcement = sim._calculate_reinforcement([], force_available=10)
        assert reinforcement == 0


class TestBattleResolution:
    """Tests for battle resolution."""

    def test_we_win_battle(self):
        """Test battle when we have more power."""
        sim = MonteCarloSimulator()
        state = {'our_power': 8, 'their_power': 5}
        result = sim._resolve_battle(state)
        assert result['margin'] == 3
        assert result['we_control'] is True

    def test_we_lose_battle(self):
        """Test battle when they have more power."""
        sim = MonteCarloSimulator()
        state = {'our_power': 3, 'their_power': 7}
        result = sim._resolve_battle(state)
        assert result['margin'] == -4
        assert result['we_control'] is False

    def test_tie_battle(self):
        """Test battle with equal power (we don't control at tie)."""
        sim = MonteCarloSimulator()
        state = {'our_power': 5, 'their_power': 5}
        result = sim._resolve_battle(state)
        assert result['margin'] == 0
        assert result['we_control'] is False


class TestHistogram:
    """Tests for histogram building."""

    def test_build_histogram(self):
        """Test histogram is built correctly from outcomes."""
        sim = MonteCarloSimulator()
        outcomes = [
            TrialOutcome(power_margin=2, we_control=True, barrier_killed=False, opponent_battled=False, turn_resolved=2),
            TrialOutcome(power_margin=2, we_control=True, barrier_killed=False, opponent_battled=False, turn_resolved=2),
            TrialOutcome(power_margin=-1, we_control=False, barrier_killed=False, opponent_battled=True, turn_resolved=1),
            TrialOutcome(power_margin=4, we_control=True, barrier_killed=False, opponent_battled=False, turn_resolved=2),
        ]
        histogram = sim._build_histogram(outcomes)
        assert histogram == {2: 2, -1: 1, 4: 1}

    def test_format_histogram(self):
        """Test histogram formatting."""
        histogram = {-2: 1, 0: 2, 2: 3, 4: 1}
        formatted = MonteCarloSimulator.format_histogram(histogram)
        assert formatted == "-2:1 +0:2 +2:3 +4:1"


class TestExpectedValue:
    """Tests for expected value calculation."""

    def test_high_win_rate_good_resilience(self):
        """Test expected value with high win rate and good resilience."""
        sim = MonteCarloSimulator({'n_simulations': 20})
        sim_result = SimulationResult(
            win_rate=0.9,
            avg_power_margin=4.0,
            worst_case=2,
            best_case=8,
            percentile_10_margin=4,  # p10 >= 4 means resilience=1.0
            barrier_losses=0,
            opponent_battled_count=2,
            histogram={4: 10, 6: 5, 8: 5}
        )
        ev = sim.calculate_expected_value(100.0, sim_result)

        # win_rate=0.9, resilience=1.0 (p10>=4), early_battle=0.99, barrier=1.0
        # 100 * 0.9 * 1.0 * 0.99 * 1.0 = 89.1
        assert ev.raw_score == 100.0
        assert ev.win_rate == 0.9
        assert ev.resilience == 1.0
        assert abs(ev.early_battle_factor - 0.99) < 0.01  # 2/20 * 0.10 = 0.01
        assert ev.barrier_factor == 1.0
        assert abs(ev.final_score - 89.1) < 0.5

    def test_low_resilience_penalized(self):
        """Test that fragile plans get penalized."""
        sim = MonteCarloSimulator({'n_simulations': 20})
        sim_result = SimulationResult(
            win_rate=0.7,
            avg_power_margin=1.0,
            worst_case=-5,
            best_case=5,
            percentile_10_margin=-3,  # p10 < -2 means resilience=0.70
            barrier_losses=0,
            opponent_battled_count=0,
            histogram={-5: 1, -3: 2, 0: 4, 2: 8, 5: 5}
        )
        ev = sim.calculate_expected_value(100.0, sim_result)

        # resilience=0.70 because p10 < -2
        assert ev.resilience == 0.70

    def test_barrier_penalty(self):
        """Test that barrier losses reduce score."""
        sim = MonteCarloSimulator({'n_simulations': 20})
        sim_result = SimulationResult(
            win_rate=0.8,
            avg_power_margin=3.0,
            worst_case=2,
            best_case=6,
            percentile_10_margin=2,  # Good resilience
            barrier_losses=4,  # 4/20 = 20% barrier loss rate
            opponent_battled_count=0,
            histogram={2: 10, 4: 6, 6: 4}
        )
        ev = sim.calculate_expected_value(100.0, sim_result)

        # barrier_factor = 1.0 - (0.2 * 0.15) = 0.97
        assert abs(ev.barrier_factor - 0.97) < 0.01

    def test_floor_prevents_near_zero_scores(self):
        """Test that floor prevents scores from dropping below 40% of base."""
        sim = MonteCarloSimulator({'n_simulations': 20})
        sim_result = SimulationResult(
            win_rate=0.1,  # Very low win rate
            avg_power_margin=-5.0,
            worst_case=-10,
            best_case=-2,
            percentile_10_margin=-8,  # Very fragile
            barrier_losses=5,
            opponent_battled_count=18,
            histogram={-10: 5, -8: 5, -5: 5, -2: 5}
        )
        ev = sim.calculate_expected_value(100.0, sim_result)

        # Without floor: 100 * 0.1 * 0.70 * 0.82 * 0.925 ≈ 5.3
        # With floor: max(5.3, 40) = 40
        assert ev.final_score == 40.0  # Floor at 40% of base


class TestFullSimulation:
    """Integration tests for full simulation flow."""

    def test_simulate_plan_strong_position(self):
        """Test simulation with strong power advantage."""
        random.seed(42)  # For reproducibility

        sim = MonteCarloSimulator({'n_simulations': 10, 'barrier_probability': 0.0})

        # Create mock plan with strong position
        plan = MockDeploymentPlan(instructions=[
            MockDeployInstruction(
                target_location_id='loc1',
                power_contribution=10,
                card_name='Darth Vader',
                card_blueprint_id='1_168'
            )
        ])

        # Location with existing power
        locations = [
            MockLocationAnalysis(
                card_id='loc1',
                name='Hoth',
                my_power=5,
                their_power=2,
                my_icons=1,
                their_icons=1
            )
        ]

        board_state = MockBoardState(my_force_available=8)
        hand_cards = [
            {'blueprint_id': 'h1', 'power': 4, 'deploy_cost': 3},
            {'blueprint_id': 'h2', 'power': 3, 'deploy_cost': 2},
        ]

        result = sim.simulate_plan(plan, locations, hand_cards, board_state)

        # With 10 power deployed + 5 existing = 15 our power
        # Opponent has 2 + response (3-5 for medium importance)
        # We should win most simulations
        assert result.win_rate >= 0.5
        assert result.best_case > 0

    def test_simulate_plan_empty_plan(self):
        """Test simulation with empty plan (no location deploys)."""
        sim = MonteCarloSimulator({'n_simulations': 5})

        plan = MockDeploymentPlan(instructions=[])
        locations = []
        board_state = MockBoardState()
        hand_cards = []

        result = sim.simulate_plan(plan, locations, hand_cards, board_state)

        # Empty plan should return default result
        assert result.win_rate == 1.0  # We "control" by default
        assert result.worst_case == 0

    def test_simulate_plan_with_barrier(self):
        """Test simulation with high barrier probability."""
        random.seed(123)

        # High barrier probability to test barrier logic
        sim = MonteCarloSimulator({
            'n_simulations': 50,
            'barrier_probability': 0.5  # High for testing
        })

        plan = MockDeploymentPlan(instructions=[
            MockDeployInstruction(
                target_location_id='loc1',
                power_contribution=6,
                card_name='Test Character',
                card_blueprint_id='test_1'
            )
        ])

        locations = [
            MockLocationAnalysis(
                card_id='loc1',
                name='Test Location',
                my_power=0,
                their_power=0,
                my_icons=1,
                their_icons=0
            )
        ]

        board_state = MockBoardState()
        hand_cards = []

        result = sim.simulate_plan(plan, locations, hand_cards, board_state)

        # With high barrier prob, some barriers should trigger
        assert result.barrier_losses > 0


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_simulations(self):
        """Test with zero simulations configured."""
        sim = MonteCarloSimulator({'n_simulations': 0})
        sim_result = SimulationResult(
            win_rate=0.5,
            avg_power_margin=0,
            worst_case=0,
            best_case=0,
            percentile_10_margin=0,
            barrier_losses=0,
            opponent_battled_count=0,
            histogram={}
        )
        ev = sim.calculate_expected_value(100.0, sim_result)
        # Should handle divide by zero gracefully
        assert ev.early_battle_factor == 1.0
        assert ev.barrier_factor == 1.0

    def test_location_without_deploy(self):
        """Test plan targeting location not in analyses."""
        sim = MonteCarloSimulator({'n_simulations': 5})

        plan = MockDeploymentPlan(instructions=[
            MockDeployInstruction(
                target_location_id='nonexistent',
                power_contribution=5,
                card_name='Test',
                card_blueprint_id='test'
            )
        ])

        locations = [
            MockLocationAnalysis(
                card_id='other_loc',
                name='Other',
                my_power=0,
                their_power=0,
                my_icons=1,
                their_icons=0
            )
        ]

        board_state = MockBoardState()
        result = sim.simulate_plan(plan, locations, [], board_state)

        # Should handle missing location gracefully
        assert result.win_rate == 1.0  # No valid deploys = "we control"


def run_tests():
    """Run all tests and report results."""
    import traceback

    test_classes = [
        TestDataStructures,
        TestMonteCarloSimulatorInit,
        TestLocationImportance,
        TestReinforcement,
        TestBattleResolution,
        TestHistogram,
        TestExpectedValue,
        TestFullSimulation,
        TestEdgeCases,
    ]

    total = 0
    passed = 0
    failed = 0

    for test_class in test_classes:
        print(f"\n{'='*60}")
        print(f"Running {test_class.__name__}")
        print('='*60)

        instance = test_class()
        methods = [m for m in dir(instance) if m.startswith('test_')]

        for method_name in methods:
            total += 1
            try:
                getattr(instance, method_name)()
                print(f"  ✅ {method_name}")
                passed += 1
            except AssertionError as e:
                print(f"  ❌ {method_name}: {e}")
                failed += 1
            except Exception as e:
                print(f"  ❌ {method_name}: {type(e).__name__}: {e}")
                traceback.print_exc()
                failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed}/{total} passed, {failed} failed")
    print('='*60)

    return failed == 0


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)

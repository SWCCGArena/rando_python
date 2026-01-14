"""
Tests for Neural Deploy Planner.

Run with: pytest tests/test_neural_planner.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest
from dataclasses import dataclass, field
from typing import List, Dict, Any

from engine.neural_planner.state_encoder import StateEncoder, STATE_DIM, NUM_ACTIONS
from engine.neural_planner.action_decoder import ActionDecoder
from engine.deploy_planner import DeploymentPlan, DeployStrategy


# =============================================================================
# MOCK CLASSES (simplified from test_deploy_planner.py)
# =============================================================================

@dataclass
class MockCard:
    """Mock card in hand."""
    card_id: str
    blueprint_id: str
    card_title: str
    card_type: str = "Character"
    owner: str = "bot"
    zone: str = "HAND"
    power: int = 0
    deploy: int = 0
    ability: int = 0
    location_index: int = -1
    attached_cards: List = field(default_factory=list)


@dataclass
class MockLocation:
    """Mock location on the board."""
    card_id: str
    blueprint_id: str
    name: str
    system_name: str = ""
    site_name: str = ""
    is_site: bool = True
    is_space: bool = False
    is_ground: bool = True
    is_interior: bool = False
    is_exterior: bool = True
    location_index: int = 0
    my_cards: List = field(default_factory=list)
    their_cards: List = field(default_factory=list)
    my_icons: str = "1"
    their_icons: str = "1"


@dataclass
class MockBoardState:
    """Mock board state for testing."""
    my_player_name: str = "bot"
    opponent_name: str = "opponent"
    my_side: str = "dark"

    force_pile: int = 10
    used_pile: int = 0
    reserve_deck: int = 30
    hand_size: int = 8

    their_force_pile: int = 10
    their_used_pile: int = 0
    their_reserve_deck: int = 30
    their_hand_size: int = 8

    turn_number: int = 1
    current_phase: str = "Deploy"
    current_turn_player: str = "bot"

    locations: List = field(default_factory=list)
    cards_in_hand: List = field(default_factory=list)
    cards_in_play: Dict = field(default_factory=dict)

    dark_power_at_locations: Dict = field(default_factory=dict)
    light_power_at_locations: Dict = field(default_factory=dict)
    _dark_icons: Dict = field(default_factory=dict)
    _light_icons: Dict = field(default_factory=dict)

    dark_generation: int = 3
    light_generation: int = 3

    consecutive_hold_turns: int = 0
    hold_failed_last_turn: bool = False

    def is_my_turn(self) -> bool:
        return self.current_turn_player == self.my_player_name

    def my_power_at_location(self, index: int) -> int:
        if self.my_side == "dark":
            return self.dark_power_at_locations.get(index, 0)
        return self.light_power_at_locations.get(index, 0)

    def their_power_at_location(self, index: int) -> int:
        if self.my_side == "dark":
            return self.light_power_at_locations.get(index, 0)
        return self.dark_power_at_locations.get(index, 0)

    def my_icons_at_location(self, index: int) -> int:
        if self.my_side == "dark":
            return self._dark_icons.get(index, 0)
        return self._light_icons.get(index, 0)

    def their_icons_at_location(self, index: int) -> int:
        if self.my_side == "dark":
            return self._light_icons.get(index, 0)
        return self._dark_icons.get(index, 0)

    def total_my_power(self) -> int:
        if self.my_side == "dark":
            return sum(self.dark_power_at_locations.values())
        return sum(self.light_power_at_locations.values())

    def total_their_power(self) -> int:
        if self.my_side == "dark":
            return sum(self.light_power_at_locations.values())
        return sum(self.dark_power_at_locations.values())


# =============================================================================
# TESTS
# =============================================================================

class TestStateEncoder:
    """Tests for StateEncoder."""

    def test_encode_produces_correct_shape(self):
        """State encoder produces [512] tensor."""
        encoder = StateEncoder()
        board_state = MockBoardState()

        state = encoder.encode(board_state)

        assert state.shape == (STATE_DIM,)
        assert state.dtype == np.float32

    def test_encode_with_locations(self):
        """State encoder handles locations correctly."""
        encoder = StateEncoder()
        board_state = MockBoardState()

        # Add a location
        loc = MockLocation(
            card_id="loc1",
            blueprint_id="1_123",
            name="Mos Eisley",
            site_name="Tatooine: Mos Eisley",
            is_ground=True,
            is_space=False,
            location_index=0,
        )
        board_state.locations = [loc]
        board_state.dark_power_at_locations = {0: 5}
        board_state.light_power_at_locations = {0: 3}

        state = encoder.encode(board_state)

        assert state.shape == (STATE_DIM,)
        # Check that location features are populated (offset 64)
        assert state[64] == 1.0  # Location exists

    def test_encode_with_hand(self):
        """State encoder handles hand cards correctly."""
        encoder = StateEncoder()
        board_state = MockBoardState()

        # Add cards to hand
        cards = [
            MockCard(
                card_id="c1",
                blueprint_id="1_1",
                card_title="Stormtrooper",
                card_type="Character",
                power=4,
                deploy=2,
            ),
            MockCard(
                card_id="c2",
                blueprint_id="1_2",
                card_title="TIE Fighter",
                card_type="Starship",
                power=3,
                deploy=3,
            ),
        ]
        board_state.cards_in_hand = cards

        state = encoder.encode(board_state)

        assert state.shape == (STATE_DIM,)
        # Hand features start at offset 448
        # First feature is total_ground_power (normalized)
        assert state[448] > 0  # Should have some ground power

    def test_action_mask_hold_always_valid(self):
        """HOLD_BACK action is always valid."""
        encoder = StateEncoder()
        board_state = MockBoardState()

        mask = encoder.get_action_mask(board_state)

        assert mask.shape == (NUM_ACTIONS,)
        assert mask[0] == True  # HOLD_BACK

    def test_action_mask_with_deployable_cards(self):
        """Action mask enables location actions when cards can deploy."""
        encoder = StateEncoder()
        board_state = MockBoardState()

        # Add a ground location
        loc = MockLocation(
            card_id="loc1",
            blueprint_id="1_123",
            name="Cantina",
            is_ground=True,
            location_index=0,
        )
        board_state.locations = [loc]

        # Add a character that can deploy
        card = MockCard(
            card_id="c1",
            blueprint_id="1_1",
            card_title="Stormtrooper",
            card_type="Character",
            power=4,
            deploy=2,  # Can afford with 10 force
        )
        board_state.cards_in_hand = [card]

        mask = encoder.get_action_mask(board_state)

        assert mask[0] == True  # HOLD_BACK
        assert mask[1] == True  # DEPLOY_TO_LOC_0
        assert mask[18] == True  # ESTABLISH_GROUND


class TestActionDecoder:
    """Tests for ActionDecoder."""

    def test_hold_back_action(self):
        """Decoding HOLD_BACK returns correct plan."""
        decoder = ActionDecoder()
        board_state = MockBoardState()

        plan = decoder.decode(action=0, board_state=board_state, confidence=0.9)

        assert isinstance(plan, DeploymentPlan)
        assert plan.strategy == DeployStrategy.HOLD_BACK
        assert len(plan.instructions) == 0

    def test_deploy_to_location(self):
        """Decoding DEPLOY_TO_LOC creates plan with instructions."""
        decoder = ActionDecoder()
        board_state = MockBoardState()

        # Add location
        loc = MockLocation(
            card_id="loc1",
            blueprint_id="1_123",
            name="Cantina",
            site_name="Mos Eisley Cantina",
            is_ground=True,
            location_index=0,
        )
        board_state.locations = [loc]

        # Add deployable character
        card = MockCard(
            card_id="c1",
            blueprint_id="1_1",
            card_title="Stormtrooper",
            card_type="Character",
            power=4,
            deploy=2,
        )
        board_state.cards_in_hand = [card]

        plan = decoder.decode(action=1, board_state=board_state, confidence=0.8)

        assert isinstance(plan, DeploymentPlan)
        assert plan.strategy in [DeployStrategy.ESTABLISH, DeployStrategy.REINFORCE]
        assert len(plan.instructions) >= 1


class TestNeuralPlannerIntegration:
    """Integration tests for NeuralDeployPlanner."""

    def test_create_plan_without_model(self):
        """Planner works without trained model (fallback)."""
        from engine.neural_planner import NeuralDeployPlanner

        # Use non-existent model path
        planner = NeuralDeployPlanner(
            model_path='nonexistent.pt',
            fallback_planner=None,
        )

        board_state = MockBoardState()
        plan = planner.create_plan(board_state)

        assert isinstance(plan, DeploymentPlan)
        # Without model or fallback, should hold back
        assert plan.strategy == DeployStrategy.HOLD_BACK

    def test_planner_interface_matches_rules_based(self):
        """NeuralDeployPlanner has same interface as DeployPhasePlanner."""
        from engine.neural_planner import NeuralDeployPlanner

        planner = NeuralDeployPlanner(model_path='nonexistent.pt')

        # Check interface methods exist
        assert hasattr(planner, 'create_plan')
        assert hasattr(planner, 'get_card_score')
        assert hasattr(planner, 'record_deployment')
        assert hasattr(planner, 'should_hold_back')
        assert hasattr(planner, 'get_plan_summary')
        assert hasattr(planner, 'reset')


class TestNetworkArchitecture:
    """Tests for neural network architecture."""

    @pytest.mark.skipif(
        not os.environ.get('TEST_TORCH'),
        reason="PyTorch tests disabled (set TEST_TORCH=1 to enable)"
    )
    def test_network_forward_pass(self):
        """Network forward pass produces correct output shapes."""
        import torch
        from engine.neural_planner.network import DeployPolicyNetwork

        network = DeployPolicyNetwork()

        # Test input
        state = torch.randn(4, STATE_DIM)
        mask = torch.ones(4, NUM_ACTIONS, dtype=torch.bool)

        logits, value = network(state, mask)

        assert logits.shape == (4, NUM_ACTIONS)
        assert value.shape == (4, 1)

    @pytest.mark.skipif(
        not os.environ.get('TEST_TORCH'),
        reason="PyTorch tests disabled (set TEST_TORCH=1 to enable)"
    )
    def test_network_parameter_count(self):
        """Network has expected parameter count (small for CPU inference)."""
        from engine.neural_planner.network import DeployPolicyNetwork, count_parameters

        network = DeployPolicyNetwork()
        params = count_parameters(network)

        # Should be under 1M parameters for efficient CPU inference
        # Current size: ~600K params = ~2.4MB
        assert params < 1_000_000
        print(f"Network has {params:,} parameters (~{params * 4 / 1024 / 1024:.1f}MB)")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

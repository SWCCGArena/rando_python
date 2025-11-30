"""
Extra Actions Test Suite

Tests the "extra actions" feature where the bot can take additional
non-planned actions after the deployment plan is complete, as long as
there's force remaining above the reserved amount.

Run with: python -m pytest tests/test_extra_actions.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from dataclasses import dataclass, field
from typing import List, Set, Optional

from engine.deploy_planner import (
    DeploymentPlan,
    DeploymentInstruction,
    DeployStrategy,
)


class TestDeploymentPlanExtraActions:
    """Tests for DeploymentPlan extra actions functionality"""

    def test_plan_not_complete_when_instructions_remain(self):
        """Plan is not complete while there are still instructions"""
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[
                DeploymentInstruction(
                    card_blueprint_id="test_card",
                    card_name="Test Card",
                    target_location_id="loc1",
                    target_location_name="Location 1",
                    priority=2,
                    reason="Test deploy",
                    deploy_cost=3,
                )
            ],
            deployments_made=0,
        )
        assert not plan.is_plan_complete()
        assert not plan.allows_extra_actions(10)

    def test_plan_not_complete_when_no_deployments_made(self):
        """Plan with empty instructions but no deployments is not complete"""
        plan = DeploymentPlan(
            strategy=DeployStrategy.HOLD_BACK,
            reason="Test",
            instructions=[],  # Empty
            deployments_made=0,  # No deployments made
        )
        # This is HOLD_BACK, not a "complete" plan
        assert not plan.is_plan_complete()

    def test_plan_complete_when_all_deployed(self):
        """Plan is complete when all instructions are done and deployments made"""
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[],  # All instructions cleared
            deployments_made=3,  # Made 3 deployments
            force_reserved_for_battle=2,
        )
        assert plan.is_plan_complete()

    def test_extra_force_budget_when_plan_complete(self):
        """Calculate extra force budget after plan completion"""
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[],
            deployments_made=2,
            force_reserved_for_battle=2,
        )

        # Current force = 5, reserved = 2, extra = 3
        assert plan.get_extra_force_budget(5) == 3

        # Current force = 2, reserved = 2, extra = 0
        assert plan.get_extra_force_budget(2) == 0

        # Current force = 1, reserved = 2, extra = 0 (never negative)
        assert plan.get_extra_force_budget(1) == 0

    def test_extra_force_budget_zero_when_plan_not_complete(self):
        """No extra actions allowed if plan not complete"""
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[
                DeploymentInstruction(
                    card_blueprint_id="test_card",
                    card_name="Test Card",
                    target_location_id="loc1",
                    target_location_name="Location 1",
                    priority=2,
                    reason="Test deploy",
                    deploy_cost=3,
                )
            ],
            deployments_made=1,
            force_reserved_for_battle=2,
        )
        # Even with 10 force, no extra actions while plan has instructions
        assert plan.get_extra_force_budget(10) == 0

    def test_allows_extra_actions_true(self):
        """allows_extra_actions returns True when conditions met"""
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[],
            deployments_made=1,
            force_reserved_for_battle=2,
        )
        assert plan.allows_extra_actions(5)  # 5 > 2 reserved
        assert plan.allows_extra_actions(3)  # 3 > 2 reserved

    def test_allows_extra_actions_false_at_reserve(self):
        """allows_extra_actions returns False when at reserve limit"""
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[],
            deployments_made=1,
            force_reserved_for_battle=2,
        )
        assert not plan.allows_extra_actions(2)  # At limit
        assert not plan.allows_extra_actions(1)  # Below limit

    def test_scenario_full_deployment_cycle(self):
        """Simulate a full deployment cycle with extra actions"""
        # Initial plan: 6 force for 2 cards, reserve 2 for battle
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Establish at Mos Eisley",
            total_force_available=10,
            force_reserved_for_battle=2,
            original_plan_cost=6,
            instructions=[
                DeploymentInstruction(
                    card_blueprint_id="card_a",
                    card_name="Card A",
                    target_location_id="loc1",
                    target_location_name="Mos Eisley",
                    priority=2,
                    reason="Establish",
                    deploy_cost=3,
                ),
                DeploymentInstruction(
                    card_blueprint_id="card_b",
                    card_name="Card B",
                    target_location_id="loc1",
                    target_location_name="Mos Eisley",
                    priority=2,
                    reason="Establish",
                    deploy_cost=3,
                ),
            ],
        )

        # Step 1: Deploy Card A
        assert not plan.is_plan_complete()
        plan.instructions = [i for i in plan.instructions if i.card_blueprint_id != "card_a"]
        plan.deployments_made += 1
        # Now have 1 instruction left
        assert not plan.is_plan_complete()

        # Step 2: Deploy Card B
        plan.instructions = [i for i in plan.instructions if i.card_blueprint_id != "card_b"]
        plan.deployments_made += 1
        # Plan complete!
        assert plan.is_plan_complete()

        # Now at 10 - 6 = 4 force remaining
        current_force = 4

        # Reserved 2, so extra budget = 4 - 2 = 2
        assert plan.get_extra_force_budget(current_force) == 2
        assert plan.allows_extra_actions(current_force)

        # Take an extra action costing 1 force
        current_force -= 1  # Now 3
        assert plan.get_extra_force_budget(current_force) == 1
        assert plan.allows_extra_actions(current_force)

        # Take another extra action costing 1 force
        current_force -= 1  # Now 2 (at reserve limit)
        assert plan.get_extra_force_budget(current_force) == 0
        assert not plan.allows_extra_actions(current_force)


class TestExtraActionTypeRestrictions:
    """
    Tests that extra actions are restricted to certain card types.

    Characters, vehicles, and starships should NOT be allowed as extras.
    All other card types (effects, interrupts, devices, weapons, etc.) are allowed.
    """

    def test_extra_action_allows_effect(self):
        """Effects should be allowed as extra actions"""
        from unittest.mock import MagicMock, patch
        from engine.deploy_planner import DeployPhasePlanner, DeploymentPlan, DeployStrategy

        planner = DeployPhasePlanner()

        # Set up a stale plan that allows extras
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[],
            deployments_made=1,
        )
        plan.force_allow_extras = True
        planner.current_plan = plan

        # Mock an effect card (not character/vehicle/starship)
        mock_effect = MagicMock()
        mock_effect.is_character = False
        mock_effect.is_vehicle = False
        mock_effect.is_starship = False
        mock_effect.card_type = "Effect"

        with patch('engine.card_loader.get_card', return_value=mock_effect):
            score, reason = planner.get_card_score("effect_123", current_force=5)

        assert score > 0, f"Effect should have positive score, got {score}: {reason}"
        assert "EXTRA ACTION" in reason

    def test_extra_action_allows_interrupt(self):
        """Interrupts should be allowed as extra actions"""
        from unittest.mock import MagicMock, patch
        from engine.deploy_planner import DeployPhasePlanner, DeploymentPlan, DeployStrategy

        planner = DeployPhasePlanner()
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[],
            deployments_made=1,
        )
        plan.force_allow_extras = True
        planner.current_plan = plan

        mock_interrupt = MagicMock()
        mock_interrupt.is_character = False
        mock_interrupt.is_vehicle = False
        mock_interrupt.is_starship = False
        mock_interrupt.card_type = "Interrupt"

        with patch('engine.card_loader.get_card', return_value=mock_interrupt):
            score, reason = planner.get_card_score("interrupt_123", current_force=5)

        assert score > 0, f"Interrupt should have positive score, got {score}: {reason}"

    def test_extra_action_allows_device(self):
        """Devices should be allowed as extra actions"""
        from unittest.mock import MagicMock, patch
        from engine.deploy_planner import DeployPhasePlanner, DeploymentPlan, DeployStrategy

        planner = DeployPhasePlanner()
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[],
            deployments_made=1,
        )
        plan.force_allow_extras = True
        planner.current_plan = plan

        mock_device = MagicMock()
        mock_device.is_character = False
        mock_device.is_vehicle = False
        mock_device.is_starship = False
        mock_device.card_type = "Device"

        with patch('engine.card_loader.get_card', return_value=mock_device):
            score, reason = planner.get_card_score("device_123", current_force=5)

        assert score > 0, f"Device should have positive score, got {score}: {reason}"

    def test_extra_action_allows_weapon(self):
        """Weapons should be allowed as extra actions"""
        from unittest.mock import MagicMock, patch
        from engine.deploy_planner import DeployPhasePlanner, DeploymentPlan, DeployStrategy

        planner = DeployPhasePlanner()
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[],
            deployments_made=1,
        )
        plan.force_allow_extras = True
        planner.current_plan = plan

        mock_weapon = MagicMock()
        mock_weapon.is_character = False
        mock_weapon.is_vehicle = False
        mock_weapon.is_starship = False
        mock_weapon.card_type = "Weapon"

        with patch('engine.card_loader.get_card', return_value=mock_weapon):
            score, reason = planner.get_card_score("weapon_123", current_force=5)

        assert score > 0, f"Weapon should have positive score, got {score}: {reason}"

    def test_extra_action_rejects_character(self):
        """Characters should NOT be allowed as extra actions"""
        from unittest.mock import MagicMock, patch
        from engine.deploy_planner import DeployPhasePlanner, DeploymentPlan, DeployStrategy

        planner = DeployPhasePlanner()
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[],
            deployments_made=1,
        )
        plan.force_allow_extras = True
        planner.current_plan = plan

        mock_character = MagicMock()
        mock_character.is_character = True
        mock_character.is_vehicle = False
        mock_character.is_starship = False
        mock_character.card_type = "Character"

        with patch('engine.card_loader.get_card', return_value=mock_character):
            score, reason = planner.get_card_score("character_123", current_force=5)

        assert score < 0, f"Character should have negative score, got {score}: {reason}"
        assert "not allowed as extra action" in reason

    def test_extra_action_rejects_starship(self):
        """Starships should NOT be allowed as extra actions"""
        from unittest.mock import MagicMock, patch
        from engine.deploy_planner import DeployPhasePlanner, DeploymentPlan, DeployStrategy

        planner = DeployPhasePlanner()
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[],
            deployments_made=1,
        )
        plan.force_allow_extras = True
        planner.current_plan = plan

        mock_starship = MagicMock()
        mock_starship.is_character = False
        mock_starship.is_vehicle = False
        mock_starship.is_starship = True
        mock_starship.card_type = "Starship"

        with patch('engine.card_loader.get_card', return_value=mock_starship):
            score, reason = planner.get_card_score("starship_123", current_force=5)

        assert score < 0, f"Starship should have negative score, got {score}: {reason}"
        assert "not allowed as extra action" in reason

    def test_extra_action_rejects_vehicle(self):
        """Vehicles should NOT be allowed as extra actions"""
        from unittest.mock import MagicMock, patch
        from engine.deploy_planner import DeployPhasePlanner, DeploymentPlan, DeployStrategy

        planner = DeployPhasePlanner()
        plan = DeploymentPlan(
            strategy=DeployStrategy.ESTABLISH,
            reason="Test",
            instructions=[],
            deployments_made=1,
        )
        plan.force_allow_extras = True
        planner.current_plan = plan

        mock_vehicle = MagicMock()
        mock_vehicle.is_character = False
        mock_vehicle.is_vehicle = True
        mock_vehicle.is_starship = False
        mock_vehicle.card_type = "Vehicle"

        with patch('engine.card_loader.get_card', return_value=mock_vehicle):
            score, reason = planner.get_card_score("vehicle_123", current_force=5)

        assert score < 0, f"Vehicle should have negative score, got {score}: {reason}"
        assert "not allowed as extra action" in reason


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

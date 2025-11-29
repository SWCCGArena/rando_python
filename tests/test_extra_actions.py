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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

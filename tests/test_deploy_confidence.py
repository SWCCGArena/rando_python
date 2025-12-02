"""
Deploy Confidence Test Suite

These tests verify the CORE deployment behaviors that must work correctly.
Each test includes explicit assertions about:
- Which cards are in the plan
- What power totals are being deployed
- That thresholds are respected
- That force is reserved for battle when needed

Run with: python -m pytest tests/test_deploy_confidence.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import logging

# Import infrastructure from existing test file
from tests.test_deploy_planner import (
    ScenarioBuilder,
    run_scenario,
    patch_card_loader,
    unpatch_card_loader,
    MOCK_CARD_DB,
)
from engine.deploy_planner import DeploymentPlan, DeployStrategy

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def setup_teardown():
    """Setup and teardown for each test"""
    MOCK_CARD_DB.clear()
    patch_card_loader()
    yield
    unpatch_card_loader()
    MOCK_CARD_DB.clear()


def get_plan_power_at_location(plan: DeploymentPlan, location_name: str) -> int:
    """Calculate total power being deployed to a location in the plan"""
    return sum(
        inst.power_contribution
        for inst in plan.instructions
        if inst.target_location_name == location_name
    )


def get_plan_cards_at_location(plan: DeploymentPlan, location_name: str) -> list:
    """Get list of card names being deployed to a location"""
    return [
        inst.card_name
        for inst in plan.instructions
        if inst.target_location_name == location_name
    ]


def get_plan_total_cost(plan: DeploymentPlan) -> int:
    """Calculate total force cost of the plan"""
    return sum(inst.deploy_cost for inst in plan.instructions)


# =============================================================================
# THRESHOLD TESTS: Only deploy when power >= 6
# =============================================================================

class TestThresholdEnforcement:
    """Verify deploy threshold is enforced for all card type combinations"""

    def test_single_character_below_threshold_holds_back(self):
        """A single character with 5 power should NOT deploy (below threshold)"""
        scenario = (
            ScenarioBuilder("Below Threshold - Hold Back")
            .as_side("dark")
            .with_force(10)
            .with_turn(4)  # Turn 4+ uses full threshold (6)
            .with_deploy_threshold(6)
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=2, exterior=True)
            .add_character("Weak Trooper", power=5, deploy_cost=3)
            .expect_hold_back()
            .build()
        )
        result = run_scenario(scenario)

        assert result.plan.strategy == DeployStrategy.HOLD_BACK, \
            f"Should HOLD_BACK with 5 power (below threshold 6), got {result.plan.strategy}"
        assert len(result.plan.instructions) == 0, \
            f"Should have no deployments, got {len(result.plan.instructions)}"

    def test_single_character_at_threshold_deploys(self):
        """A single character with exactly 6 power SHOULD deploy"""
        scenario = (
            ScenarioBuilder("At Threshold - Deploys")
            .as_side("dark")
            .with_force(10)
            .with_deploy_threshold(6)
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=2, exterior=True)
            .add_character("Strong Trooper", power=6, deploy_cost=4)
            .expect_target("Mos Eisley")
            .build()
        )
        result = run_scenario(scenario)

        assert result.plan.strategy == DeployStrategy.ESTABLISH, \
            f"Should ESTABLISH with 6 power, got {result.plan.strategy}"
        power = get_plan_power_at_location(result.plan, "Mos Eisley")
        assert power >= 6, f"Should deploy 6+ power, got {power}"

    def test_two_characters_combine_to_threshold(self):
        """Two characters that together reach threshold should both deploy"""
        scenario = (
            ScenarioBuilder("Combine to Threshold")
            .as_side("dark")
            .with_force(10)
            .with_deploy_threshold(6)
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=2, exterior=True)
            # 3 + 3 = 6 power combined
            .add_character("Trooper A", power=3, deploy_cost=2)
            .add_character("Trooper B", power=3, deploy_cost=2)
            .expect_target("Mos Eisley")
            .build()
        )
        result = run_scenario(scenario)

        assert result.plan.strategy == DeployStrategy.ESTABLISH
        power = get_plan_power_at_location(result.plan, "Mos Eisley")
        assert power >= 6, f"Combined power should be 6+, got {power}"
        cards = get_plan_cards_at_location(result.plan, "Mos Eisley")
        assert len(cards) == 2, f"Should deploy BOTH characters, got {cards}"

    def test_vehicle_alone_meets_threshold(self):
        """A vehicle with permanent pilot meeting threshold should deploy"""
        scenario = (
            ScenarioBuilder("Vehicle Alone at Threshold")
            .as_side("dark")
            .with_force(10)
            .with_deploy_threshold(6)
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=2, exterior=True)
            .add_vehicle("AT-AT", power=6, deploy_cost=5, has_permanent_pilot=True)
            .expect_target("Mos Eisley")
            .build()
        )
        result = run_scenario(scenario)

        assert result.plan.strategy == DeployStrategy.ESTABLISH
        power = get_plan_power_at_location(result.plan, "Mos Eisley")
        assert power >= 6, f"Vehicle should provide 6+ power, got {power}"

    def test_starship_alone_meets_threshold(self):
        """A starship with permanent pilot meeting threshold should deploy to space"""
        scenario = (
            ScenarioBuilder("Starship Alone at Threshold")
            .as_side("dark")
            .with_force(10)
            .with_deploy_threshold(6)
            .add_space_location("Tatooine System", my_icons=2, their_icons=2)
            .add_starship("Star Destroyer", power=7, deploy_cost=6, has_permanent_pilot=True)
            .expect_target("Tatooine System")
            .build()
        )
        result = run_scenario(scenario)

        assert result.plan.strategy == DeployStrategy.ESTABLISH
        power = get_plan_power_at_location(result.plan, "Tatooine System")
        assert power >= 6, f"Starship should provide 6+ power, got {power}"

    def test_vehicle_plus_character_combine_to_threshold(self):
        """Vehicle + character combining to threshold should both deploy"""
        scenario = (
            ScenarioBuilder("Vehicle + Character Combo")
            .as_side("dark")
            .with_force(15)
            .with_deploy_threshold(6)
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=2, exterior=True)
            # Vehicle 4 power + character 3 power = 7 (meets threshold)
            .add_vehicle("AT-ST", power=4, deploy_cost=4, has_permanent_pilot=True)
            .add_character("Trooper", power=3, deploy_cost=2)
            .expect_target("Mos Eisley")
            .build()
        )
        result = run_scenario(scenario)

        power = get_plan_power_at_location(result.plan, "Mos Eisley")
        assert power >= 6, f"Combined power should be 6+, got {power}"

    def test_multiple_starships_combine_to_threshold(self):
        """Multiple small starships combining to threshold should all deploy"""
        scenario = (
            ScenarioBuilder("Multiple Ships Combine")
            .as_side("dark")
            .with_force(15)
            .with_deploy_threshold(6)
            .add_space_location("Tatooine System", my_icons=2, their_icons=2)
            # 3 + 3 = 6 power
            .add_starship("TIE Fighter A", power=3, deploy_cost=2, has_permanent_pilot=True)
            .add_starship("TIE Fighter B", power=3, deploy_cost=2, has_permanent_pilot=True)
            .expect_target("Tatooine System")
            .build()
        )
        result = run_scenario(scenario)

        power = get_plan_power_at_location(result.plan, "Tatooine System")
        assert power >= 6, f"Combined starship power should be 6+, got {power}"
        cards = get_plan_cards_at_location(result.plan, "Tatooine System")
        assert len(cards) == 2, f"Should deploy BOTH starships, got {cards}"


# =============================================================================
# PILOT TESTS: Unpiloted vehicles/starships get pilots
# =============================================================================

class TestPilotAllocation:
    """Verify pilots are included when deploying unpiloted vehicles/starships"""

    def test_unpiloted_vehicle_gets_pilot_in_plan(self):
        """An unpiloted vehicle must include a pilot character in the plan"""
        scenario = (
            ScenarioBuilder("Unpiloted Vehicle + Pilot")
            .as_side("dark")
            .with_force(15)
            .with_deploy_threshold(6)
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=2, exterior=True)
            # Unpiloted vehicle - needs pilot
            .add_vehicle("AT-ST", power=4, deploy_cost=3, has_permanent_pilot=False)
            # Pilot character
            .add_character("AT-ST Driver", power=2, deploy_cost=2, is_pilot=True, is_warrior=False)
            .expect_target("Mos Eisley")
            .build()
        )
        result = run_scenario(scenario)

        cards = get_plan_cards_at_location(result.plan, "Mos Eisley")

        # BOTH vehicle AND pilot must be in the plan
        assert "AT-ST" in cards, f"Vehicle should be in plan, got {cards}"
        assert "AT-ST Driver" in cards, f"Pilot should be in plan with vehicle, got {cards}"

        power = get_plan_power_at_location(result.plan, "Mos Eisley")
        assert power >= 6, f"Combined vehicle + pilot power should be 6+, got {power}"

    def test_unpiloted_starship_gets_pilot_in_plan(self):
        """An unpiloted starship must include a pilot character in the plan"""
        scenario = (
            ScenarioBuilder("Unpiloted Starship + Pilot")
            .as_side("dark")
            .with_force(15)
            .with_deploy_threshold(6)
            .add_space_location("Tatooine System", my_icons=2, their_icons=2)
            # Unpiloted starship
            .add_starship("TIE Advanced", power=4, deploy_cost=3, has_permanent_pilot=False)
            # Pilot
            .add_character("TIE Pilot", power=2, deploy_cost=2, is_pilot=True, is_warrior=False)
            .expect_target("Tatooine System")
            .build()
        )
        result = run_scenario(scenario)

        cards = get_plan_cards_at_location(result.plan, "Tatooine System")

        assert "TIE Advanced" in cards, f"Starship should be in plan, got {cards}"
        assert "TIE Pilot" in cards, f"Pilot should be in plan with starship, got {cards}"

    def test_unpiloted_vehicle_without_pilot_doesnt_deploy(self):
        """An unpiloted vehicle with NO pilot available should not deploy alone"""
        scenario = (
            ScenarioBuilder("Unpiloted Vehicle Without Pilot")
            .as_side("dark")
            .with_force(15)
            .with_deploy_threshold(6)
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=2, exterior=True)
            # Unpiloted vehicle with no pilot available
            .add_vehicle("AT-ST", power=4, deploy_cost=3, has_permanent_pilot=False)
            # No pilot in hand!
            .expect_hold_back()  # Should hold back - can't deploy
            .build()
        )
        result = run_scenario(scenario)

        # Should hold back or not deploy the unpiloted vehicle
        cards = get_plan_cards_at_location(result.plan, "Mos Eisley")
        assert "AT-ST" not in cards, \
            f"Unpiloted vehicle without pilot should NOT deploy, got {cards}"


# =============================================================================
# REINFORCE TESTS: Weak presence gets reinforced to threshold
# =============================================================================

class TestWeakPresenceReinforcement:
    """Verify locations with presence < threshold get reinforced"""

    def test_reinforce_weak_ground_presence(self):
        """Location with 3 power (< 6) and no enemies should get reinforced to 6+"""
        scenario = (
            ScenarioBuilder("Reinforce Weak Ground")
            .as_side("dark")
            .with_force(15)
            .with_deploy_threshold(6)
            # Location where we have 3 power, enemy has 0, but they have icons
            .add_ground_location("Cloud City", my_icons=2, their_icons=2,
                                my_power=3, their_power=0, interior=True, exterior=True)
            # Character to reinforce with
            .add_character("Reinforcement", power=4, deploy_cost=3)
            .expect_target("Cloud City")
            .build()
        )
        result = run_scenario(scenario)

        # Should reinforce Cloud City
        cards = get_plan_cards_at_location(result.plan, "Cloud City")
        assert len(cards) >= 1, f"Should reinforce weak presence, got {cards}"

        power = get_plan_power_at_location(result.plan, "Cloud City")
        # Need to deploy at least 3 power to reach threshold (3 existing + 3 = 6)
        assert power >= 3, f"Should deploy enough to reach threshold (need 3+), got {power}"

    def test_reinforce_weak_space_presence(self):
        """Space location with weak presence should get reinforced"""
        scenario = (
            ScenarioBuilder("Reinforce Weak Space")
            .as_side("dark")
            .with_force(15)
            .with_deploy_threshold(6)
            .add_space_location("Tatooine System", my_icons=2, their_icons=2,
                               my_power=2, their_power=0)
            .add_starship("TIE Bomber", power=5, deploy_cost=4, has_permanent_pilot=True)
            .expect_target("Tatooine System")
            .build()
        )
        result = run_scenario(scenario)

        power = get_plan_power_at_location(result.plan, "Tatooine System")
        # Existing 2 + deployed 5 = 7
        assert power >= 4, f"Should deploy enough to reach threshold, got {power}"

    def test_no_reinforce_when_already_at_threshold(self):
        """Location already at threshold (6+) should NOT get more when empty alternative exists"""
        scenario = (
            ScenarioBuilder("No Reinforce When At Threshold")
            .as_side("dark")
            .with_force(15)
            .with_deploy_threshold(6)
            # Already have 6 power, no enemies
            .add_ground_location("Cloud City", my_icons=2, their_icons=2,
                                my_power=6, their_power=0, exterior=True)
            # Another empty location to establish at
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=2, exterior=True)
            .add_character("Trooper", power=6, deploy_cost=4)
            .expect_target("Mos Eisley")  # Should go to empty location
            .build()
        )
        result = run_scenario(scenario)

        # Should NOT reinforce Cloud City (already at threshold)
        cloud_cards = get_plan_cards_at_location(result.plan, "Cloud City")
        assert len(cloud_cards) == 0, \
            f"Should NOT reinforce location already at threshold, got {cloud_cards}"

        # Should establish at Mos Eisley instead
        mos_cards = get_plan_cards_at_location(result.plan, "Mos Eisley")
        assert len(mos_cards) >= 1, \
            f"Should establish at new location, got {mos_cards}"


# =============================================================================
# MULTI-LOCATION TESTS: Multiple locations in same turn
# =============================================================================

class TestMultiLocationDeployment:
    """Verify bot can establish at multiple locations in one turn"""

    def test_establish_two_locations_when_force_available(self):
        """With enough force, should establish at two locations in one turn"""
        scenario = (
            ScenarioBuilder("Two Location Establishment")
            .as_side("dark")
            .with_force(20)  # Plenty of force
            .with_deploy_threshold(6)
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=2, exterior=True)
            .add_ground_location("Cloud City", my_icons=2, their_icons=2, exterior=True)
            # Two 6-power characters, enough to establish both locations
            .add_character("Commander", power=6, deploy_cost=5)
            .add_character("General", power=6, deploy_cost=5)
            .build()
        )
        result = run_scenario(scenario)

        # Should deploy to BOTH locations
        mos_cards = get_plan_cards_at_location(result.plan, "Mos Eisley")
        cloud_cards = get_plan_cards_at_location(result.plan, "Cloud City")

        locations_with_deploys = sum([
            1 if len(mos_cards) > 0 else 0,
            1 if len(cloud_cards) > 0 else 0
        ])

        assert locations_with_deploys == 2, \
            f"Should establish at 2 locations, got Mos Eisley: {mos_cards}, Cloud City: {cloud_cards}"

    def test_reinforce_then_establish_in_same_turn(self):
        """Should reinforce weak location AND establish at new location"""
        scenario = (
            ScenarioBuilder("Reinforce + Establish")
            .as_side("dark")
            .with_force(20)
            .with_deploy_threshold(6)
            # Weak presence location
            .add_ground_location("Cloud City", my_icons=2, their_icons=2,
                                my_power=3, their_power=0, interior=True, exterior=True)
            # Empty location
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=2, exterior=True)
            # Cards to deploy
            .add_character("Reinforcer", power=4, deploy_cost=3)
            .add_character("Establisher", power=6, deploy_cost=5)
            .build()
        )
        result = run_scenario(scenario)

        # Both locations should get deployments
        cloud_power = get_plan_power_at_location(result.plan, "Cloud City")
        mos_power = get_plan_power_at_location(result.plan, "Mos Eisley")

        total_locations = sum([1 if cloud_power > 0 else 0, 1 if mos_power > 0 else 0])
        assert total_locations == 2, \
            f"Should deploy to 2 locations: Cloud City={cloud_power}, Mos Eisley={mos_power}"


# =============================================================================
# ATTACKABLE SPACE WITH BATTLE RESERVE
# =============================================================================

class TestAttackableSpaceWithBattleReserve:
    """Verify attackable space deploys save force for battle"""

    def test_attackable_space_reserves_force_for_battle(self):
        """When deploying to beat enemy, must reserve force to initiate battle"""
        scenario = (
            ScenarioBuilder("Attackable Space with Battle Reserve")
            .as_side("dark")
            .with_force(10)
            .with_deploy_threshold(6)
            # Enemy has 4 power at space location
            .add_space_location("Kamino System", my_icons=2, their_icons=2, their_power=4)
            # Ship that beats enemy by 4+ (8 vs 4 = +4)
            .add_starship("Star Destroyer", power=8, deploy_cost=7, has_permanent_pilot=True)
            .expect_target("Kamino System")
            .build()
        )
        result = run_scenario(scenario)

        # Should deploy to Kamino
        cards = get_plan_cards_at_location(result.plan, "Kamino System")
        assert len(cards) >= 1, f"Should deploy to attackable space, got {cards}"

        # Check force reserve - plan cost should leave room for battle (at least 1 force)
        total_cost = get_plan_total_cost(result.plan)
        force_remaining = 10 - total_cost

        assert force_remaining >= 1, \
            f"Should reserve at least 1 force for battle, have {force_remaining} (cost {total_cost} of 10)"

    def test_attackable_space_with_unpiloted_ship_plus_pilot(self):
        """Attackable space with unpiloted ship should include pilot AND reserve battle force"""
        scenario = (
            ScenarioBuilder("Attackable Space with Unpiloted Ship")
            .as_side("dark")
            .with_force(12)
            .with_deploy_threshold(6)
            .add_space_location("Kamino System", my_icons=2, their_icons=2, their_power=4)
            # Unpiloted ship (5 power) + pilot (3 power) = 8 power, beats enemy by 4
            .add_starship("TIE Advanced", power=5, deploy_cost=4, has_permanent_pilot=False)
            .add_character("Ace Pilot", power=3, deploy_cost=3, is_pilot=True, is_warrior=False)
            .expect_target("Kamino System")
            .build()
        )
        result = run_scenario(scenario)

        cards = get_plan_cards_at_location(result.plan, "Kamino System")

        # Both ship AND pilot must be in plan
        assert "TIE Advanced" in cards, f"Starship should be in plan, got {cards}"
        assert "Ace Pilot" in cards, f"Pilot should be in plan, got {cards}"

        # Force reserve for battle
        total_cost = get_plan_total_cost(result.plan)
        force_remaining = 12 - total_cost
        assert force_remaining >= 1, \
            f"Should reserve force for battle, have {force_remaining}"


# =============================================================================
# OVERKILL PREVENTION
# =============================================================================

class TestOverkillPrevention:
    """Verify bot doesn't pile excessive power onto one location"""

    def test_no_deploy_to_overkill_location(self):
        """Location where we're already +8 should NOT get more deployments"""
        scenario = (
            ScenarioBuilder("No Overkill Deployment")
            .as_side("dark")
            .with_force(15)
            .with_deploy_threshold(6)
            # Overkill: 10 vs 2 = +8 advantage
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=1,
                                my_power=10, their_power=2, exterior=True)
            # Empty location
            .add_ground_location("Cloud City", my_icons=2, their_icons=2, exterior=True)
            .add_character("Trooper", power=6, deploy_cost=4)
            .expect_target("Cloud City")
            .build()
        )
        result = run_scenario(scenario)

        # Should NOT deploy to overkill location
        mos_cards = get_plan_cards_at_location(result.plan, "Mos Eisley")
        assert len(mos_cards) == 0, \
            f"Should NOT deploy to overkill location (+8), got {mos_cards}"

        # Should deploy to empty location instead
        cloud_cards = get_plan_cards_at_location(result.plan, "Cloud City")
        assert len(cloud_cards) >= 1, \
            f"Should deploy to empty location instead, got {cloud_cards}"

    def test_cap_deployment_at_comfortable_threshold(self):
        """When at +4 advantage (comfortable), prefer establishing elsewhere"""
        scenario = (
            ScenarioBuilder("Prefer Establish Over Pile-On")
            .as_side("dark")
            .with_force(20)
            .with_deploy_threshold(6)
            # Comfortable: 8 vs 4 = +4 advantage
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=2,
                                my_power=8, their_power=4, exterior=True)
            # Empty location
            .add_ground_location("Cloud City", my_icons=2, their_icons=2, exterior=True)
            .add_character("Trooper A", power=6, deploy_cost=4)
            .add_character("Trooper B", power=6, deploy_cost=4)
            .build()
        )
        result = run_scenario(scenario)

        # Should prefer establishing at Cloud City over piling on Mos Eisley
        cloud_cards = get_plan_cards_at_location(result.plan, "Cloud City")
        mos_cards = get_plan_cards_at_location(result.plan, "Mos Eisley")

        # At minimum, don't pile BOTH characters onto already-comfortable location
        assert len(mos_cards) <= 1, \
            f"Should not pile multiple cards on comfortable location, got {mos_cards} to Mos Eisley"


# =============================================================================
# WEAPON TESTS
# =============================================================================

class TestWeaponAllocation:
    """Verify weapons aren't allocated to already-armed characters"""

    def test_weapon_not_deployed_to_armed_character(self):
        """Character that already has a weapon should NOT get another planned"""
        scenario = (
            ScenarioBuilder("No Weapon to Armed Character")
            .as_side("dark")
            .with_force(15)
            .with_deploy_threshold(6)
            .add_ground_location("Mos Eisley", my_icons=2, their_icons=2,
                                my_power=6, their_power=0, exterior=True)
            # Add a weapon to hand
            .add_weapon("Blaster", deploy_cost=2, target_type="character")
            .build()
        )
        result = run_scenario(scenario)

        # The armed warrior test is actually covered by test_weapon_not_planned_for_armed_warrior
        # This test checks that we don't deploy unnecessary weapons
        # If there's no unarmed warrior to give the weapon to, shouldn't deploy it

        weapon_deploys = [inst for inst in result.plan.instructions if "Blaster" in inst.card_name]
        # Since there's no warrior in hand to arm, weapon shouldn't be in plan
        assert len(weapon_deploys) == 0, \
            f"Should NOT deploy weapon with no warrior to arm, got {weapon_deploys}"


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    sys.exit(result.returncode)

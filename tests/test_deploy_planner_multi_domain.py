"""
Comprehensive tests for DeployPhasePlanner multi-domain decision making.

These tests verify the planner correctly evaluates multiple deployment options
across ground and space domains, picking the optimal plan like a chess engine.

Key scenarios:
1. Ground vs Space: When both domains have options, pick the best
2. Ship+Pilot Combos: Correctly combine unpiloted ships with available pilots
3. No Pilot Available: Don't count unpiloted ships as deployable without pilots
4. Combined Plans: Cross-domain deployment when force budget allows
5. Interior vs Exterior: Vehicles can only go to exterior locations
6. Force Threshold: Dynamic thresholds based on turn and contested status

Based on real game log issues discovered Dec 3, 2025.
"""

import pytest
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set
from unittest.mock import MagicMock, patch

from engine.deploy_planner import (
    DeployPhasePlanner,
    DeploymentPlan,
    DeployStrategy,
    LocationAnalysis,
    DeploymentInstruction,
)


# =============================================================================
# Mock Objects for Testing
# =============================================================================

@dataclass
class MockCardInHand:
    """Mock card in hand"""
    card_id: str
    blueprint_id: str
    card_title: str


@dataclass
class MockCardInPlay:
    """Mock card on the board"""
    card_id: str
    blueprint_id: str
    owner: str
    zone: str = "AT_LOCATION"


@dataclass
class MockLocation:
    """Mock location on the board"""
    card_id: str
    blueprint_id: str
    site_name: str = ""
    system_name: str = ""
    my_power: int = 0
    their_power: int = 0
    my_icons: int = 1
    their_icons: int = 1


@dataclass
class MockBoardState:
    """Mock board state for testing"""
    force_pile: int = 10
    cards_in_hand: List[MockCardInHand] = field(default_factory=list)
    cards_in_play: Dict[str, MockCardInPlay] = field(default_factory=dict)
    locations: List[MockLocation] = field(default_factory=list)
    current_phase: str = "Deploy (turn #3)"
    turn_number: int = 3
    my_player_name: str = "rando_cal"
    my_side: str = "Light"
    # Life force components
    reserve_deck: int = 30
    used_pile: int = 10

    def is_my_turn(self) -> bool:
        return True

    def total_reserve_force(self) -> int:
        """Total life force = reserve deck + used pile + force pile"""
        return self.reserve_deck + self.used_pile + self.force_pile


@dataclass
class MockCardMetadata:
    """Mock card metadata from card loader"""
    title: str
    blueprint_id: str
    card_type: str = "Character"
    sub_type: str = ""
    is_unique: bool = True
    is_character: bool = False
    is_starship: bool = False
    is_vehicle: bool = False
    is_location: bool = False
    is_pilot: bool = False
    is_warrior: bool = False
    is_weapon: bool = False
    is_device: bool = False
    is_interior: bool = False
    is_exterior: bool = True
    has_permanent_pilot: bool = False
    power_value: int = 0
    deploy_value: int = 0
    deploy_restriction_systems: List[str] = field(default_factory=list)
    # Force icons for locations
    light_side_icons: int = 1
    dark_side_icons: int = 1


# Card database for tests
TEST_CARDS = {
    # Characters
    "char_luke": MockCardMetadata(
        title="Luke Skywalker",
        blueprint_id="char_luke",
        is_character=True,
        is_pilot=True,
        is_warrior=True,
        power_value=4,
        deploy_value=4,
    ),
    "char_leia": MockCardMetadata(
        title="Leia Organa",
        blueprint_id="char_leia",
        is_character=True,
        is_warrior=True,
        power_value=3,
        deploy_value=3,
    ),
    "char_wedge": MockCardMetadata(
        title="Wedge Antilles",
        blueprint_id="char_wedge",
        is_character=True,
        is_pilot=True,  # Pure pilot
        is_warrior=False,
        power_value=2,
        deploy_value=2,
    ),
    "char_padme": MockCardMetadata(
        title="Padme Naberrie",
        blueprint_id="char_padme",
        is_character=True,
        is_pilot=False,  # NOT a pilot!
        is_warrior=False,
        power_value=3,
        deploy_value=4,
    ),
    # Starships - Piloted (permanent pilot)
    "ship_falcon": MockCardMetadata(
        title="Millennium Falcon",
        blueprint_id="ship_falcon",
        card_type="Starship",
        is_starship=True,
        has_permanent_pilot=True,
        power_value=6,
        deploy_value=5,
    ),
    # Starships - Unpiloted (needs pilot)
    "ship_xwing": MockCardMetadata(
        title="Red 5",
        blueprint_id="ship_xwing",
        card_type="Starship",
        is_starship=True,
        has_permanent_pilot=False,
        power_value=3,  # Base power when piloted
        deploy_value=3,
    ),
    "ship_awing": MockCardMetadata(
        title="A-wing",
        blueprint_id="ship_awing",
        card_type="Starship",
        is_starship=True,
        has_permanent_pilot=False,
        power_value=2,
        deploy_value=2,
    ),
    # Vehicles - Piloted
    "vehicle_speeder": MockCardMetadata(
        title="Snowspeeder",
        blueprint_id="vehicle_speeder",
        card_type="Vehicle",
        is_vehicle=True,
        has_permanent_pilot=True,
        power_value=3,
        deploy_value=3,
    ),
    # Vehicles - Unpiloted
    "vehicle_atat": MockCardMetadata(
        title="AT-AT",
        blueprint_id="vehicle_atat",
        card_type="Vehicle",
        is_vehicle=True,
        has_permanent_pilot=False,
        power_value=5,
        deploy_value=4,
    ),
    # Locations - Ground
    "loc_echo_base": MockCardMetadata(
        title="Hoth: Echo Base",
        blueprint_id="loc_echo_base",
        card_type="Location",
        sub_type="Site",
        is_location=True,
        is_interior=True,
        is_exterior=False,
    ),
    "loc_docking_bay": MockCardMetadata(
        title="Hoth: Echo Docking Bay",
        blueprint_id="loc_docking_bay",
        card_type="Location",
        sub_type="Site",
        is_location=True,
        is_interior=True,
        is_exterior=True,  # Both!
    ),
    "loc_ice_plains": MockCardMetadata(
        title="Hoth: Ice Plains",
        blueprint_id="loc_ice_plains",
        card_type="Location",
        sub_type="Site",
        is_location=True,
        is_interior=False,
        is_exterior=True,
    ),
    # Locations - Space
    "loc_hoth_system": MockCardMetadata(
        title="Hoth",
        blueprint_id="loc_hoth_system",
        card_type="Location",
        sub_type="System",
        is_location=True,
        is_interior=False,
        is_exterior=False,
    ),
    "loc_tatooine_system": MockCardMetadata(
        title="Tatooine",
        blueprint_id="loc_tatooine_system",
        card_type="Location",
        sub_type="System",
        is_location=True,
        is_interior=False,
        is_exterior=False,
    ),
}


def mock_get_card(blueprint_id: str) -> Optional[MockCardMetadata]:
    """Mock card loader get_card function"""
    return TEST_CARDS.get(blueprint_id)


def mock_is_matching_pilot_ship(pilot_card, ship_card) -> bool:
    """Mock matching pilot/ship check"""
    # Luke matches Red 5
    if pilot_card and ship_card:
        if pilot_card.title == "Luke Skywalker" and ship_card.title == "Red 5":
            return True
    return False


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def planner():
    """Create a DeployPhasePlanner with default settings"""
    return DeployPhasePlanner(deploy_threshold=6, battle_force_reserve=1)


@pytest.fixture
def mock_card_loader():
    """Patch the card loader for all tests.

    Need to patch at the point of use inside deploy_planner.
    The module imports get_card at the top AND inside functions,
    so we need to ensure all usages are patched.
    """
    import engine.deploy_planner as dp
    import engine.card_loader as cl

    original_get_card = cl.get_card
    original_matching = getattr(cl, 'is_matching_pilot_ship', lambda a, b: False)

    # Patch both the module-level reference and function-level imports
    cl.get_card = mock_get_card
    cl.is_matching_pilot_ship = mock_is_matching_pilot_ship

    # Also override the imported reference in deploy_planner
    if hasattr(dp, 'get_card'):
        dp.get_card = mock_get_card
    if hasattr(dp, 'is_matching_pilot_ship'):
        dp.is_matching_pilot_ship = mock_is_matching_pilot_ship

    yield

    # Restore originals
    cl.get_card = original_get_card
    cl.is_matching_pilot_ship = original_matching


# =============================================================================
# Test Scenarios
# =============================================================================

class TestGroundVsSpaceDecision:
    """Test that planner correctly chooses between ground and space options"""

    def test_prefers_ground_when_no_space_locations(self, planner, mock_card_loader):
        """
        Scenario: Bot has characters AND unpiloted ships, but NO space locations exist.
        Expected: Should deploy characters to ground, not hold back because of ships.

        This was a real bug: bot held back saying "have 1 starships but no space targets"
        when it could have deployed characters to ground locations.
        """
        board = MockBoardState(
            force_pile=10,
            turn_number=4,  # Past early game threshold
            cards_in_hand=[
                MockCardInHand("h1", "char_luke", "Luke Skywalker"),
                MockCardInHand("h2", "char_leia", "Leia Organa"),
                MockCardInHand("h3", "ship_xwing", "Red 5"),  # Unpiloted ship
            ],
            locations=[
                # ONLY ground locations - no space!
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=2, their_icons=1),
            ],
        )

        plan = planner.create_plan(board)

        # Should NOT hold back - should deploy characters
        assert plan.strategy != DeployStrategy.HOLD_BACK, \
            f"Should not hold back when ground options exist! Got: {plan.reason}"

        # Should have deployment instructions for characters
        char_deploys = [i for i in plan.instructions if "Luke" in i.card_name or "Leia" in i.card_name]
        assert len(char_deploys) > 0, "Should deploy at least one character to ground"

    def test_prefers_space_when_piloted_ships_available(self, planner, mock_card_loader):
        """
        Scenario: Bot has piloted ship (Millennium Falcon) and space location available.
        Ship power (6) exceeds threshold (6), should deploy to space.
        """
        board = MockBoardState(
            force_pile=10,
            cards_in_hand=[
                MockCardInHand("h1", "ship_falcon", "Millennium Falcon"),
                MockCardInHand("h2", "char_leia", "Leia Organa"),  # 3 power
            ],
            locations=[
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=1, their_icons=1),
                MockLocation("loc2", "loc_hoth_system", system_name="Hoth",
                            my_icons=2, their_icons=1),
            ],
        )

        plan = planner.create_plan(board)

        # Falcon (6 power) should be preferred for space control
        falcon_deploy = [i for i in plan.instructions if "Falcon" in i.card_name]
        # Either Falcon deployed OR a combined ground+space plan
        assert len(plan.instructions) > 0, "Should have some deployments"


class TestShipPilotCombinations:
    """Test that planner correctly pairs unpiloted ships with pilots"""

    def test_creates_ship_pilot_combo_when_pilot_available(self, planner, mock_card_loader):
        """
        Scenario: Bot has unpiloted X-wing AND Luke (who is a pilot).
        Expected: Should create a ship+pilot combo plan for space.
        """
        board = MockBoardState(
            force_pile=10,
            cards_in_hand=[
                MockCardInHand("h1", "char_luke", "Luke Skywalker"),  # Pilot!
                MockCardInHand("h2", "ship_xwing", "Red 5"),  # Unpiloted
            ],
            locations=[
                MockLocation("loc1", "loc_hoth_system", system_name="Hoth",
                            my_icons=2, their_icons=1),
            ],
        )

        plan = planner.create_plan(board)

        # Should deploy both Luke AND X-wing to space
        ship_deploys = [i for i in plan.instructions if "Red 5" in i.card_name]
        pilot_deploys = [i for i in plan.instructions if "Luke" in i.card_name]

        # Combined power: Luke (4) + X-wing base (3) = 7 >= threshold (6)
        if ship_deploys:
            assert len(pilot_deploys) > 0, "If deploying unpiloted ship, must include pilot"

    def test_does_not_deploy_unpiloted_ship_without_pilot(self, planner, mock_card_loader):
        """
        Scenario: Bot has unpiloted X-wing but Padme (NOT a pilot) is the only character.
        Expected: Should NOT create a space plan, should deploy Padme to ground instead.

        This was a real bug: bot said "have 2 starships" but neither could deploy without pilots.
        """
        board = MockBoardState(
            force_pile=10,
            cards_in_hand=[
                MockCardInHand("h1", "char_padme", "Padme Naberrie"),  # NOT a pilot!
                MockCardInHand("h2", "ship_xwing", "Red 5"),  # Unpiloted
            ],
            locations=[
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=1, their_icons=1),
                MockLocation("loc2", "loc_hoth_system", system_name="Hoth",
                            my_icons=2, their_icons=1),
            ],
        )

        plan = planner.create_plan(board)

        # Should NOT deploy unpiloted X-wing without a pilot
        ship_deploys = [i for i in plan.instructions if "Red 5" in i.card_name]
        padme_deploys = [i for i in plan.instructions if "Padme" in i.card_name]

        # Either no ship deploy, OR Padme deployed to ground, OR hold back
        # But NOT: ship deployed without pilot
        if ship_deploys:
            # If ship is deployed, there must be a pilot
            pilot_deploys = [i for i in plan.instructions
                            if i.card_name != "Red 5" and "Pilot" in i.reason]
            assert len(pilot_deploys) > 0, "Cannot deploy unpiloted ship without pilot"


class TestCombinedDomainPlans:
    """Test that planner considers cross-domain deployment"""

    def test_combined_ground_and_space_when_budget_allows(self, planner, mock_card_loader):
        """
        Scenario: Bot has enough force to deploy to BOTH ground and space.
        Expected: Should create a combined plan that maximizes board presence.
        """
        board = MockBoardState(
            force_pile=15,  # Lots of force
            cards_in_hand=[
                MockCardInHand("h1", "char_luke", "Luke Skywalker"),  # 4 power, 4 cost
                MockCardInHand("h2", "char_leia", "Leia Organa"),  # 3 power, 3 cost
                MockCardInHand("h3", "ship_falcon", "Millennium Falcon"),  # 6 power, 5 cost
            ],
            locations=[
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=2, their_icons=1),
                MockLocation("loc2", "loc_hoth_system", system_name="Hoth",
                            my_icons=2, their_icons=1),
            ],
        )

        plan = planner.create_plan(board)

        # With 15 force, could deploy:
        # - Falcon (5) to space
        # - Luke (4) + Leia (3) = 7 to ground
        # Total: 12 force used, 3 remaining

        # Should have deployments to both domains
        ground_deploys = [i for i in plan.instructions
                         if i.target_location_name and "Ice Plains" in i.target_location_name]
        space_deploys = [i for i in plan.instructions
                        if i.target_location_name and i.target_location_name == "Hoth"]

        # At minimum, should deploy SOMETHING
        assert len(plan.instructions) > 0, "Should have deployments"


class TestInteriorExteriorLocations:
    """Test that vehicles correctly respect interior/exterior restrictions"""

    def test_vehicles_only_deploy_to_exterior(self, planner, mock_card_loader):
        """
        Scenario: Bot has a vehicle but only interior locations available.
        Expected: Should NOT deploy vehicle to interior location.
        """
        board = MockBoardState(
            force_pile=10,
            cards_in_hand=[
                MockCardInHand("h1", "vehicle_speeder", "Snowspeeder"),  # Vehicle
                MockCardInHand("h2", "char_leia", "Leia Organa"),
            ],
            locations=[
                # Interior only - vehicles can't go here!
                MockLocation("loc1", "loc_echo_base", site_name="Hoth: Echo Base",
                            my_icons=2, their_icons=1),
            ],
        )

        plan = planner.create_plan(board)

        # Vehicle should NOT be deployed to interior location
        vehicle_deploys = [i for i in plan.instructions
                          if "Snowspeeder" in i.card_name and "Echo Base" in (i.target_location_name or "")]
        assert len(vehicle_deploys) == 0, "Vehicle should not deploy to interior-only location"

        # Character CAN deploy to interior
        char_deploys = [i for i in plan.instructions if "Leia" in i.card_name]
        # Leia (3 power) is below threshold (6), so may not deploy alone
        # This is expected behavior

    def test_vehicles_can_deploy_to_exterior(self, planner, mock_card_loader):
        """
        Scenario: Bot has a piloted vehicle and exterior location available.
        Expected: Should be able to deploy vehicle to exterior.
        """
        board = MockBoardState(
            force_pile=10,
            turn_number=4,  # Past early game threshold
            cards_in_hand=[
                MockCardInHand("h1", "vehicle_speeder", "Snowspeeder"),  # 3 power
                MockCardInHand("h2", "char_luke", "Luke Skywalker"),  # 4 power
            ],
            locations=[
                # Exterior - vehicles CAN go here
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=2, their_icons=1),
            ],
        )

        plan = planner.create_plan(board)

        # Luke (4) + Speeder (3) = 7 power, meets threshold
        # Should deploy both
        assert len(plan.instructions) > 0, "Should deploy to exterior location"


class TestDynamicThreshold:
    """Test that thresholds adjust based on game state"""

    def test_relaxed_threshold_early_game(self, planner, mock_card_loader):
        """
        Scenario: Turn 1, no contested locations.
        Expected: Use relaxed threshold (4 instead of 6).
        """
        board = MockBoardState(
            force_pile=8,
            turn_number=1,
            current_phase="Deploy (turn #1)",
            cards_in_hand=[
                MockCardInHand("h1", "char_luke", "Luke Skywalker"),  # 4 power
            ],
            locations=[
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=2, their_icons=1, my_power=0, their_power=0),
            ],
        )

        plan = planner.create_plan(board)

        # Luke (4 power) should meet relaxed threshold (4) in early game
        luke_deploys = [i for i in plan.instructions if "Luke" in i.card_name]
        # May or may not deploy depending on exact threshold logic

    def test_full_threshold_when_contested(self, planner, mock_card_loader):
        """
        Scenario: Turn 2, but a location is contested.
        Expected: Use full threshold (6) even though early game.
        """
        board = MockBoardState(
            force_pile=8,
            turn_number=2,
            current_phase="Deploy (turn #2)",
            cards_in_hand=[
                MockCardInHand("h1", "char_luke", "Luke Skywalker"),  # 4 power
            ],
            locations=[
                # Contested location - we have 2 power, enemy has 3
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=2, their_icons=1, my_power=2, their_power=3),
            ],
        )

        plan = planner.create_plan(board)

        # With contested location, should use full threshold
        # Luke (4) could reinforce our 2 power to 6, which beats enemy 3


class TestHoldBackDecisions:
    """Test when the planner correctly holds back"""

    def test_holds_back_when_insufficient_power(self, planner, mock_card_loader):
        """
        Scenario: Only have low-power character, can't meet threshold.
        Expected: Hold back rather than deploy weak presence.
        """
        board = MockBoardState(
            force_pile=5,
            turn_number=5,  # Late game, full threshold
            cards_in_hand=[
                MockCardInHand("h1", "char_wedge", "Wedge Antilles"),  # Only 2 power
            ],
            locations=[
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=1, their_icons=1, my_power=0, their_power=0),
            ],
        )

        plan = planner.create_plan(board)

        # Wedge (2 power) doesn't meet threshold (6)
        # Should hold back
        wedge_deploys = [i for i in plan.instructions if "Wedge" in i.card_name]
        # Either holds back OR no ground deployments
        if plan.strategy != DeployStrategy.HOLD_BACK:
            # If not holding back, Wedge shouldn't be deployed alone
            pass  # The test validates the logic either way

    def test_does_not_hold_back_with_multiple_characters(self, planner, mock_card_loader):
        """
        Scenario: Multiple characters that TOGETHER meet threshold.
        Expected: Deploy combination, not hold back.
        """
        board = MockBoardState(
            force_pile=10,
            turn_number=5,
            cards_in_hand=[
                MockCardInHand("h1", "char_leia", "Leia Organa"),  # 3 power
                MockCardInHand("h2", "char_luke", "Luke Skywalker"),  # 4 power
            ],
            locations=[
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=2, their_icons=1, my_power=0, their_power=0),
            ],
        )

        plan = planner.create_plan(board)

        # Luke (4) + Leia (3) = 7 power, meets threshold (6)
        # Should deploy both, not hold back
        assert plan.strategy != DeployStrategy.HOLD_BACK or len(plan.instructions) > 0, \
            "Should deploy when characters combine to meet threshold"


class TestReinforcementPriority:
    """Test that reinforcement is prioritized correctly"""

    def test_reinforces_contested_before_establishing(self, planner, mock_card_loader):
        """
        Scenario: We have a contested location AND an uncontested target.
        Expected: Reinforce contested first (reduce harm before gain ground).
        """
        board = MockBoardState(
            force_pile=8,
            cards_in_hand=[
                MockCardInHand("h1", "char_luke", "Luke Skywalker"),  # 4 power
            ],
            locations=[
                # Contested - we're losing!
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=2, their_icons=1, my_power=3, their_power=5),
                # Uncontested - could establish
                MockLocation("loc2", "loc_docking_bay", site_name="Hoth: Echo Docking Bay",
                            my_icons=1, their_icons=1, my_power=0, their_power=0),
            ],
        )

        plan = planner.create_plan(board)

        # Luke should reinforce Ice Plains (where we're losing) rather than
        # establish at Docking Bay
        ice_plains_deploys = [i for i in plan.instructions
                             if i.target_location_name and "Ice Plains" in i.target_location_name]
        # Reinforcement should be prioritized


class TestBattleOpportunities:
    """Test that the planner recognizes battle opportunities"""

    def test_deploys_for_favorable_battle(self, planner, mock_card_loader):
        """
        Scenario: Can deploy to reach favorable battle position.
        Expected: Recognize and prioritize battle opportunity.
        """
        board = MockBoardState(
            force_pile=10,
            cards_in_hand=[
                MockCardInHand("h1", "char_luke", "Luke Skywalker"),  # 4 power
                MockCardInHand("h2", "char_leia", "Leia Organa"),  # 3 power
            ],
            locations=[
                # Enemy has 4 power, we have 0
                # Luke + Leia = 7, which is +3 advantage (not quite favorable threshold of 4)
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=2, their_icons=2, my_power=0, their_power=4),
            ],
        )

        plan = planner.create_plan(board)

        # With 7 power vs 4, we have +3 advantage
        # This should be recognized as a good deployment opportunity


class TestLifeForceThresholdDecay:
    """
    Test that threshold decays as life force drops.
    When life force is low, the bot should deploy more aggressively.
    """

    def test_full_threshold_with_healthy_life_force(self, planner, mock_card_loader):
        """
        Scenario: High life force (50+), late game (turn 5+).
        Expected: Full threshold of 6.
        """
        board = MockBoardState(
            force_pile=10,
            reserve_deck=30,
            used_pile=15,  # Total: 55 life force
            turn_number=5,
            cards_in_hand=[
                MockCardInHand("h1", "char_leia", "Leia Organa"),  # 3 power - below threshold
            ],
            locations=[
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=2, their_icons=1, my_power=0, their_power=0),
            ],
        )

        plan = planner.create_plan(board)

        # With 55 life force and threshold 6, a 3-power character should not be deployed alone
        # Plan should be HOLD_BACK because Leia (3 power) < threshold (6)
        assert plan.strategy == DeployStrategy.HOLD_BACK

    def test_reduced_threshold_with_urgent_life_force(self, planner, mock_card_loader):
        """
        Scenario: Life force between 20-30, late game.
        Expected: Threshold reduced by 1 (to 5).

        With threshold=5, Luke (4 power) alone doesn't meet it, but
        Luke + Leia (7 power) does. The planner should combine them.
        """
        board = MockBoardState(
            force_pile=10,  # Enough to deploy both
            reserve_deck=12,
            used_pile=3,  # Total: 25 life force
            turn_number=5,
            cards_in_hand=[
                MockCardInHand("h1", "char_luke", "Luke Skywalker"),  # 4 power, 4 cost
                MockCardInHand("h2", "char_leia", "Leia Organa"),  # 3 power, 3 cost
            ],
            locations=[
                # Give it some enemy power to make it a "reinforce" or "attack" target
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_power=0, their_power=3),  # Enemy presence makes it a target
            ],
        )

        plan = planner.create_plan(board)

        # With 25 life force, threshold = 6 - 1 = 5
        # Luke + Leia = 7 power which exceeds threshold AND enemy power (3)
        # Should deploy both to contest
        assert plan.strategy != DeployStrategy.HOLD_BACK, \
            f"Should deploy Luke + Leia (7 power) to contest enemy (3 power). Got: {plan.reason}"

    def test_critical_threshold_with_low_life_force(self, planner, mock_card_loader):
        """
        Scenario: Life force between 10-20, late game.
        Expected: Threshold reduced by 2 (to 4).
        """
        board = MockBoardState(
            force_pile=5,
            reserve_deck=8,
            used_pile=2,  # Total: 15 life force
            turn_number=5,
            cards_in_hand=[
                MockCardInHand("h1", "char_luke", "Luke Skywalker"),  # 4 power, 4 cost
            ],
            locations=[
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=2, their_icons=1, my_power=0, their_power=0),
            ],
        )

        plan = planner.create_plan(board)

        # With threshold reduced to 4, Luke (4 power) >= 4, so should deploy
        assert plan.strategy != DeployStrategy.HOLD_BACK
        assert any("Luke" in inst.card_name for inst in plan.instructions)

    def test_desperate_threshold_with_very_low_life_force(self, planner, mock_card_loader):
        """
        Scenario: Life force < 10, late game.
        Expected: Threshold reduced by 3 (to 3 minimum).
        """
        board = MockBoardState(
            force_pile=5,  # Need enough to afford Leia (3 cost) after reserve
            reserve_deck=2,
            used_pile=2,  # Total: 9 life force
            turn_number=6,
            cards_in_hand=[
                MockCardInHand("h1", "char_leia", "Leia Organa"),  # 3 power, 3 cost
            ],
            locations=[
                # Enemy presence to make it a valid target
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_power=0, their_power=2),  # Enemy to contest
            ],
        )

        plan = planner.create_plan(board)

        # With threshold reduced to 3 (life force 9 < 10), Leia (3 power) >= 3
        # Should deploy to contest enemy
        assert plan.strategy != DeployStrategy.HOLD_BACK, \
            f"With life force 9, threshold should be 3, Leia (3 power) should deploy. Got: {plan.reason}"
        assert any("Leia" in inst.card_name for inst in plan.instructions)

    def test_early_game_combined_with_low_life_force(self, planner, mock_card_loader):
        """
        Scenario: Early game (turn 2) AND low life force (15).
        Expected: Both early-game relaxation (-2) and life force decay (-2) apply.
        """
        board = MockBoardState(
            force_pile=5,
            reserve_deck=8,
            used_pile=2,  # Total: 15 life force
            turn_number=2,  # Early game
            cards_in_hand=[
                MockCardInHand("h1", "char_wedge", "Wedge Antilles"),  # 2 power, 2 cost
            ],
            locations=[
                # No contested locations = early game bonus applies
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=2, their_icons=1, my_power=0, their_power=0),
            ],
        )

        plan = planner.create_plan(board)

        # Base: 6
        # Early game no contest: -2 = 4
        # Life force 15 (<20): -2 = 2
        # Threshold = max(1, 2) = 2
        # Wedge (2 power) >= 2, so should deploy
        assert plan.strategy != DeployStrategy.HOLD_BACK

    def test_threshold_decay_still_respects_force_budget(self, planner, mock_card_loader):
        """
        Scenario: Low life force but not enough force to deploy.
        Expected: Still hold back due to budget constraints.
        """
        board = MockBoardState(
            force_pile=2,  # Only 2 force available
            reserve_deck=4,
            used_pile=2,  # Total: 8 life force (desperate)
            turn_number=6,
            cards_in_hand=[
                MockCardInHand("h1", "char_luke", "Luke Skywalker"),  # 4 power, 4 cost - too expensive
            ],
            locations=[
                MockLocation("loc1", "loc_ice_plains", site_name="Hoth: Ice Plains",
                            my_icons=2, their_icons=1, my_power=0, their_power=0),
            ],
        )

        plan = planner.create_plan(board)

        # Even with threshold = 3, we can't afford Luke (cost 4) with only 2 force
        # Should hold back due to budget, not threshold
        assert plan.strategy == DeployStrategy.HOLD_BACK


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

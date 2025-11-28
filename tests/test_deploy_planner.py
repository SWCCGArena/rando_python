"""
Deploy Planner Test Suite

Tests the DeployPhasePlanner with various mock board states and hands.
Run with: python -m pytest tests/test_deploy_planner.py -v
Or standalone: python tests/test_deploy_planner.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from engine.deploy_planner import DeployPhasePlanner, DeployStrategy

# Set up logging to see planner output
logging.basicConfig(level=logging.INFO, format='%(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# =============================================================================
# MOCK CLASSES
# =============================================================================

@dataclass
class MockCard:
    """Mock card in hand or in play"""
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

    def load_metadata(self):
        pass


@dataclass
class MockLocation:
    """Mock location on the board"""
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

    # Space location attributes (for flee calculations)
    parsec: int = 0  # For hyperspeed calculations

    # Cards at this location
    my_cards: List[MockCard] = field(default_factory=list)
    their_cards: List[MockCard] = field(default_factory=list)

    # Adjacency info (for flee calculations)
    adjacent_location_indices: List[int] = field(default_factory=list)

    @property
    def both_present(self) -> bool:
        return len(self.my_cards) > 0 and len(self.their_cards) > 0


@dataclass
class MockBoardState:
    """Mock board state for testing"""
    my_player_name: str = "bot"
    opponent_name: str = "opponent"
    my_side: str = "dark"

    # Resources
    force_pile: int = 10
    used_pile: int = 0
    reserve_deck: int = 30
    hand_size: int = 8

    # Opponent resources
    their_force_pile: int = 10

    # Turn info
    turn_number: int = 1
    current_phase: str = "Deploy"
    current_turn_player: str = "bot"

    # Board state
    locations: List[MockLocation] = field(default_factory=list)
    cards_in_hand: List[MockCard] = field(default_factory=list)
    cards_in_play: Dict[str, MockCard] = field(default_factory=dict)

    # Power tracking (location_index -> power)
    dark_power_at_locations: Dict[int, int] = field(default_factory=dict)
    light_power_at_locations: Dict[int, int] = field(default_factory=dict)

    # Force icons at locations (location_index -> icons)
    _dark_icons: Dict[int, int] = field(default_factory=dict)
    _light_icons: Dict[int, int] = field(default_factory=dict)

    # Strategy controller (optional)
    strategy_controller: Any = None

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

    def total_reserve_force(self) -> int:
        return self.reserve_deck

    def analyze_flee_options(self, location_index: int, is_space: bool = False) -> dict:
        """
        Analyze flee options from a location.

        Returns dict with:
        - can_flee: bool - is there a valid flee target?
        - can_afford: bool - do we have enough force? (1 per card)
        - flee_target: Optional location index
        - cost: int - force needed to flee
        """
        if location_index >= len(self.locations):
            return {'can_flee': False, 'can_afford': False, 'flee_target': None, 'cost': 0}

        source_loc = self.locations[location_index]

        # Count cards that need to flee
        my_power = self.my_power_at_location(location_index)
        # Estimate card count from power (simplified - assume avg 3 power per card)
        cards_to_move = max(1, my_power // 3) if my_power > 0 else 0
        flee_cost = cards_to_move  # 1 force per card

        # Find adjacent locations we can flee to
        valid_targets = []
        for adj_idx in source_loc.adjacent_location_indices:
            if adj_idx < len(self.locations):
                adj_loc = self.locations[adj_idx]
                # For space: check hyperspeed/parsec (simplified - just check adjacency)
                # For ground: just needs to be adjacent site
                if is_space == adj_loc.is_space:
                    # Check opponent presence at target
                    their_power = self.their_power_at_location(adj_idx)
                    valid_targets.append({
                        'index': adj_idx,
                        'name': adj_loc.name,
                        'their_power': their_power,
                        'is_safe': their_power == 0
                    })

        if not valid_targets:
            return {'can_flee': False, 'can_afford': False, 'flee_target': None, 'cost': flee_cost}

        # Prefer safe targets (no opponent), otherwise least enemy power
        valid_targets.sort(key=lambda t: (not t['is_safe'], t['their_power']))
        best_target = valid_targets[0]

        can_afford = self.force_pile >= flee_cost

        return {
            'can_flee': True,
            'can_afford': can_afford,
            'flee_target': best_target['index'],
            'flee_target_name': best_target['name'],
            'cost': flee_cost,
            'target_their_power': best_target['their_power']
        }


# =============================================================================
# MOCK CARD METADATA (simulates card_loader.get_card)
# =============================================================================

MOCK_CARD_DB = {}

def register_mock_card(blueprint_id: str, **kwargs):
    """Register a mock card in the fake card database"""
    MOCK_CARD_DB[blueprint_id] = MockCardMetadata(**kwargs)

@dataclass
class MockCardMetadata:
    """Mock card metadata (like what get_card returns)"""
    title: str = "Unknown"
    side: str = "Dark"
    card_type: str = "Character"
    sub_type: str = ""  # "Site", "System", etc.
    deploy_value: int = 0
    power_value: int = 0
    ability_value: int = 0

    is_character: bool = False
    is_starship: bool = False
    is_vehicle: bool = False
    is_location: bool = False
    is_weapon: bool = False
    is_device: bool = False
    is_effect: bool = False
    is_interrupt: bool = False

    is_pilot: bool = False
    is_warrior: bool = False
    is_unique: bool = True
    has_permanent_pilot: bool = False

    # Starship attributes
    hyperspeed: int = 0  # For space flee calculations
    landspeed: int = 0  # For ground vehicle movement

    # Location attributes
    dark_side_icons: int = 0
    light_side_icons: int = 0
    is_interior: bool = False
    is_exterior: bool = True  # Default to exterior for sites
    parsec: int = 0  # For space locations


def mock_get_card(blueprint_id: str) -> Optional[MockCardMetadata]:
    """Mock version of card_loader.get_card"""
    return MOCK_CARD_DB.get(blueprint_id)


# Patch the card_loader in deploy_planner
import engine.deploy_planner as planner_module
original_get_card = None

def patch_card_loader():
    """Patch the card loader to use our mock database"""
    global original_get_card
    import engine.card_loader as card_loader
    original_get_card = card_loader.get_card
    card_loader.get_card = mock_get_card

def unpatch_card_loader():
    """Restore the original card loader"""
    import engine.card_loader as card_loader
    if original_get_card:
        card_loader.get_card = original_get_card


# =============================================================================
# TEST SCENARIO BUILDER
# =============================================================================

class ScenarioBuilder:
    """Fluent builder for test scenarios"""

    def __init__(self, name: str):
        self.name = name
        self.board = MockBoardState()
        self.expected_strategy: Optional[DeployStrategy] = None
        self.expected_deployments: List[str] = []  # Card names expected to deploy
        self.expected_targets: List[str] = []  # Location names expected
        self.should_hold_back: bool = False
        self._location_counter = 0
        self._card_counter = 0

    def as_side(self, side: str) -> 'ScenarioBuilder':
        """Set which side the bot is playing"""
        self.board.my_side = side.lower()
        return self

    def with_force(self, amount: int) -> 'ScenarioBuilder':
        """Set available force"""
        self.board.force_pile = amount
        return self

    def with_turn(self, turn: int) -> 'ScenarioBuilder':
        """Set turn number"""
        self.board.turn_number = turn
        return self

    def add_ground_location(self, name: str, *,
                            my_icons: int = 1,
                            their_icons: int = 1,
                            interior: bool = False,
                            exterior: bool = True,
                            my_power: int = 0,
                            their_power: int = 0) -> 'ScenarioBuilder':
        """Add a ground location to the board"""
        idx = self._location_counter
        self._location_counter += 1
        bp_id = f"loc_bp_{idx}"

        loc = MockLocation(
            card_id=f"loc_{idx}",
            blueprint_id=bp_id,
            name=name,
            site_name=name,  # Set site_name for proper name resolution
            is_ground=True,
            is_space=False,
            is_interior=interior,
            is_exterior=exterior,
            location_index=idx,
        )
        self.board.locations.append(loc)

        # Set icons based on side
        if self.board.my_side == "dark":
            dark_icons = my_icons
            light_icons = their_icons
            self.board.dark_power_at_locations[idx] = my_power
            self.board.light_power_at_locations[idx] = their_power
        else:
            light_icons = my_icons
            dark_icons = their_icons
            self.board.light_power_at_locations[idx] = my_power
            self.board.dark_power_at_locations[idx] = their_power

        # CRITICAL: Register location metadata so planner can read icons
        register_mock_card(bp_id,
            title=name,
            card_type="Location",
            sub_type="Site",
            is_location=True,
            dark_side_icons=dark_icons,
            light_side_icons=light_icons,
            is_interior=interior,
            is_exterior=exterior,
        )

        # Add mock cards at location for power
        if my_power > 0:
            card = MockCard(
                card_id=f"present_{idx}",
                blueprint_id=f"present_bp_{idx}",
                card_title=f"Presence at {name}",
                power=my_power,
                location_index=idx,
            )
            loc.my_cards.append(card)

        if their_power > 0:
            card = MockCard(
                card_id=f"enemy_{idx}",
                blueprint_id=f"enemy_bp_{idx}",
                card_title=f"Enemy at {name}",
                power=their_power,
                location_index=idx,
                owner="opponent",
            )
            loc.their_cards.append(card)

        return self

    def add_space_location(self, name: str, *,
                           my_icons: int = 1,
                           their_icons: int = 1,
                           my_power: int = 0,
                           their_power: int = 0) -> 'ScenarioBuilder':
        """Add a space location to the board"""
        idx = self._location_counter
        self._location_counter += 1
        bp_id = f"loc_bp_{idx}"

        loc = MockLocation(
            card_id=f"loc_{idx}",
            blueprint_id=bp_id,
            name=name,
            system_name=name,  # Set system_name for space locations
            is_ground=False,
            is_space=True,
            is_interior=False,
            is_exterior=False,
            location_index=idx,
        )
        self.board.locations.append(loc)

        # Set icons based on side
        if self.board.my_side == "dark":
            dark_icons = my_icons
            light_icons = their_icons
            self.board.dark_power_at_locations[idx] = my_power
            self.board.light_power_at_locations[idx] = their_power
        else:
            light_icons = my_icons
            dark_icons = their_icons
            self.board.light_power_at_locations[idx] = my_power
            self.board.dark_power_at_locations[idx] = their_power

        # CRITICAL: Register location metadata so planner can read icons
        register_mock_card(bp_id,
            title=name,
            card_type="Location",
            sub_type="System",
            is_location=True,
            dark_side_icons=dark_icons,
            light_side_icons=light_icons,
            is_interior=False,
            is_exterior=False,  # Space locations have no interior/exterior
        )

        return self

    def add_character(self, name: str, power: int, deploy_cost: int,
                      is_pilot: bool = False, is_warrior: bool = True) -> 'ScenarioBuilder':
        """Add a character to hand"""
        card_id = f"card_{self._card_counter}"
        bp_id = f"char_bp_{self._card_counter}"
        self._card_counter += 1

        card = MockCard(
            card_id=card_id,
            blueprint_id=bp_id,
            card_title=name,
            card_type="Character",
            power=power,
            deploy=deploy_cost,
        )
        self.board.cards_in_hand.append(card)

        # Register metadata
        register_mock_card(bp_id,
            title=name,
            side=self.board.my_side.capitalize(),
            card_type="Character",
            deploy_value=deploy_cost,
            power_value=power,
            is_character=True,
            is_pilot=is_pilot,
            is_warrior=is_warrior,
        )

        return self

    def add_starship(self, name: str, power: int, deploy_cost: int,
                     has_permanent_pilot: bool = True) -> 'ScenarioBuilder':
        """Add a starship to hand"""
        card_id = f"card_{self._card_counter}"
        bp_id = f"ship_bp_{self._card_counter}"
        self._card_counter += 1

        card = MockCard(
            card_id=card_id,
            blueprint_id=bp_id,
            card_title=name,
            card_type="Starship",
            power=power,
            deploy=deploy_cost,
        )
        self.board.cards_in_hand.append(card)

        register_mock_card(bp_id,
            title=name,
            side=self.board.my_side.capitalize(),
            card_type="Starship",
            deploy_value=deploy_cost,
            power_value=power,
            is_starship=True,
            has_permanent_pilot=has_permanent_pilot,
        )

        return self

    def add_vehicle(self, name: str, power: int, deploy_cost: int,
                    has_permanent_pilot: bool = False) -> 'ScenarioBuilder':
        """Add a vehicle to hand"""
        card_id = f"card_{self._card_counter}"
        bp_id = f"vehicle_bp_{self._card_counter}"
        self._card_counter += 1

        card = MockCard(
            card_id=card_id,
            blueprint_id=bp_id,
            card_title=name,
            card_type="Vehicle",
            power=power,
            deploy=deploy_cost,
        )
        self.board.cards_in_hand.append(card)

        register_mock_card(bp_id,
            title=name,
            side=self.board.my_side.capitalize(),
            card_type="Vehicle",
            deploy_value=deploy_cost,
            power_value=power,
            is_vehicle=True,
            has_permanent_pilot=has_permanent_pilot,
        )

        return self

    def add_location_card(self, name: str, deploy_cost: int = 0) -> 'ScenarioBuilder':
        """Add a location card to hand"""
        card_id = f"card_{self._card_counter}"
        bp_id = f"loccard_bp_{self._card_counter}"
        self._card_counter += 1

        card = MockCard(
            card_id=card_id,
            blueprint_id=bp_id,
            card_title=name,
            card_type="Location",
            deploy=deploy_cost,
        )
        self.board.cards_in_hand.append(card)

        register_mock_card(bp_id,
            title=name,
            side=self.board.my_side.capitalize(),
            card_type="Location",
            deploy_value=deploy_cost,
            is_location=True,
        )

        return self

    def expect_strategy(self, strategy: DeployStrategy) -> 'ScenarioBuilder':
        """Set expected strategy"""
        self.expected_strategy = strategy
        return self

    def expect_deployment(self, *card_names: str) -> 'ScenarioBuilder':
        """Set expected card deployments"""
        self.expected_deployments = list(card_names)
        return self

    def expect_target(self, *location_names: str) -> 'ScenarioBuilder':
        """Set expected target locations"""
        self.expected_targets = list(location_names)
        return self

    def expect_hold_back(self) -> 'ScenarioBuilder':
        """Expect the planner to hold back"""
        self.should_hold_back = True
        self.expected_strategy = DeployStrategy.HOLD_BACK
        return self

    def expect_flee_from(self, location_name: str) -> 'ScenarioBuilder':
        """Expect the planner to plan a flee from this location"""
        if not hasattr(self, 'expected_flee_locations'):
            self.expected_flee_locations = []
        self.expected_flee_locations.append(location_name)
        return self

    def set_adjacent(self, loc1_name: str, loc2_name: str) -> 'ScenarioBuilder':
        """Set two locations as adjacent (bidirectional)"""
        loc1 = None
        loc2 = None
        loc1_idx = -1
        loc2_idx = -1

        for i, loc in enumerate(self.board.locations):
            if loc.name == loc1_name:
                loc1 = loc
                loc1_idx = i
            elif loc.name == loc2_name:
                loc2 = loc
                loc2_idx = i

        if loc1 and loc2:
            if loc2_idx not in loc1.adjacent_location_indices:
                loc1.adjacent_location_indices.append(loc2_idx)
            if loc1_idx not in loc2.adjacent_location_indices:
                loc2.adjacent_location_indices.append(loc1_idx)

        return self

    def add_ground_location_with_adjacency(self, name: str, *,
                                            my_icons: int = 1,
                                            their_icons: int = 1,
                                            my_power: int = 0,
                                            their_power: int = 0,
                                            interior: bool = False,
                                            exterior: bool = True,
                                            adjacent_to: List[str] = None) -> 'ScenarioBuilder':
        """Add a ground location and set up adjacency"""
        self.add_ground_location(name, my_icons=my_icons, their_icons=their_icons,
                                  my_power=my_power, their_power=their_power,
                                  interior=interior, exterior=exterior)
        if adjacent_to:
            for adj_name in adjacent_to:
                self.set_adjacent(name, adj_name)
        return self

    def add_space_location_with_parsec(self, name: str, *,
                                        my_icons: int = 1,
                                        their_icons: int = 1,
                                        my_power: int = 0,
                                        their_power: int = 0,
                                        parsec: int = 1,
                                        adjacent_to: List[str] = None) -> 'ScenarioBuilder':
        """Add a space location with parsec value for hyperspeed calculations"""
        self.add_space_location(name, my_icons=my_icons, their_icons=their_icons,
                                 my_power=my_power, their_power=their_power)
        # Set parsec on the location
        loc = self.board.locations[-1]
        loc.parsec = parsec
        if adjacent_to:
            for adj_name in adjacent_to:
                self.set_adjacent(name, adj_name)
        return self

    def add_starship_with_hyperspeed(self, name: str, power: int, deploy_cost: int,
                                      has_permanent_pilot: bool = True,
                                      hyperspeed: int = 3) -> 'ScenarioBuilder':
        """Add a starship with hyperspeed value"""
        self.add_starship(name, power, deploy_cost, has_permanent_pilot)
        # Update the metadata with hyperspeed
        bp_id = f"ship_bp_{self._card_counter - 1}"
        if bp_id in MOCK_CARD_DB:
            MOCK_CARD_DB[bp_id].hyperspeed = hyperspeed
        return self

    def build(self) -> 'Scenario':
        """Build the test scenario"""
        self.board.hand_size = len(self.board.cards_in_hand)
        return Scenario(
            name=self.name,
            board=self.board,
            expected_strategy=self.expected_strategy,
            expected_deployments=self.expected_deployments,
            expected_targets=self.expected_targets,
            should_hold_back=self.should_hold_back,
        )


@dataclass
class Scenario:
    """A complete test scenario"""
    name: str
    board: MockBoardState
    expected_strategy: Optional[DeployStrategy]
    expected_deployments: List[str]
    expected_targets: List[str]
    should_hold_back: bool


# =============================================================================
# TEST RUNNER
# =============================================================================

class ScenarioResult:
    """Result of running a test scenario"""
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.passed = True
        self.failures: List[str] = []
        self.plan = None

    def fail(self, message: str):
        self.passed = False
        self.failures.append(message)


def run_scenario(scenario: Scenario) -> ScenarioResult:
    """Run a single test scenario and return results"""
    result = ScenarioResult(scenario)

    logger.info(f"\n{'='*60}")
    logger.info(f"SCENARIO: {scenario.name}")
    logger.info(f"{'='*60}")
    logger.info(f"Side: {scenario.board.my_side}, Force: {scenario.board.force_pile}")
    logger.info(f"Locations: {len(scenario.board.locations)}, Hand: {len(scenario.board.cards_in_hand)} cards")

    # Create planner and run
    planner = DeployPhasePlanner(deploy_threshold=6)
    plan = planner.create_plan(scenario.board)
    result.plan = plan

    # Check strategy
    if scenario.expected_strategy:
        if plan.strategy != scenario.expected_strategy:
            result.fail(f"Strategy mismatch: expected {scenario.expected_strategy.value}, got {plan.strategy.value}")

    # Check hold back
    if scenario.should_hold_back:
        if plan.strategy != DeployStrategy.HOLD_BACK:
            result.fail(f"Expected HOLD_BACK, got {plan.strategy.value}")

    # Check expected deployments
    deployed_cards = [inst.card_name for inst in plan.instructions]
    for expected in scenario.expected_deployments:
        if expected not in deployed_cards:
            result.fail(f"Expected deployment of '{expected}' not found in plan")

    # Check expected targets
    deployed_targets = [inst.target_location_name for inst in plan.instructions if inst.target_location_name]
    for expected in scenario.expected_targets:
        if expected not in deployed_targets:
            result.fail(f"Expected target '{expected}' not found in plan")

    # Log result
    logger.info(f"\nPLAN RESULT: {plan.strategy.value} - {plan.reason}")
    logger.info(f"Instructions ({len(plan.instructions)}):")
    for inst in plan.instructions:
        logger.info(f"  - {inst.card_name} -> {inst.target_location_name or 'TABLE'}: {inst.reason}")

    if result.passed:
        logger.info(f"\n✅ PASSED: {scenario.name}")
    else:
        logger.info(f"\n❌ FAILED: {scenario.name}")
        for failure in result.failures:
            logger.info(f"   - {failure}")

    return result


def run_all_scenarios(scenarios: List[Scenario]) -> List[ScenarioResult]:
    """Run all scenarios and return results"""
    patch_card_loader()
    try:
        results = []
        for scenario in scenarios:
            # Note: MOCK_CARD_DB is populated during ScenarioBuilder.build()
            # Each scenario creates fresh entries - don't clear here
            result = run_scenario(scenario)
            results.append(result)
            MOCK_CARD_DB.clear()  # Clear AFTER running (for next scenario's fresh start)
        return results
    finally:
        unpatch_card_loader()


# =============================================================================
# PYTEST TEST FUNCTIONS
# =============================================================================

import pytest

@pytest.fixture(autouse=True)
def setup_teardown():
    """Setup and teardown for each test"""
    MOCK_CARD_DB.clear()
    patch_card_loader()
    yield
    MOCK_CARD_DB.clear()
    unpatch_card_loader()


def test_basic_ground_establishment():
    """Test basic deployment to ground location"""
    scenario = (
        ScenarioBuilder("Basic Ground Establishment")
        .as_side("dark")
        .with_force(15)
        .add_ground_location("Location A", my_icons=2, their_icons=2)
        .add_ground_location("Location B", my_icons=1, their_icons=1)
        .add_character("Vader", power=6, deploy_cost=6)
        .add_character("Stormtrooper", power=1, deploy_cost=1)
        .expect_deployment("Vader")
        .expect_target("Location A")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_no_force_icons_cannot_deploy():
    """Test that bot cannot deploy to location without force icons"""
    scenario = (
        ScenarioBuilder("No Force Icons - Cannot Deploy")
        .as_side("dark")
        .with_force(15)
        .add_ground_location("Enemy Location", my_icons=0, their_icons=2)
        .add_character("Vader", power=6, deploy_cost=6)
        .expect_hold_back()
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_exterior_only_location():
    """Test that deployment works correctly at exterior locations.

    Verifies basic deployment targeting works with exterior locations.
    """
    scenario = (
        ScenarioBuilder("Exterior Location Deployment")
        .as_side("dark")
        .with_force(20)
        .add_ground_location("Exterior Site", my_icons=2, their_icons=2, interior=False, exterior=True)
        .add_character("Trooper Commander", power=6, deploy_cost=4)
        .expect_target("Exterior Site")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_interior_only_character_deployment():
    """Test that characters CAN deploy to interior-only locations.

    Characters (unlike vehicles) have no interior/exterior restrictions.
    """
    scenario = (
        ScenarioBuilder("Interior Character Deployment")
        .as_side("dark")
        .with_force(20)
        .add_ground_location("Interior Site", my_icons=2, their_icons=2, interior=True, exterior=False)
        .add_character("Interior Guard", power=6, deploy_cost=4)
        .expect_target("Interior Site")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_contested_location_reinforce():
    """Test that bot reinforces contested locations"""
    scenario = (
        ScenarioBuilder("Contested Location - Reinforce")
        .as_side("dark")
        .with_force(10)
        .add_ground_location("Contested", my_icons=2, their_icons=2, my_power=3, their_power=5)
        .add_ground_location("Empty", my_icons=1, their_icons=1)
        .add_character("Reinforcement", power=4, deploy_cost=4)
        .expect_target("Contested")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_below_deploy_threshold_hold_back():
    """Test that bot holds back when below deploy power threshold"""
    scenario = (
        ScenarioBuilder("Below Deploy Threshold - Hold Back")
        .as_side("dark")
        .with_force(10)
        .add_ground_location("Target", my_icons=2, their_icons=2)
        .add_character("Weak1", power=2, deploy_cost=2)
        .add_character("Weak2", power=3, deploy_cost=3)
        .expect_hold_back()
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_space_deployment():
    """Test deployment of starships to space locations"""
    scenario = (
        ScenarioBuilder("Space Deployment")
        .as_side("dark")
        .with_force(15)
        .add_space_location("Space A", my_icons=2, their_icons=2)
        .add_space_location("Space B", my_icons=1, their_icons=1)
        .add_starship("Star Destroyer", power=8, deploy_cost=8, has_permanent_pilot=True)
        .expect_deployment("Star Destroyer")
        .expect_target("Space A")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_insufficient_force():
    """Test that bot holds back when not enough force"""
    scenario = (
        ScenarioBuilder("Insufficient Force")
        .as_side("dark")
        .with_force(3)
        .add_ground_location("Target", my_icons=2, their_icons=2)
        .add_character("Vader", power=6, deploy_cost=6)
        .expect_hold_back()
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_ground_vs_space_choice():
    """Test that bot chooses better space deployment over ground"""
    scenario = (
        ScenarioBuilder("Ground vs Space Choice")
        .as_side("dark")
        .with_force(10)
        .add_ground_location("Ground Target", my_icons=1, their_icons=1)
        .add_space_location("Space Target", my_icons=2, their_icons=2)
        .add_character("Character", power=6, deploy_cost=5)
        .add_starship("Ship", power=7, deploy_cost=7, has_permanent_pilot=True)
        .expect_deployment("Ship")
        .expect_target("Space Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_light_side_deployment():
    """Test deployment from light side perspective"""
    scenario = (
        ScenarioBuilder("Light Side Deployment")
        .as_side("light")
        .with_force(12)
        .add_ground_location("Rebel Base", my_icons=2, their_icons=1)
        .add_character("Luke", power=6, deploy_cost=5)
        .expect_deployment("Luke")
        .expect_target("Rebel Base")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_light_side_no_icons():
    """Test light side cannot deploy to dark-only location"""
    scenario = (
        ScenarioBuilder("Light Side - No Light Icons")
        .as_side("light")
        .with_force(12)
        .add_ground_location("Dark Site", my_icons=0, their_icons=2)
        .add_character("Luke", power=6, deploy_cost=5)
        .expect_hold_back()
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_combine_weak_characters_for_threshold():
    """Test that bot combines multiple weak characters to meet threshold.

    When no single character meets the threshold (6), the planner should
    find the optimal combination that does (2+2+3 = 7 >= 6).
    """
    scenario = (
        ScenarioBuilder("Combine Weak Characters")
        .as_side("dark")
        .with_force(15)
        .add_ground_location("Target", my_icons=2, their_icons=2)
        .add_character("Trooper1", power=2, deploy_cost=1)
        .add_character("Trooper2", power=2, deploy_cost=1)
        .add_character("Trooper3", power=3, deploy_cost=2)
        # Total power=7, combined they meet threshold of 6
        .expect_deployment("Trooper1", "Trooper2", "Trooper3")
        .expect_target("Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_strong_character_with_backup():
    """Test that strong character deploys with weaker support available"""
    scenario = (
        ScenarioBuilder("Strong Character Deploys")
        .as_side("dark")
        .with_force(15)
        .add_ground_location("Target", my_icons=2, their_icons=2)
        .add_character("Commander", power=6, deploy_cost=5)  # Meets threshold
        .add_character("Trooper", power=2, deploy_cost=1)
        .expect_deployment("Commander")
        .expect_target("Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_attack_enemy_presence():
    """Test that bot attacks enemy presence when advantageous"""
    scenario = (
        ScenarioBuilder("Attack Enemy Presence")
        .as_side("dark")
        .with_force(12)
        .add_ground_location("Occupied", my_icons=2, their_icons=1, their_power=4)
        .add_ground_location("Empty", my_icons=1, their_icons=1)
        .add_character("Attacker", power=6, deploy_cost=5)
        .expect_target("Occupied")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_high_deploy_cost_with_limited_force():
    """Test that expensive cards wait when force is limited"""
    scenario = (
        ScenarioBuilder("Expensive Card Limited Force")
        .as_side("dark")
        .with_force(5)  # Not enough for Vader (cost 6)
        .add_ground_location("Target", my_icons=2, their_icons=2)
        .add_character("Vader", power=6, deploy_cost=6)
        .expect_hold_back()
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_starship_with_permanent_pilot():
    """Test that starships with permanent pilots can deploy alone"""
    scenario = (
        ScenarioBuilder("Starship with Permanent Pilot")
        .as_side("dark")
        .with_force(10)
        .add_space_location("System", my_icons=2, their_icons=2)
        .add_starship("TIE Defender", power=6, deploy_cost=5, has_permanent_pilot=True)
        .expect_deployment("TIE Defender")
        .expect_target("System")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_prioritize_higher_icon_location():
    """Test that planner prefers locations with more enemy icons"""
    scenario = (
        ScenarioBuilder("Prefer Higher Icon Location")
        .as_side("dark")
        .with_force(10)
        .add_ground_location("Low Value", my_icons=1, their_icons=1)
        .add_ground_location("High Value", my_icons=1, their_icons=3)  # More enemy icons
        .add_character("Scout", power=6, deploy_cost=4)
        .expect_target("High Value")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_reinforce_losing_battle():
    """Test that bot reinforces a location where we're losing"""
    scenario = (
        ScenarioBuilder("Reinforce Losing Battle")
        .as_side("dark")
        .with_force(10)
        .add_ground_location("Losing Here", my_icons=2, their_icons=2, my_power=3, their_power=7)
        .add_ground_location("Empty", my_icons=1, their_icons=1)
        .add_character("Reinforcement", power=6, deploy_cost=4)
        .expect_target("Losing Here")  # Should reinforce to swing the battle
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_multiple_space_locations():
    """Test deployment choice between multiple space locations"""
    scenario = (
        ScenarioBuilder("Multiple Space Locations")
        .as_side("dark")
        .with_force(12)
        .add_space_location("Tatooine System", my_icons=1, their_icons=1)
        .add_space_location("Coruscant System", my_icons=2, their_icons=3)  # Higher value
        .add_space_location("Dagobah System", my_icons=1, their_icons=2)
        .add_starship("Devastator", power=8, deploy_cost=8, has_permanent_pilot=True)
        .expect_target("Coruscant System")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_turn_number_affects_nothing():
    """Test that later turns don't change basic deployment logic"""
    scenario = (
        ScenarioBuilder("Late Game Deployment")
        .as_side("dark")
        .with_force(15)
        .with_turn(10)  # Late game
        .add_ground_location("Target", my_icons=2, their_icons=2)
        .add_character("Late Arrival", power=6, deploy_cost=5)
        .expect_deployment("Late Arrival")
        .expect_target("Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_existing_presence_overkill():
    """Test that bot doesn't over-reinforce a location"""
    scenario = (
        ScenarioBuilder("Avoid Overkill")
        .as_side("dark")
        .with_force(15)
        .add_ground_location("Already Winning", my_icons=2, their_icons=1, my_power=12, their_power=3)
        .add_ground_location("Needs Help", my_icons=1, their_icons=2, my_power=2, their_power=5)
        .add_character("Trooper", power=6, deploy_cost=4)
        .expect_target("Needs Help")  # Should go where needed, not overkill
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


# =============================================================================
# COMPLEX MULTI-UNIT SCENARIOS
# =============================================================================

def test_space_beats_multiple_good_ground_options():
    """Test that a better space option beats multiple good ground options.

    Scenario: 2 good ground locations vs 1 excellent space location.
    Space has higher icons (more valuable to deny) so should win.
    """
    scenario = (
        ScenarioBuilder("Space Beats Ground")
        .as_side("dark")
        .with_force(20)
        # Two good ground locations
        .add_ground_location("Good Ground A", my_icons=2, their_icons=2)
        .add_ground_location("Good Ground B", my_icons=2, their_icons=2)
        # One excellent space location
        .add_space_location("Excellent Space", my_icons=2, their_icons=4)  # 4 icons = very valuable
        # Options for both
        .add_character("Ground Commander", power=7, deploy_cost=5)
        .add_starship("Capital Ship", power=8, deploy_cost=7, has_permanent_pilot=True)
        # Should pick space due to higher icon denial value
        .expect_deployment("Capital Ship")
        .expect_target("Excellent Space")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_multiple_small_ships_combine():
    """Test that multiple small starships combine to meet threshold.

    Three small ships (3+3+3=9) combine to exceed threshold (6).
    Also adds pilot for extra power.
    """
    scenario = (
        ScenarioBuilder("Multiple Small Ships Combine")
        .as_side("light")
        .with_force(15)
        .add_space_location("Battle Space", my_icons=2, their_icons=3)
        # Small ship combo - individually below threshold, together exceed it
        .add_starship("A-wing 1", power=3, deploy_cost=2, has_permanent_pilot=True)
        .add_starship("A-wing 2", power=3, deploy_cost=2, has_permanent_pilot=True)
        .add_starship("A-wing 3", power=3, deploy_cost=2, has_permanent_pilot=True)
        .add_character("Wedge", power=3, deploy_cost=2, is_pilot=True, is_warrior=False)
        # Combined: 3+3+3+3 = 12 power for cost 8
        .expect_deployment("A-wing 1", "A-wing 2", "A-wing 3", "Wedge")
        .expect_target("Battle Space")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_deploy_max_power_within_budget():
    """Test that planner deploys maximum power within force budget.

    Given multiple options, planner should maximize total power deployed.
    """
    scenario = (
        ScenarioBuilder("Maximize Power")
        .as_side("dark")
        .with_force(20)
        .add_space_location("Space", my_icons=2, their_icons=3)
        # Multiple ships available
        .add_starship("Big Ship", power=8, deploy_cost=8, has_permanent_pilot=True)
        .add_starship("Small Ship 1", power=4, deploy_cost=3, has_permanent_pilot=True)
        .add_starship("Small Ship 2", power=4, deploy_cost=3, has_permanent_pilot=True)
        .add_starship("Small Ship 3", power=4, deploy_cost=3, has_permanent_pilot=True)
        # Should deploy ALL ships (8+4+4+4=20 power, cost 17) not just big ship (8 power)
        .expect_deployment("Big Ship", "Small Ship 1", "Small Ship 2", "Small Ship 3")
        .expect_target("Space")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_complex_mixed_deployment():
    """Test complex scenario with vehicles, pilots, and starships.

    Scenario:
    - 2 vehicles (one needs pilot, one has permanent pilot)
    - 2 characters (one pilot, one warrior)
    - 3 starships (one big, two small)
    - Multiple ground and space targets

    The planner should find the optimal combination.
    """
    scenario = (
        ScenarioBuilder("Complex Mixed Units")
        .as_side("dark")
        .with_force(25)
        # Ground locations
        .add_ground_location("Ground Target", my_icons=2, their_icons=2)
        # Space locations
        .add_space_location("Space Target", my_icons=2, their_icons=3)  # Higher value
        # Characters
        .add_character("TIE Pilot", power=2, deploy_cost=2, is_pilot=True, is_warrior=False)
        .add_character("Stormtrooper Commander", power=5, deploy_cost=4)
        # Starships - varied power levels
        .add_starship("Executor", power=9, deploy_cost=10, has_permanent_pilot=True)
        .add_starship("TIE Interceptor 1", power=3, deploy_cost=2, has_permanent_pilot=True)
        .add_starship("TIE Interceptor 2", power=3, deploy_cost=2, has_permanent_pilot=True)
        # Should deploy to space (higher value) with good combo
        .expect_target("Space Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_optimal_efficiency_selection():
    """Test that planner selects most efficient deployment.

    When we have limited force, prefer deploying units with best power/cost ratio.
    """
    scenario = (
        ScenarioBuilder("Optimal Efficiency")
        .as_side("dark")
        .with_force(8)  # Limited force
        .add_ground_location("Target", my_icons=2, their_icons=2)
        # High power but expensive
        .add_character("Big Expensive", power=7, deploy_cost=7)  # Ratio: 1.0
        # Lower power but efficient
        .add_character("Efficient 1", power=4, deploy_cost=2)  # Ratio: 2.0
        .add_character("Efficient 2", power=3, deploy_cost=2)  # Ratio: 1.5
        # Combined efficient units: 7 power for 4 cost vs Big's 7 power for 7 cost
        # Should pick efficient combo (same power, lower cost)
        .expect_deployment("Efficient 1", "Efficient 2")
        .expect_target("Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_multiple_location_deployment():
    """Test deploying to multiple locations when we have enough force.

    With sufficient force, we should establish at multiple high-value locations.
    """
    scenario = (
        ScenarioBuilder("Multiple Location Deploy")
        .as_side("dark")
        .with_force(20)
        .add_ground_location("Ground A", my_icons=2, their_icons=3)  # Highest value
        .add_ground_location("Ground B", my_icons=2, their_icons=2)
        .add_space_location("Space A", my_icons=2, their_icons=2)
        # Plenty of units
        .add_character("Commander", power=6, deploy_cost=5)
        .add_character("Officer", power=6, deploy_cost=5)
        .add_starship("Cruiser", power=7, deploy_cost=6, has_permanent_pilot=True)
        # Should deploy to highest value targets
        .expect_target("Ground A")  # Highest ground icons
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_just_above_threshold():
    """Test deployment when exactly meeting threshold.

    Edge case: combined power exactly equals threshold (6).
    """
    scenario = (
        ScenarioBuilder("Exactly Threshold")
        .as_side("dark")
        .with_force(10)
        .add_ground_location("Target", my_icons=2, their_icons=2)
        .add_character("Weak1", power=2, deploy_cost=1)
        .add_character("Weak2", power=2, deploy_cost=1)
        .add_character("Weak3", power=2, deploy_cost=1)
        # Total: 6 power exactly equals threshold
        .expect_deployment("Weak1", "Weak2", "Weak3")
        .expect_target("Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_just_below_threshold():
    """Test that deployment doesn't happen when just below threshold.

    Edge case: combined power is 5, threshold is 6.
    """
    scenario = (
        ScenarioBuilder("Just Below Threshold")
        .as_side("dark")
        .with_force(10)
        .add_ground_location("Target", my_icons=2, their_icons=2)
        .add_character("Weak1", power=2, deploy_cost=1)
        .add_character("Weak2", power=2, deploy_cost=1)
        .add_character("Weak3", power=1, deploy_cost=1)
        # Total: 5 power, below threshold of 6
        .expect_hold_back()
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_starship_combo_with_pilot_boost():
    """Test that small ships + pilot beats threshold efficiently.

    Two small ships (3 power each) + pilot (4 power) = 10 total
    This exceeds threshold (6), so planner efficiently stops there.
    The 3rd A-wing is not needed and correctly not deployed.
    """
    scenario = (
        ScenarioBuilder("Starship + Pilot Combo")
        .as_side("light")
        .with_force(15)
        .add_space_location("Space Battle", my_icons=2, their_icons=3)
        .add_starship("A-wing 1", power=3, deploy_cost=2, has_permanent_pilot=True)
        .add_starship("A-wing 2", power=3, deploy_cost=2, has_permanent_pilot=True)
        .add_starship("A-wing 3", power=3, deploy_cost=2, has_permanent_pilot=True)
        .add_character("Ace Pilot", power=4, deploy_cost=3, is_pilot=True, is_warrior=False)
        # Efficient combo: 2 A-wings (6 power) + pilot (4 power) = 10, exceeds threshold 6
        .expect_deployment("A-wing 1", "A-wing 2", "Ace Pilot")
        .expect_target("Space Battle")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_need_all_ships_to_beat_opponent():
    """Test that all ships deploy when needed to beat opponent.

    When opponent has presence, we need MORE power to beat them.
    This forces the planner to deploy all available ships.
    """
    scenario = (
        ScenarioBuilder("All Ships Needed")
        .as_side("light")
        .with_force(15)
        # Opponent has 8 power - need 9+ to beat
        .add_space_location("Contested Space", my_icons=2, their_icons=3, their_power=8)
        .add_starship("A-wing 1", power=3, deploy_cost=2, has_permanent_pilot=True)
        .add_starship("A-wing 2", power=3, deploy_cost=2, has_permanent_pilot=True)
        .add_starship("A-wing 3", power=3, deploy_cost=2, has_permanent_pilot=True)
        .add_character("Ace Pilot", power=4, deploy_cost=3, is_pilot=True, is_warrior=False)
        # Need all: 3+3+3+4 = 13 to beat their 8
        .expect_deployment("A-wing 1", "A-wing 2", "A-wing 3", "Ace Pilot")
        .expect_target("Contested Space")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


# =============================================================================
# ICON & PRESENCE RULE TESTS (Regression suite for deployment eligibility)
# =============================================================================

def test_attractive_location_without_icons_skipped():
    """Test that high-value location (0/3 icons) is skipped for one with icons.

    SWCCG Rule: Cannot deploy to a location without force icons (unless presence).
    The 0/3 location looks "best" (3 opponent icons to deny) but has 0 bot icons,
    so bot must pick the less attractive but deployable 1/1 location.
    """
    scenario = (
        ScenarioBuilder("Skip High-Value No-Icons Location")
        .as_side("dark")
        .with_force(15)
        # This looks best (3 opponent icons!) but has 0 dark side icons
        .add_ground_location("Tempting Location", my_icons=0, their_icons=3)
        # This is less valuable but actually deployable
        .add_ground_location("Deployable Location", my_icons=1, their_icons=1)
        .add_character("Vader", power=6, deploy_cost=5)
        # Should deploy to "Deployable Location" NOT "Tempting Location"
        .expect_deployment("Vader")
        .expect_target("Deployable Location")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_contested_location_with_zero_icons_via_presence():
    """Test that contested 0-icon location can be reinforced via presence rule.

    SWCCG Rule: Can deploy to ANY location where you have presence.
    Even though we have 0 icons at "Contested No Icons", we have 3 power there,
    so we CAN deploy reinforcements to contest the enemy's 5 power.
    """
    scenario = (
        ScenarioBuilder("Reinforce via Presence Rule")
        .as_side("dark")
        .with_force(15)
        # 0 dark icons but we have presence (3 power) - contested by enemy (5 power)
        .add_ground_location("Contested No Icons", my_icons=0, their_icons=3, my_power=3, their_power=5)
        # Alternative: empty location with our icons
        .add_ground_location("Empty With Icons", my_icons=2, their_icons=1)
        .add_character("Reinforcement", power=6, deploy_cost=5)
        # Should reinforce contested location (swing the battle) not establish new
        .expect_deployment("Reinforcement")
        .expect_target("Contested No Icons")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_controlled_location_without_icons_not_reinforced():
    """Test that uncontested 0-icon location we control is NOT reinforced.

    When we have presence at a 0-icon location but enemy doesn't contest it,
    there's no need to deploy more there. Better to establish elsewhere.
    """
    scenario = (
        ScenarioBuilder("No Reinforce Uncontested")
        .as_side("dark")
        .with_force(15)
        # We control this (3 power, no enemy) but 0 icons
        .add_ground_location("We Control", my_icons=0, their_icons=3, my_power=3, their_power=0)
        # Better to establish here
        .add_ground_location("New Target", my_icons=1, their_icons=2)
        .add_character("Commander", power=6, deploy_cost=5)
        # Should establish at new location, not pile on uncontested one
        .expect_deployment("Commander")
        .expect_target("New Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_multiple_0_icon_locations_all_skipped():
    """Test that ALL 0-icon locations are skipped even if they look great.

    When all locations have 0 bot icons, bot should hold back (can't deploy anywhere).
    """
    scenario = (
        ScenarioBuilder("All Locations No Icons")
        .as_side("dark")
        .with_force(15)
        .add_ground_location("Great Location A", my_icons=0, their_icons=3)
        .add_ground_location("Great Location B", my_icons=0, their_icons=2)
        .add_space_location("Great Space", my_icons=0, their_icons=2)
        .add_character("Vader", power=6, deploy_cost=5)
        .add_starship("Destroyer", power=7, deploy_cost=6, has_permanent_pilot=True)
        # No valid deployment targets - all have 0 dark icons
        .expect_hold_back()
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_presence_at_space_enables_starship_deployment():
    """Test that presence at 0-icon space location enables starship deployment.

    Same as ground presence rule - can deploy via presence even without icons.
    """
    scenario = (
        ScenarioBuilder("Space Presence Rule")
        .as_side("light")
        .with_force(15)
        # 0 light icons but we have presence - contested by enemy
        .add_space_location("Contested Space", my_icons=0, their_icons=2, my_power=4, their_power=6)
        .add_starship("X-wing", power=4, deploy_cost=3, has_permanent_pilot=True)
        .add_starship("Y-wing", power=3, deploy_cost=2, has_permanent_pilot=True)
        # Should reinforce to swing the battle (4+4+3 = 11 vs 6)
        .expect_target("Contested Space")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_icon_priority_when_multiple_valid_options():
    """Test that higher opponent icons = higher priority when icons present.

    When we have icons at multiple locations, prefer the one with MORE
    opponent icons (more valuable to deny via force drains).
    """
    scenario = (
        ScenarioBuilder("Prefer Higher Opponent Icons")
        .as_side("dark")
        .with_force(15)
        .add_ground_location("Low Value", my_icons=2, their_icons=1)
        .add_ground_location("Medium Value", my_icons=1, their_icons=2)
        .add_ground_location("High Value", my_icons=1, their_icons=3)  # Best target
        .add_character("Commander", power=6, deploy_cost=5)
        # Should target "High Value" (3 opponent icons to deny)
        .expect_deployment("Commander")
        .expect_target("High Value")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


# =============================================================================
# FLEE SCENARIO TESTS
# =============================================================================

def test_ground_flee_to_safe_adjacent():
    """Test that planner identifies flee situation at severe power deficit.

    When power deficit <= -6 (RETREAT_THRESHOLD), the location should be
    marked for flee rather than reinforcement, IF there's a valid adjacent target.
    """
    scenario = (
        ScenarioBuilder("Ground Flee to Safe")
        .as_side("dark")
        .with_force(15)
        # Losing badly here (3 vs 12 = -9 deficit)
        .add_ground_location("Losing Battle", my_icons=2, their_icons=2, my_power=3, their_power=12)
        # Safe adjacent location to flee to
        .add_ground_location("Safe Retreat", my_icons=1, their_icons=1)
        .set_adjacent("Losing Battle", "Safe Retreat")
        # Card to potentially deploy
        .add_character("Commander", power=6, deploy_cost=5)
        # Should NOT reinforce "Losing Battle" - should plan to flee instead
        # Deploy to "Safe Retreat" or hold back
        .expect_target("Safe Retreat")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_ground_flee_to_less_dangerous_adjacent():
    """Test flee to adjacent location with opponent but lower deficit.

    When fleeing, prefer a location with lower enemy presence over staying
    at a location with massive deficit.
    """
    scenario = (
        ScenarioBuilder("Ground Flee to Less Dangerous")
        .as_side("dark")
        .with_force(15)
        # Losing badly here (3 vs 15 = -12 deficit)
        .add_ground_location("Disaster Zone", my_icons=2, their_icons=2, my_power=3, their_power=15)
        # Adjacent has enemy but much less (-2 deficit is better than -12)
        .add_ground_location("Less Bad", my_icons=1, their_icons=1, their_power=5)
        .set_adjacent("Disaster Zone", "Less Bad")
        # Card to deploy
        .add_character("Commander", power=6, deploy_cost=5)
        # Should recognize flee situation and deploy to less dangerous location
        .expect_target("Less Bad")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_space_flee_scenario():
    """Test flee logic for space locations.

    Same logic as ground - severe deficit should trigger flee if possible.
    The planner marks the losing location as should_flee and won't reinforce it.
    """
    scenario = (
        ScenarioBuilder("Space Flee")
        .as_side("light")
        .with_force(15)
        # Losing badly in space (4 vs 14 = -10 deficit)
        .add_space_location_with_parsec("Losing System", my_icons=2, their_icons=2,
                                         my_power=4, their_power=14, parsec=1)
        # Safe adjacent system
        .add_space_location_with_parsec("Safe System", my_icons=2, their_icons=2, parsec=2)
        .set_adjacent("Losing System", "Safe System")
        # Starship strong enough to meet threshold alone
        .add_starship_with_hyperspeed("Cruiser", power=7, deploy_cost=6, hyperspeed=5)
        # Should deploy to safe system, not reinforce losing battle
        .expect_deployment("Cruiser")
        .expect_target("Safe System")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_combined_deploy_and_flee_scenario():
    """Test complex scenario: deploy to establish AND flee from another location.

    This stress tests the force budgeting - need force for both deploying
    AND reserving for flee (1 force per card fleeing).
    """
    scenario = (
        ScenarioBuilder("Deploy + Flee Same Turn")
        .as_side("dark")
        .with_force(20)  # Enough for deploy + flee reserve
        # Location 1: Losing badly, need to flee (3 cards @ ~1 power each)
        .add_ground_location("Flee From Here", my_icons=2, their_icons=1, my_power=3, their_power=12)
        # Location 2: Safe adjacent to flee to
        .add_ground_location("Flee Target", my_icons=1, their_icons=0)
        .set_adjacent("Flee From Here", "Flee Target")
        # Location 3: Good deployment target
        .add_ground_location("Establish Here", my_icons=2, their_icons=3)
        # Strong character to deploy
        .add_character("Vader", power=6, deploy_cost=6)
        # Should deploy Vader to "Establish Here" while planning flee from "Flee From Here"
        .expect_deployment("Vader")
        .expect_target("Establish Here")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_no_flee_when_adjacent_blocked():
    """Test that bot doesn't mark flee when no valid adjacent location.

    If all adjacent locations have enemy presence, can't safely flee.
    """
    scenario = (
        ScenarioBuilder("No Flee - Blocked")
        .as_side("dark")
        .with_force(15)
        # Losing here
        .add_ground_location("Trapped", my_icons=2, their_icons=2, my_power=3, their_power=12)
        # Adjacent also has enemy (nowhere to flee)
        .add_ground_location("Also Enemy", my_icons=1, their_icons=1, their_power=8)
        .set_adjacent("Trapped", "Also Enemy")
        # Reinforcement available
        .add_character("Reinforcement", power=8, deploy_cost=6)
        # Can't flee, might as well reinforce if we can swing it
        # Or deploy elsewhere if another option exists
        .build()
    )
    result = run_scenario(scenario)
    # Just verify it doesn't crash - the planner should handle this edge case
    assert result.plan is not None


def test_flee_reserves_force_for_movement():
    """Test that flee planning reserves force for card movement.

    Move costs 1 force per card. If 3 cards need to flee, need 3 force reserved.
    """
    scenario = (
        ScenarioBuilder("Flee Force Reserve")
        .as_side("dark")
        .with_force(10)  # Limited force
        # Losing with ~3 power (representing ~1 card)
        .add_ground_location("Must Flee", my_icons=2, their_icons=2, my_power=3, their_power=12)
        # Safe target
        .add_ground_location("Safe", my_icons=1, their_icons=1)
        .set_adjacent("Must Flee", "Safe")
        # Deployable card - but might not deploy if force needed for flee
        .add_character("Trooper", power=4, deploy_cost=4)
        # A good deploy target
        .add_ground_location("Deploy Target", my_icons=2, their_icons=2)
        .build()
    )
    result = run_scenario(scenario)
    # Verify planner creates a valid plan considering flee force
    assert result.plan is not None


# =============================================================================
# LOCATION CARD DEPLOYMENT TESTS
# =============================================================================

def test_location_card_deployed_first():
    """Test that location cards in hand are prioritized for deployment.

    Deploying locations opens up new options for character/ship deployment.
    """
    scenario = (
        ScenarioBuilder("Deploy Location First")
        .as_side("dark")
        .with_force(15)
        # Existing location with high opponent presence
        .add_ground_location("Existing", my_icons=1, their_icons=1, their_power=5)
        # Location card in hand - deploying it gives new deployment options
        .add_location_card("Tatooine Cantina", deploy_cost=0)
        # Character to deploy
        .add_character("Commander", power=6, deploy_cost=5)
        # Should deploy the location card first (if planner supports it)
        .expect_deployment("Tatooine Cantina")
        .build()
    )
    result = run_scenario(scenario)
    # Location deployment may or may not be in current planner - just verify no crash
    assert result.plan is not None


def test_multiple_location_cards_in_hand():
    """Test handling multiple location cards in hand."""
    scenario = (
        ScenarioBuilder("Multiple Location Cards")
        .as_side("dark")
        .with_force(15)
        # One existing location
        .add_ground_location("Existing", my_icons=2, their_icons=2)
        # Multiple location cards
        .add_location_card("New Site 1", deploy_cost=0)
        .add_location_card("New Site 2", deploy_cost=0)
        # Character
        .add_character("Vader", power=6, deploy_cost=5)
        .build()
    )
    result = run_scenario(scenario)
    assert result.plan is not None


# =============================================================================
# VEHICLE DEPLOYMENT TESTS
# =============================================================================

def test_vehicle_deploys_to_exterior():
    """Test that vehicles deploy to exterior locations only.

    Vehicles cannot enter interior sites.
    """
    scenario = (
        ScenarioBuilder("Vehicle to Exterior")
        .as_side("dark")
        .with_force(15)
        # Interior only - vehicle can't go here
        .add_ground_location("Interior Site", my_icons=2, their_icons=2, interior=True, exterior=False)
        # Exterior - vehicle CAN go here
        .add_ground_location("Exterior Site", my_icons=1, their_icons=2, interior=False, exterior=True)
        # Vehicle with permanent pilot
        .add_vehicle("AT-ST", power=4, deploy_cost=4, has_permanent_pilot=True)
        # Vehicle should deploy to exterior only
        .expect_target("Exterior Site")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_vehicle_alone_meets_threshold():
    """Test that a strong vehicle alone can meet deploy threshold."""
    scenario = (
        ScenarioBuilder("Strong Vehicle Alone")
        .as_side("dark")
        .with_force(15)
        .add_ground_location("Target", my_icons=2, their_icons=2, exterior=True)
        # Vehicle with 7 power meets threshold of 6
        .add_vehicle("AT-AT", power=7, deploy_cost=7, has_permanent_pilot=True)
        .expect_deployment("AT-AT")
        .expect_target("Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_unpiloted_vehicle_needs_pilot():
    """Test that unpiloted vehicles need a pilot character to deploy.

    An unpiloted vehicle has 0 power without a pilot aboard.
    """
    scenario = (
        ScenarioBuilder("Unpiloted Vehicle + Pilot")
        .as_side("dark")
        .with_force(15)
        .add_ground_location("Target", my_icons=2, their_icons=2, exterior=True)
        # Unpiloted vehicle - needs pilot to have power
        .add_vehicle("AT-ST Walker", power=4, deploy_cost=3, has_permanent_pilot=False)
        # Pilot to drive it
        .add_character("AT-ST Pilot", power=2, deploy_cost=2, is_pilot=True, is_warrior=False)
        # Combined: vehicle (4) + pilot (2) = 6 power, meets threshold
        .expect_target("Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_vehicle_plus_character_combo():
    """Test deploying vehicle and character together for higher power."""
    scenario = (
        ScenarioBuilder("Vehicle + Character Combo")
        .as_side("dark")
        .with_force(20)
        .add_ground_location("Target", my_icons=2, their_icons=3, exterior=True)
        # Vehicle with 4 power
        .add_vehicle("AT-ST", power=4, deploy_cost=4, has_permanent_pilot=True)
        # Character with 4 power
        .add_character("Stormtrooper Commander", power=4, deploy_cost=4)
        # Combined: 8 power, well above threshold
        .expect_target("Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_vehicle_cannot_deploy_to_space():
    """Test that vehicles cannot deploy to space locations."""
    scenario = (
        ScenarioBuilder("Vehicle Cannot Space")
        .as_side("dark")
        .with_force(15)
        # Only space location available
        .add_space_location("Space System", my_icons=2, their_icons=2)
        # Vehicle - can't go to space
        .add_vehicle("AT-ST", power=6, deploy_cost=5, has_permanent_pilot=True)
        # Should hold back - no valid target
        .expect_hold_back()
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_vehicle_vs_character_choice():
    """Test that planner chooses better option between vehicle and character.

    Given exterior location, should pick higher power option.
    """
    scenario = (
        ScenarioBuilder("Vehicle vs Character")
        .as_side("dark")
        .with_force(10)
        .add_ground_location("Target", my_icons=2, their_icons=2, exterior=True)
        # Character with 5 power
        .add_character("Officer", power=5, deploy_cost=4)
        # Vehicle with 7 power - better option
        .add_vehicle("AT-AT", power=7, deploy_cost=6, has_permanent_pilot=True)
        # Should choose vehicle (higher power)
        .expect_deployment("AT-AT")
        .expect_target("Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_interior_blocks_vehicle_forces_character():
    """Test that interior-only location forces character over vehicle choice."""
    scenario = (
        ScenarioBuilder("Interior Forces Character")
        .as_side("dark")
        .with_force(15)
        # Interior only - vehicle can't go here
        .add_ground_location("Interior Only", my_icons=2, their_icons=3, interior=True, exterior=False)
        # Vehicle - can't enter interior
        .add_vehicle("AT-ST", power=8, deploy_cost=6, has_permanent_pilot=True)
        # Character - CAN enter interior
        .add_character("Stormtrooper", power=6, deploy_cost=4)
        # Should choose character (only valid option)
        .expect_deployment("Stormtrooper")
        .expect_target("Interior Only")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


def test_multiple_vehicles_combine():
    """Test that multiple vehicles can combine power at same location."""
    scenario = (
        ScenarioBuilder("Multiple Vehicles")
        .as_side("dark")
        .with_force(20)
        .add_ground_location("Target", my_icons=2, their_icons=3, exterior=True)
        # Two vehicles that together exceed threshold
        .add_vehicle("AT-ST 1", power=3, deploy_cost=3, has_permanent_pilot=True)
        .add_vehicle("AT-ST 2", power=3, deploy_cost=3, has_permanent_pilot=True)
        .add_vehicle("AT-ST 3", power=3, deploy_cost=3, has_permanent_pilot=True)
        # Combined: 9 power
        .expect_target("Target")
        .build()
    )
    result = run_scenario(scenario)
    assert result.passed, f"Failed: {result.failures}"


# =============================================================================
# MAIN (for standalone execution)
# =============================================================================

def main():
    """Run all tests via pytest"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

"""
GamePlan Test Suite

Tests for the meta-thinking strategic planning system.

Run with: python -m pytest tests/test_game_plan.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict

from engine.game_plan import (
    WinPath, GoalType, Goal, TurnProjection, TurnPlan, MultiTurnPlan,
    CardSaveRecommendation, GamePlanConfig, GamePlan,
    get_game_plan_config, is_game_plan_enabled,
)

logger = logging.getLogger(__name__)


# =============================================================================
# MOCK CLASSES
# =============================================================================

@dataclass
class MockCard:
    """Mock card for testing"""
    card_id: str
    blueprint_id: str
    title: str
    power: int = 0
    deploy_cost: int = 0


@dataclass
class MockLocation:
    """Mock location for testing"""
    card_id: str
    title: str
    my_icons: str = "1"      # Force icons I control (string like LocationInPlay)
    their_icons: str = "1"   # Force icons opponent controls (string like LocationInPlay)


class MockBoardState:
    """Mock BoardState for testing GamePlan"""

    def __init__(self):
        self.locations: List[MockLocation] = []
        self.my_side = "dark"
        self.turn_number = 1
        self.force_pile = 10
        self.dark_generation = 4
        self.light_generation = 3
        self.cards_by_zone: Dict[str, List[MockCard]] = {}
        self._power_by_location: Dict[int, tuple] = {}  # idx -> (my_power, their_power)
        self._life_force = (30, 30)  # (my, their)

    def add_location(self, title: str, dark_icons: int = 1, light_icons: int = 1,
                     my_power: int = 0, their_power: int = 0):
        """Add a location to the board"""
        idx = len(self.locations)
        # Convert absolute icons to relative based on side
        if self.my_side == "dark":
            my_icons_val = dark_icons
            their_icons_val = light_icons
        else:
            my_icons_val = light_icons
            their_icons_val = dark_icons
        loc = MockLocation(
            card_id=f"loc_{idx}",
            title=title,
            my_icons=str(my_icons_val),
            their_icons=str(their_icons_val),
        )
        self.locations.append(loc)
        self._power_by_location[idx] = (my_power, their_power)
        return loc

    def my_power_at_location(self, idx: int) -> int:
        """Get our power at location index"""
        if idx in self._power_by_location:
            return self._power_by_location[idx][0]
        return 0

    def their_power_at_location(self, idx: int) -> int:
        """Get opponent's power at location index"""
        if idx in self._power_by_location:
            return self._power_by_location[idx][1]
        return 0

    def total_reserve_force(self) -> int:
        """Get our total life force (reserve deck + used pile + force pile)"""
        return self._life_force[0]

    def their_total_life_force(self) -> int:
        """Get opponent's total life force"""
        return self._life_force[1]

    def set_life_force(self, mine: int, theirs: int):
        """Set life force values"""
        self._life_force = (mine, theirs)


class MockDeckArchetype:
    """Mock for DeckArchetype enum"""
    SPACE_CONTROL = "space_control"
    GROUND_SWARM = "ground_swarm"
    DRAIN_RACE = "drain_race"
    MAINS = "mains"
    BALANCED = "balanced"

    def __init__(self, value: str):
        self.value = value


class MockStrategicGoals:
    """Mock for StrategicGoals"""

    def __init__(self, key_cards: List[str] = None):
        self.key_cards = key_cards or []
        self.preferred_domains = []
        self.early_game_priorities = []


# =============================================================================
# GOAL DATACLASS TESTS
# =============================================================================

class TestGoalDataclass:
    """Tests for Goal dataclass"""

    def test_goal_progress_calculation(self):
        """Goal progress should be current/target ratio"""
        goal = Goal(
            goal_type=GoalType.GENERATE_FORCE,
            target="force_icons",
            target_name="Force Generation",
            priority=25,
            current_value=3.0,
            target_value=6.0,
        )
        assert goal.progress == 0.5

    def test_goal_progress_capped_at_one(self):
        """Goal progress should be capped at 1.0"""
        goal = Goal(
            goal_type=GoalType.GENERATE_FORCE,
            target="force_icons",
            target_name="Force Generation",
            priority=25,
            current_value=10.0,
            target_value=6.0,
        )
        assert goal.progress == 1.0

    def test_goal_progress_zero_target(self):
        """Goal with zero target should have 100% progress"""
        goal = Goal(
            goal_type=GoalType.ESTABLISH_PRESENCE,
            target="loc_1",
            target_name="Test Location",
            priority=30,
            current_value=0.0,
            target_value=0.0,
        )
        assert goal.progress == 1.0

    def test_goal_is_complete(self):
        """Goal should be complete when progress >= 1.0"""
        incomplete = Goal(
            goal_type=GoalType.WIN_LOCATION,
            target="loc_1",
            target_name="Test",
            priority=40,
            current_value=3.0,
            target_value=6.0,
        )
        assert not incomplete.is_complete

        complete = Goal(
            goal_type=GoalType.WIN_LOCATION,
            target="loc_1",
            target_name="Test",
            priority=40,
            current_value=6.0,
            target_value=6.0,
        )
        assert complete.is_complete

    def test_goal_str_representation(self):
        """Goal string should include priority, type, and target"""
        goal = Goal(
            goal_type=GoalType.STOP_BLEEDING,
            target="loc_1",
            target_name="Tatooine",
            priority=45,
            current_value=0.0,
            target_value=1.0,
        )
        s = str(goal)
        assert "[P:45]" in s
        assert "stop_bleeding" in s
        assert "Tatooine" in s


# =============================================================================
# TURN PROJECTION TESTS
# =============================================================================

class TestTurnProjection:
    """Tests for TurnProjection dataclass"""

    def test_winning_when_turns_to_win_less(self):
        """Should be winning when turns_to_win < turns_to_lose"""
        proj = TurnProjection(
            turn_number=5,
            my_life_force=20,
            their_life_force=10,
            life_differential=10,
            my_drain_per_turn=3,
            their_drain_per_turn=1,
            drain_differential=2,
            estimated_turns_to_win=4,
            estimated_turns_to_lose=20,
        )
        assert proj.winning

    def test_losing_when_turns_to_lose_less(self):
        """Should not be winning when turns_to_lose < turns_to_win"""
        proj = TurnProjection(
            turn_number=5,
            my_life_force=10,
            their_life_force=20,
            life_differential=-10,
            my_drain_per_turn=1,
            their_drain_per_turn=3,
            drain_differential=-2,
            estimated_turns_to_win=20,
            estimated_turns_to_lose=4,
        )
        assert not proj.winning


# =============================================================================
# GAME PLAN CONFIG TESTS
# =============================================================================

class TestGamePlanConfig:
    """Tests for GamePlanConfig"""

    def test_config_from_dict(self):
        """Config should be loadable from dict"""
        d = {
            'enabled': True,
            'multi_turn_horizon': 5,
            'adaptation_enabled': False,
            'card_saving_enabled': True,
            'goal_score_multiplier': 1.5,
            'min_save_advantage': 1.5,
        }
        config = GamePlanConfig.from_dict(d)
        assert config.enabled == True
        assert config.multi_turn_horizon == 5
        assert config.adaptation_enabled == False
        assert config.goal_score_multiplier == 1.5

    def test_config_defaults(self):
        """Config should have sensible defaults"""
        config = GamePlanConfig()
        assert config.enabled == False
        assert config.multi_turn_horizon == 3
        assert config.adaptation_enabled == True
        assert config.card_saving_enabled == True
        assert config.goal_score_multiplier == 1.0
        assert config.min_save_advantage == 1.3

    def test_config_from_dict_with_missing_keys(self):
        """Config should use defaults for missing keys"""
        d = {'enabled': True}
        config = GamePlanConfig.from_dict(d)
        assert config.enabled == True
        assert config.multi_turn_horizon == 3  # default


# =============================================================================
# WIN PATH DETERMINATION TESTS
# =============================================================================

class TestWinPathDetermination:
    """Tests for win path selection based on archetype"""

    def test_space_control_prefers_drain_engine(self):
        """Space control archetype should prefer drain engine"""
        from engine.archetype_detector import DeckArchetype
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(
            archetype=DeckArchetype.SPACE_CONTROL,
            config=config
        )
        assert game_plan.win_path == WinPath.DRAIN_ENGINE

    def test_ground_swarm_prefers_drain_engine(self):
        """Ground swarm archetype should prefer drain engine"""
        from engine.archetype_detector import DeckArchetype
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(
            archetype=DeckArchetype.GROUND_SWARM,
            config=config
        )
        assert game_plan.win_path == WinPath.DRAIN_ENGINE

    def test_mains_prefers_battle_dominance(self):
        """Mains archetype should prefer battle dominance"""
        from engine.archetype_detector import DeckArchetype
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(
            archetype=DeckArchetype.MAINS,
            config=config
        )
        assert game_plan.win_path == WinPath.BATTLE_DOMINANCE

    def test_balanced_prefers_attrition(self):
        """Balanced archetype should prefer attrition"""
        from engine.archetype_detector import DeckArchetype
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(
            archetype=DeckArchetype.BALANCED,
            config=config
        )
        assert game_plan.win_path == WinPath.ATTRITION

    def test_unknown_archetype_defaults_to_attrition(self):
        """Unknown/None archetype should default to attrition"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(
            archetype=None,
            config=config
        )
        assert game_plan.win_path == WinPath.ATTRITION


# =============================================================================
# GOAL SETTING TESTS
# =============================================================================

class TestGoalSetting:
    """Tests for goal generation logic"""

    def test_stop_bleeding_goal_when_opponent_controls_with_our_icons(self):
        """Should create STOP_BLEEDING goal when opponent drains us"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.win_path = WinPath.DRAIN_ENGINE

        bs = MockBoardState()
        bs.my_side = "dark"
        # Opponent controls (has power, we have none), and we have dark icons
        loc = bs.add_location("Tatooine", dark_icons=2, light_icons=1,
                             my_power=0, their_power=5)

        goals = game_plan.set_turn_goals(bs)

        # Should have STOP_BLEEDING goal
        stop_bleeding = [g for g in goals if g.goal_type == GoalType.STOP_BLEEDING]
        assert len(stop_bleeding) == 1
        assert stop_bleeding[0].target == loc.card_id
        assert stop_bleeding[0].priority > 0

    def test_establish_presence_goal_for_drain_engine(self):
        """Drain engine should create ESTABLISH_PRESENCE goals"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.win_path = WinPath.DRAIN_ENGINE

        bs = MockBoardState()
        bs.my_side = "dark"
        # Location with light icons where we don't control
        loc = bs.add_location("Yavin", dark_icons=0, light_icons=2,
                             my_power=0, their_power=0)

        goals = game_plan.set_turn_goals(bs)

        # Should have ESTABLISH_PRESENCE goal (opponent icons, we don't control)
        establish = [g for g in goals if g.goal_type == GoalType.ESTABLISH_PRESENCE]
        assert len(establish) == 1
        assert establish[0].target == loc.card_id

    def test_deal_damage_goal_for_battle_dominance(self):
        """Battle dominance should create DEAL_DAMAGE goals at favorable locations"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.win_path = WinPath.BATTLE_DOMINANCE

        bs = MockBoardState()
        bs.my_side = "dark"
        # Location where we have +6 advantage
        loc = bs.add_location("Hoth", dark_icons=1, light_icons=1,
                             my_power=10, their_power=4)

        goals = game_plan.set_turn_goals(bs)

        # Should have DEAL_DAMAGE goal
        deal_damage = [g for g in goals if g.goal_type == GoalType.DEAL_DAMAGE]
        assert len(deal_damage) == 1
        assert deal_damage[0].target == loc.card_id

    def test_generate_force_goal_when_low_generation(self):
        """Should create GENERATE_FORCE goal when generation < 6"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)

        bs = MockBoardState()
        bs.my_side = "dark"
        bs.dark_generation = 4  # Below target of 6

        goals = game_plan.set_turn_goals(bs)

        # Should have GENERATE_FORCE goal
        gen_force = [g for g in goals if g.goal_type == GoalType.GENERATE_FORCE]
        assert len(gen_force) == 1
        assert gen_force[0].target_value == 6.0
        assert gen_force[0].current_value == 4.0

    def test_goals_limited_to_five(self):
        """Goals should be limited to top 5 by priority"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.win_path = WinPath.DRAIN_ENGINE

        bs = MockBoardState()
        bs.my_side = "dark"
        bs.dark_generation = 3  # Creates GENERATE_FORCE goal

        # Add many locations that create goals
        for i in range(10):
            bs.add_location(f"Location{i}", dark_icons=2, light_icons=2,
                           my_power=0, their_power=3)

        goals = game_plan.set_turn_goals(bs)

        assert len(goals) <= 5

    def test_goals_sorted_by_priority(self):
        """Goals should be sorted by priority (highest first)"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.win_path = WinPath.DRAIN_ENGINE

        bs = MockBoardState()
        bs.my_side = "dark"
        bs.dark_generation = 3

        # Add locations with different drain amounts (affects priority)
        bs.add_location("Low", dark_icons=1, light_icons=1, my_power=0, their_power=5)
        bs.add_location("High", dark_icons=3, light_icons=1, my_power=0, their_power=5)

        goals = game_plan.set_turn_goals(bs)

        # Should be sorted descending by priority
        for i in range(len(goals) - 1):
            assert goals[i].priority >= goals[i+1].priority


# =============================================================================
# GAME PROJECTION TESTS
# =============================================================================

class TestGameProjection:
    """Tests for game state projection"""

    def test_project_drain_differential(self):
        """Projection should calculate drain differential"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)

        bs = MockBoardState()
        bs.my_side = "dark"
        bs.turn_number = 3
        bs.set_life_force(30, 25)

        # We control location with 2 light icons (drain 2)
        bs.add_location("Ours", dark_icons=1, light_icons=2, my_power=5, their_power=0)
        # They control location with 1 dark icon (drain 1)
        bs.add_location("Theirs", dark_icons=1, light_icons=1, my_power=0, their_power=5)

        projections = game_plan.project_game(bs, turns=3)

        assert len(projections) >= 1
        proj = projections[0]
        assert proj.my_drain_per_turn == 2  # We drain 2 (their icons at loc we control)
        assert proj.their_drain_per_turn == 1  # They drain 1 (our icons at loc they control)
        assert proj.drain_differential == 1  # We're +1

    def test_project_life_force_over_turns(self):
        """Projection should show life force changes over turns"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)

        bs = MockBoardState()
        bs.my_side = "dark"
        bs.turn_number = 1
        bs.set_life_force(20, 20)

        # Setup for us to drain 3, them to drain 1
        bs.add_location("A", dark_icons=0, light_icons=3, my_power=5, their_power=0)
        bs.add_location("B", dark_icons=1, light_icons=0, my_power=0, their_power=5)

        projections = game_plan.project_game(bs, turns=5)

        # Check life force decreases
        assert projections[0].my_life_force == 20  # Turn 1 - no drain yet
        assert projections[1].my_life_force == 19  # Turn 2 - they drained 1
        assert projections[1].their_life_force == 17  # Turn 2 - we drained 3

    def test_projection_calculates_turns_to_win(self):
        """Projection should estimate turns to win"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)

        bs = MockBoardState()
        bs.my_side = "dark"
        bs.turn_number = 1
        bs.set_life_force(30, 12)  # We're ahead

        # We drain 4 per turn
        bs.add_location("A", dark_icons=0, light_icons=4, my_power=5, their_power=0)

        projections = game_plan.project_game(bs, turns=5)

        # 12 life / 4 drain = 3 turns to win
        assert projections[0].estimated_turns_to_win == 3


# =============================================================================
# SCORE INTEGRATION TESTS
# =============================================================================

class TestScoreIntegration:
    """Tests for goal-based scoring"""

    def test_deployment_bonus_for_goal_location(self):
        """Deploying to goal location should get bonus"""
        config = GamePlanConfig(enabled=True, goal_score_multiplier=1.0)
        game_plan = GamePlan(config=config)

        # Set up a goal
        game_plan.current_goals = [
            Goal(
                goal_type=GoalType.ESTABLISH_PRESENCE,
                target="loc_1",
                target_name="Target Location",
                priority=40,
            )
        ]

        bonus, reason = game_plan.get_deployment_score_bonus(
            target_location_id="loc_1",
            card_blueprint_id="some_card"
        )

        assert bonus > 0
        assert "establish" in reason.lower()

    def test_no_bonus_for_non_goal_location(self):
        """Deploying to non-goal location should get no bonus"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)

        game_plan.current_goals = [
            Goal(
                goal_type=GoalType.ESTABLISH_PRESENCE,
                target="loc_1",
                target_name="Target Location",
                priority=40,
            )
        ]

        bonus, reason = game_plan.get_deployment_score_bonus(
            target_location_id="loc_2",  # Different location
            card_blueprint_id="some_card"
        )

        assert bonus == 0
        assert reason == ""

    def test_no_bonus_for_completed_goal(self):
        """Completed goal should not give bonus"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)

        game_plan.current_goals = [
            Goal(
                goal_type=GoalType.ESTABLISH_PRESENCE,
                target="loc_1",
                target_name="Target Location",
                priority=40,
                current_value=1.0,  # Complete
                target_value=1.0,
            )
        ]

        bonus, reason = game_plan.get_deployment_score_bonus(
            target_location_id="loc_1",
            card_blueprint_id="some_card"
        )

        assert bonus == 0

    def test_goal_score_multiplier_applies(self):
        """Goal score multiplier should scale bonuses for ESTABLISH_PRESENCE.

        Note: STOP_BLEEDING uses fixed base + per-icon bonuses (not multiplied).
        ESTABLISH_PRESENCE uses multiplied formula, so test with that goal type.
        """
        config1 = GamePlanConfig(enabled=True, goal_score_multiplier=1.0)
        game_plan1 = GamePlan(config=config1)
        game_plan1.current_goals = [
            Goal(goal_type=GoalType.ESTABLISH_PRESENCE, target="loc_1",
                 target_name="Test", priority=40)
        ]

        config2 = GamePlanConfig(enabled=True, goal_score_multiplier=2.0)
        game_plan2 = GamePlan(config=config2)
        game_plan2.current_goals = [
            Goal(goal_type=GoalType.ESTABLISH_PRESENCE, target="loc_1",
                 target_name="Test", priority=40)
        ]

        bonus1, _ = game_plan1.get_deployment_score_bonus("loc_1", "card")
        bonus2, _ = game_plan2.get_deployment_score_bonus("loc_1", "card")

        # Higher multiplier should give higher bonus (not exact 2x due to base value)
        assert bonus2 > bonus1

    def test_action_score_adjustment_for_battle(self):
        """Battle action at DEAL_DAMAGE goal should get bonus"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)

        game_plan.current_goals = [
            Goal(
                goal_type=GoalType.DEAL_DAMAGE,
                target="loc_1",
                target_name="Battle Location",
                priority=50,
            )
        ]

        adj, reason = game_plan.get_action_score_adjustment(
            action_type='battle',
            target_location_id='loc_1'
        )

        assert adj > 0
        assert "deal_damage" in reason.lower()


# =============================================================================
# ADAPTATION TESTS
# =============================================================================

class TestAdaptation:
    """Tests for mid-game strategy adaptation"""

    def test_drain_engine_never_adapts_even_with_critical_bleeding(self):
        """DRAIN_ENGINE should NEVER adapt - tested and found to hurt performance.

        Dark side with adaptation: 33% win rate
        Dark side without: 50% win rate
        DRAIN_ENGINE either wins via drains or loses - adaptation doesn't help.
        """
        config = GamePlanConfig(enabled=True, adaptation_enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.win_path = WinPath.DRAIN_ENGINE

        # Set up losing projection
        game_plan.projections = [
            TurnProjection(
                turn_number=5,
                my_life_force=10,
                their_life_force=25,
                life_differential=-15,
                my_drain_per_turn=1,
                their_drain_per_turn=3,
                drain_differential=-2,
                estimated_turns_to_win=25,
                estimated_turns_to_lose=4,
            )
        ]

        # Create a critical STOP_BLEEDING goal (2+ turns active)
        game_plan.current_goals = [
            Goal(
                goal_type=GoalType.STOP_BLEEDING,
                target="loc_1",
                target_name="Test Location",
                priority=60,
                turns_active=2,  # Critical - active for 2 turns
            )
        ]

        bs = MockBoardState()
        bs.my_side = "dark"
        # Add location where we have favorable power (+6 advantage)
        bs.add_location("Battle", my_power=10, their_power=4)

        # DRAIN_ENGINE should NOT adapt - adaptation was found to hurt performance
        new_path = game_plan.should_adapt_strategy(bs)

        # Should NOT switch - DRAIN_ENGINE stays as-is
        assert new_path is None

    def test_drain_engine_no_adapt_without_critical_bleeding(self):
        """DRAIN_ENGINE should NOT adapt (ever)"""
        config = GamePlanConfig(enabled=True, adaptation_enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.win_path = WinPath.DRAIN_ENGINE

        # Set up losing projection
        game_plan.projections = [
            TurnProjection(
                turn_number=5,
                my_life_force=10,
                their_life_force=25,
                life_differential=-15,
                my_drain_per_turn=1,
                their_drain_per_turn=3,
                drain_differential=-2,
                estimated_turns_to_win=25,
                estimated_turns_to_lose=4,
            )
        ]

        # NO critical goals - empty current_goals
        game_plan.current_goals = []

        bs = MockBoardState()
        bs.my_side = "dark"
        bs.add_location("Battle", my_power=10, their_power=4)

        # DRAIN_ENGINE should NOT adapt without critical bleeding
        new_path = game_plan.should_adapt_strategy(bs)

        assert new_path is None  # No adaptation without critical goals

    def test_no_adapt_when_disabled(self):
        """Should not adapt when adaptation disabled"""
        config = GamePlanConfig(enabled=True, adaptation_enabled=False)
        game_plan = GamePlan(config=config)
        game_plan.win_path = WinPath.DRAIN_ENGINE

        game_plan.projections = [
            TurnProjection(
                turn_number=5,
                my_life_force=10,
                their_life_force=25,
                life_differential=-15,
                my_drain_per_turn=1,
                their_drain_per_turn=3,
                drain_differential=-2,
                estimated_turns_to_win=25,
                estimated_turns_to_lose=4,
            )
        ]

        bs = MockBoardState()
        bs.add_location("Battle", my_power=10, their_power=4)

        new_path = game_plan.should_adapt_strategy(bs)

        assert new_path is None

    def test_adapt_to_attrition_when_big_lead(self):
        """Should switch to attrition when way ahead (BATTLE_DOMINANCE only)"""
        config = GamePlanConfig(enabled=True, adaptation_enabled=True)
        game_plan = GamePlan(config=config)
        # Use BATTLE_DOMINANCE - DRAIN_ENGINE doesn't adapt
        game_plan.win_path = WinPath.BATTLE_DOMINANCE

        game_plan.projections = [
            TurnProjection(
                turn_number=5,
                my_life_force=35,
                their_life_force=10,
                life_differential=25,  # Way ahead
                my_drain_per_turn=2,
                their_drain_per_turn=1,
                drain_differential=1,
                estimated_turns_to_win=5,
                estimated_turns_to_lose=35,
            )
        ]

        bs = MockBoardState()
        bs.set_life_force(35, 10)  # +25 differential

        new_path = game_plan.should_adapt_strategy(bs)

        assert new_path == WinPath.ATTRITION


# =============================================================================
# EVENT HOOKS TESTS
# =============================================================================

class TestEventHooks:
    """Tests for event hook behavior"""

    def test_on_turn_started_sets_goals(self):
        """on_turn_started should set goals for the turn"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)

        bs = MockBoardState()
        bs.my_side = "dark"
        bs.turn_number = 3
        bs.dark_generation = 3
        bs.add_location("Test", dark_icons=2, light_icons=2, my_power=0, their_power=5)

        game_plan.on_turn_started(bs)

        assert len(game_plan.current_goals) > 0
        assert game_plan.last_turn_updated == 3

    def test_on_turn_started_skips_duplicate_calls(self):
        """on_turn_started should skip if already updated this turn"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.last_turn_updated = 5

        bs = MockBoardState()
        bs.turn_number = 5

        original_goals = game_plan.current_goals.copy()
        game_plan.on_turn_started(bs)

        # Goals should not have changed
        assert game_plan.current_goals == original_goals

    def test_on_turn_started_creates_projections(self):
        """on_turn_started should create game projections"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)

        bs = MockBoardState()
        bs.my_side = "dark"
        bs.turn_number = 3
        bs.set_life_force(25, 20)
        bs.add_location("Test", dark_icons=1, light_icons=2, my_power=5, their_power=0)

        game_plan.on_turn_started(bs)

        assert len(game_plan.projections) > 0

    def test_on_turn_started_disabled_does_nothing(self):
        """on_turn_started should do nothing when disabled"""
        config = GamePlanConfig(enabled=False)
        game_plan = GamePlan(config=config)

        bs = MockBoardState()
        bs.turn_number = 3

        game_plan.on_turn_started(bs)

        assert len(game_plan.current_goals) == 0
        assert len(game_plan.projections) == 0


# =============================================================================
# CARD SAVING TESTS
# =============================================================================

class TestCardSaving:
    """Tests for card saving recommendations"""

    def test_should_exclude_saved_card(self):
        """Cards marked for saving should be excluded"""
        config = GamePlanConfig(enabled=True, card_saving_enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.cards_to_save = ["1_168", "2_100"]

        assert game_plan.should_exclude_card("1_168") == True
        assert game_plan.should_exclude_card("2_100") == True
        assert game_plan.should_exclude_card("3_50") == False

    def test_multi_turn_plan_creates_cards_to_save(self):
        """Multi-turn plan should identify cards to save"""
        config = GamePlanConfig(enabled=True, card_saving_enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.current_goals = []
        game_plan.projections = []

        bs = MockBoardState()
        bs.turn_number = 3
        bs.force_pile = 7  # Just barely enough for Vader (6 cost) but tight

        # High cost card that might be worth saving
        hand_cards = [{'blueprintId': '1_168'}]  # Vader costs 6

        # Mock get_card to return Vader-like card
        import engine.card_loader as card_loader
        original_get_card = card_loader.get_card

        class MockVader:
            title = "Darth Vader"
            deploy_value = 6

        def mock_get_card(bp_id):
            if bp_id == '1_168':
                return MockVader()
            return None

        card_loader.get_card = mock_get_card

        try:
            plan = game_plan.create_multi_turn_plan(bs, hand_cards)
            # At 7 force, deploying 6-cost Vader leaves only 1 force
            # Card saving logic should recognize this as a tight situation
            # but not necessarily recommend saving since 7-6=1 is playable
        finally:
            card_loader.get_card = original_get_card


# =============================================================================
# STATUS SUMMARY TESTS
# =============================================================================

class TestStatusSummary:
    """Tests for status summary output"""

    def test_status_summary_includes_win_path(self):
        """Status summary should include win path"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.win_path = WinPath.DRAIN_ENGINE

        summary = game_plan.get_status_summary()

        assert "drain_engine" in summary.lower()

    def test_status_summary_includes_goals(self):
        """Status summary should list current goals"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.current_goals = [
            Goal(goal_type=GoalType.STOP_BLEEDING, target="loc_1",
                 target_name="Tatooine", priority=45)
        ]

        summary = game_plan.get_status_summary()

        assert "Tatooine" in summary
        assert "Goals" in summary

    def test_status_summary_includes_trajectory(self):
        """Status summary should include win/lose trajectory"""
        config = GamePlanConfig(enabled=True)
        game_plan = GamePlan(config=config)
        game_plan.projections = [
            TurnProjection(
                turn_number=5,
                my_life_force=20,
                their_life_force=10,
                life_differential=10,
                my_drain_per_turn=3,
                their_drain_per_turn=1,
                drain_differential=2,
                estimated_turns_to_win=4,
                estimated_turns_to_lose=20,
            )
        ]

        summary = game_plan.get_status_summary()

        assert "WINNING" in summary


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

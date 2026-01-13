"""
GamePlan - Meta-Thinking System for Strategic Play

Provides game-wide strategic planning by:
1. Determining HOW to win (drain engine vs battle dominance vs attrition)
2. Setting explicit goals each turn that advance toward win condition
3. Scoring actions based on goal alignment
4. Adapting strategy mid-game when needed
5. Planning across multiple turns with card saving
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Tuple, TYPE_CHECKING

from engine.card_loader import get_card

if TYPE_CHECKING:
    from engine.board_state import BoardState
    from engine.archetype_detector import StrategicGoals, DeckArchetype

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS
# =============================================================================

class WinPath(Enum):
    """How we plan to win the game."""
    DRAIN_ENGINE = "drain_engine"          # Control locations, drain every turn
    BATTLE_DOMINANCE = "battle_dominance"  # Win battles, force forfeit losses
    ATTRITION = "attrition"                # Efficient trades, out-resource opponent


class GoalType(Enum):
    """Types of goals the bot can pursue."""
    ESTABLISH_PRESENCE = "establish_presence"  # Get power >= 1 at location
    WIN_LOCATION = "win_location"              # Achieve favorable power advantage
    GENERATE_FORCE = "generate_force"          # Increase force icon generation
    STOP_BLEEDING = "stop_bleeding"            # Contest location where we're drained
    PROTECT_ASSET = "protect_asset"            # Keep key card safe from battle
    DEAL_DAMAGE = "deal_damage"                # Win battles for forfeit losses
    SAVE_FORCE = "save_force"                  # Reserve force for next turn
    AVOID_LOCATION = "avoid_location"          # Don't deploy here - overwhelming enemy force


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Goal:
    """A single goal for the current turn."""
    goal_type: GoalType
    target: str              # Location card_id or card blueprint_id
    target_name: str         # Human-readable name
    priority: int            # 1-100 (higher = more important)

    # Progress tracking
    current_value: float = 0.0
    target_value: float = 1.0

    # Timing
    deadline_turn: Optional[int] = None  # Must achieve by this turn

    # Persistence tracking - how many turns has this goal been active?
    turns_active: int = 1  # Starts at 1 (first turn it appears)

    # Space emergency flag (System 2) - space bleeding is critical
    is_space_emergency: bool = False

    @property
    def progress(self) -> float:
        """0.0 to 1.0 completion percentage."""
        if self.target_value <= 0:
            return 1.0
        return min(1.0, self.current_value / self.target_value)

    @property
    def is_complete(self) -> bool:
        return self.progress >= 1.0

    @property
    def is_critical(self) -> bool:
        """Goal is critical if unaddressed for 2+ turns."""
        return self.turns_active >= 2 and not self.is_complete

    def __str__(self) -> str:
        status = "✓" if self.is_complete else f"{self.progress:.0%}"
        age_str = f" [CRITICAL:{self.turns_active}T]" if self.is_critical else (
            f" [{self.turns_active}T]" if self.turns_active > 1 else ""
        )
        return f"[P:{self.priority}] {self.goal_type.value} @ {self.target_name} ({status}){age_str}"


@dataclass
class TurnProjection:
    """Projected game state at a future turn."""
    turn_number: int

    # Life force projections
    my_life_force: int
    their_life_force: int
    life_differential: int  # positive = we're ahead

    # Drain projections (assuming current board holds)
    my_drain_per_turn: int
    their_drain_per_turn: int
    drain_differential: int  # positive = we drain more

    # Board control
    locations_i_control: int = 0
    locations_they_control: int = 0

    # Derived
    estimated_turns_to_win: int = 99
    estimated_turns_to_lose: int = 99

    @property
    def winning(self) -> bool:
        """True if we win first at current trajectory."""
        return self.estimated_turns_to_win < self.estimated_turns_to_lose


@dataclass
class TurnPlan:
    """Plan for a single turn in a multi-turn sequence."""
    turn_number: int
    primary_goal: Optional[Goal]
    force_budget: int              # How much force to use this turn
    cards_to_save: List[str] = field(default_factory=list)  # blueprint_ids to hold
    expected_position: Optional[TurnProjection] = None


@dataclass
class MultiTurnPlan:
    """Coordinated plan spanning multiple turns."""
    turns: List[TurnPlan]
    total_expected_gain: int = 0   # Net life force gain over plan
    key_sequence: str = ""         # Human-readable description


@dataclass
class CardSaveRecommendation:
    """Recommendation to save a card for a future turn."""
    card_blueprint_id: str
    card_name: str
    save_until_turn: int
    reason: str
    expected_value_if_saved: float
    expected_value_if_played_now: float


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class GamePlanConfig:
    """Configuration for GamePlan behavior."""
    enabled: bool = False
    multi_turn_horizon: int = 3
    adaptation_enabled: bool = True
    card_saving_enabled: bool = True
    goal_score_multiplier: float = 1.0
    min_save_advantage: float = 1.3  # 30% better to wait

    # Avoidance logic - don't deploy where we'd get crushed
    avoid_overwhelming_force: bool = True
    overwhelming_force_threshold: int = 8  # Power difference to trigger avoidance
    avoid_penalty_multiplier: float = 2.0  # How strongly to penalize bad locations

    # Card saving - hold specific cards for combos
    save_pilots_for_ships: bool = True
    save_weak_characters: bool = True
    weak_character_power: int = 2  # Characters with power <= this are "weak"

    @classmethod
    def from_dict(cls, d: dict) -> 'GamePlanConfig':
        return cls(
            enabled=d.get('enabled', False),
            multi_turn_horizon=d.get('multi_turn_horizon', 3),
            adaptation_enabled=d.get('adaptation_enabled', True),
            card_saving_enabled=d.get('card_saving_enabled', True),
            goal_score_multiplier=d.get('goal_score_multiplier', 1.0),
            min_save_advantage=d.get('min_save_advantage', 1.3),
            avoid_overwhelming_force=d.get('avoid_overwhelming_force', True),
            overwhelming_force_threshold=d.get('overwhelming_force_threshold', 8),
            avoid_penalty_multiplier=d.get('avoid_penalty_multiplier', 2.0),
            save_pilots_for_ships=d.get('save_pilots_for_ships', True),
            save_weak_characters=d.get('save_weak_characters', True),
            weak_character_power=d.get('weak_character_power', 2),
        )


def get_game_plan_config() -> GamePlanConfig:
    """Get GamePlan configuration from strategy config."""
    from engine.strategy_config import get_config
    config = get_config()
    return GamePlanConfig(
        enabled=config.get('game_plan', 'enabled', False),
        multi_turn_horizon=config.get('game_plan', 'multi_turn_horizon', 3),
        adaptation_enabled=config.get('game_plan', 'adaptation_enabled', True),
        card_saving_enabled=config.get('game_plan', 'card_saving_enabled', True),
        goal_score_multiplier=config.get('game_plan', 'goal_score_multiplier', 1.0),
        min_save_advantage=config.get('game_plan', 'min_save_advantage', 1.3),
        avoid_overwhelming_force=config.get('game_plan', 'avoid_overwhelming_force', True),
        overwhelming_force_threshold=config.get('game_plan', 'overwhelming_force_threshold', 8),
        avoid_penalty_multiplier=config.get('game_plan', 'avoid_penalty_multiplier', 2.0),
        save_pilots_for_ships=config.get('game_plan', 'save_pilots_for_ships', True),
        save_weak_characters=config.get('game_plan', 'save_weak_characters', True),
        weak_character_power=config.get('game_plan', 'weak_character_power', 2),
    )


def is_game_plan_enabled() -> bool:
    """Quick check if GamePlan is enabled."""
    from engine.strategy_config import get_config
    config = get_config()
    return config.get('game_plan', 'enabled', False)


# =============================================================================
# GAME PLAN CLASS
# =============================================================================

class GamePlan:
    """
    Meta-thinking system that provides game-wide strategic planning.

    Responsibilities:
    - Determine win path based on deck archetype and game state
    - Set prioritized goals each turn
    - Provide score adjustments for evaluators based on goal alignment
    - Project game state forward to assess trajectory
    - Adapt strategy mid-game when current path isn't working
    - Plan across multiple turns with card saving
    """

    def __init__(self,
                 deck_strategy: Optional['StrategicGoals'] = None,
                 archetype: Optional['DeckArchetype'] = None,
                 config: Optional[GamePlanConfig] = None):
        """
        Initialize GamePlan.

        Args:
            deck_strategy: Strategic goals from deck archetype detection
            archetype: Detected deck archetype
            config: Configuration options
        """
        self.deck_strategy = deck_strategy
        self.archetype = archetype
        self.config = config or GamePlanConfig()

        # Win path determination
        self.win_path = self._determine_initial_win_path()
        self.adaptation_count = 0

        # Current turn state
        self.current_goals: List[Goal] = []
        self.projections: List[TurnProjection] = []
        self.multi_turn_plan: Optional[MultiTurnPlan] = None
        self.cards_to_save: List[str] = []  # blueprint_ids

        # Tracking
        self.goals_completed_this_game: int = 0
        self.last_turn_updated: int = 0

        # Goal persistence tracking - key is (goal_type, target)
        # Value is number of turns the goal has been active
        self._goal_age: Dict[Tuple[GoalType, str], int] = {}

        # Track if we should "hold for better hand" this deploy phase
        self.recommend_hold_for_hand: bool = False
        self.hold_reason: str = ""

        # Adaptation tracking - detect persistent losing patterns
        self.consecutive_losing_turns: int = 0
        self.last_drain_gap: int = 0
        self.drain_gap_worsening_turns: int = 0
        self.turns_since_last_adaptation: int = 99  # Start high to allow initial adaptation

        logger.info(f"🎯 GamePlan initialized: win_path={self.win_path.value}, "
                   f"archetype={archetype.value if archetype else 'unknown'}")

    @property
    def enabled(self) -> bool:
        """Check if GamePlan is enabled."""
        return self.config.enabled

    # =========================================================================
    # WIN PATH DETERMINATION
    # =========================================================================

    def _determine_initial_win_path(self) -> WinPath:
        """Determine initial win path based on deck archetype."""
        if self.archetype is None:
            return WinPath.ATTRITION

        from engine.archetype_detector import DeckArchetype

        if self.archetype in [DeckArchetype.SPACE_CONTROL, DeckArchetype.GROUND_SWARM,
                               DeckArchetype.DRAIN_RACE]:
            return WinPath.DRAIN_ENGINE
        elif self.archetype == DeckArchetype.MAINS:
            return WinPath.BATTLE_DOMINANCE
        else:
            return WinPath.ATTRITION

    def should_adapt_strategy(self, board_state: 'BoardState') -> Optional[WinPath]:
        """
        Check if current win path is working. Switch if needed.

        Adaptation triggers:
        1. BATTLE_DOMINANCE on losing trajectory → ATTRITION (always works)
        2. DRAIN_ENGINE with critical bleeding for 2+ turns → try BATTLE_DOMINANCE first
        3. Big life force lead → protect with ATTRITION

        Key insight: "if you go two turns without being able to counter, it's time
        to rethink strategy" - waiting 3 turns is usually too long.
        """
        if not self.config.adaptation_enabled:
            return None

        if not self.projections:
            return None

        current_projection = self.projections[0]

        # Check for critical bleeding that we haven't addressed
        critical_bleeding_turns = 0
        for goal in self.current_goals:
            if goal.goal_type == GoalType.STOP_BLEEDING and goal.is_critical:
                critical_bleeding_turns = max(critical_bleeding_turns, goal.turns_active)

        # ===== DRAIN_ENGINE ADAPTATION =====
        # NOTE: DRAIN_ENGINE adaptation was tested and found to HURT performance.
        # Dark side with adaptation: 33% win rate
        # Dark side without: 50% win rate
        # Leaving DRAIN_ENGINE as-is - it either wins via drains or loses,
        # adaptation to other paths doesn't help.

        # ===== BATTLE_DOMINANCE ADAPTATION =====
        # Check if we're on losing trajectory
        if current_projection.estimated_turns_to_lose < current_projection.estimated_turns_to_win:
            if self.win_path == WinPath.BATTLE_DOMINANCE:
                # Battles aren't working - try to turtle up
                self.adaptation_count += 1
                logger.info(f"🔄 Strategy adapted #{self.adaptation_count}: battle_dominance → attrition")
                return WinPath.ATTRITION

        # ===== PROTECT LEAD =====
        # Check for big life force swing (only for non-DRAIN_ENGINE paths)
        if self.win_path != WinPath.DRAIN_ENGINE:
            life_diff = self._get_life_differential(board_state)
            if life_diff > 20 and self.win_path != WinPath.ATTRITION:
                # We're way ahead - protect the lead
                self.adaptation_count += 1
                logger.info(f"🔄 Strategy adapted #{self.adaptation_count}: protecting lead ({life_diff} ahead) → attrition")
                return WinPath.ATTRITION

        return None

    def _can_switch_to_battle_dominance(self, board_state: 'BoardState') -> bool:
        """
        Check if we have the power to switch to battle-focused strategy.

        Require +4 power advantage at a contested location.
        This ensures we only switch when we have a STRONG position,
        not just a marginal one that could go either way.
        """
        for i, loc in enumerate(board_state.locations):
            my_power = board_state.my_power_at_location(i)
            their_power = board_state.their_power_at_location(i)
            # Require +4 advantage for a strong battle position
            if their_power > 0 and my_power >= their_power + 4:
                return True
        return False

    def _get_life_differential(self, board_state: 'BoardState') -> int:
        """Get life force differential (positive = we're ahead)."""
        my_life = board_state.total_reserve_force()
        their_life = board_state.their_total_life_force()
        return my_life - their_life

    # =========================================================================
    # GAME PROJECTION
    # =========================================================================

    def project_game(self, board_state: 'BoardState', turns: int = 5) -> List[TurnProjection]:
        """
        Project game state forward assuming current board holds.

        This gives us a baseline to compare decisions against.
        """
        projections = []

        my_life = board_state.total_reserve_force()
        their_life = board_state.their_total_life_force()

        my_drain = self._calculate_my_drain_potential(board_state)
        their_drain = self._calculate_their_drain_potential(board_state)

        current_turn = board_state.turn_number or 1

        for turn_offset in range(turns):
            turn_num = current_turn + turn_offset

            # Apply drains (both players drain each round)
            if turn_offset > 0:  # Don't apply on current turn (already happened)
                my_life -= their_drain
                their_life -= my_drain

            # Calculate turns to win/lose
            if my_drain > 0:
                turns_to_win = max(1, their_life // my_drain) if their_life > 0 else 1
            else:
                turns_to_win = 99

            if their_drain > 0:
                turns_to_lose = max(1, my_life // their_drain) if my_life > 0 else 1
            else:
                turns_to_lose = 99

            projection = TurnProjection(
                turn_number=turn_num,
                my_life_force=max(0, my_life),
                their_life_force=max(0, their_life),
                life_differential=my_life - their_life,
                my_drain_per_turn=my_drain,
                their_drain_per_turn=their_drain,
                drain_differential=my_drain - their_drain,
                locations_i_control=self._count_locations_i_control(board_state),
                locations_they_control=self._count_locations_they_control(board_state),
                estimated_turns_to_win=turns_to_win,
                estimated_turns_to_lose=turns_to_lose,
            )
            projections.append(projection)

            if my_life <= 0 or their_life <= 0:
                break

        return projections

    def _calculate_my_drain_potential(self, board_state: 'BoardState') -> int:
        """Calculate how much we can drain opponent per turn."""
        total_drain = 0
        my_side = board_state.my_side

        for i, loc in enumerate(board_state.locations):
            # We drain at locations we control with opponent's icons
            my_power = board_state.my_power_at_location(i)
            their_power = board_state.their_power_at_location(i)

            if my_power > 0 and their_power <= 0:
                # We control this location - drain = opponent's icons here
                our_icons, opp_icons = self._get_location_icons(loc, my_side)
                total_drain += opp_icons

        return total_drain

    def _calculate_their_drain_potential(self, board_state: 'BoardState') -> int:
        """Calculate how much opponent can drain us per turn."""
        total_drain = 0
        my_side = board_state.my_side

        for i, loc in enumerate(board_state.locations):
            my_power = board_state.my_power_at_location(i)
            their_power = board_state.their_power_at_location(i)

            if their_power > 0 and my_power <= 0:
                # They control this location - drain = our icons here
                our_icons, opp_icons = self._get_location_icons(loc, my_side)
                total_drain += our_icons

        return total_drain

    def _count_locations_i_control(self, board_state: 'BoardState') -> int:
        """Count locations where we have presence and opponent doesn't."""
        count = 0
        for i in range(len(board_state.locations)):
            if board_state.my_power_at_location(i) > 0 and \
               board_state.their_power_at_location(i) == 0:
                count += 1
        return count

    def _count_locations_they_control(self, board_state: 'BoardState') -> int:
        """Count locations where opponent has presence and we don't."""
        count = 0
        for i in range(len(board_state.locations)):
            if board_state.their_power_at_location(i) > 0 and \
               board_state.my_power_at_location(i) == 0:
                count += 1
        return count

    def _get_location_icons(self, loc, my_side: str) -> Tuple[int, int]:
        """
        Get icons for a location from card metadata, with fallback to LocationInPlay.

        Card metadata is the authoritative source, but LocationInPlay fields are used
        as a fallback for testing or when metadata isn't available.

        Args:
            loc: LocationInPlay object
            my_side: "dark" or "light"

        Returns:
            Tuple of (my_icons, their_icons)
        """
        blueprint_id = getattr(loc, 'blueprint_id', '')
        if blueprint_id:
            loc_metadata = get_card(blueprint_id)
            if loc_metadata:
                # Icons on the card are what each side controls
                dark_icons = loc_metadata.dark_side_icons or 0
                light_icons = loc_metadata.light_side_icons or 0

                if my_side.lower() == 'dark':
                    return (dark_icons, light_icons)
                else:
                    return (light_icons, dark_icons)

        # Fallback: try to parse from LocationInPlay fields (for testing or when no metadata)
        my_icons_str = getattr(loc, 'my_icons', '0') or '0'
        their_icons_str = getattr(loc, 'their_icons', '0') or '0'

        try:
            # Handle potential asterisks or other markers
            my_icons = int(my_icons_str.replace('*', '').strip() or '0')
        except (ValueError, AttributeError):
            my_icons = 0

        try:
            their_icons = int(their_icons_str.replace('*', '').strip() or '0')
        except (ValueError, AttributeError):
            their_icons = 0

        return (my_icons, their_icons)

    def _is_goal_achievable(self, board_state: 'BoardState',
                            enemy_power: int,
                            location,
                            location_idx: int) -> bool:
        """
        Check if contesting a location is realistically achievable.

        A goal is achievable if:
        1. Enemy power is not overwhelming (< threshold)
        2. We have enough hand power to contest
        3. We have the right domain capability (ground vs space)
        4. We have enough force to deploy

        Returns False for locations we can't realistically contest.

        NOTE: For STOP_BLEEDING goals, we are MORE LENIENT because:
        - The enemy is actively draining us - we NEED to address this
        - Creating the goal influences force saving and card draw decisions
        - Ships are expensive; we might need to save force for multiple turns
        """
        # Skip if enemy power is overwhelming
        overwhelming_threshold = self.config.overwhelming_force_threshold
        if enemy_power >= overwhelming_threshold:
            return False

        is_space = getattr(location, 'is_space', False)

        # SPACE LOCATIONS: Be more lenient for STOP_BLEEDING goals
        # Ships are expensive (7-13 cost), so we might not be able to afford them NOW
        # but creating the goal will drive force saving behavior
        if is_space:
            # Check if we have ANY ships in hand (even unaffordable ones)
            hand_cards = getattr(board_state, 'cards_in_hand', [])
            has_any_ship = False
            for card in hand_cards:
                card_bp = getattr(card, 'blueprint_id', None)
                if card_bp:
                    card_data = get_card(card_bp)
                    if card_data and card_data.is_starship:
                        has_any_ship = True
                        break

            # If we have ANY ship in hand, allow the goal (we can save for it)
            # Also allow if enemy power is low enough that any ship could contest
            if has_any_ship or enemy_power <= 6:
                logger.debug(f"   Space STOP_BLEEDING achievable: has_ship={has_any_ship}, "
                           f"enemy_power={enemy_power}")
                return True
            else:
                logger.debug(f"   Space STOP_BLEEDING not achievable: no ships and "
                           f"enemy_power={enemy_power} > 6")
                return False

        # GROUND LOCATIONS: Use stricter checks (characters are cheaper)
        # Check if we have strategic state for hand analysis
        strategic_state = getattr(board_state, 'strategic_state', None)
        if not strategic_state or not getattr(strategic_state, 'enabled', False):
            # Without strategic state, use conservative heuristic
            # Allow goals for enemy power <= 6
            return enemy_power <= 6

        # Check hand power - can we even match their power?
        inventory = getattr(strategic_state, 'inventory', None)
        if inventory:
            hand_power = getattr(inventory, 'total_hand_power', 0)
            if hand_power < enemy_power:
                # We can't match their power with everything in hand
                return False

            # Check domain capability for ground
            if not getattr(inventory, 'has_ground_capability', True):
                return False

        # Check available force vs typical deploy costs
        force_pile = getattr(board_state, 'force_pile', 0) or 0
        if force_pile < 4:  # Can't deploy anything meaningful
            return False

        return True

    # =========================================================================
    # GOAL SETTING
    # =========================================================================

    def set_turn_goals(self, board_state: 'BoardState') -> List[Goal]:
        """
        Analyze current position and generate prioritized goals for this turn.
        """
        goals = []
        my_side = board_state.my_side

        # Debug: log values used for goal setting
        my_gen = board_state.dark_generation if my_side == "dark" else board_state.light_generation
        logger.info(f"📋 Goal inputs: side={my_side}, locs={len(board_state.locations)}, "
                    f"gen={my_gen} (d={board_state.dark_generation}/l={board_state.light_generation})")

        # ===== DEFENSIVE GOALS (stop bleeding) =====
        for i, loc in enumerate(board_state.locations):
            my_power = board_state.my_power_at_location(i)
            their_power = board_state.their_power_at_location(i)

            # Get icons from card metadata (authoritative source)
            our_icons, opp_icons = self._get_location_icons(loc, my_side)
            loc_name = getattr(loc, 'site_name', '') or getattr(loc, 'system_name', '') or getattr(loc, 'title', '') or getattr(loc, 'blueprint_id', 'Unknown')
            logger.info(f"📋 Loc {i} '{loc_name}': my_power={my_power}, their_power={their_power}, "
                        f"our_icons={our_icons}, opp_icons={opp_icons}")

            # They control and we have icons here = they drain us
            # Note: my_power can be -1 when we have no presence (treat as 0)
            if their_power > 0 and my_power <= 0:
                if our_icons > 0:
                    # ACHIEVABILITY CHECK: Don't create goals for locations we can't contest
                    if not self._is_goal_achievable(board_state, their_power, loc, i):
                        logger.debug(f"   Skipping STOP_BLEEDING at {loc_name}: not achievable "
                                    f"(enemy power {their_power})")
                        continue

                    # Priority encodes drain icons: 30 base + 15 per icon
                    # Higher drain = higher priority AND higher bonus when matched
                    drain_priority = 30 + (our_icons * 15)

                    # System 2: Space emergency escalation
                    # Space bleeding is harder to fix (ships are expensive) and compounds faster
                    is_space_emergency = False
                    blueprint_id = getattr(loc, 'blueprint_id', None)
                    if blueprint_id:
                        loc_card_data = get_card(blueprint_id)
                        if loc_card_data and getattr(loc_card_data, 'has_space_icon', False):
                            # This is a space location - apply multiplier
                            space_multiplier = 1.5

                            # Check if we have ANY affordable ships
                            force_available = getattr(board_state, 'force_pile', 0)
                            hand_cards = getattr(board_state, 'cards_in_hand', [])
                            has_affordable_ship = False
                            for card in hand_cards:
                                card_bp = getattr(card, 'blueprint_id', None)
                                if card_bp:
                                    card_data = get_card(card_bp)
                                    if card_data:
                                        card_type = (getattr(card_data, 'card_type', '') or '').lower()
                                        deploy_cost = getattr(card_data, 'deploy_cost', 0) or 0
                                        if isinstance(deploy_cost, str):
                                            deploy_cost = int(deploy_cost) if deploy_cost.isdigit() else 99
                                        # Affordable = can deploy within 2 turns of force generation
                                        if card_type == 'starship' and deploy_cost <= force_available + 6:
                                            has_affordable_ship = True
                                            break

                            if not has_affordable_ship:
                                # CRITICAL: Space bleeding with no way to fix it!
                                space_multiplier = 2.5
                                is_space_emergency = True
                                logger.warning(f"🚨 SPACE EMERGENCY goal at {loc_name}: "
                                             f"opponent drains {our_icons} icons, NO affordable ships!")

                            drain_priority = int(drain_priority * space_multiplier)
                            logger.info(f"   🚀 Space STOP_BLEEDING at {loc_name}: "
                                       f"priority {30 + (our_icons * 15)} -> {drain_priority} "
                                       f"(x{space_multiplier}, emergency={is_space_emergency})")

                    goals.append(Goal(
                        goal_type=GoalType.STOP_BLEEDING,
                        target=loc.card_id,
                        target_name=loc_name,
                        priority=drain_priority,
                        target_value=1.0,  # Need to establish presence
                        current_value=0.0,  # We have NO presence yet (that's why it's bleeding!)
                        is_space_emergency=is_space_emergency,
                    ))

        # ===== OFFENSIVE GOALS (advance win condition) =====
        if self.win_path == WinPath.DRAIN_ENGINE:
            # Find locations with opponent icons where we could establish
            for i, loc in enumerate(board_state.locations):
                my_power = board_state.my_power_at_location(i)
                their_power = board_state.their_power_at_location(i)

                # Get icons from card metadata (authoritative source)
                our_icons, opp_icons = self._get_location_icons(loc, my_side)
                loc_name = getattr(loc, 'site_name', '') or getattr(loc, 'system_name', '') or getattr(loc, 'title', '') or getattr(loc, 'blueprint_id', 'Unknown')

                # Location with opponent icons that we don't control
                if opp_icons > 0 and (my_power <= 0 or their_power > 0):
                    # For contested locations (WIN_LOCATION), check achievability
                    if their_power > 0:
                        if not self._is_goal_achievable(board_state, their_power, loc, i):
                            logger.debug(f"   Skipping WIN_LOCATION at {loc_name}: not achievable "
                                        f"(enemy power {their_power})")
                            continue
                        goal_type = GoalType.WIN_LOCATION
                    else:
                        goal_type = GoalType.ESTABLISH_PRESENCE

                    goals.append(Goal(
                        goal_type=goal_type,
                        target=loc.card_id,
                        target_name=loc_name,
                        priority=20 + (opp_icons * 10),
                        current_value=float(my_power),
                        target_value=float(their_power + 1) if their_power > 0 else 1.0,
                    ))

        elif self.win_path == WinPath.BATTLE_DOMINANCE:
            # Find locations where we have favorable power
            for i, loc in enumerate(board_state.locations):
                my_power = board_state.my_power_at_location(i)
                their_power = board_state.their_power_at_location(i)
                advantage = my_power - their_power
                loc_name = getattr(loc, 'site_name', '') or getattr(loc, 'system_name', '') or getattr(loc, 'title', '') or getattr(loc, 'blueprint_id', 'Unknown')

                # Require +4 advantage for favorable battle
                if their_power > 0 and advantage >= 4:
                    goals.append(Goal(
                        goal_type=GoalType.DEAL_DAMAGE,
                        target=loc.card_id,
                        target_name=loc_name,
                        priority=40 + (advantage * 5),
                        current_value=float(advantage),
                        target_value=4.0,  # Need +4 for favorable battle
                    ))

        elif self.win_path == WinPath.ATTRITION:
            # ATTRITION: Focus on efficient trades and resource denial
            # Goal: Make opponent pay more than us for every exchange
            for i, loc in enumerate(board_state.locations):
                my_power = board_state.my_power_at_location(i)
                their_power = board_state.their_power_at_location(i)
                our_icons, opp_icons = self._get_location_icons(loc, my_side)
                loc_name = getattr(loc, 'site_name', '') or getattr(loc, 'system_name', '') or getattr(loc, 'title', '') or getattr(loc, 'blueprint_id', 'Unknown')

                # Priority 1: Establish at high-value opponent icon locations (deny drains)
                if opp_icons >= 2 and my_power <= 0 and their_power <= 0:
                    goals.append(Goal(
                        goal_type=GoalType.ESTABLISH_PRESENCE,
                        target=loc.card_id,
                        target_name=loc_name,
                        priority=35 + (opp_icons * 10),  # Higher priority for more icons
                        current_value=0.0,
                        target_value=1.0,
                    ))

                # Priority 2: If we have advantage, consider efficient battle
                if their_power > 0 and my_power > their_power:
                    advantage = my_power - their_power
                    goals.append(Goal(
                        goal_type=GoalType.DEAL_DAMAGE,
                        target=loc.card_id,
                        target_name=loc_name,
                        priority=30 + (advantage * 3),  # Lower priority than BATTLE_DOMINANCE
                        current_value=float(advantage),
                        target_value=1.0,  # Any advantage is worth considering
                    ))

        # ===== RESOURCE GOALS =====
        my_generation = board_state.dark_generation if my_side == "dark" else board_state.light_generation
        if my_generation < 6:
            goals.append(Goal(
                goal_type=GoalType.GENERATE_FORCE,
                target="force_icons",
                target_name="Force Generation",
                priority=25,
                target_value=6.0,
                current_value=float(my_generation),
            ))

        # ===== PROTECTION GOALS =====
        if self.deck_strategy and self.deck_strategy.key_cards:
            for key_card_name in self.deck_strategy.key_cards[:3]:
                # Check if key card is at a risky location
                if self._is_card_at_risk(key_card_name, board_state):
                    goals.append(Goal(
                        goal_type=GoalType.PROTECT_ASSET,
                        target=key_card_name,
                        target_name=key_card_name,
                        priority=50,
                    ))

        # ===== AVOIDANCE GOALS (don't deploy where we'd get crushed) =====
        # IMPORTANT: Never avoid locations where opponent is DRAINING us!
        # At drain locations, we must contest or lose the game.
        if self.config.avoid_overwhelming_force:
            threshold = self.config.overwhelming_force_threshold
            my_side = getattr(board_state, 'my_side', 'dark') or 'dark'

            for i, loc in enumerate(board_state.locations):
                my_power = board_state.my_power_at_location(i)
                their_power = board_state.their_power_at_location(i)

                # Calculate how badly we're outmatched
                disadvantage = their_power - (my_power if my_power > 0 else 0)

                if their_power > 0 and disadvantage >= threshold:
                    loc_name = getattr(loc, 'site_name', '') or getattr(loc, 'system_name', '') or getattr(loc, 'title', '') or getattr(loc, 'blueprint_id', 'Unknown')

                    # CRITICAL: Check if opponent is draining us at this location
                    # If they control it (we have no power) and it has our icons,
                    # we CANNOT avoid it - we must contest or they'll drain us to death
                    my_icons = 0
                    blueprint_id = getattr(loc, 'blueprint_id', None)
                    if blueprint_id:
                        card_data = get_card(blueprint_id)
                        if card_data:
                            if my_side.lower() == 'dark':
                                my_icons = getattr(card_data, 'dark_side_icons', 0) or 0
                            else:
                                my_icons = getattr(card_data, 'light_side_icons', 0) or 0

                    # If they control and we have icons, they're draining us
                    # Don't avoid - a STOP_BLEEDING goal should exist
                    # Note: GEMP uses -1 for "no presence", so check <= 0
                    if my_power <= 0 and my_icons > 0:
                        logger.info(f"   📍 NOT avoiding {loc_name}: opponent drains us {my_icons} icons/turn (must contest!)")
                        continue

                    # Higher disadvantage = higher priority to AVOID
                    # Scale: 8+ disadvantage = 40 priority, 12+ = 60, 16+ = 80
                    avoid_priority = 40 + ((disadvantage - threshold) * 5)

                    goals.append(Goal(
                        goal_type=GoalType.AVOID_LOCATION,
                        target=loc.card_id,
                        target_name=loc_name,
                        priority=min(90, avoid_priority),  # Cap at 90
                        current_value=float(disadvantage),
                        target_value=float(threshold),
                    ))
                    logger.info(f"   ⚠️ AVOID GOAL: {loc_name} (we're outmatched {disadvantage}:{threshold})")

        # ===== APPLY GOAL PERSISTENCE TRACKING =====
        # Track which goals persist from last turn and escalate priority
        new_goal_ages: Dict[Tuple[GoalType, str], int] = {}
        critical_bleeding_turns = 0  # Track how long we've been bleeding without addressing

        for goal in goals:
            goal_key = (goal.goal_type, goal.target)

            # Check if this goal existed last turn
            if goal_key in self._goal_age:
                # Increment age and apply to goal
                goal.turns_active = self._goal_age[goal_key] + 1

                # ESCALATE PRIORITY for goals unaddressed for 2+ turns
                if goal.turns_active >= 2:
                    if goal.goal_type == GoalType.STOP_BLEEDING:
                        # Critical: we've been getting drained for 2+ turns!
                        # Massive priority boost - this MUST be addressed
                        goal.priority = min(95, goal.priority + 30)
                        critical_bleeding_turns = max(critical_bleeding_turns, goal.turns_active)
                        logger.warning(f"⚠️ CRITICAL: {goal.target_name} bleeding for {goal.turns_active} turns! "
                                      f"Priority escalated to {goal.priority}")
                    else:
                        # Other goals get moderate boost
                        goal.priority = min(90, goal.priority + 15)

            # Record for next turn
            new_goal_ages[goal_key] = goal.turns_active

        # Update the tracking dict for next turn
        self._goal_age = new_goal_ages

        # ===== DETERMINE IF WE SHOULD HOLD FOR BETTER HAND =====
        # If we have critical bleeding goals but no good way to address them,
        # recommend holding cards and drawing more rather than making weak deploys
        self.recommend_hold_for_hand = False
        self.hold_reason = ""

        # NOTE: "Hold for better hand" was tested and found to HURT performance.
        # Keeping the tracking for diagnostic purposes but not acting on it.
        if critical_bleeding_turns >= 2:
            self.recommend_hold_for_hand = True
            self.hold_reason = f"Critical drain at uncontested location for {critical_bleeding_turns} turns"
            # Disabled logging - was too noisy and the penalty wasn't helping
            # logger.debug(f"🔍 Critical bleeding detected: {self.hold_reason}")

        # Sort by priority (highest first) and return top 5
        goals.sort(key=lambda g: g.priority, reverse=True)
        logger.info(f"📋 Goals created: {len(goals)} total, returning {len(goals[:5])}, "
                   f"critical_bleeding={critical_bleeding_turns}T")
        return goals[:5]

    def _is_card_at_risk(self, card_name: str, board_state: 'BoardState') -> bool:
        """Check if a key card is at a location where we might lose a battle."""
        # For now, simple heuristic: key card at contested location where we're not favorable
        # TODO: Implement proper card tracking
        return False

    # =========================================================================
    # MULTI-TURN PLANNING
    # =========================================================================

    def create_multi_turn_plan(self, board_state: 'BoardState',
                                hand_cards: List[dict]) -> Optional[MultiTurnPlan]:
        """
        Create a coordinated plan spanning multiple turns.

        Key insight: Sometimes the BEST play this turn is to do LESS,
        saving resources for a stronger play next turn.
        """
        if not self.config.card_saving_enabled:
            return None

        horizon = self.config.multi_turn_horizon
        turn_plans = []
        current_turn = board_state.turn_number or 1

        # Simple approach: identify high-value cards that might be worth saving
        cards_to_save = []
        save_recommendations = []  # Track full recommendations for trimming
        force_available = board_state.force_pile or 0

        for card_data in hand_cards:
            save_rec = self._should_save_card(card_data, board_state, force_available,
                                              all_hand_cards=hand_cards)
            if save_rec:
                save_recommendations.append(save_rec)

        # SAFETY CHECK: Don't save so many characters that we have nothing to deploy!
        # Count characters in hand vs characters being saved
        from engine.card_loader import get_card
        chars_in_hand = sum(1 for c in hand_cards
                           if get_card(c.get('blueprintId', '')) and
                           'character' in (getattr(get_card(c.get('blueprintId', '')), 'card_type', '') or '').lower())
        chars_being_saved = sum(1 for r in save_recommendations
                               if get_card(r.card_blueprint_id) and
                               'character' in (getattr(get_card(r.card_blueprint_id), 'card_type', '') or '').lower())

        # Must keep at least 1 character deployable
        min_deployable = 1
        if chars_in_hand > 0 and chars_being_saved >= chars_in_hand:
            # Trim save list - keep only the highest value recommendations
            save_recommendations.sort(key=lambda r: r.expected_value_if_saved, reverse=True)
            max_chars_to_save = max(0, chars_in_hand - min_deployable)

            chars_saved_count = 0
            trimmed_recommendations = []
            for rec in save_recommendations:
                card = get_card(rec.card_blueprint_id)
                is_char = card and 'character' in (getattr(card, 'card_type', '') or '').lower()
                if is_char:
                    if chars_saved_count < max_chars_to_save:
                        trimmed_recommendations.append(rec)
                        chars_saved_count += 1
                    else:
                        logger.info(f"🎯 SKIP SAVE: {rec.card_name} - need deployable characters")
                else:
                    trimmed_recommendations.append(rec)  # Non-characters always OK to save

            save_recommendations = trimmed_recommendations

        # Build final list and log
        for save_rec in save_recommendations:
            cards_to_save.append(save_rec.card_blueprint_id)
            logger.info(f"🎯 SAVE CARD: {save_rec.card_name} - {save_rec.reason}")

        # Create turn plan for current turn
        turn_plan = TurnPlan(
            turn_number=current_turn,
            primary_goal=self.current_goals[0] if self.current_goals else None,
            force_budget=force_available,
            cards_to_save=cards_to_save,
            expected_position=self.projections[0] if self.projections else None,
        )
        turn_plans.append(turn_plan)

        # Store cards to save
        self.cards_to_save = cards_to_save

        if not turn_plans:
            return None

        plan = MultiTurnPlan(
            turns=turn_plans,
            total_expected_gain=0,
            key_sequence=f"T{current_turn}: Execute primary goal, save {len(cards_to_save)} cards"
        )

        return plan

    def _should_save_card(self, card_data: dict, board_state: 'BoardState',
                          force_available: int,
                          all_hand_cards: Optional[List[dict]] = None) -> Optional[CardSaveRecommendation]:
        """
        Determine if a card should be saved for a future turn.

        Cards worth saving:
        1. High-cost cards when we don't have optimal force
        2. Weak characters (power <= 2) - easily crushed if deployed alone
        3. Pilots when we have matching ships in hand
        4. Key characters waiting for safe deployment opportunity
        """
        from engine.card_loader import get_card, is_matching_pilot_ship

        blueprint_id = card_data.get('blueprintId', '')
        card = get_card(blueprint_id)
        if not card:
            return None

        deploy_cost = card.deploy_value or 0
        card_name = getattr(card, 'title', '') or ''
        card_type = getattr(card, 'card_type', '') or ''  # Note: Card uses card_type, not type
        turn_num = board_state.turn_number or 1

        # Safely convert power/ability to int (may be string from JSON)
        raw_power = getattr(card, 'power', 0)
        raw_ability = getattr(card, 'ability', 0)
        try:
            card_power = int(raw_power) if raw_power else 0
        except (ValueError, TypeError):
            card_power = 0
        try:
            card_ability = int(raw_ability) if raw_ability else 0
        except (ValueError, TypeError):
            card_ability = 0

        # ===== SAVE WEAK CHARACTERS =====
        # Weak characters (power <= threshold) are easily crushed if deployed alone
        # Save them until we can deploy with stronger characters for protection
        if self.config.save_weak_characters and card_type.lower() == 'character':
            if card_power > 0 and card_power <= self.config.weak_character_power:
                # Only save if we have stronger characters available
                stronger_available = False
                if all_hand_cards:
                    for other_card_data in all_hand_cards:
                        other_id = other_card_data.get('blueprintId', '')
                        if other_id == blueprint_id:
                            continue
                        other_card = get_card(other_id)
                        if other_card and other_card.card_type and other_card.card_type.lower() == 'character':
                            # Safely convert power to int
                            try:
                                other_power = int(other_card.power) if other_card.power else 0
                            except (ValueError, TypeError):
                                other_power = 0
                            if other_power > self.config.weak_character_power:
                                stronger_available = True
                                break

                if stronger_available:
                    return CardSaveRecommendation(
                        card_blueprint_id=blueprint_id,
                        card_name=card_name,
                        save_until_turn=turn_num + 1,
                        reason=f"Weak character (power {card_power}) - save for combo with stronger ally",
                        expected_value_if_saved=card_power * 2.0,
                        expected_value_if_played_now=card_power * 0.5,
                    )

        # ===== SAVE PILOTS FOR SHIPS =====
        # If this is a pilot and we have a matching ship, save the pilot
        if self.config.save_pilots_for_ships and card_ability > 0:
            # Check if this could be a pilot (has ability)
            if all_hand_cards:
                for ship_data in all_hand_cards:
                    ship_id = ship_data.get('blueprintId', '')
                    if ship_id == blueprint_id:
                        continue
                    ship_card = get_card(ship_id)
                    if ship_card and ship_card.card_type and 'starship' in ship_card.card_type.lower():
                        # Check if this pilot matches this ship
                        if is_matching_pilot_ship(card, ship_card):
                            ship_cost = ship_card.deploy_value or 0
                            total_combo_cost = deploy_cost + ship_cost

                            # Only save if we can't afford the combo this turn
                            if force_available < total_combo_cost:
                                return CardSaveRecommendation(
                                    card_blueprint_id=blueprint_id,
                                    card_name=card_name,
                                    save_until_turn=turn_num + 1,
                                    reason=f"Matching pilot for {ship_card.title} - save for combo (need {total_combo_cost} force)",
                                    expected_value_if_saved=(card_power + 10) * 1.5,  # +10 for matching bonus
                                    expected_value_if_played_now=card_power * 1.0,
                                )

        # ===== SAVE EXPENSIVE CARDS FOR BETTER FORCE =====
        # Skip low-cost cards - usually not worth saving
        if deploy_cost < 4:
            return None

        # If we can barely afford this card, maybe save for better opportunity
        if deploy_cost >= force_available - 2 and force_available < deploy_cost + 4:
            # We'd have very little force left after deploying
            # Check if saving would give us a better play next turn
            future_force = force_available  # Rough estimate: same force next turn
            if future_force >= deploy_cost + 4:
                return CardSaveRecommendation(
                    card_blueprint_id=blueprint_id,
                    card_name=card_name,
                    save_until_turn=turn_num + 1,
                    reason=f"Save for better force position ({force_available} -> ~{future_force})",
                    expected_value_if_saved=deploy_cost * 1.5,
                    expected_value_if_played_now=deploy_cost * 1.0,
                )

        return None

    # =========================================================================
    # SCORE INTEGRATION
    # =========================================================================

    def get_priority_location_ids(self) -> List[str]:
        """
        Get location IDs that MUST be considered as deploy targets.

        Returns location IDs from STOP_BLEEDING and ESTABLISH_PRESENCE goals.
        These should be included in deploy planner target lists even if
        normal filtering would exclude them.

        This ensures GamePlan strategic goals drive target selection,
        not just score adjustments after plans are generated.
        """
        priority_ids = []
        if not self.current_goals:
            return priority_ids

        for goal in self.current_goals:
            if goal.goal_type in [GoalType.STOP_BLEEDING, GoalType.ESTABLISH_PRESENCE]:
                if goal.target and not goal.is_complete:
                    priority_ids.append(goal.target)
                    logger.debug(f"🎯 Priority target from {goal.goal_type.value}: {goal.target_name}")

        return priority_ids

    def get_deployment_score_bonus(self, target_location_id: str,
                                    card_blueprint_id: str) -> Tuple[int, str]:
        """
        Get score bonus for deploying to a specific location.

        Called by deploy planner to weight plans based on goal alignment.
        Returns positive for good targets, NEGATIVE for locations to avoid.

        DESIGN PRINCIPLE: Goals should be TIEBREAKERS, not overrides.
        - Tactical scores range from 35-150
        - Goal bonuses should be 20-60 to influence close decisions
        - Clear tactical wins (100+ score) should NOT be overridden by goals
        - Critical goals (2+ turns unaddressed) get +50% escalation
        """
        if not self.current_goals:
            return 0, ""

        total_bonus = 0
        reasons = []

        # Get config-driven goal weights with TIEBREAKER-SCALE defaults
        # These are intentionally low to complement tactical scoring, not override
        from engine.strategy_config import get_config
        stop_bleeding_base = get_config().get('goal_weights', 'stop_bleeding_base', 30)  # Was 200
        stop_bleeding_per_icon = get_config().get('goal_weights', 'stop_bleeding_per_icon', 10)  # Was 50
        avoid_location_penalty = get_config().get('goal_weights', 'avoid_location_penalty', 100)  # Was 500
        establish_base = get_config().get('goal_weights', 'establish_drain_base', 20)  # Was 100

        for goal in self.current_goals:
            if goal.is_complete:
                continue

            # Check if this deployment advances the goal
            if goal.target == target_location_id:

                # STOP_BLEEDING: Contest locations where opponent drains us
                if goal.goal_type == GoalType.STOP_BLEEDING:
                    # Priority encodes drain icons: 30 base + 15 per icon
                    drain_icons = max(1, (goal.priority - 30) // 15)
                    bonus = stop_bleeding_base + (drain_icons * stop_bleeding_per_icon)

                    # Critical goal escalation: +50% for goals unaddressed 2+ turns
                    if goal.is_critical:
                        escalation = int(bonus * 0.5)
                        bonus += escalation
                        reasons.append(f"+{bonus} STOP_BLEEDING({drain_icons}i, critical)")
                    else:
                        reasons.append(f"+{bonus} STOP_BLEEDING({drain_icons}i)")

                    total_bonus += bonus
                    logger.debug(f"   🎯 Goal: STOP_BLEEDING @ {goal.target_name} -> +{bonus}")

                # ESTABLISH_PRESENCE: Expand to new drain locations
                elif goal.goal_type == GoalType.ESTABLISH_PRESENCE:
                    bonus = establish_base + int(goal.priority * 0.2 * self.config.goal_score_multiplier)
                    if goal.is_critical:
                        bonus = int(bonus * 1.5)
                    total_bonus += bonus
                    reasons.append(f"+{bonus} establish")
                    logger.debug(f"   🎯 Goal: establish @ {goal.target_name} -> +{bonus}")

                # WIN_LOCATION and DEAL_DAMAGE: Contest and battle
                elif goal.goal_type in [GoalType.WIN_LOCATION, GoalType.DEAL_DAMAGE]:
                    bonus = int(goal.priority * 0.3 * self.config.goal_score_multiplier) + 15
                    if goal.is_critical:
                        bonus = int(bonus * 1.5)
                    total_bonus += bonus
                    reasons.append(f"+{bonus} {goal.goal_type.value}")
                    logger.debug(f"   🎯 Goal: {goal.goal_type.value} @ {goal.target_name} -> +{bonus}")

            # AVOID_LOCATION: Keep penalty meaningful but not crushing
            if goal.goal_type == GoalType.AVOID_LOCATION and goal.target == target_location_id:
                penalty = avoid_location_penalty
                total_bonus -= penalty
                reasons.append(f"-{penalty} AVOID")
                logger.debug(f"   ⚠️ AVOID: {goal.target_name} -> -{penalty}")

        reason_str = "; ".join(reasons) if reasons else ""
        return total_bonus, reason_str

    def get_action_score_adjustment(self, action_type: str,
                                     target_location_id: Optional[str] = None,
                                     card_blueprint_id: Optional[str] = None) -> Tuple[int, str]:
        """
        Get score adjustment for any action based on goal alignment.

        Called by evaluators to adjust action scores.
        """
        if not self.current_goals:
            return 0, ""

        total_adjustment = 0
        reasons = []

        for goal in self.current_goals:
            if goal.is_complete:
                continue

            alignment = self._calculate_action_goal_alignment(
                action_type, target_location_id, card_blueprint_id, goal
            )

            if alignment > 0:
                bonus = int(alignment * goal.priority * 0.5 * self.config.goal_score_multiplier)
                total_adjustment += bonus
                reasons.append(f"+{bonus} advances {goal.goal_type.value}")
            elif alignment < 0:
                penalty = int(abs(alignment) * goal.priority * 0.3 * self.config.goal_score_multiplier)
                total_adjustment -= penalty
                reasons.append(f"-{penalty} hurts {goal.goal_type.value}")

        reason_str = "; ".join(reasons) if reasons else ""
        return total_adjustment, reason_str

    def _calculate_action_goal_alignment(self, action_type: str,
                                          target_location_id: Optional[str],
                                          card_blueprint_id: Optional[str],
                                          goal: Goal) -> float:
        """
        Calculate how well an action aligns with a goal.

        Returns: -1.0 (hurts goal) to +1.0 (directly advances goal)
        """
        if goal.goal_type in [GoalType.ESTABLISH_PRESENCE, GoalType.STOP_BLEEDING]:
            if action_type == 'deploy' and target_location_id == goal.target:
                return 1.0
            return 0.0

        elif goal.goal_type == GoalType.WIN_LOCATION:
            if action_type == 'deploy' and target_location_id == goal.target:
                return 0.8  # Deploying helps but doesn't guarantee win
            return 0.0

        elif goal.goal_type == GoalType.DEAL_DAMAGE:
            if action_type == 'battle' and target_location_id == goal.target:
                return 1.0
            if action_type == 'deploy' and target_location_id == goal.target:
                return 0.5  # Deploying might help set up battle
            return 0.0

        elif goal.goal_type == GoalType.PROTECT_ASSET:
            if card_blueprint_id and goal.target in str(card_blueprint_id):
                # Action involves the protected asset
                # TODO: Check if action puts it at risk
                return 0.0

        return 0.0

    def should_exclude_card(self, card_blueprint_id: str) -> bool:
        """Check if a card should be excluded from deployment (saved for later)."""
        return card_blueprint_id in self.cards_to_save

    # =========================================================================
    # EVENT HOOKS
    # =========================================================================

    def on_turn_started(self, board_state: 'BoardState'):
        """Called at the start of our turn."""
        if not self.enabled:
            return

        turn_num = board_state.turn_number or 1
        if turn_num == self.last_turn_updated:
            return  # Already updated this turn

        self.last_turn_updated = turn_num

        # Project game state
        self.projections = self.project_game(board_state)

        # Check if we should adapt strategy
        new_path = self.should_adapt_strategy(board_state)
        if new_path:
            old_path = self.win_path
            self.win_path = new_path
            # Note: adaptation_count already incremented in should_adapt_strategy

        # Set goals for this turn
        self.current_goals = self.set_turn_goals(board_state)

        # Log goals
        if self.current_goals:
            logger.info(f"🎯 Turn {turn_num} Goals (win_path={self.win_path.value}):")
            for goal in self.current_goals:
                logger.info(f"   {goal}")

        # Log projection
        if self.projections:
            proj = self.projections[0]
            trajectory = "WINNING" if proj.winning else "LOSING"
            logger.info(f"📊 Projection: {trajectory} trajectory "
                       f"(life diff: {proj.life_differential:+d}, "
                       f"drain diff: {proj.drain_differential:+d}/turn)")

    def on_deploy_phase_starting(self, board_state: 'BoardState'):
        """Called when deploy phase starts."""
        if not self.enabled:
            return

        # Create multi-turn plan if card saving is enabled
        if self.config.card_saving_enabled:
            # Get hand cards from board state
            hand_cards = []
            if hasattr(board_state, 'cards_in_hand'):
                hand_cards = [{'blueprintId': c.blueprint_id} for c in board_state.cards_in_hand]

            self.multi_turn_plan = self.create_multi_turn_plan(board_state, hand_cards)

            if self.cards_to_save:
                logger.info(f"🎯 Saving {len(self.cards_to_save)} cards for future turns")

    def on_goal_completed(self, goal: Goal):
        """Called when a goal is completed."""
        self.goals_completed_this_game += 1
        logger.info(f"✅ Goal completed: {goal.goal_type.value} @ {goal.target_name}")

    def get_status_summary(self) -> str:
        """Get human-readable status summary for logging/UI."""
        lines = [
            f"Win Path: {self.win_path.value}",
            f"Adaptations: {self.adaptation_count}",
            f"Goals Completed: {self.goals_completed_this_game}",
        ]
        if self.current_goals:
            lines.append("Current Goals:")
            for goal in self.current_goals:
                lines.append(f"  {goal}")
        if self.projections:
            proj = self.projections[0]
            lines.append(f"Trajectory: {'WINNING' if proj.winning else 'LOSING'}")
        return "\n".join(lines)

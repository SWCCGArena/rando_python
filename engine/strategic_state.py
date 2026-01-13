"""
Strategic State Engine - Real-time game trajectory tracking.

Monitors:
- Card inventory (what types we have vs need)
- Drain trajectory (improving, stable, worsening)
- Opponent strategy detection

Outputs decision mode flags:
- force_draw_mode: Draw aggressively to find missing card types
- force_attack_mode: Prioritize contesting opponent drains over establishing

This enables the bot to make FUNDAMENTALLY different decisions based on
strategic necessity, not just marginal score adjustments.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, TYPE_CHECKING

from engine.strategy_config import get_config
from engine.card_loader import get_card

if TYPE_CHECKING:
    from engine.board_state import BoardState

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIG-DRIVEN PARAMETERS
# =============================================================================

def _get_adaptive_config(key: str, default):
    """Get adaptive strategy config value."""
    return get_config().get('adaptive_strategy', key, default)


def _get_goal_weight_config(key: str, default):
    """Get goal weight config value."""
    return get_config().get('goal_weights', key, default)


def _get_threshold_config(key: str, default):
    """Get threshold adjustment config value."""
    return get_config().get('threshold_adjustments', key, default)


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class StrategicInventory:
    """
    Track what card types we have available to deploy.

    Used to detect when we're missing critical capabilities
    (e.g., no starships = can't contest space).
    """
    characters_in_hand: int = 0
    starships_in_hand: int = 0
    vehicles_in_hand: int = 0
    locations_in_hand: int = 0
    weapons_in_hand: int = 0

    total_hand_power: int = 0
    total_hand_cost: int = 0
    hand_size: int = 0

    # Estimated deck contents (from deck analysis minus drawn/lost)
    estimated_starships_in_deck: int = 0
    estimated_characters_in_deck: int = 0

    # Critical capability flags
    has_space_capability: bool = False  # Can we deploy to space at all?
    has_ground_capability: bool = False  # Can we deploy to ground?

    # What we're missing (triggers draw mode)
    missing_critical_type: Optional[str] = None  # "starships", "characters", etc.


@dataclass
class DrainTrajectory:
    """
    Track drain economy over time to detect winning/losing trends.

    Key insight: If we're losing the drain war and it's getting WORSE,
    we need to fundamentally change strategy, not just make marginal adjustments.
    """
    current_drain_gap: int = 0  # our_drain - their_drain (positive = winning)
    drain_gap_last_turn: int = 0
    drain_gap_trend: str = "stable"  # "improving", "stable", "worsening"
    turns_at_negative: int = 0  # Consecutive turns losing drain war

    # Specific bleed locations (opponent draining us)
    uncontested_opponent_drains: List[str] = field(default_factory=list)
    contestable_drains: List[str] = field(default_factory=list)  # Drains we CAN stop

    # Our drain presence
    our_drain_locations: List[str] = field(default_factory=list)

    # Totals
    our_total_drain: int = 0
    their_total_drain: int = 0


@dataclass
class OpponentStrategy:
    """
    Detected opponent focus based on their board presence.

    Used to decide whether to compete or focus elsewhere.
    """
    primary_domain: str = "unknown"  # "space", "ground", "balanced"
    total_space_power: int = 0
    total_ground_power: int = 0
    uncontested_space_locations: int = 0
    uncontested_ground_locations: int = 0

    # Strongest opponent positions
    strongest_location: Optional[str] = None
    strongest_power: int = 0


@dataclass
class StrategicThresholds:
    """
    Dynamic thresholds that adjust based on urgency.

    When critical: Lower all thresholds to be more aggressive.
    When comfortable: Use normal conservative thresholds.
    """
    deploy_threshold: int = 4
    contest_advantage: int = 2
    goal_multiplier: float = 1.0
    force_reserve: int = 1


# =============================================================================
# MAIN STRATEGIC STATE CLASS
# =============================================================================

class StrategicState:
    """
    Real-time strategic health monitor.

    Updated at key moments:
    - Turn start (reset trajectory tracking)
    - Deploy phase start (analyze inventory and determine mode)
    - Control phase (after drains calculated)

    Key outputs used by other components:
    - force_draw_mode: Tells DrawEvaluator to draw aggressively
    - force_attack_mode: Tells DeployPlanner to prioritize contesting
    - get_strategic_urgency(): Returns "critical", "urgent", or "normal"
    - get_thresholds(): Returns adjusted thresholds for current urgency
    """

    def __init__(self, deck_composition: Optional[Dict] = None):
        self.inventory = StrategicInventory()
        self.trajectory = DrainTrajectory()
        self.opponent = OpponentStrategy()

        # Decision mode flags (KEY OUTPUTS)
        self.force_draw_mode: bool = False
        self.force_attack_mode: bool = False

        # Space emergency tracking (System 2)
        self.space_emergency: bool = False
        self.space_emergency_icons: int = 0  # Total icons bleeding from uncontested space

        # Store deck composition for estimating remaining cards
        self.deck_composition = deck_composition or {}
        self.cards_drawn_or_lost: int = 0

        # Config
        self.enabled = _get_adaptive_config('enabled', True)
        self.attack_mode_drain_threshold = _get_adaptive_config('attack_mode_drain_threshold', -2)
        self.draw_mode_missing_types = _get_adaptive_config('draw_mode_missing_types', True)
        self.critical_drain_turns = _get_adaptive_config('critical_drain_turns', 3)

        logger.info(f"Strategic State initialized: enabled={self.enabled}")

    # =========================================================================
    # INVENTORY ANALYSIS
    # =========================================================================

    def analyze_inventory(self, board_state: 'BoardState') -> None:
        """
        Count what card types we have in hand.

        Sets:
        - inventory counts
        - capability flags
        - missing_critical_type
        - force_draw_mode
        """
        if not self.enabled:
            return

        # Reset counts
        self.inventory.characters_in_hand = 0
        self.inventory.starships_in_hand = 0
        self.inventory.vehicles_in_hand = 0
        self.inventory.locations_in_hand = 0
        self.inventory.weapons_in_hand = 0
        self.inventory.total_hand_power = 0
        self.inventory.total_hand_cost = 0
        self.inventory.hand_size = 0

        # Count cards in hand by type
        hand_cards = getattr(board_state, 'cards_in_hand', [])
        for card in hand_cards:
            self.inventory.hand_size += 1
            blueprint_id = getattr(card, 'blueprint_id', None)
            if not blueprint_id:
                continue

            card_data = get_card(blueprint_id)
            if not card_data:
                continue

            # Card object uses attributes, not dict (card_type, power_value, deploy_cost)
            card_type = (getattr(card_data, 'card_type', '') or '').lower()
            power = getattr(card_data, 'power_value', 0) or 0
            deploy_cost = getattr(card_data, 'deploy_cost', 0) or 0

            # Handle string values
            if isinstance(power, str):
                power = int(power) if power.isdigit() else 0
            if isinstance(deploy_cost, str):
                deploy_cost = int(deploy_cost) if deploy_cost.isdigit() else 0

            self.inventory.total_hand_power += power
            self.inventory.total_hand_cost += deploy_cost

            if card_type == 'character':
                self.inventory.characters_in_hand += 1
            elif card_type == 'starship':
                self.inventory.starships_in_hand += 1
            elif card_type == 'vehicle':
                self.inventory.vehicles_in_hand += 1
            elif card_type == 'location':
                self.inventory.locations_in_hand += 1
            elif card_type == 'weapon':
                self.inventory.weapons_in_hand += 1

        # Estimate deck contents
        self._estimate_deck_contents(board_state)

        # Set capability flags
        self.inventory.has_space_capability = (
            self.inventory.starships_in_hand > 0 or
            self.inventory.estimated_starships_in_deck >= 2
        )
        self.inventory.has_ground_capability = (
            self.inventory.characters_in_hand > 0 or
            self.inventory.estimated_characters_in_deck >= 2
        )

        # Check for missing critical types
        self._check_missing_types(board_state)

        logger.info(f"   Inventory: {self.inventory.characters_in_hand}c, "
                   f"{self.inventory.starships_in_hand}s, "
                   f"{self.inventory.vehicles_in_hand}v, "
                   f"{self.inventory.locations_in_hand}l in hand")
        logger.info(f"   Capabilities: space={self.inventory.has_space_capability}, "
                   f"ground={self.inventory.has_ground_capability}")

    def _estimate_deck_contents(self, board_state: 'BoardState') -> None:
        """Estimate how many of each card type remain in deck."""
        # Use deck composition if available
        if self.deck_composition:
            # Typical deck has ~10 starships, ~15 characters
            original_starships = self.deck_composition.get('starships', 10)
            original_characters = self.deck_composition.get('characters', 15)

            # Subtract cards we've seen (rough estimate)
            cards_seen = self.inventory.hand_size + getattr(board_state, 'lost_pile_count', 0)
            reduction_factor = min(0.5, cards_seen / 60.0)  # Max 50% reduction

            self.inventory.estimated_starships_in_deck = max(
                0, int(original_starships * (1 - reduction_factor)) - self.inventory.starships_in_hand
            )
            self.inventory.estimated_characters_in_deck = max(
                0, int(original_characters * (1 - reduction_factor)) - self.inventory.characters_in_hand
            )
        else:
            # Conservative estimates
            self.inventory.estimated_starships_in_deck = 5
            self.inventory.estimated_characters_in_deck = 8

    def _check_missing_types(self, board_state: 'BoardState') -> None:
        """
        Check if we're missing a critical card type and enable draw mode.

        Priority order:
        1. No starships when opponent controls space -> CRITICAL
        2. No characters when opponent controls ground -> CRITICAL
        3. No cards with power when we need to contest -> URGENT
        """
        self.inventory.missing_critical_type = None
        self.force_draw_mode = False

        if not self.draw_mode_missing_types:
            return

        # Check space capability vs opponent space presence
        if (not self.inventory.has_space_capability and
            self.opponent.uncontested_space_locations > 0):
            self.inventory.missing_critical_type = "starships"
            self.force_draw_mode = True
            logger.warning(f"STRATEGIC: Missing starships, opponent has "
                          f"{self.opponent.uncontested_space_locations} uncontested space locations")

        # Check ground capability vs opponent ground presence
        elif (not self.inventory.has_ground_capability and
              self.opponent.uncontested_ground_locations > 0):
            self.inventory.missing_critical_type = "characters"
            self.force_draw_mode = True
            logger.warning(f"STRATEGIC: Missing characters, opponent has "
                          f"{self.opponent.uncontested_ground_locations} uncontested ground locations")

        # Check for general power deficit
        elif (self.inventory.total_hand_power < 4 and
              len(self.trajectory.contestable_drains) > 0):
            self.inventory.missing_critical_type = "power"
            self.force_draw_mode = True
            logger.warning(f"STRATEGIC: Low hand power ({self.inventory.total_hand_power}), "
                          f"need cards to contest {len(self.trajectory.contestable_drains)} locations")

        # Check for critically low hand with no deployable units
        # This is CRITICAL - if hand < 5 and we have nothing to deploy, we MUST draw
        elif (self.inventory.hand_size < 5 and
              not self.inventory.has_space_capability and
              not self.inventory.has_ground_capability):
            self.inventory.missing_critical_type = "deployable_units"
            self.force_draw_mode = True
            logger.warning(f"STRATEGIC: Hand critically low ({self.inventory.hand_size}) "
                          f"with no deployable units - MUST DRAW!")

    # =========================================================================
    # DRAIN TRAJECTORY TRACKING
    # =========================================================================

    def update_drain_trajectory(self, board_state: 'BoardState') -> None:
        """
        Track how drain economy is changing turn-over-turn.

        Sets:
        - trajectory.current_drain_gap
        - trajectory.drain_gap_trend
        - trajectory.turns_at_negative
        - trajectory.contestable_drains
        - force_attack_mode
        """
        if not self.enabled:
            return

        # Store previous gap for trend calculation
        old_gap = self.trajectory.current_drain_gap
        self.trajectory.drain_gap_last_turn = old_gap

        # Calculate new drain totals
        self._calculate_drain_totals(board_state)

        # Calculate gap
        self.trajectory.current_drain_gap = (
            self.trajectory.our_total_drain - self.trajectory.their_total_drain
        )

        # Determine trend
        gap_change = self.trajectory.current_drain_gap - old_gap
        if gap_change > 1:
            self.trajectory.drain_gap_trend = "improving"
        elif gap_change < -1:
            self.trajectory.drain_gap_trend = "worsening"
        else:
            self.trajectory.drain_gap_trend = "stable"

        # Track consecutive negative turns (use <= to count at threshold)
        if self.trajectory.current_drain_gap <= self.attack_mode_drain_threshold:
            self.trajectory.turns_at_negative += 1
        else:
            self.trajectory.turns_at_negative = 0

        # Enable attack mode if conditions met
        self._update_attack_mode()

        # Check for space emergency (System 2)
        self._check_space_emergency(board_state)

        logger.info(f"   Drain trajectory: gap={self.trajectory.current_drain_gap:+d} "
                   f"({self.trajectory.drain_gap_trend}), "
                   f"negative for {self.trajectory.turns_at_negative} turns")

    def _calculate_drain_totals(self, board_state: 'BoardState') -> None:
        """Calculate drain totals for both players."""
        self.trajectory.our_total_drain = 0
        self.trajectory.their_total_drain = 0
        self.trajectory.uncontested_opponent_drains = []
        self.trajectory.contestable_drains = []
        self.trajectory.our_drain_locations = []

        # Also update opponent strategy tracking
        self.opponent.total_space_power = 0
        self.opponent.total_ground_power = 0
        self.opponent.uncontested_space_locations = 0
        self.opponent.uncontested_ground_locations = 0
        self.opponent.strongest_power = 0
        self.opponent.strongest_location = None

        locations = getattr(board_state, 'locations', [])
        low_enemy_threshold = 4  # From deploy_planner config
        # Use same robust pattern as deploy_planner - default to 'dark' and handle None/empty
        my_side = getattr(board_state, 'my_side', 'dark')
        if not my_side:  # Handle None or empty string
            my_side = 'dark'
        my_side = my_side.lower()

        # Debug: Log what we're working with
        if len(locations) > 0:
            logger.info(f"   Drain calc: {len(locations)} locations, my_side={my_side}")

        for idx, loc in enumerate(locations):
            if loc is None:
                continue

            loc_name = getattr(loc, 'site_name', None) or getattr(loc, 'name', str(loc))
            # Get location index - use the stored index or fall back to enumeration index
            loc_index = getattr(loc, 'location_index', idx)
            my_power = board_state.my_power_at_location(loc_index) if hasattr(board_state, 'my_power_at_location') else 0
            their_power = board_state.their_power_at_location(loc_index) if hasattr(board_state, 'their_power_at_location') else 0

            # Get location icons from card data
            blueprint_id = getattr(loc, 'blueprint_id', None)
            my_icons = 0
            their_icons = 0
            is_space = False

            if not blueprint_id or blueprint_id == 'unknown':
                continue

            card_data = get_card(blueprint_id)
            if card_data:
                # Get icons based on our side (Card object uses attributes, not dict)
                # my_side is guaranteed to be 'dark' or 'light' after normalization above
                if my_side == 'dark':
                    my_icons = getattr(card_data, 'dark_side_icons', 0) or 0
                    their_icons = getattr(card_data, 'light_side_icons', 0) or 0
                else:  # 'light'
                    my_icons = getattr(card_data, 'light_side_icons', 0) or 0
                    their_icons = getattr(card_data, 'dark_side_icons', 0) or 0

                # Check if space location - use is_system like deploy_planner does
                # has_space_icon only checks for 'space'/'starship' in icons, but systems don't have those
                is_space = getattr(card_data, 'is_system', False) or 'System' in (getattr(card_data, 'sub_type', '') or '')

                # Debug logging for drain calculation
                if my_icons > 0 or their_icons > 0:
                    logger.debug(f"   {loc_name}: my_pwr={my_power}, their_pwr={their_power}, my_icons={my_icons}, their_icons={their_icons}")
            else:
                logger.debug(f"   {loc_name}: No card data for blueprint {blueprint_id}")

            # Our drains (we control locations with opponent icons)
            # We have presence, they don't, and location has THEIR icons
            # Note: GEMP uses -1 for "no presence", so check <= 0
            if my_power > 0 and their_power <= 0 and their_icons > 0:
                self.trajectory.our_total_drain += their_icons
                self.trajectory.our_drain_locations.append(loc_name)

            # Their drains (they control locations with our icons)
            # Note: GEMP uses -1 for "no presence", so check <= 0
            if their_power > 0 and my_power <= 0 and my_icons > 0:
                self.trajectory.their_total_drain += my_icons
                self.trajectory.uncontested_opponent_drains.append(loc_name)

                # Is this contestable? (low enemy power)
                if their_power <= low_enemy_threshold:
                    self.trajectory.contestable_drains.append(loc_name)

            # Track opponent presence
            if their_power > 0:
                if is_space:
                    self.opponent.total_space_power += their_power
                    if my_power == 0:
                        self.opponent.uncontested_space_locations += 1
                else:
                    self.opponent.total_ground_power += their_power
                    if my_power == 0:
                        self.opponent.uncontested_ground_locations += 1

                # Track strongest position
                if their_power > self.opponent.strongest_power:
                    self.opponent.strongest_power = their_power
                    self.opponent.strongest_location = loc_name

        # Determine opponent's primary domain
        if self.opponent.total_space_power > self.opponent.total_ground_power * 1.5:
            self.opponent.primary_domain = "space"
        elif self.opponent.total_ground_power > self.opponent.total_space_power * 1.5:
            self.opponent.primary_domain = "ground"
        else:
            self.opponent.primary_domain = "balanced"

        # Log drain economy results
        if self.trajectory.our_total_drain > 0 or self.trajectory.their_total_drain > 0:
            drain_gap = self.trajectory.our_total_drain - self.trajectory.their_total_drain
            emoji = "🩸" if drain_gap < 0 else ("💧" if drain_gap > 0 else "⚖️")
            logger.info(f"   {emoji} Drain economy: we drain {self.trajectory.our_total_drain}, "
                       f"they drain {self.trajectory.their_total_drain} (gap {drain_gap:+d})")
            if self.trajectory.our_drain_locations:
                logger.info(f"      We drain at: {', '.join(self.trajectory.our_drain_locations)}")
            if self.trajectory.uncontested_opponent_drains:
                logger.info(f"      They drain at: {', '.join(self.trajectory.uncontested_opponent_drains)}")
        else:
            logger.info(f"   Drain economy: we drain 0, they drain 0 (no drains yet)")
        if self.trajectory.contestable_drains:
            logger.info(f"   Contestable bleeds: {self.trajectory.contestable_drains}")

    def _update_attack_mode(self) -> None:
        """
        Enable attack mode if we're losing drain war and can contest.

        Attack mode changes deploy planner scoring to prioritize
        contesting opponent drains over establishing new presence.
        """
        self.force_attack_mode = False

        # Check conditions for attack mode (use <= to trigger at threshold)
        if (self.trajectory.current_drain_gap <= self.attack_mode_drain_threshold and
            len(self.trajectory.contestable_drains) > 0 and
            (self.inventory.has_ground_capability or self.inventory.has_space_capability)):

            self.force_attack_mode = True
            logger.info(f"ATTACK MODE ENABLED: drain gap {self.trajectory.current_drain_gap:+d}, "
                       f"{len(self.trajectory.contestable_drains)} contestable locations")

    def _check_space_emergency(self, board_state: 'BoardState') -> bool:
        """
        Check if we have a space emergency - opponent in space, we're not.

        A space emergency is particularly critical because:
        1. Ships are expensive (harder to fix than ground)
        2. Space locations often have 2+ icons (compounds faster)
        3. Takes time to draw ships if we don't have them

        Sets:
        - space_emergency: True if opponent has uncontested space
        - space_emergency_icons: Total icons we're losing to space drains
        """
        self.space_emergency = False
        self.space_emergency_icons = 0

        # Count opponent space presence vs ours
        opponent_space_locations = 0
        our_space_locations = 0
        bleeding_icons = 0

        locations = getattr(board_state, 'locations', [])
        my_side = getattr(board_state, 'my_side', 'dark')
        if not my_side:
            my_side = 'dark'
        my_side = my_side.lower()

        for idx, loc in enumerate(locations):
            if loc is None:
                continue

            # Check if space location
            blueprint_id = getattr(loc, 'blueprint_id', None)
            if not blueprint_id:
                continue

            card_data = get_card(blueprint_id)
            if not card_data:
                continue

            # Check if space location - use is_system like deploy_planner does
            # has_space_icon only checks for 'space'/'starship' in icons, but systems don't have those
            is_space = getattr(card_data, 'is_system', False) or 'System' in (getattr(card_data, 'sub_type', '') or '')
            if not is_space:
                continue

            # Get power at this location
            loc_index = getattr(loc, 'location_index', idx)
            my_power = board_state.my_power_at_location(loc_index) if hasattr(board_state, 'my_power_at_location') else 0
            their_power = board_state.their_power_at_location(loc_index) if hasattr(board_state, 'their_power_at_location') else 0

            # Count presence
            if their_power > 0 and my_power <= 0:
                opponent_space_locations += 1
                # Count icons we're losing
                if my_side == 'dark':
                    my_icons = getattr(card_data, 'dark_side_icons', 0) or 0
                else:
                    my_icons = getattr(card_data, 'light_side_icons', 0) or 0
                bleeding_icons += my_icons

            if my_power > 0:
                our_space_locations += 1

        # Space emergency: opponent has space presence, we have NONE
        if opponent_space_locations > 0 and our_space_locations == 0:
            self.space_emergency = True
            self.space_emergency_icons = bleeding_icons
            logger.warning(f"🚨 SPACE EMERGENCY: {opponent_space_locations} opponent locations, "
                          f"{bleeding_icons} icons/turn bleeding! We have NO space presence!")
            return True

        return False

    # =========================================================================
    # URGENCY & THRESHOLDS
    # =========================================================================

    def get_strategic_urgency(self) -> str:
        """
        Determine overall strategic urgency level.

        Returns:
            "critical" - Losing badly, need drastic action
            "urgent" - Behind, need to push harder
            "normal" - On track or winning
        """
        if not self.enabled:
            return "normal"

        # Critical conditions:
        # - Drain gap worse than -4 AND worsening
        # - Missing critical card type AND opponent has uncontested positions
        # - Losing drain war for 3+ consecutive turns
        # - Space emergency (opponent in space, we're not) - System 2

        critical_threshold = _get_adaptive_config('urgency_critical_threshold', -4)
        urgent_threshold = _get_adaptive_config('urgency_urgent_threshold', -2)

        # Space emergency is ALWAYS critical - compounds fast and hard to fix
        if self.space_emergency:
            logger.info(f"   🚨 Urgency: CRITICAL (space emergency, {self.space_emergency_icons} icons/turn)")
            return "critical"

        if (self.trajectory.current_drain_gap < critical_threshold and
            self.trajectory.drain_gap_trend == "worsening"):
            return "critical"

        if self.trajectory.turns_at_negative >= self.critical_drain_turns:
            return "critical"

        if (self.inventory.missing_critical_type and
            (self.opponent.uncontested_space_locations > 0 or
             self.opponent.uncontested_ground_locations > 0)):
            return "critical"

        # Urgent conditions:
        # - Drain gap negative
        # - Missing card types (but not critical)

        if self.trajectory.current_drain_gap < urgent_threshold:
            return "urgent"

        if self.force_draw_mode or self.force_attack_mode:
            return "urgent"

        return "normal"

    def get_thresholds(self) -> StrategicThresholds:
        """
        Get adjusted thresholds based on current urgency.

        When critical: Lower all thresholds to be more aggressive.
        When urgent: Slightly lower thresholds.
        When normal: Use config defaults.
        """
        urgency = self.get_strategic_urgency()

        # Default thresholds from config
        base_deploy = get_config().get('deploy_strategy', 'deploy_threshold', 4)
        base_contest = get_config().get('contest_strategy', 'min_contest_advantage', 2)
        base_reserve = get_config().get('battle_strategy', 'force_reserve', 1)

        if urgency == "critical":
            return StrategicThresholds(
                deploy_threshold=max(2, base_deploy - _get_threshold_config('critical_deploy_reduction', 2)),
                contest_advantage=_get_threshold_config('critical_contest_advantage', 0),
                goal_multiplier=3.0,
                force_reserve=0,
            )
        elif urgency == "urgent":
            return StrategicThresholds(
                deploy_threshold=max(3, base_deploy - _get_threshold_config('urgent_deploy_reduction', 1)),
                contest_advantage=max(0, base_contest - 1),
                goal_multiplier=2.0,
                force_reserve=base_reserve,
            )
        else:
            return StrategicThresholds(
                deploy_threshold=base_deploy,
                contest_advantage=base_contest,
                goal_multiplier=1.0,
                force_reserve=base_reserve,
            )

    # =========================================================================
    # LOGGING
    # =========================================================================

    def log_strategic_summary(self) -> None:
        """Log a summary of current strategic state."""
        urgency = self.get_strategic_urgency()
        thresholds = self.get_thresholds()

        logger.info(f"=== STRATEGIC STATE ({urgency.upper()}) ===")
        logger.info(f"   Inventory: {self.inventory.characters_in_hand}c, "
                   f"{self.inventory.starships_in_hand}s, "
                   f"{self.inventory.hand_size} total in hand")
        logger.info(f"   Capabilities: space={self.inventory.has_space_capability}, "
                   f"ground={self.inventory.has_ground_capability}")
        if self.inventory.missing_critical_type:
            logger.info(f"   MISSING: {self.inventory.missing_critical_type}")
        logger.info(f"   Drain: gap={self.trajectory.current_drain_gap:+d} "
                   f"({self.trajectory.drain_gap_trend})")
        logger.info(f"   Opponent: {self.opponent.primary_domain} focused, "
                   f"{self.opponent.uncontested_space_locations}s/{self.opponent.uncontested_ground_locations}g uncontested")
        logger.info(f"   Modes: draw={self.force_draw_mode}, attack={self.force_attack_mode}")
        logger.info(f"   Thresholds: deploy={thresholds.deploy_threshold}, "
                   f"contest={thresholds.contest_advantage}, "
                   f"goal_mult={thresholds.goal_multiplier}x")

    # =========================================================================
    # FULL UPDATE
    # =========================================================================

    def update_from_board_state(self, board_state: 'BoardState') -> None:
        """
        Full update of strategic state from current board.

        Called at deploy phase start and other key moments.
        """
        if not self.enabled:
            return

        logger.info("Updating strategic state...")

        # Update all components
        self.update_drain_trajectory(board_state)
        self.analyze_inventory(board_state)

        # Log summary
        self.log_strategic_summary()

    def on_turn_start(self, board_state: 'BoardState') -> None:
        """
        Called at start of each turn.

        Tracks turn-over-turn trajectory changes.
        """
        if not self.enabled:
            return

        # Drain trajectory update happens here to track turn-over-turn changes
        self.update_drain_trajectory(board_state)

        # Reset any per-turn state if needed
        # (modes are recalculated each phase)

    def on_deploy_phase_start(self, board_state: 'BoardState') -> None:
        """
        Called at start of deploy phase.

        Full inventory analysis and mode determination.
        """
        if not self.enabled:
            return

        self.analyze_inventory(board_state)

        # Re-check attack mode with updated inventory
        self._update_attack_mode()

        # Log summary before deploy decisions
        self.log_strategic_summary()

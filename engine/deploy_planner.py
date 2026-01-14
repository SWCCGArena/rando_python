"""
Deploy Phase Planner - Holistic Turn Planning

Creates a comprehensive deployment plan for the entire phase, not individual cards.

Strategic flow:
1. DEPLOY LOCATIONS FIRST - opens new options
2. REDUCE HARM - reinforce contested locations where we're losing
3. GAIN GROUND - establish at uncontested locations with opponent icons
4. NEVER deploy to 0-icon uncontested locations

The planner outputs SPECIFIC deployment instructions:
- Which cards to deploy
- Which location each card should go to
- Priority order for execution

The evaluator then just matches actions to the plan - no second-guessing.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Tuple, Set

from engine.card_loader import get_card, is_matching_pilot_ship
from engine.strategy_config import get_config
from engine.monte_carlo import MonteCarloSimulator, SimulationResult, ExpectedValue
# NOTE: GoalType was removed - hold penalty testing showed it hurt performance

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIG-DRIVEN PARAMETERS
# All values loaded from JSON config with hardcoded fallback defaults
# =============================================================================

def _get_deploy_config(key: str, default):
    """Get deploy strategy config value."""
    return get_config().get('deploy_strategy', key, default)

def _get_battle_config(key: str, default):
    """Get battle strategy config value."""
    return get_config().get('battle_strategy', key, default)

def _get_contest_config(key: str, default):
    """Get contest strategy config value."""
    return get_config().get('contest_strategy', key, default)

# Battle threshold - power advantage needed to feel comfortable battling
def get_battle_favorable_threshold() -> int:
    return _get_battle_config('favorable_threshold', 4)

# Minimum power advantage required to contest a location
def get_min_contest_advantage() -> int:
    return _get_contest_config('min_contest_advantage', 2)


def get_contest_advantage(life_force: int) -> int:
    """
    Calculate the required power advantage for contesting based on life force.

    When life is high, we're conservative and require +2 advantage.
    As life decreases, we accept riskier battles:
    - life >= 30: +2 (comfortable, play safe)
    - life 20-29: +1 (need to be more aggressive)
    - life < 20: +0 (accept ties, desperate for presence)

    Returns the required advantage (0-2).
    """
    base_advantage = get_min_contest_advantage()
    if life_force >= 30:
        return base_advantage  # Full advantage required
    elif life_force >= 20:
        return max(0, base_advantage - 1)  # Accept narrow wins
    else:
        return 0  # Accept ties (desperate)

# Thresholds for battle/flee decisions
def get_retreat_threshold() -> int:
    return _get_battle_config('retreat_threshold', -6)

def get_dangerous_threshold() -> int:
    return _get_battle_config('dangerous_threshold', -2)

# Minimum total power before deploying characters
def get_deploy_threshold() -> int:
    return _get_deploy_config('deploy_threshold', 4)

# Power advantage where we stop reinforcing (overkill prevention)
def get_deploy_overkill_threshold() -> int:
    return _get_deploy_config('deploy_overkill_threshold', 8)

# Enemy power buildup that prevents threshold relaxation (react/move threat)
def get_react_threat_threshold() -> int:
    return _get_battle_config('react_threat_threshold', 8)

# Early game hold-back: minimum score to deploy in turns 1-3
# Prevents weak "establish" plays that waste cards; bot waits for better opportunity
def get_early_game_threshold() -> int:
    return _get_deploy_config('early_game_threshold', 80)

def get_early_game_turns() -> int:
    return _get_deploy_config('early_game_turns', 3)

# Mid-late game reinforcement: target power level
def get_reinforce_target_power() -> int:
    return _get_deploy_config('reinforce_target_power', 10)

# Bonus score for matching pilot/ship combos
def get_matching_pilot_bonus() -> int:
    return _get_deploy_config('matching_pilot_bonus', 10)

# Additional deploy strategy parameters
def get_low_enemy_threshold() -> int:
    return _get_deploy_config('low_enemy_threshold', 4)

def get_max_establish_locations() -> int:
    return _get_deploy_config('max_establish_locations', 2)

def get_uncontested_fortified_threshold() -> int:
    return _get_deploy_config('uncontested_fortified_threshold', 10)

def get_reinforce_force_threshold() -> int:
    return _get_deploy_config('reinforce_force_threshold', 4)

def get_min_force_for_weapons() -> int:
    return _get_deploy_config('min_force_for_weapons', 2)

# Minimum power threshold for establishing at a new location
# This is a FLOOR that dynamic thresholds cannot go below
def get_min_establish_power() -> int:
    return _get_deploy_config('min_establish_power', 4)

# Whether weak characters require a "buddy" (another character deployed with them)
def get_weak_char_buddy_required() -> bool:
    return _get_deploy_config('weak_char_buddy_required', True)

# Power threshold below which a character is considered "weak"
def get_weak_character_power() -> int:
    return _get_deploy_config('weak_character_power', 2)


def _pilot_score_for_ship(pilot_dict: Dict, ship_dict: Dict) -> int:
    """
    Score a pilot for deployment aboard a specific ship.

    Higher is better. Considers:
    - Base power (higher = better)
    - Matching bonus (pilot/ship from same matching field)

    Args:
        pilot_dict: Pilot info dict with 'power', 'blueprint_id', 'name' keys
        ship_dict: Ship info dict with 'blueprint_id', 'name' keys

    Returns:
        Score for this pilot/ship combination
    """
    base_score = pilot_dict.get('power', 0)

    # Check for matching pilot/ship bonus
    pilot_card = get_card(pilot_dict.get('blueprint_id', ''))
    ship_card = get_card(ship_dict.get('blueprint_id', ''))

    if pilot_card and ship_card and is_matching_pilot_ship(pilot_card, ship_card):
        base_score += get_matching_pilot_bonus()
        logger.debug(f"   ⭐ Matching pilot bonus: {pilot_dict.get('name', '?')} + {ship_dict.get('name', '?')}")

    return base_score


def is_restricted_deployment_location(location_name: str) -> bool:
    """
    Check if a location has special deployment restrictions (Dagobah/Ahch-To).

    Dagobah and Ahch-To have special rules:
    - Characters, vehicles, starships may NOT deploy there unless specifically
      allowed by their gametext (e.g., "May deploy to Dagobah")
    - Most cards cannot deploy to these locations

    Since checking every card's gametext for deployment permissions is complex,
    we simply exclude these locations from deployment planning. The rare cards
    that CAN deploy there (like Yoda) would need special handling.

    Args:
        location_name: The location's name/title

    Returns:
        True if this is a restricted deployment location
    """
    name_lower = location_name.lower()
    return 'dagobah' in name_lower or 'ahch-to' in name_lower


def is_interior_naboo_site(location_name: str, is_interior: bool) -> bool:
    """
    Check if a location is an interior Naboo site.

    Used for "We Have A Plan" objective restriction:
    "While this side up, you may not deploy characters to interior Naboo sites."

    Args:
        location_name: The location's name/title
        is_interior: Whether the location is marked as interior

    Returns:
        True if this is an interior Naboo site
    """
    if not is_interior:
        return False
    name_lower = location_name.lower()
    # Theed Palace sites are interior Naboo sites
    # Naboo: Theed Palace Throne Room, Theed Palace Hallway, etc.
    return 'naboo' in name_lower or 'theed' in name_lower


def has_we_have_a_plan_restriction(board_state) -> bool:
    """
    Check if "We Have A Plan" objective is active (front side) on our side of table.

    The objective "We Have A Plan / They Will Be Lost And Confused" (14_52)
    has a restriction while on the front (0) side:
    "While this side up, you may not deploy characters to interior Naboo sites."

    When flipped to "They Will Be Lost And Confused" (collapsed=True),
    the restriction is lifted.

    Args:
        board_state: Current board state

    Returns:
        True if the restriction is active (objective on front side)
    """
    if not hasattr(board_state, 'cards_in_play') or not board_state.cards_in_play:
        return False

    my_player_name = getattr(board_state, 'my_player_name', '')

    for card_id, card in board_state.cards_in_play.items():
        # Check for We Have A Plan objective (14_52)
        if card.blueprint_id == '14_52':
            # Must be our card and on SIDE_OF_TABLE
            if card.owner == my_player_name and card.zone == 'SIDE_OF_TABLE':
                # collapsed=False means front side ("We Have A Plan") is showing
                # This is when the restriction is active
                if not card.collapsed:
                    logger.debug(f"📋 We Have A Plan restriction ACTIVE (not flipped)")
                    return True
                else:
                    logger.debug(f"📋 We Have A Plan flipped to 'They Will Be Lost And Confused' - no restriction")
                    return False

    return False


class DeployStrategy(Enum):
    """High-level deployment strategy for this phase"""
    HOLD_BACK = "hold_back"           # Don't deploy - save for later
    ESTABLISH = "establish"            # Deploy to new location to gain control
    REINFORCE = "reinforce"            # Strengthen a weak position
    OVERWHELM = "overwhelm"            # Crush opponent at a location
    DEPLOY_LOCATIONS = "locations"     # Deploy location cards first


@dataclass
class LocationAnalysis:
    """Analysis of a single location for deployment targeting"""
    card_id: str
    name: str
    is_ground: bool
    is_space: bool

    # Power totals
    my_power: int = 0
    their_power: int = 0

    # Control status
    i_control: bool = False
    they_control: bool = False
    contested: bool = False

    # Icons (for force drain value)
    my_icons: int = 0
    their_icons: int = 0

    # Strategic value
    priority_score: float = 0.0

    # Battle/Flee analysis
    should_flee: bool = False  # If True, don't reinforce - we'll flee
    can_flip_to_favorable: bool = False  # If True, deploying here makes us favorable
    is_battle_opportunity: bool = False  # If True, we can deploy + battle to win
    location_index: int = -1  # Index for board_state lookups

    # Interior/Exterior (for vehicle deployment - vehicles can only go to exterior)
    is_interior: bool = False
    is_exterior: bool = True  # Default to exterior

    # Site vs System (characters can only deploy to sites, not systems)
    is_site: bool = False  # True if this is a site (ground location with : in name)

    @property
    def power_differential(self) -> int:
        """Positive = I'm ahead, negative = they're ahead"""
        return self.my_power - self.their_power


@dataclass
class DeploymentInstruction:
    """A specific instruction to deploy one card to one location"""
    card_blueprint_id: str
    card_name: str
    target_location_id: Optional[str]  # None for locations (they deploy to table)
    target_location_name: Optional[str]
    priority: int  # Lower = deploy first (0 = locations, 1 = reinforce, 2 = establish)
    reason: str
    power_contribution: int = 0
    deploy_cost: int = 0
    ability_contribution: int = 0  # For battle destiny eligibility (need 4+ total)
    # Backup target if primary is unavailable (e.g., blocked by game rules)
    backup_location_id: Optional[str] = None
    backup_location_name: Optional[str] = None
    backup_reason: Optional[str] = None

    # For pilots deploying aboard ships: track the ship they should board
    # The ship_card_id is None until the ship deploys and gets assigned an ID
    aboard_ship_name: Optional[str] = None
    aboard_ship_blueprint_id: Optional[str] = None
    aboard_ship_card_id: Optional[str] = None  # Set when ship gets PCIP event

    # For cards deploying to locations in hand: track by name until location deploys
    # The target_location_id may be None initially if location is in hand
    target_location_pending: bool = False  # True if waiting for location to deploy
    target_location_blueprint_id: Optional[str] = None  # For matching PCIP events

    def __post_init__(self):
        """Auto-detect pending locations from 'planned_' prefix in card_id."""
        if self.target_location_id and self.target_location_id.startswith("planned_"):
            # Extract blueprint_id from "planned_<blueprint_id>" format
            self.target_location_blueprint_id = self.target_location_id[8:]  # Skip "planned_"
            self.target_location_pending = True
            self.target_location_id = None  # Clear the placeholder ID


@dataclass
class DeploymentPlan:
    """The complete deployment plan for this phase"""
    strategy: DeployStrategy
    reason: str

    # SPECIFIC deployment instructions in priority order
    instructions: List[DeploymentInstruction] = field(default_factory=list)

    # Cards we explicitly should NOT deploy
    hold_back_cards: Set[str] = field(default_factory=set)

    # Target locations for reference
    target_locations: List[LocationAnalysis] = field(default_factory=list)

    # Budget tracking
    total_force_available: int = 0
    force_reserved_for_battle: int = 2  # Reserve some for battle destiny/effects
    force_to_spend: int = 0

    # Phase state
    phase_started: bool = False
    deployments_made: int = 0

    # Flag set by evaluator when planned cards aren't available
    force_allow_extras: bool = False

    # Original plan cost (before any deployments)
    original_plan_cost: int = 0

    def should_deploy_card(self, blueprint_id: str) -> bool:
        """Check if a card is in our deployment plan"""
        return any(inst.card_blueprint_id == blueprint_id for inst in self.instructions)

    def get_instruction_for_card(self, blueprint_id: str) -> Optional[DeploymentInstruction]:
        """Get the deployment instruction for a specific card"""
        for inst in self.instructions:
            if inst.card_blueprint_id == blueprint_id:
                return inst
        return None

    def get_target_for_card(self, blueprint_id: str) -> Optional[str]:
        """Get the target location for a card, if any"""
        inst = self.get_instruction_for_card(blueprint_id)
        return inst.target_location_id if inst else None

    def is_plan_complete(self) -> bool:
        """Check if all planned deployments have been executed"""
        return len(self.instructions) == 0 and self.deployments_made > 0

    def get_extra_force_budget(self, current_force: int) -> int:
        """
        Calculate how much extra force is available for non-planned actions.

        Extra actions are allowed when:
        1. The plan is complete (all planned deployments done)
        2. We have more force than the reserved amount

        Args:
            current_force: Current force pile value

        Returns:
            Amount of force available for extra actions (0 if none)
        """
        if not self.is_plan_complete():
            return 0  # Plan not complete, no extra actions yet

        # Extra force = current force - reserved for battle
        extra = current_force - self.force_reserved_for_battle
        return max(0, extra)

    def allows_extra_actions(self, current_force: int) -> bool:
        """Check if we should allow non-planned extra actions"""
        return self.get_extra_force_budget(current_force) > 0

    def get_pending_card_types(self) -> Dict[str, bool]:
        """
        Check what card types are still pending in the plan.

        Deployment order is:
        1. Locations (priority 0)
        2. Ships/Vehicles (priority 1)
        3. Characters (priority 2)

        Returns dict with keys: 'locations', 'ships_vehicles', 'characters'
        """
        pending = {
            'locations': False,
            'ships_vehicles': False,
            'characters': False,
        }

        for inst in self.instructions:
            card_meta = get_card(inst.card_blueprint_id)
            if not card_meta:
                continue

            if card_meta.is_location:
                pending['locations'] = True
            elif card_meta.is_starship or card_meta.is_vehicle:
                pending['ships_vehicles'] = True
            elif card_meta.is_character:
                pending['characters'] = True

        return pending

    def should_deploy_card_now(self, blueprint_id: str, available_blueprint_ids: Optional[List[str]] = None) -> Tuple[bool, str]:
        """
        Check if a card should deploy NOW based on type ordering.

        Deployment order:
        1. Locations FIRST
        2. Ships/Vehicles SECOND
        3. Characters LAST

        If higher-priority types are still pending AND available to deploy,
        this card should wait. But if higher-priority types are pending but
        NOT available (GEMP not offering them), we allow this card to deploy
        to prevent the bot from hanging.

        Args:
            blueprint_id: Card to check
            available_blueprint_ids: List of blueprint IDs that GEMP is offering
                                     If provided, only block if higher-priority
                                     cards are actually available

        Returns:
            (should_deploy, reason)
            - should_deploy: True if this card type should deploy now
            - reason: Explanation of why or why not
        """
        card_meta = get_card(blueprint_id)
        if not card_meta:
            return (True, "Unknown card type - allow deploy")

        pending = self.get_pending_card_types()

        # Helper to check if any pending cards of a type are available
        def _pending_type_available(card_type: str) -> bool:
            """Check if any pending cards of given type are in available_blueprint_ids"""
            if not available_blueprint_ids:
                return True  # If no list provided, assume all pending cards are available

            for inst in self.instructions:
                inst_meta = get_card(inst.card_blueprint_id)
                if not inst_meta:
                    continue

                is_match = False
                if card_type == 'locations' and inst_meta.is_location:
                    is_match = True
                elif card_type == 'ships_vehicles' and (inst_meta.is_starship or inst_meta.is_vehicle):
                    is_match = True

                if is_match and inst.card_blueprint_id in available_blueprint_ids:
                    return True

            return False

        # Locations always deploy first
        if card_meta.is_location:
            return (True, "Locations deploy first")

        # Ships/Vehicles deploy after locations
        if card_meta.is_starship or card_meta.is_vehicle:
            if pending['locations']:
                if _pending_type_available('locations'):
                    return (False, "Wait: locations must deploy first")
                else:
                    logger.info("📋 Locations pending but not available - allowing ship/vehicle deploy")
            return (True, "Ships/vehicles deploy (no available locations pending)")

        # Characters deploy last
        if card_meta.is_character:
            if pending['locations']:
                if _pending_type_available('locations'):
                    return (False, "Wait: locations must deploy first")
                else:
                    logger.info("📋 Locations pending but not available - checking ships/vehicles")

            if pending['ships_vehicles']:
                if _pending_type_available('ships_vehicles'):
                    return (False, "Wait: ships/vehicles must deploy first")
                else:
                    logger.info("📋 Ships/vehicles pending but not available - allowing character deploy")

            return (True, "Characters deploy (no available higher-priority pending)")

        # Other card types (effects, interrupts, etc.) - no ordering restriction
        return (True, "No ordering restriction for this card type")

    def update_deployed_card_id(self, blueprint_id: str, card_id: str, card_name: str) -> bool:
        """
        Update the plan when a card deploys and gets assigned a card_id.

        This is called when we receive a PCIP (Put Card In Play) event for a card
        that's in our plan. It updates:
        1. Any pilots waiting to board this ship (sets aboard_ship_card_id)
        2. Any cards waiting to deploy to this location (sets target_location_id)

        Args:
            blueprint_id: The blueprint ID of the card that just deployed
            card_id: The card_id assigned by GEMP
            card_name: The name of the card (for logging)

        Returns:
            True if any instructions were updated
        """
        updated = False

        for inst in self.instructions:
            # Check if any pilot is waiting to board this ship
            if inst.aboard_ship_blueprint_id == blueprint_id and inst.aboard_ship_card_id is None:
                inst.aboard_ship_card_id = card_id
                logger.info(f"📋 Plan updated: {inst.card_name} will board {card_name} (card_id={card_id})")
                updated = True

            # Check if any card is waiting to deploy to this location
            if inst.target_location_pending:
                # Match by blueprint_id if available (most reliable)
                if inst.target_location_blueprint_id and inst.target_location_blueprint_id == blueprint_id:
                    inst.target_location_id = card_id
                    inst.target_location_pending = False
                    logger.info(f"📋 Plan updated: {inst.card_name} will deploy to {card_name} (card_id={card_id})")
                    updated = True
                # Fallback to name matching if no blueprint_id
                elif inst.target_location_name and not inst.target_location_blueprint_id:
                    card_meta = get_card(blueprint_id)
                    if card_meta and card_meta.is_location:
                        if card_name and inst.target_location_name.lower() in card_name.lower():
                            inst.target_location_id = card_id
                            inst.target_location_pending = False
                            logger.info(f"📋 Plan updated: {inst.card_name} will deploy to {card_name} (card_id={card_id})")
                            updated = True

        return updated


@dataclass
class NextTurnCrushPlan:
    """
    Tracks a planned CRUSH attack for next turn.

    When the bot decides to hold back this turn to set up a crushing attack
    next turn, this stores the details so other phases (draw, extra actions)
    can respect the plan and not waste force.
    """
    # Target location info
    target_location_id: str
    target_location_name: str
    target_enemy_power: int

    # Cards we'll deploy for the crush
    card_blueprint_ids: List[str]
    card_names: List[str]

    # Power and cost calculations
    total_power: int
    total_deploy_cost: int

    # Force needed = deploy cost + battle reserve (usually +2)
    force_needed: int

    # Expected force next turn (current unused + generation)
    expected_force_next_turn: int

    # The power advantage we'll achieve
    expected_advantage: int

    # Force generation per turn (needed for draw calculations)
    force_generation: int = 0

    def get_max_draw_force(self, current_force: int) -> int:
        """
        Calculate maximum force that can be spent on drawing cards.

        We need to save enough force so that next turn we can afford the crush.
        Drawing costs force now but we regenerate next turn.

        IMPORTANT: This uses current_force to properly track force depletion
        during the draw phase. Each draw reduces current_force by 1.
        """
        # Calculate what we'll actually have next turn based on CURRENT force
        # (not the original force when the plan was made)
        actual_next_turn = current_force + self.force_generation

        # We need force_needed next turn, so max we can spend on draws is the excess
        max_draw = actual_next_turn - self.force_needed

        # Keep at least 1 force buffer for safety
        return max(0, max_draw - 1)


@dataclass
class ExpensiveCardBudget:
    """
    System 1: Tracks force we're saving for an expensive card.

    When we have a high-value expensive card (e.g., Finalizer at 13 cost)
    but can't afford it this turn, create a budget to save force across turns.

    This prevents the bot from spending all force on cheap deployments
    when saving would let us deploy a powerful card next turn.
    """
    target_blueprint_id: str  # Blueprint ID of card we're saving for
    target_name: str          # Human-readable name
    target_cost: int          # Deploy cost of the card
    target_power: int         # Power value of the card
    save_per_turn: int        # How much to save each turn
    turns_remaining: int      # Estimated turns until we can afford it
    is_starship: bool = False  # True if saving for a ship (space emergency relevance)

    def update_turn(self, current_force: int, force_generation: int) -> bool:
        """
        Update budget at turn start. Returns True if we can now afford the card.

        Args:
            current_force: Force we have right now
            force_generation: How much force we'll generate

        Returns:
            True if we should now be able to deploy the card
        """
        self.turns_remaining -= 1
        expected_force = current_force + force_generation
        can_afford = expected_force >= self.target_cost
        if can_afford:
            logger.info(f"💰 BUDGET COMPLETE: Can now afford {self.target_name} "
                       f"(have {current_force} + {force_generation} gen = {expected_force}, need {self.target_cost})")
        else:
            logger.info(f"💰 Budget update: {self.target_name} - {self.turns_remaining} turns left "
                       f"(have {current_force}, need {self.target_cost})")
        return can_afford

    def get_force_to_reserve(self, current_force: int) -> int:
        """
        Get how much force we should reserve this turn.

        Returns minimum of:
        - save_per_turn (our target savings rate)
        - Half of current force (never save more than half)
        """
        max_reserve = current_force // 2
        reserve = min(self.save_per_turn, max_reserve)
        return max(0, reserve)


class DeployPhasePlanner:
    """
    Creates comprehensive deployment plans for the entire phase.

    Usage:
    1. Call create_plan() at start of deploy phase
    2. Evaluator checks plan.should_deploy_card() for each option
    3. High score if card is in plan, low score if not
    """

    def __init__(self, deploy_threshold: int = 6, battle_force_reserve: int = 1):
        self.deploy_threshold = deploy_threshold
        self.battle_force_reserve = battle_force_reserve  # Base reserve (1 for effects/reactions)
        self.current_plan: Optional[DeploymentPlan] = None
        self._last_phase: str = ""
        self._last_turn: int = -1
        self._current_turn: int = 1  # Current turn for scoring (set in create_plan)
        self._board_state = None  # Current board state (set in create_plan)

        # Monte Carlo simulation for plan stress-testing
        mc_config = get_config().get_section('monte_carlo')
        self.monte_carlo_enabled = mc_config.get('enabled', False)
        self.monte_carlo_top_n = mc_config.get('simulate_top_n', 5)
        if self.monte_carlo_enabled:
            self.monte_carlo = MonteCarloSimulator(mc_config)
            logger.info(f"🎲 Monte Carlo simulation ENABLED (n={self.monte_carlo.n_simulations}, "
                       f"barrier_prob={self.monte_carlo.barrier_prob})")
        else:
            self.monte_carlo = None

    def reset(self):
        """Reset planner state for a new game. Call this when game starts."""
        logger.info("📋 Deploy planner reset for new game")
        self.current_plan = None
        self._last_phase = ""
        self._last_turn = -1
        self._current_turn = 1
        self._board_state = None

    def _estimate_force_generation(self, locations: List[LocationAnalysis], board_state=None) -> int:
        """
        Estimate our force generation for next turn.

        Uses the actual GEMP-reported force generation value when available.
        Falls back to counting icons at controlled locations only.

        Args:
            locations: Analyzed locations (for fallback calculation)
            board_state: Board state with GEMP's activation value

        Returns:
            Estimated force generation for next turn
        """
        # Use actual GEMP value if available (most accurate)
        if board_state and hasattr(board_state, 'activation') and board_state.activation > 0:
            return board_state.activation

        # Fallback: only count icons at locations we CONTROL (have presence)
        # This is more conservative than the buggy "count all icons" approach
        total_icons = 0
        for loc in locations:
            # Only count icons at locations where we have presence (control)
            if loc.my_icons > 0 and loc.my_power > 0:
                total_icons += loc.my_icons

        # Ensure at least 1 (starting force generation)
        return max(1, total_icons)

    def _estimate_next_turn_force(self, current_force: int, locations: List[LocationAnalysis],
                                   force_spent_this_turn: int = 0, board_state=None) -> int:
        """
        Estimate how much force we'll have available next turn.

        Args:
            current_force: Current force pile
            locations: Analyzed locations for icon counting
            force_spent_this_turn: Force we're planning to spend this turn
            board_state: Board state with GEMP's activation value

        Returns:
            Estimated force available next turn
        """
        force_remaining = current_force - force_spent_this_turn
        force_generation = self._estimate_force_generation(locations, board_state)
        return force_remaining + force_generation

    def _find_next_turn_crush_opportunities(
        self,
        board_state,
        locations: List[LocationAnalysis],
        all_cards_in_hand: List[Dict],
        current_force: int
    ) -> Optional[NextTurnCrushPlan]:
        """
        Look for CRUSH opportunities that become possible by waiting one turn.

        Only considers:
        - Locations where enemy has presence (their_power > 0)
        - Cards that are too expensive now but affordable next turn
        - Vehicle+pilot combos that could crush

        Does NOT consider:
        - Establish opportunities (only CRUSH)
        - Reinforcement (only CRUSH at locations we don't control)

        Returns:
            NextTurnCrushPlan if a good opportunity exists, None otherwise
        """
        if not board_state or not locations:
            return None

        # Calculate next turn's expected force (if we spend 0 this turn)
        # Must account for:
        # 1. Reserve deck limits activation (can't activate more than reserve size)
        # 2. Drains will deplete reserve before next activate phase
        force_generation = self._estimate_force_generation(locations, board_state)
        reserve_deck = getattr(board_state, 'reserve_deck', 0) or 0

        # Get drain gap from strategic state if available
        drain_gap = 0
        strategic_state = getattr(board_state, 'strategic_state', None)
        if strategic_state and strategic_state.enabled:
            drain_gap = strategic_state.trajectory.current_drain_gap

        # If drain gap is negative, we'll lose that much reserve to drains before next activate
        expected_drain_loss = abs(drain_gap) if drain_gap < 0 else 0
        effective_reserve = max(0, reserve_deck - expected_drain_loss)

        # Can only activate up to effective reserve (minus a small buffer for destiny draws)
        destiny_buffer = 2  # Keep some cards for destiny draws
        max_activation = max(0, min(force_generation, effective_reserve - destiny_buffer))
        next_turn_force = current_force + max_activation

        logger.debug(f"🔮 Next-turn analysis: current={current_force}, gen={force_generation}, "
                    f"reserve={reserve_deck}, drain_gap={drain_gap}, effective_reserve={effective_reserve}, "
                    f"max_activation={max_activation}, next={next_turn_force}")

        # Find enemy-held locations we could potentially crush
        enemy_held = [
            loc for loc in locations
            if loc.their_power > 0  # Enemy has presence
            and loc.my_power == 0   # We DON'T have presence (not reinforcement)
            and loc.my_icons > 0    # We can deploy there (have icons)
        ]

        if not enemy_held:
            logger.debug("🔮 No enemy-held locations to consider for next-turn crush")
            return None

        # Get all cards (including expensive ones we can't afford now)
        all_characters = [c for c in all_cards_in_hand if c.get('is_character')]
        all_vehicles = [c for c in all_cards_in_hand if c.get('is_vehicle')]
        all_starships = [c for c in all_cards_in_hand if c.get('is_starship')]

        # Get pilots for vehicle/ship combos
        pilots = [c for c in all_characters if c.get('is_pilot')]

        best_opportunity = None
        best_score = 0

        # Check each enemy-held location
        for loc in enemy_held:
            enemy_power = loc.their_power
            # Need favorable threshold advantage for CRUSH
            power_needed_for_crush = enemy_power + get_battle_favorable_threshold()

            logger.debug(f"🔮 Checking {loc.name}: enemy={enemy_power}, need {power_needed_for_crush} for crush")

            # Option 1: Vehicle + pilot combo (ground locations only)
            if loc.is_ground and not loc.is_interior:
                for vehicle in all_vehicles:
                    v_power = vehicle.get('power', 0) or vehicle.get('power_value', 0)
                    v_cost = vehicle.get('cost', 0) or vehicle.get('deploy_value', 0)
                    v_name = vehicle.get('name', 'Unknown')

                    # Skip if we can afford it now (not a "next turn" opportunity)
                    if v_cost <= current_force - self.battle_force_reserve:
                        continue

                    # Find best pilot for this vehicle
                    for pilot in pilots:
                        p_power = pilot.get('pilot_adds_power', 2) or 2  # Default pilot bonus
                        p_cost = pilot.get('cost', 0) or pilot.get('deploy_value', 0)
                        p_name = pilot.get('name', 'Unknown')

                        total_power = v_power + p_power
                        total_cost = v_cost + p_cost
                        force_needed = total_cost + 2  # Deploy + battle reserve

                        # Can we afford this next turn?
                        if force_needed > next_turn_force:
                            continue

                        # Is this a crush?
                        if total_power < power_needed_for_crush:
                            continue

                        advantage = total_power - enemy_power
                        # Score: advantage * 10 + location value (their_icons)
                        score = advantage * 10 + loc.their_icons * 15

                        logger.debug(f"🔮   Vehicle combo: {v_name}+{p_name} = {total_power} power, "
                                   f"cost {total_cost}, advantage +{advantage}, score {score}")

                        if score > best_score:
                            best_score = score
                            best_opportunity = NextTurnCrushPlan(
                                target_location_id=loc.card_id,
                                target_location_name=loc.name,
                                target_enemy_power=enemy_power,
                                card_blueprint_ids=[vehicle['blueprint_id'], pilot['blueprint_id']],
                                card_names=[v_name, p_name],
                                total_power=total_power,
                                total_deploy_cost=total_cost,
                                force_needed=force_needed,
                                expected_force_next_turn=next_turn_force,
                                expected_advantage=advantage,
                                force_generation=force_generation,
                            )

            # Option 2: Multiple characters combined
            # Sort by power/cost efficiency
            affordable_next_turn = [
                c for c in all_characters
                if (c.get('cost', 0) or c.get('deploy_value', 0)) <= next_turn_force - 2
            ]

            if len(affordable_next_turn) >= 2:
                # Try combinations of 2-3 characters
                from itertools import combinations
                for combo_size in [3, 2]:
                    if len(affordable_next_turn) < combo_size:
                        continue

                    for combo in combinations(affordable_next_turn, combo_size):
                        total_power = sum(c.get('power', 0) or c.get('power_value', 0) for c in combo)
                        total_cost = sum(c.get('cost', 0) or c.get('deploy_value', 0) for c in combo)
                        force_needed = total_cost + 2

                        # Skip if we can afford it now
                        if total_cost <= current_force - self.battle_force_reserve:
                            continue

                        # Can we afford next turn?
                        if force_needed > next_turn_force:
                            continue

                        # Is this a crush at this location?
                        if total_power < power_needed_for_crush:
                            continue

                        # For ground locations, check if characters can deploy there
                        if loc.is_ground:
                            advantage = total_power - enemy_power
                            score = advantage * 10 + loc.their_icons * 15

                            if score > best_score:
                                best_score = score
                                best_opportunity = NextTurnCrushPlan(
                                    target_location_id=loc.card_id,
                                    target_location_name=loc.name,
                                    target_enemy_power=enemy_power,
                                    card_blueprint_ids=[c['blueprint_id'] for c in combo],
                                    card_names=[c.get('name', '?') for c in combo],
                                    total_power=total_power,
                                    total_deploy_cost=total_cost,
                                    force_needed=force_needed,
                                    expected_force_next_turn=next_turn_force,
                                    expected_advantage=advantage,
                                    force_generation=force_generation,
                                )

        if best_opportunity:
            logger.info(f"🔮 NEXT-TURN CRUSH FOUND: {best_opportunity.card_names} -> "
                       f"{best_opportunity.target_location_name} "
                       f"({best_opportunity.total_power} vs {best_opportunity.target_enemy_power}, "
                       f"+{best_opportunity.expected_advantage})")

        return best_opportunity

    def _find_next_turn_bleed_stop_opportunities(
        self,
        board_state,
        locations: List[LocationAnalysis],
        bleed_locations: List[LocationAnalysis],
        all_cards_in_hand: List[Dict],
        current_force: int
    ) -> Optional[NextTurnCrushPlan]:
        """
        Look for opportunities to STOP BLEEDING that become possible by waiting one turn.

        Similar to next-turn crush planning, but for stopping force drains:
        - Finds bleed locations we can't contest THIS turn (not enough force/power)
        - Checks if we could stop the bleed NEXT turn with saved force
        - Only considers plans where we can BEAT the enemy power

        Returns:
            NextTurnCrushPlan if a good bleed-stop opportunity exists, None otherwise
            (Reuses NextTurnCrushPlan structure - works for any "save for next turn" plan)
        """
        if not board_state or not bleed_locations:
            return None

        # Calculate next turn's expected force (if we spend 0 this turn)
        # Must account for reserve deck limits and drain losses
        force_generation = self._estimate_force_generation(locations, board_state)
        reserve_deck = getattr(board_state, 'reserve_deck', 0) or 0

        # Get drain gap from strategic state if available
        drain_gap = 0
        strategic_state = getattr(board_state, 'strategic_state', None)
        if strategic_state and strategic_state.enabled:
            drain_gap = strategic_state.trajectory.current_drain_gap

        # If drain gap is negative, we'll lose that much reserve to drains before next activate
        expected_drain_loss = abs(drain_gap) if drain_gap < 0 else 0
        effective_reserve = max(0, reserve_deck - expected_drain_loss)

        # Can only activate up to effective reserve (minus buffer for destiny draws)
        destiny_buffer = 2
        max_activation = max(0, min(force_generation, effective_reserve - destiny_buffer))
        next_turn_force = current_force + max_activation

        logger.debug(f"🩸🔮 Next-turn bleed analysis: current={current_force}, gen={force_generation}, "
                    f"reserve={reserve_deck}, drain_gap={drain_gap}, effective_reserve={effective_reserve}, "
                    f"max_activation={max_activation}, next={next_turn_force}")

        # Get all cards (including expensive ones we can't afford now)
        all_characters = [c for c in all_cards_in_hand if c.get('is_character')]
        all_starships = [c for c in all_cards_in_hand if c.get('is_starship')]

        best_opportunity = None
        best_score = 0

        # Sort bleed locations by drain severity (highest icons = most urgent)
        bleed_sorted = sorted(bleed_locations, key=lambda x: x.my_icons, reverse=True)

        for loc in bleed_sorted:
            enemy_power = loc.their_power
            icons_at_stake = loc.my_icons  # Icons we're being drained for

            # Need at least +1 advantage to beat them (stop the bleed)
            power_needed = enemy_power + 1

            logger.debug(f"🩸🔮 Checking bleed at {loc.name}: enemy={enemy_power}, icons={icons_at_stake}, need {power_needed} to stop")

            # For ground bleed locations - check characters
            if loc.is_ground:
                for char in all_characters:
                    c_power = char.get('power', 0) or char.get('power_value', 0)
                    c_cost = char.get('cost', 0) or char.get('deploy_value', 0)
                    c_name = char.get('name', 'Unknown')

                    # Skip if can't beat enemy even with this card
                    if c_power <= enemy_power:
                        continue

                    # Skip if we can afford it now (not a "next turn" opportunity)
                    if c_cost <= current_force - self.battle_force_reserve:
                        continue

                    force_needed = c_cost + self.battle_force_reserve

                    # Can we afford this next turn?
                    if force_needed > next_turn_force:
                        continue

                    advantage = c_power - enemy_power
                    # Score: icons saved × 20 (matches STOP BLEEDING scoring) + advantage bonus
                    score = icons_at_stake * 20 + advantage * 5

                    logger.debug(f"🩸🔮   Character: {c_name} = {c_power} power, "
                               f"cost {c_cost}, advantage +{advantage}, score {score}")

                    if score > best_score:
                        best_score = score
                        best_opportunity = NextTurnCrushPlan(
                            target_location_id=loc.card_id,
                            target_location_name=loc.name,
                            target_enemy_power=enemy_power,
                            card_blueprint_ids=[char['blueprint_id']],
                            card_names=[c_name],
                            total_power=c_power,
                            total_deploy_cost=c_cost,
                            force_needed=force_needed,
                            expected_force_next_turn=next_turn_force,
                            expected_advantage=advantage,
                            force_generation=force_generation,
                        )

            # For space bleed locations - check starships
            elif loc.is_space:
                for ship in all_starships:
                    s_power = ship.get('power', 0) or ship.get('power_value', 0)
                    s_cost = ship.get('cost', 0) or ship.get('deploy_value', 0)
                    s_name = ship.get('name', 'Unknown')

                    # Skip unpiloted ships (power would be 0)
                    if ship.get('needs_pilot', False):
                        continue

                    # Skip if can't beat enemy
                    if s_power <= enemy_power:
                        continue

                    # Skip if we can afford it now
                    if s_cost <= current_force - self.battle_force_reserve:
                        continue

                    force_needed = s_cost + self.battle_force_reserve

                    # Can we afford this next turn?
                    if force_needed > next_turn_force:
                        continue

                    advantage = s_power - enemy_power
                    score = icons_at_stake * 20 + advantage * 5

                    logger.debug(f"🩸🔮   Starship: {s_name} = {s_power} power, "
                               f"cost {s_cost}, advantage +{advantage}, score {score}")

                    if score > best_score:
                        best_score = score
                        best_opportunity = NextTurnCrushPlan(
                            target_location_id=loc.card_id,
                            target_location_name=loc.name,
                            target_enemy_power=enemy_power,
                            card_blueprint_ids=[ship['blueprint_id']],
                            card_names=[s_name],
                            total_power=s_power,
                            total_deploy_cost=s_cost,
                            force_needed=force_needed,
                            expected_force_next_turn=next_turn_force,
                            expected_advantage=advantage,
                            force_generation=force_generation,
                        )

        if best_opportunity:
            logger.info(f"🩸🔮 NEXT-TURN BLEED STOP FOUND: {best_opportunity.card_names} -> "
                       f"{best_opportunity.target_location_name} "
                       f"(save {bleed_sorted[0].my_icons if bleed_sorted else '?'} drain/turn, "
                       f"{best_opportunity.total_power} vs {best_opportunity.target_enemy_power})")

        return best_opportunity

    def _find_unpiloted_ships_in_play(self, board_state) -> List[Dict]:
        """
        Find all unpiloted starships/vehicles we have in play.

        These are ships that had a pilot but the pilot was forfeited,
        or ships that we deployed via special rules without a pilot.

        Returns:
            List of dicts with 'card_id', 'name', 'blueprint_id', 'location_id',
            'location_name', 'base_power', 'is_starship', 'is_vehicle' keys.
        """
        unpiloted = []
        if not board_state:
            return unpiloted

        my_player = getattr(board_state, 'my_player_name', None)
        if not my_player:
            return unpiloted

        cards_in_play = getattr(board_state, 'cards_in_play', {})

        for card_id, card in cards_in_play.items():
            # Skip if not ours
            if card.owner != my_player:
                continue

            # CRITICAL: Only consider cards actually AT a location (not in hand, lost pile, etc.)
            # Cards in hand have zone="HAND", cards on board have zone="AT_LOCATION"
            if card.zone != "AT_LOCATION":
                continue

            # Skip placeholder blueprints (hidden cards)
            if card.blueprint_id and card.blueprint_id.startswith('-1_'):
                continue

            metadata = get_card(card.blueprint_id) if card.blueprint_id else None
            if not metadata:
                continue

            # Check if it's a starship or vehicle
            if not (metadata.is_starship or metadata.is_vehicle):
                continue

            # Skip if it has permanent pilot
            if metadata.has_permanent_pilot:
                continue

            # Check if it has a pilot aboard (attached)
            has_pilot_aboard = False
            for attached in card.attached_cards:
                attached_meta = get_card(attached.blueprint_id) if attached.blueprint_id else None
                if attached_meta and attached_meta.is_pilot:
                    has_pilot_aboard = True
                    break

            if has_pilot_aboard:
                continue

            # This ship/vehicle is unpiloted!
            # Get location info from card's location_index
            location_idx = card.location_index
            location_name = None
            location_card_id = None

            # Try to find the location name from board state locations
            # location_index is the position in the locations list (0, 1, 2, etc.)
            locations = getattr(board_state, 'locations', [])
            for loc in locations:
                # Match by location_index (the common field)
                if hasattr(loc, 'location_index') and loc.location_index == location_idx:
                    location_name = getattr(loc, 'name', None) or getattr(loc, 'site_name', None) or getattr(loc, 'system_name', None)
                    location_card_id = getattr(loc, 'card_id', None)
                    break

            # Get base power from metadata
            base_power = metadata.power_value if metadata.power_value else 0

            unpiloted.append({
                'card_id': card_id,
                'name': metadata.title,
                'blueprint_id': card.blueprint_id,
                'location_index': location_idx,
                'location_card_id': location_card_id,
                'location_name': location_name,
                'base_power': base_power,
                'is_starship': metadata.is_starship,
                'is_vehicle': metadata.is_vehicle,
            })
            logger.warning(f"🚀 Found UNPILOTED ship in play: {metadata.title} (#{card_id}) at {location_name or 'unknown (idx=' + str(location_idx) + ')'}")

        return unpiloted

    def _find_ships_with_pilot_capacity(self, board_state) -> List[Dict]:
        """
        Find all starships/vehicles we have in play that can accept additional pilots.

        Ships may have "May add X pilots" in their gametext, allowing additional
        pilots to be deployed aboard for extra power. This is a key reinforcement
        strategy - adding a pilot to an existing ship boosts its power significantly.

        Returns:
            List of dicts with:
            - 'card_id', 'name', 'blueprint_id': Ship identification
            - 'location_index', 'location_card_id', 'location_name': Where the ship is
            - 'current_power': Current power contribution
            - 'pilot_capacity': Max pilots allowed (from gametext)
            - 'pilots_aboard': Current number of pilots aboard
            - 'remaining_capacity': How many more pilots can be added
            - 'is_starship', 'is_vehicle': Ship type flags
        """
        ships_with_capacity = []
        if not board_state:
            return ships_with_capacity

        my_player = getattr(board_state, 'my_player_name', None)
        if not my_player:
            return ships_with_capacity

        cards_in_play = getattr(board_state, 'cards_in_play', {})

        for card_id, card in cards_in_play.items():
            # Skip if not ours
            if card.owner != my_player:
                continue

            # Only consider cards actually AT a location (not in hand, lost pile, etc.)
            if card.zone != "AT_LOCATION":
                continue

            # Skip placeholder blueprints (hidden cards)
            if card.blueprint_id and card.blueprint_id.startswith('-1_'):
                continue

            metadata = get_card(card.blueprint_id) if card.blueprint_id else None
            if not metadata:
                continue

            # Check if it's a starship or vehicle
            if not (metadata.is_starship or metadata.is_vehicle):
                continue

            # Check pilot capacity (from gametext like "May add 3 pilots")
            capacity = metadata.pilot_capacity
            if capacity <= 0:
                continue  # This ship doesn't accept pilots

            # Count current pilots aboard
            pilots_aboard = 0
            for attached in card.attached_cards:
                attached_meta = get_card(attached.blueprint_id) if attached.blueprint_id else None
                if attached_meta and attached_meta.is_pilot:
                    pilots_aboard += 1

            # Calculate remaining capacity
            remaining = capacity - pilots_aboard
            if remaining <= 0:
                continue  # Ship is full

            # Get location info
            location_idx = card.location_index
            location_name = None
            location_card_id = None

            locations = getattr(board_state, 'locations', [])
            for loc in locations:
                if hasattr(loc, 'location_index') and loc.location_index == location_idx:
                    location_name = getattr(loc, 'name', None) or getattr(loc, 'site_name', None) or getattr(loc, 'system_name', None)
                    location_card_id = getattr(loc, 'card_id', None)
                    break

            # Get current power - use power_at_location if available, else base power
            current_power = metadata.power_value if metadata.power_value else 0

            ships_with_capacity.append({
                'card_id': card_id,
                'name': metadata.title,
                'blueprint_id': card.blueprint_id,
                'location_index': location_idx,
                'location_card_id': location_card_id,
                'location_name': location_name,
                'current_power': current_power,
                'pilot_capacity': capacity,
                'pilots_aboard': pilots_aboard,
                'remaining_capacity': remaining,
                'is_starship': metadata.is_starship,
                'is_vehicle': metadata.is_vehicle,
            })
            logger.info(f"🛳️ Found ship with pilot capacity: {metadata.title} (#{card_id}) at {location_name or 'unknown'} "
                       f"[{pilots_aboard}/{capacity} pilots, {remaining} slots open]")

        return ships_with_capacity

    def _get_dynamic_threshold(self, locations: List['LocationAnalysis'],
                                is_space: bool, turn_number: int,
                                life_force: int = 60) -> int:
        """
        Calculate dynamic deploy threshold based on game state.

        Threshold adjustments (applied in order):
        1. Early game (turn < 4) with no contested locations: -3 (relaxed)
           This allows 3-power characters to deploy and enable force drains.
        2. Late game with low life force: additional decay
           - life_force < 10: -3 (desperate - deploy anything)
           - life_force < 20: -2 (critical - very aggressive)
           - life_force < 30: -1 (urgent - somewhat aggressive)

        Ground and space are tracked SEPARATELY:
        - Contested ground doesn't raise space threshold
        - Contested space doesn't raise ground threshold

        This allows the bot to deploy more freely in the domain the opponent
        isn't contesting, while being conservative where needed.

        Args:
            locations: All analyzed locations on board
            is_space: True for space threshold, False for ground
            turn_number: Current turn number
            life_force: Total remaining life force (reserve + used + force pile)

        Returns:
            Deploy threshold to use (minimum 1)
        """
        threshold = self.deploy_threshold
        domain = 'space' if is_space else 'ground'

        # EARLY GAME RELAXATION: Before turn 4 with no contested locations
        early_game_relaxed = False
        if turn_number < 4:
            # Check for contested locations in the relevant domain only
            has_contested = False
            for loc in locations:
                # Skip locations without both players present
                if loc.my_power <= 0 or loc.their_power <= 0:
                    continue

                # Check the appropriate domain
                if is_space and loc.is_space:
                    has_contested = True
                    logger.debug(f"   📊 Contested space found: {loc.name} ({loc.my_power} vs {loc.their_power})")
                    break
                elif not is_space and loc.is_ground:
                    has_contested = True
                    logger.debug(f"   📊 Contested ground found: {loc.name} ({loc.my_power} vs {loc.their_power})")
                    break

            if not has_contested:
                # REACT THREAT CHECK: Before relaxing, check for large enemy buildups
                # that could react/move to crush weak deploys. If opponent has significant
                # power anywhere in the same domain, they can move to an "uncontested"
                # location and crush a lone weak character we just deployed.
                has_react_threat = False
                for loc in locations:
                    # Check enemy power in the relevant domain
                    if is_space and loc.is_space and loc.their_power >= get_react_threat_threshold():
                        has_react_threat = True
                        logger.debug(f"   ⚠️ React threat in space: {loc.name} has {loc.their_power} enemy power")
                        break
                    elif not is_space and loc.is_ground and loc.their_power >= get_react_threat_threshold():
                        has_react_threat = True
                        logger.debug(f"   ⚠️ React threat on ground: {loc.name} has {loc.their_power} enemy power")
                        break

                if has_react_threat:
                    # Don't relax threshold - enemy can react/move to crush weak deploys
                    logger.debug(f"   📊 No threshold relaxation ({domain}): react threat exists")
                else:
                    # Safe to relax - no large enemy buildups to threaten weak deploys
                    # Use config-driven minimum floor to prevent deploying weak characters alone
                    min_floor = get_min_establish_power()
                    if is_space:
                        threshold = max(min_floor, threshold - 2)
                    else:
                        threshold = max(min_floor, threshold - 2)  # Ground uses same floor now
                    early_game_relaxed = True

        # LATE GAME LIFE FORCE DECAY: Lower threshold when losing badly
        # Only allow threshold to drop below min_establish_power when truly desperate
        min_floor = get_min_establish_power()
        life_force_decay = 0
        if life_force < 10:
            life_force_decay = 2  # Desperate: can go slightly below floor
            threshold = max(min_floor - 1, threshold - life_force_decay)
        elif life_force < 20:
            life_force_decay = 1  # Critical: stay at floor minimum
            threshold = max(min_floor, threshold - life_force_decay)
        elif life_force < 30:
            life_force_decay = 1  # Urgent: slightly more aggressive but respect floor
            threshold = max(min_floor, threshold - life_force_decay)

        # Build log message
        adjustments = []
        if early_game_relaxed:
            adjustments.append(f"early game -2")
        if life_force_decay > 0:
            adjustments.append(f"life force {life_force} -{life_force_decay}")

        if adjustments:
            logger.debug(f"   📊 Dynamic threshold ({domain}): {threshold} ({', '.join(adjustments)})")
        else:
            logger.debug(f"   📊 Dynamic threshold ({domain}): {threshold} (full threshold, turn {turn_number})")

        return threshold

    def _calculate_force_drain_gap(self, locations: List['LocationAnalysis']) -> Tuple[int, int, int, List['LocationAnalysis']]:
        """
        Calculate the force drain economy - how much they drain us vs we drain them.

        Force drains happen at locations where:
        - One player has presence (power > 0) and the other doesn't
        - The controlling player drains for their opponent's icons

        IMPORTANT: "Bleed locations" for STOP BLEEDING plans must have:
        - Enemy presence but no enemy CARDS there (their_icons > 0 means they CAN deploy)
        - Actually NO: bleed means they ARE draining us, so their_power > 0
        - But for STOP BLEEDING to work, we need to be able to contest without
          requiring threshold power - so we want UNCONTESTED locations where
          opponent can drain from a SAFE position (no enemy cards to fight)

        The safest "stop bleeding" targets are locations where:
        - We have icons (my_icons > 0) - they drain us for these
        - They have NO cards there (their_power == 0) - so we can stop the drain
          by just showing up, no battle required
        - BUT there's a nuance: if their_power == 0, they're NOT draining us!
          Force drains require presence.

        REVISED: For counting drain economy, enemy drains us at locations where:
        - their_power > 0 (they have presence)
        - my_power == 0 (we have no presence)
        - my_icons > 0 (they drain us for our icons)

        For "STOP BLEEDING" targets (presence-only plans), we want locations where:
        - We're being drained (above conditions)
        - AND it's GROUND (so characters can deploy)
        - AND the enemy force is small enough that presence could help

        Returns:
            Tuple of:
            - their_drain: Total icons opponent drains from us per turn
            - our_drain: Total icons we drain from them per turn
            - drain_gap: our_drain - their_drain (negative = we're losing)
            - bleed_locations: GROUND locations where opponent drains us WITH LOW enemy power
        """
        their_drain = 0
        our_drain = 0
        bleed_locations = []

        # Threshold for "low enemy power" - below this, presence-only makes sense
        # Above this, we should use normal threshold logic to contest
        low_enemy_threshold = get_low_enemy_threshold()

        for loc in locations:
            # Opponent drains us: they have presence, we don't
            # They drain for OUR icons (my_icons)
            if loc.their_power > 0 and loc.my_power == 0 and loc.my_icons > 0:
                their_drain += loc.my_icons
                # Add to bleed_locations if enemy power is LOW enough for presence-only
                # Works for both ground (characters) and space (starships)
                if loc.their_power <= low_enemy_threshold:
                    bleed_locations.append(loc)
                    domain = "space" if loc.is_space else "ground"
                    logger.debug(f"   🩸 BLEED ({domain}, contestable): {loc.name} - opponent drains {loc.my_icons} icons, enemy power {loc.their_power}")
                else:
                    domain = "space" if loc.is_space else "ground"
                    logger.debug(f"   🩸 BLEED ({domain}, high threat): {loc.name} - opponent drains {loc.my_icons} icons, enemy power {loc.their_power}")

            # We drain opponent: we have presence, they don't
            # We drain for THEIR icons (their_icons)
            if loc.my_power > 0 and loc.their_power == 0 and loc.their_icons > 0:
                our_drain += loc.their_icons
                logger.debug(f"   💧 DRAIN: {loc.name} - we drain {loc.their_icons} icons")

        drain_gap = our_drain - their_drain

        if their_drain > 0 or our_drain > 0:
            emoji = "🩸" if drain_gap < 0 else ("💧" if drain_gap > 0 else "⚖️")
            logger.info(f"   {emoji} FORCE DRAIN ECONOMY: We drain {our_drain}, they drain {their_drain} = gap {drain_gap:+d}")

        return their_drain, our_drain, drain_gap, bleed_locations

    def _evaluate_hold_for_bleeding(
        self,
        domain: str,  # "ground" or "space"
        bleeding_locations: List['LocationAnalysis'],
        deployable_power: int,
        force_pile: int,
        reserve_deck: int,
        current_turn: int,
        board_state: 'BoardState'
    ) -> Tuple[bool, str, float]:
        """
        Evaluate whether to HOLD and draw cards instead of skipping/deploying sub-optimally
        when we can't meaningfully contest a bleeding location.

        This is better than deploying to an unrelated space location when we're bleeding
        on ground (or vice versa).

        Args:
            domain: "ground" or "space" - what type of cards we need
            bleeding_locations: Locations where we're being drained
            deployable_power: Total power we can currently deploy
            force_pile: Cards in force pile (available to draw)
            reserve_deck: Cards remaining in reserve deck
            current_turn: Current game turn
            board_state: Current board state

        Returns:
            (should_hold, reason, expected_value)
        """
        from engine.deck_tracker import get_deck_tracker

        # Don't hold if we can't draw anything
        if force_pile < 1:
            return False, "No force pile to draw from", 0.0

        # Don't hold too late in game - few cards remaining means low probability
        total_cards_remaining = force_pile + reserve_deck
        if total_cards_remaining < 8:
            return False, f"Late game - only {total_cards_remaining} cards remaining", 0.0

        # Don't hold too many turns in a row
        consecutive_holds = getattr(board_state, 'consecutive_hold_turns', 0)
        if consecutive_holds >= 2:
            return False, f"Already held {consecutive_holds} turns - must act", 0.0

        tracker = get_deck_tracker()
        if not tracker.deck_loaded:
            return False, "Deck tracker not loaded", 0.0

        # Calculate total bleeding per turn
        total_bleed = sum(loc.my_icons for loc in bleeding_locations if loc.their_power > 0)
        if total_bleed == 0:
            return False, "Not actually bleeding", 0.0

        # Calculate worst location power deficit
        worst_deficit = 0
        worst_location = None
        for loc in bleeding_locations:
            if loc.their_power > 0:
                deficit = loc.their_power - deployable_power
                if deficit > worst_deficit:
                    worst_deficit = deficit
                    worst_location = loc

        # Determine what card types we need
        if domain == "ground":
            needed_types = ["Character", "Vehicle"]
            p_draw_needed = tracker.probability_draw_type("Character") + tracker.probability_draw_type("Vehicle")
        else:  # space
            needed_types = ["Starship"]
            p_draw_needed = tracker.probability_draw_type("Starship")

        # Calculate expected power from drawing
        remaining = tracker.get_remaining_in_reserve()
        total_needed_power = 0
        needed_count = 0
        for bp_id, count in remaining.items():
            stats = tracker._card_stats.get(bp_id)
            if stats and stats.card_type in needed_types:
                total_needed_power += stats.power * count
                needed_count += count

        avg_power_if_draw = total_needed_power / needed_count if needed_count > 0 else 0

        # Calculate expected draws this turn (force pile size, up to 6)
        max_draws = min(force_pile, 6)

        # Probability of drawing at least one needed card in max_draws attempts
        # P(at least 1) = 1 - P(none)^draws
        p_at_least_one = 1.0 - ((1.0 - p_draw_needed) ** max_draws) if p_draw_needed > 0 else 0.0

        # Expected power gain from holding
        expected_power_gain = p_at_least_one * avg_power_if_draw

        # Calculate value of holding vs not holding
        # Value of stopping bleed = icons_saved * expected_turns_to_recover
        # If we hold, we lose 1 turn of bleed but might gain enough power to contest
        turns_to_recover = 2  # Assume 2 turns to set up after drawing
        bleed_cost = total_bleed  # 1 turn of bleeding

        # Can we realistically contest next turn with the expected power?
        expected_total_power = deployable_power + expected_power_gain
        can_contest_next_turn = expected_total_power >= worst_deficit * 0.7  # Within 70% of deficit

        # Decision criteria:
        # 1. High probability of drawing what we need (>30%)
        # 2. Expected power gain would let us contest
        # 3. Bleeding is significant (2+ icons)
        should_hold = (
            p_at_least_one >= 0.30 and
            can_contest_next_turn and
            total_bleed >= 2
        )

        if should_hold:
            reason = (f"HOLD FOR {domain.upper()} DRAW: P(draw needed)={p_at_least_one:.0%} in {max_draws} draws, "
                     f"E[power]={expected_power_gain:.1f}, can contest {worst_location.name if worst_location else 'location'} next turn "
                     f"(bleed={total_bleed}/turn)")
            expected_value = expected_power_gain - bleed_cost
        else:
            reasons = []
            if p_at_least_one < 0.30:
                reasons.append(f"low P(draw)={p_at_least_one:.0%}")
            if not can_contest_next_turn:
                reasons.append(f"can't contest (need {worst_deficit}, expect {expected_total_power:.0f})")
            if total_bleed < 2:
                reasons.append(f"minor bleed ({total_bleed})")
            reason = f"Not holding: {', '.join(reasons)}"
            expected_value = 0.0

        logger.info(f"   🎰 Hold evaluation ({domain}): {reason}")

        return should_hold, reason, expected_value

    def _generate_presence_only_plans(
        self,
        characters: List[Dict],
        vehicles: List[Dict],
        bleed_locations: List['LocationAnalysis'],
        force_budget: int,
        locations: List['LocationAnalysis'],
        pilots: List[Dict] = None,
        starships: List[Dict] = None
    ) -> List[Tuple[List['DeploymentInstruction'], int, float]]:
        """
        Generate "STOP THE BLEEDING" plans that deploy ANY presence to high-drain locations.

        These plans have REDUCED power threshold - we deploy even when outpowered.
        The goal is to stop force drains, not win battles.
        Rationale: Presence stops drains even if we can't win a battle.
        Even if enemy attacks and wins, we saved icons for at least 1 turn.

        Only skips true suicide missions (<40% of enemy power).

        Handles both:
        - Ground bleed: characters/vehicles to ground locations
        - Space bleed: starships to space locations

        Scoring: Based primarily on icons saved per turn, with penalty for risky deploys.
        """
        plans = []

        if not bleed_locations:
            return plans

        # Separate ground and space bleed locations
        ground_bleeds = [loc for loc in bleed_locations if loc.is_ground]
        space_bleeds = [loc for loc in bleed_locations if loc.is_space]

        # Combine characters and piloted vehicles for ground
        affordable_chars = [c for c in characters if c['cost'] <= force_budget and c.get('power', 0) >= 1]
        affordable_piloted_vehicles = [
            v for v in vehicles
            if v['cost'] <= force_budget and v.get('has_permanent_pilot', False) and v.get('power', 0) >= 1
        ]
        ground_deployable = affordable_chars + affordable_piloted_vehicles

        # Get affordable starships for space (piloted or with permanent pilot)
        affordable_ships = []
        if starships:
            for ship in starships:
                if ship['cost'] > force_budget:
                    continue
                power = ship.get('power', 0)
                # Unpiloted ships need a pilot to have meaningful power
                if ship.get('needs_pilot', False):
                    # For now, skip unpiloted ships in presence plans
                    # (complex to handle pilot combos here)
                    continue
                if power >= 1:
                    affordable_ships.append(ship)

        has_ground = ground_deployable and ground_bleeds
        has_space = affordable_ships and space_bleeds

        if not has_ground and not has_space:
            return plans

        bleed_count = len(ground_bleeds) + len(space_bleeds)
        logger.info(f"   🩸 STOP THE BLEEDING: Generating presence plans for {bleed_count} drain locations "
                   f"({len(ground_bleeds)} ground, {len(space_bleeds)} space)")

        # Sort bleed locations by drain severity (highest icons first)
        all_bleeds_sorted = sorted(bleed_locations, key=lambda x: x.my_icons, reverse=True)

        for target_loc in all_bleeds_sorted:
            icons_at_stake = target_loc.my_icons  # Icons we'd stop being drained for

            # Choose deployables based on location type
            if target_loc.is_ground:
                deployable_pool = ground_deployable
            elif target_loc.is_space:
                deployable_pool = affordable_ships
            else:
                continue

            if not deployable_pool:
                continue

            # Filter deployables for this location (check restrictions)
            location_deployables = []
            for card in deployable_pool:
                # Check deploy system restrictions
                restrictions = card.get('deploy_restriction_systems', [])
                if restrictions:
                    loc_clean = target_loc.name.lstrip('•').strip()
                    can_deploy = False
                    for system in restrictions:
                        system_lower = system.lower()
                        loc_lower = loc_clean.lower()
                        if loc_lower.startswith(system_lower):
                            can_deploy = True
                            break
                        if ':' in loc_clean:
                            loc_system = loc_clean.split(':')[0].strip().lower()
                            if loc_system == system_lower:
                                can_deploy = True
                                break
                    if not can_deploy:
                        continue

                # Vehicles need exterior locations
                if card.get('is_vehicle') and not target_loc.is_exterior:
                    continue

                location_deployables.append(card)

            if not location_deployables:
                continue

            # Generate a plan for EACH affordable card
            # The key difference from normal plans: REDUCED threshold
            # For STOP_THE_BLEEDING, we deploy presence to stop drains even when outpowered
            # Rationale: Even if enemy attacks and wins, we saved icons for at least 1 turn
            for card in location_deployables:
                if card['cost'] > force_budget:
                    continue

                card_power = card['power']

                # Only skip true suicide missions: less than 40% of enemy power
                # e.g. 1p vs 10p is suicide (10%), 3p vs 4p is valid contestation (75%)
                if target_loc.their_power > 0:
                    power_ratio = card_power / target_loc.their_power
                    if power_ratio < 0.4:
                        logger.debug(f"      🩸 Skipping {card['name']} - {card_power}p is suicide vs enemy {target_loc.their_power}p ({power_ratio:.0%})")
                        continue

                # Create "STOP BLEEDING" instruction
                domain = "space" if target_loc.is_space else "ground"
                is_risky = target_loc.their_power > card_power
                risk_note = " (risky)" if is_risky else ""
                reason = f"STOP BLEEDING ({domain}): {target_loc.name} (save {icons_at_stake} drain/turn with {card_power}p{risk_note})"

                instructions = [DeploymentInstruction(
                    card_blueprint_id=card['blueprint_id'],
                    card_name=card['name'],
                    target_location_id=target_loc.card_id,
                    target_location_name=target_loc.name,
                    priority=2,
                    reason=reason,
                    power_contribution=card['power'],
                    deploy_cost=card['cost'],
                )]

                force_remaining = force_budget - card['cost']

                # Score PRIMARILY on icons saved, with power as secondary factor
                # Icons saved × 20 (each icon = 20 points, same as DENY DRAIN bonus)
                # Plus small power bonus (power × 2)
                # Plus opponent icons at location × 10 (we could drain THEM)
                icons_saved_value = icons_at_stake * 20
                drain_potential = target_loc.their_icons * 10  # We could drain them!
                power_value = card_power * 2
                score = icons_saved_value + drain_potential + power_value

                # Penalty for risky deploys (enemy stronger than us)
                # We still want to do them to stop bleeding, but prefer safer options
                if is_risky:
                    power_deficit = target_loc.their_power - card_power
                    risk_penalty = power_deficit * 3  # -3 per power we're behind
                    score -= risk_penalty

                plans.append((instructions, force_remaining, score))

                logger.debug(f"      🩸 PRESENCE ({domain}) plan: {card['name']} → {target_loc.name} "
                           f"(save {icons_at_stake}, power {card_power}{', risky -' + str(risk_penalty) if is_risky else ''}, score={score:.0f})")

        if plans:
            logger.info(f"   🩸 Generated {len(plans)} STOP BLEEDING plans")

        return plans

    def _calculate_plan_reserve(self, instructions: List['DeploymentInstruction'],
                                 locations: List['LocationAnalysis'],
                                 flee_count: int = 0) -> int:
        """
        Calculate the force reserve needed for a specific plan.

        Dynamic reserve based on what the plan requires:
        - Base: battle_force_reserve (default 1) for effects/reactions
        - +1 if plan deploys to a contested location (need to initiate battle)
        - +1 per card that needs to flee (movement costs)

        Args:
            instructions: The deployment instructions in the plan
            locations: All analyzed locations on board
            flee_count: Number of cards that need to flee (from flee plan)

        Returns:
            Total force that should be reserved
        """
        reserve = self.battle_force_reserve  # Base reserve (1)

        # Check if any deployment targets a contested location
        contested_targets = set()
        for inst in instructions:
            if inst.target_location_name:
                for loc in locations:
                    if loc.name == inst.target_location_name and loc.their_power > 0:
                        contested_targets.add(inst.target_location_name)
                        break

        # Add 1 force for battle initiation if deploying to contested location
        if contested_targets:
            reserve += 1
            logger.debug(f"   📋 Reserve +1 for battle at: {contested_targets}")

        # Add 1 force per card that needs to flee
        if flee_count > 0:
            reserve += flee_count
            logger.debug(f"   📋 Reserve +{flee_count} for flee movement")

        return reserve

    def _parse_icon_string(self, icon_value) -> int:
        """
        Parse icon string (e.g., "2", "2*", "") to integer.
        Icons can be strings like "2" or "2*" (battleground marker).
        """
        if icon_value is None:
            return 0
        if isinstance(icon_value, int):
            return icon_value
        if isinstance(icon_value, str):
            if not icon_value or icon_value == "0":
                return 0
            try:
                # Remove battleground marker (*) and parse
                return int(icon_value.replace("*", "").strip() or "0")
            except ValueError:
                return 1 if icon_value else 0
        return 0

    def _find_optimal_combination(
        self,
        cards: List[Dict],
        budget: int,
        power_goal: int,
        must_exceed: bool = False,
        require_ability: bool = True
    ) -> Tuple[List[Dict], int, int]:
        """
        Find the optimal combination of cards to deploy within budget.

        Uses efficiency-based selection: prioritize cards that give the most
        power per Force spent, while still achieving the power goal.

        ABILITY AWARENESS (require_ability=True):
        - Prefers combinations with total ability >= 4 (can draw battle destiny)
        - If we can't get ability >= 4, requires EXTRA power to compensate
          (opponent can draw destiny, so we need margin)
        - This prevents deploying e.g. two 2-power/1-ability chars (4 power, 2 ability)
          that lose to one 4-power/4-ability char

        Args:
            cards: List of card dicts with 'power', 'cost', 'name', 'ability', etc.
            budget: Maximum Force we can spend
            power_goal: Power we're trying to reach
            must_exceed: If True, we need power > goal (for beating opponent)
            require_ability: If True, prefer combos with ability >= 4

        Returns:
            (selected_cards, total_power, total_cost)
        """
        if not cards or budget <= 0:
            return ([], 0, 0)

        # Filter to affordable cards
        affordable = [c for c in cards if c['cost'] <= budget]
        if not affordable:
            return ([], 0, 0)

        # For small card counts, try all combinations to find optimal
        # This is O(2^n) but n is typically < 10 for hand size
        if len(affordable) <= 8:
            return self._find_optimal_brute_force(affordable, budget, power_goal, must_exceed, require_ability)

        # For larger hands, use greedy efficiency-based approach
        return self._find_optimal_greedy(affordable, budget, power_goal, must_exceed, require_ability)

    def _find_optimal_brute_force(
        self,
        cards: List[Dict],
        budget: int,
        power_goal: int,
        must_exceed: bool,
        require_ability: bool = True
    ) -> Tuple[List[Dict], int, int]:
        """
        Try all combinations to find the best one.

        Best = achieves power goal with maximum power within budget.
        Once goal is achieved, prefer higher power (for scoring) over cheaper cost.

        ABILITY AWARENESS:
        When require_ability=True, we track whether combo has ability >= 4 (battle destiny).
        - Prefer combos with ability >= 4 over those without
        - If no combo has ability >= 4, prefer combos with MORE power (compensate for no destiny)
        - The ABILITY_POWER_BONUS effectively adds 3-4 power to ability-enabled combos in comparison
        """
        from itertools import combinations

        ABILITY_THRESHOLD = 4  # Need 4+ ability to draw battle destiny
        # When comparing combos without ability, we need this much extra power
        # to compensate for opponent potentially drawing destiny
        ABILITY_POWER_COMPENSATION = 3

        best_combo = []
        best_power = 0
        best_cost = float('inf')
        best_ability = 0
        best_achieves_goal = False
        best_has_ability = False  # Does best combo have ability >= 4?

        # Try all possible subset sizes
        for size in range(1, len(cards) + 1):
            for combo in combinations(cards, size):
                total_cost = sum(c['cost'] for c in combo)
                if total_cost > budget:
                    continue

                total_power = sum(c['power'] for c in combo)
                total_ability = sum(c.get('ability', 0) for c in combo) if require_ability else 0
                has_ability = total_ability >= ABILITY_THRESHOLD

                # Check if this achieves the goal
                # ABILITY COMPENSATION: Only in battle scenarios (must_exceed=True)
                # where we need to actually beat enemy power and destiny draws matter.
                # For threshold scenarios (establish), we just need presence.
                effective_goal = power_goal
                if require_ability and not has_ability and must_exceed:
                    # In a battle, we need extra power to compensate for opponent's destiny draw
                    effective_goal = power_goal + ABILITY_POWER_COMPENSATION

                if must_exceed:
                    achieves_goal = total_power > effective_goal
                else:
                    achieves_goal = total_power >= effective_goal

                # Selection priority (highest to lowest):
                # 1. Achieves goal (power threshold met)
                # 2. Has ability >= 4 (can draw battle destiny)
                # 3. Higher power (for battle scenarios) OR cheaper cost (for threshold scenarios)
                def is_better_combo():
                    nonlocal best_achieves_goal, best_has_ability, best_power, best_cost, best_ability

                    # Goal achievement is highest priority
                    if achieves_goal and not best_achieves_goal:
                        return True
                    if not achieves_goal and best_achieves_goal:
                        return False

                    # Both achieve goal OR both don't - compare ability
                    if require_ability:
                        if has_ability and not best_has_ability:
                            return True  # Ability-enabled wins
                        if not has_ability and best_has_ability:
                            return False  # Can't beat ability-enabled

                    # Same ability status - compare by power/cost
                    if achieves_goal:
                        if must_exceed:
                            # Battle: prefer more power, then cheaper
                            if total_power > best_power:
                                return True
                            if total_power == best_power and total_cost < best_cost:
                                return True
                        else:
                            # Threshold: prefer ability first, then cheaper
                            if require_ability and total_ability > best_ability:
                                return True  # More ability is better even after threshold
                            if total_cost < best_cost:
                                return True
                            if total_cost == best_cost and total_power < best_power:
                                return True
                    else:
                        # Neither achieves goal - prefer more power
                        if total_power > best_power:
                            return True

                    return False

                if is_better_combo():
                    best_combo = list(combo)
                    best_power = total_power
                    best_cost = total_cost
                    best_ability = total_ability
                    best_achieves_goal = achieves_goal
                    best_has_ability = has_ability

        # Log ability awareness decision
        if require_ability and best_combo:
            if best_has_ability:
                logger.debug(f"   🎯 Selected combo with ability {best_ability} (can draw destiny)")
            else:
                logger.debug(f"   ⚠️ Selected combo with ability {best_ability} (NO destiny draw, compensated with power)")

        return (best_combo, best_power, int(best_cost) if best_cost != float('inf') else 0)

    def _find_optimal_greedy(
        self,
        cards: List[Dict],
        budget: int,
        power_goal: int,
        must_exceed: bool,
        require_ability: bool = True
    ) -> Tuple[List[Dict], int, int]:
        """
        Greedy approach for larger hands: sort by efficiency (power/cost).

        ABILITY AWARENESS:
        - Prefers high-ability cards to reach ability >= 4 threshold
        - If ability goal not met, continues adding cards to get more power
        - Sort key includes ability as tiebreaker
        """
        ABILITY_THRESHOLD = 4  # Need 4+ ability to draw battle destiny
        ABILITY_POWER_COMPENSATION = 3  # Extra power needed without destiny

        # Sort by composite score: efficiency + ability bonus
        # High-ability cards get a boost to be selected first
        def card_score(c):
            efficiency = (c['power'] / c['cost']) if c['cost'] > 0 else float('inf')
            ability_bonus = 0
            if require_ability and c.get('ability', 0) >= 3:
                # Cards with 3+ ability get a significant boost
                ability_bonus = c.get('ability', 0) * 0.5
            return efficiency + ability_bonus

        sorted_cards = sorted(cards, key=card_score, reverse=True)

        selected = []
        total_power = 0
        total_cost = 0
        total_ability = 0
        remaining_budget = budget

        for card in sorted_cards:
            if card['cost'] <= remaining_budget:
                selected.append(card)
                total_power += card['power']
                total_cost += card['cost']
                total_ability += card.get('ability', 0)
                remaining_budget -= card['cost']

                # Calculate effective power goal based on ability
                # ABILITY COMPENSATION: Only in battle scenarios (must_exceed=True)
                has_ability = total_ability >= ABILITY_THRESHOLD
                effective_goal = power_goal
                if require_ability and not has_ability and must_exceed:
                    # In a battle, need extra power to compensate for opponent destiny
                    effective_goal = power_goal + ABILITY_POWER_COMPENSATION

                # Stop if we've achieved both goals
                if must_exceed and total_power > effective_goal and (has_ability or not require_ability):
                    break
                elif not must_exceed and total_power >= effective_goal and (has_ability or not require_ability):
                    break

        # Log ability awareness decision
        if require_ability and selected:
            has_ability = total_ability >= ABILITY_THRESHOLD
            if has_ability:
                logger.debug(f"   🎯 Greedy selected {len(selected)} cards with ability {total_ability} (can draw destiny)")
            else:
                logger.debug(f"   ⚠️ Greedy selected {len(selected)} cards with ability {total_ability} (NO destiny, need extra power)")

        return (selected, total_power, total_cost)

    def _score_plan(self, instructions: List[DeploymentInstruction], locations: List[LocationAnalysis],
                    turn_number: int = 1) -> float:
        """
        Score a deployment plan based on strategic value.

        Scoring factors:
        1. FAVORABLE battles (power advantage >= threshold) - Highest priority
        2. GUARANTEED CONTROL (0 enemy, we have presence) - Very valuable
        3. Icons denied (their_icons at target locations)
        4. Power deployed - base value
        5. MID-LATE GAME REINFORCEMENT (turn > 3) - Bonus for reinforcing weak positions

        KEY INSIGHT: A guaranteed win at an empty location is often BETTER than
        a marginal fight. Marginal fights (+1 to +3 power) are risky due to
        destiny variance. Only FAVORABLE fights (+4 or more) should get big bonuses.
        """
        if not instructions:
            return 0.0

        ABILITY_THRESHOLD = 4  # Need 4+ ability to draw battle destiny

        score = 0.0
        target_loc_ids = set()
        power_by_location = {}  # Track power going to each location
        cards_by_location = {}  # Track card count per location for Barrier awareness
        ability_by_location = {}  # Track ability for battle destiny eligibility

        for inst in instructions:
            # Find the target location
            target_loc = None
            if inst.target_location_id:
                target_loc_ids.add(inst.target_location_id)
                for loc in locations:
                    if loc.card_id == inst.target_location_id:
                        target_loc = loc
                        break

                # Track power by location for crush calculation
                if inst.target_location_id not in power_by_location:
                    power_by_location[inst.target_location_id] = 0
                power_by_location[inst.target_location_id] += inst.power_contribution

                # Track card count by location for Barrier awareness
                if inst.target_location_id not in cards_by_location:
                    cards_by_location[inst.target_location_id] = 0
                cards_by_location[inst.target_location_id] += 1

                # Track ability by location for battle destiny eligibility
                if inst.target_location_id not in ability_by_location:
                    ability_by_location[inst.target_location_id] = 0
                # Get ability from card data (more reliable than instruction field)
                card_meta = get_card(inst.card_blueprint_id)
                if card_meta:
                    ability_by_location[inst.target_location_id] += card_meta.ability_value or 0

            # Power contribution (base value)
            score += inst.power_contribution * 2

        # === ANALYZE EACH TARGET LOCATION ===
        for loc_id, our_power in power_by_location.items():
            target_loc = None
            for loc in locations:
                if loc.card_id == loc_id:
                    target_loc = loc
                    break

            if not target_loc:
                continue

            if target_loc.their_power > 0:
                # === CONTESTED LOCATION ===
                power_advantage = our_power - target_loc.their_power

                # CRITICAL: If we have icons here and they control it, they drain US!
                # Contesting/winning prevents this drain, which is very valuable.
                deny_drain_bonus = 0
                if target_loc.my_icons > 0:
                    # They're draining us for our icons - contesting stops this!
                    deny_drain_bonus = target_loc.my_icons * 20
                    logger.debug(f"   🛡️ DENY DRAIN at {target_loc.name}: +{deny_drain_bonus} "
                               f"(prevent drain of {target_loc.my_icons} icons)")

                # WINNING BONUS: When we WIN, we get control and can drain their icons!
                # This is IN ADDITION to the fight bonus - we get ongoing value.
                win_control_bonus = 0
                if power_advantage > 0 and target_loc.their_icons > 0:
                    # When we win, we'll drain their icons like at an empty location
                    win_control_bonus = target_loc.their_icons * 15
                    logger.debug(f"   🎯 WIN CONTROL at {target_loc.name}: +{win_control_bonus} "
                               f"(will drain {target_loc.their_icons} icons)")

                if power_advantage >= get_battle_favorable_threshold():
                    # FAVORABLE FIGHT: We have solid advantage (+4 or more)
                    # This is a true "crush" - give big bonus
                    crush_bonus = 50 + (power_advantage * 10) + deny_drain_bonus + win_control_bonus
                    score += crush_bonus
                    logger.debug(f"   💥 FAVORABLE FIGHT at {target_loc.name}: +{crush_bonus} "
                               f"({our_power} vs {target_loc.their_power}, +{power_advantage} advantage)")
                elif power_advantage > 0:
                    # MARGINAL FIGHT: We'd win but it's risky (+1 to +3)
                    # Still valuable because:
                    # 1. We stop their drain (deny_drain_bonus)
                    # 2. We start draining them (win_control_bonus)
                    # 3. We remove their presence from the board
                    # Increase base from 10 to 25 to make winning more attractive than establishing
                    marginal_bonus = 25 + (power_advantage * 5) + deny_drain_bonus + win_control_bonus
                    score += marginal_bonus
                    logger.debug(f"   ⚠️ MARGINAL FIGHT at {target_loc.name}: +{marginal_bonus} "
                               f"({our_power} vs {target_loc.their_power}, only +{power_advantage})")
                else:
                    # LOSING FIGHT: We don't beat them
                    # But contesting still denies force drain!
                    score += 5 + deny_drain_bonus
                    logger.debug(f"   ❌ LOSING at {target_loc.name}: +{5 + deny_drain_bonus} (contest only)")

                # =================================================================
                # BARRIER CARD AWARENESS (34% of decks have Barrier)
                # Opponent can use Barrier to prevent our deployed card from
                # battling or moving. Prefer deploying MULTIPLE cards so even
                # if one gets Barriered, others can still participate.
                # =================================================================
                cards_here = cards_by_location.get(loc_id, 1)
                if cards_here == 1:
                    # Single card deployment to contested - vulnerable to Barrier
                    barrier_risk = -15.0
                    score += barrier_risk
                    logger.debug(f"   🚧 BARRIER RISK at {target_loc.name}: {barrier_risk} "
                               f"(single card vulnerable)")
                elif cards_here >= 2:
                    # Multiple cards - even if one Barriered, others can battle
                    barrier_resilience = 10.0 * (cards_here - 1)  # +10 for each extra card
                    score += barrier_resilience
                    logger.debug(f"   🛡️ BARRIER RESILIENCE at {target_loc.name}: +{barrier_resilience} "
                               f"({cards_here} cards - Barrier can't stop all)")

                    # =================================================================
                    # FULL BATTLE COMMITMENT BONUS
                    # When we're deploying 2+ cards to CRUSH an enemy, reward full commitment
                    # This makes concentrated attacks more attractive than spreading thin
                    # Scale with power advantage - stronger crushes get more bonus
                    # =================================================================
                    if power_advantage >= get_battle_favorable_threshold():
                        # Scale: +10 per extra card for each point of advantage beyond threshold
                        # +4 advantage: 10 per card, +5: 20 per card, +6: 30 per card, etc.
                        advantage_factor = power_advantage - get_battle_favorable_threshold() + 1
                        commitment_bonus = advantage_factor * 10.0 * (cards_here - 1)
                        score += commitment_bonus
                        logger.debug(f"   ⚔️ BATTLE COMMITMENT at {target_loc.name}: +{commitment_bonus} "
                                   f"({cards_here} cards, +{power_advantage} advantage)")

                # =================================================================
                # ABILITY AWARENESS FOR CONTESTED LOCATIONS
                # If we can't draw battle destiny (ability < 4), we're vulnerable
                # to opponent destiny draws swinging the battle against us.
                # Apply penalty for low-ability deployments to contested locations.
                # =================================================================
                our_ability = ability_by_location.get(loc_id, 0)
                if our_ability >= ABILITY_THRESHOLD:
                    # Can draw destiny - bonus for battle advantage
                    ability_bonus = 25.0
                    score += ability_bonus
                    logger.debug(f"   🎯 CAN DRAW DESTINY at {target_loc.name}: +{ability_bonus} "
                               f"(ability {our_ability} >= {ABILITY_THRESHOLD})")
                else:
                    # Can't draw destiny - penalty proportional to our power investment
                    # Higher penalty for bigger deployments since we're committing
                    # resources that can't defend themselves via destiny
                    ability_penalty = -20.0 - (our_power * 2)
                    score += ability_penalty
                    logger.debug(f"   ⚠️ NO DESTINY at {target_loc.name}: {ability_penalty} "
                               f"(ability {our_ability} < {ABILITY_THRESHOLD}, vulnerable!)")

            else:
                # === EMPTY LOCATION WITH OUR PRESENCE ===
                # Two types of value here:
                # 1. OFFENSIVE: We can drain them for their_icons (attack potential)
                # 2. DEFENSIVE: We protect our own icons from being drained (defense)
                #
                # Defensive is often MORE important early game because:
                # - If opponent establishes at our 2-icon location, they drain us 2/turn
                # - Protecting it first denies them that option entirely
                # - This is especially true for our starting/objective locations

                establish_bonus = 40  # Base value for presence

                # OFFENSIVE VALUE: We can drain them
                if target_loc.their_icons > 0:
                    offensive_bonus = target_loc.their_icons * 15
                    establish_bonus += offensive_bonus
                    logger.debug(f"   ⚔️ OFFENSIVE VALUE at {target_loc.name}: +{offensive_bonus} "
                               f"(drain {target_loc.their_icons} icons)")

                # DEFENSIVE VALUE: Protect our icons from opponent drain
                # Important but must not exceed contested location bonuses (deny_drain = my_icons * 20)
                # Otherwise we'd prefer empty over contested, which is wrong
                # Use 15 per icon (same as offensive) - the real benefit is in preventing opponent from taking it
                if target_loc.my_icons > 0:
                    defensive_bonus = target_loc.my_icons * 15
                    establish_bonus += defensive_bonus
                    logger.debug(f"   🛡️ DEFENSIVE VALUE at {target_loc.name}: +{defensive_bonus} "
                               f"(protect {target_loc.my_icons} of our icons)")

                score += establish_bonus
                logger.debug(f"   ✅ ESTABLISH CONTROL at {target_loc.name}: +{establish_bonus} "
                           f"({our_power} power, {target_loc.their_icons} their icons, {target_loc.my_icons} our icons)")

                # =================================================================
                # ABILITY AWARENESS FOR ESTABLISH LOCATIONS
                # Weak establish (3-4 power, no ability) is a STRATEGIC TRAP:
                # - Bot gets 1-2 drains before opponent counter-deploys
                # - Opponent deploys single strong character with ability
                # - Opponent wins battle (can draw destiny, bot can't)
                # - Bot loses more life from battle than gained from drains
                #
                # Safe establish requires EITHER:
                # - High power (5+): hard for opponent to counter in one deploy
                # - Moderate power (4): borderline safe, opponent needs strong counter
                # - Ability >= 4: can draw destiny if challenged
                #
                # Truly weak (1-3 power, no ability) = TRAP - opponent easily counters
                # =================================================================
                our_ability = ability_by_location.get(loc_id, 0)
                has_ability = our_ability >= ABILITY_THRESHOLD
                has_strong_power = our_power >= 5  # Hard to counter-deploy against
                has_moderate_power = our_power >= 4  # Borderline - opponent needs 5+ to beat us

                if has_ability and has_strong_power:
                    # Ideal: both power and ability
                    ability_bonus = 25.0
                    score += ability_bonus
                    logger.info(f"   🎯 STRONG DEFENSIBLE ESTABLISH at {target_loc.name}: +{ability_bonus} "
                               f"({our_power} power + ability {our_ability} = very safe)")
                elif has_ability:
                    # Good: can draw destiny if challenged
                    ability_bonus = 15.0
                    score += ability_bonus
                    logger.info(f"   🎯 DEFENSIBLE ESTABLISH at {target_loc.name}: +{ability_bonus} "
                               f"(ability {our_ability} >= {ABILITY_THRESHOLD}, can draw destiny)")
                elif has_strong_power:
                    # Moderate risk: 5+ power without ability >= 4
                    # Harder to counter than 4 power, but still risky:
                    # - Opponent needs 6+ power to beat us outright
                    # - But if they do counter with ability 4, they draw destiny and we don't
                    # HARD BLOCK: No solo establishes without ability >= 4
                    # This is a trap - looks strong but loses to any ability 4 counter
                    ability_penalty = -500.0
                    score += ability_penalty
                    logger.warning(f"   🚫 BLOCKED ESTABLISH at {target_loc.name}: {ability_penalty} "
                               f"({our_power} power but ability {our_ability} < 4 - WILL LOSE to destiny!)")
                elif has_moderate_power:
                    # HARD BLOCK: 4 power without ability >= 4 = guaranteed loss
                    # Even with 4 power, opponent can easily counter:
                    # - They deploy 5-7 power with ability 4
                    # - They win battle and DRAW attrition destiny (we can't!)
                    # - We lose character + extra damage from their destiny draw
                    #
                    # This is NOT "borderline safe" - it's a TRAP
                    ability_penalty = -500.0
                    score += ability_penalty
                    logger.warning(f"   🚫 BLOCKED ESTABLISH at {target_loc.name}: {ability_penalty} "
                               f"({our_power} power, ability {our_ability} < 4 - GUARANTEED LOSS!)")
                else:
                    # HARD BLOCK: weak power (1-3) + no ability = easy crush target
                    # Opponent can deploy almost anything and destroy us
                    ability_penalty = -500.0
                    score += ability_penalty
                    logger.warning(f"   🚫 BLOCKED ESTABLISH at {target_loc.name}: {ability_penalty} "
                               f"({our_power} power, ability {our_ability} - EASY CRUSH TARGET!)")

                # =================================================================
                # MID-LATE GAME REINFORCEMENT BONUS
                # After early game (turn > 3), prioritize reinforcing existing weak
                # positions to get_reinforce_target_power() (10) before establishing elsewhere.
                # This prevents our early positions from being easy crush targets.
                # =================================================================
                if turn_number > get_early_game_turns():
                    # Check if this is a REINFORCE (we have existing presence) vs ESTABLISH (new location)
                    existing_power = target_loc.my_power
                    if existing_power > 0 and existing_power < get_reinforce_target_power():
                        # This is reinforcing a weak position
                        # Bonus scales with:
                        # 1. How far below target we are (more urgent = higher bonus)
                        # 2. Value of the location (icons)
                        power_deficit = get_reinforce_target_power() - existing_power
                        new_total_power = existing_power + our_power

                        # Base bonus: 30 (higher than establish base of 40, but establish gets icon bonuses)
                        # Scale: +10 per point of deficit (max 10 deficit = +100)
                        # This ensures reinforce beats establish but not crush
                        reinforce_bonus = 30 + (power_deficit * 10)

                        # Extra bonus if this reinforcement reaches target
                        if new_total_power >= get_reinforce_target_power():
                            reinforce_bonus += 20  # Reached safety target!

                        # Scale with location value (icons we're protecting)
                        location_value = target_loc.my_icons + target_loc.their_icons
                        reinforce_bonus += location_value * 5

                        score += reinforce_bonus
                        logger.debug(f"   🏰 MID-LATE REINFORCE at {target_loc.name}: +{reinforce_bonus} "
                                   f"(turn {turn_number}, {existing_power} -> {new_total_power} power, "
                                   f"target {get_reinforce_target_power()})")

        # Icons at target locations (additional value for multi-location plans)
        for loc_id in target_loc_ids:
            for loc in locations:
                if loc.card_id == loc_id:
                    # Both offensive (their icons) and defensive (our icons) matter
                    score += loc.their_icons * 10  # Reduced from 20, since establish_bonus covers this
                    score += loc.my_icons * 10     # Same as offensive - establish_bonus handles primary value
                    break

        # === COST EFFICIENCY BONUS ===
        # For UNCONTESTED locations, prefer cheaper ways to reach threshold.
        # This encourages deploying 3 troopers (3 cost) over Vader (6 cost)
        # when both reach the same threshold at an empty location.
        total_cost = sum(inst.deploy_cost for inst in instructions)
        total_power = sum(inst.power_contribution for inst in instructions)

        # Only apply efficiency bonus when NOT targeting contested locations
        # (for contested, raw power matters more)
        has_contested_target = any(
            any(loc.card_id == inst.target_location_id and loc.their_power > 0
                for loc in locations)
            for inst in instructions
            if inst.target_location_id
        )

        if not has_contested_target and total_cost > 0:
            # Efficiency = power gained per force spent
            # Bonus for more efficient deployments
            # Key insight: for UNCONTESTED locations, cost matters MORE than excess power
            # because force saved can be used elsewhere.
            efficiency_ratio = total_power / total_cost

            # Two-part bonus:
            # 1. Direct cost savings: subtract cost to penalize expensive deployments
            #    This directly rewards cheaper options
            # 2. Efficiency ratio bonus: rewards high power/cost ratio
            cost_penalty = -total_cost * 3  # Penalize high cost
            efficiency_bonus = min(30, efficiency_ratio * 15)
            total_efficiency_adjustment = cost_penalty + efficiency_bonus
            score += total_efficiency_adjustment
            logger.debug(f"   💰 EFFICIENCY: {total_efficiency_adjustment:+.1f} "
                       f"(cost penalty: {cost_penalty}, ratio bonus: +{efficiency_bonus:.1f})")

        # === STRATEGIC DOMAIN BONUS ===
        # Apply bonuses based on deck archetype's domain preference
        # (e.g., space_control deck gets bonus for space locations)
        from engine.strategy_profile import get_deck_strategy
        deck_strategy = get_deck_strategy()
        if deck_strategy:
            strategic_bonus = 0
            for loc_id in target_loc_ids:
                for loc in locations:
                    if loc.card_id == loc_id:
                        # Use the location's is_space flag
                        if loc.is_space:
                            strategic_bonus += deck_strategy.space_location_bonus
                        else:
                            strategic_bonus += deck_strategy.ground_location_bonus
                        break

            if strategic_bonus > 0:
                score += strategic_bonus
                logger.debug(f"   🎯 STRATEGIC: +{strategic_bonus} ({deck_strategy.archetype.value} "
                           f"domain={deck_strategy.primary_domain})")

        # === GOAL-BASED SCORING ===
        # Apply bonuses based on GamePlan goals (if enabled)
        if self._board_state and hasattr(self._board_state, 'game_plan') and self._board_state.game_plan:
            game_plan = self._board_state.game_plan
            if game_plan.enabled and game_plan.current_goals:
                goal_bonus = 0
                goal_reasons = []
                for inst in instructions:
                    if inst.target_location_id:
                        bonus, reason = game_plan.get_deployment_score_bonus(
                            target_location_id=inst.target_location_id,
                            card_blueprint_id=inst.card_blueprint_id,
                        )
                        if bonus != 0:
                            goal_bonus += bonus
                            if reason:
                                goal_reasons.append(reason)

                if goal_bonus != 0:
                    score += goal_bonus
                    reason_str = "; ".join(goal_reasons[:2])  # Limit logged reasons
                    logger.debug(f"   🎯 GOALS: {goal_bonus:+d} ({reason_str})")

                # NOTE: "Hold for better hand" penalty was tested and found to HURT performance.
                # A -200 blanket penalty blocked ALL deploys when bleeding, even good ones.
                # Better approach: let the tactical evaluators decide on a case-by-case basis.

        # === STRATEGIC ATTACK MODE SCORING ===
        # When strategic state indicates we're losing the drain war and have
        # contestable bleeds, provide MAJOR bonuses for contesting and
        # penalties for establishing at low-value locations.
        # This makes the bot prioritize ATTACKING opponent drains over
        # establishing new presence when we're behind.
        strategic_state = getattr(self._board_state, 'strategic_state', None) if self._board_state else None
        if strategic_state and strategic_state.enabled and strategic_state.force_attack_mode:
            attack_mode_adjustment = 0

            for loc_id, our_power in power_by_location.items():
                target_loc = None
                for loc in locations:
                    if loc.card_id == loc_id:
                        target_loc = loc
                        break

                if not target_loc:
                    continue

                # CONTESTING A BLEED - massive priority boost
                # This is a location where opponent has presence and we have icons (they drain us)
                if target_loc.their_power > 0 and target_loc.my_icons > 0:
                    icons_saved = target_loc.my_icons
                    attack_bonus = 150 + (icons_saved * 40)  # 150 base + 40 per icon
                    attack_mode_adjustment += attack_bonus
                    logger.info(f"   🎯 ATTACK MODE: +{attack_bonus} for contesting {target_loc.name} "
                               f"(stop {icons_saved} drain/turn)")

                # PENALIZE establishing at 0-icon locations while bleeding
                # If we're behind on drains, don't waste resources on locations with no drain value
                elif target_loc.their_power == 0 and target_loc.my_icons == 0 and target_loc.their_icons == 0:
                    drain_gap = strategic_state.trajectory.current_drain_gap
                    if drain_gap < -2:
                        establish_penalty = -100
                        attack_mode_adjustment += establish_penalty
                        logger.info(f"   ⚠️ ATTACK MODE: {establish_penalty} for establishing at {target_loc.name} "
                                   f"(no drain value, drain gap {drain_gap:+d})")

                # PENALIZE reinforcing UNCONTESTED locations while losing drain war
                # If opponent has NO presence at this location but we're behind on drains,
                # we should be establishing at bleed locations instead of stacking more power here
                elif target_loc.their_power == 0 and target_loc.my_power > 0:
                    drain_gap = strategic_state.trajectory.current_drain_gap
                    if drain_gap < -1:
                        # Already have power here, don't reinforce while losing drain war
                        # Penalty scales with how much we're already ahead at this location
                        existing_power = target_loc.my_power
                        if existing_power >= 6:  # Already well-established
                            reinforce_penalty = -80 - (existing_power * 5)  # Heavier penalty for overkill
                            attack_mode_adjustment += reinforce_penalty
                            logger.info(f"   ⚠️ ATTACK MODE: {reinforce_penalty} for reinforcing uncontested {target_loc.name} "
                                       f"(already {existing_power}p, drain gap {drain_gap:+d} - go contest bleed locations!)")
                        elif existing_power >= 3:  # Moderate presence
                            reinforce_penalty = -40
                            attack_mode_adjustment += reinforce_penalty
                            logger.info(f"   ⚠️ ATTACK MODE: {reinforce_penalty} for reinforcing uncontested {target_loc.name} "
                                       f"({existing_power}p, drain gap {drain_gap:+d})")

            if attack_mode_adjustment != 0:
                score += attack_mode_adjustment
                logger.info(f"   ⚔️ ATTACK MODE TOTAL: {attack_mode_adjustment:+d}")

        return score

    def _apply_monte_carlo_simulation(
        self,
        valid_plans: List[Tuple],
        locations: List['LocationAnalysis'],
        hand_cards: List[Dict],
        board_state
    ) -> List[Tuple]:
        """
        Apply Monte Carlo simulation to top N plans and re-rank by expected value.

        This stress-tests each plan against randomized opponent counterplays:
        - Opponent deploys power to counter (weighted by location importance)
        - Opponent may play Barrier (~8% chance)
        - Opponent may initiate battle if favorable

        Args:
            valid_plans: List of (plan_type, instructions, force_left, score, reserve)
            locations: Analyzed locations
            hand_cards: Cards in hand as dicts
            board_state: Current board state

        Returns:
            Re-sorted list of plans with expected values
        """
        if not self.monte_carlo:
            return valid_plans

        top_n = min(self.monte_carlo_top_n, len(valid_plans))
        top_plans = valid_plans[:top_n]
        rest_plans = valid_plans[top_n:]

        logger.info(f"🎲 Monte Carlo: simulating top {top_n} plans")

        mc_scored_plans = []
        for plan_type, instructions, force_left, base_score, reserve in top_plans:
            # Create a temporary DeploymentPlan for simulation
            temp_plan = DeploymentPlan(
                strategy=DeployStrategy.REINFORCE,  # Doesn't matter for simulation
                reason="MC simulation",
                instructions=list(instructions),
            )

            # Run simulation
            sim_result = self.monte_carlo.simulate_plan(
                temp_plan, locations, hand_cards, board_state
            )
            expected = self.monte_carlo.calculate_expected_value(base_score, sim_result)

            # Log simulation results
            self._log_monte_carlo_result(plan_type, instructions, base_score, expected, sim_result)

            # Use expected value as new score
            mc_scored_plans.append((
                plan_type, instructions, force_left, expected.final_score, reserve, expected
            ))

        # Sort by expected value (final_score)
        mc_scored_plans.sort(key=lambda x: x[3], reverse=True)

        # If ranking changed, log it
        if mc_scored_plans[0][0] != top_plans[0][0]:
            old_best = top_plans[0][0]
            new_best = mc_scored_plans[0][0]
            logger.info(f"🎲 Monte Carlo RERANKED: {old_best} → {new_best}")

        # Convert back to original format (drop the ExpectedValue object)
        result = [(t, i, f, s, r) for t, i, f, s, r, _ in mc_scored_plans]
        result.extend(rest_plans)

        return result

    def _log_monte_carlo_result(
        self,
        plan_type: str,
        instructions: List['DeploymentInstruction'],
        base_score: float,
        expected: 'ExpectedValue',
        sim_result: 'SimulationResult'
    ):
        """Log Monte Carlo simulation results for a plan."""
        if not instructions:
            return

        target = instructions[0].target_location_name if instructions else "?"
        card_summary = ", ".join(inst.card_name for inst in instructions[:2])
        if len(instructions) > 2:
            card_summary += f", +{len(instructions)-2}"

        logger.info(f"🎲 MC: {plan_type} at {target}")
        logger.info(f"   Cards: {card_summary}")
        logger.info(f"   Win rate: {sim_result.win_rate*100:.0f}% ({int(sim_result.win_rate*20)}/20)")
        logger.info(f"   Margin: {sim_result.worst_case:+d} to {sim_result.best_case:+d} "
                   f"(avg {sim_result.avg_power_margin:+.1f}, p10={sim_result.percentile_10_margin:+d})")

        if sim_result.opponent_battled_count > 0:
            logger.info(f"   Opponent battled T1: {sim_result.opponent_battled_count}/20")
        if sim_result.barrier_losses > 0:
            logger.info(f"   Barrier losses: {sim_result.barrier_losses}/20")

        # Format histogram
        histogram_str = MonteCarloSimulator.format_histogram(sim_result.histogram)
        logger.info(f"   Histogram: {histogram_str}")

        # Show score adjustment
        ratio = expected.final_score / base_score if base_score > 0 else 0
        logger.info(f"   Score: {base_score:.0f} → {expected.final_score:.0f} ({ratio:.2f}x)")

    def _generate_ground_plan_for_card(self, card: Dict, other_chars: List[Dict],
                                        ground_targets: List[LocationAnalysis],
                                        force_budget: int) -> Tuple[List[DeploymentInstruction], int]:
        """
        Generate a ground plan starting with a specific card.

        This allows comparing plans that prioritize different characters.
        """
        instructions = []
        force_remaining = force_budget

        if card['cost'] > force_remaining:
            return instructions, force_remaining

        MIN_ESTABLISH_POWER = self.deploy_threshold

        # Find best location for this specific card (that the card can deploy to)
        best_loc = None
        for loc in ground_targets:
            # Check deploy restrictions for this card
            eligible = self._filter_cards_for_location([card], loc.name)
            if not eligible:
                continue  # Card can't deploy here due to restrictions
            min_needed = loc.their_power + 1 if loc.their_power > 0 else MIN_ESTABLISH_POWER
            if card['power'] >= min_needed:
                best_loc = loc
                break  # Take first viable location (already sorted by priority)

        if not best_loc:
            # Card can't beat enemy or establish at any location - skip
            # IMPORTANT: Don't deploy into deficit or matching scenarios!
            # We should only deploy if we can BEAT the enemy power.
            logger.debug(f"   ⏭️ {card['name']} ({card['power']} power) can't beat enemy or establish (threshold {MIN_ESTABLISH_POWER})")
            return instructions, force_remaining

        # Build reason based on whether location is empty or contested
        if best_loc.their_power > 0:
            reason = f"Ground: Beat {best_loc.name} ({card['power']} vs {best_loc.their_power} power)"
        else:
            reason = f"Ground: Establish control at {best_loc.name} ({card['power']} power)"

        # Deploy the primary card
        instructions.append(DeploymentInstruction(
            card_blueprint_id=card['blueprint_id'],
            card_name=card['name'],
            target_location_id=best_loc.card_id,
            target_location_name=best_loc.name,
            priority=2,
            reason=reason,
            power_contribution=card['power'],
            deploy_cost=card['cost'],
        ))
        force_remaining -= card['cost']

        # Try to add more cards with remaining budget
        available_chars = [c for c in other_chars if c != card]
        remaining_targets = [t for t in ground_targets if t != best_loc]

        for loc in remaining_targets:
            if not available_chars or force_remaining <= 0:
                break

            # Filter characters that can deploy to this location
            eligible_chars = self._filter_cards_for_location(available_chars, loc.name)
            if not eligible_chars:
                continue

            power_goal = max(MIN_ESTABLISH_POWER, loc.their_power + 1)
            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                eligible_chars, force_remaining, power_goal, must_exceed=False
            )

            min_needed = loc.their_power + 1 if loc.their_power > 0 else MIN_ESTABLISH_POWER
            if not cards_for_location or power_allocated < min_needed:
                continue

            force_remaining -= cost_used

            # Build reason based on whether location is empty or contested
            if loc.their_power > 0:
                loc_reason = f"Ground: Contest {loc.name} (vs {loc.their_power} power)"
            else:
                loc_reason = f"Ground: Establish control at {loc.name} ({power_allocated} power)"

            for c in cards_for_location:
                if c in available_chars:
                    available_chars.remove(c)
                instructions.append(DeploymentInstruction(
                    card_blueprint_id=c['blueprint_id'],
                    card_name=c['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=2,
                    reason=loc_reason,
                    power_contribution=c['power'],
                    deploy_cost=c['cost'],
                ))

        return instructions, force_remaining

    def _generate_combined_ground_plan(self, characters: List[Dict],
                                        ground_targets: List[LocationAnalysis],
                                        force_budget: int) -> Tuple[List[DeploymentInstruction], int]:
        """
        Generate a ground plan using optimal combinations at each location.

        This method considers deploying MULTIPLE characters to the SAME location
        to meet the power threshold, which individual-card plans don't do.
        """
        instructions = []
        force_remaining = force_budget
        available_chars = characters.copy()

        MIN_ESTABLISH_POWER = self.deploy_threshold

        for loc in ground_targets:
            if not available_chars or force_remaining <= 0:
                break

            # Filter characters that can deploy to this location
            eligible_chars = self._filter_cards_for_location(available_chars, loc.name)
            if not eligible_chars:
                continue

            # Calculate power needed for this location
            power_goal = max(MIN_ESTABLISH_POWER, loc.their_power + 1)

            # Find optimal combination of characters for this location
            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                eligible_chars, force_remaining, power_goal, must_exceed=False
            )

            # Check if the combination meets our requirements
            min_needed = loc.their_power + 1 if loc.their_power > 0 else MIN_ESTABLISH_POWER
            if not cards_for_location or power_allocated < min_needed:
                continue

            # Build reason based on whether location is empty or contested
            if loc.their_power > 0:
                reason = f"Ground: Contest {loc.name} (combined {power_allocated} vs {loc.their_power})"
            else:
                reason = f"Ground: Establish at {loc.name} (combined {power_allocated} power)"

            # Add instructions for all cards in the combination
            force_remaining -= cost_used
            for card in cards_for_location:
                if card in available_chars:
                    available_chars.remove(card)

                instructions.append(DeploymentInstruction(
                    card_blueprint_id=card['blueprint_id'],
                    card_name=card['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=2,
                    reason=reason,
                    power_contribution=card['power'],
                    deploy_cost=card['cost'],
                ))

        return instructions, force_remaining

    def _generate_all_ground_plans(self, characters: List[Dict], vehicles: List[Dict],
                                    ground_targets: List[LocationAnalysis],
                                    force_budget: int,
                                    locations: List[LocationAnalysis],
                                    pilots: List[Dict] = None,
                                    ground_threshold: int = None,
                                    contest_advantage: int = None) -> List[Tuple[List[DeploymentInstruction], int, float]]:
        """
        Generate multiple ground plans - one for EACH target location.

        CRITICAL: Generate a plan for each location independently and score them.
        This ensures we consider crushing contested locations vs establishing at empty.

        If pilots are provided, will also generate vehicle+pilot combo plans
        for unpiloted ground vehicles (like Blizzard 1). Any pilot (warrior or pure)
        can drive a vehicle.

        Args:
            ground_threshold: Dynamic threshold for establishing ground presence.
                              Uses self.deploy_threshold if not specified.
            contest_advantage: Power advantage required over enemy to contest.
                              Defaults to get_min_contest_advantage() (2) if not specified.
                              When life is low, this may be reduced to accept ties.

        Returns list of (instructions, force_remaining, score) tuples.
        """
        plans = []
        available_pilots = (pilots or []).copy()

        # Filter to affordable characters
        affordable_chars = [c for c in characters if c['cost'] <= force_budget]

        # Filter to affordable vehicles WITH permanent pilots (can deploy alone)
        affordable_piloted_vehicles = [
            v for v in vehicles
            if v['cost'] <= force_budget and v.get('has_permanent_pilot', False)
        ]

        # Filter to unpiloted vehicles that we could potentially deploy with a pilot
        affordable_unpiloted_vehicles = [
            v for v in vehicles
            if v['cost'] <= force_budget and v.get('needs_pilot', False)
        ]

        # Combine characters and piloted vehicles into one pool of deployable cards
        # Both can contribute power to reach threshold
        all_deployable = affordable_chars + affordable_piloted_vehicles

        if not all_deployable and not (affordable_unpiloted_vehicles and available_pilots):
            return plans

        # Use dynamic threshold if provided, otherwise fall back to default
        MIN_ESTABLISH_POWER = ground_threshold if ground_threshold is not None else self.deploy_threshold

        # Use dynamic contest advantage if provided, otherwise fall back to default
        required_advantage = contest_advantage if contest_advantage is not None else get_min_contest_advantage()

        # === GENERATE PLANS FOR EACH CARD × LOCATION COMBINATION ===
        # For multi-location establishment to work, we need separate plans
        # for each card at each location (not just the optimal combination).
        # This allows the multi-location combiner to find plans that use
        # different cards at different locations.
        for target_loc in ground_targets:
            # Skip if we can't deploy characters there (interior check done in target filtering)
            if not target_loc.is_ground:
                continue

            # Adjust budget for contested locations (need +1 reserve for battle initiation)
            # This ensures plans don't exceed total_force when reserve is added
            if target_loc.their_power > 0:
                loc_budget = force_budget - 1  # Reserve 1 extra for battle
            else:
                loc_budget = force_budget  # Uncontested - use full budget

            if loc_budget <= 0:
                continue

            # Filter deployables for this location (vehicles need exterior, system restrictions)
            location_deployables = []
            for card in all_deployable:
                # Check deploy system restrictions first
                restrictions = card.get('deploy_restriction_systems', [])
                if restrictions:
                    # Card has restrictions - check if location matches any allowed system
                    loc_clean = target_loc.name.lstrip('•').strip()
                    can_deploy = False
                    for system in restrictions:
                        system_lower = system.lower()
                        loc_lower = loc_clean.lower()
                        if loc_lower.startswith(system_lower):
                            can_deploy = True
                            break
                        if ':' in loc_clean:
                            loc_system = loc_clean.split(':')[0].strip().lower()
                            if loc_system == system_lower:
                                can_deploy = True
                                break
                    if not can_deploy:
                        continue  # Card restricted to other systems

                if card.get('is_vehicle'):
                    # Vehicles can only deploy to exterior locations
                    if target_loc.is_exterior:
                        location_deployables.append(card)
                else:
                    # Characters can deploy anywhere (interior/exterior check done in target filtering)
                    # BUT: Pure pilots should NOT be wasted on OVER-THRESHOLD reinforcement
                    # They CAN help reach threshold (first 6 power), but shouldn't pile on above that
                    # Reinforcement = we're already at/above threshold AND no enemy presence
                    is_above_threshold = target_loc.my_power >= MIN_ESTABLISH_POWER and target_loc.their_power == 0
                    if is_above_threshold and card.get('is_pure_pilot'):
                        # Skip pure pilots for above-threshold reinforcement - save them for ships!
                        continue
                    location_deployables.append(card)

            if not location_deployables:
                continue

            # Calculate power needed for this specific location
            # CRITICAL: Include our EXISTING power at the location for crushable scenarios
            existing_power = target_loc.my_power if target_loc.my_power > 0 else 0

            # For contested locations: need BOTH deploy threshold AND required advantage
            # required_advantage is dynamic based on life force (lower when desperate)
            if target_loc.their_power > 0:
                # Total needed = max(threshold, enemy + advantage)
                total_power_goal = max(MIN_ESTABLISH_POWER, target_loc.their_power + required_advantage)
                # We only need to DEPLOY enough to reach goal (subtract existing)
                power_goal = max(1, total_power_goal - existing_power)
            else:
                power_goal = max(1, MIN_ESTABLISH_POWER - existing_power)

            # APPROACH 1: Generate a plan for EACH SINGLE CARD that meets threshold
            # This enables multi-location combinations using different cards
            for card in location_deployables:
                if card['cost'] > loc_budget:
                    continue

                card_power = existing_power + card['power']

                # Check if this single card meets threshold
                if target_loc.their_power > 0:
                    if card_power < target_loc.their_power + required_advantage:
                        continue
                    if card_power < MIN_ESTABLISH_POWER:
                        continue
                elif card_power < MIN_ESTABLISH_POWER:
                    continue

                # BUDDY SYSTEM: Don't deploy weak characters alone to establish new presence
                # This prevents enemy from moving in and crushing our lone weak character
                if get_weak_char_buddy_required():
                    weak_threshold = get_weak_character_power()
                    is_establishing = (existing_power == 0)
                    is_weak_char = (card['power'] <= weak_threshold)
                    if is_establishing and is_weak_char:
                        logger.debug(f"   ⏭️ BUDDY: {card['name']} (power {card['power']}) needs buddy to establish at {target_loc.name}")
                        continue  # Skip single-card plan, multi-card plans still considered below

                # Create a single-card plan
                if target_loc.their_power > 0:
                    power_advantage = card_power - target_loc.their_power
                    if power_advantage >= get_battle_favorable_threshold():
                        reason = f"Ground: Crush {target_loc.name} ({card_power} vs {target_loc.their_power})"
                    else:
                        reason = f"Ground: Reinforce {target_loc.name} ({card_power} vs {target_loc.their_power}, +{power_advantage})"
                else:
                    reason = f"Ground: Establish at {target_loc.name} ({card_power} power)"

                instructions = [DeploymentInstruction(
                    card_blueprint_id=card['blueprint_id'],
                    card_name=card['name'],
                    target_location_id=target_loc.card_id,
                    target_location_name=target_loc.name,
                    priority=2,
                    reason=reason,
                    power_contribution=card['power'],
                    deploy_cost=card['cost'],
                )]
                # Keep force_remaining relative to original budget for consistent validation
                force_remaining = force_budget - card['cost']
                score = self._score_plan(instructions, locations, self._current_turn)
                plans.append((instructions, force_remaining, score))

            # APPROACH 2: Also find optimal combination for locations needing multiple cards
            # This covers cases where no single card meets threshold but combinations do
            cards_for_loc, power_allocated, cost_used = self._find_optimal_combination(
                location_deployables.copy(), loc_budget, power_goal, must_exceed=(target_loc.their_power > 0 and existing_power == 0)
            )

            if not cards_for_loc or len(cards_for_loc) <= 1:
                continue  # Single-card plans already handled above

            # Calculate TOTAL power (existing + deployed)
            total_power = existing_power + power_allocated

            # Must meet BOTH thresholds for contested locations
            if target_loc.their_power > 0:
                if total_power < target_loc.their_power + required_advantage:
                    continue
                if total_power < MIN_ESTABLISH_POWER:
                    continue
            elif total_power < MIN_ESTABLISH_POWER:
                continue

            # Build multi-card combination instructions
            instructions = []
            # Keep force_remaining relative to original budget for consistent validation
            force_remaining = force_budget - cost_used

            for card in cards_for_loc:
                if target_loc.their_power > 0:
                    power_advantage = total_power - target_loc.their_power
                    if power_advantage >= get_battle_favorable_threshold():
                        reason = f"Ground: Crush {target_loc.name} ({total_power} vs {target_loc.their_power})"
                    else:
                        reason = f"Ground: Reinforce {target_loc.name} ({total_power} vs {target_loc.their_power}, +{power_advantage})"
                else:
                    reason = f"Ground: Establish at {target_loc.name} (combined {total_power} power)"

                instructions.append(DeploymentInstruction(
                    card_blueprint_id=card['blueprint_id'],
                    card_name=card['name'],
                    target_location_id=target_loc.card_id,
                    target_location_name=target_loc.name,
                    priority=2,
                    reason=reason,
                    power_contribution=card['power'],
                    deploy_cost=card['cost'],
                ))

            if instructions:
                score = self._score_plan(instructions, locations, self._current_turn)
                plans.append((instructions, force_remaining, score))

        # === GENERATE VEHICLE + PILOT COMBO PLANS ===
        # For unpiloted ground vehicles (like Blizzard 1), generate combo plans
        # with a pilot character that will board the vehicle
        # Any pilot (warrior or pure) can drive, but prefer pure pilots when available
        if affordable_unpiloted_vehicles and available_pilots:
            cheapest_pilot_cost = min(p['cost'] for p in available_pilots)

            for vehicle in affordable_unpiloted_vehicles:
                # Combined cost must fit in budget
                combined_cost = vehicle['cost'] + cheapest_pilot_cost
                if combined_cost > force_budget:
                    continue

                # Find all pilots that can afford to combo with this vehicle
                affordable_pilots_for_vehicle = [p for p in available_pilots if vehicle['cost'] + p['cost'] <= force_budget]
                if not affordable_pilots_for_vehicle:
                    continue

                # Pick best pilot: highest pilot_adds_power, with pure pilots preferred as tiebreaker
                best_pilot = max(affordable_pilots_for_vehicle,
                                key=lambda p: (p.get('pilot_adds_power', 1), p.get('is_pure_pilot', False)))
                actual_combined_cost = vehicle['cost'] + best_pilot['cost']
                # Vehicle power = base_power + pilot's "Adds X to power" (from gametext)
                pilot_contribution = best_pilot.get('pilot_adds_power', 1)
                actual_estimated_power = vehicle.get('base_power', 0) + pilot_contribution

                # Generate a plan for each exterior ground target
                for target_loc in ground_targets:
                    # Vehicles can only deploy to exterior locations
                    if not target_loc.is_exterior:
                        continue

                    # Check deploy system restrictions for vehicle
                    restrictions = vehicle.get('deploy_restriction_systems', [])
                    if restrictions:
                        loc_clean = target_loc.name.lstrip('•').strip()
                        can_deploy = False
                        for system in restrictions:
                            system_lower = system.lower()
                            loc_lower = loc_clean.lower()
                            if loc_lower.startswith(system_lower):
                                can_deploy = True
                                break
                            if ':' in loc_clean:
                                loc_system = loc_clean.split(':')[0].strip().lower()
                                if loc_system == system_lower:
                                    can_deploy = True
                                    break
                        if not can_deploy:
                            continue

                    # Adjust budget for contested locations
                    if target_loc.their_power > 0:
                        loc_budget = force_budget - 1
                    else:
                        loc_budget = force_budget

                    if actual_combined_cost > loc_budget:
                        continue

                    # Calculate total power at location
                    existing_power = target_loc.my_power if target_loc.my_power > 0 else 0
                    total_power = existing_power + actual_estimated_power

                    # Check if this meets thresholds
                    if target_loc.their_power > 0:
                        if total_power < target_loc.their_power + required_advantage:
                            continue
                        if total_power < MIN_ESTABLISH_POWER:
                            continue
                    elif total_power < MIN_ESTABLISH_POWER:
                        continue

                    # Build vehicle + pilot combo plan
                    if target_loc.their_power > 0:
                        power_advantage = total_power - target_loc.their_power
                        if power_advantage >= get_battle_favorable_threshold():
                            reason = f"Ground: Crush {target_loc.name} with vehicle ({total_power} vs {target_loc.their_power})"
                        else:
                            reason = f"Ground: Reinforce {target_loc.name} with vehicle ({total_power} vs {target_loc.their_power})"
                    else:
                        reason = f"Ground: Establish at {target_loc.name} with vehicle ({total_power} power)"

                    instructions = [
                        DeploymentInstruction(
                            card_blueprint_id=vehicle['blueprint_id'],
                            card_name=vehicle['name'],
                            target_location_id=target_loc.card_id,
                            target_location_name=target_loc.name,
                            priority=2,
                            reason=reason,
                            power_contribution=vehicle.get('base_power', 0),
                            deploy_cost=vehicle['cost'],
                        ),
                        DeploymentInstruction(
                            card_blueprint_id=best_pilot['blueprint_id'],
                            card_name=best_pilot['name'],
                            target_location_id=target_loc.card_id,
                            target_location_name=target_loc.name,
                            priority=3,  # After vehicle
                            reason=f"Pilot aboard {vehicle['name']}",
                            power_contribution=best_pilot.get('power', 2),
                            deploy_cost=best_pilot['cost'],
                            # Track which vehicle pilot is boarding
                            aboard_ship_name=vehicle['name'],
                            aboard_ship_blueprint_id=vehicle['blueprint_id'],
                            aboard_ship_card_id=None,  # Will be set when vehicle deploys
                        )
                    ]

                    force_remaining = force_budget - actual_combined_cost
                    score = self._score_plan(instructions, locations, self._current_turn)
                    plans.append((instructions, force_remaining, score))
                    logger.debug(f"   🚗 Vehicle+pilot plan: {vehicle['name']} + {best_pilot['name']} at {target_loc.name} (power={actual_estimated_power}, cost={actual_combined_cost}, score={score:.0f})")

        return plans

    def _generate_ground_plan(self, characters: List[Dict], vehicles: List[Dict],
                               ground_targets: List[LocationAnalysis],
                               force_budget: int) -> Tuple[List[DeploymentInstruction], int]:
        """Generate a ground-focused deployment plan using full budget (legacy method)."""
        instructions = []
        force_remaining = force_budget
        available_chars = characters.copy()

        MIN_ESTABLISH_POWER = self.deploy_threshold

        for loc in ground_targets:
            if not available_chars or force_remaining <= 0:
                break

            # Filter characters that can deploy to this location (respects deploy restrictions)
            eligible_chars = self._filter_cards_for_location(available_chars, loc.name)
            if not eligible_chars:
                logger.debug(f"   ⏭️ No characters can deploy to {loc.name} (all restricted)")
                continue

            power_goal = max(MIN_ESTABLISH_POWER, loc.their_power + 1)

            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                eligible_chars, force_remaining, power_goal, must_exceed=False
            )

            min_needed = loc.their_power + 1 if loc.their_power > 0 else MIN_ESTABLISH_POWER
            if not cards_for_location or power_allocated < min_needed:
                continue

            force_remaining -= cost_used
            for card in cards_for_location:
                if card in available_chars:
                    available_chars.remove(card)

                instructions.append(DeploymentInstruction(
                    card_blueprint_id=card['blueprint_id'],
                    card_name=card['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=2,
                    reason=f"Ground: Attack {loc.name} (beat {loc.their_power} power)",
                    power_contribution=card['power'],
                    deploy_cost=card['cost'],
                ))

        return instructions, force_remaining

    def _generate_space_plan(self, starships: List[Dict],
                              space_targets: List[LocationAnalysis],
                              force_budget: int,
                              pilots: List[Dict] = None) -> Tuple[List[DeploymentInstruction], int]:
        """
        Generate a space-focused deployment plan using full budget.

        If pilots are provided, will try to include them boarding starships.
        Any pilot (warrior or pure) can fly ships, but pure pilots are preferred.

        IMPORTANT: Ships needing pilots have 0 power until piloted. When we have
        available pilots, we estimate piloted ship power as base_power + pilot_adds_power.
        """
        instructions = []
        force_remaining = force_budget
        available_ships = starships.copy()
        available_pilots = (pilots or []).copy()

        MIN_ESTABLISH_POWER = self.deploy_threshold

        # CRITICAL FIX: When we have pilots, estimate ship power WITH pilot
        # Otherwise unpiloted ships (0 power) will never meet power thresholds
        # We modify 'cost' to include pilot cost so _find_optimal_combination works correctly
        ships_with_estimated_power = []
        for ship in available_ships:
            ship_copy = ship.copy()
            if ship.get('needs_pilot') and available_pilots:
                # Estimate piloted power: base_power + best pilot's "Adds X to power" (from gametext)
                # Include cheapest pilot cost in ship cost for budget calculation
                cheapest_pilot_cost = min(p['cost'] for p in available_pilots)
                best_pilot_power = max(p.get('pilot_adds_power', 1) for p in available_pilots)
                ship_copy['power'] = ship.get('base_power', 0) + best_pilot_power
                ship_copy['original_cost'] = ship['cost']  # Save original for actual deployment
                ship_copy['cost'] = ship['cost'] + cheapest_pilot_cost  # Include pilot cost
                logger.debug(f"   🚀 {ship['name']}: estimated piloted power={ship_copy['power']}, "
                            f"combined cost={ship_copy['cost']} (ship {ship_copy['original_cost']} + pilot {cheapest_pilot_cost})")
            ships_with_estimated_power.append(ship_copy)

        for loc in space_targets:
            if not ships_with_estimated_power or force_remaining <= 0:
                break

            power_goal = max(MIN_ESTABLISH_POWER, loc.their_power + 1)

            # Use estimated power for finding optimal combination
            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                ships_with_estimated_power, force_remaining, power_goal, must_exceed=False
            )

            if not cards_for_location or power_allocated < MIN_ESTABLISH_POWER:
                logger.debug(f"   ⚠️ Space plan: couldn't meet {MIN_ESTABLISH_POWER} power at {loc.name} "
                            f"(got {power_allocated} power)")
                continue

            force_remaining -= cost_used
            for ship in cards_for_location:
                # Find and remove from both lists (estimated and original)
                ship_blueprint = ship['blueprint_id']
                ships_with_estimated_power = [s for s in ships_with_estimated_power
                                               if s['blueprint_id'] != ship_blueprint]
                available_ships = [s for s in available_ships
                                   if s['blueprint_id'] != ship_blueprint]

                if loc.their_power > 0:
                    reason = f"Space: Contest {loc.name} (vs {loc.their_power} power)"
                else:
                    reason = f"Space: Control {loc.name} ({loc.their_icons} icons)"

                # Use actual ship cost (not estimated combined cost), power will come from pilot
                actual_cost = ship.get('original_cost', ship.get('cost', 0))
                actual_power = ship.get('base_power', 0) if ship.get('needs_pilot') else ship.get('power', 0)

                instructions.append(DeploymentInstruction(
                    card_blueprint_id=ship['blueprint_id'],
                    card_name=ship['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=2,
                    reason=reason,
                    power_contribution=actual_power,
                    deploy_cost=actual_cost,
                ))

                # After deploying a starship that needs a pilot, deploy a pilot aboard it
                # This is REQUIRED for unpiloted ships, not optional
                # Any pilot can fly, but prefer pure pilots (they're specialized)
                if ship.get('needs_pilot') and available_pilots:
                    # Find affordable pilots
                    affordable_pilots_here = [p for p in available_pilots if p['cost'] <= force_remaining]
                    if affordable_pilots_here:
                        # Pick best pilot: score for ship match, with pure pilots preferred
                        best_pilot = max(affordable_pilots_here,
                                        key=lambda p: (_pilot_score_for_ship(p, ship), p.get('is_pure_pilot', False)))
                        force_remaining -= best_pilot['cost']
                        available_pilots.remove(best_pilot)

                        instructions.append(DeploymentInstruction(
                            card_blueprint_id=best_pilot['blueprint_id'],
                            card_name=best_pilot['name'],
                            target_location_id=loc.card_id,  # System where ship is going
                            target_location_name=loc.name,
                            priority=3,  # After the ship
                            reason=f"Pilot aboard {ship['name']}",
                            power_contribution=best_pilot['power'],
                            deploy_cost=best_pilot['cost'],
                            # Track which ship pilot is boarding (card_id assigned after ship deploys)
                            aboard_ship_name=ship['name'],
                            aboard_ship_blueprint_id=ship['blueprint_id'],
                            aboard_ship_card_id=None,  # Will be set when ship deploys
                        ))
                        logger.info(f"   👨‍✈️ Plan: Deploy pilot {best_pilot['name']} aboard {ship['name']}")
                elif ship.get('has_permanent_pilot') and available_pilots:
                    # Ship has permanent pilot but we can still add another pilot for extra power
                    affordable_pilots_here = [p for p in available_pilots if p['cost'] <= force_remaining]
                    if affordable_pilots_here:
                        best_pilot = max(affordable_pilots_here,
                                        key=lambda p: (_pilot_score_for_ship(p, ship), p.get('is_pure_pilot', False)))
                        force_remaining -= best_pilot['cost']
                        available_pilots.remove(best_pilot)

                        instructions.append(DeploymentInstruction(
                            card_blueprint_id=best_pilot['blueprint_id'],
                            card_name=best_pilot['name'],
                            target_location_id=loc.card_id,
                            target_location_name=loc.name,
                            priority=3,
                            reason=f"Extra pilot aboard {ship['name']}",
                            power_contribution=best_pilot['power'],
                            deploy_cost=best_pilot['cost'],
                            # Track which ship pilot is boarding
                            aboard_ship_name=ship['name'],
                            aboard_ship_blueprint_id=ship['blueprint_id'],
                            aboard_ship_card_id=None,  # Will be set when ship deploys
                        ))
                        logger.info(f"   👨‍✈️ Plan: Deploy extra pilot {best_pilot['name']} aboard {ship['name']}")

        return instructions, force_remaining

    def _generate_space_plan_for_ship(self, ship: Dict, other_ships: List[Dict],
                                       space_targets: List[LocationAnalysis],
                                       force_budget: int,
                                       pilots: List[Dict] = None) -> Tuple[List[DeploymentInstruction], int]:
        """
        Generate a space plan starting with a specific starship.

        This allows comparing plans that prioritize different starships.
        Any pilot (warrior or pure) can fly, but pure pilots are preferred.
        """
        instructions = []
        force_remaining = force_budget
        available_pilots = (pilots or []).copy()

        if ship['cost'] > force_remaining:
            return instructions, force_remaining

        MIN_ESTABLISH_POWER = self.deploy_threshold

        # Find best location for this specific ship
        best_loc = None
        for loc in space_targets:
            min_needed = loc.their_power + 1 if loc.their_power > 0 else MIN_ESTABLISH_POWER
            if ship['power'] >= min_needed:
                best_loc = loc
                break

        if not best_loc:
            # Ship can't beat enemy or establish at any location - skip
            # IMPORTANT: Don't deploy into deficit or matching scenarios!
            # We should only deploy if we can BEAT the enemy power.
            logger.debug(f"   ⏭️ {ship['name']} ({ship['power']} power) can't beat enemy or establish space control (threshold {MIN_ESTABLISH_POWER})")
            return instructions, force_remaining

        # Deploy the primary ship
        if best_loc.their_power > 0:
            reason = f"Space: Beat {best_loc.name} ({ship['power']} vs {best_loc.their_power} power)"
        else:
            reason = f"Space: Establish control at {best_loc.name} ({ship['power']} power)"

        instructions.append(DeploymentInstruction(
            card_blueprint_id=ship['blueprint_id'],
            card_name=ship['name'],
            target_location_id=best_loc.card_id,
            target_location_name=best_loc.name,
            priority=2,
            reason=reason,
            power_contribution=ship['power'],
            deploy_cost=ship['cost'],
        ))
        force_remaining -= ship['cost']

        # Add pilot aboard if available (prefer pure pilots)
        if available_pilots:
            affordable_pilots_here = [p for p in available_pilots if p['cost'] <= force_remaining]
            if affordable_pilots_here:
                best_pilot = max(affordable_pilots_here,
                                key=lambda p: (_pilot_score_for_ship(p, ship), p.get('is_pure_pilot', False)))
                force_remaining -= best_pilot['cost']
                available_pilots.remove(best_pilot)
                instructions.append(DeploymentInstruction(
                    card_blueprint_id=best_pilot['blueprint_id'],
                    card_name=best_pilot['name'],
                    target_location_id=best_loc.card_id,
                    target_location_name=best_loc.name,
                    priority=3,
                    reason=f"Pure pilot aboard {ship['name']}",
                    power_contribution=best_pilot['power'],
                    deploy_cost=best_pilot['cost'],
                ))

        # Try to add more ships with remaining budget
        available_ships = [s for s in other_ships if s != ship]
        remaining_targets = [t for t in space_targets if t != best_loc]

        for loc in remaining_targets:
            if not available_ships or force_remaining <= 0:
                break

            power_goal = max(MIN_ESTABLISH_POWER, loc.their_power + 1)
            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                available_ships, force_remaining, power_goal, must_exceed=False
            )

            if not cards_for_location or power_allocated < MIN_ESTABLISH_POWER:
                continue

            force_remaining -= cost_used
            for s in cards_for_location:
                if s in available_ships:
                    available_ships.remove(s)

                if loc.their_power > 0:
                    reason = f"Space: Contest {loc.name} (vs {loc.their_power} power)"
                else:
                    reason = f"Space: Control {loc.name} ({loc.their_icons} icons)"

                instructions.append(DeploymentInstruction(
                    card_blueprint_id=s['blueprint_id'],
                    card_name=s['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=2,
                    reason=reason,
                    power_contribution=s['power'],
                    deploy_cost=s['cost'],
                ))

        return instructions, force_remaining

    def _generate_combined_space_plan(self, starships: List[Dict],
                                       space_targets: List[LocationAnalysis],
                                       force_budget: int,
                                       pilots: List[Dict] = None) -> Tuple[List[DeploymentInstruction], int]:
        """
        Generate a space plan using optimal combinations at each location.

        This considers deploying MULTIPLE starships + pilots to the SAME location
        to maximize power, potentially beating a single big ship strategy.
        Any pilot (warrior or pure) can fly, but pure pilots are preferred.
        """
        instructions = []
        force_remaining = force_budget
        available_ships = starships.copy()
        available_pilots = (pilots or []).copy()

        MIN_ESTABLISH_POWER = self.deploy_threshold

        for loc in space_targets:
            if not available_ships or force_remaining <= 0:
                break

            # Calculate power needed for this location
            power_goal = max(MIN_ESTABLISH_POWER, loc.their_power + 1)

            # Find optimal combination of starships for this location
            ships_for_location, power_allocated, cost_used = self._find_optimal_combination(
                available_ships, force_remaining, power_goal, must_exceed=False
            )

            # Check if the combination meets our requirements
            if not ships_for_location or power_allocated < MIN_ESTABLISH_POWER:
                continue

            # Calculate potential pilot bonus
            pilot_bonus_power = 0
            pilots_to_add = []
            remaining_after_ships = force_remaining - cost_used

            # Try to add pilots to boost power
            if available_pilots and remaining_after_ships > 0:
                affordable_pilots = [p for p in available_pilots if p['cost'] <= remaining_after_ships]
                # Add pilots sorted by power (best first), with bonus for matching ships

                def pilot_score_for_group(pilot):
                    """Score pilot for this group of ships, bonus if matches any ship."""
                    best_score = pilot.get('power', 0)
                    for ship in ships_for_location:
                        score = _pilot_score_for_ship(pilot, ship)
                        if score > best_score:
                            best_score = score
                    return best_score

                affordable_pilots.sort(key=pilot_score_for_group, reverse=True)
                for pilot in affordable_pilots:
                    if pilot['cost'] <= remaining_after_ships:
                        pilots_to_add.append(pilot)
                        pilot_bonus_power += pilot['power']
                        remaining_after_ships -= pilot['cost']
                        # Limit to one pilot per ship roughly
                        if len(pilots_to_add) >= len(ships_for_location):
                            break

            total_power = power_allocated + pilot_bonus_power

            # Build reason
            if loc.their_power > 0:
                reason = f"Space: Contest {loc.name} (combined {total_power} vs {loc.their_power})"
            else:
                reason = f"Space: Control {loc.name} (combined {total_power} power)"

            # Add instructions for all ships
            force_remaining -= cost_used
            for ship in ships_for_location:
                if ship in available_ships:
                    available_ships.remove(ship)

                instructions.append(DeploymentInstruction(
                    card_blueprint_id=ship['blueprint_id'],
                    card_name=ship['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=2,
                    reason=reason,
                    power_contribution=ship['power'],
                    deploy_cost=ship['cost'],
                ))

            # Add instructions for pilots - pair with ships
            for i, pilot in enumerate(pilots_to_add):
                if pilot in available_pilots:
                    available_pilots.remove(pilot)
                force_remaining -= pilot['cost']

                # Pair pilot with a ship (cycle through if more pilots than ships)
                target_ship = ships_for_location[i % len(ships_for_location)]

                instructions.append(DeploymentInstruction(
                    card_blueprint_id=pilot['blueprint_id'],
                    card_name=pilot['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=3,  # After ships
                    reason=f"Pilot aboard {target_ship['name']} at {loc.name}",
                    power_contribution=pilot['power'],
                    deploy_cost=pilot['cost'],
                    aboard_ship_name=target_ship['name'],
                    aboard_ship_blueprint_id=target_ship['blueprint_id'],
                    aboard_ship_card_id=None,  # Will be set when ship deploys
                ))

        return instructions, force_remaining

    def _generate_all_space_plans(self, starships: List[Dict],
                                   space_targets: List[LocationAnalysis],
                                   force_budget: int,
                                   all_pilots: List[Dict],
                                   locations: List[LocationAnalysis],
                                   space_threshold: int = None,
                                   contest_advantage: int = None) -> List[Tuple[List[DeploymentInstruction], int, float]]:
        """
        Generate multiple space plans - one for EACH target location.

        CRITICAL: Generate a plan for each location independently and score them.
        This ensures we consider crushing contested locations vs establishing at empty.

        Args:
            starships: List of starship cards in hand
            space_targets: List of space locations to consider
            force_budget: Available force for deploying
            all_pilots: List of ALL pilot characters (not just pure pilots)
            locations: All analyzed locations for scoring
            space_threshold: Dynamic threshold for establishing space presence.
                             Uses self.deploy_threshold if not specified.
            contest_advantage: Power advantage required over enemy to contest.
                              Defaults to get_min_contest_advantage() (2) if not specified.

        Returns list of (instructions, force_remaining, score) tuples.
        """
        plans = []

        # Filter to affordable starships WITH permanent pilots (can deploy alone)
        affordable_piloted_ships = [
            s for s in starships
            if s['cost'] <= force_budget and s.get('has_permanent_pilot', False)
        ]

        # Also consider unpiloted ships IF we have affordable pilots
        affordable_unpiloted_ships = [
            s for s in starships
            if s['cost'] <= force_budget and not s.get('has_permanent_pilot', False)
        ]
        # CRITICAL: Use ALL pilots (any character with pilot ability can fly ships)
        # Not just "pure pilots" - warrior-pilots like Solo can also pilot!
        affordable_pilots = [p for p in all_pilots if p['cost'] <= force_budget] if all_pilots else []

        # For unpiloted ships, we need a pilot - create combined entries
        # The ship+pilot combo is treated as a single deployable unit
        # CRITICAL: Use base_power for unpiloted ships (power is 0 for unpiloted)
        ship_pilot_combos = []
        for ship in affordable_unpiloted_ships:
            for pilot in affordable_pilots:
                combined_cost = ship['cost'] + pilot['cost']
                if combined_cost <= force_budget:
                    # IMPORTANT: Use base_power for the ship since unpiloted has power=0
                    ship_base_power = ship.get('base_power', ship['power'])
                    combined_power = ship_base_power + pilot['power']
                    # Create a combo entry - will unpack when building instructions
                    ship_pilot_combos.append({
                        'blueprint_id': ship['blueprint_id'],  # Primary is the ship
                        'name': ship['name'],
                        'power': combined_power,  # Ship base + pilot power
                        'base_power': combined_power,  # Same as power for combo
                        'cost': combined_cost,
                        'is_combo': True,
                        'ship': ship,
                        'pilot': pilot,
                    })
                    break  # One pilot per ship is enough

        # Combine all deployable space units
        all_deployable_space = affordable_piloted_ships + ship_pilot_combos

        if not all_deployable_space:
            # Log WHY no space plans can be generated (helps debugging)
            if starships:
                if affordable_piloted_ships:
                    # This shouldn't happen - we have affordable piloted ships
                    pass
                elif affordable_unpiloted_ships:
                    if not all_pilots:
                        logger.info(f"   ⚠️ Space: {len(affordable_unpiloted_ships)} unpiloted ship(s) but NO pilots in hand")
                    elif not affordable_pilots:
                        logger.info(f"   ⚠️ Space: {len(affordable_unpiloted_ships)} unpiloted ship(s) but pilots too expensive (force={force_budget})")
                    elif not ship_pilot_combos:
                        # Have unpiloted ships and pilots, but combos exceed budget
                        cheapest_combo = None
                        for ship in affordable_unpiloted_ships:
                            for pilot in affordable_pilots:
                                cost = ship['cost'] + pilot['cost']
                                if cheapest_combo is None or cost < cheapest_combo[2]:
                                    cheapest_combo = (ship['name'], pilot['name'], cost)
                        if cheapest_combo:
                            logger.info(f"   ⚠️ Space: ship+pilot combos exceed budget "
                                       f"(cheapest: {cheapest_combo[0]}+{cheapest_combo[1]}={cheapest_combo[2]}, have {force_budget})")
                else:
                    # Ships exist but none affordable - try to find cheaper alternative
                    cheapest = min(starships, key=lambda s: s['cost'])
                    logger.info(f"   ⚠️ Space: starship(s) too expensive "
                               f"(cheapest: {cheapest['name']} costs {cheapest['cost']}, have {force_budget})")

                    # System 3: Try to find ANY affordable starship as alternative
                    # This looks at the full hand, not just pre-filtered list
                    if self._board_state:
                        already_in_plan = {s['blueprint_id'] for s in starships}
                        alternative = self._find_cheaper_alternative(
                            self._board_state,
                            card_type="starship",
                            max_cost=force_budget,
                            exclude_blueprints=already_in_plan
                        )
                        if alternative:
                            # Found a cheaper alternative! Add it to deployables
                            if alternative.get('has_permanent_pilot', False):
                                affordable_piloted_ships = [alternative]
                                logger.info(f"   🔄 Using alternative ship: {alternative['name']}")
                            else:
                                affordable_unpiloted_ships = [alternative]
                                # Rebuild combos with this ship
                                ship_pilot_combos = []
                                for pilot in affordable_pilots:
                                    combined_cost = alternative['cost'] + pilot['cost']
                                    if combined_cost <= force_budget:
                                        ship_base_power = alternative.get('base_power', 0)
                                        combined_power = ship_base_power + pilot['power']
                                        ship_pilot_combos.append({
                                            'blueprint_id': alternative['blueprint_id'],
                                            'name': alternative['name'],
                                            'power': combined_power,
                                            'base_power': combined_power,
                                            'cost': combined_cost,
                                            'is_combo': True,
                                            'ship': alternative,
                                            'pilot': pilot,
                                        })
                                        break
                                logger.info(f"   🔄 Using alternative ship: {alternative['name']} (needs pilot)")

                            # Rebuild all_deployable_space with the alternative
                            all_deployable_space = affordable_piloted_ships + ship_pilot_combos
                            # Continue with normal plan generation below
            if not all_deployable_space:
                return plans

        # Use dynamic threshold if provided, otherwise fall back to default
        MIN_ESTABLISH_POWER = space_threshold if space_threshold is not None else self.deploy_threshold

        # Use dynamic contest advantage if provided, otherwise fall back to default
        required_advantage = contest_advantage if contest_advantage is not None else get_min_contest_advantage()

        # === GENERATE A PLAN FOR EACH TARGET LOCATION ===
        # This is the key change: try each location independently
        for target_loc in space_targets:
            # Adjust budget for contested locations (need +1 reserve for battle initiation)
            # This ensures plans don't exceed total_force when reserve is added
            if target_loc.their_power > 0:
                loc_budget = force_budget - 1  # Reserve 1 extra for battle
            else:
                loc_budget = force_budget  # Uncontested - use full budget

            if loc_budget <= 0:
                continue

            # Filter deployables for this location (system restrictions)
            location_deployables = []
            for ship in all_deployable_space:
                # Check deploy system restrictions
                restrictions = ship.get('deploy_restriction_systems', [])
                if restrictions:
                    # Ship has restrictions - check if location matches any allowed system
                    loc_clean = target_loc.name.lstrip('•').strip()
                    can_deploy = False
                    for system in restrictions:
                        system_lower = system.lower()
                        loc_lower = loc_clean.lower()
                        if loc_lower.startswith(system_lower):
                            can_deploy = True
                            break
                        if ':' in loc_clean:
                            loc_system = loc_clean.split(':')[0].strip().lower()
                            if loc_system == system_lower:
                                can_deploy = True
                                break
                    if not can_deploy:
                        continue  # Ship restricted to other systems
                location_deployables.append(ship)

            if not location_deployables:
                continue

            # Calculate power needed for this specific location
            # CRITICAL: Include our EXISTING power at the location for crushable scenarios
            existing_power = target_loc.my_power if target_loc.my_power > 0 else 0

            # For contested locations: need BOTH deploy threshold AND required advantage
            # required_advantage is dynamic based on life force (lower when desperate)
            if target_loc.their_power > 0:
                # Total needed = max(threshold, enemy + advantage)
                total_power_goal = max(MIN_ESTABLISH_POWER, target_loc.their_power + required_advantage)
                # We only need to DEPLOY enough to reach goal (subtract existing)
                power_goal = max(1, total_power_goal - existing_power)
            else:
                power_goal = max(1, MIN_ESTABLISH_POWER - existing_power)

            # Find optimal combination of ships (including ship+pilot combos) for THIS location
            ships_for_loc, power_allocated, cost_used = self._find_optimal_combination(
                location_deployables.copy(), loc_budget, power_goal, must_exceed=(target_loc.their_power > 0 and existing_power == 0)
            )

            if not ships_for_loc:
                continue

            # Calculate TOTAL power (existing + deployed)
            total_power = existing_power + power_allocated

            # Must meet BOTH thresholds for contested locations
            if target_loc.their_power > 0:
                # Need to beat enemy by at least required_advantage
                if total_power < target_loc.their_power + required_advantage:
                    continue  # Not enough advantage, skip
                # Also need to meet deploy threshold (so we're not left weak after battle)
                if total_power < MIN_ESTABLISH_POWER:
                    continue  # Would be left too weak after winning
            elif total_power < MIN_ESTABLISH_POWER:
                continue  # Can't establish at empty location

            # Build instructions for this location
            instructions = []
            # Keep force_remaining relative to original budget for consistent validation
            force_remaining = force_budget - cost_used
            pilots_already_added = set()  # Track pilots already included via combos

            for ship_entry in ships_for_loc:
                if target_loc.their_power > 0:
                    power_advantage = total_power - target_loc.their_power
                    if power_advantage >= get_battle_favorable_threshold():
                        reason = f"Space: Crush {target_loc.name} ({total_power} vs {target_loc.their_power})"
                    else:
                        reason = f"Space: Contest {target_loc.name} ({total_power} vs {target_loc.their_power}, +{power_advantage})"
                else:
                    reason = f"Space: Control {target_loc.name} (combined {total_power} power)"

                # Check if this is a ship+pilot combo - unpack into separate instructions
                if ship_entry.get('is_combo'):
                    actual_ship = ship_entry['ship']
                    pilot = ship_entry['pilot']

                    # Add ship instruction
                    # CRITICAL: Use base_power for unpiloted ships (power is 0 until piloted)
                    ship_power = actual_ship.get('base_power', actual_ship['power'])
                    instructions.append(DeploymentInstruction(
                        card_blueprint_id=actual_ship['blueprint_id'],
                        card_name=actual_ship['name'],
                        target_location_id=target_loc.card_id,
                        target_location_name=target_loc.name,
                        priority=2,
                        reason=reason,
                        power_contribution=ship_power,
                        deploy_cost=actual_ship['cost'],
                    ))

                    # Add pilot instruction
                    instructions.append(DeploymentInstruction(
                        card_blueprint_id=pilot['blueprint_id'],
                        card_name=pilot['name'],
                        target_location_id=target_loc.card_id,
                        target_location_name=target_loc.name,
                        priority=3,
                        reason=f"Pilot aboard {actual_ship['name']} at {target_loc.name}",
                        power_contribution=pilot['power'],
                        deploy_cost=pilot['cost'],
                        # Track which ship pilot is boarding
                        aboard_ship_name=actual_ship['name'],
                        aboard_ship_blueprint_id=actual_ship['blueprint_id'],
                        aboard_ship_card_id=None,  # Will be set when ship deploys
                    ))
                    pilots_already_added.add(pilot['blueprint_id'])
                else:
                    # Regular piloted ship
                    instructions.append(DeploymentInstruction(
                        card_blueprint_id=ship_entry['blueprint_id'],
                        card_name=ship_entry['name'],
                        target_location_id=target_loc.card_id,
                        target_location_name=target_loc.name,
                        priority=2,
                        reason=reason,
                        power_contribution=ship_entry['power'],
                        deploy_cost=ship_entry['cost'],
                    ))

            # Add additional pilots ONLY for ships with permanent pilots (power boost)
            # Unpiloted ships already got their required pilot via combos - don't add more!
            piloted_ships = [s for s in ships_for_loc if not s.get('is_combo')]
            if piloted_ships and all_pilots and force_remaining > 0:
                # Find the first piloted ship to board
                target_ship = piloted_ships[0]
                for pilot in all_pilots:
                    if pilot['blueprint_id'] in pilots_already_added:
                        continue  # Already included via combo
                    if pilot['cost'] <= force_remaining:
                        instructions.append(DeploymentInstruction(
                            card_blueprint_id=pilot['blueprint_id'],
                            card_name=pilot['name'],
                            target_location_id=target_loc.card_id,
                            target_location_name=target_loc.name,
                            priority=3,
                            reason=f"Pilot aboard {target_ship['name']} at {target_loc.name}",
                            power_contribution=pilot['power'],
                            deploy_cost=pilot['cost'],
                            aboard_ship_name=target_ship['name'],
                            aboard_ship_blueprint_id=target_ship['blueprint_id'],
                            aboard_ship_card_id=None,  # Will be set when ship deploys
                        ))
                        force_remaining -= pilot['cost']
                        break  # One additional pilot is enough

            if instructions:
                score = self._score_plan(instructions, locations, self._current_turn)
                plans.append((instructions, force_remaining, score))

        return plans

    def create_plan(self, board_state) -> DeploymentPlan:
        """
        Create a comprehensive deployment plan for this phase.

        Evaluates GROUND and SPACE plans independently, then picks the best.
        Each plan gets the full force budget to work with.
        """
        from .card_loader import get_card

        # Get current state for cache invalidation
        current_phase = getattr(board_state, 'current_phase', '')
        current_turn = getattr(board_state, 'turn_number', 0)
        self._current_turn = current_turn  # Store for use in helper methods
        self._board_state = board_state  # Store for goal scoring in _score_plan
        is_my_turn = board_state.is_my_turn() if hasattr(board_state, 'is_my_turn') else True

        # === CRITICAL: Don't plan during opponent's turn! ===
        # Both players have "Deploy (turn #X)" phase, but we only want to plan for OUR deploy.
        # If it's not our turn, don't create or update the plan.
        if not is_my_turn:
            logger.debug(f"📋 Skipping deploy plan - not our turn (phase={current_phase})")
            # Return a HOLD_BACK plan if we don't have one, or the cached plan
            if not self.current_plan:
                return DeploymentPlan(
                    strategy=DeployStrategy.HOLD_BACK,
                    reason="Not our turn - waiting",
                )
            return self.current_plan

        # ONLY invalidate cache on phase or turn change
        # Do NOT invalidate due to force/hand changes during deployment!
        # The plan should persist through the entire deploy phase.
        # Force/hand changes naturally happen as we execute the plan.
        state_changed = (
            current_phase != self._last_phase or
            current_turn != self._last_turn
        )

        if state_changed:
            if self.current_plan:
                logger.info(f"📋 Invalidating cached plan: phase_changed={current_phase != self._last_phase}, "
                           f"turn_changed={current_turn != self._last_turn}")
            self._last_phase = current_phase
            self._last_turn = current_turn
            self.current_plan = None

        # Return existing plan if we have one
        if self.current_plan and self.current_plan.phase_started:
            logger.debug("📋 Returning cached deployment plan")
            return self.current_plan

        logger.info("📋 Creating comprehensive deployment plan...")

        # === UPDATE STRATEGIC STATE ===
        # Update strategic state at deploy phase start for accurate mode flags
        strategic_state = getattr(board_state, 'strategic_state', None)
        if strategic_state and strategic_state.enabled:
            strategic_state.on_deploy_phase_start(board_state)

        # Log config values being used for this decision
        logger.info(f"📊 DEPLOY CONFIG: early_game_threshold={get_early_game_threshold()}, "
                   f"early_game_turns={get_early_game_turns()}, deploy_threshold={get_deploy_threshold()}, "
                   f"overkill_threshold={get_deploy_overkill_threshold()}, contest_advantage={get_min_contest_advantage()}")

        # =================================================================
        # FAILED HOLD CHECK
        # If we held last turn for a specific reason, check if it worked out.
        # If the hold didn't pay off (enemy countered, didn't draw cards),
        # mark the hold as failed so we're more aggressive this turn.
        # =================================================================
        if hasattr(board_state, 'next_turn_crush_plan') and board_state.next_turn_crush_plan:
            crush_plan = board_state.next_turn_crush_plan
            # Check if we can still execute the crush
            target_loc = None
            for loc in board_state.locations:
                if loc.name == crush_plan.target_location_name:
                    target_loc = loc
                    break

            if target_loc:
                # Check if enemy power changed significantly (they counter-deployed)
                current_enemy_power = target_loc.their_power
                planned_enemy_power = crush_plan.target_enemy_power
                enemy_reinforced = current_enemy_power > planned_enemy_power + 2

                # Check if we have enough force for the crush
                force_available = board_state.force_pile
                force_needed = crush_plan.force_needed
                insufficient_force = force_available < force_needed

                if enemy_reinforced or insufficient_force:
                    board_state.hold_failed_last_turn = True
                    board_state.last_hold_reason = f"Crush at {crush_plan.target_location_name}"
                    if enemy_reinforced:
                        logger.warning(f"⚠️ HOLD FAILED: Enemy reinforced {crush_plan.target_location_name} "
                                      f"({planned_enemy_power} → {current_enemy_power})")
                    if insufficient_force:
                        logger.warning(f"⚠️ HOLD FAILED: Insufficient force ({force_available}/{force_needed})")
                    logger.info(f"   Will be more aggressive this turn - no more speculative holds")
                else:
                    logger.info(f"✅ Previous hold valid: Can still crush {crush_plan.target_location_name}")

            # Clear the old crush plan regardless
            board_state.next_turn_crush_plan = None

        # Log side detection for debugging
        my_side = getattr(board_state, 'my_side', 'unknown')
        logger.info(f"   🎭 My side: {my_side}")

        # DIAGNOSTIC: Log full hand for debugging
        raw_hand = getattr(board_state, 'cards_in_hand', [])
        logger.info(f"   📊 Raw board_state: force_pile={board_state.force_pile}, "
                   f"cards_in_hand={len(raw_hand)}, turn={getattr(board_state, 'turn_number', '?')}")
        if raw_hand:
            logger.info(f"   🃏 Full hand ({len(raw_hand)} cards):")
            for i, c in enumerate(raw_hand):
                # Get card metadata for power/deploy info
                card_meta = get_card(c.blueprint_id) if c.blueprint_id else None
                power = card_meta.power_value if card_meta and card_meta.power_value else 0
                deploy = card_meta.deploy_value if card_meta and card_meta.deploy_value else 0
                card_type = card_meta.card_type if card_meta else "Unknown"
                logger.info(f"      [{i}] {c.card_title or c.blueprint_id} ({card_type}) - Power: {power}, Deploy: {deploy}")

        # Get available force (use base reserve initially - dynamic reserve applied per-plan)
        total_force = board_state.force_pile
        # Start with minimal reserve - actual reserve depends on what the plan does:
        # - Base: battle_force_reserve (1) for effects/reactions
        # - +1 if deploying to contested location (need to initiate battle)
        # - +1 per card that needs to flee (movement costs)
        base_reserve = self.battle_force_reserve

        # CRITICAL: When life force is low AND force is scarce, don't reserve anything!
        # Deploying SOMETHING is more important than saving 1 force for reactions
        life_force = getattr(board_state, 'life_force', 30)
        critical_life_threshold = 12  # Same as strategy config critical threshold
        low_force_threshold = 4  # If we have 4 or less, every force matters
        hand_size = len(board_state.cards_in_hand) if board_state.cards_in_hand else 0
        small_hand_threshold = 5  # If hand is below this, save force for drawing

        if life_force <= critical_life_threshold and total_force <= low_force_threshold:
            base_reserve = 0
            logger.warning(f"   ⚠️ CRITICAL: Life={life_force}, Force={total_force} - NO RESERVE, use everything!")

        force_to_spend = max(0, total_force - base_reserve)

        logger.info(f"   Force: {total_force} total, {force_to_spend} for deploying (base reserve {base_reserve}, dynamic per-plan)")

        # =================================================================
        # SYSTEM 1: EXPENSIVE CARD BUDGET
        # Check if we should save force for expensive high-value cards
        # =================================================================
        budget_reserve = 0
        expensive_budget = getattr(board_state, 'expensive_card_budget', None)

        # First, check if existing budget is complete (can now afford the card)
        budget_just_completed = False
        if expensive_budget:
            force_gen = self._estimate_force_generation([], board_state)
            if expensive_budget.update_turn(total_force, force_gen):
                # Can now afford! Clear the budget so we deploy normally
                target_name = expensive_budget.target_name
                board_state.expensive_card_budget = None
                expensive_budget = None
                budget_just_completed = True  # Don't create new budget this turn
                logger.info(f"💰 Budget cleared - deploying {target_name} this turn!")

        # If no active budget AND we didn't just complete one, check if we should create one
        if not expensive_budget and not budget_just_completed and life_force > critical_life_threshold:
            # Look for expensive high-value cards we can't afford
            expensive_threshold = 10  # Cards costing 10+ are "expensive"
            force_gen = self._estimate_force_generation([], board_state)

            raw_hand = getattr(board_state, 'cards_in_hand', [])
            high_value_expensive = []

            for card in raw_hand:
                if not card.blueprint_id:
                    continue
                card_meta = get_card(card.blueprint_id)
                if not card_meta:
                    continue

                deploy_cost = card_meta.deploy_value or 0
                power = card_meta.power_value or 0

                # Check if expensive and unaffordable
                if deploy_cost >= expensive_threshold and deploy_cost > force_to_spend:
                    # Check if it's high-value (starship with power >= 8, or main character)
                    is_valuable = False
                    is_starship = card_meta.is_starship

                    if is_starship and power >= 8:
                        is_valuable = True
                    elif card_meta.is_character and (
                        getattr(card_meta, 'is_main_character', False) or
                        getattr(card_meta, 'ability_value', 0) >= 5
                    ):
                        is_valuable = True

                    if is_valuable:
                        # Calculate turns to afford
                        force_deficit = deploy_cost - force_to_spend
                        turns_needed = max(1, (force_deficit + force_gen - 1) // force_gen) if force_gen > 0 else 99
                        value_score = power + (20 if is_starship else 0)

                        high_value_expensive.append({
                            'blueprint_id': card.blueprint_id,
                            'name': card_meta.title,
                            'cost': deploy_cost,
                            'power': power,
                            'is_starship': is_starship,
                            'turns_needed': turns_needed,
                            'value_score': value_score,
                        })

            # Pick the best target for budgeting (highest value / fewest turns)
            if high_value_expensive:
                # Sort by value per turn
                high_value_expensive.sort(key=lambda x: x['value_score'] / x['turns_needed'], reverse=True)
                best = high_value_expensive[0]

                # Only budget if achievable in 2 turns
                if best['turns_needed'] <= 2:
                    save_per_turn = (best['cost'] - force_to_spend + force_gen - 1) // best['turns_needed']
                    save_per_turn = min(save_per_turn, force_to_spend // 2)  # Never save more than half

                    if save_per_turn >= 2:  # Only worth budgeting if saving at least 2
                        expensive_budget = ExpensiveCardBudget(
                            target_blueprint_id=best['blueprint_id'],
                            target_name=best['name'],
                            target_cost=best['cost'],
                            target_power=best['power'],
                            save_per_turn=save_per_turn,
                            turns_remaining=best['turns_needed'],
                            is_starship=best['is_starship'],
                        )
                        board_state.expensive_card_budget = expensive_budget
                        logger.info(f"💰 BUDGET: Saving for {best['name']} "
                                   f"(cost {best['cost']}, power {best['power']}, {best['turns_needed']} turns)")

        # Apply budget reserve if active
        # BUT: Check if we have a space emergency with affordable ships that budget would block
        budget_suspended = False
        if expensive_budget:
            budget_reserve = expensive_budget.get_force_to_reserve(force_to_spend)

            # Check for space emergency: are we bleeding in space with no presence?
            has_space_emergency = False
            affordable_ship_blocked = False
            if hasattr(board_state, 'strategic_state') and board_state.strategic_state:
                ss = board_state.strategic_state
                has_space_emergency = getattr(ss, 'space_emergency', False)

                if has_space_emergency:
                    # Check if we have an affordable ship that budget would block
                    raw_hand = getattr(board_state, 'cards_in_hand', [])
                    for card in raw_hand:
                        if not card.blueprint_id:
                            continue
                        card_meta = get_card(card.blueprint_id)
                        if not card_meta or not card_meta.is_starship:
                            continue
                        ship_cost = card_meta.deploy_value or 0
                        # Check if affordable now but not with budget
                        if ship_cost <= force_to_spend and ship_cost > (force_to_spend - budget_reserve):
                            affordable_ship_blocked = True
                            break

            if has_space_emergency and affordable_ship_blocked:
                # Suspend budget - deploying ship to stop bleed is more important
                logger.warning(f"💰⚠️ BUDGET SUSPENDED: Space emergency! "
                              f"Affordable ship blocked by budget - prioritizing immediate deploy")
                budget_suspended = True
            else:
                force_to_spend = max(2, force_to_spend - budget_reserve)  # Keep minimum 2 for emergencies
                logger.info(f"💰 Reserving {budget_reserve} force for {expensive_budget.target_name}, "
                           f"{force_to_spend} remaining for this turn")

        # Initialize the plan
        plan = DeploymentPlan(
            strategy=DeployStrategy.HOLD_BACK,
            reason="Planning...",
            total_force_available=total_force,
            force_reserved_for_battle=base_reserve,  # Will be updated with dynamic reserve
            force_to_spend=force_to_spend,
        )

        # Get all deployable cards from hand
        all_cards = self._get_all_deployable_cards(board_state)

        # DEBUG: Log what was returned to diagnose empty hand issue
        if not all_cards:
            logger.warning(f"   ⚠️ _get_all_deployable_cards returned EMPTY list!")
        else:
            logger.debug(f"   📋 _get_all_deployable_cards returned {len(all_cards)} cards")
            for c in all_cards:
                logger.debug(f"      - {c['name']}: is_char={c['is_character']}, is_ship={c['is_starship']}")

        locations_in_hand = [c for c in all_cards if c['is_location']]

        # Check if GamePlan wants us to save any cards
        saved_cards = set()
        if hasattr(board_state, 'game_plan') and board_state.game_plan:
            saved_cards = set(board_state.game_plan.cards_to_save or [])
            if saved_cards:
                logger.info(f"   🎯 GamePlan says SAVE these cards: {saved_cards}")

        # Filter out saved cards from deployment consideration
        def should_deploy(card):
            bp_id = card.get('blueprint_id', '')
            if bp_id in saved_cards:
                logger.info(f"   ⏸️ EXCLUDING {card['name']} from deploy (marked for saving)")
                return False
            return True

        characters = [c for c in all_cards if c['is_character'] and not c['is_location'] and should_deploy(c)]
        # IMPORTANT: Separate starships (space) from vehicles (ground)
        starships = [c for c in all_cards if c['is_starship'] and not c['is_vehicle'] and should_deploy(c)]
        vehicles = [c for c in all_cards if c['is_vehicle'] and should_deploy(c)]
        weapons = [c for c in all_cards if c.get('is_weapon') or c.get('is_device')]
        # Pilots can go aboard unpiloted vehicles/starships
        pilots = [c for c in characters if c['is_pilot']]

        logger.info(f"   Hand: {len(locations_in_hand)} locations, {len(characters)} characters, "
                    f"{len(starships)} starships, {len(vehicles)} vehicles, {len(weapons)} weapons")

        # Log character details for debugging
        if characters:
            char_details = [(c['name'], c['power'], c['cost']) for c in characters]
            logger.info(f"   📋 Characters available: {char_details}")
        else:
            logger.info(f"   ⚠️ No characters in hand!")

        # Log starship details for debugging
        if starships:
            for s in starships:
                pilot_status = 'needs_pilot (0 power)' if s.get('needs_pilot') else f'has_pilot ({s["power"]} power)'
                logger.info(f"   🚀 Starship: {s['name']} - {pilot_status}, cost={s['cost']}")
        else:
            logger.info(f"   ⚠️ No starships in hand!")

        # Calculate total deployable power for battle analysis
        total_deployable_power = sum(c['power'] for c in characters)
        total_deployable_power += sum(c['power'] for c in starships)
        total_deployable_power += sum(c['power'] for c in vehicles)

        # Analyze board locations with battle/flee context
        locations = self._analyze_locations(board_state, total_deployable_power)

        # Log all analyzed locations for debugging
        logger.info(f"   📍 Analyzed {len(locations)} locations on board:")
        for loc in locations:
            if loc.is_space:
                loc_type = "SPACE"
            elif loc.is_interior and loc.is_exterior:
                loc_type = "GROUND(int+ext)"
            elif loc.is_interior:
                loc_type = "GROUND(interior)"
            elif loc.is_exterior:
                loc_type = "GROUND(exterior)"
            else:
                loc_type = "GROUND"
            logger.info(f"      - {loc.name}: {loc_type}, my={loc.my_power}, their={loc.their_power}, my_icons={loc.my_icons}, their_icons={loc.their_icons}")

        # === LOG OPPONENT BOARD STATE SUMMARY ===
        self._log_opponent_board_summary(board_state, locations)

        # =================================================================
        # Calculate DYNAMIC thresholds for ground and space separately
        # Early game (turn < 4) with no contested locations: relaxed threshold (4)
        # Later game OR contested locations: full threshold (6)
        # Late game with low life force: threshold decays further
        # =================================================================
        life_force = board_state.total_reserve_force() if hasattr(board_state, 'total_reserve_force') else 60
        ground_threshold = self._get_dynamic_threshold(locations, is_space=False, turn_number=current_turn, life_force=life_force)
        space_threshold = self._get_dynamic_threshold(locations, is_space=True, turn_number=current_turn, life_force=life_force)

        # Calculate dynamic contest advantage based on life force
        # When life is low, accept riskier battles (ties instead of requiring +2 advantage)
        contest_advantage = get_contest_advantage(life_force)
        if contest_advantage < get_min_contest_advantage():
            logger.info(f"   📊 Dynamic thresholds: ground={ground_threshold}, space={space_threshold} (turn {current_turn}, life={life_force})")
            logger.info(f"   ⚔️ Low life ({life_force}): accepting {'+' + str(contest_advantage) if contest_advantage > 0 else 'ties'} instead of +2 advantage")
        else:
            logger.info(f"   📊 Dynamic thresholds: ground={ground_threshold}, space={space_threshold} (turn {current_turn}, life={life_force})")

        # =================================================================
        # CHECK OBJECTIVE-BASED DEPLOYMENT RESTRICTIONS
        # "We Have A Plan" (14_52) prevents deploying chars to interior Naboo sites
        # =================================================================
        whap_restriction = has_we_have_a_plan_restriction(board_state)
        if whap_restriction:
            logger.info("   📋 ACTIVE: 'We Have A Plan' - cannot deploy chars to interior Naboo sites")

        # =================================================================
        # STEP 1: DEPLOY LOCATIONS FIRST
        # This is CRITICAL - locations open new tactical options
        # Also create virtual LocationAnalysis for each deployed location so
        # characters can be planned to deploy there in the same phase.
        # =================================================================
        force_remaining = force_to_spend
        newly_deployed_locations: List[LocationAnalysis] = []

        for loc_card in locations_in_hand:
            if loc_card['cost'] <= force_remaining:
                plan.instructions.append(DeploymentInstruction(
                    card_blueprint_id=loc_card['blueprint_id'],
                    card_name=loc_card['name'],
                    target_location_id=None,
                    target_location_name=None,
                    priority=0,  # Highest priority
                    reason="Deploy location to open new options",
                    deploy_cost=loc_card['cost'],
                ))
                force_remaining -= loc_card['cost']
                logger.info(f"   📍 Plan: Deploy location {loc_card['name']} (cost {loc_card['cost']})")

                # Create virtual LocationAnalysis for the newly deployed location
                # so we can plan character deployments there in the same phase
                loc_meta = get_card(loc_card['blueprint_id'])
                if loc_meta:
                    # Determine icons based on our side
                    if my_side == 'dark':
                        my_icons = loc_meta.dark_side_icons or 0
                        their_icons = loc_meta.light_side_icons or 0
                    else:
                        my_icons = loc_meta.light_side_icons or 0
                        their_icons = loc_meta.dark_side_icons or 0

                    is_ground = getattr(loc_meta, 'is_site', False) or 'Site' in (loc_meta.sub_type or '')
                    is_space = getattr(loc_meta, 'is_system', False) or 'System' in (loc_meta.sub_type or '')
                    is_interior = getattr(loc_meta, 'is_interior', False)
                    is_exterior = getattr(loc_meta, 'is_exterior', True)  # Default exterior

                    # Only add as target if opponent has icons there (for force drain value)
                    if their_icons > 0:
                        virtual_loc = LocationAnalysis(
                            card_id=f"planned_{loc_card['blueprint_id']}",
                            name=loc_card['name'],
                            is_ground=is_ground,
                            is_space=is_space,
                            is_site=is_ground,  # Sites are ground locations
                            my_power=0,
                            their_power=0,
                            my_icons=my_icons,
                            their_icons=their_icons,
                            is_interior=is_interior,
                            is_exterior=is_exterior,
                            location_index=-1,  # No index yet
                        )
                        newly_deployed_locations.append(virtual_loc)
                        logger.info(f"   📍 New location available for chars: {loc_card['name']} (our={my_icons}, their={their_icons} icons)")

        # =================================================================
        # STEP 2: IDENTIFY CONTESTED LOCATIONS (reduce harm)
        # Locations where we have presence but are at power deficit
        # CRITICAL: Skip locations where we should FLEE
        # Split into ground (use characters) and space (use starships)
        # =================================================================
        contested_ground = [
            loc for loc in locations
            if loc.my_power > 0 and loc.their_power > 0 and loc.power_differential < 0
            and not loc.should_flee  # DON'T REINFORCE IF WE'RE FLEEING
            and loc.is_ground  # Characters for ground
            and not (whap_restriction and is_interior_naboo_site(loc.name, loc.is_interior))  # Skip interior Naboo if WHAP active
        ]
        contested_space = [
            loc for loc in locations
            if loc.my_power > 0 and loc.their_power > 0 and loc.power_differential < 0
            and not loc.should_flee  # DON'T REINFORCE IF WE'RE FLEEING
            and loc.is_space  # Starships for space
        ]

        # Log any skipped flee locations
        flee_locs = [loc for loc in locations if loc.should_flee]
        for loc in flee_locs:
            logger.info(f"   🏃 Skip reinforce at {loc.name}: will flee ({loc.power_differential} diff)")

        # Sort: Battle opportunities first, then by severity (biggest deficit first)
        contested_ground.sort(key=lambda x: (not x.is_battle_opportunity, x.power_differential))
        contested_space.sort(key=lambda x: (not x.is_battle_opportunity, x.power_differential))

        # =================================================================
        # STEP 2B: IDENTIFY WEAK PRESENCE LOCATIONS (below threshold)
        # Locations where we have presence but power < deploy_threshold
        # These need reinforcement BEFORE we establish at new locations!
        # Uses dynamic thresholds calculated above (ground vs space separate)
        # =================================================================
        weak_presence_ground = [
            loc for loc in locations
            if loc.my_power > 0  # We have presence
            and loc.their_power == 0  # No enemy (not contested)
            and loc.my_power < ground_threshold  # Below dynamic ground threshold
            and loc.is_ground  # Ground location
            and loc.my_icons > 0  # Skip 0-icon locations (low value to reinforce)
            and loc.their_icons > 0  # Skip if opponent can't deploy here (safe location!)
            and not (whap_restriction and is_interior_naboo_site(loc.name, loc.is_interior))  # Skip interior Naboo if WHAP active
        ]
        weak_presence_space = [
            loc for loc in locations
            if loc.my_power > 0  # We have presence
            and loc.their_power == 0  # No enemy (not contested)
            and loc.my_power < space_threshold  # Below dynamic space threshold
            and loc.is_space  # Space location
            and loc.my_icons > 0  # Skip 0-icon locations (low value to reinforce)
            and loc.their_icons > 0  # Skip if opponent can't deploy here (safe location!)
        ]

        # Sort by icons (higher value locations first), then by how close to threshold
        weak_presence_ground.sort(key=lambda x: (-x.my_icons, x.my_power), reverse=False)
        weak_presence_space.sort(key=lambda x: (-x.my_icons, x.my_power), reverse=False)

        if weak_presence_ground:
            logger.info(f"   🔧 Weak presence ground (need reinforcement): "
                       f"{[(loc.name, loc.my_power) for loc in weak_presence_ground]}")
        if weak_presence_space:
            logger.info(f"   🔧 Weak presence space (need reinforcement): "
                       f"{[(loc.name, loc.my_power) for loc in weak_presence_space]}")

        # CRITICAL: Merge weak presence locations into contested list for processing
        # They have HIGHER priority than establishing at new locations
        # Add them AFTER truly contested locations (enemy > 0) but BEFORE establish
        contested_ground = contested_ground + weak_presence_ground
        contested_space = contested_space + weak_presence_space

        # Combined list for backwards compatibility (STEP 4 uses this for ground)
        contested = contested_ground

        # =================================================================
        # STEP 3: IDENTIFY UNCONTESTED TARGETS (gain ground)
        # Locations with opponent icons where we can establish
        # CRITICAL: Characters can only deploy to GROUND locations (is_ground=True)
        #           Starships can only deploy to SPACE locations (is_space=True)
        # =================================================================
        # Ground locations for characters
        # CRITICAL: Can only deploy to locations where we have icons OR presence
        uncontested_ground = [
            loc for loc in locations
            if loc.their_icons > 0  # Has opponent icons to deny
            and loc.my_power == 0  # We're not there yet
            and loc.their_power == 0  # Enemy has no presence (truly uncontested)
            and loc.is_ground  # Characters can only go to ground locations
            and loc.my_icons > 0  # MUST have force icons to deploy (or presence, but my_power==0)
            and not is_restricted_deployment_location(loc.name)  # Skip Dagobah/Ahch-To
        ]
        # Space locations for ships
        # CRITICAL: Can only deploy to locations where we have force icons
        # NOTE: "Uncontested" means no enemy ships - their_power == 0
        # Locations WITH enemy ships go in attackable_space instead
        uncontested_space = [
            loc for loc in locations
            if loc.their_icons > 0  # Has opponent icons to deny
            and loc.my_power == 0  # We're not there yet
            and loc.their_power == 0  # Enemy has no ships (truly uncontested)
            and loc.is_space  # It's a space location
            and loc.my_icons > 0  # MUST have our force icons to deploy
            and not is_restricted_deployment_location(loc.name)  # Skip Dagobah/Ahch-To
        ]

        # =================================================================
        # ATTACKABLE SPACE: Enemy has ships, we have icons, we can deploy and WIN
        # These are high-value targets for battle if we have a big enough ship
        # Key: my_icons > 0 (can deploy) AND their_power > 0 (enemy to fight)
        # =================================================================
        attackable_space = [
            loc for loc in locations
            if loc.is_space
            and loc.my_power == 0  # We're not there yet
            and loc.their_power > 0  # Enemy HAS ships there
            and loc.my_icons > 0  # We have icons - can deploy!
            and not is_restricted_deployment_location(loc.name)  # Skip Dagobah/Ahch-To
        ]
        if attackable_space:
            logger.info(f"   ⚔️ Attackable space: {[(loc.name, loc.their_power, loc.my_icons) for loc in attackable_space]}")

        # =================================================================
        # ATTACKABLE GROUND: STOP_BLEEDING locations with enemy presence
        # Key difference from contested_ground: we have NO presence (my_power=0)
        # but we have icons (my_icons > 0) so opponent drains us!
        # This is the MISSING category that caused strategic failures - the bot
        # was ignoring high-threat bleed locations because:
        # - Not in bleed_locations (enemy power > low_enemy_threshold)
        # - Not in contested_ground (requires my_power > 0)
        # - Not in uncontested_ground (requires their_power == 0)
        # =================================================================
        attackable_ground = [
            loc for loc in locations
            if loc.is_ground
            and loc.my_power == 0  # We're not there yet
            and loc.their_power > 0  # Enemy HAS presence there
            and loc.my_icons > 0  # We have icons - we're BLEEDING!
            and not loc.should_flee  # Don't attack if we can't win
            and not is_restricted_deployment_location(loc.name)  # Skip Dagobah/Ahch-To
            and not (whap_restriction and is_interior_naboo_site(loc.name, loc.is_interior))  # Skip interior Naboo if WHAP active
        ]
        if attackable_ground:
            logger.info(f"   🩸 Attackable ground (STOP_BLEEDING): {[(loc.name, loc.their_power, loc.my_icons) for loc in attackable_ground]}")

        # Sort by icons (most valuable first)
        # Primary: opponent icons (deny their force drain)
        # Secondary: our icons (maximize our force generation) - tiebreaker
        uncontested_ground.sort(key=lambda x: (x.their_icons, x.my_icons), reverse=True)
        uncontested_space.sort(key=lambda x: (x.their_icons, x.my_icons), reverse=True)
        # Sort attackable by enemy power (easier targets first for quick wins)
        attackable_space.sort(key=lambda x: x.their_power)
        # Sort attackable ground by our icons (highest drain/bleed priority first)
        # These are STOP_BLEEDING locations - stopping 2-icon drain is more valuable than 1-icon
        attackable_ground.sort(key=lambda x: (-x.my_icons, x.their_power))

        # CONCENTRATION STRATEGY: Don't spread thin!
        # Pick at most 1-2 locations to establish at, and deploy meaningful force there
        # Instead of 3 power to 4 locations, do 6+ power to 2 locations
        max_establish = get_max_establish_locations()
        uncontested_ground = uncontested_ground[:max_establish]
        uncontested_space = uncontested_space[:max_establish]

        if uncontested_ground:
            logger.info(f"   🎯 Ground targets (chars): {[loc.name for loc in uncontested_ground]}")
        if uncontested_space:
            logger.info(f"   🚀 Space targets (starships): {[loc.name for loc in uncontested_space]}")

        # =================================================================
        # STEP 3B: CALCULATE FORCE DRAIN ECONOMY
        # Track how much we're being drained vs how much we're draining them.
        # If drain gap is significantly negative (they drain more), we need
        # to prioritize establishing presence to "stop the bleeding"
        # =================================================================
        their_drain, our_drain, drain_gap, bleed_locations = self._calculate_force_drain_gap(locations)

        # =================================================================
        # MERGE ATTACKABLE_GROUND INTO CONTESTED LIST
        # These are STOP_BLEEDING locations where we have NO presence but
        # opponent does. They should be processed in STEP 4 with HIGH priority
        # because stopping 2-icon drains is critical to winning.
        # Add them AFTER contested (where we already have presence) because
        # maintaining existing presence is slightly higher priority than
        # establishing new presence at enemy-controlled locations.
        # =================================================================
        if attackable_ground:
            # Add to contested list for STEP 4 processing
            contested = contested + attackable_ground
            contested_ground = contested_ground + attackable_ground
            logger.info(f"   🩸 Added {len(attackable_ground)} STOP_BLEEDING targets to contested list")

        # =================================================================
        # STEP 4: ALLOCATE CHARACTERS TO CONTESTED/WEAK LOCATIONS
        # Priority: Reinforce locations where we're losing OR below threshold
        # Goal: Reach power advantage (contested) or threshold (weak presence)
        # Uses optimal combination finding to maximize power within budget
        # =================================================================
        available_chars = characters.copy()

        max_deployable_char_power = sum(c['power'] for c in available_chars)

        # =================================================================
        # PRE-CHECK: HOLD FOR DRAW IF WE CAN'T CONTEST BLEEDING LOCATIONS
        # Before iterating through contested locations, check if we're in a
        # situation where we're bleeding but can't meaningfully contest.
        # In this case, it may be better to HOLD and draw cards rather than
        # deploy sub-optimally to space or other unrelated locations.
        #
        # EXCEPTION: When GamePlan is active, skip this hold-for-draw check
        # in early game - we need to establish presence and be aggressive.
        # =================================================================
        gameplan_active = False
        if board_state and hasattr(board_state, 'game_plan') and board_state.game_plan:
            gameplan_active = getattr(board_state.game_plan, 'enabled', False)
        skip_hold_for_draw = gameplan_active and current_turn <= 2
        if skip_hold_for_draw:
            logger.info(f"   🎯 GamePlan active - skipping hold-for-draw check (turn {current_turn})")

        if attackable_ground and drain_gap < 0 and not skip_hold_for_draw:
            # Check if we can meaningfully contest ANY bleeding location
            can_contest_any = False
            total_char_power = sum(c['power'] for c in available_chars)
            destiny_margin = 4  # Average destiny draw advantage

            for loc in attackable_ground:
                # Can we beat this location's enemy power?
                if total_char_power + destiny_margin >= loc.their_power:
                    can_contest_any = True
                    break

            if not can_contest_any and attackable_ground:
                # We're bleeding and can't meaningfully contest ANY location
                # Evaluate whether holding for draw is better than sub-optimal deploy
                force_pile = getattr(board_state, 'force_pile', 0) or 0
                reserve_deck = getattr(board_state, 'reserve_deck', 0) or 0

                should_hold, hold_reason, expected_value = self._evaluate_hold_for_bleeding(
                    domain="ground",
                    bleeding_locations=attackable_ground,
                    deployable_power=total_char_power,
                    force_pile=force_pile,
                    reserve_deck=reserve_deck,
                    current_turn=current_turn,
                    board_state=board_state
                )

                if should_hold:
                    logger.info(f"   🎰 HOLD FOR GROUND DRAW: {hold_reason}")
                    plan.instructions.clear()
                    plan.strategy = DeployStrategy.HOLD_BACK
                    plan.reason = hold_reason
                    board_state.consecutive_hold_turns = getattr(board_state, 'consecutive_hold_turns', 0) + 1
                    plan.phase_started = True
                    self.current_plan = plan
                    return plan

        for loc in contested:
            if not available_chars or force_remaining <= 0:
                break

            # Filter characters by deploy restrictions for THIS location
            location_chars = []
            for char in available_chars:
                restrictions = char.get('deploy_restriction_systems', [])
                if restrictions:
                    # Card has restrictions - check if location matches any allowed system
                    loc_clean = loc.name.lstrip('•').strip()
                    can_deploy = False
                    for system in restrictions:
                        system_lower = system.lower()
                        loc_lower = loc_clean.lower()
                        if loc_lower.startswith(system_lower):
                            can_deploy = True
                            break
                        if ':' in loc_clean:
                            loc_system = loc_clean.split(':')[0].strip().lower()
                            if loc_system == system_lower:
                                can_deploy = True
                                break
                    if not can_deploy:
                        logger.debug(f"   ⛔ {char['name']} cannot deploy to {loc.name} (restricted to {restrictions})")
                        continue  # Skip this character for this location
                location_chars.append(char)

            if not location_chars:
                continue  # No eligible characters for this location

            # Calculate power needed based on location type
            is_weak_presence = loc.their_power == 0 and loc.my_power > 0
            if is_weak_presence:
                # Weak presence: need to reach threshold
                power_needed = ground_threshold - loc.my_power
                if power_needed <= 0:
                    # Already at or above threshold, skip
                    continue
                log_tag = "WEAK"
                logger.info(f"   🔧 {log_tag}: {loc.name} (have {loc.my_power}, need +{power_needed} to reach {ground_threshold})")
            else:
                # Contested: need to beat enemy
                deficit = abs(loc.power_differential)
                power_needed = deficit + get_battle_favorable_threshold()  # Want to reach favorable
                log_tag = "BATTLE OPP" if loc.is_battle_opportunity else "Contested"
                logger.info(f"   ⚔️ {log_tag}: {loc.name} ({loc.my_power} vs {loc.their_power}, need +{power_needed})")

                # === UNREACHABLE TARGET CHECK ===
                # Skip STOP_BLEEDING locations where we can't possibly win
                # Allow +4 margin for destiny draws, but if we need +15 and only have 8 power, skip
                # EXCEPTION: On early turns (1-2), still try to establish presence even at hard locations
                # This prevents giving up strategic locations too easily
                location_char_power = sum(c['power'] for c in location_chars)
                destiny_margin = 4  # Average destiny draw advantage
                current_turn = getattr(board_state, 'current_turn', 1)
                is_early_game_establish = current_turn <= 2 and loc.their_power > 0

                if loc.my_power == 0 and power_needed > location_char_power + destiny_margin:
                    if is_early_game_establish:
                        # Early game: still try to establish presence, but note the disadvantage
                        logger.info(f"   ⚠️ HARD TARGET: {loc.name} needs +{power_needed} but only {location_char_power} deployable - will try anyway (early game)")
                        # Reduce power_needed to just establish presence, not win immediately
                        power_needed = max(4, location_char_power)  # Deploy what we can
                    else:
                        logger.info(f"   ⏭️ UNREACHABLE: {loc.name} needs +{power_needed} but only {location_char_power} deployable (+{destiny_margin} destiny) - skipping")
                        continue

            # Find OPTIMAL combination of cards within budget
            # For weak presence: just reach threshold efficiently (prefer cheaper)
            # For contested: want to WIN decisively (prefer more power)
            location_budget = force_remaining

            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                location_chars,  # Use filtered chars that can deploy to this location
                location_budget,
                power_needed,
                must_exceed=not is_weak_presence  # Contested: want to beat enemy decisively
            )

            if not cards_for_location:
                logger.info(f"   ⏭️ No affordable cards for {loc.name}")
                continue

            # CRITICAL: For NEW attacks (attackable_ground where my_power=0), verify we can actually WIN
            # The optimization functions may return partial results even when we can't
            # achieve the goal. If we can't beat the enemy, don't waste cards!
            # BUT: For existing presence (my_power > 0), we should reinforce even if risky
            # EXCEPTION: If we MATCH power and can draw destiny (ability >= 4, reserve deck > 0),
            # we have ~50% chance to win - acceptable for high-icon bleeding locations
            is_presence_deploy = False  # Track if this is a desperate presence deploy
            if not is_weak_presence and loc.my_power == 0:
                # This is an ATTACK on enemy-controlled location (STOP_BLEEDING case)
                new_total_power = power_allocated  # No existing power
                if new_total_power < loc.their_power:
                    # We can't beat the enemy - but should we deploy anyway to STOP DRAIN?
                    # When severely bleeding (drain_gap <= -2) AND life is low, presence stops the drain
                    # which may be more valuable than saving characters for a battle we'll lose
                    # Only do desperate presence deploys when life is low (< 25) - otherwise wait for better opportunity
                    if drain_gap <= -2 and loc.my_icons >= 2 and life_force < 25:
                        # Severe drain deficit at high-value location AND low life - deploy anyway!
                        # Even losing a battle saves (icons × turns) life force from drains
                        icons_to_save = loc.my_icons
                        logger.info(f"   🩸 PRESENCE DEPLOY: Can't beat {loc.name}: {new_total_power} vs {loc.their_power}, "
                                   f"but drain_gap={drain_gap}, life={life_force}, saving {icons_to_save} drain/turn - worth it!")
                        is_presence_deploy = True  # Mark as presence deploy
                    else:
                        # Not desperate enough or low-value location - skip
                        logger.info(f"   ⏭️ Can't beat {loc.name}: {new_total_power} vs {loc.their_power}, skipping")
                        continue
                elif new_total_power == loc.their_power:
                    # We can MATCH but not beat - check if we can draw destiny
                    total_ability = sum(c.get('ability', 0) for c in cards_for_location)
                    reserve_deck_cards = getattr(board_state, 'reserve_deck', 0) or 0
                    can_draw_destiny = total_ability >= 4 and reserve_deck_cards > 0

                    if can_draw_destiny:
                        # Can draw destiny - ~50% chance to win, worth it for high-icon locations
                        logger.info(f"   ⚔️ Will TIE at {loc.name}: {new_total_power} vs {loc.their_power}, "
                                   f"but CAN draw destiny (ability={total_ability}) - worth the gamble!")
                    elif loc.my_icons >= 1 and drain_gap < 0:
                        # Can't draw destiny BUT tying STOPS THEIR DRAIN!
                        # At a TIE, NEITHER side controls → NEITHER side drains
                        # This is VALUABLE when we're being drained (drain_gap < 0)
                        icons_to_save = loc.my_icons
                        logger.info(f"   🩸 TIE TO STOP DRAIN at {loc.name}: {new_total_power} vs {loc.their_power}, "
                                   f"can't win but TIE stops their {icons_to_save} drain/turn (drain_gap={drain_gap})")
                        is_presence_deploy = True  # Mark as presence deploy since we're not trying to win
                    else:
                        # Not being drained OR no icons to save - skip
                        logger.info(f"   ⏭️ Can only tie {loc.name}: {new_total_power} vs {loc.their_power}, "
                                   f"can't draw destiny (ability={total_ability}), drain_gap={drain_gap}, skipping")
                        continue

            # Log the selected combination
            card_names = [c['name'] for c in cards_for_location]
            logger.info(f"   📊 Optimal combo: {card_names} = {power_allocated} power for {cost_used} Force")

            # Update remaining budget and available cards
            force_remaining -= cost_used
            for card in cards_for_location:
                if card in available_chars:
                    available_chars.remove(card)

            # Create instructions for these deployments
            for char in cards_for_location:
                if is_presence_deploy:
                    reason = f"PRESENCE at {loc.name} to STOP {loc.my_icons} drain/turn (desperate: drain_gap={drain_gap})"
                    priority = 1
                elif loc.is_battle_opportunity:
                    reason = f"BATTLE OPP at {loc.name} (deploy +{char['power']}, then fight!)"
                    priority = 1  # Same as reinforce but flagged for battle
                elif is_weak_presence:
                    reason = f"Reinforce {loc.name} (had {loc.my_power}, adding {char['power']} to reach threshold)"
                    priority = 1  # Same priority as contested reinforce
                else:
                    deficit = abs(loc.power_differential)
                    reason = f"Reinforce {loc.name} (was -{deficit}, adding {char['power']})"
                    priority = 1

                plan.instructions.append(DeploymentInstruction(
                    card_blueprint_id=char['blueprint_id'],
                    card_name=char['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=priority,
                    reason=reason,
                    power_contribution=char['power'],
                    deploy_cost=char['cost'],
                ))
                emoji = "🔧" if is_weak_presence else ("⚔️" if loc.is_battle_opportunity else "🛡️")
                logger.info(f"   {emoji} Plan: Deploy {char['name']} ({char['power']} power, {char['cost']} cost) to {loc.name}")

        # =================================================================
        # STEP 4B: ALLOCATE STARSHIPS TO CONTESTED/WEAK SPACE LOCATIONS
        # Same as STEP 4 but for starships to space locations
        # =================================================================
        available_ships = starships.copy()

        # =================================================================
        # PRE-CHECK: HOLD FOR DRAW IF WE CAN'T CONTEST SPACE BLEEDING
        # Same logic as ground - if we're bleeding in space and can't contest,
        # consider holding to draw ships instead of deploying ground sub-optimally.
        # =================================================================
        if attackable_space and drain_gap < 0:
            # Check if we can meaningfully contest ANY space bleeding location
            can_contest_any_space = False
            total_ship_power = sum(s['power'] for s in available_ships)
            destiny_margin = 4

            for loc in attackable_space:
                if total_ship_power + destiny_margin >= loc.their_power:
                    can_contest_any_space = True
                    break

            if not can_contest_any_space and attackable_space:
                # We're bleeding in space and can't contest
                force_pile = getattr(board_state, 'force_pile', 0) or 0
                reserve_deck = getattr(board_state, 'reserve_deck', 0) or 0

                should_hold, hold_reason, expected_value = self._evaluate_hold_for_bleeding(
                    domain="space",
                    bleeding_locations=attackable_space,
                    deployable_power=total_ship_power,
                    force_pile=force_pile,
                    reserve_deck=reserve_deck,
                    current_turn=current_turn,
                    board_state=board_state
                )

                if should_hold:
                    logger.info(f"   🎰 HOLD FOR SPACE DRAW: {hold_reason}")
                    plan.instructions.clear()
                    plan.strategy = DeployStrategy.HOLD_BACK
                    plan.reason = hold_reason
                    board_state.consecutive_hold_turns = getattr(board_state, 'consecutive_hold_turns', 0) + 1
                    plan.phase_started = True
                    self.current_plan = plan
                    return plan

        for loc in contested_space:
            if not available_ships or force_remaining <= 0:
                break

            # Calculate power needed based on location type
            is_weak_presence = loc.their_power == 0 and loc.my_power > 0
            if is_weak_presence:
                # Weak presence: need to reach threshold
                power_needed = space_threshold - loc.my_power
                if power_needed <= 0:
                    # Already at or above threshold, skip
                    continue
                log_tag = "WEAK SPACE"
                logger.info(f"   🔧 {log_tag}: {loc.name} (have {loc.my_power}, need +{power_needed} to reach {space_threshold})")
            else:
                # Contested: need to beat enemy
                deficit = abs(loc.power_differential)
                power_needed = deficit + get_battle_favorable_threshold()  # Want to reach favorable
                log_tag = "BATTLE OPP" if loc.is_battle_opportunity else "Contested"
                logger.info(f"   🚀 {log_tag}: {loc.name} ({loc.my_power} vs {loc.their_power}, need +{power_needed})")

                # === UNREACHABLE TARGET CHECK (SPACE) ===
                # Skip STOP_BLEEDING locations where we can't possibly win
                available_ship_power = sum(s['power'] for s in available_ships)
                destiny_margin = 4  # Average destiny draw advantage
                if loc.my_power == 0 and power_needed > available_ship_power + destiny_margin:
                    logger.info(f"   ⏭️ UNREACHABLE SPACE: {loc.name} needs +{power_needed} but only {available_ship_power} deployable (+{destiny_margin} destiny) - skipping")
                    continue

            # Find OPTIMAL combination of starships within budget
            # For weak presence: just reach threshold efficiently (prefer cheaper)
            # For contested: want to WIN decisively (prefer more power)
            location_budget = force_remaining

            ships_for_location, power_allocated, cost_used = self._find_optimal_combination(
                available_ships,
                location_budget,
                power_needed,
                must_exceed=not is_weak_presence  # Contested: want to beat enemy decisively
            )

            if not ships_for_location:
                logger.info(f"   ⏭️ No affordable starships for {loc.name}")
                continue

            # CRITICAL: For NEW attacks (attackable_space where my_power=0), verify we can actually WIN
            # Same logic as STEP 4 for characters
            # EXCEPTION: If we MATCH power and can draw destiny (ability >= 4, reserve deck > 0),
            # we have ~50% chance to win - acceptable for high-icon bleeding locations
            is_presence_deploy = False  # Track if this is a desperate presence deploy
            if not is_weak_presence and loc.my_power == 0:
                new_total_power = power_allocated  # No existing power
                if new_total_power < loc.their_power:
                    # We can't beat the enemy - but should we deploy anyway to STOP DRAIN?
                    # Same logic as ground: severe drain deficit at high-value location AND low life
                    if drain_gap <= -2 and loc.my_icons >= 2 and life_force < 25:
                        # Severe drain deficit at high-value location AND low life - deploy anyway!
                        icons_to_save = loc.my_icons
                        logger.info(f"   🩸 PRESENCE DEPLOY: Can't beat {loc.name}: {new_total_power} vs {loc.their_power}, "
                                   f"but drain_gap={drain_gap}, life={life_force}, saving {icons_to_save} drain/turn - worth it!")
                        is_presence_deploy = True  # Mark as presence deploy
                    else:
                        # Not desperate enough or low-value location - skip
                        logger.info(f"   ⏭️ Can't beat {loc.name}: {new_total_power} vs {loc.their_power}, skipping")
                        continue
                elif new_total_power == loc.their_power:
                    # We can MATCH but not beat - check if we can draw destiny
                    # For starships, ability comes from permanent pilots
                    total_ability = sum(s.get('ability', 0) for s in ships_for_location)
                    reserve_deck_cards = getattr(board_state, 'reserve_deck', 0) or 0
                    can_draw_destiny = total_ability >= 4 and reserve_deck_cards > 0

                    if can_draw_destiny:
                        # Can draw destiny - ~50% chance to win, worth it for high-icon locations
                        logger.info(f"   ⚔️ Will TIE at {loc.name}: {new_total_power} vs {loc.their_power}, "
                                   f"but CAN draw destiny (ability={total_ability}) - worth the gamble!")
                    elif loc.my_icons >= 1 and drain_gap < 0:
                        # Can't draw destiny BUT tying STOPS THEIR DRAIN!
                        # At a TIE, NEITHER side controls → NEITHER side drains
                        icons_to_save = loc.my_icons
                        logger.info(f"   🩸 TIE TO STOP DRAIN at {loc.name}: {new_total_power} vs {loc.their_power}, "
                                   f"can't win but TIE stops their {icons_to_save} drain/turn (drain_gap={drain_gap})")
                        is_presence_deploy = True
                    else:
                        # Not being drained OR no icons to save - skip
                        logger.info(f"   ⏭️ Can only tie {loc.name}: {new_total_power} vs {loc.their_power}, "
                                   f"can't draw destiny (ability={total_ability}), drain_gap={drain_gap}, skipping")
                        continue

            # Log the selected combination
            ship_names = [s['name'] for s in ships_for_location]
            logger.info(f"   📊 Optimal starship combo: {ship_names} = {power_allocated} power for {cost_used} Force")

            # Update remaining budget and available ships
            force_remaining -= cost_used
            for ship in ships_for_location:
                if ship in available_ships:
                    available_ships.remove(ship)

            # Create instructions for these deployments
            for ship in ships_for_location:
                if is_presence_deploy:
                    reason = f"PRESENCE at {loc.name} to STOP {loc.my_icons} drain/turn (desperate: drain_gap={drain_gap})"
                    priority = 1
                elif is_weak_presence:
                    reason = f"Reinforce {loc.name} (had {loc.my_power}, adding {ship['power']} to reach threshold)"
                    priority = 1  # Same priority as contested reinforce
                else:
                    deficit = abs(loc.power_differential)
                    reason = f"Reinforce {loc.name} (was -{deficit}, adding {ship['power']})"
                    priority = 1

                plan.instructions.append(DeploymentInstruction(
                    card_blueprint_id=ship['blueprint_id'],
                    card_name=ship['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=priority,
                    reason=reason,
                    power_contribution=ship['power'],
                    deploy_cost=ship['cost'],
                ))
                emoji = "🔧" if is_weak_presence else "🚀"
                logger.info(f"   {emoji} Plan: Deploy {ship['name']} ({ship['power']} power, {ship['cost']} cost) to {loc.name}")

        # =================================================================
        # STEP 5: COMPARE GROUND vs SPACE PLANS
        # Generate each plan INDEPENDENTLY with full budget, then pick the best
        # (Uses dynamic thresholds: ground_threshold, space_threshold)
        # =================================================================

        # Prepare vehicle variables for later use in STEP 5C
        available_vehicles = vehicles.copy()
        unpiloted_vehicles = [v for v in available_vehicles if v.get('needs_pilot')]

        # Ground targets for characters
        # CRITICAL: Must have our icons to deploy (or presence, but we filter my_power==0)
        # EXCLUDE: Dagobah and Ahch-To (special deployment restrictions - most cards can't deploy)
        # EXCLUDE: Interior Naboo sites if "We Have A Plan" objective is active (front side)
        #
        # TWO types of valuable locations:
        # 1. OFFENSIVE: Opponent has icons/presence (we can drain them or beat them)
        # 2. DEFENSIVE: WE have high icons (opponent could drain us if they establish first!)
        char_ground_targets = [
            loc for loc in locations
            if loc.is_ground
            and loc.is_site  # CRITICAL: Characters can only deploy to sites, not systems
            and (loc.their_power > 0 or loc.their_icons > 0 or loc.my_icons >= 2)  # Offensive OR defensive value
            and loc.my_power == 0  # We don't have presence yet
            and loc.my_icons > 0  # MUST have our force icons to deploy there
            and not is_restricted_deployment_location(loc.name)  # Skip Dagobah/Ahch-To
            and not (whap_restriction and is_interior_naboo_site(loc.name, loc.is_interior))  # Skip interior Naboo if WHAP active
        ]
        # CRITICAL: Sort to prioritize:
        # 1. Contested locations FIRST (their_power > 0) - immediate threats
        # 2. Then by COMBINED icon value (offensive + defensive)
        # 3. Defensive locations with 2+ of our icons are HIGH priority even with 0 opponent icons
        # Sort key: (is_contested DESC, combined_icon_value DESC)
        char_ground_targets.sort(
            key=lambda x: (
                x.their_power > 0,                    # Contested first
                x.their_icons + (x.my_icons * 1.5),   # Combined value (our icons weighted higher for defense)
            ),
            reverse=True
        )

        # IMPORTANT: Don't discard uncontested locations entirely!
        # If we have contested locations that need high power, we may not be able to beat them
        # but we should still be able to establish at uncontested locations.
        # Keep at least 1 uncontested location if available
        contested_targets = [loc for loc in char_ground_targets if loc.their_power > 0]
        uncontested_targets = [loc for loc in char_ground_targets if loc.their_power == 0]

        # Take up to 2 contested (high priority) and at least 1 uncontested (fallback)
        char_ground_targets = contested_targets[:2] + uncontested_targets[:2]

        # Add newly deployed ground locations as targets (uncontested, enemy icons)
        for new_loc in newly_deployed_locations:
            if new_loc.is_ground and new_loc.is_site and new_loc.their_icons > 0:
                char_ground_targets.append(new_loc)
                logger.info(f"   📍 Added newly deployed location as target: {new_loc.name}")

        # GAMEPLAN INTEGRATION: Ensure priority locations from goals are included
        # This connects GamePlan strategic goals to target selection
        priority_location_ids = []
        if board_state and hasattr(board_state, 'game_plan'):
            game_plan = board_state.game_plan
            if game_plan and getattr(game_plan, 'enabled', False):
                if hasattr(game_plan, 'get_priority_location_ids'):
                    priority_location_ids = game_plan.get_priority_location_ids()
                    if priority_location_ids:
                        logger.info(f"   🎯 GamePlan priority locations: {priority_location_ids}")

        # Add priority locations that aren't already in targets
        existing_target_ids = {loc.card_id for loc in char_ground_targets}
        for loc in locations:
            if loc.card_id in priority_location_ids and loc.card_id not in existing_target_ids:
                if loc.is_ground and loc.is_site and loc.my_icons > 0:
                    char_ground_targets.insert(0, loc)  # High priority - add at front
                    logger.info(f"   🎯 Added GamePlan priority target: {loc.name}")

        # Log why locations were excluded
        excluded_space = [loc.name for loc in locations if loc.is_space and not loc.is_ground]
        if excluded_space:
            logger.info(f"   ⏭️ Space locations (chars can't go): {excluded_space}")

        # Log systems excluded (ground but not site - would cause deployment failures)
        excluded_systems = [loc.name for loc in locations if loc.is_ground and not loc.is_site]
        if excluded_systems:
            logger.info(f"   ⏭️ System locations (chars need sites): {excluded_systems}")

        if char_ground_targets:
            # Log with both offensive (their_icons) and defensive (my_icons) values
            logger.info(f"   🎯 Ground targets: {[(loc.name, f'our:{loc.my_icons}', f'their:{loc.their_icons}', f'pwr:{loc.their_power}') for loc in char_ground_targets]}")

        # Include contested/crushable ground locations for character reinforcement
        # These have our presence AND enemy presence - we can deploy to CRUSH them!
        # CRITICAL: This includes locations where we're WINNING but can crush further
        # EXCEPT: Don't add if we're already winning by get_deploy_overkill_threshold() (overkill)
        crushable_ground = [
            loc for loc in locations
            if loc.my_power > 0  # We have presence
            and loc.their_power > 0  # Enemy has presence (contested)
            and loc.is_ground  # Ground location
            and loc.is_site  # Must be a site, not a system (characters can't deploy to systems)
            and not loc.should_flee  # Don't reinforce if fleeing
            and (loc.my_power - loc.their_power) < get_deploy_overkill_threshold()  # Not overkill
            and not is_restricted_deployment_location(loc.name)  # Skip Dagobah/Ahch-To
        ]
        for loc in crushable_ground:
            if loc not in char_ground_targets:
                char_ground_targets.insert(0, loc)  # Contested locations first (higher priority)
                logger.info(f"   ⚔️ Crushable ground: {loc.name} ({loc.my_power} vs {loc.their_power})")

        # Include FRIENDLY uncontested ground locations for reinforcement
        # These have our presence but NO enemy presence - good for:
        # 1. Building up forces before attacking adjacent locations
        # 2. Deploying vehicles that can then have weapons attached (e.g., AT-AT + AT-AT Cannon)
        # Lower priority than contested/crushable, but still valid deployment targets
        # IMPORTANT: Require my_icons > 0 so this is "our" location, not just presence-based
        # IMPORTANT: Don't reinforce if already well-fortified (10+ power) - save cards for future!
        fortified_threshold = get_uncontested_fortified_threshold()
        reinforceable_ground = [
            loc for loc in locations
            if loc.my_power > 0  # We have presence
            and loc.their_power == 0  # Enemy has NO presence (uncontested by us)
            and loc.is_ground  # Ground location
            and loc.is_site  # Must be a site, not a system (characters can't deploy to systems)
            and loc.their_icons > 0  # Has enemy icons (strategically valuable)
            and loc.my_icons > 0  # Has our icons (it's "our" location, not just presence)
            and loc.my_power < fortified_threshold  # Not already fortified
            and not is_restricted_deployment_location(loc.name)  # Skip Dagobah/Ahch-To
        ]
        for loc in reinforceable_ground:
            if loc not in char_ground_targets:
                char_ground_targets.append(loc)  # Lower priority - append to end
                logger.info(f"   🏰 Reinforceable friendly: {loc.name} (my power: {loc.my_power}, enemy icons: {loc.their_icons})")

        # Include contested/crushable space locations for starship reinforcement
        # These have our presence (can deploy via presence rule even without icons)
        # CRITICAL: This includes locations where we're WINNING but can crush further
        # EXCEPT: Don't add if we're already winning by get_deploy_overkill_threshold() (overkill)
        crushable_space = [
            loc for loc in locations
            if loc.my_power > 0  # We have presence
            and loc.their_power > 0  # Enemy has presence (contested)
            and loc.is_space  # Space location
            and not loc.should_flee  # Don't reinforce if fleeing
            and (loc.my_power - loc.their_power) < get_deploy_overkill_threshold()  # Not overkill
            and not is_restricted_deployment_location(loc.name)  # Skip Dagobah/Ahch-To
        ]
        space_targets = uncontested_space.copy()

        # Add attackable space locations (enemy has ships, we can deploy and win)
        # These are HIGH PRIORITY - battle opportunities!
        for loc in attackable_space:
            if loc not in space_targets:
                space_targets.insert(0, loc)  # Attackable locations first (battle opportunity!)
                logger.info(f"   ⚔️ Attackable space: {loc.name} (enemy has {loc.their_power} power)")

        # Add crushable space locations (we're there, enemy is there, not overkill)
        for loc in crushable_space:
            if loc not in space_targets:
                space_targets.insert(0, loc)  # Contested locations also high priority
                logger.info(f"   ⚔️ Crushable space: {loc.name} ({loc.my_power} vs {loc.their_power})")

        # Add reinforceable space locations (we control, no enemy, could add more ships)
        # This allows deploying piloted ships to locations we already control but aren't at overkill
        # CRITICAL: Without this, piloted ships in hand won't deploy if all space is "controlled"
        reinforceable_space = [
            loc for loc in locations
            if loc.my_power > 0  # We have presence
            and loc.their_power == 0  # Enemy has NO presence (uncontested by us)
            and loc.is_space  # Space location
            and loc.their_icons > 0  # Has enemy icons (strategically valuable - they could deploy)
            and loc.my_icons > 0  # Has our icons (it's "our" location)
            and loc.my_power < fortified_threshold  # Not already fortified (10+ power)
            and not is_restricted_deployment_location(loc.name)  # Skip Dagobah/Ahch-To
        ]
        for loc in reinforceable_space:
            if loc not in space_targets:
                space_targets.append(loc)  # Lower priority - append to end
                logger.info(f"   🏰 Reinforceable space: {loc.name} (my power: {loc.my_power}, enemy icons: {loc.their_icons})")

        # Add newly deployed space locations as targets (uncontested, enemy icons)
        for new_loc in newly_deployed_locations:
            if new_loc.is_space and new_loc.their_icons > 0:
                space_targets.append(new_loc)
                logger.info(f"   📍 Added newly deployed space location as target: {new_loc.name}")

        if space_targets:
            logger.info(f"   🚀 Space targets: {[(loc.name, loc.their_icons, loc.their_power) for loc in space_targets]}")

        # Identify ALL pilots (any character with pilot ability can fly ships)
        # Pure pilots (pilot but not warrior) are BEST aboard ships but all can pilot
        # CRITICAL: Use original `characters` list, NOT `available_chars` which may have been
        # depleted by earlier steps. Step 5 generates INDEPENDENT plans for comparison.
        all_pilots = [c for c in characters if c.get('is_pilot')]
        pure_pilots = [c for c in characters if c.get('is_pure_pilot')]
        if all_pilots:
            pilot_names = [p['name'] for p in all_pilots]
            pure_names = [p['name'] for p in pure_pilots]
            logger.info(f"   👨‍✈️ All pilots available: {pilot_names}")
            if pure_pilots:
                logger.info(f"   👨‍✈️ Pure pilots (best for ships): {pure_names}")

        # =================================================================
        # STEP 5-PRE: CHECK FOR UNPILOTED SHIPS IN PLAY
        # If we have unpiloted ships on the board (pilot was forfeited),
        # generate RE-PILOT plans to reclaim them. These are HIGH VALUE
        # because we save the cost of deploying a new ship!
        # =================================================================
        unpiloted_ships = self._find_unpiloted_ships_in_play(board_state)
        all_repilot_plans = []

        if unpiloted_ships and all_pilots:
            logger.info(f"   🚀 Found {len(unpiloted_ships)} UNPILOTED ships - generating re-pilot plans")

            for ship in unpiloted_ships:
                # Find affordable pilots for this ship
                affordable_pilots_for_ship = [
                    p for p in all_pilots if p['cost'] <= force_remaining
                ]

                if not affordable_pilots_for_ship:
                    logger.info(f"      ⏭️ No affordable pilots for {ship['name']}")
                    continue

                # Score each pilot for this ship and pick the best
                pilot_scores = []
                for pilot in affordable_pilots_for_ship:
                    score = _pilot_score_for_ship(pilot, ship)
                    pilot_scores.append((pilot, score))

                # Sort by score descending
                pilot_scores.sort(key=lambda x: x[1], reverse=True)
                best_pilot, pilot_score = pilot_scores[0]

                # Calculate total power when piloted
                # Ship's base_power + pilot's power (from ability score typically)
                total_power = ship['base_power'] + best_pilot.get('power', 0)

                # Generate re-pilot plan
                # Target is the SHIP card_id, not a location!
                # Use a special target format: "aboard:card_id:ship_name"
                target_aboard = f"aboard:{ship['card_id']}:{ship['name']}"

                instruction = DeploymentInstruction(
                    card_blueprint_id=best_pilot['blueprint_id'],
                    card_name=best_pilot['name'],
                    target_location_id=ship['card_id'],  # The ship's card_id
                    target_location_name=target_aboard,  # Special marker for aboard
                    priority=1,  # High priority
                    reason=f"RE-PILOT {ship['name']} (reclaim ship, {total_power} power!)",
                    power_contribution=total_power,
                    deploy_cost=best_pilot['cost'],
                )

                # Score: HIGH value because we're reclaiming an existing asset
                # - Ship's base power value (we get this for free!)
                # - Pilot power contribution
                # - Bonus for matching pilot/ship
                # - Big bonus for not having to pay for a new ship
                reclaim_bonus = 60  # Saving a ship deployment is valuable!
                base_score = ship['base_power'] * 10 + best_pilot.get('power', 0) * 5 + reclaim_bonus + pilot_score

                force_left_after = force_remaining - best_pilot['cost']

                all_repilot_plans.append(([instruction], force_left_after, base_score))
                logger.info(f"      🔧 RE-PILOT plan: {best_pilot['name']} → {ship['name']} "
                           f"(score={base_score:.0f}, cost={best_pilot['cost']}, power={total_power})")

        # =================================================================
        # STEP 5-PRE-B: CHECK FOR SHIPS WITH REMAINING PILOT CAPACITY
        # If we have piloted ships that can accept MORE pilots ("May add X pilots"),
        # generate ADD-PILOT plans. This is a key reinforcement strategy -
        # adding a pilot to an existing ship boosts its power significantly.
        # =================================================================
        ships_with_capacity = self._find_ships_with_pilot_capacity(board_state)
        all_addpilot_plans = []

        if ships_with_capacity and all_pilots:
            logger.info(f"   🛳️ Found {len(ships_with_capacity)} ships with pilot capacity - generating add-pilot plans")

            for ship in ships_with_capacity:
                # Find affordable pilots for this ship
                affordable_pilots_for_ship = [
                    p for p in all_pilots if p['cost'] <= force_remaining
                ]

                if not affordable_pilots_for_ship:
                    logger.info(f"      ⏭️ No affordable pilots for {ship['name']}")
                    continue

                # Score each pilot for this ship and pick the best
                pilot_scores = []
                for pilot in affordable_pilots_for_ship:
                    score = _pilot_score_for_ship(pilot, ship)
                    # Also include pilot_adds_power for ranking
                    pilot_card = get_card(pilot.get('blueprint_id', ''))
                    adds_power = pilot_card.pilot_adds_power if pilot_card else 1
                    score += adds_power * 5  # Weight power contribution
                    pilot_scores.append((pilot, score, adds_power))

                # Sort by score descending
                pilot_scores.sort(key=lambda x: x[1], reverse=True)
                best_pilot, pilot_score, adds_power = pilot_scores[0]

                # Calculate power boost from adding this pilot
                # Use pilot_adds_power if available (from gametext like "Adds 3 to power")
                power_boost = adds_power

                # Generate add-pilot plan
                # Target is the SHIP card_id, not a location
                target_aboard = f"aboard:{ship['card_id']}:{ship['name']}"

                instruction = DeploymentInstruction(
                    card_blueprint_id=best_pilot['blueprint_id'],
                    card_name=best_pilot['name'],
                    target_location_id=ship['card_id'],  # The ship's card_id
                    target_location_name=target_aboard,  # Special marker for aboard
                    priority=2,  # Medium priority
                    reason=f"ADD-PILOT to {ship['name']} (+{power_boost} power)",
                    power_contribution=power_boost,
                    deploy_cost=best_pilot['cost'],
                )

                # Score: Moderate value - reinforcing existing position
                # - Power boost * 10 (main value)
                # - Base bonus for reinforcement (30)
                # - Matching pilot bonus from pilot_score
                # - Extra bonus if at contested location (check locations list)
                reinforce_bonus = 30
                location_bonus = 0

                # Check if ship is at a contested location (higher value)
                for loc in locations:
                    if loc.name == ship.get('location_name'):
                        if loc.their_power > 0:  # Contested!
                            location_bonus = 25  # Extra value at contested location
                            if loc.their_power > loc.my_power:
                                location_bonus = 40  # Even more value if we're losing!
                        break

                base_score = (power_boost * 10 + reinforce_bonus + pilot_score + location_bonus)

                force_left_after = force_remaining - best_pilot['cost']

                all_addpilot_plans.append(([instruction], force_left_after, base_score))
                logger.info(f"      👨‍✈️ ADD-PILOT plan: {best_pilot['name']} → {ship['name']} "
                           f"(score={base_score:.0f}, +{power_boost} power, cost={best_pilot['cost']})")

        # =================================================================
        # Generate MULTIPLE plans for each deployable card and compare
        # This ensures we consider all viable deployment options
        # (Dynamic thresholds already calculated earlier: ground_threshold, space_threshold)
        # =================================================================

        # Generate all ground plans (one per affordable character)
        # CRITICAL: Use fresh copy of `characters`, NOT `available_chars` which was depleted
        # by earlier steps. Step 5 generates INDEPENDENT plans for comparison.
        # Pass ALL pilots (not just pure) for vehicle+pilot combos - any pilot can drive!
        # Log final target list for debugging (includes crushable locations added after initial log)
        logger.info(f"   🎯 Final ground targets for planning: {[loc.name for loc in char_ground_targets]}")
        all_ground_plans = self._generate_all_ground_plans(
            characters.copy(), vehicles.copy(), char_ground_targets, force_remaining, locations, all_pilots,
            ground_threshold=ground_threshold, contest_advantage=contest_advantage
        )

        # Generate all space plans (one per affordable starship)
        # Uses space_targets which includes both uncontested AND contested space locations
        # CRITICAL: Pass ALL pilots (not just pure pilots) - any pilot can fly ships!
        all_space_plans = self._generate_all_space_plans(
            starships.copy(), space_targets, force_remaining, all_pilots, locations,
            space_threshold=space_threshold, contest_advantage=contest_advantage
        )

        # =================================================================
        # STEP 5A: GENERATE "STOP THE BLEEDING" PRESENCE PLANS
        # If drain gap is negative (we're losing the force drain war), generate
        # special plans that establish ANY presence at bleed locations.
        # These have NO threshold - any power >= 1 is enough to stop drains.
        # =================================================================
        all_presence_plans = []
        if drain_gap < 0 and bleed_locations:
            # Only generate presence plans if we're losing the drain economy
            # AND we don't already have normal plans for these locations
            bleed_names = {loc.name for loc in bleed_locations}
            normal_plan_targets = set()
            # Check both ground and space plans for coverage
            for instructions, _, _ in all_ground_plans + all_space_plans:
                for inst in instructions:
                    if inst.target_location_name:
                        normal_plan_targets.add(inst.target_location_name)

            # Filter bleed locations to only those without normal plans
            uncovered_bleed = [loc for loc in bleed_locations if loc.name not in normal_plan_targets]

            if uncovered_bleed:
                logger.info(f"   🩸 BLEED LOCATIONS without normal plans: {[loc.name for loc in uncovered_bleed]}")
                all_presence_plans = self._generate_presence_only_plans(
                    characters.copy(), vehicles.copy(), uncovered_bleed, force_remaining, locations,
                    pilots=all_pilots, starships=starships.copy()
                )

        # Log all plans for debugging
        repilot_count = len(all_repilot_plans)
        addpilot_count = len(all_addpilot_plans)
        presence_count = len(all_presence_plans)
        if repilot_count > 0 or addpilot_count > 0 or presence_count > 0:
            logger.info(f"   📊 Generated {len(all_ground_plans)} ground, {len(all_space_plans)} space, "
                       f"{repilot_count} RE-PILOT, {addpilot_count} ADD-PILOT, {presence_count} PRESENCE plans")
        else:
            logger.info(f"   📊 Generated {len(all_ground_plans)} ground plans, {len(all_space_plans)} space plans")

        for i, (instructions, force_left, score) in enumerate(all_ground_plans):
            cost = force_remaining - force_left
            cards = [inst.card_name for inst in instructions]
            # Show WHERE this plan deploys and WHY (the reason tells us establish/reinforce/crush)
            target = instructions[0].target_location_name if instructions else "?"
            reason_type = "establish"
            if instructions and instructions[0].reason:
                if "Crush" in instructions[0].reason:
                    reason_type = "CRUSH"
                elif "Reinforce" in instructions[0].reason:
                    reason_type = "reinforce"
            logger.info(f"      GROUND {i+1}: {cards} → {target} ({reason_type}) score={score:.0f}, cost={cost}")

        for i, (instructions, force_left, score) in enumerate(all_space_plans):
            cost = force_remaining - force_left
            cards = [inst.card_name for inst in instructions]
            target = instructions[0].target_location_name if instructions else "?"
            reason_type = "establish"
            if instructions and instructions[0].reason:
                if "Crush" in instructions[0].reason:
                    reason_type = "CRUSH"
                elif "Contest" in instructions[0].reason:
                    reason_type = "CONTEST"
                elif "Reinforce" in instructions[0].reason:
                    reason_type = "reinforce"
            logger.info(f"      SPACE {i+1}: {cards} → {target} ({reason_type}) score={score:.0f}, cost={cost}")

        for i, (instructions, force_left, score) in enumerate(all_repilot_plans):
            cost = force_remaining - force_left
            cards = [inst.card_name for inst in instructions]
            # For re-pilot, target is "aboard:card_id:ship_name"
            target = instructions[0].target_location_name if instructions else "?"
            if target and target.startswith("aboard:"):
                parts = target.split(":", 2)
                target = parts[2] if len(parts) > 2 else target
            logger.info(f"      RE-PILOT {i+1}: {cards} → aboard {target} score={score:.0f}, cost={cost}")

        for i, (instructions, force_left, score) in enumerate(all_presence_plans):
            cost = force_remaining - force_left
            cards = [inst.card_name for inst in instructions]
            target = instructions[0].target_location_name if instructions else "?"
            logger.info(f"      🩸 PRESENCE {i+1}: {cards} → {target} (STOP BLEEDING) score={score:.0f}, cost={cost}")

        for i, (instructions, force_left, score) in enumerate(all_addpilot_plans):
            cost = force_remaining - force_left
            cards = [inst.card_name for inst in instructions]
            # For add-pilot, target is "aboard:card_id:ship_name"
            target = instructions[0].target_location_name if instructions else "?"
            if target and target.startswith("aboard:"):
                parts = target.split(":", 2)
                target = parts[2] if len(parts) > 2 else target
            logger.info(f"      🛳️ ADD-PILOT {i+1}: {cards} → aboard {target} score={score:.0f}, cost={cost}")

        # =================================================================
        # STEP 5B: GENERATE COMBINED GROUND+SPACE PLANS
        # Instead of just picking best single-domain and using leftover,
        # consider combined plans that allocate force across both domains.
        # This finds globally optimal deployment across all locations.
        # =================================================================

        # Collect all single-domain plans
        all_plans = []
        for instructions, force_left, score in all_ground_plans:
            all_plans.append(('ground', instructions, force_left, score))
        for instructions, force_left, score in all_space_plans:
            all_plans.append(('space', instructions, force_left, score))
        # Add re-pilot plans (reclaiming unpiloted ships already in play)
        for instructions, force_left, score in all_repilot_plans:
            all_plans.append(('repilot', instructions, force_left, score))
        # Add add-pilot plans (adding pilots to ships with capacity for reinforcement)
        for instructions, force_left, score in all_addpilot_plans:
            all_plans.append(('addpilot', instructions, force_left, score))
        # Add "stop the bleeding" presence plans (low threshold, high drain priority)
        for instructions, force_left, score in all_presence_plans:
            all_plans.append(('presence', instructions, force_left, score))

        # Generate COMBINED ground+space plans
        # For each ground plan, check if we can add a compatible space plan
        combined_plans = []
        for g_instructions, g_force_left, g_score in all_ground_plans:
            g_cost = force_remaining - g_force_left
            g_blueprints = {inst.card_blueprint_id for inst in g_instructions}

            # Check if ground plan targets a contested location (needs battle reserve)
            g_contested = any(
                inst.target_location_name and
                any(loc.name == inst.target_location_name and loc.their_power > 0
                    for loc in locations)
                for inst in g_instructions
            )

            for s_instructions, s_force_left, s_score in all_space_plans:
                s_cost = force_remaining - s_force_left
                s_blueprints = {inst.card_blueprint_id for inst in s_instructions}

                # Skip if cards overlap (can't deploy same card twice)
                if g_blueprints & s_blueprints:
                    continue

                # Check if space plan targets a contested location
                s_contested = any(
                    inst.target_location_name and
                    any(loc.name == inst.target_location_name and loc.their_power > 0
                        for loc in locations)
                    for inst in s_instructions
                )

                # Calculate battle reserve needed
                # Need 1 force per contested location we're deploying to
                battle_reserve = 0
                if g_contested:
                    battle_reserve += 1
                if s_contested:
                    battle_reserve += 1

                total_cost = g_cost + s_cost
                # Check if combined plan fits in budget WITH battle reserve
                if total_cost + battle_reserve <= force_remaining:
                    combined_force_left = force_remaining - total_cost
                    # Combined score: sum of both domain scores
                    # Add a small bonus for using force efficiently across domains
                    efficiency_bonus = min(20, (total_cost / max(1, force_remaining)) * 20)
                    combined_score = g_score + s_score + efficiency_bonus

                    combined_instructions = list(g_instructions) + list(s_instructions)
                    combined_plans.append((
                        'combined',
                        combined_instructions,
                        combined_force_left,
                        combined_score
                    ))

        # Log combined plans
        if combined_plans:
            logger.info(f"   🔀 Generated {len(combined_plans)} combined ground+space plans")
            # Show top 3 combined plans
            combined_plans.sort(key=lambda x: x[3], reverse=True)
            for i, (_, instructions, force_left, score) in enumerate(combined_plans[:3]):
                cost = force_remaining - force_left
                cards = [inst.card_name for inst in instructions]
                logger.info(f"      COMBINED {i+1}: {cards} -> score={score:.0f}, cost={cost}")

        # =================================================================
        # STEP 5B-2: COMBINE SAME-DOMAIN PLANS FOR MULTI-LOCATION ESTABLISHMENT
        # If we have multiple ground plans or multiple space plans that use
        # different cards and fit in budget, combine them for multi-location control.
        # =================================================================
        multi_location_plans = []

        # Combine ground + ground (multi-location ground establishment)
        if len(all_ground_plans) >= 2:
            for i, (g1_inst, g1_force_left, g1_score) in enumerate(all_ground_plans):
                g1_cost = force_remaining - g1_force_left
                g1_blueprints = {inst.card_blueprint_id for inst in g1_inst}
                g1_locations = {inst.target_location_name for inst in g1_inst}

                for j, (g2_inst, g2_force_left, g2_score) in enumerate(all_ground_plans):
                    if j <= i:  # Avoid duplicates and self-combination
                        continue
                    g2_blueprints = {inst.card_blueprint_id for inst in g2_inst}
                    g2_locations = {inst.target_location_name for inst in g2_inst}

                    # Skip if cards overlap or same location
                    if g1_blueprints & g2_blueprints:
                        continue
                    if g1_locations & g2_locations:
                        continue

                    g2_cost = force_remaining - g2_force_left
                    total_cost = g1_cost + g2_cost

                    if total_cost <= force_remaining:
                        combined_force_left = force_remaining - total_cost

                        # Check if either plan involves CRUSHING an enemy
                        # If so, reduce multi-location bonus - should commit to the crush!
                        g1_is_crush = any('Crush' in (inst.reason or '') for inst in g1_inst)
                        g2_is_crush = any('Crush' in (inst.reason or '') for inst in g2_inst)

                        if g1_is_crush or g2_is_crush:
                            # One plan is a CRUSH - prefer committing to battle over spreading
                            # Give NO bonus for splitting forces away from a crush opportunity
                            multi_loc_bonus = 0
                            logger.debug(f"   ⚔️ Multi-ground: No bonus (one plan is CRUSH)")
                        else:
                            # Neither is a crush - normal bonus for spreading control
                            multi_loc_bonus = 30

                        combined_score = g1_score + g2_score + multi_loc_bonus

                        combined_instructions = list(g1_inst) + list(g2_inst)
                        multi_location_plans.append((
                            'multi_ground',
                            combined_instructions,
                            combined_force_left,
                            combined_score
                        ))

        # Combine space + space (multi-location space establishment)
        if len(all_space_plans) >= 2:
            for i, (s1_inst, s1_force_left, s1_score) in enumerate(all_space_plans):
                s1_cost = force_remaining - s1_force_left
                s1_blueprints = {inst.card_blueprint_id for inst in s1_inst}
                s1_locations = {inst.target_location_name for inst in s1_inst}

                for j, (s2_inst, s2_force_left, s2_score) in enumerate(all_space_plans):
                    if j <= i:
                        continue
                    s2_blueprints = {inst.card_blueprint_id for inst in s2_inst}
                    s2_locations = {inst.target_location_name for inst in s2_inst}

                    if s1_blueprints & s2_blueprints:
                        continue
                    if s1_locations & s2_locations:
                        continue

                    s2_cost = force_remaining - s2_force_left
                    total_cost = s1_cost + s2_cost

                    if total_cost <= force_remaining:
                        combined_force_left = force_remaining - total_cost

                        # Check if either plan involves CRUSHING an enemy
                        s1_is_crush = any('Crush' in (inst.reason or '') for inst in s1_inst)
                        s2_is_crush = any('Crush' in (inst.reason or '') for inst in s2_inst)

                        if s1_is_crush or s2_is_crush:
                            # One plan is a CRUSH - prefer committing to battle
                            multi_loc_bonus = 0
                            logger.debug(f"   ⚔️ Multi-space: No bonus (one plan is CRUSH)")
                        else:
                            multi_loc_bonus = 30

                        combined_score = s1_score + s2_score + multi_loc_bonus

                        combined_instructions = list(s1_inst) + list(s2_inst)
                        multi_location_plans.append((
                            'multi_space',
                            combined_instructions,
                            combined_force_left,
                            combined_score
                        ))

        if multi_location_plans:
            logger.info(f"   🌍 Generated {len(multi_location_plans)} multi-location same-domain plans")

        # Add combined plans to all_plans
        all_plans.extend(combined_plans)
        all_plans.extend(multi_location_plans)

        if all_plans:
            # =================================================================
            # DYNAMIC RESERVE: Filter and score plans based on actual reserve needed
            # Each plan may need different reserve:
            # - Base reserve (1) for effects/reactions
            # - +1 if deploying to contested location (battle initiation)
            # =================================================================
            valid_plans = []
            for plan_type, instructions, force_left, score in all_plans:
                # Calculate this plan's required reserve
                plan_reserve = self._calculate_plan_reserve(instructions, locations)
                plan_cost = force_to_spend - force_left

                # Check if plan fits with its required reserve
                # Plan is valid if: plan_cost + plan_reserve <= total_force
                if plan_cost + plan_reserve <= total_force:
                    # Adjust force_left to account for actual reserve
                    actual_force_left = total_force - plan_cost - plan_reserve
                    valid_plans.append((plan_type, instructions, actual_force_left, score, plan_reserve))
                else:
                    logger.debug(f"   ⏭️ Plan {plan_type} rejected: cost {plan_cost} + reserve {plan_reserve} > {total_force}")

            if not valid_plans:
                logger.info(f"   ⏭️ All {len(all_plans)} plans rejected due to reserve requirements")
                all_plans = []  # Clear to trigger "No valid plans" path
            else:
                # Sort by score descending, pick the best
                valid_plans.sort(key=lambda x: x[3], reverse=True)

                # =================================================================
                # MONTE CARLO SIMULATION (optional)
                # If enabled, stress-test top N plans against adversarial scenarios
                # and re-rank by expected value after opponent counterplay.
                # =================================================================
                if self.monte_carlo_enabled and self.monte_carlo and len(valid_plans) > 1:
                    valid_plans = self._apply_monte_carlo_simulation(
                        valid_plans, locations, all_cards, board_state
                    )

                best_type, best_instructions, best_force_left, best_score, best_reserve = valid_plans[0]

                # === LOG ALL CANDIDATE PLANS FOR ANALYSIS ===
                if len(valid_plans) > 1:
                    logger.info(f"   📊 PLAN COMPARISON ({len(valid_plans)} candidates):")
                    for i, (ptype, pinst, pforce, pscore, preserve) in enumerate(valid_plans):
                        marker = "→" if i == 0 else " "
                        card_summary = ", ".join(inst.card_name for inst in pinst[:3])
                        if len(pinst) > 3:
                            card_summary += f", +{len(pinst)-3} more"
                        # Show destination and strategy
                        target = pinst[0].target_location_name if pinst else "?"
                        reason_type = "establish"
                        if pinst and pinst[0].reason:
                            if "Crush" in pinst[0].reason:
                                reason_type = "CRUSH"
                            elif "Reinforce" in pinst[0].reason:
                                reason_type = "reinforce"
                        logger.info(f"      {marker} {ptype.upper()}: {card_summary} → {target} ({reason_type}) score={pscore:.0f}")

                logger.info(f"   ✅ CHOSE {best_type.upper()} PLAN (score {best_score:.0f}, reserve {best_reserve})")
                for inst in best_instructions:
                    logger.info(f"      - {inst.card_name} -> {inst.target_location_name} ({inst.power_contribution} power)")

                # =================================================================
                # EARLY GAME HOLD-BACK CHECK
                # In the early game (turns 1-3), hold back weak "establish" plans.
                # This prevents wasting cards on low-value locations when waiting
                # one turn could yield a much better opportunity.
                # =================================================================
                best_is_crush = any('Crush' in (inst.reason or '') for inst in best_instructions)
                best_is_reinforce = any('Reinforce' in (inst.reason or '') for inst in best_instructions)
                best_is_counter = best_is_crush or best_is_reinforce

                # Early game hold-back: in turns 1-3, don't deploy weak establish plans
                # EXCEPT when life is low (need to be aggressive)
                # EXCEPT when GamePlan says we're on WINNING trajectory
                is_early_game = current_turn <= get_early_game_turns()
                is_weak_plan = best_score < get_early_game_threshold()
                is_low_life = life_force < 20  # Critical/desperate life force

                # Check if GamePlan says we're on WINNING trajectory
                # If winning, don't hold back - maintain momentum
                # If NOT winning but GamePlan is active, also don't hold back - need to act!
                has_winning_trajectory = False
                gameplan_active = False
                if board_state and hasattr(board_state, 'game_plan'):
                    game_plan = board_state.game_plan
                    if game_plan and getattr(game_plan, 'enabled', False):
                        gameplan_active = True
                        projections = getattr(game_plan, 'projections', None)
                        if projections and len(projections) > 0:
                            has_winning_trajectory = getattr(projections[0], 'winning', False)
                            if has_winning_trajectory:
                                logger.info(f"🎯 GamePlan: WINNING trajectory - skip early hold-back")
                            else:
                                # Not winning with GamePlan = need to act aggressively
                                logger.info(f"🎯 GamePlan: NOT winning - skip hold-back, deploy to change trajectory")

                # When GamePlan is active, never hold back in early game:
                # - If winning: maintain momentum
                # - If not winning: need to act to change trajectory
                skip_holdback_for_gameplan = gameplan_active

                # =================================================================
                # ABILITY SAFETY CHECK - Cannot be bypassed by GamePlan!
                # Establishing alone with ability < 4 is a TRAP:
                # - Opponent counters with ability 4+ characters
                # - They draw attrition destiny, we don't
                # - We lose character + extra damage from their destiny
                #
                # This check is SEPARATE from general hold-back and applies even
                # when GamePlan wants presence - better to wait for good characters.
                # =================================================================
                is_risky_low_ability_establish = False
                if (plan.instructions and
                    plan.strategy in [DeployStrategy.ESTABLISH, DeployStrategy.OVERWHELM] and
                    is_early_game):
                    # Check if ALL deployments in this plan have ability < 4
                    all_low_ability = True
                    for instr in plan.instructions:
                        # Get the character's ability from our tracking
                        card_name = instr.card_name
                        card_ability = 0
                        for card in self._get_all_hand_cards_as_dicts(board_state):
                            if card.get('title') == card_name or card.get('card_name') == card_name:
                                card_ability = card.get('ability', 0)
                                break
                        if card_ability >= 4:
                            all_low_ability = False
                            break

                    if all_low_ability and not is_low_life:
                        is_risky_low_ability_establish = True
                        logger.info(f"⚠️ ABILITY SAFETY: Plan has only ability < 4 characters - risky establish!")

                if is_risky_low_ability_establish:
                    # Override even GamePlan - don't establish with ability < 4 alone
                    logger.info(f"⛔ ABILITY SAFETY HOLD-BACK (turn {current_turn})")
                    logger.info(f"   Establishing with ability < 4 is a trap - opponent will crush with destiny!")
                    logger.info(f"   Holding for characters with ability >= 4")

                    plan.instructions.clear()
                    plan.strategy = DeployStrategy.HOLD_BACK
                    plan.reason = "Ability safety: only low-ability characters available, holding for better options"
                    plan.phase_started = True
                    self.current_plan = plan
                    return plan

                if (is_early_game and
                    not best_is_counter and
                    is_weak_plan and
                    not is_low_life and
                    not has_winning_trajectory and
                    not skip_holdback_for_gameplan):  # Don't hold back when GamePlan active
                    # Early game + weak plan + not winning + no GamePlan = hold back
                    logger.info(f"⏳ EARLY GAME HOLD-BACK (turn {current_turn})")
                    logger.info(f"   Plan score {best_score:.0f} < threshold {get_early_game_threshold()}")
                    logger.info(f"   Waiting for better deployment opportunity")

                    # Clear the plan and set HOLD_BACK
                    plan.instructions.clear()
                    plan.strategy = DeployStrategy.HOLD_BACK
                    plan.reason = (f"Early game (turn {current_turn}): score {best_score:.0f} below "
                                  f"threshold {get_early_game_threshold()}, holding for better play")

                    # Mark phase as started so this plan gets cached properly
                    plan.phase_started = True
                    self.current_plan = plan
                    return plan

                # =================================================================
                # NEXT-TURN CRUSH CHECK
                # If the best current plan is NOT a CRUSH or REINFORCE (counter),
                # check if waiting one turn would allow a CRUSHING attack.
                # Next-turn crush should beat "establish" plans but not counters.
                # =================================================================

                # Check hold duration limit (used by both crush and bleed stop checks)
                max_consecutive_holds = 2
                already_held_too_long = board_state.consecutive_hold_turns >= max_consecutive_holds

                if not best_is_counter:
                    # Current plan is establish/other - check for next-turn crush opportunity
                    all_hand_cards = self._get_all_hand_cards_as_dicts(board_state)
                    next_turn_crush = self._find_next_turn_crush_opportunities(
                        board_state, locations, all_hand_cards, total_force
                    )

                    if next_turn_crush:
                        # For next-turn crush, we want to favor it over "establish" plans
                        # because crushing is decisive - it removes enemy cards and deals damage.
                        #
                        # Scoring: A +7 crush is much more valuable than establishing at
                        # a 2-icon location because:
                        # 1. Crush deals direct damage (cards lost = life damage)
                        # 2. Removes enemy presence, potentially stopping their drains
                        # 3. Is a forcing play that puts opponent on back foot
                        #
                        # Score next-turn crush more generously:
                        # - Base: advantage * 25 (each point of advantage is very valuable)
                        # - Bonus: +50 for any valid crush (guaranteed damage)
                        # - Icons at target: their_icons * 10
                        target_icons = 0
                        for loc in locations:
                            if loc.name == next_turn_crush.target_location_name:
                                target_icons = loc.their_icons
                                break
                        next_turn_score = (50 +  # Base crush value
                                          next_turn_crush.expected_advantage * 25 +
                                          target_icons * 10)

                        # Prefer next-turn crush if:
                        # 1. Has meaningful advantage (>= get_battle_favorable_threshold())
                        # 2. AND either:
                        #    a. Current plan is just "establish" (not a counter) - always prefer crush
                        #    b. Score comparison favors crush
                        #
                        # For establish plans, we use a lower threshold (0.5) because
                        # establishing only denies icons, while crushing removes cards.
                        #
                        # EXCEPTION: In early game (turns 1-2) with GamePlan active,
                        # don't hold for next-turn crush - prioritize establishing presence.
                        # Holding on turn 1 loses tempo that's hard to recover.

                        is_early_game_with_gameplan = (
                            current_turn <= 2 and
                            gameplan_active
                        )
                        if is_early_game_with_gameplan:
                            logger.info(f"🔮 Skipping next-turn crush check (GamePlan active, turn {current_turn})")

                        # Check if a previous hold failed (enemy countered, etc.)
                        hold_failed = getattr(board_state, 'hold_failed_last_turn', False)
                        if hold_failed:
                            logger.info(f"🔮 Skipping hold - previous hold failed, being aggressive")

                        should_hold_for_crush = (
                            not already_held_too_long and
                            not is_early_game_with_gameplan and  # Don't hold in early game with GamePlan
                            not hold_failed and  # Don't hold if previous hold failed
                            next_turn_crush.expected_advantage >= get_battle_favorable_threshold() and
                            next_turn_score > best_score * 0.5
                        )

                        # FIX: When we've held too long, EXECUTE the crush NOW instead of skipping
                        if already_held_too_long and next_turn_crush.expected_advantage >= get_battle_favorable_threshold():
                            logger.info(f"🔮 EXECUTE CRUSH NOW - already held {board_state.consecutive_hold_turns} turns, deploying!")
                            logger.info(f"   Crush target: {next_turn_crush.card_names} → {next_turn_crush.target_location_name}")
                            logger.info(f"   Power: {next_turn_crush.total_power} vs {next_turn_crush.target_enemy_power} "
                                       f"(+{next_turn_crush.expected_advantage})")

                            # Clear any existing instructions and add crush cards
                            plan.instructions.clear()
                            plan.strategy = DeployStrategy.REINFORCE
                            plan.reason = (f"EXECUTING CRUSH: {', '.join(next_turn_crush.card_names)} → "
                                          f"{next_turn_crush.target_location_name} "
                                          f"(+{next_turn_crush.expected_advantage} advantage)")

                            # Create deployment instructions for each card in the crush plan
                            for i, blueprint_id in enumerate(next_turn_crush.card_blueprint_ids):
                                card_name = next_turn_crush.card_names[i] if i < len(next_turn_crush.card_names) else "Unknown"
                                card_data = get_card(blueprint_id)
                                deploy_cost = card_data.deploy_value if card_data else 4  # Default cost
                                power = card_data.power_value if card_data else 3  # Default power

                                plan.instructions.append(DeploymentInstruction(
                                    card_blueprint_id=blueprint_id,
                                    card_name=card_name,
                                    target_location_id=next_turn_crush.target_location_id,
                                    target_location_name=next_turn_crush.target_location_name,
                                    priority=1,  # High priority - reinforce/crush
                                    reason=f"CRUSH at {next_turn_crush.target_location_name} (+{next_turn_crush.expected_advantage})",
                                    power_contribution=power,
                                    deploy_cost=deploy_cost,
                                ))

                            # Reset the hold counter since we're executing now
                            board_state.consecutive_hold_turns = 0
                            logger.info(f"   Created {len(plan.instructions)} deployment instructions")

                            plan.phase_started = True
                            self.current_plan = plan
                            return plan

                        if should_hold_for_crush:
                            logger.info(f"🔮 HOLD FOR NEXT-TURN CRUSH!")
                            logger.info(f"   Current plan: {best_type} score={best_score:.0f} (establish, not counter)")
                            logger.info(f"   Next-turn crush: {next_turn_crush.card_names} → {next_turn_crush.target_location_name}")
                            logger.info(f"   Power: {next_turn_crush.total_power} vs {next_turn_crush.target_enemy_power} "
                                       f"(+{next_turn_crush.expected_advantage})")
                            logger.info(f"   Crush score: {next_turn_score:.0f} vs establish score: {best_score:.0f}")
                            logger.info(f"   Force needed: {next_turn_crush.force_needed}, expected next turn: {next_turn_crush.expected_force_next_turn}")

                            # Clear the plan and set HOLD_BACK
                            plan.instructions.clear()
                            plan.strategy = DeployStrategy.HOLD_BACK
                            plan.reason = (f"Saving for next-turn CRUSH: {', '.join(next_turn_crush.card_names)} → "
                                          f"{next_turn_crush.target_location_name} "
                                          f"(+{next_turn_crush.expected_advantage} advantage)")

                            # Store the next-turn crush plan on board_state for other evaluators
                            board_state.next_turn_crush_plan = next_turn_crush

                            # Increment consecutive hold counter
                            board_state.consecutive_hold_turns += 1
                            logger.info(f"   Consecutive holds: {board_state.consecutive_hold_turns}")

                            # Mark phase as started so this plan gets cached properly
                            plan.phase_started = True
                            self.current_plan = plan
                            return plan
                        else:
                            logger.debug(f"🔮 Next-turn crush found but current plan is better: "
                                       f"crush score ~{next_turn_score:.0f} vs current {best_score:.0f}")

                # =================================================================
                # NEXT-TURN BLEED STOP CHECK
                # If we're losing the drain economy and have uncovered bleeds,
                # check if waiting one turn would let us stop a significant bleed.
                # =================================================================
                if drain_gap < 0 and bleed_locations:
                    # Check if current plan already covers bleed locations
                    current_targets = {inst.target_location_name for inst in best_instructions}
                    uncovered_for_next_turn = [
                        loc for loc in bleed_locations
                        if loc.name not in current_targets
                    ]

                    if uncovered_for_next_turn:
                        all_hand_cards = self._get_all_hand_cards_as_dicts(board_state)
                        next_turn_bleed_stop = self._find_next_turn_bleed_stop_opportunities(
                            board_state, locations, uncovered_for_next_turn, all_hand_cards, total_force
                        )

                        if next_turn_bleed_stop:
                            # Score: icons saved per turn × 20 (compounding value)
                            # Compare to current plan score, but weight bleed stops highly
                            # (each icon saved per turn is like gaining 1 force advantage permanently)
                            icons_at_target = next(
                                (loc.my_icons for loc in uncovered_for_next_turn
                                 if loc.card_id == next_turn_bleed_stop.target_location_id),
                                2)
                            next_turn_score = icons_at_target * 25  # High weight for stopping bleeds

                            # Prefer next-turn bleed stop if:
                            # - Current plan is weak (score < 40) AND bleed is significant (icons >= 2)
                            # OR current plan is just marginally better
                            # BUT NOT if we've already held for too many turns
                            should_hold_for_bleed = (
                                not already_held_too_long and
                                not hold_failed and  # Don't hold if previous hold failed
                                ((best_score < 40 and icons_at_target >= 2) or next_turn_score > best_score * 0.9)
                            )

                            if should_hold_for_bleed:
                                logger.info(f"🩸🔮 HOLD FOR NEXT-TURN BLEED STOP!")
                                logger.info(f"   Current plan: {best_type} score={best_score:.0f}")
                                logger.info(f"   Next-turn stop: {next_turn_bleed_stop.card_names} → {next_turn_bleed_stop.target_location_name}")
                                logger.info(f"   Will save {icons_at_target} drain/turn")
                                logger.info(f"   Force needed: {next_turn_bleed_stop.force_needed}, expected next turn: {next_turn_bleed_stop.expected_force_next_turn}")

                                # Clear the plan and set HOLD_BACK
                                plan.instructions.clear()
                                plan.strategy = DeployStrategy.HOLD_BACK
                                plan.reason = (f"Saving for next-turn BLEED STOP: {', '.join(next_turn_bleed_stop.card_names)} → "
                                              f"{next_turn_bleed_stop.target_location_name} "
                                              f"(stop {icons_at_target} drain/turn)")

                                # Store the plan on board_state for other evaluators
                                board_state.next_turn_crush_plan = next_turn_bleed_stop

                                # Increment consecutive hold counter
                                board_state.consecutive_hold_turns += 1
                                logger.info(f"   Consecutive holds: {board_state.consecutive_hold_turns}")

                                # Mark phase as started so this plan gets cached properly
                                plan.phase_started = True
                                self.current_plan = plan
                                return plan
                            else:
                                logger.debug(f"🩸🔮 Next-turn bleed stop found but current plan is better: "
                                           f"bleed score ~{next_turn_score:.0f} vs current {best_score:.0f}")

                # =================================================================
                # HOLD FOR BETTER DRAWS CHECK
                # If current plan is marginal (weak establish or low score) AND
                # there's good probability of drawing better cards from force pile,
                # consider holding back to turn a mediocre plan into a crushing one.
                #
                # Uses DeckTracker to estimate probability of drawing characters/ships.
                # =================================================================
                if (not already_held_too_long and
                    best_score > 0 and best_score < 45 and  # Marginal plan
                    best_type == 'ground' and  # Only for ground (space usually better)
                    total_force >= 3):  # Have force to work with next turn

                    from engine.deck_tracker import get_deck_tracker
                    tracker = get_deck_tracker()

                    if tracker.deck_loaded:
                        # Calculate expected power improvement from drawing
                        p_char = tracker.probability_draw_type("Character")
                        p_ship = tracker.probability_draw_type("Starship")
                        expected_destiny = tracker.expected_destiny()

                        # Get average power of characters/ships in remaining deck
                        remaining = tracker.get_remaining_in_reserve()
                        total_char_power = 0
                        char_count = 0
                        for bp_id, count in remaining.items():
                            stats = tracker._card_stats.get(bp_id)
                            if stats and stats.card_type == "Character":
                                total_char_power += stats.power * count
                                char_count += count

                        avg_char_power = total_char_power / char_count if char_count > 0 else 3.0

                        # Estimate: if we draw and get a character with power > current plan's average
                        current_avg_power = sum(
                            inst.power_contribution for inst in best_instructions
                        ) / len(best_instructions) if best_instructions else 3.0

                        # Hold if:
                        # 1. Good chance of drawing a character (>35%)
                        # 2. Average remaining character is better than current plan
                        # 3. Have enough force to draw (1) + deploy next turn
                        can_afford_draw_then_deploy = (
                            board_state.force_pile >= 1 and  # Can draw at least one
                            total_force >= avg_char_power + 2  # Expected force next turn covers deploy
                        )

                        should_hold_for_draw = (
                            p_char >= 0.35 and
                            avg_char_power > current_avg_power + 1 and
                            can_afford_draw_then_deploy and
                            not hold_failed  # Don't hold if previous hold failed
                        )

                        if should_hold_for_draw:
                            logger.info(f"🎰 HOLD FOR BETTER DRAW!")
                            logger.info(f"   Current plan: {best_type} score={best_score:.0f}, avg power={current_avg_power:.1f}")
                            logger.info(f"   P(Character)={p_char:.0%}, avg remaining char power={avg_char_power:.1f}")
                            logger.info(f"   E[destiny]={expected_destiny:.1f}, force for draw+deploy: {total_force}")

                            # Clear the plan and set HOLD_BACK
                            plan.instructions.clear()
                            plan.strategy = DeployStrategy.HOLD_BACK
                            plan.reason = (f"Holding for better draw: P(char)={p_char:.0%}, "
                                          f"avg power {avg_char_power:.1f} > current {current_avg_power:.1f}")

                            # Increment consecutive hold counter
                            board_state.consecutive_hold_turns += 1
                            logger.info(f"   Consecutive holds: {board_state.consecutive_hold_turns}")

                            plan.phase_started = True
                            self.current_plan = plan
                            return plan
                        else:
                            logger.debug(f"🎰 Draw hold check: P(char)={p_char:.0%}, avg={avg_char_power:.1f} vs current={current_avg_power:.1f}")

                # CRITICAL: Filter out cards already in the plan from earlier steps
                # (e.g., STEP 4 may have added cards for weak location reinforcement)
                already_planned_blueprints = {inst.card_blueprint_id for inst in plan.instructions}
                new_instructions = [
                    inst for inst in best_instructions
                    if inst.card_blueprint_id not in already_planned_blueprints
                ]
                if len(new_instructions) < len(best_instructions):
                    skipped = len(best_instructions) - len(new_instructions)
                    logger.info(f"   ⏭️ Skipped {skipped} cards already in plan from earlier steps")

                plan.instructions.extend(new_instructions)
                plan.force_reserved_for_battle = best_reserve  # Update with actual reserve
                force_remaining = best_force_left

                # Update available_chars based on what was used
                used_blueprints = {inst.card_blueprint_id for inst in best_instructions}
                available_chars = [c for c in available_chars if c['blueprint_id'] not in used_blueprints]

                # =================================================================
                # STEP 5C: CROSS-DOMAIN DEPLOYMENT (fallback)
                # If we chose a single-domain plan and still have force left,
                # try to deploy to the other domain with remaining force.
                # =================================================================
                if force_remaining > 0 and best_type != 'combined':
                    if best_type == 'repilot' and starships:
                        # Re-pilot plan chosen - deploy remaining piloted starships to other space locations
                        remaining_ships = [s for s in starships if s['blueprint_id'] not in used_blueprints]
                        piloted_ships = [s for s in remaining_ships if not s.get('needs_pilot') and s['power'] > 0]

                        if piloted_ships and space_targets:
                            logger.info(f"   🔄 AFTER RE-PILOT: {force_remaining} force left, deploying to space")
                            deployed_any = False
                            for loc in space_targets:
                                if force_remaining <= 0:
                                    break
                                # Find affordable ships that meet threshold
                                affordable = [s for s in piloted_ships
                                             if s['cost'] <= force_remaining and s['power'] >= space_threshold]
                                if not affordable:
                                    continue
                                # Pick best ship (highest power)
                                best_ship = max(affordable, key=lambda s: s['power'])
                                plan.instructions.append(DeploymentInstruction(
                                    card_blueprint_id=best_ship['blueprint_id'],
                                    card_name=best_ship['name'],
                                    target_location_id=loc.card_id,
                                    target_location_name=loc.name,
                                    priority=2,
                                    reason=f"Space: Deploy after re-pilot ({best_ship['power']} power)",
                                    power_contribution=best_ship['power'],
                                    deploy_cost=best_ship['cost'],
                                ))
                                logger.info(f"   🚀 AFTER RE-PILOT: Deploy {best_ship['name']} to {loc.name}")
                                force_remaining -= best_ship['cost']
                                piloted_ships.remove(best_ship)
                                deployed_any = True
                                # Continue to next location - deploy to multiple if affordable!

                            # Also try to deploy remaining ships to same location if we have force left
                            if not deployed_any and piloted_ships and force_remaining > 0:
                                # Couldn't deploy to new locations, try any affordable ship
                                affordable = [s for s in piloted_ships if s['cost'] <= force_remaining]
                                if affordable and space_targets:
                                    best_ship = max(affordable, key=lambda s: s['power'])
                                    loc = space_targets[0]
                                    plan.instructions.append(DeploymentInstruction(
                                        card_blueprint_id=best_ship['blueprint_id'],
                                        card_name=best_ship['name'],
                                        target_location_id=loc.card_id,
                                        target_location_name=loc.name,
                                        priority=2,
                                        reason=f"Space: Deploy after re-pilot (below threshold but using force)",
                                        power_contribution=best_ship['power'],
                                        deploy_cost=best_ship['cost'],
                                    ))
                                    logger.info(f"   🚀 AFTER RE-PILOT: Deploy {best_ship['name']} to {loc.name} (using remaining force)")
                                    force_remaining -= best_ship['cost']

                    elif best_type == 'ground' and starships:
                        # Ground plan chosen - deploy remaining piloted starships to space
                        remaining_ships = [s for s in starships if s['blueprint_id'] not in used_blueprints]
                        piloted_ships = [s for s in remaining_ships if not s.get('needs_pilot') and s['power'] > 0]

                        if piloted_ships and space_targets:
                            logger.info(f"   🔄 CROSS-DOMAIN: {force_remaining} force left, checking space deployment")
                            for loc in space_targets:
                                if force_remaining <= 0:
                                    break
                                # Find affordable ships that meet threshold
                                affordable = [s for s in piloted_ships
                                             if s['cost'] <= force_remaining and s['power'] >= space_threshold]
                                if not affordable:
                                    continue
                                # Pick best ship
                                best_ship = max(affordable, key=lambda s: s['power'])
                                plan.instructions.append(DeploymentInstruction(
                                    card_blueprint_id=best_ship['blueprint_id'],
                                    card_name=best_ship['name'],
                                    target_location_id=loc.card_id,
                                    target_location_name=loc.name,
                                    priority=2,
                                    reason=f"Space: Cross-domain deploy after ground ({best_ship['power']} power)",
                                    power_contribution=best_ship['power'],
                                    deploy_cost=best_ship['cost'],
                                ))
                                logger.info(f"   🚀 CROSS-DOMAIN: Deploy {best_ship['name']} to {loc.name}")
                                force_remaining -= best_ship['cost']
                                piloted_ships.remove(best_ship)
                                break  # One location per cross-domain for now

                    elif best_type == 'space' and available_chars:
                        # Space plan chosen - deploy remaining characters to ground
                        if char_ground_targets:
                            logger.info(f"   🔄 CROSS-DOMAIN: {force_remaining} force left, checking ground deployment")
                            for loc in char_ground_targets:
                                if force_remaining <= 0:
                                    break
                                # Find optimal character combination for this location
                                power_goal = max(ground_threshold, loc.their_power + 1)
                                chars_for_loc, power_allocated, cost_used = self._find_optimal_combination(
                                    available_chars, force_remaining, power_goal, must_exceed=(loc.their_power > 0)
                                )
                                if not chars_for_loc:
                                    continue
                                # Check if we meet requirements
                                if loc.their_power > 0 and power_allocated <= loc.their_power:
                                    continue
                                if loc.their_power == 0 and power_allocated < ground_threshold:
                                    continue
                                # Deploy characters
                                for char in chars_for_loc:
                                    plan.instructions.append(DeploymentInstruction(
                                        card_blueprint_id=char['blueprint_id'],
                                        card_name=char['name'],
                                        target_location_id=loc.card_id,
                                        target_location_name=loc.name,
                                        priority=2,
                                        reason=f"Ground: Cross-domain deploy after space ({char['power']} power)",
                                        power_contribution=char['power'],
                                        deploy_cost=char['cost'],
                                    ))
                                    logger.info(f"   🎭 CROSS-DOMAIN: Deploy {char['name']} to {loc.name}")
                                    if char in available_chars:
                                        available_chars.remove(char)
                                force_remaining -= cost_used
                                break  # One location per cross-domain for now

        else:
            logger.info(f"   ⏭️ No valid ground or space plans")

        # STEP 5C: Deploy VEHICLES + PILOTS to GROUND locations
        # Vehicles can't go to space! And unpiloted vehicles need pilots to have power.
        # =================================================================
        # Get available pilots from remaining characters for vehicle combos
        # These are pilots that weren't used in the chosen ground/space plan
        available_pilot_chars = [c for c in available_chars if c.get('is_pilot')]
        if available_pilot_chars:
            logger.info(f"   👨‍✈️ Available pilots for vehicles: {[p['name'] for p in available_pilot_chars]}")

        # Create pilot+vehicle combos for unpiloted vehicles
        piloted_combos = []  # List of {vehicle, pilot, combined_power, combined_cost}
        available_reserved = available_pilot_chars.copy()

        if unpiloted_vehicles and available_reserved:
            logger.info(f"   🔧 Pairing {len(available_reserved)} reserved pilots with unpiloted vehicles...")
            for vehicle in unpiloted_vehicles[:]:  # Copy to allow modification
                if not available_reserved:
                    break
                # Pick best pilot (highest power, with matching bonus)
                best_pilot = max(available_reserved, key=lambda p: _pilot_score_for_ship(p, vehicle))
                combined_power = vehicle['base_power'] + best_pilot['power']
                combined_cost = vehicle['cost'] + best_pilot['cost']

                piloted_combos.append({
                    'vehicle': vehicle,
                    'pilot': best_pilot,
                    'power': combined_power,
                    'cost': combined_cost,
                    'name': f"{vehicle['name']} + {best_pilot['name']}",
                })
                logger.info(f"   👨‍✈️ Combo: {vehicle['name']} + {best_pilot['name']} = {combined_power} power, {combined_cost} cost")
                available_reserved.remove(best_pilot)
                available_vehicles.remove(vehicle)

        # Also include piloted vehicles (permanent pilot) that have power on their own
        piloted_vehicles = [v for v in available_vehicles if not v.get('needs_pilot') and v['power'] > 0]

        # Find EXTERIOR ground locations where opponent has presence
        # IMPORTANT: Vehicles can ONLY deploy to EXTERIOR locations (not interior!)
        # This is more aggressive - we want to ATTACK, not just establish
        # CRITICAL: Must have our icons to deploy (or presence, but we filter my_power==0)
        ground_targets = [
            loc for loc in locations
            if loc.is_ground and loc.is_site and loc.is_exterior  # Vehicles need exterior sites (not systems)
            and (loc.their_power > 0 or loc.their_icons > 0)  # Has opponent presence or icons
            and loc.my_power == 0  # We're not there yet
            and loc.my_icons > 0  # MUST have our force icons to deploy there
        ]
        # Sort by opponent power (attack weakest first for easy wins)
        ground_targets.sort(key=lambda x: x.their_power)

        # Log why locations were excluded
        interior_only = [loc.name for loc in locations if loc.is_ground and loc.is_interior and not loc.is_exterior]
        if interior_only:
            logger.info(f"   ⛔ Interior-only locations (no vehicles): {interior_only}")

        logger.info(f"   🎯 Ground targets for vehicles (exterior): {[loc.name for loc in ground_targets]}")

        # Deploy piloted combos (vehicle + pilot together)
        # Uses ground_threshold since vehicles deploy to ground locations
        for loc in ground_targets:
            if not piloted_combos or force_remaining <= 0:
                break

            # At contested locations: just beat opponent
            # At uncontested locations: must meet threshold to establish
            power_needed = loc.their_power + 1 if loc.their_power > 0 else ground_threshold
            affordable_combos = [c for c in piloted_combos if c['cost'] <= force_remaining]

            if not affordable_combos:
                continue

            # Pick combo with highest power
            best_combo = max(affordable_combos, key=lambda c: c['power'])

            if best_combo['power'] >= power_needed:
                # Deploy both vehicle and pilot!
                vehicle = best_combo['vehicle']
                pilot = best_combo['pilot']

                logger.info(f"   🚗+👨‍✈️ Plan: Deploy {vehicle['name']} + {pilot['name']} ({best_combo['power']} power, {best_combo['cost']} cost) to {loc.name}")

                # Add vehicle to plan
                plan.instructions.append(DeploymentInstruction(
                    card_blueprint_id=vehicle['blueprint_id'],
                    card_name=vehicle['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=2,
                    reason=f"Vehicle to {loc.name} (with pilot)",
                    power_contribution=vehicle['base_power'],
                    deploy_cost=vehicle['cost'],
                ))

                # Add pilot to plan (will board the vehicle)
                plan.instructions.append(DeploymentInstruction(
                    card_blueprint_id=pilot['blueprint_id'],
                    card_name=pilot['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=2,
                    reason=f"Pilot for {vehicle['name']} at {loc.name}",
                    power_contribution=pilot['power'],
                    deploy_cost=pilot['cost'],
                ))

                force_remaining -= best_combo['cost']
                piloted_combos.remove(best_combo)

                # Remove pilot from available_chars too
                if pilot in available_chars:
                    available_chars.remove(pilot)

        # Deploy any remaining piloted vehicles (have permanent pilots)
        for loc in ground_targets:
            if not piloted_vehicles or force_remaining <= 0:
                break

            # At contested locations: just beat opponent
            # At uncontested locations: must meet threshold to establish
            power_needed = loc.their_power + 1 if loc.their_power > 0 else ground_threshold

            for vehicle in piloted_vehicles[:]:  # Copy to allow removal
                if vehicle['cost'] > force_remaining:
                    continue
                if vehicle['power'] >= power_needed:
                    plan.instructions.append(DeploymentInstruction(
                        card_blueprint_id=vehicle['blueprint_id'],
                        card_name=vehicle['name'],
                        target_location_id=loc.card_id,
                        target_location_name=loc.name,
                        priority=2,
                        reason=f"Vehicle at {loc.name} (has permanent pilot)",
                        power_contribution=vehicle['power'],
                        deploy_cost=vehicle['cost'],
                    ))
                    logger.info(f"   🚗 Plan: Deploy {vehicle['name']} ({vehicle['power']} power) to {loc.name}")
                    force_remaining -= vehicle['cost']
                    piloted_vehicles.remove(vehicle)
                    break

        # =================================================================
        # STEP 5D: PILE ON - Deploy additional cards to CONTESTED locations
        # Once we've committed to a battle, spend remaining force to crush opponent
        # Keep force reserved for battle itself (configurable)
        # NOTE: Only pile on at contested locations (their_power > 0)
        #       Uncontested locations use establish threshold, not pile-on
        # =================================================================
        battle_reserve = self.battle_force_reserve
        planned_location_ids = set(inst.target_location_id for inst in plan.instructions if inst.target_location_id)

        # Only pile on at CONTESTED locations (opponent has presence)
        attack_locs = [
            loc for loc in locations
            if loc.card_id in planned_location_ids
            and loc.their_power > 0  # ONLY contested locations
        ]

        if attack_locs and force_remaining > battle_reserve:
            logger.info(f"   💪 PILE ON: {force_remaining} force remaining, {len(attack_locs)} contested locations")

            # Sort by opponent power (pile on to hardest battles first)
            attack_locs.sort(key=lambda x: x.their_power, reverse=True)

            for loc in attack_locs:
                if force_remaining <= battle_reserve:
                    break

                # Deploy any remaining piloted vehicles here (only to exterior locations!)
                if loc.is_exterior:
                    # Filter vehicles that can deploy to this location
                    eligible_vehicles = self._filter_cards_for_location(piloted_vehicles, loc.name)
                    for vehicle in eligible_vehicles[:]:
                        if force_remaining <= battle_reserve:
                            break
                        if vehicle['cost'] <= force_remaining - battle_reserve:
                            plan.instructions.append(DeploymentInstruction(
                                card_blueprint_id=vehicle['blueprint_id'],
                                card_name=vehicle['name'],
                                target_location_id=loc.card_id,
                                target_location_name=loc.name,
                                priority=2,
                                reason=f"PILE ON at {loc.name} (+{vehicle['power']} power)",
                                power_contribution=vehicle['power'],
                                deploy_cost=vehicle['cost'],
                            ))
                            logger.info(f"   💪 PILE ON: Deploy {vehicle['name']} ({vehicle['power']} power) to {loc.name}")
                            force_remaining -= vehicle['cost']
                            piloted_vehicles.remove(vehicle)

                # Deploy any remaining characters here (if it's a ground location)
                if loc.is_ground:
                    # Filter characters that can deploy to this location
                    eligible_chars = self._filter_cards_for_location(available_chars, loc.name)
                    for char in eligible_chars[:]:
                        if force_remaining <= battle_reserve:
                            break
                        if char['cost'] <= force_remaining - battle_reserve:
                            plan.instructions.append(DeploymentInstruction(
                                card_blueprint_id=char['blueprint_id'],
                                card_name=char['name'],
                                target_location_id=loc.card_id,
                                target_location_name=loc.name,
                                priority=2,
                                reason=f"PILE ON at {loc.name} (+{char['power']} power)",
                                power_contribution=char['power'],
                                deploy_cost=char['cost'],
                            ))
                            logger.info(f"   💪 PILE ON: Deploy {char['name']} ({char['power']} power) to {loc.name}")
                            force_remaining -= char['cost']
                            available_chars.remove(char)

        # =================================================================
        # STEP 5D-2: REINFORCE ESTABLISHED (UNCONTESTED) LOCATIONS
        # If we're establishing at empty locations and have EXCESS force,
        # add extra power for defensive buffer (opponent may attack next turn).
        # Only do this if we have substantial leftover force (> 4) to avoid
        # waste on marginal gains.
        # =================================================================
        reinforce_threshold = get_reinforce_force_threshold()

        # Find UNCONTESTED locations where we're establishing (their_power == 0)
        establish_locs = [
            loc for loc in locations
            if loc.card_id in planned_location_ids
            and loc.their_power == 0  # ONLY uncontested locations
        ]

        if establish_locs and force_remaining > reinforce_threshold and available_chars:
            logger.info(f"   🛡️ REINFORCE ESTABLISHED: {force_remaining} force remaining, "
                       f"{len(establish_locs)} uncontested locations, {len(available_chars)} chars available")

            # Sort by their_icons (higher value locations get reinforced first)
            establish_locs.sort(key=lambda x: x.their_icons, reverse=True)

            for loc in establish_locs:
                if force_remaining <= reinforce_threshold:
                    break

                # Calculate current planned power at this location
                planned_power = sum(
                    inst.power_contribution for inst in plan.instructions
                    if inst.target_location_id == loc.card_id
                )

                # Only reinforce if we're at/near threshold (not already heavily fortified)
                # Use appropriate threshold for location type
                loc_threshold = space_threshold if loc.is_space else ground_threshold
                if planned_power >= loc_threshold + 4:
                    logger.debug(f"   ⏭️ Skip {loc.name}: already well-fortified ({planned_power} power)")
                    continue

                # Deploy additional characters (ground locations only)
                if loc.is_ground:
                    # Filter characters that can deploy to this location
                    eligible_chars = self._filter_cards_for_location(available_chars, loc.name)
                    for char in eligible_chars[:]:
                        if force_remaining <= reinforce_threshold:
                            break
                        # NOTE: No battle_reserve needed here - these are UNCONTESTED locations
                        # (their_power == 0), so no battle will be initiated
                        if char['cost'] <= force_remaining:
                            plan.instructions.append(DeploymentInstruction(
                                card_blueprint_id=char['blueprint_id'],
                                card_name=char['name'],
                                target_location_id=loc.card_id,
                                target_location_name=loc.name,
                                priority=2,
                                reason=f"REINFORCE {loc.name} (+{char['power']} defensive buffer)",
                                power_contribution=char['power'],
                                deploy_cost=char['cost'],
                            ))
                            logger.info(f"   🛡️ REINFORCE: Deploy {char['name']} ({char['power']} power) to {loc.name}")
                            force_remaining -= char['cost']
                            available_chars.remove(char)

        # =================================================================
        # STEP 5E: DEPLOY TARGETED WEAPONS if we have spare force (>= 2 remaining)
        # Priority: Attack locations we're deploying to, then existing presence
        # Weapons attach to characters/vehicles/starships based on weapon subtype
        #
        # RULES:
        # - Each character/vehicle/starship can have at most 1 weapon attached
        # - Weapon subtype must match target type (character→character, etc.)
        # - Standalone weapons (automated, artillery) are NOT included here
        #   (they're handled as "extra actions" after planned deployments)
        # =================================================================
        min_force_weapons = get_min_force_for_weapons()

        # Filter to only TARGETED weapons (not standalone)
        targeted_weapons = [w for w in weapons if w.get('is_targeted_weapon')]
        standalone_weapons = [w for w in weapons if w.get('is_standalone_weapon')]

        if standalone_weapons:
            logger.info(f"   🎯 {len(standalone_weapons)} standalone weapons (automated/artillery) - saved for extra actions")

        if targeted_weapons and force_remaining >= min_force_weapons:
            logger.info(f"   🗡️ Checking {len(targeted_weapons)} targeted weapons ({force_remaining} force remaining)")

            # Separate by weapon target type
            char_weapons = [w for w in targeted_weapons if w.get('weapon_target_type') == 'character']
            vehicle_weapons = [w for w in targeted_weapons if w.get('weapon_target_type') == 'vehicle']
            starship_weapons = [w for w in targeted_weapons if w.get('weapon_target_type') == 'starship']

            if char_weapons:
                logger.info(f"      Character weapons: {[w['name'] for w in char_weapons]}")
            if vehicle_weapons:
                logger.info(f"      Vehicle weapons: {[w['name'] for w in vehicle_weapons]}")
            if starship_weapons:
                logger.info(f"      Starship weapons: {[w['name'] for w in starship_weapons]}")

            # Find locations where we have or WILL HAVE presence
            # Priority order: attack locations (we're deploying there) > existing presence
            attack_locs_for_weapons = [loc for loc in locations if loc.card_id in planned_location_ids]
            existing_presence = [loc for loc in locations if loc.my_power > 0 and loc.card_id not in planned_location_ids]

            # Combine in priority order - attack locations first!
            weapon_target_locs = attack_locs_for_weapons + existing_presence

            # =================================================================
            # WARRIOR TRACKING for character weapons
            # Character weapons can ONLY be held by warriors!
            # Track available warriors at each location (from plan + existing)
            # =================================================================
            warriors_at_location: Dict[str, int] = {}  # loc_id -> count of available warriors

            # Count warriors being deployed in this plan
            for inst in plan.instructions:
                if inst.target_location_id:
                    # Find the character in our hand list
                    char_info = next((c for c in characters if c['blueprint_id'] == inst.card_blueprint_id), None)
                    if char_info and char_info.get('is_warrior'):
                        loc_id = inst.target_location_id
                        warriors_at_location[loc_id] = warriors_at_location.get(loc_id, 0) + 1
                        logger.debug(f"      Warrior in plan: {inst.card_name} -> {inst.target_location_name}")

            # Count existing warriors at locations (from board state)
            # CRITICAL: Only count warriors that DON'T already have weapons attached!
            if board_state and hasattr(board_state, 'cards_in_play'):
                from .card_loader import get_card
                for card_id, card in board_state.cards_in_play.items():
                    if card.owner == board_state.my_player_name and card.zone == "AT_LOCATION":
                        loc_idx = getattr(card, 'location_index', -1)
                        if loc_idx >= 0 and loc_idx < len(locations):
                            metadata = get_card(card.blueprint_id)
                            if metadata and metadata.is_warrior:
                                # Check if this warrior already has a weapon attached
                                has_weapon = any(
                                    get_card(ac.blueprint_id) and get_card(ac.blueprint_id).is_weapon
                                    for ac in card.attached_cards
                                )
                                if has_weapon:
                                    logger.debug(f"      Skip warrior {card.card_title} - already has weapon")
                                    continue
                                loc_id = locations[loc_idx].card_id
                                warriors_at_location[loc_id] = warriors_at_location.get(loc_id, 0) + 1
                                logger.debug(f"      Available warrior: {card.card_title} at loc {loc_idx}")

            if warriors_at_location:
                logger.info(f"      Warriors available: {warriors_at_location}")

            # =================================================================
            # VEHICLE TRACKING for vehicle weapons (e.g., AT-AT Cannon)
            # Vehicle weapons can ONLY be attached to vehicles!
            # Track available vehicles at each location (from plan + existing)
            # =================================================================
            vehicles_at_location: Dict[str, int] = {}  # loc_id -> count of available vehicles

            # Count vehicles being deployed in this plan
            for inst in plan.instructions:
                if inst.target_location_id:
                    vehicle_info = next((v for v in vehicles if v['blueprint_id'] == inst.card_blueprint_id), None)
                    if vehicle_info:
                        loc_id = inst.target_location_id
                        vehicles_at_location[loc_id] = vehicles_at_location.get(loc_id, 0) + 1
                        logger.debug(f"      Vehicle in plan: {inst.card_name} -> {inst.target_location_name}")

            # Count existing vehicles at locations (from board state)
            # Only count vehicles that DON'T already have vehicle weapons attached!
            if board_state and hasattr(board_state, 'cards_in_play'):
                from .card_loader import get_card
                for card_id, card in board_state.cards_in_play.items():
                    if card.owner == board_state.my_player_name and card.zone == "AT_LOCATION":
                        loc_idx = getattr(card, 'location_index', -1)
                        if loc_idx >= 0 and loc_idx < len(locations):
                            metadata = get_card(card.blueprint_id)
                            if metadata and metadata.card_type == 'Vehicle':
                                # Check if this vehicle already has a weapon attached
                                has_weapon = any(
                                    get_card(ac.blueprint_id) and get_card(ac.blueprint_id).is_weapon
                                    for ac in card.attached_cards
                                )
                                if has_weapon:
                                    logger.debug(f"      Skip vehicle {card.card_title} - already has weapon")
                                    continue
                                loc_id = locations[loc_idx].card_id
                                vehicles_at_location[loc_id] = vehicles_at_location.get(loc_id, 0) + 1
                                logger.debug(f"      Available vehicle: {card.card_title} at loc {loc_idx}")

            if vehicles_at_location:
                logger.info(f"      Vehicles available: {vehicles_at_location}")

            if weapon_target_locs:
                # Track which targets already have a weapon in the plan
                # (to enforce 1 weapon max per target)
                targets_with_planned_weapons: Set[str] = set()
                # Track weapons allocated per location (for character weapons)
                weapons_at_location: Dict[str, int] = {}
                # Track vehicle weapons allocated per location
                vehicle_weapons_at_location: Dict[str, int] = {}

                for weapon in targeted_weapons:
                    if force_remaining < weapon['cost']:
                        continue

                    weapon_type = weapon.get('weapon_target_type')
                    is_char_specific = weapon.get('is_character_weapon', False)
                    matching_chars = weapon.get('matching_weapon', [])

                    # =============================================================
                    # CHARACTER-SPECIFIC WEAPON CHECK
                    # Weapons like "Qui-Gon Jinn's Lightsaber" can only deploy
                    # on specific characters. Check if a matching character exists.
                    # =============================================================
                    if is_char_specific and matching_chars:
                        # Build list of character names available (in plan + on board)
                        available_char_names = set()

                        # Characters being deployed in this plan
                        for inst in plan.instructions:
                            if inst.card_name:
                                available_char_names.add(inst.card_name.lower())

                        # Characters already in play
                        if board_state and hasattr(board_state, 'cards_in_play'):
                            for card_id, card in board_state.cards_in_play.items():
                                if (card.owner == board_state.my_player_name and
                                    card.zone == "AT_LOCATION" and card.card_title):
                                    available_char_names.add(card.card_title.lower())

                        # Check if ANY matching character is available
                        has_matching_char = False
                        for match_name in matching_chars:
                            match_lower = match_name.lower() if match_name else ""
                            for char_name in available_char_names:
                                if match_lower in char_name:
                                    has_matching_char = True
                                    logger.debug(f"      {weapon['name']} matches character: {char_name}")
                                    break
                            if has_matching_char:
                                break

                        if not has_matching_char:
                            logger.info(f"   ⏭️ Skip {weapon['name']}: no matching character (needs: {matching_chars[:3]}...)")
                            continue

                    # Find a location with a valid target for this weapon type
                    target_loc = None
                    for loc in weapon_target_locs:
                        # Check if this location has a valid target type
                        # Ground locations: can have characters and vehicles
                        # Space locations: can have starships
                        if weapon_type == 'character' and loc.is_ground:
                            # CRITICAL: Character weapons require WARRIORS!
                            # Check if there's an available warrior at this location
                            available_warriors = warriors_at_location.get(loc.card_id, 0)
                            allocated_weapons = weapons_at_location.get(loc.card_id, 0)
                            if available_warriors > allocated_weapons:
                                target_loc = loc
                                break
                            else:
                                logger.debug(f"      Skip {loc.name}: no available warriors ({available_warriors} warriors, {allocated_weapons} weapons allocated)")
                        elif weapon_type == 'vehicle' and loc.is_ground and loc.is_exterior:
                            # CRITICAL: Vehicle weapons require VEHICLES!
                            # Check if there's an available vehicle at this location
                            available_vehicles = vehicles_at_location.get(loc.card_id, 0)
                            allocated_vweapons = vehicle_weapons_at_location.get(loc.card_id, 0)
                            if available_vehicles > allocated_vweapons:
                                target_loc = loc
                                break
                            else:
                                logger.debug(f"      Skip {loc.name}: no available vehicles ({available_vehicles} vehicles, {allocated_vweapons} weapons allocated)")
                        elif weapon_type == 'starship' and loc.is_space:
                            target_loc = loc
                            break

                    if not target_loc:
                        logger.info(f"   ⏭️ No valid location for {weapon_type} weapon {weapon['name']}")
                        continue

                    # Add weapon to plan
                    is_attack_target = target_loc.card_id in planned_location_ids
                    reason = f"Arm {weapon_type} at {target_loc.name}" + (" for BATTLE!" if is_attack_target else "")

                    plan.instructions.append(DeploymentInstruction(
                        card_blueprint_id=weapon['blueprint_id'],
                        card_name=weapon['name'],
                        target_location_id=target_loc.card_id,
                        target_location_name=target_loc.name,
                        priority=3,  # After character/ship deploys
                        reason=reason,
                        power_contribution=weapon.get('power', 0),
                        deploy_cost=weapon['cost'],
                    ))
                    force_remaining -= weapon['cost']

                    # Track weapons allocated (for target limits)
                    if weapon_type == 'character':
                        weapons_at_location[target_loc.card_id] = weapons_at_location.get(target_loc.card_id, 0) + 1
                    elif weapon_type == 'vehicle':
                        vehicle_weapons_at_location[target_loc.card_id] = vehicle_weapons_at_location.get(target_loc.card_id, 0) + 1

                    logger.info(f"   🗡️ Plan: Deploy {weapon['name']} ({weapon_type} weapon, cost {weapon['cost']}) to {target_loc.name}")
            else:
                logger.info("   🗡️ No locations with our presence for weapon targets")

        # =================================================================
        # STEP 6: FINALIZE PLAN
        # =================================================================

        # Calculate original plan cost for extra action tracking
        plan.original_plan_cost = sum(i.deploy_cost for i in plan.instructions)

        # Sort instructions by priority
        plan.instructions.sort(key=lambda x: x.priority)

        # Determine overall strategy
        if plan.instructions:
            has_locations = any(i.priority == 0 for i in plan.instructions)
            has_reinforcements = any(i.priority == 1 for i in plan.instructions)

            if has_locations:
                plan.strategy = DeployStrategy.DEPLOY_LOCATIONS
                plan.reason = f"Deploy {len([i for i in plan.instructions if i.priority == 0])} locations"
            elif has_reinforcements:
                plan.strategy = DeployStrategy.REINFORCE
                plan.reason = f"Reinforce {len([i for i in plan.instructions if i.priority == 1])} locations"
            else:
                plan.strategy = DeployStrategy.ESTABLISH
                plan.reason = f"Establish at {len([i for i in plan.instructions if i.priority == 2])} locations"

            logger.info(f"📋 FINAL PLAN: {plan.strategy.value} - {len(plan.instructions)} deployments")
            # Log all location card_ids for debugging deploy target matching
            loc_card_ids = [(loc.card_id, loc.name) for loc in locations]
            logger.info(f"   📍 Location card_ids: {loc_card_ids}")
            for i, inst in enumerate(plan.instructions):
                backup_info = f" (backup: {inst.backup_location_name}, id={inst.backup_location_id})" if inst.backup_location_id else ""
                target_info = f"{inst.target_location_name or 'table'} (id={inst.target_location_id})"
                logger.info(f"   {i+1}. {inst.card_name} -> {target_info}: {inst.reason}{backup_info}")
        else:
            plan.strategy = DeployStrategy.HOLD_BACK
            # Build detailed reason why we're holding back
            reasons = []
            if not locations:
                reasons.append("no locations on board")
            elif not char_ground_targets and not space_targets:
                reasons.append("no valid targets (all ground locs either have our presence or no opponent threat)")
            if not characters and not starships and not vehicles:
                reasons.append("no deployable units in hand")
            elif characters and not char_ground_targets:
                reasons.append(f"have {len(characters)} chars but no ground targets")
            if starships and not space_targets:
                reasons.append(f"have {len(starships)} starships but no space targets")
            if force_remaining <= 0:
                reasons.append("no force remaining")

            plan.reason = "; ".join(reasons) if reasons else "No good deployment options"
            logger.info(f"📋 FINAL PLAN: HOLD BACK - {plan.reason}")
            logger.info(f"   Debug: {len(locations)} locations, {len(characters)} chars, {len(starships)} ships, {force_remaining} force left")

        plan.phase_started = True
        plan.target_locations = locations

        # Assign backup targets for each instruction
        self._assign_backup_targets(plan, locations, board_state)

        # Reset consecutive hold counter if we're actually deploying (not holding)
        if plan.instructions and plan.strategy != DeployStrategy.HOLD_BACK:
            if hasattr(board_state, 'consecutive_hold_turns') and board_state.consecutive_hold_turns > 0:
                logger.info(f"📋 Resetting consecutive hold counter (was {board_state.consecutive_hold_turns})")
                board_state.consecutive_hold_turns = 0
            # Also reset hold_failed flag when we actually deploy
            if hasattr(board_state, 'hold_failed_last_turn'):
                board_state.hold_failed_last_turn = False

        self.current_plan = plan
        return plan

    def _assign_backup_targets(self, plan: DeploymentPlan, locations: List[LocationAnalysis], board_state=None):
        """
        For each instruction, find a backup location in case the primary is unavailable.

        This handles cases where game rules block deployment to the primary target
        (e.g., location is full, character can't deploy there due to card text).
        """
        if not locations or not plan.instructions:
            return

        # Check for objective-based deployment restrictions
        whap_restriction = has_we_have_a_plan_restriction(board_state) if board_state else False
        if whap_restriction:
            logger.debug("   📋 Backup selection: WHAP restriction active, excluding interior Naboo sites")

        # Separate ground and space locations
        # Apply WHAP restriction to ground locations for character deployments
        ground_locs = [
            loc for loc in locations
            if loc.is_ground and loc.my_icons > 0
            and not (whap_restriction and is_interior_naboo_site(loc.name, loc.is_interior))
        ]
        space_locs = [loc for loc in locations if loc.is_space and loc.my_icons > 0]

        logger.debug(f"   📋 Backup candidates: {len(ground_locs)} ground, {len(space_locs)} space locations")

        # Sort by strategic value (uncontested opponent locations first, then reinforcement opportunities)
        def location_value(loc: LocationAnalysis) -> tuple:
            """Higher value = better backup target"""
            has_opponent = loc.their_power > 0
            is_contested = has_opponent and loc.my_power > 0
            can_win = loc.my_power > loc.their_power if is_contested else True
            return (
                has_opponent and not is_contested,  # Uncontested opponent location (best)
                is_contested and can_win,            # Winning contested (good)
                loc.their_icons,                     # More opponent icons = more drain potential
                -loc.my_power,                       # Less of our power = more room to help
            )

        ground_locs.sort(key=location_value, reverse=True)
        space_locs.sort(key=location_value, reverse=True)

        for inst in plan.instructions:
            if not inst.target_location_id:
                continue  # Location cards don't need backups

            # Find backup from same type (ground or space)
            primary_loc = next((loc for loc in locations if loc.card_id == inst.target_location_id), None)
            if not primary_loc:
                continue

            backup_candidates = ground_locs if primary_loc.is_ground else space_locs

            # Find first candidate that isn't the primary
            # CRITICAL: Skip locations where we'd be walking into a massacre!
            # Don't pick a backup where opponent has overwhelming power compared to what we're deploying
            card_power = inst.power_contribution or 0

            for loc in backup_candidates:
                if loc.card_id == inst.target_location_id:
                    continue

                # === POWER DEFICIT CHECK ===
                # Backup locations should be SAFE to deploy to, not suicide missions!
                # Use the SAME standards we'd use for primary location selection.

                if loc.their_power > 0 and loc.my_power == 0:
                    # We'd be establishing ALONE against opponent
                    # This is very risky - opponent will likely battle and kill us
                    power_after = card_power
                    deficit = loc.their_power - power_after

                    # STRICT: Don't establish alone if we'd be at ANY significant deficit
                    # Allow at most -2 deficit (reasonable destiny swing)
                    # Also skip if opponent has 2x our power (they could reinforce easily)
                    if deficit > 2:
                        logger.debug(f"   Skipping backup {loc.name}: {card_power} vs {loc.their_power} = deficit {deficit} (too risky alone)")
                        continue
                    if card_power > 0 and loc.their_power >= card_power * 2:
                        logger.debug(f"   Skipping backup {loc.name}: {card_power} vs {loc.their_power} = opponent 2x+ our power")
                        continue

                elif loc.their_power > 0 and loc.my_power > 0:
                    # Contested location - check if deploying here helps meaningfully
                    power_after = loc.my_power + card_power
                    deficit = loc.their_power - power_after

                    # Skip if we'd STILL be at a deficit > 4 after deploying
                    # (We need to be competitive, not just slightly less losing)
                    if deficit > 4:
                        logger.debug(f"   Skipping backup {loc.name}: {power_after} vs {loc.their_power} = still losing by {deficit}")
                        continue

                # This location is viable as backup
                inst.backup_location_id = loc.card_id
                inst.backup_location_name = loc.name
                # Describe why this is the backup
                if loc.their_power > 0 and loc.my_power == 0:
                    inst.backup_reason = f"establish against opponent ({loc.their_power} power)"
                elif loc.their_power > 0:
                    inst.backup_reason = f"reinforce ({loc.my_power} vs {loc.their_power})"
                else:
                    inst.backup_reason = f"establish presence ({loc.my_icons} icons)"
                logger.debug(f"   📋 Backup for {inst.card_name}: {loc.name} ({inst.backup_reason})")
                break
            else:
                # No viable backup found after checking all candidates
                logger.debug(f"   ⚠️ No viable backup for {inst.card_name} - all locations too dangerous or restricted")

    def _get_all_hand_cards_as_dicts(self, board_state) -> List[Dict]:
        """
        Get ALL cards in hand as dicts, regardless of cost.

        Used for next-turn planning where we want to consider expensive cards
        that we can't afford this turn but could afford next turn.
        """
        from .card_loader import get_card

        all_cards = []
        for card in board_state.cards_in_hand:
            if not card.blueprint_id:
                continue
            metadata = get_card(card.blueprint_id)
            if not metadata:
                continue

            has_permanent_pilot = getattr(metadata, 'has_permanent_pilot', False)
            is_warrior = metadata.is_warrior if hasattr(metadata, 'is_warrior') else False
            is_unpiloted_craft = (metadata.is_starship or metadata.is_vehicle) and not has_permanent_pilot

            # Get parsed gametext abilities for better evaluation
            base_cost = metadata.deploy_value or 0
            deploy_reduction = metadata.parsed.deploy_reduction if hasattr(metadata, 'parsed') else 0
            effective_cost = max(0, base_cost - deploy_reduction)

            all_cards.append({
                'blueprint_id': card.blueprint_id,
                'name': metadata.title,
                'cost': effective_cost,  # Use effective cost (with reductions)
                'base_cost': base_cost,  # Original cost for reference
                'deploy_value': base_cost,
                'deploy_reduction': deploy_reduction,
                'power': 0 if is_unpiloted_craft else (metadata.power_value or 0),
                'power_value': metadata.power_value or 0,
                'base_power': metadata.power_value or 0,
                'is_character': metadata.is_character,
                'is_starship': metadata.is_starship,
                'is_vehicle': metadata.is_vehicle,
                'is_pilot': metadata.is_pilot,
                'is_warrior': is_warrior,
                'is_location': metadata.is_location,
                'has_permanent_pilot': has_permanent_pilot,
                'needs_pilot': is_unpiloted_craft,
                'pilot_adds_power': getattr(metadata, 'pilot_adds_power', 2) if metadata.is_pilot else 0,
                'ability': metadata.ability_value or 0,  # For destiny draw eligibility
                # Parsed gametext abilities
                'has_attrition_immunity': metadata.has_attrition_immunity,
                'immune_attrition_threshold': metadata.immune_attrition_threshold,
                'draws_extra_destiny': metadata.draws_extra_destiny,
                'force_drain_bonus': metadata.parsed.force_drain_bonus if hasattr(metadata, 'parsed') else 0,
            })

        return all_cards

    def _find_cheaper_alternative(
        self,
        board_state,
        card_type: str,  # "starship", "character", "vehicle"
        max_cost: int,
        exclude_blueprints: Set[str] = None
    ) -> Optional[Dict]:
        """
        Find the cheapest alternative card of a given type that we can afford.

        System 3: When a high-value card is unaffordable, find a cheaper alternative
        that serves the same purpose (same type, same domain).

        Args:
            board_state: Current board state
            card_type: Type of card to find ("starship", "character", "vehicle")
            max_cost: Maximum deploy cost we can afford
            exclude_blueprints: Blueprint IDs to exclude (already in plan)

        Returns:
            Card dict formatted for deployment, or None if no alternative exists.
        """
        from .card_loader import get_card

        exclude_blueprints = exclude_blueprints or set()
        alternatives = []

        # Get ALL cards from hand (including ones we filtered for being expensive)
        hand_list = list(board_state.cards_in_hand) if board_state.cards_in_hand else []

        for card in hand_list:
            if not card.blueprint_id:
                continue
            if card.blueprint_id in exclude_blueprints:
                continue

            metadata = get_card(card.blueprint_id)
            if not metadata:
                continue

            # Check card type
            is_match = False
            if card_type == "starship" and metadata.is_starship:
                is_match = True
            elif card_type == "character" and metadata.is_character:
                is_match = True
            elif card_type == "vehicle" and metadata.is_vehicle:
                is_match = True

            if not is_match:
                continue

            # Calculate effective deploy cost
            base_cost = metadata.deploy_value or 0
            deploy_reduction = metadata.parsed.deploy_reduction if hasattr(metadata, 'parsed') else 0
            effective_cost = max(0, base_cost - deploy_reduction)

            # Check affordability
            if effective_cost > max_cost:
                continue

            # Build card dict
            has_permanent_pilot = getattr(metadata, 'has_permanent_pilot', False)
            is_warrior = metadata.is_warrior if hasattr(metadata, 'is_warrior') else False
            is_unpiloted_craft = (metadata.is_starship or metadata.is_vehicle) and not has_permanent_pilot

            alternatives.append({
                'card_id': card.card_id,
                'blueprint_id': card.blueprint_id,
                'name': metadata.title,
                'power': 0 if is_unpiloted_craft else (metadata.power_value or 0),
                'base_power': metadata.power_value or 0,
                'cost': effective_cost,
                'base_cost': base_cost,
                'is_character': metadata.is_character,
                'is_starship': metadata.is_starship,
                'is_vehicle': metadata.is_vehicle,
                'is_pilot': metadata.is_pilot,
                'is_warrior': is_warrior,
                'has_permanent_pilot': has_permanent_pilot,
                'needs_pilot': is_unpiloted_craft,
                'pilot_adds_power': getattr(metadata, 'pilot_adds_power', 2) if metadata.is_pilot else 0,
            })

        if not alternatives:
            return None

        # Score alternatives by value per cost (prefer higher power, lower cost)
        def score_alternative(card):
            power = card.get('base_power', 0) or 0
            cost = max(1, card['cost'])
            has_pilot = card.get('has_permanent_pilot', False)
            # Higher power per cost is better; permanent pilot ships are more valuable
            return (power / cost) + (5 if has_pilot else 0)

        alternatives.sort(key=score_alternative, reverse=True)
        best = alternatives[0]

        # Find what we couldn't afford (for logging)
        unaffordable_ships = []
        for card in hand_list:
            if not card.blueprint_id:
                continue
            metadata = get_card(card.blueprint_id)
            if metadata and card_type == "starship" and metadata.is_starship:
                base_cost = metadata.deploy_value or 0
                deploy_reduction = metadata.parsed.deploy_reduction if hasattr(metadata, 'parsed') else 0
                effective_cost = max(0, base_cost - deploy_reduction)
                if effective_cost > max_cost:
                    unaffordable_ships.append((metadata.title, effective_cost))

        if unaffordable_ships:
            expensive_str = ', '.join(f"{name}({cost})" for name, cost in unaffordable_ships)
            logger.info(f"🔄 ALTERNATIVE: Can't afford [{expensive_str}], "
                       f"using {best['name']} ({best['cost']}) instead")
        else:
            logger.info(f"🔄 ALTERNATIVE: Found {best['name']} ({best['cost']}) as {card_type}")

        return best

    def _get_all_deployable_cards(self, board_state) -> List[Dict]:
        """Get all cards we can deploy with their metadata.

        Respects SWCCG uniqueness rules:
        - Unique cards (• prefix) can only have 1 copy on the entire board
        - If a unique card is already in play, don't include copies from hand
        - If multiple copies of a unique card are in hand, only include 1
        """
        from .card_loader import get_card

        deployable = []
        # Reserve force for battle effects (configurable), but never go negative
        available_force = max(0, board_state.force_pile - self.battle_force_reserve)

        # === UNIQUENESS TRACKING ===
        # Track unique card titles actually deployed on the board (our side only)
        # IMPORTANT: cards_in_play contains ALL cards including hand - filter by zone!
        unique_titles_on_board: Set[str] = set()
        my_player = getattr(board_state, 'my_player_name', None)

        if hasattr(board_state, 'cards_in_play'):
            for card_id, card_in_play in board_state.cards_in_play.items():
                # Only check our own cards
                if card_in_play.owner != my_player:
                    continue
                # Only check cards that are ON THE BOARD (not in hand, not in piles)
                # AT_LOCATION = deployed at a location
                # ATTACHED = attached to another card (also on board)
                card_zone = getattr(card_in_play, 'zone', '')
                if card_zone not in ('AT_LOCATION', 'ATTACHED'):
                    continue
                # Get metadata to check uniqueness
                if card_in_play.blueprint_id:
                    card_meta = get_card(card_in_play.blueprint_id)
                    if card_meta and card_meta.is_unique:
                        unique_titles_on_board.add(card_meta.title)

        # Track unique card titles we've already added from hand
        unique_titles_in_plan: Set[str] = set()

        # Log unique cards already on board (helps explain why cards from hand can't deploy)
        if unique_titles_on_board:
            logger.info(f"   📋 Unique cards already on board: {sorted(unique_titles_on_board)}")

        # DEBUG: Log how many cards we're iterating over
        hand_list = list(board_state.cards_in_hand)  # Materialize to get count
        logger.info(f"   🔍 _get_all_deployable_cards: {len(hand_list)} cards in hand, {available_force} force available")
        cards_added = 0  # Counter for debugging

        for card in hand_list:
            if not card.blueprint_id:
                logger.info(f"   ⏭️ Skip card: no blueprint_id (title={getattr(card, 'card_title', '?')})")
                continue

            metadata = get_card(card.blueprint_id)
            if not metadata:
                logger.info(f"   ⏭️ Skip {card.blueprint_id}: no metadata found")
                continue

            # Debug: log what we're processing (at DEBUG level unless issues)
            logger.debug(f"   📋 Processing {metadata.title}: is_char={metadata.is_character}, "
                        f"is_ship={metadata.is_starship}, is_veh={metadata.is_vehicle}, "
                        f"deploy={metadata.deploy_value}, power={metadata.power_value}")

            # === UNIQUENESS CHECK ===
            # Skip if this unique card is already on the board
            if metadata.is_unique and metadata.title in unique_titles_on_board:
                logger.info(f"   ⏭️ Skip {metadata.title}: unique card already on board")
                continue

            # Skip if we already have this unique card in our deployable list
            if metadata.is_unique and metadata.title in unique_titles_in_plan:
                logger.info(f"   ⏭️ Skip {metadata.title}: duplicate unique in hand")
                continue

            # Calculate effective deploy cost (factoring in gametext reductions)
            base_deploy_cost = metadata.deploy_value or 0
            deploy_reduction = metadata.parsed.deploy_reduction if hasattr(metadata, 'parsed') else 0
            deploy_cost = max(0, base_deploy_cost - deploy_reduction)

            if deploy_cost > available_force:
                logger.info(f"   ⏭️ Skip {metadata.title}: too expensive ({deploy_cost} > {available_force})")
                continue

            # Check if this is an unpiloted vehicle/starship (0 effective power without pilot)
            has_permanent_pilot = getattr(metadata, 'has_permanent_pilot', False)
            base_power = metadata.power_value or 0

            # Effective power: unpiloted vehicles/starships have 0 power until piloted
            is_unpiloted_craft = (metadata.is_starship or metadata.is_vehicle) and not has_permanent_pilot
            effective_power = 0 if is_unpiloted_craft else base_power

            # Pure pilots (pilot but not warrior) are best deployed aboard ships
            is_warrior = metadata.is_warrior if hasattr(metadata, 'is_warrior') else False
            is_pure_pilot = metadata.is_pilot and not is_warrior

            # Weapon target type info
            weapon_target_type = getattr(metadata, 'weapon_target_type', None)
            is_targeted_weapon = getattr(metadata, 'is_targeted_weapon', False)
            is_standalone_weapon = getattr(metadata, 'is_standalone_weapon', False)

            # Deploy restriction systems (e.g., ["Tatooine"] for Jawas)
            deploy_restrictions = getattr(metadata, 'deploy_restriction_systems', []) or []

            deployable.append({
                'card_id': card.card_id,
                'blueprint_id': card.blueprint_id,
                'name': metadata.title,
                'power': effective_power,  # Use effective power (0 for unpiloted)
                'base_power': base_power,  # Store base power for reference
                'cost': deploy_cost,  # Effective cost after reductions
                'base_cost': base_deploy_cost,  # Original cost for reference
                'deploy_reduction': deploy_reduction,  # Amount reduced by gametext
                'is_unique': metadata.is_unique,
                'is_location': metadata.is_location,
                'is_character': metadata.is_character,
                'is_starship': metadata.is_starship,
                'is_vehicle': metadata.is_vehicle,
                'is_pilot': metadata.is_pilot,
                'is_warrior': is_warrior,
                'is_pure_pilot': is_pure_pilot,
                'pilot_adds_power': getattr(metadata, 'pilot_adds_power', 1) if metadata.is_pilot else 0,
                'is_weapon': metadata.is_weapon,
                'is_device': metadata.is_device,
                'has_permanent_pilot': has_permanent_pilot,
                'needs_pilot': is_unpiloted_craft,
                # Weapon-specific fields
                'weapon_target_type': weapon_target_type,  # "character", "vehicle", "starship", or None
                'is_targeted_weapon': is_targeted_weapon,  # Needs to attach to a target
                'is_standalone_weapon': is_standalone_weapon,  # Automated/Artillery - no target needed
                'is_character_weapon': getattr(metadata, 'is_character_weapon', False),  # Deploys only on specific characters
                'matching_weapon': getattr(metadata, 'matching_weapon', []),  # List of character names weapon can deploy on
                # Deploy restriction systems (empty list = can deploy anywhere)
                'deploy_restriction_systems': deploy_restrictions,
                # Parsed gametext abilities for better evaluation
                'has_attrition_immunity': metadata.has_attrition_immunity,
                'immune_attrition_threshold': metadata.immune_attrition_threshold,
                'draws_extra_destiny': metadata.draws_extra_destiny,
                'force_drain_bonus': metadata.parsed.force_drain_bonus if hasattr(metadata, 'parsed') else 0,
            })

            cards_added += 1

            # Track unique cards we've added (to prevent duplicates from hand)
            if metadata.is_unique:
                unique_titles_in_plan.add(metadata.title)

        # DEBUG: Warn if we got no deployable cards but hand wasn't empty
        if len(hand_list) > 0 and len(deployable) == 0:
            logger.warning(f"   ⚠️ _get_all_deployable_cards: Hand had {len(hand_list)} cards but 0 are deployable!")
        else:
            logger.info(f"   ✅ _get_all_deployable_cards: {cards_added} deployable cards from {len(hand_list)} in hand")

        return deployable

    def _filter_cards_for_location(self, cards: List[Dict], location_name: str) -> List[Dict]:
        """
        Filter cards to only those that can deploy to a specific location.

        Cards with "Deploys only on <System>" restrictions can only deploy to
        locations in that system (e.g., Jawas can only go to Tatooine sites).

        Args:
            cards: List of card dicts from _get_all_deployable_cards
            location_name: Name of the target location (e.g., "•Tatooine: Mos Eisley")

        Returns:
            Filtered list of cards that can deploy to this location
        """
        filtered = []
        for card in cards:
            restrictions = card.get('deploy_restriction_systems', [])
            if not restrictions:
                # No restriction - can deploy anywhere
                filtered.append(card)
                continue

            # Card has restrictions - check if location matches any allowed system
            loc_clean = location_name.lstrip('•').strip()
            can_deploy = False

            for system in restrictions:
                system_lower = system.lower()
                loc_lower = loc_clean.lower()

                # Check if location is in the restricted system
                if loc_lower.startswith(system_lower):
                    can_deploy = True
                    break
                if ':' in loc_clean:
                    loc_system = loc_clean.split(':')[0].strip().lower()
                    if loc_system == system_lower:
                        can_deploy = True
                        break

            if can_deploy:
                filtered.append(card)
            else:
                logger.debug(f"   🚫 {card['name']} restricted to {restrictions}, skipping {location_name}")

        return filtered

    def _analyze_locations(self, board_state, deployable_power: int = 0) -> List[LocationAnalysis]:
        """
        Analyze all locations on the board.

        Args:
            board_state: Current board state
            deployable_power: Total power we could deploy this turn
        """
        locations = []
        seen_card_ids = set()  # Track to avoid duplicates

        if not hasattr(board_state, 'locations') or not board_state.locations:
            return locations

        for idx, loc in enumerate(board_state.locations):
            if not loc:
                continue

            # Skip duplicates (same card_id already processed)
            card_id = getattr(loc, 'card_id', '')
            if card_id and card_id in seen_card_ids:
                continue
            if card_id:
                seen_card_ids.add(card_id)

            # Get location name - prefer site_name, fall back to system_name or blueprint_id
            site_name = getattr(loc, 'site_name', '')
            system_name = getattr(loc, 'system_name', '')
            blueprint_id = getattr(loc, 'blueprint_id', '')
            loc_name = site_name or system_name or blueprint_id or 'Unknown'

            # Debug: log what we're getting if name resolution fails
            if loc_name == 'Unknown':
                logger.warning(f"   ⚠️ Location {idx} has no name: site='{site_name}', system='{system_name}', bp='{blueprint_id}'")

            # Check interior/exterior from card metadata (for vehicle filtering)
            from .card_loader import get_card
            loc_metadata = get_card(blueprint_id) if blueprint_id else None

            # Determine if ground (site) or space (system) from card metadata
            # RULE: Systems have sub_type="System" and NO interior/exterior icons
            #       Sites have sub_type="Site" and ALWAYS have interior and/or exterior icons
            is_interior = False
            is_exterior = False
            loc_is_space = False
            loc_is_ground = False

            if loc_metadata:
                # Check sub_type first - most reliable
                sub_type = getattr(loc_metadata, 'sub_type', '') or ''
                if sub_type.lower() == 'system':
                    loc_is_space = True
                    loc_is_ground = False
                elif sub_type.lower() == 'site':
                    loc_is_ground = True
                    loc_is_space = False
                    # Sites always have interior and/or exterior
                    is_interior = loc_metadata.is_interior
                    is_exterior = loc_metadata.is_exterior
                else:
                    # Fallback: check icons
                    # If has interior or exterior icon, it's a site (ground)
                    if loc_metadata.is_interior or loc_metadata.is_exterior:
                        loc_is_ground = True
                        is_interior = loc_metadata.is_interior
                        is_exterior = loc_metadata.is_exterior
                    else:
                        # No interior/exterior = system (space)
                        loc_is_space = True

            analysis = LocationAnalysis(
                card_id=card_id,
                name=loc_name,
                is_ground=loc_is_ground,
                is_space=loc_is_space,
                is_site=loc_is_ground,  # Sites are ground locations (have interior/exterior)
                location_index=idx,
            )

            # Store interior/exterior for vehicle filtering
            analysis.is_interior = is_interior
            analysis.is_exterior = is_exterior

            # Get power from board_state (uses array index, same as admin panel)
            # Note: board_state power values are authoritative - no need to recalculate from cards
            raw_my_power = board_state.my_power_at_location(idx) if hasattr(board_state, 'my_power_at_location') else 0
            raw_their_power = board_state.their_power_at_location(idx) if hasattr(board_state, 'their_power_at_location') else 0

            analysis.my_power = max(0, raw_my_power)
            analysis.their_power = max(0, raw_their_power)
            analysis.i_control = getattr(loc, 'i_control', False)
            analysis.they_control = getattr(loc, 'they_control', False)
            analysis.contested = analysis.my_power > 0 and analysis.their_power > 0

            # Get force icons from card metadata
            # Icons on the card are what each side can control:
            # - light_side_icons = icons Light side controls when they control location
            # - dark_side_icons = icons Dark side controls when they control location
            my_side = getattr(board_state, 'my_side', 'light') or 'light'
            if loc_metadata:
                if my_side.lower() == 'light':
                    analysis.my_icons = loc_metadata.light_side_icons or 0
                    analysis.their_icons = loc_metadata.dark_side_icons or 0
                else:
                    analysis.my_icons = loc_metadata.dark_side_icons or 0
                    analysis.their_icons = loc_metadata.light_side_icons or 0
            else:
                # Fallback to LocationInPlay data (usually empty)
                analysis.my_icons = self._parse_icon_string(getattr(loc, 'my_icons', 0))
                analysis.their_icons = self._parse_icon_string(getattr(loc, 'their_icons', 0))

            # =============================================================
            # BATTLE/FLEE ANALYSIS
            # Integrate with battle evaluator logic to avoid wasted deploys
            # =============================================================
            power_diff = analysis.power_differential

            # BATTLE OPPORTUNITY CHECK FIRST: Can we win by reinforcing?
            # This must happen BEFORE flee decision so we don't flee winnable battles
            can_win_with_reinforcements = False
            potential_diff = 0
            if analysis.contested and deployable_power > 0:
                potential_power = analysis.my_power + deployable_power
                potential_diff = potential_power - analysis.their_power

                # If we can WIN (even by 1), don't flee - reinforce instead!
                if potential_diff >= 0:
                    can_win_with_reinforcements = True
                    logger.info(f"   💪 {analysis.name}: CAN WIN with reinforcements ({analysis.my_power}+{deployable_power}={potential_power} vs {analysis.their_power}, diff=+{potential_diff})")

                if potential_diff >= get_battle_favorable_threshold():
                    analysis.can_flip_to_favorable = True
                    # This is a battle opportunity if we can also afford to battle
                    if board_state.force_pile >= 3:  # Need force for deploy + battle
                        analysis.is_battle_opportunity = True
                        logger.info(f"   ⚔️ {analysis.name}: BATTLE OPPORTUNITY (+{potential_diff} after deploy)")

            # RETREAT situation: We're at severe disadvantage AND can't win with reinforcements
            # Don't reinforce - we'll flee in move phase
            if analysis.contested and power_diff <= get_retreat_threshold() and not can_win_with_reinforcements:
                analysis.should_flee = True
                # Check if we can actually flee
                if hasattr(board_state, 'analyze_flee_options'):
                    flee_info = board_state.analyze_flee_options(idx, analysis.is_space)
                    if flee_info.get('can_flee') and flee_info.get('can_afford'):
                        logger.info(f"   🏃 {analysis.name}: should flee ({power_diff} diff, can't win even with +{deployable_power}), skip reinforce")
                    else:
                        # Can't flee - might need to reinforce anyway
                        analysis.should_flee = False
                        logger.info(f"   ⚠️ {analysis.name}: severe deficit ({power_diff}) but CAN'T FLEE")
            elif analysis.contested and power_diff <= get_retreat_threshold() and can_win_with_reinforcements:
                # We're behind but CAN win - DON'T flee, reinforce!
                analysis.should_flee = False
                logger.info(f"   🔄 {analysis.name}: behind ({power_diff}) but WILL REINFORCE TO WIN (+{potential_diff} after deploy)")

            locations.append(analysis)

        return locations

    def _log_opponent_board_summary(self, board_state, locations: List['LocationAnalysis']):
        """
        Log a summary of opponent's board state for strategy analysis.

        This helps track opponent patterns and prepare counter-strategies.
        """
        if not hasattr(board_state, 'cards_in_play') or not board_state.cards_in_play:
            return

        opponent_name = getattr(board_state, 'opponent_name', 'Opponent')
        my_player_name = getattr(board_state, 'my_player_name', '')

        # Collect opponent cards by location
        opponent_by_location = {}  # location_name -> list of (card_name, power, card_type)
        total_opponent_power = 0
        opponent_card_count = 0

        for card_id, card in board_state.cards_in_play.items():
            if card.owner == my_player_name:
                continue  # Skip our cards

            if card.zone != "AT_LOCATION":
                continue

            # Get card metadata
            card_meta = get_card(card.blueprint_id) if card.blueprint_id else None
            card_name = card.card_title or card.blueprint_id or "Unknown"
            power = card_meta.power_value if card_meta else 0
            card_type = card_meta.card_type if card_meta else "Unknown"

            # Find location name
            loc_name = f"location_{card.location_index}"
            if 0 <= card.location_index < len(board_state.locations):
                loc = board_state.locations[card.location_index]
                loc_name = loc.site_name or loc.system_name or loc_name

            if loc_name not in opponent_by_location:
                opponent_by_location[loc_name] = []
            opponent_by_location[loc_name].append((card_name, power, card_type))
            total_opponent_power += power
            opponent_card_count += 1

        # Log summary
        if opponent_card_count > 0:
            logger.info(f"   👁️ OPPONENT BOARD ({opponent_name}): {opponent_card_count} cards, {total_opponent_power} total power")
            for loc_name, cards in sorted(opponent_by_location.items()):
                loc_power = sum(p for _, p, _ in cards)
                card_list = ", ".join(f"{n}({p})" for n, p, _ in cards)
                logger.info(f"      - {loc_name}: {loc_power} power [{card_list}]")

    def get_card_score(self, blueprint_id: str, current_force: int = 0,
                       available_blueprint_ids: Optional[List[str]] = None) -> Tuple[float, str]:
        """
        Get the score for a card based on whether it's in the plan.

        Args:
            blueprint_id: Card blueprint ID to score
            current_force: Current force pile (for extra actions check)
            available_blueprint_ids: List of blueprint IDs that GEMP is offering
                                     Used for deployment order fallback

        Returns (score, reason)
        """
        if not self.current_plan:
            return (0.0, "No plan available")

        instruction = self.current_plan.get_instruction_for_card(blueprint_id)
        if instruction:
            # Card is in the plan - check deployment ORDER first
            # Locations -> Ships/Vehicles -> Characters
            should_deploy_now, order_reason = self.current_plan.should_deploy_card_now(
                blueprint_id, available_blueprint_ids
            )

            if not should_deploy_now:
                # Card is in plan but should wait for higher-priority types
                logger.info(f"⏳ {instruction.card_name}: {order_reason}")
                return (-50.0, f"IN PLAN but waiting: {order_reason}")

            # Card is in the plan AND should deploy now - high score based on priority
            priority_bonus = (3 - instruction.priority) * 50  # Priority 0 = +150, 1 = +100, 2 = +50
            return (100.0 + priority_bonus, instruction.reason)
        else:
            # Card is NOT in the plan
            if self.current_plan.strategy == DeployStrategy.HOLD_BACK:
                # Reduced from -500 to -150 for better nuance
                return (-150.0, f"HOLD BACK: {self.current_plan.reason}")

            # Check if we can take extra actions
            # Plan complete + have force above reserve = allow extra actions
            # OR: Plan is stale (planned cards not available) - force_allow_extras flag
            #
            # UPDATED: Allow characters/vehicles/starships as extras when:
            # 1. Plan is complete
            # 2. Sufficient force available (can afford card)
            # 3. Give them a moderate score (less than planned cards, but positive)
            # This prevents wasting force when the planner was too conservative.
            from .card_loader import get_card
            card_meta = get_card(blueprint_id)
            is_major_card = False
            deploy_cost = 0
            card_power = 0
            if card_meta:
                is_major_card = (
                    card_meta.is_character or
                    card_meta.is_vehicle or
                    card_meta.is_starship
                )
                deploy_cost = card_meta.deploy_value or 0
                card_power = card_meta.power_value or 0

            if self.current_plan.allows_extra_actions(current_force):
                extra_budget = self.current_plan.get_extra_force_budget(current_force)
                if not is_major_card:
                    # Non-major cards (effects, interrupts, etc.) - allow freely
                    logger.info(f"🎁 Plan complete, allowing extra action (budget: {extra_budget} force)")
                    return (25.0, f"EXTRA ACTION (plan done, {extra_budget} force available)")
                elif extra_budget >= deploy_cost:
                    # Major card (character/ship/vehicle) - MUST meet strategic thresholds!
                    # The planner already decided not to include this card for a reason.
                    # Only allow if:
                    # 1. Card meets establish threshold (power >= 4 AND ability >= 4), OR
                    # 2. We have existing presence to consolidate with
                    min_establish = get_min_establish_power()
                    card_type = card_meta.card_type if card_meta else "card"
                    card_ability = card_meta.ability_value if card_meta else 0

                    # To establish ALONE safely, need BOTH power and ability >= 4
                    # Without ability >= 4, opponent draws attrition destiny and crushes us
                    can_establish_alone = card_power >= min_establish and card_ability >= 4

                    if can_establish_alone:
                        # Strong enough AND high ability - safe to establish alone
                        power_score = min(50.0, card_power * 8.0)
                        logger.info(f"🎁 Plan complete, allowing extra {card_type} (power {card_power}, ability {card_ability} - safe)")
                        return (power_score, f"EXTRA {card_type.upper()} (plan done, {card_power} power/{card_ability} ability)")
                    else:
                        # Weak character - only allow if we have presence to consolidate with
                        # This prevents the "spread thin" problem
                        has_presence = False
                        if self._board_state:
                            bs = self._board_state
                            for loc_idx, loc in enumerate(bs.locations or []):
                                if loc and loc.card_id:
                                    our_power = bs.my_power_at_location(loc_idx)
                                    if our_power > 0:
                                        has_presence = True
                                        break

                        if has_presence:
                            # We have presence somewhere - allow consolidation
                            power_score = min(30.0, card_power * 6.0)
                            logger.info(f"🎁 Plan complete, allowing weak {card_type} to consolidate (power {card_power}, ability {card_ability})")
                            return (power_score, f"EXTRA {card_type.upper()} (consolidate with existing presence)")
                        else:
                            # No presence and risky card - reject!
                            # This is what the planner decided, respect it
                            if card_power < min_establish:
                                reason = f"{card_power} power < {min_establish} threshold"
                            else:
                                reason = f"ability {card_ability} < 4 (vulnerable to attrition)"
                            logger.info(f"🚫 Extra {card_type} rejected - {reason}, no presence to consolidate")
                            return (-100.0, f"Not in plan ({card_type} can't establish alone: {reason})")
                else:
                    # Can't afford this major card
                    card_type = card_meta.card_type if card_meta else "unknown"
                    logger.info(f"🚫 Extra action rejected - {card_type} too expensive ({deploy_cost} > {extra_budget})")
                    return (-100.0, f"Not in plan ({card_type} too expensive for extra budget)")
            elif getattr(self.current_plan, 'force_allow_extras', False):
                # Plan is stale - planned cards aren't available anymore
                # Allow any card as substitute since the original plan can't be executed
                card_type = card_meta.card_type if card_meta else "card"
                power_score = min(40.0, card_power * 6.0) if is_major_card else 25.0
                logger.info(f"🎁 Stale plan, allowing {card_type} as substitute (power: {card_power})")
                return (power_score, f"SUBSTITUTE ({card_type} replacing unavailable planned card)")
            else:
                return (-100.0, "Not in deployment plan")

    def should_hold_back(self) -> bool:
        """Check if current plan is to hold back"""
        if not self.current_plan:
            return False
        return self.current_plan.strategy == DeployStrategy.HOLD_BACK

    def has_favorable_battle_setup(self, board_state: 'BoardState') -> bool:
        """
        Check if we have a favorable battle setup at any contested location.

        If we have a power advantage (FAVORABLE or CRUSH threat level), we should
        commit to battling rather than spending force on extra deployments.

        Returns True if we should skip extra actions to battle instead.
        """
        if not board_state or not board_state.locations:
            return False

        # Get thresholds from config
        favorable_threshold = 4  # Default get_battle_favorable_threshold()

        for loc_idx, loc in enumerate(board_state.locations):
            if loc is None or not loc.card_id:
                continue

            # Check if contested (both players have presence)
            my_power = board_state.my_power_at_location(loc_idx)
            their_power = board_state.their_power_at_location(loc_idx)

            if my_power > 0 and their_power > 0:
                # Contested location - check power differential
                power_diff = my_power - their_power

                if power_diff >= favorable_threshold:
                    logger.info(f"⚔️ Favorable battle at loc {loc_idx}: {my_power} vs {their_power} (+{power_diff}) - skip extras!")
                    return True

        return False

    def record_deployment(self, blueprint_id: str):
        """Record that we made a deployment"""
        if self.current_plan:
            self.current_plan.deployments_made += 1
            # Remove from instructions list
            self.current_plan.instructions = [
                i for i in self.current_plan.instructions
                if i.card_blueprint_id != blueprint_id
            ]

    def get_plan_summary(self) -> Dict:
        """
        Get a summary of the current plan for display in admin UI.

        Returns a dict with:
        - strategy: The overall strategy name
        - reason: Why this strategy was chosen
        - total_cost: Total Force cost of planned deployments
        - total_power: Total power being deployed
        - instructions: List of (card_name, target, reason) tuples
        - force_remaining: How much Force will be left after deploys
        """
        if not self.current_plan:
            return {
                'strategy': 'NO PLAN',
                'reason': 'No plan created yet',
                'total_cost': 0,
                'total_power': 0,
                'instructions': [],
                'force_remaining': 0,
            }

        plan = self.current_plan
        total_cost = sum(i.deploy_cost for i in plan.instructions)
        total_power = sum(i.power_contribution for i in plan.instructions)

        instructions = []
        for inst in plan.instructions:
            target = inst.target_location_name or "table"
            instructions.append({
                'card': inst.card_name,
                'target': target,
                'reason': inst.reason,
                'power': inst.power_contribution,
                'cost': inst.deploy_cost,
            })

        return {
            'strategy': plan.strategy.value.upper(),
            'reason': plan.reason,
            'total_cost': total_cost,
            'total_power': total_power,
            'force_available': plan.total_force_available,
            'force_remaining': plan.total_force_available - total_cost,
            'instructions': instructions,
        }

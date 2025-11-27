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

logger = logging.getLogger(__name__)

# Battle threshold - power advantage needed to feel comfortable battling
BATTLE_FAVORABLE_THRESHOLD = 4

# Thresholds for battle/flee decisions (from battle_evaluator.py)
RETREAT_THRESHOLD = -6  # Power diff <= this = should flee, don't reinforce
DANGEROUS_THRESHOLD = -2  # Power diff <= this = dangerous, need serious reinforcement


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


class DeployPhasePlanner:
    """
    Creates comprehensive deployment plans for the entire phase.

    Usage:
    1. Call create_plan() at start of deploy phase
    2. Evaluator checks plan.should_deploy_card() for each option
    3. High score if card is in plan, low score if not
    """

    def __init__(self, deploy_threshold: int = 6):
        self.deploy_threshold = deploy_threshold
        self.current_plan: Optional[DeploymentPlan] = None
        self._last_phase: str = ""

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
        must_exceed: bool = False
    ) -> Tuple[List[Dict], int, int]:
        """
        Find the optimal combination of cards to deploy within budget.

        Uses efficiency-based selection: prioritize cards that give the most
        power per Force spent, while still achieving the power goal.

        Args:
            cards: List of card dicts with 'power', 'cost', 'name', etc.
            budget: Maximum Force we can spend
            power_goal: Power we're trying to reach
            must_exceed: If True, we need power > goal (for beating opponent)

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
            return self._find_optimal_brute_force(affordable, budget, power_goal, must_exceed)

        # For larger hands, use greedy efficiency-based approach
        return self._find_optimal_greedy(affordable, budget, power_goal, must_exceed)

    def _find_optimal_brute_force(
        self,
        cards: List[Dict],
        budget: int,
        power_goal: int,
        must_exceed: bool
    ) -> Tuple[List[Dict], int, int]:
        """
        Try all combinations to find the best one.

        Best = achieves power goal with minimum cost, or gets closest to goal.
        """
        from itertools import combinations

        best_combo = []
        best_power = 0
        best_cost = float('inf')
        best_achieves_goal = False

        # Try all possible subset sizes
        for size in range(1, len(cards) + 1):
            for combo in combinations(cards, size):
                total_cost = sum(c['cost'] for c in combo)
                if total_cost > budget:
                    continue

                total_power = sum(c['power'] for c in combo)

                # Check if this achieves the goal
                if must_exceed:
                    achieves_goal = total_power > power_goal
                else:
                    achieves_goal = total_power >= power_goal

                # Prefer combos that achieve goal with minimum cost
                # If neither achieves goal, prefer more power
                if achieves_goal and not best_achieves_goal:
                    # First combo to achieve goal
                    best_combo = list(combo)
                    best_power = total_power
                    best_cost = total_cost
                    best_achieves_goal = True
                elif achieves_goal and best_achieves_goal:
                    # Both achieve goal - prefer cheaper
                    if total_cost < best_cost:
                        best_combo = list(combo)
                        best_power = total_power
                        best_cost = total_cost
                elif not achieves_goal and not best_achieves_goal:
                    # Neither achieves goal - prefer more power
                    if total_power > best_power:
                        best_combo = list(combo)
                        best_power = total_power
                        best_cost = total_cost

        return (best_combo, best_power, int(best_cost) if best_cost != float('inf') else 0)

    def _find_optimal_greedy(
        self,
        cards: List[Dict],
        budget: int,
        power_goal: int,
        must_exceed: bool
    ) -> Tuple[List[Dict], int, int]:
        """
        Greedy approach for larger hands: sort by efficiency (power/cost).
        """
        # Sort by efficiency (power per cost), with cost=0 cards first
        sorted_cards = sorted(
            cards,
            key=lambda c: (c['power'] / c['cost']) if c['cost'] > 0 else float('inf'),
            reverse=True
        )

        selected = []
        total_power = 0
        total_cost = 0
        remaining_budget = budget

        for card in sorted_cards:
            if card['cost'] <= remaining_budget:
                selected.append(card)
                total_power += card['power']
                total_cost += card['cost']
                remaining_budget -= card['cost']

                # Stop if we've achieved the goal
                if must_exceed and total_power > power_goal:
                    break
                elif not must_exceed and total_power >= power_goal:
                    break

        return (selected, total_power, total_cost)

    def create_plan(self, board_state) -> DeploymentPlan:
        """
        Create a comprehensive deployment plan for this phase.

        This is the MASTER PLAN that determines everything:
        - Which cards to deploy
        - Which location each goes to
        - What order to deploy them
        """
        from .card_loader import get_card

        # Check if we're in a new phase
        current_phase = getattr(board_state, 'current_phase', '')
        if current_phase != self._last_phase:
            self._last_phase = current_phase
            self.current_plan = None

        # Return existing plan if we have one
        if self.current_plan and self.current_plan.phase_started:
            return self.current_plan

        logger.info("📋 Creating comprehensive deployment plan...")

        # Get available force (reserve some for battle)
        total_force = board_state.force_pile
        force_reserved = 2  # Reserve for battle effects
        force_to_spend = max(0, total_force - force_reserved)

        logger.info(f"   Force: {total_force} total, {force_to_spend} for deploying (reserve {force_reserved})")

        # Initialize the plan
        plan = DeploymentPlan(
            strategy=DeployStrategy.HOLD_BACK,
            reason="Planning...",
            total_force_available=total_force,
            force_reserved_for_battle=force_reserved,
            force_to_spend=force_to_spend,
        )

        # Get all deployable cards from hand
        all_cards = self._get_all_deployable_cards(board_state)
        locations_in_hand = [c for c in all_cards if c['is_location']]
        characters = [c for c in all_cards if c['is_character'] and not c['is_location']]
        # IMPORTANT: Separate starships (space) from vehicles (ground)
        starships = [c for c in all_cards if c['is_starship'] and not c['is_vehicle']]
        vehicles = [c for c in all_cards if c['is_vehicle']]
        weapons = [c for c in all_cards if c.get('is_weapon') or c.get('is_device')]
        # Pilots can go aboard unpiloted vehicles/starships
        pilots = [c for c in characters if c['is_pilot']]

        logger.info(f"   Hand: {len(locations_in_hand)} locations, {len(characters)} characters, "
                    f"{len(starships)} starships, {len(vehicles)} vehicles, {len(weapons)} weapons")

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
            logger.info(f"      - {loc.name}: {loc_type}, my={loc.my_power}, their={loc.their_power}, icons={loc.their_icons}")

        # =================================================================
        # STEP 1: DEPLOY LOCATIONS FIRST
        # This is CRITICAL - locations open new tactical options
        # =================================================================
        force_remaining = force_to_spend
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

        # =================================================================
        # STEP 2: IDENTIFY CONTESTED LOCATIONS (reduce harm)
        # Locations where we have characters but are at power deficit
        # CRITICAL: Skip locations where we should FLEE
        # =================================================================
        contested = [
            loc for loc in locations
            if loc.my_power > 0 and loc.their_power > 0 and loc.power_differential < 0
            and not loc.should_flee  # DON'T REINFORCE IF WE'RE FLEEING
        ]

        # Log any skipped flee locations
        flee_locs = [loc for loc in locations if loc.should_flee]
        for loc in flee_locs:
            logger.info(f"   🏃 Skip reinforce at {loc.name}: will flee ({loc.power_differential} diff)")

        # Sort: Battle opportunities first, then by severity (biggest deficit first)
        # Battle opportunities are higher priority because we can flip and win
        contested.sort(key=lambda x: (not x.is_battle_opportunity, x.power_differential))

        # =================================================================
        # STEP 3: IDENTIFY UNCONTESTED TARGETS (gain ground)
        # Locations with opponent icons where we can establish
        # CRITICAL: Characters can only deploy to GROUND locations (is_ground=True)
        #           Starships can only deploy to SPACE locations (is_space=True)
        # =================================================================
        # Ground locations for characters
        uncontested_ground = [
            loc for loc in locations
            if loc.their_icons > 0  # Has opponent icons to deny
            and loc.my_power == 0  # We're not there yet
            and loc.is_ground  # Characters can only go to ground locations
        ]
        # Space locations for ships
        uncontested_space = [
            loc for loc in locations
            if loc.their_icons > 0
            and loc.my_power == 0
            and loc.is_space
        ]

        # Sort by icons (most valuable first)
        uncontested_ground.sort(key=lambda x: x.their_icons, reverse=True)
        uncontested_space.sort(key=lambda x: x.their_icons, reverse=True)

        # CONCENTRATION STRATEGY: Don't spread thin!
        # Pick at most 1-2 locations to establish at, and deploy meaningful force there
        # Instead of 3 power to 4 locations, do 6+ power to 2 locations
        MAX_ESTABLISH_LOCATIONS = 2
        uncontested_ground = uncontested_ground[:MAX_ESTABLISH_LOCATIONS]
        uncontested_space = uncontested_space[:MAX_ESTABLISH_LOCATIONS]

        logger.debug(f"   Ground targets: {[loc.name for loc in uncontested_ground]}")
        logger.debug(f"   Space targets: {[loc.name for loc in uncontested_space]}")

        # =================================================================
        # STEP 4: ALLOCATE CHARACTERS TO CONTESTED LOCATIONS
        # Priority: Reinforce locations where we're losing
        # Goal: Reach power parity or advantage
        # Uses optimal combination finding to maximize power within budget
        # =================================================================
        available_chars = characters.copy()

        for loc in contested:
            if not available_chars or force_remaining <= 0:
                break

            deficit = abs(loc.power_differential)
            power_needed = deficit + BATTLE_FAVORABLE_THRESHOLD  # Want to reach favorable

            battle_tag = "BATTLE OPP" if loc.is_battle_opportunity else "Contested"
            logger.info(f"   ⚔️ {battle_tag}: {loc.name} ({loc.my_power} vs {loc.their_power}, need +{power_needed})")

            # Find OPTIMAL combination of cards within budget
            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                available_chars,
                force_remaining,
                power_needed,
                must_exceed=False  # >= is fine for reinforcement
            )

            if not cards_for_location:
                logger.info(f"   ⏭️ No affordable cards for {loc.name}")
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
                if loc.is_battle_opportunity:
                    reason = f"BATTLE OPP at {loc.name} (deploy +{char['power']}, then fight!)"
                    priority = 1  # Same as reinforce but flagged for battle
                else:
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
                emoji = "⚔️" if loc.is_battle_opportunity else "🛡️"
                logger.info(f"   {emoji} Plan: Deploy {char['name']} ({char['power']} power, {char['cost']} cost) to {loc.name}")

        # =================================================================
        # STEP 5: ALLOCATE TO UNCONTESTED ICON LOCATIONS
        # Characters go to GROUND locations, Ships go to SPACE locations
        # Require minimum power threshold to establish meaningful presence
        # =================================================================
        MIN_ESTABLISH_POWER = self.deploy_threshold  # From config, default 6

        # =================================================================
        # STEP 5-PRE: RESERVE PILOTS FOR UNPILOTED VEHICLES
        # Before deploying characters, check if we should save pilots for vehicles
        # This prevents pilots from being "wasted" as regular character deploys
        # =================================================================
        available_vehicles = vehicles.copy()
        unpiloted_vehicles = [v for v in available_vehicles if v.get('needs_pilot')]
        reserved_pilots = []  # Pilots reserved for vehicle combos

        if unpiloted_vehicles:
            # Find pilots in our character list
            pilots_in_hand = [c for c in available_chars if c['is_pilot']]

            # Find EXTERIOR ground locations where vehicles can attack
            vehicle_targets = [
                loc for loc in locations
                if loc.is_ground and loc.is_exterior
                and (loc.their_power > 0 or loc.their_icons > 0)
                and loc.my_power == 0
            ]

            # If we have good vehicle targets and pilots, reserve pilots for vehicles
            if vehicle_targets and pilots_in_hand:
                logger.info(f"   🔧 Reserving pilots for {len(unpiloted_vehicles)} unpiloted vehicles...")
                for vehicle in unpiloted_vehicles:
                    if not pilots_in_hand:
                        break
                    # Reserve the best pilot (highest power)
                    best_pilot = max(pilots_in_hand, key=lambda p: p['power'])
                    reserved_pilots.append(best_pilot)
                    pilots_in_hand.remove(best_pilot)
                    available_chars.remove(best_pilot)
                    logger.info(f"   🔧 Reserved {best_pilot['name']} for {vehicle['name']}")

        # STEP 5A: Deploy CHARACTERS to GROUND locations
        # Target locations where opponent has presence OR icons (be aggressive!)
        char_ground_targets = [
            loc for loc in locations
            if loc.is_ground and (loc.their_power > 0 or loc.their_icons > 0) and loc.my_power == 0
        ]
        # Sort by opponent power (attack weakest first)
        char_ground_targets.sort(key=lambda x: x.their_power)

        # Limit to top targets to concentrate forces
        char_ground_targets = char_ground_targets[:MAX_ESTABLISH_LOCATIONS]

        logger.info(f"   🎯 Ground targets for characters: {[loc.name for loc in char_ground_targets]}")

        for loc in char_ground_targets:
            if not available_chars or force_remaining <= 0:
                break

            # Find optimal combination - require enough to beat opponent
            # If opponent has power, we need to beat it; otherwise use minimum threshold
            power_goal = max(MIN_ESTABLISH_POWER, loc.their_power + 1) if loc.their_power > 0 else MIN_ESTABLISH_POWER

            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                available_chars,
                force_remaining,
                power_goal,
                must_exceed=False  # >= power_goal is fine
            )

            # Only deploy if we can beat opponent's power (or reach minimum for empty locations)
            min_needed = loc.their_power + 1 if loc.their_power > 0 else MIN_ESTABLISH_POWER
            if not cards_for_location or power_allocated < min_needed:
                logger.info(f"   ⏭️ Skip {loc.name}: can't beat {loc.their_power} power (got {power_allocated})")
                continue

            # Log the selected combination
            card_names = [c['name'] for c in cards_for_location]
            logger.info(f"   📊 Optimal combo for {loc.name}: {card_names} = {power_allocated} power for {cost_used} Force")

            # Update remaining budget and available cards
            force_remaining -= cost_used
            for card in cards_for_location:
                if card in available_chars:
                    available_chars.remove(card)

            # Create instructions
            for char in cards_for_location:
                plan.instructions.append(DeploymentInstruction(
                    card_blueprint_id=char['blueprint_id'],
                    card_name=char['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=2,  # Lower priority (after reinforcements)
                    reason=f"Attack {loc.name} (beat {loc.their_power} power)",
                    power_contribution=char['power'],
                    deploy_cost=char['cost'],
                ))
                logger.info(f"   🚀 Plan: Deploy {char['name']} ({char['power']} power, {char['cost']} cost) to {loc.name}")

        # STEP 5B: Deploy STARSHIPS to SPACE locations (NOT vehicles - they go to ground)
        # Note: Unpiloted starships have 0 effective power - need pilot pairing
        available_starships = starships.copy()

        # Log any unpiloted starships that need pilots
        unpiloted_starships = [s for s in available_starships if s.get('needs_pilot')]
        if unpiloted_starships:
            names = [s['name'] for s in unpiloted_starships]
            logger.info(f"   ⚠️ Unpiloted starships (0 power without pilot): {names}")

        for loc in uncontested_space:
            if not available_starships or force_remaining <= 0:
                break

            power_goal = max(MIN_ESTABLISH_POWER, loc.their_power + 1)

            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                available_starships,
                force_remaining,
                power_goal,
                must_exceed=False
            )

            if not cards_for_location or power_allocated < MIN_ESTABLISH_POWER:
                logger.info(f"   ⏭️ Skip {loc.name}: can't reach {MIN_ESTABLISH_POWER} starship power (got {power_allocated})")
                continue

            card_names = [c['name'] for c in cards_for_location]
            logger.info(f"   📊 Optimal starship combo for {loc.name}: {card_names} = {power_allocated} power for {cost_used} Force")

            force_remaining -= cost_used
            for card in cards_for_location:
                if card in available_starships:
                    available_starships.remove(card)

            for ship in cards_for_location:
                plan.instructions.append(DeploymentInstruction(
                    card_blueprint_id=ship['blueprint_id'],
                    card_name=ship['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=2,
                    reason=f"Establish starship at {loc.name} ({loc.their_icons} icons)",
                    power_contribution=ship['power'],
                    deploy_cost=ship['cost'],
                ))
                logger.info(f"   🚀 Plan: Deploy {ship['name']} ({ship['power']} power, {ship['cost']} cost) to {loc.name}")

        # STEP 5C: Deploy VEHICLES + PILOTS to GROUND locations
        # Vehicles can't go to space! And unpiloted vehicles need pilots to have power.
        # NOTE: Pilots were reserved in STEP 5-PRE, so use reserved_pilots here
        # =================================================================
        # Create pilot+vehicle combos for unpiloted vehicles using reserved pilots
        piloted_combos = []  # List of {vehicle, pilot, combined_power, combined_cost}
        available_reserved = reserved_pilots.copy()

        if unpiloted_vehicles and available_reserved:
            logger.info(f"   🔧 Pairing {len(available_reserved)} reserved pilots with unpiloted vehicles...")
            for vehicle in unpiloted_vehicles[:]:  # Copy to allow modification
                if not available_reserved:
                    break
                # Pick best pilot (highest power)
                best_pilot = max(available_reserved, key=lambda p: p['power'])
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
        ground_targets = [
            loc for loc in locations
            if loc.is_ground and loc.is_exterior  # Vehicles need exterior
            and (loc.their_power > 0 or loc.their_icons > 0)  # Has opponent presence or icons
            and loc.my_power == 0  # We're not there yet
        ]
        # Sort by opponent power (attack weakest first for easy wins)
        ground_targets.sort(key=lambda x: x.their_power)

        # Log why locations were excluded
        interior_only = [loc.name for loc in locations if loc.is_ground and loc.is_interior and not loc.is_exterior]
        if interior_only:
            logger.info(f"   ⛔ Interior-only locations (no vehicles): {interior_only}")

        logger.info(f"   🎯 Ground targets for vehicles (exterior): {[loc.name for loc in ground_targets]}")

        # Deploy piloted combos (vehicle + pilot together)
        for loc in ground_targets:
            if not piloted_combos or force_remaining <= 0:
                break

            # Find best combo that can beat opponent's power
            power_needed = loc.their_power + 1
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

            power_needed = loc.their_power + 1

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
        # STEP 5D: PILE ON - Deploy additional cards to locations we're attacking
        # Once we've committed to a battle, spend remaining force to crush opponent
        # Keep only 2 force reserved for battle itself
        # =================================================================
        BATTLE_FORCE_RESERVE = 2
        attack_locations = set(inst.target_location_id for inst in plan.instructions if inst.target_location_id)

        if attack_locations and force_remaining > BATTLE_FORCE_RESERVE:
            logger.info(f"   💪 PILE ON: {force_remaining} force remaining, attacking {len(attack_locations)} locations")

            # Get location objects for our attack targets
            attack_locs = [loc for loc in locations if loc.card_id in attack_locations]

            # Sort by opponent power (pile on to hardest battles first)
            attack_locs.sort(key=lambda x: x.their_power, reverse=True)

            for loc in attack_locs:
                if force_remaining <= BATTLE_FORCE_RESERVE:
                    break

                # Deploy any remaining piloted vehicles here
                for vehicle in piloted_vehicles[:]:
                    if force_remaining <= BATTLE_FORCE_RESERVE:
                        break
                    if vehicle['cost'] <= force_remaining - BATTLE_FORCE_RESERVE:
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
                    for char in available_chars[:]:
                        if force_remaining <= BATTLE_FORCE_RESERVE:
                            break
                        if char['cost'] <= force_remaining - BATTLE_FORCE_RESERVE:
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
        # STEP 5E: DEPLOY WEAPONS if we have spare force (>= 2 remaining)
        # Priority: Attack locations we're deploying to, then existing presence
        # Weapons attach to characters at locations, so we need chars there
        # =================================================================
        MIN_FORCE_FOR_WEAPONS = 2
        if weapons and force_remaining >= MIN_FORCE_FOR_WEAPONS:
            logger.info(f"   🗡️ Checking weapons ({len(weapons)} available, {force_remaining} force remaining)")

            # Find locations where we have or WILL HAVE characters (for weapon targets)
            # Priority order: attack locations (we're deploying there) > existing presence
            attack_locs_for_weapons = [loc for loc in locations if loc.card_id in attack_locations]
            existing_presence = [loc for loc in locations if loc.my_power > 0 and loc.card_id not in attack_locations]

            # Combine in priority order - attack locations first!
            weapon_target_locs = attack_locs_for_weapons + existing_presence

            if weapon_target_locs:
                for weapon in weapons:
                    if force_remaining < weapon['cost']:
                        continue

                    # Find best location for this weapon
                    target_loc = weapon_target_locs[0] if weapon_target_locs else None
                    if not target_loc:
                        continue

                    # Add weapon to plan
                    is_attack_target = target_loc.card_id in attack_locations
                    reason = f"Arm character at {target_loc.name}" + (" for BATTLE!" if is_attack_target else "")

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
                    logger.info(f"   🗡️ Plan: Deploy {weapon['name']} (cost {weapon['cost']}) to {target_loc.name}")
            else:
                logger.info("   🗡️ No locations with our characters for weapon targets")

        # =================================================================
        # STEP 6: FINALIZE PLAN
        # =================================================================

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
            for i, inst in enumerate(plan.instructions):
                logger.info(f"   {i+1}. {inst.card_name} -> {inst.target_location_name or 'table'}: {inst.reason}")
        else:
            plan.strategy = DeployStrategy.HOLD_BACK
            plan.reason = "No good deployment options"
            logger.info(f"📋 FINAL PLAN: HOLD BACK - {plan.reason}")

        plan.phase_started = True
        plan.target_locations = locations
        self.current_plan = plan
        return plan

    def _get_all_deployable_cards(self, board_state) -> List[Dict]:
        """Get all cards we can deploy with their metadata"""
        from .card_loader import get_card

        deployable = []
        available_force = board_state.force_pile - 1

        for card in board_state.cards_in_hand:
            if not card.blueprint_id:
                continue

            metadata = get_card(card.blueprint_id)
            if not metadata:
                continue

            deploy_cost = metadata.deploy_value or 0
            if deploy_cost > available_force:
                continue

            # Check if this is an unpiloted vehicle/starship (0 effective power without pilot)
            has_permanent_pilot = getattr(metadata, 'has_permanent_pilot', False)
            base_power = metadata.power_value or 0

            # Effective power: unpiloted vehicles/starships have 0 power until piloted
            is_unpiloted_craft = (metadata.is_starship or metadata.is_vehicle) and not has_permanent_pilot
            effective_power = 0 if is_unpiloted_craft else base_power

            deployable.append({
                'card_id': card.card_id,
                'blueprint_id': card.blueprint_id,
                'name': metadata.title,
                'power': effective_power,  # Use effective power (0 for unpiloted)
                'base_power': base_power,  # Store base power for reference
                'cost': deploy_cost,
                'is_unique': metadata.is_unique,
                'is_location': metadata.is_location,
                'is_character': metadata.is_character,
                'is_starship': metadata.is_starship,
                'is_vehicle': metadata.is_vehicle,
                'is_pilot': metadata.is_pilot,
                'is_weapon': metadata.is_weapon,
                'is_device': metadata.is_device,
                'has_permanent_pilot': has_permanent_pilot,
                'needs_pilot': is_unpiloted_craft,
            })

        return deployable

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
                location_index=idx,
            )

            # Store interior/exterior for vehicle filtering
            analysis.is_interior = is_interior
            analysis.is_exterior = is_exterior

            # Get power from board_state methods
            # NOTE: Negative values are force icons, not power - treat as 0
            raw_my_power = board_state.my_power_at_location(idx) if hasattr(board_state, 'my_power_at_location') else 0
            raw_their_power = board_state.their_power_at_location(idx) if hasattr(board_state, 'their_power_at_location') else 0
            analysis.my_power = max(0, raw_my_power)  # Negative = icons, not power
            analysis.their_power = max(0, raw_their_power)
            analysis.i_control = getattr(loc, 'i_control', False)
            analysis.they_control = getattr(loc, 'they_control', False)
            analysis.contested = analysis.my_power > 0 and analysis.their_power > 0
            # Parse icon strings to integers (icons can be "2", "2*", etc.)
            analysis.my_icons = self._parse_icon_string(getattr(loc, 'my_icons', 0))
            analysis.their_icons = self._parse_icon_string(getattr(loc, 'their_icons', 0))

            # =============================================================
            # BATTLE/FLEE ANALYSIS
            # Integrate with battle evaluator logic to avoid wasted deploys
            # =============================================================
            power_diff = analysis.power_differential

            # RETREAT situation: We're at severe disadvantage
            # Don't reinforce - we'll flee in move phase
            if analysis.contested and power_diff <= RETREAT_THRESHOLD:
                analysis.should_flee = True
                # Check if we can actually flee
                if hasattr(board_state, 'analyze_flee_options'):
                    flee_info = board_state.analyze_flee_options(idx, analysis.is_space)
                    if flee_info.get('can_flee') and flee_info.get('can_afford'):
                        logger.info(f"   🏃 {analysis.name}: should flee ({power_diff} diff), skip reinforce")
                    else:
                        # Can't flee - might need to reinforce anyway
                        analysis.should_flee = False
                        logger.info(f"   ⚠️ {analysis.name}: severe deficit ({power_diff}) but CAN'T FLEE")

            # BATTLE OPPORTUNITY: We can flip to favorable with our deploy
            # If we deploy our available power, can we reach FAVORABLE?
            if analysis.contested and deployable_power > 0:
                potential_power = analysis.my_power + deployable_power
                potential_diff = potential_power - analysis.their_power

                if potential_diff >= BATTLE_FAVORABLE_THRESHOLD:
                    analysis.can_flip_to_favorable = True
                    # This is a battle opportunity if we can also afford to battle
                    if board_state.force_pile >= 3:  # Need force for deploy + battle
                        analysis.is_battle_opportunity = True
                        logger.info(f"   ⚔️ {analysis.name}: BATTLE OPPORTUNITY (+{potential_diff} after deploy)")

            locations.append(analysis)

        return locations

    def get_card_score(self, blueprint_id: str) -> Tuple[float, str]:
        """
        Get the score for a card based on whether it's in the plan.

        Returns (score, reason)
        """
        if not self.current_plan:
            return (0.0, "No plan available")

        instruction = self.current_plan.get_instruction_for_card(blueprint_id)
        if instruction:
            # Card is in the plan - high score based on priority
            priority_bonus = (3 - instruction.priority) * 50  # Priority 0 = +150, 1 = +100, 2 = +50
            return (100.0 + priority_bonus, instruction.reason)
        else:
            # Card is NOT in the plan - should not deploy
            if self.current_plan.strategy == DeployStrategy.HOLD_BACK:
                return (-500.0, f"HOLD BACK: {self.current_plan.reason}")
            else:
                return (-100.0, "Not in deployment plan")

    def should_hold_back(self) -> bool:
        """Check if current plan is to hold back"""
        if not self.current_plan:
            return False
        return self.current_plan.strategy == DeployStrategy.HOLD_BACK

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

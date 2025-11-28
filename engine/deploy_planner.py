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
        self._last_turn: int = -1

    def reset(self):
        """Reset planner state for a new game. Call this when game starts."""
        logger.info("📋 Deploy planner reset for new game")
        self.current_plan = None
        self._last_phase = ""
        self._last_turn = -1

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

        Best = achieves power goal with maximum power within budget.
        Once goal is achieved, prefer higher power (for scoring) over cheaper cost.
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

                # Selection priority:
                # 1. First combo that achieves goal
                # 2. Among goal-achievers, prefer HIGHER POWER (better score)
                # 3. If same power, prefer cheaper
                # 4. If neither achieves goal, prefer more power
                if achieves_goal and not best_achieves_goal:
                    # First combo to achieve goal
                    best_combo = list(combo)
                    best_power = total_power
                    best_cost = total_cost
                    best_achieves_goal = True
                elif achieves_goal and best_achieves_goal:
                    # Both achieve goal - prefer HIGHER POWER (better for scoring)
                    # If same power, prefer cheaper
                    if total_power > best_power or (total_power == best_power and total_cost < best_cost):
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

    def _score_plan(self, instructions: List[DeploymentInstruction], locations: List[LocationAnalysis]) -> float:
        """
        Score a deployment plan based on strategic value.

        Scoring factors (in priority order):
        1. CRUSHING opponents (beating them with power advantage) - HIGHEST priority
        2. Icons denied (their_icons at target locations)
        3. Power deployed - for winning battles
        4. Contesting opponent presence - denies force drains

        CRITICAL: Beating an opponent is ALWAYS more valuable than establishing
        at an empty location. Force drains only happen when you CONTROL, and
        you can't control if the opponent has presence.
        """
        if not instructions:
            return 0.0

        score = 0.0
        target_loc_ids = set()
        power_by_location = {}  # Track power going to each location

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

            # Power contribution
            score += inst.power_contribution * 2

            # Bonus for deploying to location with opponent presence
            if target_loc and target_loc.their_power > 0:
                score += 15  # Contesting is valuable

        # === CRUSH BONUS (CRITICAL - HIGHEST PRIORITY) ===
        # Beating opponents is MUCH more valuable than establishing at empty.
        # Add a massive bonus for plans that BEAT enemy power.
        for loc_id, our_power in power_by_location.items():
            for loc in locations:
                if loc.card_id == loc_id and loc.their_power > 0:
                    if our_power > loc.their_power:
                        # We BEAT them! This is the best outcome.
                        power_advantage = our_power - loc.their_power
                        # +50 base for any win, +10 per power advantage
                        crush_bonus = 50 + (power_advantage * 10)
                        score += crush_bonus
                        logger.debug(f"   💥 CRUSH BONUS at {loc.name}: +{crush_bonus} "
                                   f"({our_power} vs {loc.their_power})")
                    break

        # Icons denied (only count each location once)
        for loc_id in target_loc_ids:
            for loc in locations:
                if loc.card_id == loc_id:
                    score += loc.their_icons * 20  # Icons are still valuable
                    break

        return score

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

        # Find best location for this specific card
        best_loc = None
        for loc in ground_targets:
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

            power_goal = max(MIN_ESTABLISH_POWER, loc.their_power + 1)
            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                available_chars, force_remaining, power_goal, must_exceed=False
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

            # Calculate power needed for this location
            power_goal = max(MIN_ESTABLISH_POWER, loc.their_power + 1)

            # Find optimal combination of characters for this location
            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                available_chars, force_remaining, power_goal, must_exceed=False
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
                                    locations: List[LocationAnalysis]) -> List[Tuple[List[DeploymentInstruction], int, float]]:
        """
        Generate multiple ground plans - one for EACH target location.

        CRITICAL: Generate a plan for each location independently and score them.
        This ensures we consider crushing contested locations vs establishing at empty.

        Returns list of (instructions, force_remaining, score) tuples.
        """
        plans = []

        # Filter to affordable characters
        affordable_chars = [c for c in characters if c['cost'] <= force_budget]

        if not affordable_chars:
            return plans

        MIN_ESTABLISH_POWER = self.deploy_threshold

        # === GENERATE A PLAN FOR EACH TARGET LOCATION ===
        # This is the key change: try each location independently
        for target_loc in ground_targets:
            # Skip if we can't deploy characters there (interior check done in target filtering)
            if not target_loc.is_ground:
                continue

            # Calculate power needed for this specific location
            power_goal = max(MIN_ESTABLISH_POWER, target_loc.their_power + 1)

            # Find optimal combination of characters for THIS location
            chars_for_loc, power_allocated, cost_used = self._find_optimal_combination(
                affordable_chars.copy(), force_budget, power_goal, must_exceed=(target_loc.their_power > 0)
            )

            if not chars_for_loc:
                continue

            # Must meet threshold OR beat enemy power
            if target_loc.their_power > 0:
                if power_allocated <= target_loc.their_power:
                    continue  # Can't beat them, skip this location
            elif power_allocated < MIN_ESTABLISH_POWER:
                continue  # Can't establish, skip

            # Build instructions for this location
            instructions = []
            force_remaining = force_budget - cost_used

            for char in chars_for_loc:
                if target_loc.their_power > 0:
                    reason = f"Ground: Crush {target_loc.name} ({power_allocated} vs {target_loc.their_power})"
                else:
                    reason = f"Ground: Establish at {target_loc.name} (combined {power_allocated} power)"

                instructions.append(DeploymentInstruction(
                    card_blueprint_id=char['blueprint_id'],
                    card_name=char['name'],
                    target_location_id=target_loc.card_id,
                    target_location_name=target_loc.name,
                    priority=2,
                    reason=reason,
                    power_contribution=char['power'],
                    deploy_cost=char['cost'],
                ))

            if instructions:
                score = self._score_plan(instructions, locations)
                plans.append((instructions, force_remaining, score))

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

            power_goal = max(MIN_ESTABLISH_POWER, loc.their_power + 1)

            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                available_chars, force_remaining, power_goal, must_exceed=False
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
                              pure_pilots: List[Dict] = None) -> Tuple[List[DeploymentInstruction], int]:
        """
        Generate a space-focused deployment plan using full budget.

        If pure_pilots are provided, will try to include them boarding starships.
        """
        instructions = []
        force_remaining = force_budget
        available_ships = starships.copy()
        available_pure_pilots = (pure_pilots or []).copy()

        MIN_ESTABLISH_POWER = self.deploy_threshold

        for loc in space_targets:
            if not available_ships or force_remaining <= 0:
                break

            power_goal = max(MIN_ESTABLISH_POWER, loc.their_power + 1)

            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                available_ships, force_remaining, power_goal, must_exceed=False
            )

            if not cards_for_location or power_allocated < MIN_ESTABLISH_POWER:
                continue

            force_remaining -= cost_used
            for ship in cards_for_location:
                if ship in available_ships:
                    available_ships.remove(ship)

                if loc.their_power > 0:
                    reason = f"Space: Contest {loc.name} (vs {loc.their_power} power)"
                else:
                    reason = f"Space: Control {loc.name} ({loc.their_icons} icons)"

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

                # After deploying a starship, consider deploying a pure pilot aboard it
                # Pure pilots add their ability to the ship and are better there than on ground
                if available_pure_pilots:
                    # Find affordable pure pilot
                    affordable_pilots = [p for p in available_pure_pilots if p['cost'] <= force_remaining]
                    if affordable_pilots:
                        # Pick best pilot (highest power/ability)
                        best_pilot = max(affordable_pilots, key=lambda p: p['power'])
                        force_remaining -= best_pilot['cost']
                        available_pure_pilots.remove(best_pilot)

                        instructions.append(DeploymentInstruction(
                            card_blueprint_id=best_pilot['blueprint_id'],
                            card_name=best_pilot['name'],
                            target_location_id=loc.card_id,  # Same location as ship
                            target_location_name=loc.name,
                            priority=3,  # After the ship
                            reason=f"Pure pilot aboard {ship['name']}",
                            power_contribution=best_pilot['power'],
                            deploy_cost=best_pilot['cost'],
                        ))
                        logger.info(f"   👨‍✈️ Plan: Deploy pure pilot {best_pilot['name']} aboard {ship['name']}")

        return instructions, force_remaining

    def _generate_space_plan_for_ship(self, ship: Dict, other_ships: List[Dict],
                                       space_targets: List[LocationAnalysis],
                                       force_budget: int,
                                       pure_pilots: List[Dict] = None) -> Tuple[List[DeploymentInstruction], int]:
        """
        Generate a space plan starting with a specific starship.

        This allows comparing plans that prioritize different starships.
        """
        instructions = []
        force_remaining = force_budget
        available_pure_pilots = (pure_pilots or []).copy()

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

        # Add pure pilot aboard if available
        if available_pure_pilots:
            affordable_pilots = [p for p in available_pure_pilots if p['cost'] <= force_remaining]
            if affordable_pilots:
                best_pilot = max(affordable_pilots, key=lambda p: p['power'])
                force_remaining -= best_pilot['cost']
                available_pure_pilots.remove(best_pilot)
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
                                       pure_pilots: List[Dict] = None) -> Tuple[List[DeploymentInstruction], int]:
        """
        Generate a space plan using optimal combinations at each location.

        This considers deploying MULTIPLE starships + pilots to the SAME location
        to maximize power, potentially beating a single big ship strategy.
        """
        instructions = []
        force_remaining = force_budget
        available_ships = starships.copy()
        available_pilots = (pure_pilots or []).copy()

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
                # Add pilots sorted by power (best first)
                affordable_pilots.sort(key=lambda p: p['power'], reverse=True)
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

            # Add instructions for pilots
            for pilot in pilots_to_add:
                if pilot in available_pilots:
                    available_pilots.remove(pilot)
                force_remaining -= pilot['cost']

                instructions.append(DeploymentInstruction(
                    card_blueprint_id=pilot['blueprint_id'],
                    card_name=pilot['name'],
                    target_location_id=loc.card_id,
                    target_location_name=loc.name,
                    priority=3,  # After ships
                    reason=f"Pilot aboard ships at {loc.name}",
                    power_contribution=pilot['power'],
                    deploy_cost=pilot['cost'],
                ))

        return instructions, force_remaining

    def _generate_all_space_plans(self, starships: List[Dict],
                                   space_targets: List[LocationAnalysis],
                                   force_budget: int,
                                   pure_pilots: List[Dict],
                                   locations: List[LocationAnalysis]) -> List[Tuple[List[DeploymentInstruction], int, float]]:
        """
        Generate multiple space plans - one for EACH target location.

        CRITICAL: Generate a plan for each location independently and score them.
        This ensures we consider crushing contested locations vs establishing at empty.

        Returns list of (instructions, force_remaining, score) tuples.
        """
        plans = []

        # Filter to affordable starships
        affordable_ships = [s for s in starships if s['cost'] <= force_budget]

        if not affordable_ships:
            return plans

        MIN_ESTABLISH_POWER = self.deploy_threshold

        # === GENERATE A PLAN FOR EACH TARGET LOCATION ===
        # This is the key change: try each location independently
        for target_loc in space_targets:
            # Calculate power needed for this specific location
            power_goal = max(MIN_ESTABLISH_POWER, target_loc.their_power + 1)

            # Find optimal combination of ships for THIS location
            ships_for_loc, power_allocated, cost_used = self._find_optimal_combination(
                affordable_ships.copy(), force_budget, power_goal, must_exceed=(target_loc.their_power > 0)
            )

            if not ships_for_loc:
                continue

            # Must meet threshold OR beat enemy power
            if target_loc.their_power > 0:
                if power_allocated <= target_loc.their_power:
                    continue  # Can't beat them, skip this location
            elif power_allocated < MIN_ESTABLISH_POWER:
                continue  # Can't establish, skip

            # Build instructions for this location
            instructions = []
            force_remaining = force_budget - cost_used

            for ship in ships_for_loc:
                if target_loc.their_power > 0:
                    reason = f"Space: Crush {target_loc.name} ({power_allocated} vs {target_loc.their_power})"
                else:
                    reason = f"Space: Control {target_loc.name} (combined {power_allocated} power)"

                instructions.append(DeploymentInstruction(
                    card_blueprint_id=ship['blueprint_id'],
                    card_name=ship['name'],
                    target_location_id=target_loc.card_id,
                    target_location_name=target_loc.name,
                    priority=2,
                    reason=reason,
                    power_contribution=ship['power'],
                    deploy_cost=ship['cost'],
                ))

            # Add pilots if available and affordable
            if pure_pilots and force_remaining > 0:
                for pilot in pure_pilots:
                    if pilot['cost'] <= force_remaining:
                        instructions.append(DeploymentInstruction(
                            card_blueprint_id=pilot['blueprint_id'],
                            card_name=pilot['name'],
                            target_location_id=target_loc.card_id,
                            target_location_name=target_loc.name,
                            priority=3,
                            reason=f"Pilot aboard at {target_loc.name}",
                            power_contribution=pilot['power'],
                            deploy_cost=pilot['cost'],
                        ))
                        force_remaining -= pilot['cost']
                        break  # One pilot is enough

            if instructions:
                score = self._score_plan(instructions, locations)
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
        # Locations where we have presence but are at power deficit
        # CRITICAL: Skip locations where we should FLEE
        # Split into ground (use characters) and space (use starships)
        # =================================================================
        contested_ground = [
            loc for loc in locations
            if loc.my_power > 0 and loc.their_power > 0 and loc.power_differential < 0
            and not loc.should_flee  # DON'T REINFORCE IF WE'RE FLEEING
            and loc.is_ground  # Characters for ground
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
            and loc.is_ground  # Characters can only go to ground locations
            and loc.my_icons > 0  # MUST have force icons to deploy (or presence, but my_power==0)
        ]
        # Space locations for ships
        # CRITICAL: Can only deploy to locations where we have icons OR presence
        uncontested_space = [
            loc for loc in locations
            if loc.their_icons > 0  # Has opponent icons to deny
            and loc.my_power == 0  # We're not there yet
            and loc.is_space  # It's a space location
            and loc.my_icons > 0  # MUST have force icons to deploy (or presence, but my_power==0)
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

        if uncontested_ground:
            logger.info(f"   🎯 Ground targets (chars): {[loc.name for loc in uncontested_ground]}")
        if uncontested_space:
            logger.info(f"   🚀 Space targets (starships): {[loc.name for loc in uncontested_space]}")

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
        # STEP 5: COMPARE GROUND vs SPACE PLANS
        # Generate each plan INDEPENDENTLY with full budget, then pick the best
        # =================================================================
        MIN_ESTABLISH_POWER = self.deploy_threshold  # From config, default 6

        # Prepare vehicle variables for later use in STEP 5C
        available_vehicles = vehicles.copy()
        unpiloted_vehicles = [v for v in available_vehicles if v.get('needs_pilot')]

        # Ground targets for characters
        # CRITICAL: Must have our icons to deploy (or presence, but we filter my_power==0)
        char_ground_targets = [
            loc for loc in locations
            if loc.is_ground
            and (loc.their_power > 0 or loc.their_icons > 0)  # Opponent has presence/icons
            and loc.my_power == 0  # We don't have presence yet
            and loc.my_icons > 0  # MUST have our force icons to deploy there
        ]
        # CRITICAL: Sort contested locations FIRST (their_power > 0), then by icons
        # Beating opponents is MORE valuable than establishing at empty locations!
        # Sort key: (is_contested DESC, their_icons DESC)
        char_ground_targets.sort(
            key=lambda x: (x.their_power > 0, x.their_icons),  # Contested first, then icons
            reverse=True
        )
        char_ground_targets = char_ground_targets[:MAX_ESTABLISH_LOCATIONS]

        # Log why locations were excluded
        excluded_space = [loc.name for loc in locations if loc.is_space and not loc.is_ground]
        if excluded_space:
            logger.info(f"   ⏭️ Space locations (chars can't go): {excluded_space}")

        if char_ground_targets:
            logger.info(f"   🎯 Ground targets: {[(loc.name, loc.their_icons, loc.their_power) for loc in char_ground_targets]}")

        # Include contested space locations for starship reinforcement
        # These have our presence (can deploy via presence rule even without icons)
        space_targets = uncontested_space.copy()
        for loc in contested_space:
            if loc not in space_targets:
                space_targets.insert(0, loc)  # Contested locations first (higher priority)
                logger.info(f"   ⚔️ Contested space: {loc.name} ({loc.my_power} vs {loc.their_power})")

        if space_targets:
            logger.info(f"   🚀 Space targets: {[(loc.name, loc.their_icons, loc.their_power) for loc in space_targets]}")

        # Identify pure pilots (pilot but not warrior) - they're best aboard ships
        pure_pilots = [c for c in available_chars if c.get('is_pure_pilot')]
        if pure_pilots:
            logger.info(f"   👨‍✈️ Pure pilots available: {[p['name'] for p in pure_pilots]}")

        # =================================================================
        # Generate MULTIPLE plans for each deployable card and compare
        # This ensures we consider all viable deployment options
        # =================================================================

        # Generate all ground plans (one per affordable character)
        all_ground_plans = self._generate_all_ground_plans(
            available_chars.copy(), vehicles.copy(), char_ground_targets, force_remaining, locations
        )

        # Generate all space plans (one per affordable starship)
        # Uses space_targets which includes both uncontested AND contested space locations
        all_space_plans = self._generate_all_space_plans(
            starships.copy(), space_targets, force_remaining, pure_pilots, locations
        )

        # Log all plans for debugging
        logger.info(f"   📊 Generated {len(all_ground_plans)} ground plans, {len(all_space_plans)} space plans")

        for i, (instructions, force_left, score) in enumerate(all_ground_plans):
            cost = force_remaining - force_left
            cards = [inst.card_name for inst in instructions]
            logger.info(f"      GROUND {i+1}: {cards} -> score={score:.0f}, cost={cost}")

        for i, (instructions, force_left, score) in enumerate(all_space_plans):
            cost = force_remaining - force_left
            cards = [inst.card_name for inst in instructions]
            logger.info(f"      SPACE {i+1}: {cards} -> score={score:.0f}, cost={cost}")

        # Combine all plans and pick the best
        all_plans = []
        for instructions, force_left, score in all_ground_plans:
            all_plans.append(('ground', instructions, force_left, score))
        for instructions, force_left, score in all_space_plans:
            all_plans.append(('space', instructions, force_left, score))

        if all_plans:
            # Sort by score descending, pick the best
            all_plans.sort(key=lambda x: x[3], reverse=True)
            best_type, best_instructions, best_force_left, best_score = all_plans[0]

            logger.info(f"   ✅ CHOSE {best_type.upper()} PLAN (score {best_score:.0f})")
            for inst in best_instructions:
                logger.info(f"      - {inst.card_name} -> {inst.target_location_name} ({inst.power_contribution} power)")

            plan.instructions.extend(best_instructions)
            force_remaining = best_force_left

            # Update available_chars based on what was used
            used_blueprints = {inst.card_blueprint_id for inst in best_instructions}
            available_chars = [c for c in available_chars if c['blueprint_id'] not in used_blueprints]
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
        # CRITICAL: Must have our icons to deploy (or presence, but we filter my_power==0)
        ground_targets = [
            loc for loc in locations
            if loc.is_ground and loc.is_exterior  # Vehicles need exterior
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
            # Build detailed reason why we're holding back
            reasons = []
            if not locations:
                reasons.append("no locations on board")
            elif not char_ground_targets and not uncontested_space:
                reasons.append("no valid targets (all ground locs either have our presence or no opponent threat)")
            if not characters and not starships and not vehicles:
                reasons.append("no deployable units in hand")
            elif characters and not char_ground_targets:
                reasons.append(f"have {len(characters)} chars but no ground targets")
            if starships and not uncontested_space:
                reasons.append(f"have {len(starships)} starships but no space targets")
            if force_remaining <= 0:
                reasons.append("no force remaining")

            plan.reason = "; ".join(reasons) if reasons else "No good deployment options"
            logger.info(f"📋 FINAL PLAN: HOLD BACK - {plan.reason}")
            logger.info(f"   Debug: {len(locations)} locations, {len(characters)} chars, {len(starships)} ships, {force_remaining} force left")

        plan.phase_started = True
        plan.target_locations = locations
        self.current_plan = plan
        return plan

    def _get_all_deployable_cards(self, board_state) -> List[Dict]:
        """Get all cards we can deploy with their metadata"""
        from .card_loader import get_card

        deployable = []
        # Reserve 1 force for battle effects, but never go negative
        available_force = max(0, board_state.force_pile - 1)

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

            # Pure pilots (pilot but not warrior) are best deployed aboard ships
            is_warrior = metadata.is_warrior if hasattr(metadata, 'is_warrior') else False
            is_pure_pilot = metadata.is_pilot and not is_warrior

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
                'is_warrior': is_warrior,
                'is_pure_pilot': is_pure_pilot,
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

            # Get power from board_state (uses array index, same as admin panel)
            raw_my_power = board_state.my_power_at_location(idx) if hasattr(board_state, 'my_power_at_location') else 0
            raw_their_power = board_state.their_power_at_location(idx) if hasattr(board_state, 'their_power_at_location') else 0

            # Debug: Also calculate power from cards to compare
            cards_my_power = sum(c.power for c in loc.my_cards if hasattr(c, 'power') and c.power) if hasattr(loc, 'my_cards') else 0
            cards_their_power = sum(c.power for c in loc.their_cards if hasattr(c, 'power') and c.power) if hasattr(loc, 'their_cards') else 0

            if cards_their_power != max(0, raw_their_power):
                logger.warning(f"   ⚠️ POWER MISMATCH at {loc_name}: dict says {raw_their_power}, cards sum to {cards_their_power}")
                logger.warning(f"      Their cards: {[(c.card_title, c.power) for c in loc.their_cards] if hasattr(loc, 'their_cards') else []}")
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

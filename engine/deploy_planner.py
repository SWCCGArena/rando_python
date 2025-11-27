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
        ships = [c for c in all_cards if c['is_starship'] or c['is_vehicle']]

        logger.info(f"   Hand: {len(locations_in_hand)} locations, {len(characters)} characters, {len(ships)} ships")

        # Calculate total deployable power for battle analysis
        total_deployable_power = sum(c['power'] for c in characters)
        total_deployable_power += sum(c['power'] for c in ships)

        # Analyze board locations with battle/flee context
        locations = self._analyze_locations(board_state, total_deployable_power)

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
        # =================================================================
        uncontested_targets = [
            loc for loc in locations
            if loc.their_icons > 0  # Has opponent icons to deny
            and loc.my_power == 0  # We're not there yet
        ]
        # Sort by icons (most valuable first)
        uncontested_targets.sort(key=lambda x: x.their_icons, reverse=True)

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
        # Only if we have enough power to establish control
        # Uses optimal combination finding to beat their power efficiently
        # =================================================================
        for loc in uncontested_targets:
            if not available_chars or force_remaining <= 0:
                break

            # Find optimal combination to beat their power
            # must_exceed=True because we need power > their_power to control
            cards_for_location, power_allocated, cost_used = self._find_optimal_combination(
                available_chars,
                force_remaining,
                loc.their_power,
                must_exceed=True  # Need to beat, not just match
            )

            # Only deploy if we can actually beat them
            if not cards_for_location or power_allocated <= loc.their_power:
                logger.info(f"   ⏭️ Skip {loc.name}: can't beat {loc.their_power} power within budget")
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
                    reason=f"Establish at {loc.name} ({loc.their_icons} icons)",
                    power_contribution=char['power'],
                    deploy_cost=char['cost'],
                ))
                logger.info(f"   🚀 Plan: Deploy {char['name']} ({char['power']} power, {char['cost']} cost) to {loc.name}")

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

            deployable.append({
                'card_id': card.card_id,
                'blueprint_id': card.blueprint_id,
                'name': metadata.title,
                'power': metadata.power_value or 0,
                'cost': deploy_cost,
                'is_unique': metadata.is_unique,
                'is_location': metadata.is_location,
                'is_character': metadata.is_character,
                'is_starship': metadata.is_starship,
                'is_vehicle': metadata.is_vehicle,
                'is_pilot': metadata.is_pilot,
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

        if not hasattr(board_state, 'locations') or not board_state.locations:
            return locations

        for idx, loc in enumerate(board_state.locations):
            if not loc:
                continue

            analysis = LocationAnalysis(
                card_id=getattr(loc, 'card_id', ''),
                name=getattr(loc, 'name', 'Unknown'),
                is_ground=getattr(loc, 'is_site', False) or not getattr(loc, 'is_space', True),
                is_space=getattr(loc, 'is_space', False),
                location_index=idx,
            )

            analysis.my_power = getattr(loc, 'my_power', 0) or 0
            analysis.their_power = getattr(loc, 'their_power', 0) or 0
            analysis.i_control = getattr(loc, 'i_control', False)
            analysis.they_control = getattr(loc, 'they_control', False)
            analysis.contested = analysis.my_power > 0 and analysis.their_power > 0
            analysis.my_icons = getattr(loc, 'my_icons', 0) or 0
            analysis.their_icons = getattr(loc, 'their_icons', 0) or 0

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

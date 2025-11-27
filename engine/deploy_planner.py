"""
Deploy Phase Planner

Provides strategic phase-level planning for deployments.
Instead of evaluating each deploy action in isolation, this creates
a cohesive plan at the start of the deploy phase.

Strategic considerations:
- Total deployable power vs threshold
- Location priorities (establish new control vs reinforce existing)
- Opponent presence and force icons
- Power differential at contested locations
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Tuple

logger = logging.getLogger(__name__)


class DeployStrategy(Enum):
    """High-level deployment strategy for this phase"""
    HOLD_BACK = "hold_back"           # Don't deploy - save for later
    ESTABLISH = "establish"            # Deploy to new location to gain control
    REINFORCE = "reinforce"            # Strengthen a weak position
    OVERWHELM = "overwhelm"            # Crush opponent at a location
    SPREAD = "spread"                  # Deploy to multiple locations


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

    @property
    def power_differential(self) -> int:
        """Positive = I'm ahead, negative = they're ahead"""
        return self.my_power - self.their_power


@dataclass
class DeploymentPlan:
    """The deployment plan for this phase"""
    strategy: DeployStrategy
    reason: str

    # Target locations in priority order
    target_locations: List[LocationAnalysis] = field(default_factory=list)

    # Cards to deploy (blueprint_ids in priority order)
    cards_to_deploy: List[str] = field(default_factory=list)

    # Total power we're committing
    total_power: int = 0
    total_cost: int = 0

    # Phase state
    phase_started: bool = False
    deployments_made: int = 0

    def should_deploy_card(self, blueprint_id: str) -> bool:
        """Check if a card is part of our plan"""
        return blueprint_id in self.cards_to_deploy

    def get_target_location(self) -> Optional[str]:
        """Get the highest priority target location card_id"""
        if self.target_locations:
            return self.target_locations[0].card_id
        return None


class DeployPhasePlanner:
    """
    Creates strategic deployment plans at the start of deploy phase.

    Usage:
    1. Call create_plan() at start of deploy phase
    2. Individual deploy evaluators consult the plan
    3. Plan tracks state across multiple deploy decisions
    """

    def __init__(self, deploy_threshold: int = 6):
        self.deploy_threshold = deploy_threshold
        self.current_plan: Optional[DeploymentPlan] = None
        self._last_phase: str = ""

    def create_plan(self, board_state) -> DeploymentPlan:
        """
        Analyze board state and create a deployment plan.

        Called at the start of deploy phase (or first deploy decision).
        """
        from .card_loader import get_card

        # Check if we're in a new phase
        current_phase = getattr(board_state, 'current_phase', '')
        if current_phase != self._last_phase:
            self._last_phase = current_phase
            # Reset plan for new phase
            self.current_plan = None

        # Return existing plan if we have one for this phase
        if self.current_plan and self.current_plan.phase_started:
            return self.current_plan

        logger.info("📋 Creating deployment plan for this phase...")

        # Calculate total deployable power
        ground_power = board_state.total_hand_deployable_ground_power()
        space_power = board_state.total_hand_deployable_space_power()
        total_deployable = ground_power + space_power

        logger.info(f"   Deployable power: ground={ground_power}, space={space_power}, total={total_deployable}")

        # DECISION 1: Do we have enough power to deploy?
        if total_deployable < self.deploy_threshold:
            plan = DeploymentPlan(
                strategy=DeployStrategy.HOLD_BACK,
                reason=f"Deployable power {total_deployable} < threshold {self.deploy_threshold}",
                total_power=total_deployable
            )
            logger.info(f"📋 Plan: HOLD BACK - {plan.reason}")
            self.current_plan = plan
            plan.phase_started = True
            return plan

        # Analyze locations
        locations = self._analyze_locations(board_state)

        # Identify cards we can deploy
        deployable_cards = self._get_deployable_cards(board_state)

        # DECISION 2: Where should we deploy?
        strategy, target_locs, reason = self._choose_strategy(
            locations, deployable_cards, ground_power, space_power, board_state
        )

        # Build the plan
        plan = DeploymentPlan(
            strategy=strategy,
            reason=reason,
            target_locations=target_locs,
            cards_to_deploy=[c['blueprint_id'] for c in deployable_cards],
            total_power=total_deployable,
            total_cost=sum(c['cost'] for c in deployable_cards)
        )
        plan.phase_started = True

        logger.info(f"📋 Plan: {strategy.value.upper()} - {reason}")
        if target_locs:
            logger.info(f"   Target: {target_locs[0].name} (my power: {target_locs[0].my_power}, "
                       f"their power: {target_locs[0].their_power})")
        logger.info(f"   Deploying: {[c['name'] for c in deployable_cards[:3]]}")

        self.current_plan = plan
        return plan

    def _analyze_locations(self, board_state) -> List[LocationAnalysis]:
        """Analyze all locations for deployment targeting"""
        locations = []

        if not hasattr(board_state, 'locations') or not board_state.locations:
            return locations

        for loc in board_state.locations:
            if not loc:
                continue

            analysis = LocationAnalysis(
                card_id=getattr(loc, 'card_id', ''),
                name=getattr(loc, 'name', 'Unknown'),
                is_ground=getattr(loc, 'is_site', False) or not getattr(loc, 'is_space', True),
                is_space=getattr(loc, 'is_space', False)
            )

            # Get power at location
            analysis.my_power = getattr(loc, 'my_power', 0) or 0
            analysis.their_power = getattr(loc, 'their_power', 0) or 0

            # Control status
            analysis.i_control = getattr(loc, 'i_control', False)
            analysis.they_control = getattr(loc, 'they_control', False)
            analysis.contested = analysis.my_power > 0 and analysis.their_power > 0

            # Icons
            analysis.my_icons = getattr(loc, 'my_icons', 0) or 0
            analysis.their_icons = getattr(loc, 'their_icons', 0) or 0

            # Calculate priority score
            analysis.priority_score = self._calculate_location_priority(analysis)

            locations.append(analysis)

        # Sort by priority (highest first)
        locations.sort(key=lambda x: x.priority_score, reverse=True)

        return locations

    def _calculate_location_priority(self, loc: LocationAnalysis) -> float:
        """
        Calculate strategic priority of a location.

        Higher priority = more valuable deployment target.

        KEY PRINCIPLE: Opponent icons are the PRIMARY reason to deploy.
        - Locations with opponent icons = drain potential = HIGH priority
        - Locations with opponent characters but no icons = counter opportunity
        - Locations with neither = almost never deploy there
        """
        score = 0.0

        # =================================================================
        # PRIMARY FACTOR: Opponent's force icons (drain potential)
        # This is the MAIN reason to deploy somewhere
        # =================================================================
        if loc.their_icons > 0:
            # Base bonus for having any opponent icons
            score += 50.0
            # Additional bonus per icon (more icons = more drain damage)
            score += loc.their_icons * 25.0
            logger.debug(f"  {loc.name}: +{50 + loc.their_icons * 25:.0f} (opponent has {loc.their_icons} icons)")

        # =================================================================
        # SECONDARY FACTOR: Counter opponent characters (no icons)
        # Only valuable if we can actually beat them
        # =================================================================
        elif loc.their_power > 0:
            # They have characters but no icons - counter opportunity
            # Only worth it if we can WIN (overpower them)
            if loc.my_power > loc.their_power:
                # We're already winning - moderate value
                score += 15.0
                logger.debug(f"  {loc.name}: +15 (countering {loc.their_power} power, already winning)")
            elif loc.my_power > 0:
                # We're there but losing - only deploy if we can catch up
                score += 10.0
                logger.debug(f"  {loc.name}: +10 (contested, can reinforce)")
            else:
                # They're alone, we're not there, AND no icons
                # Low value - deploying here doesn't deny drains
                score -= 20.0
                logger.debug(f"  {loc.name}: -20 (opponent characters but no icons - low value)")

        # =================================================================
        # PENALTY: No opponent presence AND no opponent icons
        # Almost never deploy here - no strategic value
        # =================================================================
        else:
            # No opponent icons, no opponent characters
            if loc.my_power > 0:
                # We're already there alone - definitely don't reinforce
                score -= 50.0
                logger.debug(f"  {loc.name}: -50 (we're alone, no strategic value)")
            else:
                # Empty location with no icons - very low value
                score -= 30.0
                logger.debug(f"  {loc.name}: -30 (empty, no opponent icons)")

        # =================================================================
        # MODIFIERS
        # =================================================================

        # Locations they control but we could contest (with icons)
        if loc.they_control and loc.their_power < 6 and loc.their_icons > 0:
            score += 20.0  # Easy takeover of valuable target

        # Contested locations where we're losing badly
        if loc.contested and loc.power_differential < -3:
            score += 15.0 + abs(loc.power_differential)  # Urgent reinforcement

        # Locations where we're very strong already
        if loc.my_power > 7 and loc.their_power < loc.my_power:
            score -= 15.0  # Don't over-reinforce

        # Bonus for ground locations (usually more tactical options)
        if loc.is_ground:
            score += 2.0

        return score

    def _get_deployable_cards(self, board_state) -> List[Dict]:
        """Get list of cards we can deploy with their metadata"""
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
                continue  # Can't afford

            # Skip starships for now (handled separately)
            if metadata.is_starship:
                continue

            # Skip pure pilots
            if metadata.is_pilot and not metadata.is_warrior and (metadata.power_value or 0) <= 4:
                continue

            deployable.append({
                'card_id': card.card_id,
                'blueprint_id': card.blueprint_id,
                'name': metadata.title,
                'power': metadata.power_value or 0,
                'cost': deploy_cost,
                'is_unique': metadata.is_unique
            })

        # Sort by power (highest first)
        deployable.sort(key=lambda x: x['power'], reverse=True)

        return deployable

    def _choose_strategy(self, locations: List[LocationAnalysis],
                         cards: List[Dict], ground_power: int, space_power: int,
                         board_state) -> Tuple[DeployStrategy, List[LocationAnalysis], str]:
        """
        Choose the best deployment strategy based on board analysis.

        KEY PRINCIPLE: Only deploy if there's a strategic reason.
        - Opponent icons = deny drains = good reason
        - Counter opponent characters = good IF we can win
        - No icons, no opponents = almost never deploy

        Returns: (strategy, target_locations, reason)
        """
        if not locations:
            # No locations analyzed - hold back
            return DeployStrategy.HOLD_BACK, [], "No locations to deploy to"

        # =================================================================
        # CATEGORIZE LOCATIONS
        # =================================================================

        # Strategic targets: locations with opponent icons (force drain potential)
        strategic_targets = [loc for loc in locations if loc.their_icons > 0]

        # Counter targets: opponent characters at locations WITHOUT icons
        # Only count if we can actually overpower them
        counter_targets = [
            loc for loc in locations
            if loc.their_power > 0 and loc.their_icons == 0
            and (loc.my_power + ground_power) > loc.their_power  # Can we win?
        ]

        # Vulnerable: we're there but losing badly
        vulnerable = [loc for loc in locations if loc.contested and loc.power_differential < -3]

        # Check if ANY location has strategic value
        has_strategic_value = bool(strategic_targets) or bool(counter_targets) or bool(vulnerable)

        # =================================================================
        # NO STRATEGIC TARGETS -> HOLD BACK
        # =================================================================
        if not has_strategic_value:
            # No opponent icons anywhere, no winnable counters, not losing anywhere
            # Deploying would be wasteful - save force for later
            logger.info(f"📋 No strategic targets: no opponent icons, no winnable counters")
            return (DeployStrategy.HOLD_BACK, [],
                    "No strategic targets - holding back (no opponent icons to deny)")

        # =================================================================
        # PRIORITY 1: REINFORCE VULNERABLE POSITIONS
        # We're at risk of losing a location we hold
        # =================================================================
        if vulnerable:
            # Prioritize vulnerable locations that have opponent icons
            vulnerable_with_icons = [loc for loc in vulnerable if loc.their_icons > 0]
            if vulnerable_with_icons:
                return (DeployStrategy.REINFORCE, vulnerable_with_icons,
                        f"Reinforcing vulnerable {vulnerable_with_icons[0].name} (down by {abs(vulnerable_with_icons[0].power_differential)}, {vulnerable_with_icons[0].their_icons} icons)")
            # Even without icons, don't lose positions
            return (DeployStrategy.REINFORCE, vulnerable,
                    f"Reinforcing vulnerable position at {vulnerable[0].name} (down by {abs(vulnerable[0].power_differential)})")

        # =================================================================
        # PRIORITY 2: ESTABLISH AT LOCATIONS WITH OPPONENT ICONS
        # This is the PRIMARY strategic goal
        # =================================================================
        if strategic_targets:
            # Sort by icons (most icons first) then by ease of takeover
            strategic_targets.sort(key=lambda x: (x.their_icons, -x.their_power), reverse=True)
            best = strategic_targets[0]

            # Can we take over or contest?
            if best.my_power == 0:
                # We're not there - establish presence
                return (DeployStrategy.ESTABLISH, strategic_targets,
                        f"Establishing at {best.name} ({best.their_icons} opponent icons)")
            elif best.my_power < best.their_power:
                # We're there but losing - reinforce
                return (DeployStrategy.REINFORCE, strategic_targets,
                        f"Reinforcing at {best.name} ({best.their_icons} icons, need +{best.their_power - best.my_power} power)")
            else:
                # We're winning - maybe overwhelm or look for next target
                if len(strategic_targets) > 1:
                    next_target = strategic_targets[1]
                    return (DeployStrategy.ESTABLISH, strategic_targets[1:],
                            f"Already winning at {best.name}, establishing at {next_target.name} ({next_target.their_icons} icons)")

        # =================================================================
        # PRIORITY 3: COUNTER OPPONENT CHARACTERS (no icons)
        # Only if we can decisively win
        # =================================================================
        if counter_targets:
            # Only counter if we'll have significant advantage
            best_counter = max(counter_targets, key=lambda x: (ground_power + x.my_power) - x.their_power)
            power_advantage = (ground_power + best_counter.my_power) - best_counter.their_power

            if power_advantage >= 3:
                return (DeployStrategy.OVERWHELM, [best_counter],
                        f"Countering {best_counter.their_power} power at {best_counter.name} (will have +{power_advantage})")
            else:
                # Not enough advantage to be worth it without icons
                logger.debug(f"Counter at {best_counter.name} only +{power_advantage} - not worth without icons")

        # =================================================================
        # NO GOOD OPTIONS -> HOLD BACK
        # =================================================================
        # If we reach here, we have some locations but none are truly worth deploying to
        logger.info(f"📋 No compelling deployment targets")
        return (DeployStrategy.HOLD_BACK, [],
                "No compelling targets - holding back")

    def get_location_bonus(self, location_card_id: str) -> float:
        """
        Get the bonus/penalty for deploying to a specific location.

        Called by deploy evaluator when scoring location choices.
        """
        if not self.current_plan:
            return 0.0

        # Bonus for deploying to target location
        for i, loc in enumerate(self.current_plan.target_locations):
            if loc.card_id == location_card_id:
                # Higher bonus for primary target
                return 50.0 - (i * 10)

        # Penalty for deploying elsewhere when we have a target
        if self.current_plan.target_locations:
            return -20.0

        return 0.0

    def should_hold_back(self) -> bool:
        """Check if current plan is to hold back"""
        if not self.current_plan:
            return False
        return self.current_plan.strategy == DeployStrategy.HOLD_BACK

    def record_deployment(self, blueprint_id: str):
        """Record that we made a deployment"""
        if self.current_plan:
            self.current_plan.deployments_made += 1
            if blueprint_id in self.current_plan.cards_to_deploy:
                self.current_plan.cards_to_deploy.remove(blueprint_id)

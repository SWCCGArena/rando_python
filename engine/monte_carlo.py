"""
Monte Carlo simulation for deploy planner stress-testing.

Runs adversarial 2-turn simulations to evaluate plan resilience against
likely opponent counterplays. Uses real hand state and generalizes unknown
opponent resources.
"""

import logging
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class TrialOutcome:
    """Result of a single 2-turn simulation trial."""
    power_margin: int           # Final power differential (positive = we win)
    we_control: bool            # Do we control at end of simulation?
    barrier_killed: bool        # Was one of our cards Barriered?
    opponent_battled: bool      # Did opponent initiate battle on turn 1?
    turn_resolved: int          # 1 = resolved turn 1, 2 = resolved turn 2


@dataclass
class SimulationResult:
    """Aggregate results from N simulation trials."""
    win_rate: float             # 0.0 - 1.0, fraction of trials where we_control=True
    avg_power_margin: float     # Average margin across all trials
    worst_case: int             # Minimum margin (most pessimistic)
    best_case: int              # Maximum margin (most optimistic)
    percentile_10_margin: int   # 10th percentile margin (2nd worst for n=20)
    barrier_losses: int         # Count of trials where barrier killed our card
    opponent_battled_count: int # Count of trials where opponent battled turn 1
    histogram: Dict[int, int]   # margin -> count (for visualization)


@dataclass
class ExpectedValue:
    """Final adjusted score after Monte Carlo analysis."""
    raw_score: float            # Original plan score before simulation
    win_rate: float             # From simulation
    resilience: float           # Factor based on worst-case margin
    early_battle_factor: float  # Penalty if opponent often attacks turn 1
    barrier_factor: float       # Small penalty for barrier vulnerability
    final_score: float          # raw_score x all factors
    histogram: Dict[int, int]   # For logging/debugging


class MonteCarloSimulator:
    """
    2-turn adversarial simulation using real hand state and generalized opponent.

    Turn 1: Opponent deploys power to counter, may initiate battle
    Turn 2: If no battle, we reinforce from real hand and battle

    Uses location importance to weight opponent response strength.
    """

    DEFAULT_N_SIMULATIONS = 20
    DEFAULT_BARRIER_PROBABILITY = 0.08  # ~8% - rare, once per game max

    # Location-importance-weighted power response ranges
    DEFAULT_POWER_RESPONSE = {
        'high': (4, 6),   # 2+ icons - opponent WILL contest
        'medium': (3, 5), # 1 icon
        'low': (2, 4),    # 0 icons
    }

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize simulator with configuration.

        Args:
            config: Optional dict with keys:
                - n_simulations: Number of trials (default 20)
                - barrier_probability: Chance of barrier (default 0.08)
                - power_response_high: [min, max] for 2+ icon locations
                - power_response_medium: [min, max] for 1 icon locations
                - power_response_low: [min, max] for 0 icon locations
        """
        config = config or {}

        self.n_simulations = config.get('n_simulations', self.DEFAULT_N_SIMULATIONS)
        self.barrier_prob = config.get('barrier_probability', self.DEFAULT_BARRIER_PROBABILITY)

        self.power_response = {
            'high': tuple(config.get('power_response_high', list(self.DEFAULT_POWER_RESPONSE['high']))),
            'medium': tuple(config.get('power_response_medium', list(self.DEFAULT_POWER_RESPONSE['medium']))),
            'low': tuple(config.get('power_response_low', list(self.DEFAULT_POWER_RESPONSE['low']))),
        }

        logger.debug(f"MonteCarloSimulator initialized: n={self.n_simulations}, "
                     f"barrier_prob={self.barrier_prob}, power_response={self.power_response}")

    def simulate_plan(
        self,
        plan: Any,  # DeploymentPlan
        location_analyses: List[Any],  # List[LocationAnalysis]
        hand_cards: List[Dict],  # Cards in hand as dicts with power, deploy_cost
        board_state: Any,  # BoardState
    ) -> SimulationResult:
        """
        Run N simulations of 2-turn sequences.
        Uses our REAL hand for turn 2 reinforcement calculations.

        Args:
            plan: The DeploymentPlan to simulate
            location_analyses: List of LocationAnalysis for all locations
            hand_cards: Cards in hand as dicts with at least:
                - blueprint_id, power, deploy_cost
            board_state: Current board state for force availability

        Returns:
            SimulationResult with win rate, margins, and histogram
        """
        outcomes = []

        for _ in range(self.n_simulations):
            outcome = self._simulate_two_turns(plan, location_analyses, hand_cards, board_state)
            outcomes.append(outcome)

        if not outcomes:
            # Edge case: no outcomes
            return SimulationResult(
                win_rate=0.0,
                avg_power_margin=0.0,
                worst_case=0,
                best_case=0,
                percentile_10_margin=0,
                barrier_losses=0,
                opponent_battled_count=0,
                histogram={0: 1}
            )

        # Calculate 10th percentile margin (more robust than worst_case)
        sorted_margins = sorted(o.power_margin for o in outcomes)
        percentile_10_idx = max(0, len(sorted_margins) // 10)  # 2nd item for n=20
        percentile_10_margin = sorted_margins[percentile_10_idx]

        return SimulationResult(
            win_rate=sum(1 for o in outcomes if o.we_control) / len(outcomes),
            avg_power_margin=sum(o.power_margin for o in outcomes) / len(outcomes),
            worst_case=min(o.power_margin for o in outcomes),
            best_case=max(o.power_margin for o in outcomes),
            percentile_10_margin=percentile_10_margin,
            barrier_losses=sum(1 for o in outcomes if o.barrier_killed),
            opponent_battled_count=sum(1 for o in outcomes if o.opponent_battled),
            histogram=self._build_histogram(outcomes)
        )

    def calculate_expected_value(
        self,
        base_score: float,
        simulation_result: SimulationResult
    ) -> ExpectedValue:
        """
        Adjust plan score based on Monte Carlo simulation results.

        Key insight: A plan where opponent battles us on turn 1 (and we lose)
        is worse than one where we control until turn 2 and win the battle.

        Uses 10th percentile margin (not worst-case) for resilience to avoid
        over-penalizing plans with rare outlier bad outcomes.

        Returns:
            ExpectedValue with final adjusted score
        """
        win_rate = simulation_result.win_rate

        # Resilience factor based on 10th percentile margin (more robust than worst-case)
        # This ignores the single worst outlier in 20 trials
        p10_margin = simulation_result.percentile_10_margin
        if p10_margin >= 4:
            resilience = 1.0   # Very robust - survives heavy counterplay
        elif p10_margin >= 2:
            resilience = 0.95  # Good - survives typical response
        elif p10_margin >= 0:
            resilience = 0.90  # Marginal - just barely holds (was 0.85)
        elif p10_margin >= -2:
            resilience = 0.80  # Fragile but recoverable
        else:
            resilience = 0.70  # Very fragile - often loses to counterplay

        # Early battle penalty - if opponent often battles on turn 1, plan is vulnerable
        # Reduced from 15% to 10% max penalty
        if self.n_simulations > 0:
            opponent_battle_rate = simulation_result.opponent_battled_count / self.n_simulations
        else:
            opponent_battle_rate = 0.0
        early_battle_factor = 1.0 - (opponent_battle_rate * 0.10)  # Up to 10% penalty

        # Barrier penalty (very small since it's rare)
        if self.n_simulations > 0:
            barrier_loss_rate = simulation_result.barrier_losses / self.n_simulations
        else:
            barrier_loss_rate = 0.0
        barrier_factor = 1.0 - (barrier_loss_rate * 0.15)  # Reduced from 0.2

        # Calculate raw expected value
        raw_expected = base_score * win_rate * resilience * early_battle_factor * barrier_factor

        # Apply floor: don't let score drop below 40% of base score
        # This prevents near-zero scores that don't differentiate well
        floor = base_score * 0.40
        final_score = max(raw_expected, floor)

        return ExpectedValue(
            raw_score=base_score,
            win_rate=win_rate,
            resilience=resilience,
            early_battle_factor=early_battle_factor,
            barrier_factor=barrier_factor,
            final_score=final_score,
            histogram=simulation_result.histogram
        )

    def _simulate_two_turns(
        self,
        plan: Any,
        location_analyses: List[Any],
        hand_cards: List[Dict],
        board_state: Any
    ) -> TrialOutcome:
        """
        Single 2-turn simulation with REALISTIC probabilistic opponent modeling.

        Key improvements over deterministic simulation:
        1. Opponent response constrained by their actual force/hand
        2. Opponent may choose to counter elsewhere (probabilistic)
        3. Opponent deploy power is sampled from realistic distribution
        4. Battle decision is probabilistic, not deterministic

        Turn 1: Opponent responds (maybe) to our deploy
        Turn 2: We respond (if no battle), then resolve
        """
        # Build location lookup
        loc_by_id = {loc.card_id: loc for loc in location_analyses}

        # Get opponent's constraints from board state
        opponent_force = getattr(board_state, 'their_force_available', 6)
        opponent_hand = getattr(board_state, 'their_hand_size', 4)

        # TURN 1: Our initial deploy (from plan)
        location_states = {}
        for instruction in plan.instructions:
            loc_id = instruction.target_location_id
            if loc_id is None:
                continue  # Locations deploy to table, not a specific location

            loc = loc_by_id.get(loc_id)
            if loc is None:
                continue

            if loc_id not in location_states:
                location_states[loc_id] = {
                    'our_power': loc.my_power,
                    'their_power': loc.their_power,
                    'icons': loc.my_icons + loc.their_icons,
                    'cards_deployed': [],
                    'loc_name': loc.name,
                }

            location_states[loc_id]['our_power'] += instruction.power_contribution
            location_states[loc_id]['cards_deployed'].append({
                'power': instruction.power_contribution,
                'name': instruction.card_name,
            })

        if not location_states:
            # No location deploys (maybe just location cards)
            return TrialOutcome(
                power_margin=0,
                we_control=True,
                barrier_killed=False,
                opponent_battled=False,
                turn_resolved=1
            )

        # Barrier check (rare - ~8% of games)
        barrier_played = random.random() < self.barrier_prob
        barrier_killed = False
        if barrier_played:
            # Find highest-value single-card deploy and cancel it
            for loc_id, state in location_states.items():
                if len(state['cards_deployed']) == 1:
                    cancelled_power = state['cards_deployed'][0]['power']
                    state['our_power'] -= cancelled_power
                    barrier_killed = True
                    break  # Only one Barrier per game

        # TURN 1: Opponent deploys - PROBABILISTIC modeling
        for loc_id, state in location_states.items():
            importance = self._get_location_importance(state['icons'])

            # PROBABILITY 1: Does opponent counter THIS location?
            # They have their own priorities - not always countering us
            counter_prob = self._get_counter_probability(importance, opponent_hand)
            if random.random() > counter_prob:
                # Opponent doesn't counter here - they go elsewhere or save resources
                continue

            # PROBABILITY 2: How much power can/will they deploy?
            # Constrained by their force and hand, sampled from distribution
            opponent_deploy = self._sample_opponent_deploy(
                importance, opponent_force, opponent_hand
            )
            state['their_power'] += opponent_deploy

        # TURN 1: Opponent decides - battle now or wait? (PROBABILISTIC)
        opponent_battled = False
        turn1_result = None
        for loc_id, state in location_states.items():
            power_diff = state['their_power'] - state['our_power']
            importance = self._get_location_importance(state['icons'])

            # Probabilistic battle decision
            battle_prob = self._get_battle_probability(power_diff, importance)
            if random.random() < battle_prob:
                opponent_battled = True
                turn1_result = self._resolve_battle(state)
                break

        if opponent_battled:
            # Game resolved in turn 1
            return TrialOutcome(
                power_margin=turn1_result['margin'],
                we_control=turn1_result['we_control'],
                barrier_killed=barrier_killed,
                opponent_battled=True,
                turn_resolved=1
            )

        # TURN 2: Opponent didn't battle - we can reinforce from our REAL hand
        # Get cards not already used in this plan
        used_blueprints = set()
        for instruction in plan.instructions:
            if instruction.card_blueprint_id:
                used_blueprints.add(instruction.card_blueprint_id)

        remaining_hand = [
            card for card in hand_cards
            if card.get('blueprint_id') not in used_blueprints
        ]

        # Calculate force we'd have next turn (rough estimate: similar to this turn)
        force_available = getattr(board_state, 'my_force_available', 0)

        # Reinforce contested locations with remaining hand
        for loc_id, state in location_states.items():
            if state['their_power'] > 0:  # Contested
                reinforcement = self._calculate_reinforcement(remaining_hand, force_available)
                state['our_power'] += reinforcement

        # TURN 2: We initiate battle
        best_outcome = None
        for loc_id, state in location_states.items():
            if state['their_power'] > 0:  # Battle at contested location
                result = self._resolve_battle(state)
                if best_outcome is None or result['margin'] > best_outcome['margin']:
                    best_outcome = result

        if best_outcome is None:
            # No contested locations - we control everything
            best_outcome = {'margin': 99, 'we_control': True}

        return TrialOutcome(
            power_margin=best_outcome['margin'],
            we_control=best_outcome['we_control'],
            barrier_killed=barrier_killed,
            opponent_battled=False,
            turn_resolved=2
        )

    def _get_counter_probability(self, importance: str, opponent_hand: int) -> float:
        """
        Probability that opponent counters at this specific location.

        Factors:
        - Location importance (high-icon locations more likely to be contested)
        - Opponent hand size (more cards = more likely to have something to deploy)
        """
        # Base probability by importance
        base_prob = {'high': 0.70, 'medium': 0.50, 'low': 0.30}[importance]

        # Adjust by hand size: small hand = less likely to counter
        if opponent_hand <= 2:
            hand_factor = 0.6  # 40% reduction
        elif opponent_hand <= 4:
            hand_factor = 0.8  # 20% reduction
        else:
            hand_factor = 1.0  # Full probability

        return base_prob * hand_factor

    def _sample_opponent_deploy(
        self, importance: str, opponent_force: int, opponent_hand: int
    ) -> int:
        """
        Sample opponent's deploy power from a realistic distribution.

        Constrained by:
        - Their available force (can't spend more than they have)
        - Their hand size (rough proxy for available cards)
        - Location importance (they invest more at important locations)

        Returns power deployed (can be 0 if they can't/don't deploy much).
        """
        # Estimate max deployable power based on force and hand
        # Assume average deploy cost is ~3 force per 4 power
        max_from_force = int(opponent_force * 1.3)  # ~4 power per 3 force
        max_from_hand = opponent_hand * 4  # Assume avg 4 power per card
        max_deployable = min(max_from_force, max_from_hand)

        if max_deployable <= 0:
            return 0

        # Sample from distribution based on importance
        # Three tiers: low response, medium response, high response
        roll = random.random()

        if importance == 'high':
            # High-icon: opponent likely to invest heavily
            if roll < 0.20:
                # Low response (saving resources, bad draw)
                power_range = (0, max(1, max_deployable // 4))
            elif roll < 0.50:
                # Medium response
                power_range = (max_deployable // 4, max_deployable // 2)
            else:
                # High response (50% chance)
                power_range = (max_deployable // 2, max_deployable)
        elif importance == 'medium':
            # Medium importance: balanced response
            if roll < 0.30:
                power_range = (0, max(1, max_deployable // 4))
            elif roll < 0.70:
                power_range = (max_deployable // 4, max_deployable // 2)
            else:
                power_range = (max_deployable // 2, max_deployable)
        else:
            # Low importance: opponent likely to underinvest
            if roll < 0.50:
                power_range = (0, max(1, max_deployable // 4))
            elif roll < 0.80:
                power_range = (max_deployable // 4, max_deployable // 2)
            else:
                power_range = (max_deployable // 2, max_deployable)

        return random.randint(power_range[0], max(power_range[0], power_range[1]))

    def _get_battle_probability(self, power_diff: int, importance: str) -> float:
        """
        Probability that opponent initiates battle given the power differential.

        power_diff = their_power - our_power (positive = they have advantage)

        More likely to battle when:
        - They have a large advantage
        - Location is important
        """
        if power_diff >= 6:
            base_prob = 0.95  # Almost certain with big advantage
        elif power_diff >= 4:
            base_prob = 0.80
        elif power_diff >= 2:
            base_prob = 0.60
        elif power_diff >= 0:
            base_prob = 0.35  # Risky to battle at parity
        elif power_diff >= -2:
            base_prob = 0.15  # Unlikely at slight disadvantage
        else:
            base_prob = 0.05  # Very unlikely at big disadvantage

        # Increase probability for important locations
        if importance == 'high':
            importance_factor = 1.2  # 20% more likely
        elif importance == 'medium':
            importance_factor = 1.0
        else:
            importance_factor = 0.8  # 20% less likely

        return min(0.98, base_prob * importance_factor)

    def _get_location_importance(self, icons: int) -> str:
        """Categorize location by icon count for opponent response weighting."""
        if icons >= 2:
            return 'high'
        elif icons >= 1:
            return 'medium'
        return 'low'

    def _calculate_reinforcement(self, remaining_hand: List[Dict], force_available: int) -> int:
        """
        Calculate how much power we could add from remaining hand.

        Simple heuristic: deploy highest-power cards that fit force budget.
        """
        total_power = 0
        force_left = force_available

        # Sort by power descending
        sorted_cards = sorted(
            remaining_hand,
            key=lambda c: c.get('power', 0),
            reverse=True
        )

        for card in sorted_cards:
            cost = card.get('deploy_cost', 0)
            power = card.get('power', 0)
            if cost <= force_left and power > 0:
                total_power += power
                force_left -= cost

        return total_power

    def _resolve_battle(self, location_state: Dict) -> Dict:
        """Simplified battle resolution - power comparison."""
        margin = location_state['our_power'] - location_state['their_power']
        return {
            'margin': margin,
            'we_control': margin > 0
        }

    def _build_histogram(self, outcomes: List[TrialOutcome]) -> Dict[int, int]:
        """Build histogram of power margins."""
        histogram: Dict[int, int] = {}
        for outcome in outcomes:
            margin = outcome.power_margin
            histogram[margin] = histogram.get(margin, 0) + 1
        return histogram

    @staticmethod
    def format_histogram(histogram: Dict[int, int]) -> str:
        """Format histogram as compact string like '-2:3 0:5 +2:8 +4:4'"""
        return " ".join(f"{k:+d}:{v}" for k, v in sorted(histogram.items()))

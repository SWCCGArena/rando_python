"""
Game Strategy Module

Central coordinator for strategic decision-making that tracks game state trends
and provides strategic context to evaluators.

This module provides:
- Game phase detection (EARLY/MID/LATE)
- Force economy tracking and targets
- Location priority scoring
- Cross-turn strategy focus (GROUND/SPACE/BALANCED)
- Reserve deck check limiting
- Battle threat assessment

Design: Proactive strategy rather than reactive per-decision scoring.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Set, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .board_state import BoardState, LocationInPlay, CardInPlay

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Game phases
EARLY_GAME_TURNS = 3
MID_GAME_TURNS = 8

# Force generation targets (sliding scale by phase)
FORCE_GEN_TARGET_EARLY = 8
FORCE_GEN_TARGET_MID = 6
FORCE_GEN_TARGET_LATE = 5

# Hand size limits
HAND_SOFT_CAP = 12
HAND_HARD_CAP = 16

# Reserve management
MAX_RESERVE_CHECKS_PER_TURN = 2
RESERVE_CHECK_COOLDOWN_TURNS = 2
MIN_RESERVE_TO_KEEP = 3

# Battle thresholds
BATTLE_CRUSH_THRESHOLD = 6      # Power advantage to "crush"
BATTLE_FAVORABLE_THRESHOLD = 2  # Good odds
BATTLE_DANGER_THRESHOLD = -6    # Should retreat

# Focus confidence
FOCUS_CONFIDENCE_LOSS_ON_SETBACK = 0.3
FOCUS_RESET_THRESHOLD = 0.3
FOCUS_CONFIDENCE_GAIN_ON_SUCCESS = 0.2

# Location priority weights
LOCATION_WEIGHT_FORCE_ICON = 20
LOCATION_WEIGHT_BATTLEGROUND = 15
LOCATION_WEIGHT_MY_PRESENCE = 10
LOCATION_WEIGHT_ENEMY_PRESENCE = 25
LOCATION_WEIGHT_ADJACENT = 5
LOCATION_WEIGHT_EMPTY = 8


# =============================================================================
# Enums
# =============================================================================

class GamePhase(Enum):
    """Game phase based on turn number"""
    EARLY = "early"   # Turns 1-3: Establishing force generation
    MID = "mid"       # Turns 4-8: Building board presence
    LATE = "late"     # Turns 9+: Consolidating and finishing


class StrategyFocus(Enum):
    """Strategic focus for card deployment"""
    GROUND = "ground"      # Prioritize characters, vehicles, sites
    SPACE = "space"        # Prioritize starships, pilots, systems
    BALANCED = "balanced"  # No preference


class ThreatLevel(Enum):
    """Threat assessment for locations"""
    SAFE = "safe"              # We control, no enemies
    CRUSH = "crush"            # Overwhelming advantage (6+)
    FAVORABLE = "favorable"    # Good odds (2-5)
    RISKY = "risky"            # Could go either way (-2 to +2)
    DANGEROUS = "dangerous"    # Bad odds (-6 to -2)
    RETREAT = "retreat"        # Should retreat (<-6)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class LocationPriority:
    """Priority score for a location"""
    location_index: int
    score: float
    reasons: List[str] = field(default_factory=list)
    is_contested: bool = False
    is_dangerous: bool = False
    threat_level: ThreatLevel = ThreatLevel.SAFE

    def add_reason(self, reason: str, delta: float):
        """Add a scoring reason"""
        self.reasons.append(f"{reason}: {delta:+.1f}")
        self.score += delta


# =============================================================================
# Main Class
# =============================================================================

class GameStrategy:
    """
    Central strategy coordinator for game-state aware decision making.

    Tracks:
    - Game phase and turn progression
    - Force economy (generation, targets, deficits)
    - Location priorities and threats
    - Cross-turn strategy focus
    - Reserve deck access limiting
    """

    def __init__(self, my_side: str = "dark", config=None):
        """
        Initialize game strategy.

        Args:
            my_side: "dark" or "light"
            config: Optional config object with AI settings
        """
        self.my_side = my_side
        self.config = config  # Store reference to config for live updates

        # Game phase
        self.phase = GamePhase.EARLY
        self.turn_number = 0

        # Force economy - use config if available
        self.my_force_generation = 0          # Total force icons I control
        self.force_generation_target = self._get_config('FORCE_GEN_TARGET', FORCE_GEN_TARGET_EARLY)
        self.force_deficit = 0                # target - actual (positive = need more)

        # Strategy focus
        self.current_focus = StrategyFocus.BALANCED
        self.focus_confidence = 0.5           # 0-1, decreases on setbacks
        self.turns_with_focus = 0
        self.focus_deployments = 0            # Successful deploys matching focus

        # Location tracking
        self.contested_locations: List[int] = []  # Both players have cards
        self.dangerous_locations: List[int] = []  # Enemy power > my power by 2+
        self.location_priorities: List[LocationPriority] = []

        # Reserve deck limiting - use config if available
        self.reserve_checks_this_turn = 0
        self.cards_seen_in_reserve: Set[str] = set()  # Blueprint IDs recently seen
        self.last_reserve_check_turn = 0
        self.max_reserve_checks_per_turn = self._get_config('MAX_RESERVE_CHECKS', MAX_RESERVE_CHECKS_PER_TURN)

        # Battle thresholds - use config if available
        self.battle_favorable_threshold = self._get_config('BATTLE_FAVORABLE_THRESHOLD', BATTLE_FAVORABLE_THRESHOLD)
        self.battle_danger_threshold = self._get_config('BATTLE_DANGER_THRESHOLD', BATTLE_DANGER_THRESHOLD)

        # Battle tracking
        self.battles_won = 0
        self.battles_lost = 0

        logger.info(f"GameStrategy initialized for {my_side} side")

    def _get_config(self, key: str, default):
        """Get config value or return default"""
        if self.config and hasattr(self.config, key):
            return getattr(self.config, key)
        return default

    def reset(self):
        """Reset for new game"""
        self.phase = GamePhase.EARLY
        self.turn_number = 0
        self.my_force_generation = 0
        self.force_generation_target = FORCE_GEN_TARGET_EARLY
        self.force_deficit = FORCE_GEN_TARGET_EARLY

        self.current_focus = StrategyFocus.BALANCED
        self.focus_confidence = 0.5
        self.turns_with_focus = 0
        self.focus_deployments = 0

        self.contested_locations.clear()
        self.dangerous_locations.clear()
        self.location_priorities.clear()

        self.reserve_checks_this_turn = 0
        self.cards_seen_in_reserve.clear()
        self.last_reserve_check_turn = 0

        self.battles_won = 0
        self.battles_lost = 0

        logger.info("GameStrategy reset for new game")

    def start_new_turn(self, turn_number: int):
        """Called at start of each turn"""
        self.turn_number = turn_number
        self.reserve_checks_this_turn = 0

        # Update game phase
        if turn_number <= EARLY_GAME_TURNS:
            self.phase = GamePhase.EARLY
            self.force_generation_target = FORCE_GEN_TARGET_EARLY
        elif turn_number <= MID_GAME_TURNS:
            self.phase = GamePhase.MID
            self.force_generation_target = FORCE_GEN_TARGET_MID
        else:
            self.phase = GamePhase.LATE
            self.force_generation_target = FORCE_GEN_TARGET_LATE

        # Clear old reserve card memory after cooldown
        if turn_number - self.last_reserve_check_turn > RESERVE_CHECK_COOLDOWN_TURNS:
            self.cards_seen_in_reserve.clear()

        logger.debug(f"Turn {turn_number}: Phase={self.phase.value}, Gen target={self.force_generation_target}")

    # =========================================================================
    # Board State Updates
    # =========================================================================

    def update_from_board_state(self, board_state: 'BoardState'):
        """
        Recalculate all strategic metrics from current board state.

        Called each turn or when board state changes significantly.
        """
        self._update_force_generation(board_state)
        self._update_location_priorities(board_state)
        self._detect_focus_from_hand(board_state)

        logger.debug(f"Strategy updated: gen={self.my_force_generation}, deficit={self.force_deficit}, "
                    f"focus={self.current_focus.value}, contested={len(self.contested_locations)}")

    def _update_force_generation(self, board_state: 'BoardState'):
        """Calculate force generation from controlled locations"""
        self.my_force_generation = 0

        for loc in board_state.locations:
            if loc is None or not loc.card_id:
                continue

            # Parse force icons from location
            icons = loc.my_icons if loc.my_icons else ""
            try:
                # Icons might be a number or empty
                if icons.isdigit():
                    self.my_force_generation += int(icons)
            except (ValueError, AttributeError):
                pass

        # Also check dark/light_generation from board state if available
        if board_state.my_side == "dark":
            # Use the server-reported generation if available and higher
            if board_state.dark_generation > self.my_force_generation:
                self.my_force_generation = board_state.dark_generation
        else:
            if board_state.light_generation > self.my_force_generation:
                self.my_force_generation = board_state.light_generation

        # Calculate deficit
        self.force_deficit = self.force_generation_target - self.my_force_generation

    def _update_location_priorities(self, board_state: 'BoardState'):
        """Score all locations for strategic priority"""
        self.location_priorities.clear()
        self.contested_locations.clear()
        self.dangerous_locations.clear()

        for i, loc in enumerate(board_state.locations):
            if loc is None or not loc.card_id:
                continue

            priority = self._score_location(loc, i, board_state)
            self.location_priorities.append(priority)

            if priority.is_contested:
                self.contested_locations.append(i)
            if priority.is_dangerous:
                self.dangerous_locations.append(i)

        # Sort by score (highest first)
        self.location_priorities.sort(key=lambda p: p.score, reverse=True)

    def _score_location(self, location: 'LocationInPlay', index: int,
                        board_state: 'BoardState') -> LocationPriority:
        """
        Score a single location for priority.

        Factors:
        - Force icons controlled (+20 per icon)
        - Battleground status (+15)
        - My presence (+10 if occupied)
        - Enemy presence (+25 if contested)
        - Empty location (+8 for easy control)
        """
        priority = LocationPriority(location_index=index, score=0.0)

        # Force icons
        try:
            icons = int(location.my_icons) if location.my_icons and location.my_icons.isdigit() else 0
            if icons > 0:
                priority.add_reason(f"{icons} force icons", icons * LOCATION_WEIGHT_FORCE_ICON)
        except ValueError:
            pass

        # Battleground status (can battle and drain here)
        # Sites are typically battlegrounds; space locations depend on type
        if location.is_site:
            priority.add_reason("Battleground site", LOCATION_WEIGHT_BATTLEGROUND)

        # My presence (reinforce existing positions)
        if location.my_cards:
            priority.add_reason(f"{len(location.my_cards)} cards here", LOCATION_WEIGHT_MY_PRESENCE)

        # Enemy presence (threat requiring resolution)
        if location.their_cards:
            priority.is_contested = True
            priority.add_reason(f"Contested ({len(location.their_cards)} enemies)",
                              LOCATION_WEIGHT_ENEMY_PRESENCE)

            # Assess threat level
            my_power = board_state.my_power_at_location(index)
            their_power = board_state.their_power_at_location(index)
            priority.threat_level = self._assess_threat_level(my_power, their_power)

            if priority.threat_level in [ThreatLevel.DANGEROUS, ThreatLevel.RETREAT]:
                priority.is_dangerous = True
                priority.add_reason(f"Threat: {priority.threat_level.value}", -10)

        # Empty location (easy control opportunity)
        elif not location.my_cards:
            priority.add_reason("Empty - easy control", LOCATION_WEIGHT_EMPTY)

        return priority

    def _assess_threat_level(self, my_power: int, their_power: int) -> ThreatLevel:
        """Assess threat level at a location based on power differential"""
        if their_power == 0:
            return ThreatLevel.SAFE

        power_diff = my_power - their_power

        # Use configurable thresholds (updated via admin UI)
        favorable = self._get_config('BATTLE_FAVORABLE_THRESHOLD', self.battle_favorable_threshold)
        danger = self._get_config('BATTLE_DANGER_THRESHOLD', self.battle_danger_threshold)
        crush = favorable + 4  # Crush is 4 more than favorable

        if power_diff >= crush:
            return ThreatLevel.CRUSH
        elif power_diff >= favorable:
            return ThreatLevel.FAVORABLE
        elif power_diff >= -favorable:
            return ThreatLevel.RISKY
        elif power_diff >= danger:
            return ThreatLevel.DANGEROUS
        else:
            return ThreatLevel.RETREAT

    # =========================================================================
    # Cross-Turn Strategy
    # =========================================================================

    def _detect_focus_from_hand(self, board_state: 'BoardState'):
        """
        Detect whether we should focus on GROUND or SPACE based on hand contents.

        Only updates focus if confidence is low or we've been balanced for a while.
        """
        if self.focus_confidence > FOCUS_RESET_THRESHOLD and self.current_focus != StrategyFocus.BALANCED:
            # Already have a strong focus, don't reset
            return

        ground_power = 0
        space_power = 0

        for card in board_state.cards_in_hand:
            card_type = card.card_type.lower() if card.card_type else ""
            power = card.power or 0

            if card_type in ["character", "vehicle"]:
                ground_power += power
            elif card_type == "starship":
                space_power += power

        # Determine focus
        if space_power > ground_power * 1.5:
            new_focus = StrategyFocus.SPACE
        elif ground_power > space_power * 1.5:
            new_focus = StrategyFocus.GROUND
        else:
            new_focus = StrategyFocus.BALANCED

        if new_focus != self.current_focus:
            old_focus = self.current_focus
            self.current_focus = new_focus
            self.turns_with_focus = 0
            self.focus_deployments = 0
            self.focus_confidence = 0.5
            logger.info(f"Strategy focus changed: {old_focus.value} -> {new_focus.value}")
        else:
            self.turns_with_focus += 1

    def on_successful_deploy(self, card_type: str):
        """Called when we successfully deploy a card matching our focus"""
        if self._card_matches_focus(card_type):
            self.focus_deployments += 1
            if self.focus_deployments >= 2:
                # Build confidence after successful deployments
                self.focus_confidence = min(1.0,
                    self.focus_confidence + FOCUS_CONFIDENCE_GAIN_ON_SUCCESS)
                logger.debug(f"Focus confidence increased to {self.focus_confidence:.2f}")

    def on_battle_result(self, won: bool):
        """Called after a battle to update focus confidence"""
        if won:
            self.battles_won += 1
        else:
            self.battles_lost += 1
            # Reduce confidence on loss
            self.focus_confidence = max(0.0,
                self.focus_confidence - FOCUS_CONFIDENCE_LOSS_ON_SETBACK)
            logger.debug(f"Battle lost, focus confidence reduced to {self.focus_confidence:.2f}")

            # If confidence is too low, reset focus
            if self.focus_confidence < FOCUS_RESET_THRESHOLD:
                self.current_focus = StrategyFocus.BALANCED
                logger.info(f"Focus reset to BALANCED due to low confidence")

    def _card_matches_focus(self, card_type: str) -> bool:
        """Check if a card type matches our current focus"""
        if self.current_focus == StrategyFocus.BALANCED:
            return False  # No bonus for balanced

        card_type_lower = card_type.lower() if card_type else ""

        if self.current_focus == StrategyFocus.GROUND:
            return card_type_lower in ["character", "vehicle", "site"]
        elif self.current_focus == StrategyFocus.SPACE:
            return card_type_lower in ["starship", "system"]

        return False

    # =========================================================================
    # Public Query Methods
    # =========================================================================

    def get_location_priority(self, location_index: int) -> Optional[LocationPriority]:
        """Get the priority score for a location"""
        for p in self.location_priorities:
            if p.location_index == location_index:
                return p
        return None

    def get_top_priority_locations(self, count: int = 3) -> List[LocationPriority]:
        """Get the top N priority locations"""
        return self.location_priorities[:count]

    def is_location_contested(self, location_index: int) -> bool:
        """Check if a location is contested"""
        return location_index in self.contested_locations

    def is_location_dangerous(self, location_index: int) -> bool:
        """Check if a location is dangerous (enemy has advantage)"""
        return location_index in self.dangerous_locations

    def get_location_threat(self, location_index: int) -> ThreatLevel:
        """Get the threat level at a location"""
        priority = self.get_location_priority(location_index)
        if priority:
            return priority.threat_level
        return ThreatLevel.SAFE

    # =========================================================================
    # Force Generation Bonuses
    # =========================================================================

    def get_location_deploy_bonus(self) -> float:
        """
        Get bonus for deploying location cards based on force deficit.

        Sliding scale: larger deficit = bigger bonus
        """
        if self.force_deficit <= 0:
            return 0.0  # At or above target
        elif self.force_deficit <= 2:
            return 15.0  # Slight need
        elif self.force_deficit <= 4:
            return 30.0  # Moderate need
        else:
            return 50.0  # Critical need

    def get_focus_deploy_bonus(self, card_type: str) -> float:
        """Get bonus for deploying cards that match our focus"""
        if self._card_matches_focus(card_type):
            # Scale by confidence
            return 15.0 * self.focus_confidence
        return 0.0

    # =========================================================================
    # Reserve Deck Management
    # =========================================================================

    def should_check_reserve(self) -> bool:
        """Check if we should look at the reserve deck"""
        return self.reserve_checks_this_turn < self.max_reserve_checks_per_turn

    def record_reserve_check(self, cards_seen: List[str] = None):
        """Record that we checked the reserve deck"""
        self.reserve_checks_this_turn += 1
        self.last_reserve_check_turn = self.turn_number

        if cards_seen:
            self.cards_seen_in_reserve.update(cards_seen)

        logger.debug(f"Reserve check #{self.reserve_checks_this_turn} this turn")

    def is_card_recently_seen_in_reserve(self, blueprint_id: str) -> bool:
        """Check if we recently saw this card in reserve"""
        return blueprint_id in self.cards_seen_in_reserve

    # =========================================================================
    # Force Activation Recommendations
    # =========================================================================

    def get_force_activation_amount(self, max_available: int,
                                    current_force: int,
                                    reserve_size: int) -> int:
        """
        Phase-aware force activation recommendation.

        Args:
            max_available: Maximum force that can be activated
            current_force: Current force pile
            reserve_size: Total reserve deck size

        Returns:
            Recommended activation amount
        """
        # Early game: Activate aggressively to deploy locations
        if self.phase == GamePhase.EARLY:
            target = min(max_available, self.my_force_generation + 2)

        # Mid game: Balance activation with reserve preservation
        elif self.phase == GamePhase.MID:
            target = min(max_available, 8)  # Cap at 8
            if reserve_size < 10:
                target = min(target, max_available - MIN_RESERVE_TO_KEEP)

        # Late game: Conservative - save for destiny draws
        else:
            target = min(max_available, 4, self.my_force_generation)

        # Don't over-activate if we already have a lot
        if current_force > 12:
            target = min(target, 2)

        # Ensure we leave some in reserve
        if reserve_size <= max_available and reserve_size <= 5:
            target = min(target, max(0, reserve_size - MIN_RESERVE_TO_KEEP))

        return max(0, target)

    # =========================================================================
    # Hand Size Management
    # =========================================================================

    def get_effective_soft_cap(self, has_deployable_cards: bool = True) -> int:
        """
        Get dynamic soft cap based on game phase.

        Real players overdraw early (up to 16 cards in turns 1-3) to find
        their key pieces, then tighten up in late game to preserve life force.

        Args:
            has_deployable_cards: True if player has cards they can deploy

        Returns:
            Effective soft cap for hand size
        """
        base_soft_cap = self._get_config('HAND_SOFT_CAP', HAND_SOFT_CAP)
        hard_cap = self._get_config('MAX_HAND_SIZE', HAND_HARD_CAP)

        # Early game (turns 1-3): Allow overdrawing to find key cards
        if self.turn_number <= 3:
            effective_cap = base_soft_cap + 4  # 12 + 4 = 16
        # Mid game (turns 4-6): Normal threshold
        elif self.turn_number <= 6:
            effective_cap = base_soft_cap  # 12
        # Late game (turns 7+): Tighten up to preserve life force
        else:
            effective_cap = base_soft_cap - 4  # 12 - 4 = 8

        # Exception: If no deployable cards, allow extra drawing to find them
        if not has_deployable_cards:
            effective_cap += 2
            logger.debug(f"No deployable cards - allowing +2 extra draw (cap {effective_cap})")

        # Clamp to hard limits
        effective_cap = max(4, min(effective_cap, hard_cap))

        return effective_cap

    def get_hand_size_penalty(self, hand_size: int, has_deployable_cards: bool = True) -> float:
        """
        Get penalty for drawing more cards based on hand size.

        Uses dynamic soft cap based on game phase.
        Returns negative value (penalty) if hand is getting full.
        """
        hard_cap = self._get_config('MAX_HAND_SIZE', HAND_HARD_CAP)
        soft_cap = self.get_effective_soft_cap(has_deployable_cards)

        if hand_size >= hard_cap:
            return -100.0  # Strongly avoid drawing
        elif hand_size >= soft_cap:
            overflow = hand_size - soft_cap
            return -20.0 * overflow  # Increasing penalty
        return 0.0

    def should_prioritize_drawing_for_locations(self, hand_size: int) -> bool:
        """Check if we should draw to find location cards"""
        soft_cap = self.get_effective_soft_cap(has_deployable_cards=True)
        return self.force_deficit > 3 and hand_size < soft_cap

    # =========================================================================
    # Status
    # =========================================================================

    def get_status(self) -> dict:
        """Get current strategy status for monitoring"""
        return {
            'phase': self.phase.value,
            'turn': self.turn_number,
            'force_generation': self.my_force_generation,
            'force_deficit': self.force_deficit,
            'focus': self.current_focus.value,
            'focus_confidence': self.focus_confidence,
            'contested_locations': len(self.contested_locations),
            'dangerous_locations': len(self.dangerous_locations),
            'reserve_checks_this_turn': self.reserve_checks_this_turn,
            'battles_won': self.battles_won,
            'battles_lost': self.battles_lost,
        }

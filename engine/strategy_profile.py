"""
Dynamic Strategy Profile System

Adjusts bot behavior based on game position:
- AGGRESSIVE: When behind - take risks, attack more, deploy faster
- BALANCED: When even - normal play
- DEFENSIVE: When ahead - preserve lead, avoid risky battles
- DESPERATION: When way behind - all-in plays, must take risks

The profile affects scoring multipliers across all evaluators.
"""

import logging
from enum import Enum
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .board_state import BoardState

logger = logging.getLogger(__name__)


class StrategyMode(Enum):
    """Current strategic stance based on game position"""
    DESPERATION = "desperation"   # Way behind - take big risks
    AGGRESSIVE = "aggressive"      # Behind - push harder
    BALANCED = "balanced"          # Even game - normal play
    DEFENSIVE = "defensive"        # Ahead - protect lead
    CRUSHING = "crushing"          # Way ahead - close out safely


@dataclass
class StrategyProfile:
    """
    Multipliers and adjustments for the current strategy.

    Evaluators read these values to adjust their scoring:
    - deploy_multiplier: Affects willingness to deploy (>1 = more aggressive)
    - battle_multiplier: Affects willingness to initiate battles
    - pass_multiplier: Affects pass action scoring (<1 = less passing)
    - risk_tolerance: How much power disadvantage is acceptable for battles
    - force_reserve: How much force to hold back
    """
    mode: StrategyMode

    # Scoring multipliers (1.0 = no change)
    deploy_multiplier: float = 1.0      # >1 deploys more aggressively
    battle_multiplier: float = 1.0      # >1 battles more aggressively
    pass_multiplier: float = 1.0        # <1 reduces pass preference
    draw_multiplier: float = 1.0        # <1 reduces draw preference

    # Threshold adjustments
    risk_tolerance: int = 0             # Power disadvantage tolerance for battles
    force_reserve: int = 1              # Minimum force to hold back
    deploy_threshold_adjustment: int = 0  # Adjustment to deploy power threshold

    # Descriptive
    reason: str = ""                    # Why this profile was chosen


# Pre-defined profiles for each mode
PROFILES = {
    StrategyMode.DESPERATION: StrategyProfile(
        mode=StrategyMode.DESPERATION,
        deploy_multiplier=1.5,      # Deploy more aggressively
        battle_multiplier=1.5,      # Take riskier battles
        pass_multiplier=0.3,        # Almost never pass
        draw_multiplier=0.5,        # Less drawing, more action
        risk_tolerance=4,           # Accept 4-power disadvantage in battles
        force_reserve=0,            # Use all force
        deploy_threshold_adjustment=-2,  # Lower threshold
        reason="DESPERATION: Way behind, must take risks!"
    ),
    StrategyMode.AGGRESSIVE: StrategyProfile(
        mode=StrategyMode.AGGRESSIVE,
        deploy_multiplier=1.25,     # Deploy more
        battle_multiplier=1.25,     # Battle more
        pass_multiplier=0.5,        # Pass less
        draw_multiplier=0.75,       # Draw a bit less
        risk_tolerance=2,           # Accept 2-power disadvantage
        force_reserve=1,            # Keep 1 force
        deploy_threshold_adjustment=-1,
        reason="AGGRESSIVE: Behind, pushing harder"
    ),
    StrategyMode.BALANCED: StrategyProfile(
        mode=StrategyMode.BALANCED,
        deploy_multiplier=1.0,
        battle_multiplier=1.0,
        pass_multiplier=1.0,
        draw_multiplier=1.0,
        risk_tolerance=0,
        force_reserve=1,
        deploy_threshold_adjustment=0,
        reason="BALANCED: Even game"
    ),
    StrategyMode.DEFENSIVE: StrategyProfile(
        mode=StrategyMode.DEFENSIVE,
        deploy_multiplier=0.85,     # Deploy less aggressively
        battle_multiplier=0.75,     # Be pickier about battles
        pass_multiplier=1.25,       # Pass a bit more
        draw_multiplier=1.25,       # Draw more for better options
        risk_tolerance=-2,          # Need +2 power advantage for battles
        force_reserve=2,            # Keep more force in reserve
        deploy_threshold_adjustment=1,
        reason="DEFENSIVE: Ahead, protecting lead"
    ),
    StrategyMode.CRUSHING: StrategyProfile(
        mode=StrategyMode.CRUSHING,
        deploy_multiplier=0.75,     # Very conservative deploys
        battle_multiplier=0.6,      # Only sure-win battles
        pass_multiplier=1.5,        # Pass more often
        draw_multiplier=1.5,        # Build hand for destiny
        risk_tolerance=-4,          # Need big advantage for battles
        force_reserve=3,            # Keep lots of force
        deploy_threshold_adjustment=2,
        reason="CRUSHING: Way ahead, closing out safely"
    ),
}


@dataclass
class GamePosition:
    """Calculated game position metrics"""
    life_force_differential: int = 0   # Positive = we're winning (they lost more)
    reserve_differential: int = 0       # Positive = we have more reserve
    board_power_differential: int = 0   # Positive = we have more board power
    drain_potential_differential: int = 0  # Positive = we drain more
    total_score: int = 0                # Combined position score

    def __str__(self):
        return (f"Position(life={self.life_force_differential:+d}, "
                f"reserve={self.reserve_differential:+d}, "
                f"power={self.board_power_differential:+d}, "
                f"drain={self.drain_potential_differential:+d}, "
                f"total={self.total_score:+d})")


def calculate_game_position(bs: 'BoardState') -> GamePosition:
    """
    Calculate the current game position from board state.

    Returns a GamePosition with various differential metrics.
    Positive values mean we're ahead, negative means behind.
    """
    pos = GamePosition()

    if not bs:
        return pos

    # 1. LIFE FORCE DIFFERENTIAL
    # Win condition: opponent loses when reserve + force_pile + used = 0
    # Lost pile = cards permanently gone
    # Higher lost pile = closer to losing
    my_life = bs.reserve_deck + bs.force_pile + bs.used_pile
    their_life = bs.their_reserve_deck + bs.their_force_pile + bs.their_used_pile
    pos.life_force_differential = their_life - my_life  # Negative if we're healthier
    # Flip sign: positive means they have more life remaining (bad for us? no, they're further from losing)
    # Actually: their_lost_pile - my_lost_pile = positive means they lost more
    pos.life_force_differential = bs.their_lost_pile - bs.lost_pile

    # 2. RESERVE DECK DIFFERENTIAL
    # More cards = more options and further from losing
    pos.reserve_differential = bs.reserve_deck - bs.their_reserve_deck

    # 3. BOARD POWER DIFFERENTIAL
    # Sum up power at all locations
    my_total_power = 0
    their_total_power = 0

    if bs.my_side == "dark":
        for loc_idx, power in bs.dark_power_at_locations.items():
            my_total_power += power
        for loc_idx, power in bs.light_power_at_locations.items():
            their_total_power += power
    else:
        for loc_idx, power in bs.light_power_at_locations.items():
            my_total_power += power
        for loc_idx, power in bs.dark_power_at_locations.items():
            their_total_power += power

    pos.board_power_differential = my_total_power - their_total_power

    # 4. DRAIN POTENTIAL DIFFERENTIAL
    # Count opponent icons at locations we control (our drain potential)
    # vs our icons at locations they control (their drain potential)
    my_drain_potential = 0
    their_drain_potential = 0

    for loc in bs.locations:
        if not loc:
            continue

        # Parse icon strings (e.g., "2" or "2*")
        def parse_icons(icon_str: str) -> int:
            if not icon_str or icon_str == "0":
                return 0
            try:
                return int(icon_str.replace("*", "").strip() or "0")
            except ValueError:
                return 1 if icon_str else 0

        my_icons = parse_icons(loc.my_icons)
        their_icons = parse_icons(loc.their_icons)

        # Check who controls (has presence at) this location
        we_have_presence = len(loc.my_cards) > 0
        they_have_presence = len(loc.their_cards) > 0

        if we_have_presence and not they_have_presence:
            # We control - we can drain their icons
            my_drain_potential += their_icons
        elif they_have_presence and not we_have_presence:
            # They control - they can drain our icons
            their_drain_potential += my_icons

    pos.drain_potential_differential = my_drain_potential - their_drain_potential

    # 5. COMBINED SCORE
    # Weight the factors (life force is most important)
    pos.total_score = (
        pos.life_force_differential * 3 +    # Life is critical
        pos.reserve_differential * 1 +        # Reserve matters
        pos.board_power_differential // 2 +   # Board presence
        pos.drain_potential_differential * 2  # Future damage
    )

    return pos


def determine_strategy_mode(position: GamePosition, turn_number: int = 0) -> StrategyMode:
    """
    Determine the appropriate strategy mode based on game position.

    Thresholds are tuned based on typical SWCCG game flow:
    - Early game (turns 1-4): More aggressive regardless of position
    - Mid/late game: Position-based strategy

    Score interpretation (positive = ahead):
    - CRUSHING: +25 or more (way ahead - play safe)
    - DEFENSIVE: +12 to +24 (comfortably ahead)
    - BALANCED: -12 to +11 (even game)
    - AGGRESSIVE: -30 to -13 (behind - push harder)
    - DESPERATION: -31 or worse (way behind - all-in)
    """
    score = position.total_score

    # Early game bias toward aggression
    if turn_number <= 3:
        # Early game - be more aggressive to establish board
        if score < -25:
            return StrategyMode.DESPERATION
        elif score < -10:
            return StrategyMode.AGGRESSIVE
        elif score < 15:
            return StrategyMode.AGGRESSIVE  # Even early game = aggressive
        else:
            return StrategyMode.BALANCED  # Way ahead early = just play normal

    # Mid/late game - position-based
    if score < -30:
        return StrategyMode.DESPERATION
    elif score < -12:
        return StrategyMode.AGGRESSIVE
    elif score < 12:
        return StrategyMode.BALANCED
    elif score < 25:
        return StrategyMode.DEFENSIVE
    else:
        return StrategyMode.CRUSHING


def get_strategy_profile(bs: 'BoardState') -> StrategyProfile:
    """
    Get the current strategy profile based on board state.

    This is the main entry point for evaluators to get strategy adjustments.
    """
    if not bs:
        return PROFILES[StrategyMode.BALANCED]

    position = calculate_game_position(bs)
    mode = determine_strategy_mode(position, bs.turn_number)
    profile = PROFILES[mode]

    # Log strategy changes (but not every call - only when it matters)
    return StrategyProfile(
        mode=profile.mode,
        deploy_multiplier=profile.deploy_multiplier,
        battle_multiplier=profile.battle_multiplier,
        pass_multiplier=profile.pass_multiplier,
        draw_multiplier=profile.draw_multiplier,
        risk_tolerance=profile.risk_tolerance,
        force_reserve=profile.force_reserve,
        deploy_threshold_adjustment=profile.deploy_threshold_adjustment,
        reason=f"{profile.reason} | {position}"
    )


# Cache to avoid recalculating every decision
_cached_profile: Optional[StrategyProfile] = None
_cached_turn: int = -1
_cached_phase: str = ""


def get_current_profile(bs: 'BoardState') -> StrategyProfile:
    """
    Get the current strategy profile with caching.

    Recalculates at the start of each phase to avoid overhead.
    """
    global _cached_profile, _cached_turn, _cached_phase

    if not bs:
        return PROFILES[StrategyMode.BALANCED]

    # Recalculate at start of each phase or turn
    if (bs.turn_number != _cached_turn or
        bs.current_phase != _cached_phase or
        _cached_profile is None):

        _cached_profile = get_strategy_profile(bs)
        _cached_turn = bs.turn_number
        _cached_phase = bs.current_phase

        # Log the strategy decision
        logger.info(f"📊 Strategy: {_cached_profile.mode.value.upper()} | "
                   f"deploy×{_cached_profile.deploy_multiplier:.2f} "
                   f"battle×{_cached_profile.battle_multiplier:.2f} "
                   f"pass×{_cached_profile.pass_multiplier:.2f}")
        logger.debug(f"   {_cached_profile.reason}")

    return _cached_profile


def reset_strategy_cache():
    """Reset the cached profile (call at game start)"""
    global _cached_profile, _cached_turn, _cached_phase
    _cached_profile = None
    _cached_turn = -1
    _cached_phase = ""

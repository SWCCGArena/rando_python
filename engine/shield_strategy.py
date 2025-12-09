"""
Defensive Shield Strategy for SWCCG Bot.

Based on NARP strategy guides, implements intelligent shield selection:
- Categorizes shields by priority and use case
- Considers game state, turn number, and opponent's deck
- Tracks shields played (4 max per game by default)
- Monitors opponent shields to avoid redundant plays
"""
import logging
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ShieldCategory(Enum):
    """Categories of defensive shields based on when/how to play them."""
    AUTO_PLAY_IMMEDIATE = "auto_immediate"   # Play immediately (turn 1-2)
    AUTO_PLAY_EARLY = "auto_early"           # Play early before opponent drains
    SITUATIONAL_HIGH = "situational_high"    # Play based on opponent deck/actions
    SITUATIONAL_MEDIUM = "situational_medium"# Context-dependent
    LOW_PRIORITY = "low_priority"            # Rarely needed
    NEVER = "never"                          # Obsolete or virtual version exists


@dataclass
class ShieldInfo:
    """Information about a defensive shield."""
    name: str
    blueprint_ids: List[str]  # Can have multiple versions (V, non-V)
    category: ShieldCategory
    description: str
    # Conditions that increase priority
    play_if_opponent_has: List[str] = field(default_factory=list)  # Card names/keywords
    play_if_we_have: List[str] = field(default_factory=list)       # Our card names/keywords
    play_if_opponent_objective: List[str] = field(default_factory=list)  # Objective names
    # Turn timing
    max_turn_to_play: int = 99  # Play by this turn or lose value
    min_turn_to_play: int = 0   # Don't play before this turn


# =============================================================================
# DARK SIDE SHIELD DEFINITIONS
# =============================================================================

DARK_SHIELDS = {
    # === AUTO PLAY IMMEDIATELY ===
    "Allegations Of Corruption": ShieldInfo(
        name="Allegations Of Corruption",
        blueprint_ids=["13_52"],
        category=ShieldCategory.AUTO_PLAY_IMMEDIATE,
        description="Grabber - grab opponent's key Used Interrupt",
        max_turn_to_play=2,
    ),
    "Secret Plans": ShieldInfo(
        name="Secret Plans",
        blueprint_ids=["13_86"],
        category=ShieldCategory.AUTO_PLAY_IMMEDIATE,
        description="Makes retrieval cost 1 force per card",
        max_turn_to_play=3,
    ),

    # === AUTO PLAY EARLY ===
    "Battle Order": ShieldInfo(
        name="Battle Order",
        blueprint_ids=["13_54"],
        category=ShieldCategory.AUTO_PLAY_EARLY,
        description="Opponent pays 3 to drain without both theaters",
        max_turn_to_play=4,
    ),
    "Come Here You Big Coward": ShieldInfo(
        name="Come Here You Big Coward",
        blueprint_ids=["13_61", "225_3"],  # Non-V and V
        category=ShieldCategory.AUTO_PLAY_EARLY,
        description="Punishes stacking at one battleground, stops retrieval",
        max_turn_to_play=5,
    ),

    # === SITUATIONAL HIGH ===
    "A Useless Gesture (V)": ShieldInfo(
        name="A Useless Gesture (V)",
        blueprint_ids=["223_7"],  # Combo with Death Star Sentry
        category=ShieldCategory.SITUATIONAL_HIGH,
        description="Limits Watch Your Step lost pile plays",
        play_if_opponent_objective=["watch your step"],
    ),
    "Do They Have A Code Clearance?": ShieldInfo(
        name="Do They Have A Code Clearance?",
        blueprint_ids=["13_66"],
        category=ShieldCategory.SITUATIONAL_HIGH,
        description="Grabs retrieval interrupts, reduces all retrieval by 1",
        play_if_opponent_has=["kessel run", "death star plans", "on the edge", "harvest", "jedi levitation"],
    ),
    "Firepower (V)": ShieldInfo(
        name="Firepower (V)",
        blueprint_ids=["200_95"],  # Fanfare (V) blueprint - need to verify
        category=ShieldCategory.SITUATIONAL_HIGH,
        description="Damage when opponent moves away, retrieval when in both theaters",
        play_if_opponent_has=["dodge", "path of least resistance", "run luke run", "hyper escape"],
    ),
    "We'll Let Fate-a Decide, Huh?": ShieldInfo(
        name="We'll Let Fate-a Decide, Huh?",
        blueprint_ids=["13_96", "223_26"],
        category=ShieldCategory.SITUATIONAL_HIGH,
        description="Cancels Sabacc, Beggar, Frozen Assets",
        play_if_opponent_has=["sabacc", "beggar", "frozen assets", "draw their fire"],
    ),
    "You Cannot Hide Forever (V)": ShieldInfo(
        name="You Cannot Hide Forever (V)",
        blueprint_ids=["200_100"],
        category=ShieldCategory.SITUATIONAL_HIGH,
        description="Stops Podracing damage, cancels inserts",
    ),

    # === SITUATIONAL MEDIUM ===
    "Resistance": ShieldInfo(
        name="Resistance",
        blueprint_ids=["13_84"],
        category=ShieldCategory.SITUATIONAL_MEDIUM,
        description="Limits force drains to 2 if we occupy 3 battlegrounds",
    ),
    "There Is No Try": ShieldInfo(
        name="There Is No Try",
        blueprint_ids=["13_90"],
        category=ShieldCategory.SITUATIONAL_MEDIUM,
        description="Anti-Sense/Alter (punishes both players)",
        play_if_opponent_has=["sense", "alter"],
    ),
    "Oppressive Enforcement": ShieldInfo(
        name="Oppressive Enforcement",
        blueprint_ids=["13_81"],
        category=ShieldCategory.SITUATIONAL_MEDIUM,
        description="Anti-Sense/Alter (only helps us)",
        play_if_opponent_has=["sense", "alter"],
        play_if_we_have=["sense", "alter"],  # Better if we use our own
    ),
    "Vote Of No Confidence": ShieldInfo(
        name="Vote Of No Confidence",
        blueprint_ids=["200_99"],
        category=ShieldCategory.SITUATIONAL_MEDIUM,
        description="Stops Senate political effects, Stone Pile",
        play_if_opponent_objective=["senate"],
        play_if_opponent_has=["stone pile"],
    ),
    "Weapon Of A Sith": ShieldInfo(
        name="Weapon Of A Sith",
        blueprint_ids=["13_95"],
        category=ShieldCategory.SITUATIONAL_MEDIUM,
        description="Stops Weapon Levitation stealing our sabers",
        play_if_opponent_has=["weapon levitation"],
        play_if_we_have=["lightsaber"],
    ),
    "I Find Your Lack Of Faith Disturbing (V)": ShieldInfo(
        name="I Find Your Lack Of Faith Disturbing (V)",
        blueprint_ids=["200_95"],  # Need to verify blueprint
        category=ShieldCategory.SITUATIONAL_MEDIUM,
        description="Anti-Combat deck",
        play_if_opponent_objective=["let them make the first move", "we'll handle this"],
    ),

    # === LOW PRIORITY ===
    "Death Star Sentry (V)": ShieldInfo(
        name="Death Star Sentry (V)",
        blueprint_ids=["223_7"],  # Combo card
        category=ShieldCategory.LOW_PRIORITY,
        description="Stops non-unique swarms, cancels Colo Claw Fish",
    ),
    "Fanfare (V)": ShieldInfo(
        name="Fanfare (V)",
        blueprint_ids=["200_95"],
        category=ShieldCategory.LOW_PRIORITY,
        description="Pull immediate effect, protect from Lost In The Wilderness",
    ),
    "Wipe Them Out, All Of Them": ShieldInfo(
        name="Wipe Them Out, All Of Them",
        blueprint_ids=["13_98"],
        category=ShieldCategory.LOW_PRIORITY,
        description="Limits non-unique destiny adding",
        play_if_opponent_objective=["watch your step"],  # Palace Raiders
    ),

    # === NEVER PLAY (obsolete) ===
    "A Useless Gesture": ShieldInfo(
        name="A Useless Gesture",
        blueprint_ids=["13_51"],
        category=ShieldCategory.NEVER,
        description="Virtual version is better",
    ),
    "Crossfire": ShieldInfo(
        name="Crossfire",
        blueprint_ids=["13_63"],
        category=ShieldCategory.NEVER,
        description="S-foils rarely played, V version exists",
    ),
    "Leave Them To Me": ShieldInfo(
        name="Leave Them To Me",
        blueprint_ids=["13_72"],
        category=ShieldCategory.NEVER,
        description="Operatives are dead",
    ),
    "No Escape": ShieldInfo(
        name="No Escape",
        blueprint_ids=["13_78"],
        category=ShieldCategory.NEVER,
        description="Very niche, effect version is better",
    ),
    "You Cannot Hide Forever": ShieldInfo(
        name="You Cannot Hide Forever",
        blueprint_ids=["13_99"],
        category=ShieldCategory.NEVER,
        description="Virtual version is better",
    ),
    "You've Never Won A Race?": ShieldInfo(
        name="You've Never Won A Race?",
        blueprint_ids=["13_100"],
        category=ShieldCategory.NEVER,
        description="Virtual version is better",
    ),
}


# =============================================================================
# LIGHT SIDE SHIELD DEFINITIONS
# =============================================================================

LIGHT_SHIELDS = {
    # === AUTO PLAY IMMEDIATELY ===
    "A Tragedy Has Occurred": ShieldInfo(
        name="A Tragedy Has Occurred",
        blueprint_ids=["13_3"],
        category=ShieldCategory.AUTO_PLAY_IMMEDIATE,
        description="Grabber - grab opponent's key Used Interrupt",
        max_turn_to_play=2,
    ),
    "Aim High": ShieldInfo(
        name="Aim High",
        blueprint_ids=["13_4"],
        category=ShieldCategory.AUTO_PLAY_IMMEDIATE,
        description="Makes retrieval cost 1 force per card",
        max_turn_to_play=3,
    ),

    # === AUTO PLAY EARLY ===
    "Battle Plan": ShieldInfo(
        name="Battle Plan",
        blueprint_ids=["13_8"],
        category=ShieldCategory.AUTO_PLAY_EARLY,
        description="Opponent pays 3 to drain without both theaters",
        max_turn_to_play=4,
    ),
    "Simple Tricks And Nonsense": ShieldInfo(
        name="Simple Tricks And Nonsense",
        blueprint_ids=["200_28"],
        category=ShieldCategory.AUTO_PLAY_EARLY,
        description="Punishes stacking, stops drains at non-BGs if < 2 BGs",
        max_turn_to_play=5,
    ),
    "Goldenrod": ShieldInfo(
        name="Goldenrod",
        blueprint_ids=["223_49"],  # Combo with Yavin Sentry
        category=ShieldCategory.AUTO_PLAY_EARLY,
        description="Makes Blizzard 4 deploys cost 2, Executor cost 2",
        max_turn_to_play=3,
        play_if_opponent_has=["blizzard 4", "they must never again leave this city"],
    ),

    # === SITUATIONAL HIGH ===
    "Weapons Display (V)": ShieldInfo(
        name="Weapons Display (V)",
        blueprint_ids=["200_30"],  # Need to verify
        category=ShieldCategory.SITUATIONAL_HIGH,
        description="Damage when opponent excludes from battle, retrieval in both theaters",
        play_if_opponent_has=["imperial barrier", "stunning leader", "you are beaten", "force push"],
    ),
    "Your Insight Serves You Well (V)": ShieldInfo(
        name="Your Insight Serves You Well (V)",
        blueprint_ids=["200_32"],
        category=ShieldCategory.SITUATIONAL_HIGH,
        description="Stops Podracing damage, Scanning Crew, inserts",
    ),

    # === SITUATIONAL MEDIUM ===
    "Ultimatum": ShieldInfo(
        name="Ultimatum",
        blueprint_ids=["13_44"],
        category=ShieldCategory.SITUATIONAL_MEDIUM,
        description="Limits force drains to 2 if we occupy 3 battlegrounds",
    ),
    "Do, Or Do Not": ShieldInfo(
        name="Do, Or Do Not",
        blueprint_ids=["13_15"],
        category=ShieldCategory.SITUATIONAL_MEDIUM,
        description="Anti-Sense/Alter (punishes both players)",
        play_if_opponent_has=["sense", "alter"],
    ),
    "Wise Advice": ShieldInfo(
        name="Wise Advice",
        blueprint_ids=["13_47"],
        category=ShieldCategory.SITUATIONAL_MEDIUM,
        description="Anti-Sense/Alter (only helps us)",
        play_if_opponent_has=["sense", "alter"],
    ),
    "The Republic No Longer Functions": ShieldInfo(
        name="The Republic No Longer Functions",
        blueprint_ids=["200_29"],
        category=ShieldCategory.SITUATIONAL_MEDIUM,
        description="Stops Senate, Scanning Crew, M'iiyoom Onith",
        play_if_opponent_objective=["senate"],
        play_if_opponent_has=["scanning crew"],
    ),
    "Only Jedi Carry That Weapon": ShieldInfo(
        name="Only Jedi Carry That Weapon",
        blueprint_ids=["13_35", "221_68"],  # Non-V and V
        category=ShieldCategory.SITUATIONAL_MEDIUM,
        description="Stops Weapon Levitation stealing our sabers",
        play_if_opponent_has=["weapon levitation"],
        play_if_we_have=["lightsaber"],
    ),
    "Affect Mind (V)": ShieldInfo(
        name="Affect Mind (V)",
        blueprint_ids=["200_25"],  # Need to verify
        category=ShieldCategory.SITUATIONAL_MEDIUM,
        description="Anti-Combat deck",
        play_if_opponent_objective=["let them make the first move", "we'll handle this"],
    ),

    # === LOW PRIORITY ===
    "Don't Do That Again (V)": ShieldInfo(
        name="Don't Do That Again (V)",
        blueprint_ids=["200_26"],
        category=ShieldCategory.LOW_PRIORITY,
        description="Stops Mobilization Points, protects from Always Thinking",
    ),
    "Yavin Sentry (V)": ShieldInfo(
        name="Yavin Sentry (V)",
        blueprint_ids=["223_49"],  # Combo card
        category=ShieldCategory.LOW_PRIORITY,
        description="Stops non-unique swarms, cancels Colo Claw Fish",
    ),
    "He Can Go About His Business": ShieldInfo(
        name="He Can Go About His Business",
        blueprint_ids=["13_22"],
        category=ShieldCategory.LOW_PRIORITY,
        description="Stops Brangus Glee shenanigans",
    ),
    "Your Ship?": ShieldInfo(
        name="Your Ship?",
        blueprint_ids=["13_50", "301_5"],
        category=ShieldCategory.LOW_PRIORITY,
        description="Stops Sabacc",
        play_if_opponent_has=["sabacc", "brangus glee", "4-lom"],
    ),

    # === NEVER PLAY (obsolete) ===
    "A Close Race": ShieldInfo(
        name="A Close Race",
        blueprint_ids=["13_1"],
        category=ShieldCategory.NEVER,
        description="Your Insight Serves You Well (V) is strictly better",
    ),
    "Another Pathetic Lifeform": ShieldInfo(
        name="Another Pathetic Lifeform",
        blueprint_ids=["13_6"],
        category=ShieldCategory.NEVER,
        description="Not very effective",
    ),
    "Don't Do That Again": ShieldInfo(
        name="Don't Do That Again",
        blueprint_ids=["13_16"],
        category=ShieldCategory.NEVER,
        description="Virtual version is better",
    ),
    "Let's Keep A Little Optimism Here": ShieldInfo(
        name="Let's Keep A Little Optimism Here",
        blueprint_ids=["13_30"],
        category=ShieldCategory.NEVER,
        description="Operatives are dead",
    ),
    "Planetary Defenses": ShieldInfo(
        name="Planetary Defenses",
        blueprint_ids=["13_38"],
        category=ShieldCategory.NEVER,
        description="Virtual version is better",
    ),
    "Your Insight Serves You Well": ShieldInfo(
        name="Your Insight Serves You Well",
        blueprint_ids=["13_49"],
        category=ShieldCategory.NEVER,
        description="Virtual version is better",
    ),
}


class ShieldTracker:
    """
    Tracks shield state during a game and provides strategic advice.
    """

    def __init__(self, my_side: str):
        """
        Initialize shield tracker.

        Args:
            my_side: "dark" or "light"
        """
        self.my_side = my_side.lower()
        self.shields_played: Set[str] = set()  # Blueprint IDs we've played
        self.max_shields = 4  # Default, can be increased by cards
        self.opponent_shields: Set[str] = set()  # Blueprint IDs opponent has
        self.opponent_cards_seen: Set[str] = set()  # Card titles we've seen opponent play
        self.opponent_objective: Optional[str] = None

        # Shield pacing - don't play all shields immediately
        # This allows us to see opponent's strategy before committing
        # Format: {turn_number: max_shields_by_that_turn}
        self.shield_pacing = {
            1: 2,  # Play at most 2 shields on turn 1
            2: 3,  # Play at most 3 shields by turn 2
            3: 4,  # Play all 4 shields by turn 3
        }

        # Get the right shield database
        self.shield_db = DARK_SHIELDS if self.my_side == "dark" else LIGHT_SHIELDS

    def shields_remaining(self) -> int:
        """How many shields can we still play?"""
        return max(0, self.max_shields - len(self.shields_played))

    def shields_allowed_this_turn(self, turn_number: int) -> int:
        """How many shields should we have played by this turn (pacing limit)."""
        # Find the applicable limit for this turn
        for turn in sorted(self.shield_pacing.keys(), reverse=True):
            if turn_number >= turn:
                return self.shield_pacing[turn]
        return 0  # Turn 0 or negative - no shields yet

    def at_pacing_cap(self, turn_number: int) -> bool:
        """Check if we've reached our shield pacing cap for this turn.

        Shield pacing prevents playing all 4 shields on turn 1, allowing
        us to see opponent's strategy before committing all shield slots.
        """
        shields_played = len(self.shields_played)
        max_for_turn = self.shields_allowed_this_turn(turn_number)
        at_cap = shields_played >= max_for_turn
        if at_cap:
            logger.debug(f"🛡️ At shield pacing cap: {shields_played}/{max_for_turn} for turn {turn_number}")
        return at_cap

    def record_shield_played(self, blueprint_id: str, card_title: str):
        """Record that we played a shield."""
        self.shields_played.add(blueprint_id)
        played = len(self.shields_played)
        remaining = self.shields_remaining()
        logger.info(f"🛡️ Shield #{played} played: {card_title} ({remaining} remaining of {self.max_shields})")

    def record_opponent_shield(self, blueprint_id: str, card_title: str):
        """Record an opponent shield we've seen."""
        self.opponent_shields.add(blueprint_id)
        logger.debug(f"🛡️ Opponent shield: {card_title}")

    def record_opponent_card(self, card_title: str):
        """Record a card title opponent has played (for situational shields)."""
        self.opponent_cards_seen.add(card_title.lower())

    def set_opponent_objective(self, objective_title: str):
        """Record opponent's objective."""
        self.opponent_objective = objective_title.lower()
        logger.info(f"🎯 Opponent objective: {objective_title}")

    def _check_conditions(self, shield: ShieldInfo, turn_number: int,
                          board_state=None) -> Tuple[bool, List[str]]:
        """
        Check if shield conditions are met.

        Returns:
            (should_play, reasons) - whether to play and why
        """
        reasons = []

        # Check turn timing
        if turn_number > shield.max_turn_to_play:
            # Late to play this shield, reduced value
            reasons.append(f"Past optimal turn ({shield.max_turn_to_play})")

        # Check opponent objective conditions
        if shield.play_if_opponent_objective and self.opponent_objective:
            for obj in shield.play_if_opponent_objective:
                if obj.lower() in self.opponent_objective:
                    reasons.append(f"Opponent plays {obj}")
                    return True, reasons

        # Check opponent card conditions
        if shield.play_if_opponent_has:
            for card in shield.play_if_opponent_has:
                if card.lower() in self.opponent_cards_seen:
                    reasons.append(f"Opponent has {card}")
                    return True, reasons

        # For auto-play shields, always play early
        if shield.category in (ShieldCategory.AUTO_PLAY_IMMEDIATE, ShieldCategory.AUTO_PLAY_EARLY):
            if turn_number <= shield.max_turn_to_play:
                reasons.append("Auto-play shield (early)")
                return True, reasons

        return len(reasons) > 0, reasons

    def score_shield(self, blueprint_id: str, card_title: str,
                     turn_number: int = 1, board_state=None) -> float:
        """
        Score a defensive shield for deployment priority.

        Args:
            blueprint_id: The card's blueprint ID
            card_title: The card's title
            turn_number: Current game turn
            board_state: Optional board state for context

        Returns:
            Score from 0-200 indicating priority, or -200 if at pacing cap
        """
        # Check if already played (shouldn't happen but be safe)
        if blueprint_id in self.shields_played:
            return -100.0

        # SHIELD PACING: Don't play too many shields early
        # This reserves shield slots to respond to opponent's strategy
        if self.at_pacing_cap(turn_number):
            # At pacing cap - return very low score to prevent playing
            # (but not -100 to differentiate from "never play" shields)
            logger.info(f"🛡️ {card_title}: Holding back (pacing cap for turn {turn_number})")
            return -50.0

        # Find the shield info
        shield_info = None
        for name, info in self.shield_db.items():
            if blueprint_id in info.blueprint_ids:
                shield_info = info
                break
            # Also check by title match
            if name.lower() in card_title.lower():
                shield_info = info
                break

        if not shield_info:
            # Unknown shield - give moderate priority
            logger.debug(f"Unknown shield: {card_title} ({blueprint_id})")
            return 50.0

        # Base score by category
        category_scores = {
            ShieldCategory.AUTO_PLAY_IMMEDIATE: 200.0,
            ShieldCategory.AUTO_PLAY_EARLY: 150.0,
            ShieldCategory.SITUATIONAL_HIGH: 100.0,
            ShieldCategory.SITUATIONAL_MEDIUM: 75.0,
            ShieldCategory.LOW_PRIORITY: 25.0,
            ShieldCategory.NEVER: -100.0,
        }
        score = category_scores.get(shield_info.category, 50.0)

        # Check conditions
        should_play, reasons = self._check_conditions(shield_info, turn_number, board_state)

        if should_play and reasons:
            # Boost score if conditions are met
            score += 50.0
            for reason in reasons:
                logger.debug(f"🛡️ {card_title}: +50 ({reason})")

        # Timing adjustments
        if turn_number > shield_info.max_turn_to_play:
            # Reduce score for late shields (less impactful)
            late_penalty = min(50.0, (turn_number - shield_info.max_turn_to_play) * 10)
            score -= late_penalty
            logger.debug(f"🛡️ {card_title}: -{late_penalty} (turn {turn_number} > max {shield_info.max_turn_to_play})")

        # Early game bonus for auto-play shields
        if shield_info.category == ShieldCategory.AUTO_PLAY_IMMEDIATE and turn_number <= 2:
            score += 25.0

        # Shields remaining affects urgency
        if self.shields_remaining() <= 1:
            # Last shield - be more selective
            if shield_info.category in (ShieldCategory.LOW_PRIORITY, ShieldCategory.SITUATIONAL_MEDIUM):
                score -= 30.0

        return score

    def get_shield_advice(self, available_shields: List[Tuple[str, str]],
                          turn_number: int, board_state=None) -> List[Tuple[str, str, float, str]]:
        """
        Get prioritized shield recommendations.

        Args:
            available_shields: List of (blueprint_id, title) tuples
            turn_number: Current game turn
            board_state: Optional board state

        Returns:
            List of (blueprint_id, title, score, reason) sorted by score
        """
        scored = []
        for bp_id, title in available_shields:
            score = self.score_shield(bp_id, title, turn_number, board_state)

            # Generate reason
            shield_info = None
            for name, info in self.shield_db.items():
                if bp_id in info.blueprint_ids or name.lower() in title.lower():
                    shield_info = info
                    break

            if shield_info:
                reason = f"{shield_info.category.value}: {shield_info.description}"
            else:
                reason = "Unknown shield"

            scored.append((bp_id, title, score, reason))

        # Sort by score descending
        scored.sort(key=lambda x: x[2], reverse=True)

        return scored


# Global tracker instance (created per game)
_shield_tracker: Optional[ShieldTracker] = None


def get_shield_tracker(my_side: str = None) -> Optional[ShieldTracker]:
    """Get or create the shield tracker for current game.

    IMPORTANT: If a side is provided and doesn't match the existing tracker's side,
    the tracker is reset. This handles cases where the side changed between games.
    """
    global _shield_tracker
    if _shield_tracker is None and my_side:
        _shield_tracker = ShieldTracker(my_side)
    elif _shield_tracker is not None and my_side and _shield_tracker.my_side != my_side.lower():
        # Side changed - reset tracker (likely a new game with different side)
        logger.warning(f"🛡️ Shield tracker side mismatch: tracker={_shield_tracker.my_side}, requested={my_side.lower()}. Resetting.")
        _shield_tracker = ShieldTracker(my_side)
    return _shield_tracker


def reset_shield_tracker():
    """Reset tracker for new game."""
    global _shield_tracker
    _shield_tracker = None


def score_shield_for_deployment(blueprint_id: str, card_title: str,
                                 turn_number: int, my_side: str,
                                 board_state=None) -> Tuple[float, str]:
    """
    Convenience function to score a shield.

    Returns:
        (score, reason) tuple
    """
    tracker = get_shield_tracker(my_side)
    if not tracker:
        tracker = ShieldTracker(my_side)

    score = tracker.score_shield(blueprint_id, card_title, turn_number, board_state)

    # Find reason
    shield_db = DARK_SHIELDS if my_side.lower() == "dark" else LIGHT_SHIELDS
    reason = "Unknown"
    for name, info in shield_db.items():
        if blueprint_id in info.blueprint_ids or name.lower() in card_title.lower():
            reason = f"{info.category.value}: {info.description}"
            break

    return score, reason

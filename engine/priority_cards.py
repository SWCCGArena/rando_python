"""
Priority Cards Module

Defines high-priority interrupts and effects that the bot should:
1. Protect from being lost/discarded
2. Use strategically (not randomly)

Based on analysis of 67 production bot decks - these cards appear in 15%+ of decks
and have significant strategic value.

Card categories:
- DEFENSIVE: Barrier cards, damage cancellation (Houjix/Ghhhk)
- DESTINY: Destiny manipulation (Jedi Levitation, Sith Fury)
- PROTECTION: Character protection (Blaster Deflection, Odin Nesloor)
- UTILITY: Cancel/retrieve cards
"""

import logging
from typing import Optional, Set, TYPE_CHECKING
from dataclasses import dataclass
from enum import Enum

if TYPE_CHECKING:
    from .board_state import BoardState

logger = logging.getLogger(__name__)


class CardCategory(Enum):
    """Categories of priority cards"""
    DEFENSIVE = "defensive"      # Barrier cards - prevent opponent actions
    DAMAGE_CANCEL = "damage_cancel"  # Houjix/Ghhhk - cancel battle damage
    DESTINY = "destiny"          # Destiny manipulation
    DESTINY_BONUS = "destiny_bonus"  # +X to destiny (battle, weapon)
    PROTECTION = "protection"    # Character protection
    UTILITY = "utility"          # General utility/cancel
    RETRIEVAL = "retrieval"      # Card retrieval from piles
    STARTING = "starting"        # Starting effects (usually shouldn't lose)


@dataclass
class PriorityCard:
    """Definition of a priority card with usage conditions"""
    blueprint_id: str
    title: str
    category: CardCategory
    side: str  # "light" or "dark"
    protection_score: float  # How much to penalize losing this card (higher = more protected)
    usage_notes: str  # Human-readable usage guidance


# ============================================================================
# HIGH PRIORITY INTERRUPTS - Protect these from loss, use strategically
# ============================================================================

PRIORITY_INTERRUPTS = {
    # ----- BARRIER CARDS (34% of decks) -----
    # Use when: Opponent just deployed to a contested location
    # Hold when: No contested locations, or opponent hasn't deployed threat
    "1_249": PriorityCard(
        blueprint_id="1_249",
        title="Imperial Barrier",
        category=CardCategory.DEFENSIVE,
        side="dark",
        protection_score=80.0,
        usage_notes="Use when opponent deploys to contested location"
    ),
    "1_105": PriorityCard(
        blueprint_id="1_105",
        title="Rebel Barrier",
        category=CardCategory.DEFENSIVE,
        side="light",
        protection_score=80.0,
        usage_notes="Use when opponent deploys to contested location"
    ),

    # ----- BATTLE DESTINY MODIFIERS (31% / 18% of decks) -----
    # Starting Interrupts with USED function: "+1 to battle destiny just drawn"
    # Use when: In battle, just drew a battle destiny (always beneficial)
    "9_51": PriorityCard(
        blueprint_id="9_51",
        title="Heading For The Medical Frigate",
        category=CardCategory.DESTINY_BONUS,
        side="light",
        protection_score=65.0,
        usage_notes="USED: +1 to battle destiny just drawn - almost always use"
    ),
    "9_139": PriorityCard(
        blueprint_id="9_139",
        title="Prepared Defenses",
        category=CardCategory.DESTINY_BONUS,
        side="dark",
        protection_score=65.0,
        usage_notes="USED: +1 to battle destiny just drawn - almost always use"
    ),

    # ----- DESTINY MANIPULATION (34% / 22% of decks) -----
    # Use when: Just drew a good character for destiny, or need to redraw bad destiny
    # Hold when: Not in battle/destiny situation
    "200_54": PriorityCard(
        blueprint_id="200_54",
        title="Jedi Levitation (V)",
        category=CardCategory.DESTINY,
        side="light",
        protection_score=90.0,
        usage_notes="Use to take good character destiny into hand or redraw bad destiny"
    ),
    "200_123": PriorityCard(
        blueprint_id="200_123",
        title="Sith Fury (V)",
        category=CardCategory.DESTINY,
        side="dark",
        protection_score=90.0,
        usage_notes="Use to take good character destiny into hand or redraw bad destiny"
    ),

    # ----- BATTLE DAMAGE CANCELLATION (28% / 24% of decks) -----
    # Use when: Lost battle with no cards left to forfeit, facing lethal damage
    # Hold when: Not in immediate danger of losing game
    "2_50": PriorityCard(
        blueprint_id="2_50",
        title="Houjix",
        category=CardCategory.DAMAGE_CANCEL,
        side="light",
        protection_score=100.0,  # Critical survival card
        usage_notes="CRITICAL: Save for when losing battle with no forfeit options"
    ),
    "2_132": PriorityCard(
        blueprint_id="2_132",
        title="Ghhhk",
        category=CardCategory.DAMAGE_CANCEL,
        side="dark",
        protection_score=100.0,  # Critical survival card
        usage_notes="CRITICAL: Save for when losing battle with no forfeit options"
    ),

    # ----- CHARACTER PROTECTION (25% of decks) -----
    "6_61": PriorityCard(
        blueprint_id="6_61",
        title="Blaster Deflection",
        category=CardCategory.PROTECTION,
        side="light",
        protection_score=70.0,
        usage_notes="Use when opponent targets ability > 4 character with weapon"
    ),
    "209_21": PriorityCard(
        blueprint_id="209_21",
        title="Odin Nesloor & First Aid",
        category=CardCategory.PROTECTION,
        side="light",
        protection_score=75.0,
        usage_notes="Use when valuable character about to be hit"
    ),

    # ----- WEAPON ENHANCEMENT (30% of decks) -----
    "10_23": PriorityCard(
        blueprint_id="10_23",
        title="Sorry About The Mess & Blaster Proficiency",
        category=CardCategory.UTILITY,
        side="light",
        protection_score=60.0,
        usage_notes="Use when firing blaster to boost weapon destiny"
    ),

    # ----- COMMAND/RETRIEVAL (19% / 16% of decks) -----
    "9_137": PriorityCard(
        blueprint_id="9_137",
        title="Imperial Command",
        category=CardCategory.RETRIEVAL,
        side="dark",
        protection_score=65.0,
        usage_notes="Use to retrieve admiral/general or add battle destiny"
    ),
    "203_17": PriorityCard(
        blueprint_id="203_17",
        title="Rebel Leadership (V)",
        category=CardCategory.RETRIEVAL,
        side="light",
        protection_score=65.0,
        usage_notes="Use to retrieve admiral/general or add battle destiny"
    ),

    # ----- UTILITY/CANCEL (24-25% of decks) -----
    "210_24": PriorityCard(
        blueprint_id="210_24",
        title="Quite A Mercenary (V)",
        category=CardCategory.UTILITY,
        side="light",
        protection_score=55.0,
        usage_notes="Use to cancel opponent effects or retrieve smuggler"
    ),
    "204_18": PriorityCard(
        blueprint_id="204_18",
        title="Escape Pod & We're Doomed",
        category=CardCategory.UTILITY,
        side="light",
        protection_score=55.0,
        usage_notes="Use to prevent Force loss from occupations"
    ),
    "12_152": PriorityCard(
        blueprint_id="12_152",
        title="Masterful Move & Endor Occupation",
        category=CardCategory.UTILITY,
        side="dark",
        protection_score=55.0,
        usage_notes="Use to cancel opponent celebrations"
    ),
    "201_13": PriorityCard(
        blueprint_id="201_13",
        title="Hear Me Baby, Hold Together (V)",
        category=CardCategory.UTILITY,
        side="light",
        protection_score=60.0,
        usage_notes="Use to play Defensive Shield or cancel opponent cards"
    ),

    # ----- COMBO INTERRUPTS (16% of decks) -----
    "11_29": PriorityCard(
        blueprint_id="11_29",
        title="A Jedi's Resilience",
        category=CardCategory.PROTECTION,
        side="light",
        protection_score=70.0,
        usage_notes="Use when about to lose a duel"
    ),
    "209_48": PriorityCard(
        blueprint_id="209_48",
        title="Lana Dobreed & Sacrifice",
        category=CardCategory.UTILITY,
        side="dark",
        protection_score=55.0,
        usage_notes="Utility interrupt"
    ),
    "10_39": PriorityCard(
        blueprint_id="10_39",
        title="Ghhhk & Those Rebels Won't Escape Us",
        category=CardCategory.DAMAGE_CANCEL,
        side="dark",
        protection_score=90.0,
        usage_notes="Combo version of Ghhhk - save for critical moments"
    ),

    # ----- SENSE/CONTROL/ALTER -----
    "1_267": PriorityCard(
        blueprint_id="1_267",
        title="Sense",
        category=CardCategory.UTILITY,
        side="dark",
        protection_score=85.0,
        usage_notes="Use to cancel opponent's Used or Lost Interrupts"
    ),
    "1_108": PriorityCard(
        blueprint_id="1_108",
        title="Sense",
        category=CardCategory.UTILITY,
        side="light",
        protection_score=85.0,
        usage_notes="Use to cancel opponent's Used or Lost Interrupts"
    ),
    # Control - cancels Sense/Alter, Immediate/Mobile Effects, force drains
    "4_139": PriorityCard(
        blueprint_id="4_139",
        title="Control",
        category=CardCategory.UTILITY,
        side="dark",
        protection_score=80.0,
        usage_notes="Cancel Sense/Alter, effects, or force drains"
    ),
    "4_62": PriorityCard(
        blueprint_id="4_62",
        title="Control",
        category=CardCategory.UTILITY,
        side="light",
        protection_score=80.0,
        usage_notes="Cancel Sense/Alter, effects, or force drains"
    ),
    # Alter - less common but still valuable
    "1_217": PriorityCard(
        blueprint_id="1_217",
        title="Alter",
        category=CardCategory.UTILITY,
        side="dark",
        protection_score=70.0,
        usage_notes="Cancel opponent's Utinni Effects or Force drain modifiers"
    ),
    "1_69": PriorityCard(
        blueprint_id="1_69",
        title="Alter",
        category=CardCategory.UTILITY,
        side="light",
        protection_score=70.0,
        usage_notes="Cancel opponent's Utinni Effects or Force drain modifiers"
    ),
}

# ============================================================================
# HIGH PRIORITY EFFECTS - Protect these from loss
# ============================================================================

PRIORITY_EFFECTS = {
    # ----- STARTING EFFECTS (48% / 43% of decks) -----
    "200_35": PriorityCard(
        blueprint_id="200_35",
        title="Anger, Fear, Aggression (V)",
        category=CardCategory.STARTING,
        side="light",
        protection_score=100.0,  # Never lose starting effect
        usage_notes="Starting effect - deploy Defensive Shields"
    ),
    "200_110": PriorityCard(
        blueprint_id="200_110",
        title="Knowledge And Defense (V)",
        category=CardCategory.STARTING,
        side="dark",
        protection_score=100.0,  # Never lose starting effect
        usage_notes="Starting effect - deploy Defensive Shields"
    ),

    # ----- FORCE GENERATION (36% of decks) -----
    "200_47": PriorityCard(
        blueprint_id="200_47",
        title="Wokling (V)",
        category=CardCategory.UTILITY,
        side="light",
        protection_score=75.0,
        usage_notes="Personal Force generation - keep deployed"
    ),
}

# Combine all priority cards for easy lookup
ALL_PRIORITY_CARDS = {**PRIORITY_INTERRUPTS, **PRIORITY_EFFECTS}

# Blueprint IDs as sets for fast lookup
PRIORITY_INTERRUPT_IDS: Set[str] = set(PRIORITY_INTERRUPTS.keys())
PRIORITY_EFFECT_IDS: Set[str] = set(PRIORITY_EFFECTS.keys())
ALL_PRIORITY_IDS: Set[str] = set(ALL_PRIORITY_CARDS.keys())

# ============================================================================
# SENSE/CONTROL TARGET LISTS - Cards worth canceling with Sense/Control
# ============================================================================

# High-value interrupt titles to Sense (cancel) - ordered by priority
# These are the opponent's cards that are most valuable to cancel
HIGH_VALUE_SENSE_TARGETS = [
    # Critical - 100 protection score
    ("houjix", 100),          # Cancels all remaining battle damage
    ("ghhhk", 100),           # DS version of Houjix

    # Very High - 90 protection score
    ("jedi levitation", 90),  # Destiny manipulation
    ("sith fury", 90),        # Destiny manipulation

    # High - 80-85 protection score
    ("sense", 85),            # Cancel our Sense with their Sense
    ("barrier", 80),          # Imperial/Rebel Barrier

    # Medium-High - 70 protection score
    ("blaster deflection", 70),  # Weapon protection
    ("odin nesloor", 70),     # Character protection
    ("jedi's resilience", 70),  # Duel protection

    # Medium - 60-65 protection score
    ("escape pod", 65),       # Escape mechanics
    ("medical frigate", 65),  # Heading For The Medical Frigate
    ("prepared defenses", 65), # Starting interrupt
    ("rebel leadership", 65),  # Command interrupt
    ("imperial command", 65),  # Command interrupt

    # Also valuable but not in our priority list
    ("nabrun leids", 60),     # Movement/escape
    ("elis helrot", 60),      # Movement/escape
    ("hyper escape", 60),     # Movement/escape
    ("alter", 55),            # Cancels Sense
    ("control", 55),          # Cancels Sense
]

# Convert to lowercase patterns for text matching
SENSE_TARGET_PATTERNS = {pattern.lower(): score for pattern, score in HIGH_VALUE_SENSE_TARGETS}


def get_sense_target_value(text: str) -> tuple[bool, int, str]:
    """
    Check if text contains a high-value Sense target.

    Used when deciding whether to play Sense on an opponent's interrupt.
    Parses action text like "Cancel [card name]" to identify valuable targets.

    Args:
        text: Action text or decision text to check

    Returns:
        (is_high_value, score, matched_pattern) tuple
        - is_high_value: True if found a valuable target
        - score: Priority score (0-100, higher = more valuable to cancel)
        - matched_pattern: The pattern that matched (for logging)
    """
    text_lower = text.lower()

    best_score = 0
    best_pattern = ""

    for pattern, score in SENSE_TARGET_PATTERNS.items():
        if pattern in text_lower:
            if score > best_score:
                best_score = score
                best_pattern = pattern

    if best_score > 0:
        return True, best_score, best_pattern
    return False, 0, ""


def is_priority_card(blueprint_id: str) -> bool:
    """Check if a card is in the priority list"""
    return blueprint_id in ALL_PRIORITY_IDS


def is_priority_interrupt(blueprint_id: str) -> bool:
    """Check if a card is a priority interrupt"""
    return blueprint_id in PRIORITY_INTERRUPT_IDS


def is_priority_effect(blueprint_id: str) -> bool:
    """Check if a card is a priority effect"""
    return blueprint_id in PRIORITY_EFFECT_IDS


def get_priority_card(blueprint_id: str) -> Optional[PriorityCard]:
    """Get priority card info, or None if not a priority card"""
    return ALL_PRIORITY_CARDS.get(blueprint_id)


def get_protection_score(blueprint_id: str) -> float:
    """
    Get the protection score for a card (how much to penalize losing it).

    Returns:
        0.0 for non-priority cards
        50-100 for priority cards (higher = more protected)
    """
    card = ALL_PRIORITY_CARDS.get(blueprint_id)
    return card.protection_score if card else 0.0


def get_card_category(blueprint_id: str) -> Optional[CardCategory]:
    """Get the category of a priority card"""
    card = ALL_PRIORITY_CARDS.get(blueprint_id)
    return card.category if card else None


# ============================================================================
# USAGE CONDITION CHECKS - When should we use specific cards?
# ============================================================================

def should_use_barrier(board_state: 'BoardState', target_card_id: str) -> tuple[bool, str]:
    """
    Check if we should use a barrier card on a target.

    Barrier cards (Imperial Barrier, Rebel Barrier) prevent a just-deployed
    card from battling or moving. They're valuable when:
    1. Target is at a contested location (we have presence there)
    2. Target is a threat (high power or abilities)

    Args:
        board_state: Current game state
        target_card_id: The card we're considering barriering

    Returns:
        (should_use, reason) tuple
    """
    if not board_state:
        return False, "No board state"

    target_card = board_state.cards_in_play.get(target_card_id)
    if not target_card:
        return False, "Target card not found"

    # Find the location
    loc_idx = target_card.location_index
    if loc_idx < 0 or loc_idx >= len(board_state.locations):
        return False, "Target location not found"

    location = board_state.locations[loc_idx]

    # Check if location is contested (both players have presence)
    has_my_presence = len(location.my_cards) > 0
    has_their_presence = len(location.their_cards) > 0

    if not has_my_presence:
        return False, "We have no presence at this location - barrier not useful"

    if not has_their_presence:
        # This shouldn't happen since we're barriering their card, but check anyway
        return False, "Opponent has no presence - save barrier"

    # Location is contested - barrier is valuable!
    return True, f"Contested location ({location.site_name or location.system_name}) - use barrier"


def should_use_damage_cancel(board_state: 'BoardState') -> tuple[bool, str]:
    """
    Check if we should use Houjix/Ghhhk to cancel battle damage.

    These are CRITICAL survival cards. Use when:
    1. We lost the battle
    2. We have no cards left to forfeit
    3. Remaining damage would be significant

    Args:
        board_state: Current game state

    Returns:
        (should_use, reason) tuple
    """
    if not board_state:
        return False, "No board state"

    # Check if we're in a battle
    if not getattr(board_state, 'in_battle', False):
        return False, "Not in battle"

    # Get our attrition/damage remaining
    my_side = getattr(board_state, 'my_side', 'dark').lower()
    if my_side == 'dark':
        attrition = getattr(board_state, 'dark_attrition_remaining', 0)
        damage = getattr(board_state, 'dark_damage_remaining', 0)
    else:
        attrition = getattr(board_state, 'light_attrition_remaining', 0)
        damage = getattr(board_state, 'light_damage_remaining', 0)

    total_damage = attrition + damage

    if total_damage <= 0:
        return False, "No damage to cancel"

    # Check if we have cards to forfeit at battle location
    battle_loc_idx = getattr(board_state, 'current_battle_location', -1)
    if battle_loc_idx >= 0 and battle_loc_idx < len(board_state.locations):
        battle_loc = board_state.locations[battle_loc_idx]
        cards_to_forfeit = len(battle_loc.my_cards)
        if cards_to_forfeit > 0:
            return False, f"Still have {cards_to_forfeit} cards to forfeit - save Houjix/Ghhhk"

    # No cards to forfeit and significant damage - use it!
    if total_damage >= 3:
        return True, f"CRITICAL: {total_damage} damage with no forfeit options - use damage cancel!"
    else:
        return True, f"{total_damage} damage with no forfeit - consider using"


def should_use_destiny_manipulation(
    board_state: 'BoardState',
    destiny_value: int,
    is_character_destiny: bool
) -> tuple[bool, str]:
    """
    Check if we should use Jedi Levitation/Sith Fury to manipulate destiny.

    Use when:
    1. Just drew a high-value character for destiny -> take into hand
    2. Just drew a low destiny when we need high -> redraw

    Args:
        board_state: Current game state
        destiny_value: The destiny value drawn
        is_character_destiny: Whether the drawn card was a character

    Returns:
        (should_use, reason) tuple
    """
    if not is_character_destiny:
        return False, "Not a character destiny - can't use"

    # If destiny is very high (5+), might want to take into hand
    if destiny_value >= 5:
        return True, f"High destiny ({destiny_value}) character - consider taking into hand"

    # If destiny is very low (1-2) and we're in battle losing, might want to redraw
    if destiny_value <= 2:
        return True, f"Low destiny ({destiny_value}) - consider redrawing"

    return False, f"Destiny value {destiny_value} is acceptable"


# ============================================================================
# CARD TITLE PATTERN MATCHING (for cards we identify by title, not blueprint)
# ============================================================================

def is_priority_card_by_title(card_title: str) -> bool:
    """
    Check if a card is high-priority based on its title.

    Used when we don't have the blueprint ID but have the title.
    """
    if not card_title:
        return False

    title_lower = card_title.lower()

    # Phase 1: Critical interrupts
    # Damage cancel cards
    if "houjix" in title_lower or "ghhhk" in title_lower:
        return True

    # Sense/Alter
    if title_lower in ["sense", "alter"]:
        return True

    # Barrier cards
    if "barrier" in title_lower and ("imperial" in title_lower or "rebel" in title_lower):
        return True

    # Destiny manipulation
    if "jedi levitation" in title_lower or "sith fury" in title_lower:
        return True

    # Phase 2: Common interrupts
    # Battle destiny modifiers
    if "heading for the medical frigate" in title_lower or "prepared defenses" in title_lower:
        return True

    # Character protection
    if "blaster deflection" in title_lower or "odin nesloor" in title_lower:
        return True

    # Weapon enhancement
    if "sorry about the mess" in title_lower:
        return True

    # Command cards
    if "imperial command" in title_lower or "rebel leadership" in title_lower:
        return True

    return False


def get_protection_score_by_title(card_title: str) -> float:
    """
    Get protection score based on card title (fallback when no blueprint).
    """
    if not card_title:
        return 0.0

    title_lower = card_title.lower()

    # Phase 1: Critical cards (highest priority - survival)
    if "houjix" in title_lower or "ghhhk" in title_lower:
        return 100.0

    # Phase 1: High priority - key interrupts
    if title_lower in ["sense", "alter"]:
        return 85.0
    if "jedi levitation" in title_lower or "sith fury" in title_lower:
        return 90.0
    if "barrier" in title_lower:
        return 80.0

    # Phase 2: Medium-high priority - common useful interrupts
    # Character protection
    if "blaster deflection" in title_lower:
        return 70.0
    if "odin nesloor" in title_lower:
        return 75.0

    # Battle destiny modifiers
    if "heading for the medical frigate" in title_lower or "prepared defenses" in title_lower:
        return 65.0

    # Command cards
    if "imperial command" in title_lower or "rebel leadership" in title_lower:
        return 65.0

    # Weapon enhancement
    if "sorry about the mess" in title_lower:
        return 60.0

    return 0.0

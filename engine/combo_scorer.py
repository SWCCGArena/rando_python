"""
Card Combo Recognition System

Parses combo data from card JSON and provides scoring for synergistic deployments.

The combo field contains strings like:
- "Card1 + Card2" - simple two-card combo
- "Card1 + Card2 + Card3" - multi-card combo
- "Card1 + Card2 Description of effect" - combo with effect description

This module:
1. Parses combo strings to extract card name relationships
2. Builds bidirectional lookup tables (card A combos with B, and B combos with A)
3. Scores deployment decisions based on combo potential
"""

import logging
import re
from typing import Dict, Set, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Scoring constants
COMBO_BONUS_ON_BOARD = 40.0    # Bonus when combo partner is already deployed
COMBO_BONUS_IN_HAND = 15.0    # Bonus when combo partner is in hand (future potential)
COMBO_BONUS_SAME_LOCATION = 20.0  # Extra bonus if partner is at same location

# Phrases that indicate description text (not card names)
DESCRIPTION_MARKERS = [
    "battle destini",  # "Two battle destinies", "battle destiny"
    "cancel",
    "retrieve",
    "deploy",
    "power +",
    "force +",
    "at same",
    "during",
    "immune",
    "may not",
    "opponent",
    "once per",
    "lost interrupt",
    "used interrupt",
]


@dataclass
class ComboData:
    """Holds parsed combo relationships for the game"""
    # Maps card title (lowercase) -> set of combo partner titles (lowercase)
    combos_by_title: Dict[str, Set[str]] = field(default_factory=dict)
    # Maps blueprint_id -> set of combo partner titles (lowercase)
    combos_by_blueprint: Dict[str, Set[str]] = field(default_factory=dict)
    # Total combos parsed
    total_combos: int = 0
    # Cards with combo data
    cards_with_combos: int = 0


# Global combo data - loaded once at startup
_combo_data: Optional[ComboData] = None


def _is_likely_description(text: str) -> bool:
    """Check if text looks like a description rather than a card name"""
    text_lower = text.lower()

    # Very long text is likely a description
    if len(text) > 50:
        return True

    # Check for description marker phrases
    for marker in DESCRIPTION_MARKERS:
        if marker in text_lower:
            return True

    return False


def _parse_combo_string(combo_str: str) -> List[str]:
    """
    Parse a combo string to extract card names.

    Examples:
        "Card1 + Card2" -> ["card1", "card2"]
        "Card1 + Card2 Description text" -> ["card1", "card2"]
        "Card1 + Card2 + Card3" -> ["card1", "card2", "card3"]

    Returns list of card names (lowercase for matching)
    """
    # Split by " + " to get potential card names
    parts = combo_str.split(" + ")

    card_names = []
    for i, part in enumerate(parts):
        part = part.strip()

        if not part:
            continue

        # The last part might contain description text after the card name
        # e.g., "Card2 Two battle destinies"
        if i == len(parts) - 1 and _is_likely_description(part):
            # Try to extract just the card name (first few words before description)
            # Card names typically don't have lowercase words like "two", "and", etc.
            words = part.split()
            card_name_words = []
            for word in words:
                # Stop when we hit a likely description word
                if word.lower() in ['two', 'three', 'four', 'five', 'cancel', 'retrieve',
                                   'during', 'power', 'force', 'once', 'may', 'and']:
                    break
                card_name_words.append(word)

            if card_name_words:
                part = " ".join(card_name_words)
            else:
                continue  # Skip if we couldn't extract a card name

        # Skip if still looks like description
        if _is_likely_description(part):
            continue

        # Clean up the card name
        # Remove leading bullet/dot if present (•)
        part = part.lstrip('•').strip()

        if part and len(part) > 1:  # Must be at least 2 chars
            card_names.append(part.lower())

    return card_names


def _load_combo_data() -> ComboData:
    """Load and parse combo data from card JSON files"""
    import json
    import os

    data = ComboData()

    # Find card JSON directory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    json_dir = os.path.join(os.path.dirname(base_dir), 'swccg-card-json')

    if not os.path.exists(json_dir):
        logger.warning(f"Card JSON directory not found: {json_dir}")
        return data

    # Load both Dark and Light side cards
    for filename in ['Dark.json', 'Light.json']:
        filepath = os.path.join(json_dir, filename)
        if not os.path.exists(filepath):
            logger.warning(f"Card file not found: {filepath}")
            continue

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                card_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load {filepath}: {e}")
            continue

        for card in card_data.get('cards', []):
            combo_list = card.get('combo', [])
            if not combo_list:
                continue

            data.cards_with_combos += 1

            # Get this card's info
            blueprint_id = card.get('gempId', '')
            card_title = card.get('front', {}).get('title', '').lower().lstrip('•').strip()

            if not card_title:
                continue

            # Parse each combo string
            for combo_str in combo_list:
                card_names = _parse_combo_string(combo_str)

                if len(card_names) < 2:
                    continue  # Need at least 2 cards for a combo

                data.total_combos += 1

                # Add bidirectional relationships for all cards in this combo
                for i, name1 in enumerate(card_names):
                    for j, name2 in enumerate(card_names):
                        if i != j:
                            # Add to title lookup
                            if name1 not in data.combos_by_title:
                                data.combos_by_title[name1] = set()
                            data.combos_by_title[name1].add(name2)

                # Also index by blueprint for faster lookup
                if blueprint_id:
                    if blueprint_id not in data.combos_by_blueprint:
                        data.combos_by_blueprint[blueprint_id] = set()
                    # Add all combo partners (excluding self)
                    for name in card_names:
                        if name != card_title:
                            data.combos_by_blueprint[blueprint_id].add(name)

    logger.info(f"🔗 Loaded combo data: {data.cards_with_combos} cards, "
                f"{data.total_combos} combo relationships, "
                f"{len(data.combos_by_title)} unique cards indexed")

    return data


def get_combo_data() -> ComboData:
    """Get the global combo data, loading if necessary"""
    global _combo_data
    if _combo_data is None:
        _combo_data = _load_combo_data()
    return _combo_data


def get_combo_partners(card_title: str = None, blueprint_id: str = None) -> Set[str]:
    """
    Get all combo partners for a card.

    Args:
        card_title: Card title to look up (case-insensitive)
        blueprint_id: Blueprint ID to look up

    Returns:
        Set of card titles (lowercase) that combo with this card
    """
    data = get_combo_data()
    partners = set()

    if blueprint_id and blueprint_id in data.combos_by_blueprint:
        partners.update(data.combos_by_blueprint[blueprint_id])

    if card_title:
        title_lower = card_title.lower().lstrip('•').strip()
        if title_lower in data.combos_by_title:
            partners.update(data.combos_by_title[title_lower])

    return partners


def score_combo_potential(
    card_title: str,
    blueprint_id: str,
    cards_on_board: List[str],
    cards_in_hand: List[str],
    same_location_cards: List[str] = None
) -> Tuple[float, str]:
    """
    Score a card's combo potential based on current game state.

    Args:
        card_title: Title of card being considered for deployment
        blueprint_id: Blueprint ID of the card
        cards_on_board: List of card titles already deployed (ours)
        cards_in_hand: List of card titles in our hand
        same_location_cards: List of card titles at the target deploy location

    Returns:
        Tuple of (score_bonus, reasoning_string)
    """
    partners = get_combo_partners(card_title=card_title, blueprint_id=blueprint_id)

    if not partners:
        return 0.0, ""

    score = 0.0
    reasons = []

    # Normalize card lists for matching
    board_lower = {c.lower().lstrip('•').strip() for c in cards_on_board if c}
    hand_lower = {c.lower().lstrip('•').strip() for c in cards_in_hand if c}
    location_lower = {c.lower().lstrip('•').strip() for c in (same_location_cards or []) if c}

    # Check for combo partners on board
    board_matches = partners.intersection(board_lower)
    for match in board_matches:
        score += COMBO_BONUS_ON_BOARD
        # Extra bonus if at same location
        if match in location_lower:
            score += COMBO_BONUS_SAME_LOCATION
            reasons.append(f"Combo with {match} at location!")
        else:
            reasons.append(f"Combo with {match} on board")

    # Check for combo partners in hand (future potential)
    hand_matches = partners.intersection(hand_lower)
    for match in hand_matches:
        if match not in board_lower:  # Don't double count
            score += COMBO_BONUS_IN_HAND
            reasons.append(f"Combo partner {match} in hand")

    if score > 0:
        reason_str = f"COMBO: {', '.join(reasons[:3])}"  # Limit to 3 reasons
        if len(reasons) > 3:
            reason_str += f" (+{len(reasons)-3} more)"
        return score, reason_str

    return 0.0, ""


def log_combo_stats():
    """Log statistics about loaded combo data"""
    data = get_combo_data()

    logger.info("=== COMBO DATA STATISTICS ===")
    logger.info(f"Cards with combo data: {data.cards_with_combos}")
    logger.info(f"Total combo relationships: {data.total_combos}")
    logger.info(f"Unique cards indexed by title: {len(data.combos_by_title)}")
    logger.info(f"Unique cards indexed by blueprint: {len(data.combos_by_blueprint)}")

    # Find cards with most combo partners
    if data.combos_by_title:
        top_cards = sorted(data.combos_by_title.items(), key=lambda x: len(x[1]), reverse=True)[:5]
        logger.info("Top 5 most connected cards:")
        for title, partners in top_cards:
            logger.info(f"  {title}: {len(partners)} combo partners")


# Initialize combo data at module load
def init_combos():
    """Initialize combo data (call during app startup)"""
    get_combo_data()
    log_combo_stats()

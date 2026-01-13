"""
Gametext Parser

Parses card gametext to extract structured ability information.
Used to enhance AI decision-making beyond raw card stats.

Pattern coverage (from card JSON analysis):
- immune_attrition: 441 cards (e.g., "Immune to attrition < 4")
- power_plus: 336 cards (e.g., "Power +2 when with Luke")
- forfeit_plus: 172 cards (e.g., "Forfeit +1 while at a site")
- deploy_minus: 186 cards (e.g., "Deploy -2 to same location as Vader")
- may_not_target: 36 cards (e.g., "May not be targeted by weapons")
- force_drain_plus: 103 cards (e.g., "Force drain +1 here")
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass
class ParsedAbility:
    """A single parsed ability from gametext."""
    ability_type: str           # e.g., "immune_attrition", "power_mod"
    value: int = 0              # Numeric value (threshold, bonus amount)
    condition: str = ""         # Condition text (e.g., "when with Luke")
    raw_text: str = ""          # Original matched text


@dataclass
class ParsedGametext:
    """All parsed abilities from a card's gametext."""
    # Immunity
    immune_attrition: int = 0           # Threshold (e.g., 4 means "immune to attrition < 4")
    immune_to_sense: bool = False
    immune_to_alter: bool = False

    # May not be targeted
    may_not_be_targeted: bool = False
    target_immunity_text: str = ""      # What they're immune to (e.g., "weapons")

    # Stat modifiers (unconditional base values)
    power_bonus: int = 0                # Unconditional power bonus
    forfeit_bonus: int = 0              # Unconditional forfeit bonus
    deploy_reduction: int = 0           # Deploy cost reduction

    # Conditional modifiers (list of conditions)
    conditional_power: List[ParsedAbility] = field(default_factory=list)
    conditional_forfeit: List[ParsedAbility] = field(default_factory=list)

    # Force drain modifiers
    force_drain_bonus: int = 0          # Force drain +X at this location

    # Destiny modifiers
    adds_destiny_to_power: int = 0      # "Adds X to power of anything he pilots"
    adds_destiny_to_attrition: int = 0  # "Adds X to attrition"
    draws_extra_destiny: int = 0        # "Draw X destiny" during battle

    # Combat abilities
    can_fire_twice: bool = False        # "May fire twice per battle"

    # All parsed abilities for debugging
    all_abilities: List[ParsedAbility] = field(default_factory=list)

    @property
    def has_immunity(self) -> bool:
        """Check if card has any form of immunity."""
        return (self.immune_attrition > 0 or
                self.immune_to_sense or
                self.immune_to_alter or
                self.may_not_be_targeted)

    @property
    def has_combat_bonus(self) -> bool:
        """Check if card has combat-relevant bonuses."""
        return (self.power_bonus > 0 or
                self.adds_destiny_to_power > 0 or
                self.draws_extra_destiny > 0 or
                len(self.conditional_power) > 0)


def parse_gametext(gametext: str) -> ParsedGametext:
    """
    Parse gametext and extract structured abilities.

    Args:
        gametext: Raw gametext string from card JSON

    Returns:
        ParsedGametext with all extracted abilities
    """
    if not gametext:
        return ParsedGametext()

    result = ParsedGametext()
    gt_lower = gametext.lower()

    # =================================================================
    # IMMUNITY PATTERNS
    # =================================================================

    # Immune to attrition < X
    # Matches: "Immune to attrition < 4", "immune to attrition < 5"
    match = re.search(r'immune to attrition\s*[<≤]\s*(\d+)', gt_lower)
    if match:
        result.immune_attrition = int(match.group(1))
        result.all_abilities.append(ParsedAbility(
            ability_type="immune_attrition",
            value=result.immune_attrition,
            raw_text=match.group(0)
        ))

    # Immune to Sense
    if 'immune to sense' in gt_lower:
        result.immune_to_sense = True
        result.all_abilities.append(ParsedAbility(
            ability_type="immune_sense",
            raw_text="immune to sense"
        ))

    # Immune to Alter
    if 'immune to alter' in gt_lower:
        result.immune_to_alter = True
        result.all_abilities.append(ParsedAbility(
            ability_type="immune_alter",
            raw_text="immune to alter"
        ))

    # May not be targeted
    # Matches: "may not be targeted by weapons", "may not be targeted by Sniper"
    match = re.search(r'may not be targeted\s*(by\s+[^.]+)?', gt_lower)
    if match:
        result.may_not_be_targeted = True
        result.target_immunity_text = match.group(1) or ""
        result.all_abilities.append(ParsedAbility(
            ability_type="may_not_target",
            condition=result.target_immunity_text,
            raw_text=match.group(0)
        ))

    # =================================================================
    # POWER MODIFIERS
    # =================================================================

    # Power +X patterns
    # Look for unconditional vs conditional
    power_matches = re.finditer(r'power\s*\+\s*(\d+)(\s+(?:when|while|if|at|for each)[^.]*)?', gt_lower)
    for match in power_matches:
        value = int(match.group(1))
        condition = (match.group(2) or "").strip()

        ability = ParsedAbility(
            ability_type="power_mod",
            value=value,
            condition=condition,
            raw_text=match.group(0)
        )
        result.all_abilities.append(ability)

        if condition:
            result.conditional_power.append(ability)
        else:
            # Unconditional - but be careful, most are conditional
            # Only count as unconditional if no condition follows
            result.power_bonus = max(result.power_bonus, value)

    # =================================================================
    # FORFEIT MODIFIERS
    # =================================================================

    forfeit_matches = re.finditer(r'forfeit\s*\+\s*(\d+)(\s+(?:when|while|if|at)[^.]*)?', gt_lower)
    for match in forfeit_matches:
        value = int(match.group(1))
        condition = (match.group(2) or "").strip()

        ability = ParsedAbility(
            ability_type="forfeit_mod",
            value=value,
            condition=condition,
            raw_text=match.group(0)
        )
        result.all_abilities.append(ability)

        if condition:
            result.conditional_forfeit.append(ability)
        else:
            result.forfeit_bonus = max(result.forfeit_bonus, value)

    # =================================================================
    # DEPLOY COST REDUCTION
    # =================================================================

    # Deploy -X patterns (note: hyphen and minus sign variants)
    match = re.search(r'deploy\s*[-−]\s*(\d+)', gt_lower)
    if match:
        result.deploy_reduction = int(match.group(1))
        result.all_abilities.append(ParsedAbility(
            ability_type="deploy_reduction",
            value=result.deploy_reduction,
            raw_text=match.group(0)
        ))

    # =================================================================
    # FORCE DRAIN MODIFIERS
    # =================================================================

    match = re.search(r'force drain\s*\+\s*(\d+)', gt_lower)
    if match:
        result.force_drain_bonus = int(match.group(1))
        result.all_abilities.append(ParsedAbility(
            ability_type="force_drain_bonus",
            value=result.force_drain_bonus,
            raw_text=match.group(0)
        ))

    # =================================================================
    # DESTINY MODIFIERS
    # =================================================================

    # "Adds X to power of anything he/she pilots"
    match = re.search(r'adds?\s+(\d+)\s+to\s+(?:the\s+)?power\s+of\s+anything\s+(?:he|she)\s+pilots', gt_lower)
    if match:
        result.adds_destiny_to_power = int(match.group(1))
        result.all_abilities.append(ParsedAbility(
            ability_type="pilot_power_bonus",
            value=result.adds_destiny_to_power,
            raw_text=match.group(0)
        ))

    # "Draw X destiny" (extra battle destiny)
    # Handle both numeric ("2") and word ("one", "two") forms
    word_to_num = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}
    match = re.search(r'draws?\s+(\d+|one|two|three|four|five)\s+(?:battle\s+)?destin(?:y|ies)', gt_lower)
    if match:
        num_str = match.group(1)
        result.draws_extra_destiny = word_to_num.get(num_str, int(num_str) if num_str.isdigit() else 1)
        result.all_abilities.append(ParsedAbility(
            ability_type="extra_destiny",
            value=result.draws_extra_destiny,
            raw_text=match.group(0)
        ))

    # =================================================================
    # COMBAT ABILITIES
    # =================================================================

    # "May fire twice"
    if 'may fire twice' in gt_lower or 'fire twice per battle' in gt_lower:
        result.can_fire_twice = True
        result.all_abilities.append(ParsedAbility(
            ability_type="fire_twice",
            raw_text="may fire twice"
        ))

    return result


def get_immunity_summary(parsed: ParsedGametext) -> str:
    """Get a human-readable summary of immunities."""
    parts = []
    if parsed.immune_attrition > 0:
        parts.append(f"attrition<{parsed.immune_attrition}")
    if parsed.immune_to_sense:
        parts.append("Sense")
    if parsed.immune_to_alter:
        parts.append("Alter")
    if parsed.may_not_be_targeted:
        parts.append(f"targeting({parsed.target_immunity_text or 'some'})")
    return ", ".join(parts) if parts else "none"

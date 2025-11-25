"""
Decision Safety Module

Provides guaranteed decision responses to ensure the bot NEVER hangs.
This is the "last line of defense" - if all evaluators and fallbacks fail,
this module ensures we still send a valid response to the server.

Design Philosophy:
- EVERY decision must get a response
- A bad decision is better than no decision (game continues vs hangs)
- Log everything for debugging, but never fail silently
"""

import logging
import xml.etree.ElementTree as ET
from typing import Optional, Tuple, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SafetyDecision:
    """A guaranteed safe decision response"""
    decision_id: str
    value: str
    reason: str
    was_emergency: bool = False


class DecisionSafety:
    """
    Provides guaranteed decision responses.

    This class implements the "never hang" principle by ensuring
    every decision type has a valid response, even if we don't
    understand the decision.
    """

    # Decision types we know how to handle
    KNOWN_TYPES = {
        'MULTIPLE_CHOICE',
        'CARD_SELECTION',
        'CARD_ACTION_CHOICE',
        'ACTION_CHOICE',
        'INTEGER',
        'ARBITRARY_CARDS'
    }

    @staticmethod
    def get_emergency_response(decision_element: ET.Element) -> SafetyDecision:
        """
        Get an emergency response for any decision.

        This is the LAST resort - called when all other handlers fail.
        It will ALWAYS return a valid response.

        Strategy:
        1. Try to pass (empty string) if allowed
        2. Pick first option if must choose
        3. Return "0" as absolute fallback
        """
        decision_type = decision_element.get('decisionType', 'UNKNOWN')
        decision_id = decision_element.get('id', '0')
        decision_text = decision_element.get('text', '')

        # Parse key parameters
        parameters = decision_element.findall('.//parameter')
        no_pass = False
        action_ids = []
        card_ids = []
        min_value = 0
        max_value = 0

        for param in parameters:
            name = param.get('name', '')
            value = param.get('value', '')
            if name == 'noPass':
                no_pass = value.lower() == 'true'
            elif name == 'actionId':
                action_ids.append(value)
            elif name == 'cardId':
                card_ids.append(value)
            elif name == 'min':
                min_value = int(value) if value.isdigit() else 0
            elif name == 'max':
                max_value = int(value) if value.isdigit() else 0

        logger.warning(f"EMERGENCY RESPONSE for {decision_type}: '{decision_text[:50]}...'")
        logger.warning(f"  noPass={no_pass}, actions={len(action_ids)}, cards={len(card_ids)}, min={min_value}, max={max_value}")

        # Determine response based on decision type
        if decision_type == 'INTEGER':
            # For INTEGER, use min_value (safer than max - preserves resources)
            response_value = str(min_value)
            reason = f"Emergency: INTEGER decision, using min value {min_value}"

        elif decision_type == 'MULTIPLE_CHOICE':
            # For yes/no questions, try to be conservative
            # Check for dangerous patterns
            text_lower = decision_text.lower()
            if 'concede' in text_lower or 'forfeit' in text_lower or 'surrender' in text_lower:
                response_value = "1"  # Usually "No" is option 1
                reason = "Emergency: Detected concede/forfeit, choosing No"
            else:
                response_value = "0"  # Default to first option
                reason = "Emergency: MULTIPLE_CHOICE, choosing first option"

        elif decision_type in ['CARD_ACTION_CHOICE', 'ACTION_CHOICE']:
            if action_ids:
                response_value = action_ids[0]
                reason = f"Emergency: Choosing first action ({action_ids[0]})"
            elif not no_pass:
                response_value = ""
                reason = "Emergency: No actions, passing"
            else:
                response_value = ""  # Last resort - might fail but won't hang
                reason = "Emergency: No actions and noPass=true, trying empty"

        elif decision_type == 'CARD_SELECTION':
            if card_ids:
                response_value = card_ids[0]
                reason = f"Emergency: Selecting first card ({card_ids[0]})"
            elif min_value == 0:
                response_value = ""
                reason = "Emergency: No cards, min=0, passing"
            else:
                response_value = ""  # Last resort
                reason = "Emergency: No cards but min>0, trying empty"

        elif decision_type == 'ARBITRARY_CARDS':
            if card_ids:
                response_value = card_ids[0]
                reason = f"Emergency: Selecting first arbitrary card ({card_ids[0]})"
            else:
                response_value = ""
                reason = "Emergency: No arbitrary cards, passing"

        else:
            # Completely unknown decision type
            logger.error(f"UNKNOWN DECISION TYPE: {decision_type}")
            if action_ids:
                response_value = action_ids[0]
            elif card_ids:
                response_value = card_ids[0]
            else:
                response_value = "0"  # Absolute fallback
            reason = f"Emergency: Unknown type '{decision_type}', guessing"

        logger.warning(f"  -> Response: '{response_value}' ({reason})")

        return SafetyDecision(
            decision_id=decision_id,
            value=response_value,
            reason=reason,
            was_emergency=True
        )

    @staticmethod
    def validate_response(decision_element: ET.Element, response_value: str) -> Tuple[bool, str]:
        """
        Validate that a response is likely valid for the given decision.

        Returns (is_valid, warning_message)
        """
        decision_type = decision_element.get('decisionType', '')
        parameters = decision_element.findall('.//parameter')

        no_pass = False
        min_value = 0
        action_ids = []
        card_ids = []

        for param in parameters:
            name = param.get('name', '')
            value = param.get('value', '')
            if name == 'noPass':
                no_pass = value.lower() == 'true'
            elif name == 'min':
                min_value = int(value) if value.isdigit() else 0
            elif name == 'actionId':
                action_ids.append(value)
            elif name == 'cardId':
                card_ids.append(value)

        # Validate based on type
        if response_value == "" and no_pass:
            return False, "Empty response but noPass=true - might fail"

        if response_value == "" and min_value > 0:
            return False, f"Empty response but min={min_value} - might fail"

        if decision_type in ['CARD_ACTION_CHOICE', 'ACTION_CHOICE']:
            if response_value and response_value not in action_ids and action_ids:
                return False, f"Response '{response_value}' not in action_ids"

        if decision_type == 'CARD_SELECTION':
            if response_value and response_value not in card_ids and card_ids:
                return False, f"Response '{response_value}' not in card_ids"

        return True, ""

    @staticmethod
    def get_safe_pass_value(decision_element: ET.Element) -> Optional[str]:
        """
        Get a safe "pass" value for the decision, if passing is allowed.

        Returns None if passing is not allowed.
        """
        parameters = decision_element.findall('.//parameter')

        no_pass = False
        min_value = 0

        for param in parameters:
            name = param.get('name', '')
            value = param.get('value', '')
            if name == 'noPass':
                no_pass = value.lower() == 'true'
            elif name == 'min':
                min_value = int(value) if value.isdigit() else 0

        # Can pass if noPass is false and min is 0
        if not no_pass and min_value == 0:
            return ""

        return None


class DecisionTracker:
    """
    Tracks decisions to detect loops and problems.

    Features:
    - Detect repeated identical decisions (potential infinite loop)
    - Track decision response times
    - Log decision history for debugging

    Loop Detection Strategy:
    - Track CONSECUTIVE repeats of the same decision type+text
    - Reset count when a different decision occurs
    - Use higher threshold for expected repeated patterns (e.g., "Optional responses")
    - True loops are when the SAME decision repeats many times IN A ROW
    """

    # Decisions that are expected to repeat many times (not loops)
    EXPECTED_REPEAT_PATTERNS = {
        'Optional responses',  # Responses during opponent's turn
        'Choose Deploy action or Pass',  # Deploy phase choices
        'Choose Move action or Pass',  # Move phase choices
    }

    # Thresholds for loop detection
    CONSECUTIVE_THRESHOLD = 10  # Consecutive same decision (likely stuck)
    EXPECTED_PATTERN_THRESHOLD = 50  # Higher threshold for expected patterns

    def __init__(self, max_history: int = 100):
        self.history: List[dict] = []
        self.max_history = max_history
        self.repeat_counts: dict = {}  # decision_key -> total count (for stats)
        self.consecutive_count: int = 0  # Consecutive repeats of same decision
        self.last_decision_key: str = ""  # Last decision seen

    def record_decision(self, decision_type: str, decision_text: str,
                       decision_id: str, response: str) -> None:
        """Record a decision and response"""
        entry = {
            'type': decision_type,
            'text': decision_text[:100],  # Truncate for memory
            'id': decision_id,
            'response': response,
        }

        self.history.append(entry)

        # Trim history if too long
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

        # Track total repeats (for stats)
        key = f"{decision_type}:{decision_text[:50]}"
        self.repeat_counts[key] = self.repeat_counts.get(key, 0) + 1

        # Track CONSECUTIVE repeats (for loop detection)
        if key == self.last_decision_key:
            self.consecutive_count += 1
        else:
            # Different decision - reset consecutive counter
            self.consecutive_count = 1
            self.last_decision_key = key

    def check_for_loop(self, decision_type: str, decision_text: str,
                       threshold: int = 5) -> Tuple[bool, int]:
        """
        Check if we're in a potential infinite loop.

        Uses CONSECUTIVE repeat count, not total count.
        Uses higher thresholds for expected repeated patterns.

        Returns (is_loop, consecutive_repeat_count)
        """
        key = f"{decision_type}:{decision_text[:50]}"

        # Determine threshold based on pattern
        effective_threshold = self.CONSECUTIVE_THRESHOLD
        for pattern in self.EXPECTED_REPEAT_PATTERNS:
            if pattern in decision_text:
                effective_threshold = self.EXPECTED_PATTERN_THRESHOLD
                break

        # Check consecutive count (only if this is the current decision)
        if key == self.last_decision_key:
            is_loop = self.consecutive_count >= effective_threshold
            return is_loop, self.consecutive_count

        # Different decision - not a loop
        return False, 0

    def reset_repeat_count(self, decision_type: str, decision_text: str) -> None:
        """Reset the repeat count (e.g., after successful progress)"""
        key = f"{decision_type}:{decision_text[:50]}"
        self.repeat_counts[key] = 0
        if key == self.last_decision_key:
            self.consecutive_count = 0

    def get_recent_decisions(self, count: int = 10) -> List[dict]:
        """Get the most recent decisions"""
        return self.history[-count:]

    def clear(self) -> None:
        """Clear all tracking data (e.g., at game start)"""
        self.history.clear()
        self.repeat_counts.clear()
        self.consecutive_count = 0
        self.last_decision_key = ""

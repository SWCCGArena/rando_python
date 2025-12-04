"""
Decision Safety Module

Provides guaranteed decision responses to ensure the bot NEVER hangs.
This is the "last line of defense" - if all evaluators and fallbacks fail,
this module ensures we still send a valid response to the server.

Design Philosophy (from C# BotAIHelper):
- EVERY decision must get a response - NEVER return without posting a decision
- A bad decision is better than no decision (game continues vs hangs)
- If noPass=true OR min>=1, we MUST return a valid choice (not empty string)
- Log everything for debugging, but never fail silently
- Final safety checks at the end of EVERY handler to catch bugs
"""

import logging
import random
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
    def parse_decision_params(decision_element: ET.Element) -> dict:
        """
        Parse all relevant parameters from a decision element.
        Returns a dict with parsed values for easy access.
        """
        params = {
            'decision_type': decision_element.get('decisionType', 'UNKNOWN'),
            'decision_id': decision_element.get('id', '0'),
            'decision_text': decision_element.get('text', ''),
            'no_pass': False,
            'min': 0,
            'max': 0,
            'action_ids': [],
            'card_ids': [],
            'selectable': [],
            'preselected': [],
        }

        for param in decision_element.findall('.//parameter'):
            name = param.get('name', '')
            value = param.get('value', '')
            if name == 'noPass':
                params['no_pass'] = value.lower() == 'true'
            elif name == 'min':
                params['min'] = int(value) if value.isdigit() else 0
            elif name == 'max':
                params['max'] = int(value) if value.isdigit() else 0
            elif name == 'actionId':
                params['action_ids'].append(value)
            elif name == 'cardId':
                params['card_ids'].append(value)
            elif name == 'selectable':
                params['selectable'].append(value.lower() == 'true')
            elif name == 'preselected':
                params['preselected'].append(value.lower() == 'true')

        return params

    @staticmethod
    def get_selectable_options(params: dict) -> List[str]:
        """
        Get list of selectable options (cardIds that are selectable and not preselected).
        This matches the C# 'realChoices' logic.
        """
        card_ids = params.get('card_ids', [])
        selectable = params.get('selectable', [])
        preselected = params.get('preselected', [])

        options = []
        for i, card_id in enumerate(card_ids):
            is_selectable = selectable[i] if i < len(selectable) else True
            is_preselected = preselected[i] if i < len(preselected) else False
            if is_selectable and not is_preselected:
                options.append(card_id)

        return options

    @staticmethod
    def must_choose(params: dict) -> bool:
        """
        Check if we MUST choose something (cannot pass).
        True if noPass=true OR min>=1.
        """
        return params.get('no_pass', False) or params.get('min', 0) >= 1

    @staticmethod
    def can_pass(params: dict) -> bool:
        """
        Check if we CAN pass (return empty string).
        True if noPass=false AND min=0.
        """
        return not params.get('no_pass', False) and params.get('min', 0) == 0

    @staticmethod
    def ensure_valid_response(decision_element: ET.Element, response: str) -> Tuple[str, str]:
        """
        CRITICAL SAFETY METHOD - ensures response is valid before sending.

        This implements the C# pattern of having a final safety check that
        catches ALL bugs in earlier logic. Called AFTER any decision is made.

        If response is invalid (empty when must choose), this will force
        a valid response using random selection.

        Returns (corrected_response, reason_if_corrected)
        """
        params = DecisionSafety.parse_decision_params(decision_element)
        decision_type = params['decision_type']
        must_choose = DecisionSafety.must_choose(params)
        action_ids = params.get('action_ids', [])

        # SPECIAL CASE: ACTION_CHOICE with empty response
        # Even when noPass=false, ACTION_CHOICE often doesn't accept empty string!
        # The server expects one of the action_ids, not empty.
        # If we have actions available and response is empty, force a selection.
        if response == "" and decision_type == "ACTION_CHOICE" and action_ids:
            # Try to find a "cancel" or "done" action
            action_texts = []
            for param in decision_element.findall('.//parameter'):
                if param.get('name') == 'actionText':
                    action_texts.append(param.get('value', ''))

            cancel_keywords = ['cancel', 'done', 'pass', 'decline', 'no response', 'no further']

            # Priority 1: Actions that START with cancel/done keywords (most explicit)
            for i, text in enumerate(action_texts):
                text_lower = text.lower().strip()
                for keyword in cancel_keywords:
                    if text_lower.startswith(keyword) and i < len(action_ids):
                        reason = f"SAFETY FORCED: ACTION_CHOICE empty response -> using cancel action '{action_ids[i]}'"
                        logger.warning(f"🚨 {reason}")
                        return action_ids[i], reason

            # Priority 2: Actions with "- cancel" or "- done" suffix patterns
            for i, text in enumerate(action_texts):
                text_lower = text.lower()
                if (' - cancel' in text_lower or ' - done' in text_lower or ' - no ' in text_lower) and i < len(action_ids):
                    reason = f"SAFETY FORCED: ACTION_CHOICE empty response -> using cancel action '{action_ids[i]}'"
                    logger.warning(f"🚨 {reason}")
                    return action_ids[i], reason

            # No cancel action found - pick last action (often cancel/done)
            forced = action_ids[-1]
            reason = f"SAFETY FORCED: ACTION_CHOICE empty response -> using last action '{forced}' (no cancel found)"
            logger.warning(f"🚨 {reason}")
            return forced, reason

        # If response is empty but we MUST choose, force a selection
        if response == "" and must_choose:
            # Get available options
            action_ids = params.get('action_ids', [])
            card_ids = params.get('card_ids', [])
            selectable_cards = DecisionSafety.get_selectable_options(params)

            # Try to find SOMETHING to select
            if selectable_cards:
                forced = random.choice(selectable_cards)
                reason = f"SAFETY FORCED: Empty response but must choose (noPass={params['no_pass']}, min={params['min']}). Picked random selectable card: {forced}"
                logger.error(f"🚨 {reason}")
                return forced, reason
            elif action_ids:
                forced = random.choice(action_ids)
                reason = f"SAFETY FORCED: Empty response but must choose. Picked random action: {forced}"
                logger.error(f"🚨 {reason}")
                return forced, reason
            elif card_ids:
                forced = random.choice(card_ids)
                reason = f"SAFETY FORCED: Empty response but must choose. Picked random card: {forced}"
                logger.error(f"🚨 {reason}")
                return forced, reason
            else:
                # Absolute last resort - return "0" for MULTIPLE_CHOICE, empty otherwise
                if params['decision_type'] == 'MULTIPLE_CHOICE':
                    logger.error(f"🚨 SAFETY FORCED: No options available, returning '0' for MULTIPLE_CHOICE")
                    return "0", "SAFETY FORCED: No options, guessing '0'"
                else:
                    # This is really bad - we have nothing to choose from
                    logger.error(f"🚨 SAFETY CRITICAL: Must choose but no options available! Trying empty anyway.")
                    return "", "SAFETY CRITICAL: No options available"

        # Response is valid (either non-empty, or empty and allowed to pass)
        return response, ""

    @staticmethod
    def get_emergency_response(decision_element: ET.Element) -> SafetyDecision:
        """
        Get an emergency response for any decision.

        This is the LAST resort - called when all other handlers fail.
        It will ALWAYS return a valid response.

        CRITICAL: If noPass=true OR min>=1, we MUST return a valid choice.
        The C# code has this as a final safety check in every handler.
        """
        params = DecisionSafety.parse_decision_params(decision_element)
        decision_type = params['decision_type']
        decision_id = params['decision_id']
        decision_text = params['decision_text']
        no_pass = params['no_pass']
        min_value = params['min']
        max_value = params['max']
        action_ids = params['action_ids']
        card_ids = params['card_ids']
        selectable_options = DecisionSafety.get_selectable_options(params)
        must_choose = DecisionSafety.must_choose(params)

        logger.warning(f"🚨 EMERGENCY RESPONSE for {decision_type}: '{decision_text[:50]}...'")
        logger.warning(f"   noPass={no_pass}, min={min_value}, max={max_value}, actions={len(action_ids)}, cards={len(card_ids)}, selectable={len(selectable_options)}, must_choose={must_choose}")

        response_value = ""
        reason = ""

        # === Handle each decision type ===
        if decision_type == 'INTEGER':
            # For INTEGER, use min_value (safer than max - preserves resources)
            response_value = str(min_value)
            reason = f"Emergency: INTEGER decision, using min value {min_value}"

        elif decision_type == 'MULTIPLE_CHOICE':
            # For yes/no questions, try to be conservative
            text_lower = decision_text.lower()
            if 'concede' in text_lower or 'forfeit' in text_lower or 'surrender' in text_lower:
                response_value = "1"  # Usually "No" is option 1
                reason = "Emergency: Detected concede/forfeit, choosing No"
            else:
                response_value = "0"  # Default to first option
                reason = "Emergency: MULTIPLE_CHOICE, choosing first option"

        elif decision_type in ['CARD_ACTION_CHOICE', 'ACTION_CHOICE']:
            if action_ids:
                response_value = random.choice(action_ids)
                reason = f"Emergency: Choosing random action ({response_value})"
            elif not must_choose:
                response_value = ""
                reason = "Emergency: No actions, passing allowed"
            else:
                response_value = ""
                reason = "Emergency: No actions but must choose - will likely fail"

        elif decision_type == 'CARD_SELECTION':
            if selectable_options:
                response_value = random.choice(selectable_options)
                reason = f"Emergency: Selecting random card ({response_value})"
            elif card_ids:
                response_value = random.choice(card_ids)
                reason = f"Emergency: Selecting random card (ignoring selectable) ({response_value})"
            elif not must_choose:
                response_value = ""
                reason = "Emergency: No cards, passing allowed"
            else:
                response_value = ""
                reason = "Emergency: No cards but must choose - will likely fail"

        elif decision_type == 'ARBITRARY_CARDS':
            # ARBITRARY_CARDS - check min/max like C# does
            if min_value == 0 and max_value == 0:
                response_value = ""
                reason = "Emergency: ARBITRARY_CARDS with min=0, max=0, passing"
            elif selectable_options:
                # Select up to min_value cards (or 1 if min=0)
                num_to_select = max(1, min_value)
                selected = []
                available = selectable_options.copy()
                for _ in range(min(num_to_select, len(available))):
                    choice = random.choice(available)
                    selected.append(choice)
                    available.remove(choice)
                response_value = ",".join(selected)
                reason = f"Emergency: ARBITRARY_CARDS, selected {len(selected)} cards: {response_value}"
            elif card_ids:
                response_value = random.choice(card_ids)
                reason = f"Emergency: ARBITRARY_CARDS, no selectable but picking from cardIds: {response_value}"
            elif not must_choose:
                response_value = ""
                reason = "Emergency: No cards, passing allowed"
            else:
                response_value = ""
                reason = "Emergency: No cards but must choose - will likely fail"

        else:
            # Completely unknown decision type
            logger.error(f"🚨 UNKNOWN DECISION TYPE: {decision_type}")
            if action_ids:
                response_value = random.choice(action_ids)
                reason = f"Emergency: Unknown type, picking random action"
            elif selectable_options:
                response_value = random.choice(selectable_options)
                reason = f"Emergency: Unknown type, picking random selectable card"
            elif card_ids:
                response_value = random.choice(card_ids)
                reason = f"Emergency: Unknown type, picking random card"
            else:
                response_value = "0"  # Absolute fallback for MULTIPLE_CHOICE-like
                reason = f"Emergency: Unknown type '{decision_type}', guessing '0'"

        # === FINAL SAFETY CHECK (like C# line 766) ===
        # If we MUST choose and response is empty, force a random pick
        if must_choose and response_value == "":
            all_options = selectable_options or action_ids or card_ids
            if all_options:
                response_value = random.choice(all_options)
                reason += f" -> SAFETY OVERRIDE: forced random pick ({response_value})"
                logger.error(f"🚨 SAFETY OVERRIDE: Must choose but had empty response, forcing: {response_value}")

        logger.warning(f"   -> Response: '{response_value}' ({reason})")

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

    CRITICAL FIX: Detects MULTI-DECISION loops (e.g., A→B→A→B cycles).

    The key insight is that a loop can involve 2-4 decisions in sequence:
    - Decision A: "Choose action" → Response: "Take Vader"
    - Decision B: "Choose card" → Response: "Pass"
    - Back to Decision A (loop!)

    Strategy:
    1. Track rolling sequence of (decision_key, response) pairs
    2. Detect when a sequence repeats (length 2, 3, or 4)
    3. When looping, track which responses to BLOCK/penalize
    4. Escalating behavior: randomize → force different → concede
    """

    # Thresholds for escalating loop response
    # Lowered from 3 to 2 for faster detection - a 2-decision loop with
    # threshold 3 takes 6 decisions before detection (too slow, bot appears hung)
    LOOP_RANDOMIZE_THRESHOLD = 2   # After 2 sequence repeats: add randomness
    LOOP_FORCE_DIFFERENT = 6       # After 6 repeats: force different choice
    LOOP_CRITICAL = 12             # After 12 repeats: consider conceding

    def __init__(self, max_history: int = 100):
        self.history: List[dict] = []
        self.max_history = max_history

        # Sequence tracking for multi-decision loops
        self.sequence: List[Tuple[str, str, str]] = []  # (decision_key, response, state_hash) triples
        self.sequence_repeat_count: int = 0
        self.detected_loop_length: int = 0

        # Blocked choices: decision_key -> set of responses to avoid
        self.blocked_responses: dict = {}

        # Track current game phase for reset
        self.last_phase: str = ""

        # Track game state for distinguishing state-changing actions from loops
        self.last_state_hash: str = ""

        # Track the last CARD_ACTION_CHOICE decision so we can block it
        # when a subsequent target selection is cancelled
        self.last_action_choice_key: str = ""
        self.last_action_choice_response: str = ""

    def _decision_key(self, decision_type: str, decision_text: str) -> str:
        """Create a unique key for a decision"""
        # Use first 60 chars of text to identify the decision
        return f"{decision_type}:{decision_text[:60]}"

    def update_state(self, board_state) -> None:
        """
        Update tracked game state. Call this before recording decisions.

        If state changes (e.g., hand size increases after drawing), the sequence
        tracking resets because state-changing actions are not loops.
        """
        if board_state is None:
            return

        # Create a state hash from key game attributes
        hand_size = getattr(board_state, 'hand_size', 0)
        force_pile = getattr(board_state, 'force_pile', 0)
        reserve_deck = getattr(board_state, 'reserve_deck', 0)
        turn = getattr(board_state, 'turn_number', 0)

        # Also track cards in play - prevents false positives when playing
        # multiple cards from effects like "Anger, Fear, Aggression"
        cards_in_play = len(board_state.cards_in_play) if hasattr(board_state, 'cards_in_play') else 0

        new_hash = f"{hand_size}:{force_pile}:{reserve_deck}:{turn}:{cards_in_play}"

        if new_hash != self.last_state_hash:
            if self.last_state_hash and self.sequence_repeat_count > 0:
                # State changed during a potential loop - it's not a real loop
                logger.debug(f"State changed ({self.last_state_hash} → {new_hash}) - resetting loop detection")
                self.sequence_repeat_count = 0
                self.detected_loop_length = 0
                # Don't clear blocked_responses immediately - keep some memory
            self.last_state_hash = new_hash

    def record_decision(self, decision_type: str, decision_text: str,
                       decision_id: str, response: str) -> None:
        """Record a decision and response"""
        key = self._decision_key(decision_type, decision_text)

        entry = {
            'type': decision_type,
            'text': decision_text[:100],
            'id': decision_id,
            'response': response,
            'key': key,
        }
        self.history.append(entry)

        # Trim history
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

        # Track the last CARD_ACTION_CHOICE so we can block it if
        # the subsequent target selection is cancelled
        if decision_type == 'CARD_ACTION_CHOICE' and response != "":
            self.last_action_choice_key = key
            self.last_action_choice_response = response

        # CRITICAL: Only track NON-PASS responses for loop detection.
        # Passing (empty response) can't cause an infinite loop because:
        # 1. It doesn't change game state
        # 2. The server controls when to move on
        # 3. Multiple "Optional responses" windows during battle are normal
        # Only actual actions (non-empty responses) can cause loops.
        if response != "":
            # Include state hash in sequence to distinguish state-changing actions
            self.sequence.append((key, response, self.last_state_hash))

            # Keep sequence reasonable length (enough to detect loops of 2-4 decisions)
            if len(self.sequence) > 20:
                self.sequence = self.sequence[-20:]

            # Check for sequence repeat
            self._check_sequence_loop()
        else:
            # Pass response - clear any detected loop since we're not looping
            # (we're just declining optional actions, which is normal)
            if self.sequence_repeat_count > 0:
                logger.debug(f"Pass response - not counting for loop detection")

    def _check_sequence_loop(self) -> None:
        """Check if we're in a multi-decision loop"""
        seq = self.sequence

        # Need at least 4 entries to detect a 2-decision loop repeating
        if len(seq) < 4:
            self.sequence_repeat_count = 0
            self.detected_loop_length = 0
            return

        # Check for loops of length 2, 3, and 4
        for loop_len in [2, 3, 4]:
            if len(seq) < loop_len * 2:
                continue

            # Get the last `loop_len` entries
            recent = seq[-loop_len:]

            # Check how many times this exact sequence appears at the end
            repeat_count = 1
            pos = len(seq) - loop_len * 2

            while pos >= 0:
                # Check if seq[pos:pos+loop_len] matches recent
                segment = seq[pos:pos + loop_len]
                if segment == recent:
                    repeat_count += 1
                    pos -= loop_len
                else:
                    break

            # If we found repeats, record it
            if repeat_count >= 2:
                if repeat_count > self.sequence_repeat_count or loop_len < self.detected_loop_length:
                    self.sequence_repeat_count = repeat_count
                    self.detected_loop_length = loop_len

                    # Log the detected loop
                    if repeat_count >= self.LOOP_RANDOMIZE_THRESHOLD:
                        logger.warning(
                            f"🔄 LOOP DETECTED: {loop_len}-decision sequence repeated {repeat_count}x"
                        )
                        for i, entry in enumerate(recent):
                            k, r = entry[0], entry[1]  # Unpack (key, response, state_hash)
                            logger.warning(f"   Step {i+1}: {k[:50]} → '{r}'")

                        # Block the responses that are causing the loop
                        for entry in recent:
                            k, r = entry[0], entry[1]
                            if k not in self.blocked_responses:
                                self.blocked_responses[k] = set()
                            self.blocked_responses[k].add(r)

                return  # Found a loop, done checking

        # No loop found
        if self.sequence_repeat_count > 0:
            # We were in a loop but it's broken now
            logger.info(f"✅ Loop broken after {self.sequence_repeat_count} repeats")
            self.sequence_repeat_count = 0
            self.detected_loop_length = 0

    def check_for_loop(self, decision_type: str, decision_text: str,
                       threshold: int = 3) -> Tuple[bool, int]:
        """
        Check if we're in a potential infinite loop.

        Returns (is_loop, repeat_count) where:
        - is_loop: True if we've detected a repeating sequence
        - repeat_count: How many times the sequence has repeated
        """
        is_loop = self.sequence_repeat_count >= threshold
        return is_loop, self.sequence_repeat_count

    def get_blocked_responses(self, decision_type: str, decision_text: str) -> set:
        """
        Get responses that should be blocked/penalized for this decision.

        Called by evaluators to avoid choices that caused loops.
        Note: Empty string (pass) is never blocked since passing can't cause loops.
        """
        key = self._decision_key(decision_type, decision_text)
        blocked = self.blocked_responses.get(key, set())
        # Never block empty response (pass) - passing can't cause loops
        return blocked - {"", None}

    def should_cancel_target_selection(self, decision_type: str, decision_text: str) -> bool:
        """
        Check if we should cancel a target selection instead of selecting.

        This detects the pattern where:
        1. We selected an action
        2. We selected a target
        3. Action failed, we're back at target selection

        In this case, we should cancel instead of selecting the same target again.
        """
        if decision_type != 'ARBITRARY_CARDS':
            return False

        # Check if "cancel" is an option
        text_lower = decision_text.lower()
        if "cancel" not in text_lower and "done" not in text_lower:
            return False

        # Look at recent sequence - did we just make a target selection?
        if len(self.sequence) < 2:
            return False

        # Get last two entries
        # Format: (decision_key, response, state_hash)
        last = self.sequence[-1]
        prev = self.sequence[-2]

        last_key = last[0]
        last_response = last[1]
        prev_response = prev[1]

        # If our last response was non-empty (we selected something) and
        # we're back at a similar decision, the action likely failed
        current_key = self._decision_key(decision_type, decision_text)

        if last_response and last_response != "" and last_key == current_key:
            logger.info(f"🎯 Target selection returned after selecting '{last_response}' - action likely failed, should cancel")
            return True

        return False

    def get_loop_severity(self) -> str:
        """
        Get the severity level of the current loop.

        Returns: 'none', 'mild', 'moderate', 'severe', 'critical'
        """
        count = self.sequence_repeat_count
        if count < self.LOOP_RANDOMIZE_THRESHOLD:
            return 'none'
        elif count < self.LOOP_FORCE_DIFFERENT:
            return 'mild'  # Add randomness
        elif count < self.LOOP_CRITICAL:
            return 'severe'  # Force different choice
        else:
            return 'critical'  # Consider conceding

    def should_force_different_choice(self) -> bool:
        """Check if we should force a different choice to break loop"""
        return self.sequence_repeat_count >= self.LOOP_FORCE_DIFFERENT

    def should_consider_concede(self) -> bool:
        """Check if loop is so severe we should consider conceding"""
        return self.sequence_repeat_count >= self.LOOP_CRITICAL

    def on_phase_change(self, new_phase: str) -> None:
        """
        Called when game phase changes.
        Resets loop tracking since phase change likely breaks loops.
        """
        if new_phase != self.last_phase:
            self.last_phase = new_phase
            self.sequence_repeat_count = 0
            self.detected_loop_length = 0
            self.blocked_responses.clear()
            self.sequence.clear()
            logger.debug(f"Loop tracker reset on phase change to: {new_phase}")

    def reset_repeat_count(self, decision_type: str, decision_text: str) -> None:
        """Reset the repeat count (e.g., after successful progress)"""
        key = self._decision_key(decision_type, decision_text)
        if key in self.blocked_responses:
            del self.blocked_responses[key]

    def get_recent_decisions(self, count: int = 10) -> List[dict]:
        """Get the most recent decisions"""
        return self.history[-count:]

    def block_last_action_on_cancel(self, decision_type: str, decision_text: str) -> bool:
        """
        Block the previous CARD_ACTION_CHOICE when we cancel a target selection.

        This breaks the loop pattern:
        1. Select action (Force Lightning) → recorded as last_action_choice
        2. Cancel target selection (no valid targets)
        3. Back to action choice → action now blocked!

        Returns True if we blocked an action, False otherwise.
        """
        # Only act when cancelling target selections (CARD_SELECTION/ARBITRARY_CARDS)
        if decision_type not in ('CARD_SELECTION', 'ARBITRARY_CARDS'):
            return False

        # Check if decision text indicates a cancel option
        text_lower = decision_text.lower()
        if "cancel" not in text_lower and "done" not in text_lower:
            return False

        # Block the last action choice if we have one
        if self.last_action_choice_key and self.last_action_choice_response:
            if self.last_action_choice_key not in self.blocked_responses:
                self.blocked_responses[self.last_action_choice_key] = set()
            self.blocked_responses[self.last_action_choice_key].add(self.last_action_choice_response)
            logger.warning(
                f"🚫 Blocking action '{self.last_action_choice_response}' for "
                f"'{self.last_action_choice_key[:50]}' - target selection was cancelled"
            )
            # Clear so we don't block it again
            self.last_action_choice_key = ""
            self.last_action_choice_response = ""
            return True

        return False

    def clear(self) -> None:
        """Clear all tracking data (e.g., at game start)"""
        self.history.clear()
        self.sequence.clear()
        self.sequence_repeat_count = 0
        self.detected_loop_length = 0
        self.blocked_responses.clear()
        self.last_phase = ""
        self.last_action_choice_key = ""
        self.last_action_choice_response = ""

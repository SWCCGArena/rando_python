"""
Decision Handler for GEMP Game Events

Handles different decision types and determines the appropriate response.
Ported from C# GameCommsHelper.cs and BotAIHelper.cs ParseDecisionEvent methods.

Now uses the Brain interface for all strategic decisions.

CRITICAL DESIGN PRINCIPLE:
Every decision MUST get a response. A bad decision is better than no decision.
The game will continue with a bad decision, but hang forever with no decision.
"""

import logging
import random
from dataclasses import dataclass
from typing import Optional, Tuple, List, Set
import xml.etree.ElementTree as ET

from .decision_safety import DecisionSafety, DecisionTracker

logger = logging.getLogger(__name__)


@dataclass
class DecisionResult:
    """
    Result of processing a decision.

    Attributes:
        decision_id: The decision ID to respond to
        value: The response value to send
        no_long_delay: If True, respond quickly (1s). If False, "think" longer (3s).
                       Based on GEMP's noLongDelay parameter which indicates
                       whether a quick response is expected.
    """
    decision_id: str
    value: str
    no_long_delay: bool = False

# Global decision tracker for loop detection
_decision_tracker = DecisionTracker()


class DecisionHandler:
    """Handles game decisions and determines appropriate responses"""

    def __init__(self, brain=None):
        """
        Initialize decision handler with a brain.

        Args:
            brain: Brain instance for strategic decisions (optional)
        """
        self.brain = brain

    @staticmethod
    def handle_decision(decision_element: ET.Element, phase_count: int = 0, board_state=None, brain=None) -> DecisionResult:
        """
        Process a decision event and return the response.

        GUARANTEE: This method ALWAYS returns a valid DecisionResult.
        It will NEVER return None. If all handlers fail, emergency fallback is used.

        Args:
            decision_element: The <ge type="D"> element
            phase_count: Current phase count (for setup detection)
            board_state: Optional BoardState for strategic decisions
            brain: Optional Brain instance for AI decisions

        Returns:
            DecisionResult with decision_id, value, and no_long_delay flag
        """
        decision_type = decision_element.get('decisionType', '')
        decision_id = decision_element.get('id', '0')
        decision_text = decision_element.get('text', '')

        # === DETECT "HIT" EVENTS ===
        # When a character is hit by a weapon, the decision text starts with 'Hit'
        # e.g., "'Hit' <div class='cardHint' value='224_16'>•Han Solo (V)</div> - Optional responses"
        # We track hit cards to avoid targeting them again (wasteful)
        if board_state and decision_text.startswith("'Hit'"):
            import re
            # Extract blueprint from cardHint value
            match = re.search(r"value='([^']+)'", decision_text)
            if match:
                hit_blueprint = match.group(1)
                # Find card_id(s) matching this blueprint at battle location
                for card_id, card in board_state.cards_in_play.items():
                    if card.blueprint_id == hit_blueprint:
                        board_state.mark_card_hit(card_id)

        # Parse noLongDelay from decision parameters
        # This flag indicates if a quick response is expected
        server_no_long_delay = False
        for param in decision_element.findall('.//parameter'):
            if param.get('name') == 'noLongDelay':
                server_no_long_delay = param.get('value', '').lower() == 'true'
                break

        no_long_delay = server_no_long_delay
        delay_reason = "server" if server_no_long_delay else ""

        # Force quick delay during OUR draw phase (drawing cards should be fast)
        # Check if we're in draw phase and it's our turn
        if board_state:
            phase = board_state.current_phase or ""
            is_my_turn = board_state.is_my_turn()
            is_our_draw_phase = "draw" in phase.lower() and is_my_turn

            if is_our_draw_phase and not no_long_delay:
                no_long_delay = True
                delay_reason = "our draw phase"

            # Build turn info for logging
            turn_info = f"phase={phase}, myTurn={is_my_turn}"
        else:
            turn_info = "no board state"

        # Parse decision parameters early for logging
        params = DecisionSafety.parse_decision_params(decision_element)
        no_pass = params.get('no_pass', False)
        min_val = params.get('min', 0)
        max_val = params.get('max', 0)
        action_ids = params.get('action_ids', [])

        # Log with clear delay source and decision parameters
        delay_str = f"quick ({delay_reason})" if no_long_delay else "normal"
        param_str = f"noPass={no_pass}, min={min_val}, max={max_val}, actions={len(action_ids)}"
        if len(decision_text) > 60:
            logger.info(f"🤔 Decision: type={decision_type}, delay={delay_str}, {turn_info}, {param_str}, text='{decision_text[:60]}...'")
        else:
            logger.info(f"🤔 Decision: type={decision_type}, delay={delay_str}, {turn_info}, {param_str}, text='{decision_text}'")

        # Update state tracking for loop detection
        # If game state changed (e.g., hand size after draw), it's not a loop
        _decision_tracker.update_state(board_state)

        # Check for potential infinite loop (now detects MULTI-DECISION loops!)
        is_loop, count = _decision_tracker.check_for_loop(decision_type, decision_text)
        loop_severity = _decision_tracker.get_loop_severity()
        blocked_responses = _decision_tracker.get_blocked_responses(decision_type, decision_text)

        # === EARLY LOOP BREAK: Cancel failed target selection ===
        # For ARBITRARY_CARDS with cancel option: if we just made a selection and
        # we're back at the same decision, the action failed - cancel immediately
        if _decision_tracker.should_cancel_target_selection(decision_type, decision_text):
            logger.warning(f"🎯 Target selection failed - canceling to break potential loop")
            _decision_tracker.record_decision(decision_type, decision_text, decision_id, "")
            return DecisionResult(decision_id=decision_id, value="", no_long_delay=no_long_delay)

        # Get available options for forced choice
        params = DecisionSafety.parse_decision_params(decision_element)
        all_options = params.get('action_ids', []) or DecisionSafety.get_selectable_options(params) or params.get('card_ids', [])

        # CRITICAL FIX: Include pass option (empty string) when passing is allowed
        # This prevents loops where all actions are blocked but we could just pass
        can_pass = DecisionSafety.can_pass(params)
        if can_pass and '' not in all_options:
            all_options = list(all_options) + ['']  # Add pass as final option

        # === CRITICAL LOOP HANDLING ===
        if loop_severity == 'critical':
            logger.error(f"🚨 CRITICAL LOOP ({count} repeats) - this is a dead end!")
            # Try to find ANY different choice
            if blocked_responses and all_options:
                available = [opt for opt in all_options if opt not in blocked_responses]
                if available:
                    # Prefer pass (empty string) as the safest escape from loops
                    if '' in available:
                        forced = ''
                        logger.error(f"🚨 CRITICAL LOOP - passing to escape (blocked: {blocked_responses})")
                    else:
                        forced = available[0]
                        logger.error(f"🚨 Forcing different choice: {forced} (blocked: {blocked_responses})")
                    _decision_tracker.record_decision(decision_type, decision_text, decision_id, forced)
                    return DecisionResult(decision_id=decision_id, value=forced, no_long_delay=no_long_delay)
                else:
                    # ALL options are blocked - we're truly stuck
                    logger.error(f"🚨 ALL OPTIONS BLOCKED - signaling need to concede")
                    # Return special marker that app.py can detect
                    # For now, just pick random and hope
                    forced = random.choice(all_options) if all_options else ""
                    _decision_tracker.record_decision(decision_type, decision_text, decision_id, forced)
                    return DecisionResult(decision_id=decision_id, value=forced, no_long_delay=no_long_delay)

        # === SEVERE LOOP: Force different choice ===
        if loop_severity == 'severe' and blocked_responses and all_options:
            available = [opt for opt in all_options if opt not in blocked_responses]
            if available:
                # Check if this is a critical selection where passing would break game mechanics
                text_lower = decision_text.lower()
                is_critical_selection = (
                    "highest-ability character" in text_lower or
                    "highest ability character" in text_lower or
                    "must choose" in text_lower or
                    "you must" in text_lower
                )

                # Prefer pass (empty string) as an escape hatch from loops
                # BUT not for critical selections!
                if '' in available and not is_critical_selection:
                    forced = ''
                    logger.warning(f"⚠️  SEVERE LOOP ({count}x) - passing to break loop")
                else:
                    # For critical selections, pick a non-empty option
                    non_empty = [opt for opt in available if opt != '']
                    if non_empty:
                        forced = random.choice(non_empty)
                    else:
                        forced = random.choice(available)
                    logger.warning(f"⚠️  SEVERE LOOP ({count}x) - forcing different: {forced}")
                _decision_tracker.record_decision(decision_type, decision_text, decision_id, forced)
                return DecisionResult(decision_id=decision_id, value=forced, no_long_delay=no_long_delay)

        # === MILD LOOP: Add randomness but still try brain ===
        use_random_to_break_loop = loop_severity in ['mild', 'severe']
        if use_random_to_break_loop:
            logger.warning(f"🔄 Loop detected ({count}x) - will penalize blocked responses: {blocked_responses}")

            # CRITICAL: Some decisions must NEVER randomly pass to break loops!
            # Passing these would cancel important game mechanics.
            text_lower = decision_text.lower()
            is_critical_selection = (
                # Sense/Alter cancellation - MUST select a character
                "highest-ability character" in text_lower or
                "highest ability character" in text_lower or
                # Other critical selections that shouldn't randomly pass
                "must choose" in text_lower or
                "you must" in text_lower
            )

            # In mild loops, add 50% chance to just pass if we can - this breaks many loops
            # BUT not for critical selections where passing would break game mechanics!
            if can_pass and loop_severity == 'mild' and random.random() < 0.5 and not is_critical_selection:
                logger.warning(f"🔄 Mild loop - randomly passing to break pattern")
                _decision_tracker.record_decision(decision_type, decision_text, decision_id, "")
                return DecisionResult(decision_id=decision_id, value="", no_long_delay=no_long_delay)
            elif is_critical_selection:
                logger.info(f"🔄 Loop detected but this is a critical selection - must choose, not pass")

        result = None

        try:
            # === LAYER 1: Brain (Smart AI) ===
            # ALWAYS pass blocked_responses so evaluators can penalize them
            # This prevents loops by penalizing cancelled actions BEFORE loop detection triggers
            if brain and board_state and decision_type in ['CARD_ACTION_CHOICE', 'CARD_SELECTION', 'ARBITRARY_CARDS', 'ACTION_CHOICE', 'INTEGER']:
                result = DecisionHandler._use_brain(
                    decision_element, board_state, phase_count, brain,
                    blocked_responses=blocked_responses  # Always pass, not just when loop detected
                )
                if result:
                    # ALWAYS check if brain chose a blocked response - override it immediately!
                    # This prevents loops BEFORE they're detected (blocked = previously cancelled)
                    if blocked_responses and result[1] in blocked_responses and all_options:
                        available = [opt for opt in all_options if opt not in blocked_responses]
                        if available:
                            # Prefer pass (empty string) as escape hatch
                            if '' in available:
                                override = ''
                                logger.warning(f"🚫 Brain chose blocked response '{result[1]}', passing (action was cancelled before)")
                            else:
                                override = random.choice(available)
                                logger.warning(f"🚫 Brain chose blocked response '{result[1]}', overriding to '{override}' (action was cancelled before)")
                            result = (result[0], override)
                    logger.debug(f"Brain handled decision: {result[1]}")

            # === LAYER 2: Type-Specific Handlers ===
            if not result:
                if decision_type == 'MULTIPLE_CHOICE':
                    result = DecisionHandler._handle_multiple_choice(decision_id, decision_text)
                elif decision_type == 'CARD_SELECTION':
                    result = DecisionHandler._handle_card_selection(decision_id, decision_text, decision_element)
                elif decision_type == 'CARD_ACTION_CHOICE':
                    result = DecisionHandler._handle_card_action_choice(decision_id, decision_text, decision_element)
                elif decision_type == 'ACTION_CHOICE':
                    result = DecisionHandler._handle_card_action_choice(decision_id, decision_text, decision_element)
                elif decision_type == 'INTEGER':
                    result = DecisionHandler._handle_integer(decision_id, decision_text, decision_element, board_state)
                elif decision_type == 'ARBITRARY_CARDS':
                    result = DecisionHandler._handle_arbitrary_cards(decision_id, decision_text, decision_element, phase_count)

        except Exception as e:
            logger.error(f"❌ Exception in decision handling: {e}", exc_info=True)
            result = None

        # === LAYER 3: Emergency Fallback (GUARANTEED) ===
        if not result:
            logger.warning(f"⚠️  All handlers failed, using EMERGENCY fallback")
            safety = DecisionSafety.get_emergency_response(decision_element)
            result = (safety.decision_id, safety.value)
            logger.warning(f"🆘 Emergency response: {safety.reason}")

        # === LAYER 4: FINAL SAFETY CHECK (like C# line 766) ===
        # This catches ALL bugs in previous logic by ensuring we never return
        # an empty response when we're required to choose something.
        # This is the "never hang" guarantee.
        corrected_value, correction_reason = DecisionSafety.ensure_valid_response(
            decision_element, result[1]
        )
        if correction_reason:
            logger.error(f"🚨 SAFETY CORRECTION: {correction_reason}")
            result = (result[0], corrected_value)

        # Validate and log the response (informational only now, since we've already corrected)
        is_valid, warning = DecisionSafety.validate_response(decision_element, result[1])
        if not is_valid:
            logger.warning(f"⚠️  Response validation warning: {warning}")

        # CRITICAL: If we're cancelling a target selection (empty response),
        # block the previous action that led us here to prevent loops
        if result[1] == "" and decision_type in ('CARD_SELECTION', 'ARBITRARY_CARDS'):
            _decision_tracker.block_last_action_on_cancel(decision_type, decision_text)

        # Track this decision
        _decision_tracker.record_decision(decision_type, decision_text, decision_id, result[1])

        # Return DecisionResult with noLongDelay info for NetworkCoordinator
        return DecisionResult(
            decision_id=result[0],
            value=result[1],
            no_long_delay=no_long_delay
        )

    @staticmethod
    def reset_tracker():
        """Reset the decision tracker (call at game start)"""
        _decision_tracker.clear()

    @staticmethod
    def notify_phase_change(new_phase: str):
        """Notify tracker of phase change (resets loop detection)"""
        _decision_tracker.on_phase_change(new_phase)

    @staticmethod
    def should_concede_due_to_loop() -> bool:
        """Check if we're in a critical loop that requires conceding"""
        return _decision_tracker.should_consider_concede()

    @staticmethod
    def get_loop_status() -> Tuple[str, int]:
        """Get current loop status: (severity, repeat_count)"""
        return _decision_tracker.get_loop_severity(), _decision_tracker.sequence_repeat_count

    @staticmethod
    def _use_brain(decision_element: ET.Element, board_state, phase_count: int, brain,
                   blocked_responses: Set[str] = None) -> Optional[Tuple[str, str]]:
        """
        Use the brain to make a strategic decision.

        Args:
            decision_element: The decision XML element
            board_state: Current board state (engine BoardState)
            phase_count: Phase count for setup detection
            brain: Brain instance to query
            blocked_responses: Optional set of response values to penalize (for loop breaking)

        Returns:
            Tuple of (decision_id, decision_value) or None if brain can't handle it
        """
        from brain import BrainContext, DecisionRequest, DecisionOption, DecisionType, GameHistory, CardInfo, BoardState as BrainBoardState

        decision_type = decision_element.get('decisionType', '')
        decision_id = decision_element.get('id', '')
        decision_text = decision_element.get('text', '')

        # Parse parameters from XML
        parameters = decision_element.findall('.//parameter')
        action_ids = []
        action_texts = []
        card_ids = []
        blueprints = []
        selectable = []
        min_value = 0
        max_value = 0
        default_value = 0
        no_pass = False  # Default: can pass (missing noPass element means passing allowed)

        for param in parameters:
            name = param.get('name', '')
            value = param.get('value', '')
            if name == 'actionId':
                action_ids.append(value)
            elif name == 'actionText':
                action_texts.append(value)
            elif name == 'cardId':
                card_ids.append(value)
            elif name == 'blueprintId':
                blueprints.append(value)
            elif name == 'selectable':
                selectable.append(value.lower() == 'true')
            elif name == 'min':
                min_value = int(value)
            elif name == 'max':
                max_value = int(value)
            elif name == 'defaultValue':
                default_value = int(value)
            elif name == 'noPass':
                no_pass = value.lower() == 'true'

        # Build DecisionOptions
        options = []
        for i in range(max(len(action_ids), len(card_ids))):
            # Get option data
            option_id = action_ids[i] if i < len(action_ids) else (card_ids[i] if i < len(card_ids) else f"option_{i}")
            display_text = action_texts[i] if i < len(action_texts) else option_id
            card_id = card_ids[i] if i < len(card_ids) else None
            blueprint = blueprints[i] if i < len(blueprints) else None
            is_selectable = selectable[i] if i < len(selectable) else True

            # Create CardInfo if we have card data
            # When blueprintId is "inPlay" or similar, look up the actual blueprint
            # from board_state.cards_in_play using the cardId (like C# does)
            card_info = None
            actual_blueprint = blueprint
            if card_id and board_state:
                # Try to look up the card in cards_in_play to get real blueprint
                # Cards are tracked when they enter play/hand via PCIP events
                card_in_play = board_state.cards_in_play.get(card_id)
                if card_in_play and card_in_play.blueprint_id:
                    # Use the tracked blueprint_id instead of "inPlay"
                    if not blueprint or blueprint == "inPlay" or blueprint.startswith("temp"):
                        actual_blueprint = card_in_play.blueprint_id
                        logger.debug(f"📍 Resolved cardId {card_id} -> blueprint {actual_blueprint} (from tracked card)")
                elif blueprint == "inPlay" or not blueprint:
                    # Card not found in tracking - this shouldn't happen normally
                    logger.warning(f"⚠️  Card {card_id} not found in cards_in_play! Blueprint={blueprint}. "
                                   f"Card may not have been tracked when it entered play/hand.")

            if card_id and actual_blueprint and actual_blueprint != "inPlay":
                from engine.card_loader import get_card
                # Skip metadata lookup for special GEMP IDs:
                # -1_2 is the "face-down/hidden card" marker (e.g., top of Force Pile)
                if actual_blueprint.startswith("-1_"):
                    logger.debug(f"Skipping metadata for hidden card marker {actual_blueprint}")
                else:
                    card_meta = get_card(actual_blueprint)
                    if card_meta:
                        card_info = CardInfo(
                            card_id=card_id,
                            blueprint_id=actual_blueprint,
                            title=card_meta.title,
                            type=card_meta.card_type,
                            power=card_meta.power_value,
                            ability=card_meta.ability_value,
                            deploy_cost=card_meta.deploy_value,
                            icons=[str(icon) for icon in card_meta.icons],
                        )
                    else:
                        logger.warning(f"⚠️  Could not get metadata for blueprint {actual_blueprint} (cardId={card_id})")

            option = DecisionOption(
                option_id=option_id,
                option_type=decision_type,
                display_text=display_text,
                card=card_info,
            )
            # Only add selectable options
            if is_selectable:
                options.append(option)

        # Map decision type
        decision_type_map = {
            'MULTIPLE_CHOICE': DecisionType.MULTIPLE_CHOICE,
            'CARD_SELECTION': DecisionType.CARD_SELECTION,
            'CARD_ACTION_CHOICE': DecisionType.CARD_ACTION,
            'ACTION_CHOICE': DecisionType.CARD_ACTION,
            'INTEGER': DecisionType.INTEGER_INPUT,
            'ARBITRARY_CARDS': DecisionType.ARBITRARY_CARDS,
        }
        brain_decision_type = decision_type_map.get(decision_type, DecisionType.CARD_ACTION)

        # Build DecisionRequest
        request = DecisionRequest(
            decision_id=decision_id,
            decision_type=brain_decision_type,
            prompt=decision_text,
            options=options,
            min_value=min_value,
            max_value=max_value,
            default_value=default_value,
            no_pass=no_pass,
        )

        # Build BrainBoardState (minimal - StaticBrain uses engine BoardState directly)
        brain_board_state = BrainBoardState(
            turn_number=phase_count,
            phase=board_state.current_phase if board_state else "",
            current_player="me" if (board_state and board_state.is_my_turn()) else "opponent",
            my_total_power=board_state.total_my_power() if board_state else 0,
            their_total_power=board_state.total_their_power() if board_state else 0,
            my_side=board_state.my_side if board_state else "unknown",
            opponent_name=board_state.opponent_name if board_state else "Unknown",
        )

        # Build BrainContext
        context = BrainContext(
            board_state=board_state,  # Pass engine BoardState directly (StaticBrain uses it)
            decision_request=request,
            game_history=GameHistory(),  # TODO: Track history
        )

        # =====================================================
        # CRITICAL: For ARBITRARY_CARDS with min > 1, bypass brain!
        # Brain only returns single choice, but multi-select needs
        # multiple cards comma-separated. Fall through to handler.
        # =====================================================
        if decision_type == 'ARBITRARY_CARDS' and min_value > 1:
            logger.info(f"🔄 ARBITRARY_CARDS with min={min_value} - bypassing brain for multi-select")
            return None

        # Ask brain for decision
        logger.debug("🧠 Using brain for decision...")
        try:
            decision = brain.make_decision(context)

            if decision and decision.choice is not None:
                logger.info(f"🧠 Brain chose: {decision.choice} | {decision.reasoning}")
                return (decision_id, decision.choice)
            else:
                logger.warning("⚠️  Brain returned no decision")
                return None

        except Exception as e:
            logger.error(f"❌ Brain error: {e}", exc_info=True)
            return None

    @staticmethod
    def _handle_multiple_choice(decision_id: str, text: str) -> Tuple[str, str]:
        """
        Handle MULTIPLE_CHOICE decision type.

        Args:
            decision_id: The decision ID
            text: The decision text

        Returns:
            Tuple of (decision_id, decision_value)
        """
        # Based on C# GameCommsHelper.cs ParseDecisionEvent()

        if text == "Select OK to start game":
            logger.info("✅ Decision: Starting game (selecting OK)")
            return (decision_id, "0")

        elif "do you want to deploy" in text.lower():
            logger.info("✅ Decision: Required deploy action (selecting Yes)")
            return (decision_id, "0")

        elif "Both players have chosen the same starting location" in text:
            logger.info("✅ Decision: Same starting location (letting opponent have it)")
            return (decision_id, "1")

        elif "Do you want to allow game to be reverted" in text:
            logger.info("✅ Decision: Allowing revert")
            return (decision_id, "0")

        elif "Do you want to draw another sabacc card?" in text:
            # TODO: Check sabacc hand size when we track game state
            logger.info("✅ Decision: Sabacc card (drawing one)")
            return (decision_id, "0")

        elif "do you want to draw" in text.lower() and "battle destiny" in text.lower():
            # Drawing battle destiny adds to our power - almost always good
            logger.info("✅ Decision: Drawing battle destiny (Yes)")
            return (decision_id, "0")

        else:
            # Default: pick first option
            logger.info(f"⚠️  Unknown multiple choice decision, defaulting to first option")
            return (decision_id, "0")

    @staticmethod
    def _handle_arbitrary_cards(decision_id: str, text: str, decision_element: ET.Element, phase_count: int) -> Optional[Tuple[str, str]]:
        """
        Handle ARBITRARY_CARDS decision type (e.g., choose starting location, take card into hand).

        Ported from C# BotAIHelper.ParseArbritraryCardDecision()

        Key logic:
        1. Build realChoices = cards where selectable=true AND preselected=false
        2. If min=0 AND max=0 → return "" (pass, nothing to select)
        3. If min=0 → optional selection, pick one random card
        4. If min>0 → must select min cards

        Args:
            decision_id: The decision ID
            text: The decision text
            decision_element: The full decision element
            phase_count: Current phase count

        Returns:
            Tuple of (decision_id, decision_value) or None if can't decide
        """
        import random

        # Parse parameters
        parameters = decision_element.findall('.//parameter')
        blueprints = []
        card_ids = []
        selectable = []
        preselected = []
        min_val = 0
        max_val = 0
        return_any_change = False

        for param in parameters:
            name = param.get('name', '')
            value = param.get('value', '')
            if name == 'blueprintId':
                blueprints.append(value)
            elif name == 'cardId':
                card_ids.append(value)
            elif name == 'selectable':
                selectable.append(value.lower() == 'true')
            elif name == 'preselected':
                preselected.append(value.lower() == 'true')
            elif name == 'min':
                min_val = int(value)
            elif name == 'max':
                max_val = int(value)
            elif name == 'returnAnyChange':
                return_any_change = value.lower() == 'true'

        is_setup = "Choose starting location" in text or phase_count <= 1

        logger.info(f"ARBITRARY_CARDS: min={min_val}, max={max_val}, cardIds={len(card_ids)}, text='{text}'")

        # Build realChoices (selectable AND NOT preselected) and preChoices (preselected)
        real_choices = []
        pre_choices = []
        forced_choice = None

        for i in range(len(card_ids)):
            can_choose = selectable[i] if i < len(selectable) else True
            is_preselected = preselected[i] if i < len(preselected) else False
            card_id = card_ids[i]
            blueprint = blueprints[i] if i < len(blueprints) else None

            if can_choose and not is_preselected:
                real_choices.append(card_id)

                # Check for forced choices during setup
                if is_setup and blueprint:
                    # Main Power Generators
                    if "13_32" in blueprint or "Main Power Generators" in blueprint:
                        forced_choice = card_id
                        logger.info(f"✅ Forcing Main Power Generators as starting location")

            if is_preselected:
                pre_choices.append(card_id)

        # If we have a forced choice, use it
        if forced_choice:
            return (decision_id, forced_choice)

        # === CRITICAL: Handle min/max logic like C# ===
        if len(real_choices) > 0:
            # Case 1: min=0 AND max=0 → pass (nothing to select)
            if min_val == 0 and max_val == 0:
                logger.info(f"⏭️  ARBITRARY_CARDS: min=0, max=0 → passing (empty string)")
                return (decision_id, "")

            # Case 2: min=0 OR returnAnyChange → optional, pick one random card
            elif min_val == 0 or return_any_change:
                choice = random.choice(real_choices)
                # Include preselected cards in result
                result = choice
                for pre in pre_choices:
                    result += "," + pre
                logger.info(f"✅ ARBITRARY_CARDS: optional selection, picked {choice}")
                return (decision_id, result)

            # Case 3: min>0 → must select min number of cards
            else:
                selected = []
                available = real_choices.copy()
                for _ in range(min(min_val, len(available))):
                    choice = random.choice(available)
                    selected.append(choice)
                    available.remove(choice)
                result = ",".join(selected)
                logger.info(f"✅ ARBITRARY_CARDS: required selection of {min_val}, picked {selected}")
                return (decision_id, result)
        else:
            # No real choices available - send empty string
            logger.warning(f"⚠️  No selectable cards for '{text}' - sending empty string")
            return (decision_id, "")

    @staticmethod
    def _handle_card_selection(decision_id: str, text: str, decision_element: ET.Element) -> Optional[Tuple[str, str]]:
        """
        Handle CARD_SELECTION decision type.

        Args:
            decision_id: The decision ID
            text: The decision text
            decision_element: The full decision element

        Returns:
            Tuple of (decision_id, decision_value) or None if can't decide
        """
        # Parse parameters to find selectable cards
        parameters = decision_element.findall('.//parameter')
        card_ids = []

        for param in parameters:
            name = param.get('name', '')
            value = param.get('value', '')
            if name == 'cardId':
                card_ids.append(value)

        # For now, pick first card
        if card_ids:
            logger.info(f"✅ Selecting first card: {card_ids[0]}")
            return (decision_id, card_ids[0])
        else:
            logger.warning(f"No cards to select, selecting empty")
            return (decision_id, "")

    @staticmethod
    def _handle_card_action_choice(decision_id: str, text: str, decision_element: ET.Element) -> Optional[Tuple[str, str]]:
        """
        Handle CARD_ACTION_CHOICE and ACTION_CHOICE decision types.

        Args:
            decision_id: The decision ID
            text: The decision text
            decision_element: The full decision element

        Returns:
            Tuple of (decision_id, decision_value) or None if can't decide
        """
        # Parse action choices and parameters
        parameters = decision_element.findall('.//parameter')
        action_ids = []
        action_texts = []
        no_pass = False  # Default: can pass (missing noPass element means passing allowed)

        for param in parameters:
            name = param.get('name', '')
            value = param.get('value', '')
            if name == 'actionId':
                action_ids.append(value)
            elif name == 'actionText':
                action_texts.append(value)
            elif name == 'noPass':
                no_pass = value.lower() == 'true'

        # Log what we found for debugging
        logger.info(f"Found {len(action_ids)} actions: {action_ids} (noPass={no_pass})")
        if action_texts:
            logger.info(f"Action texts: {action_texts}")

        # If no actions available, must pass
        if not action_ids:
            logger.warning(f"No actions available for '{text}', sending empty string to pass")
            logger.warning(f"Decision element XML: {ET.tostring(decision_element, encoding='unicode')[:300]}")
            return (decision_id, "")

        # Decide whether to take an action or pass
        # TODO: Implement ranking logic from C# to choose best action
        # For now, use simple heuristics:

        # Check if we want to pass based on action type
        should_pass = False

        # Don't deploy from Reserve Deck if it's too risky (can lead to infinite loops)
        # This is a temporary fix - proper solution is to implement ranking
        if any("Reserve Deck" in text for text in action_texts):
            # Only take this action if it's the ONLY option
            if len(action_ids) > 1:
                logger.info("🤔 Multiple actions including Reserve Deck deploy - checking alternatives...")
                # Try to find a non-Reserve-Deck action
                for i, action_text in enumerate(action_texts):
                    if "Reserve Deck" not in action_text and i < len(action_ids):
                        logger.info(f"✅ Selecting safer action: {action_ids[i]} ({action_text})")
                        return (decision_id, action_ids[i])

        # If noPass=false (can pass) and we want to pass, send empty string
        if should_pass and not no_pass:
            logger.info(f"⏭️  Choosing to pass (noPass={no_pass})")
            return (decision_id, "")

        # Otherwise, select first action (or forced action if noPass=true)
        selected_action = action_ids[0]
        selected_text = action_texts[0] if action_texts else "unknown"
        logger.info(f"✅ Selecting action: {selected_action} ({selected_text})")
        return (decision_id, selected_action)

    @staticmethod
    def _handle_integer(decision_id: str, text: str, decision_element: ET.Element, board_state=None) -> Optional[Tuple[str, str]]:
        """
        Handle INTEGER decision type (fallback when brain doesn't handle it).

        Strategic logic is now in ForceActivationEvaluator in the brain.
        This is just a simple fallback that returns max value.

        Args:
            decision_id: The decision ID
            text: The decision text
            decision_element: The full decision element
            board_state: Optional BoardState (unused in fallback)

        Returns:
            Tuple of (decision_id, decision_value)
        """
        # Parse min/max from parameters
        parameters = decision_element.findall('.//parameter')
        max_val = 0

        for param in parameters:
            name = param.get('name', '')
            value = param.get('value', '')
            if name == 'max':
                max_val = int(value)

        # Simple fallback: use maximum value
        logger.info(f"✅ INTEGER fallback: selecting max value {max_val}")
        return (decision_id, str(max_val))

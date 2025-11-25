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
from typing import Optional, Tuple, List
import xml.etree.ElementTree as ET

from .decision_safety import DecisionSafety, DecisionTracker

logger = logging.getLogger(__name__)

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
    def handle_decision(decision_element: ET.Element, phase_count: int = 0, board_state=None, brain=None) -> Tuple[str, str]:
        """
        Process a decision event and return the response.

        GUARANTEE: This method ALWAYS returns a valid (decision_id, value) tuple.
        It will NEVER return None. If all handlers fail, emergency fallback is used.

        Args:
            decision_element: The <ge type="D"> element
            phase_count: Current phase count (for setup detection)
            board_state: Optional BoardState for strategic decisions
            brain: Optional Brain instance for AI decisions

        Returns:
            Tuple of (decision_id, decision_value) - ALWAYS returns a value
        """
        decision_type = decision_element.get('decisionType', '')
        decision_id = decision_element.get('id', '0')
        decision_text = decision_element.get('text', '')

        logger.info(f"🤔 Processing decision: type={decision_type}, text='{decision_text}'")

        # Check for potential infinite loop (uses CONSECUTIVE count, not total)
        is_loop, count = _decision_tracker.check_for_loop(decision_type, decision_text)
        if is_loop:
            # Use WARNING for severe loops (50+ consecutive), DEBUG for minor ones
            if count >= 50:
                logger.warning(f"⚠️  SEVERE loop detected: '{decision_text[:50]}' repeated {count} consecutive times")
            else:
                logger.debug(f"Loop detected: '{decision_text[:50]}' repeated {count} consecutive times")
            # Don't abort - still try to respond, but maybe differently

        result = None

        try:
            # === LAYER 1: Brain (Smart AI) ===
            if brain and board_state and decision_type in ['CARD_ACTION_CHOICE', 'CARD_SELECTION', 'ARBITRARY_CARDS', 'ACTION_CHOICE', 'INTEGER']:
                result = DecisionHandler._use_brain(decision_element, board_state, phase_count, brain)
                if result:
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

        # Validate and log the response
        is_valid, warning = DecisionSafety.validate_response(decision_element, result[1])
        if not is_valid:
            logger.warning(f"⚠️  Response validation warning: {warning}")

        # Track this decision
        _decision_tracker.record_decision(decision_type, decision_text, decision_id, result[1])

        return result

    @staticmethod
    def reset_tracker():
        """Reset the decision tracker (call at game start)"""
        _decision_tracker.clear()

    @staticmethod
    def _use_brain(decision_element: ET.Element, board_state, phase_count: int, brain) -> Optional[Tuple[str, str]]:
        """
        Use the brain to make a strategic decision.

        Args:
            decision_element: The decision XML element
            board_state: Current board state (engine BoardState)
            phase_count: Phase count for setup detection
            brain: Brain instance to query

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
            card_info = None
            if card_id and blueprint:
                from engine.card_loader import get_card
                card_meta = get_card(blueprint)
                if card_meta:
                    card_info = CardInfo(
                        card_id=card_id,
                        blueprint_id=blueprint,
                        title=card_meta.title,
                        type=card_meta.card_type,
                        power=card_meta.power_value,
                        ability=card_meta.ability_value,
                        deploy_cost=card_meta.deploy_value,
                        icons=[str(icon) for icon in card_meta.icons],
                    )

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

        else:
            # Default: pick first option
            logger.info(f"⚠️  Unknown multiple choice decision, defaulting to first option")
            return (decision_id, "0")

    @staticmethod
    def _handle_arbitrary_cards(decision_id: str, text: str, decision_element: ET.Element, phase_count: int) -> Optional[Tuple[str, str]]:
        """
        Handle ARBITRARY_CARDS decision type (e.g., choose starting location).

        Args:
            decision_id: The decision ID
            text: The decision text
            decision_element: The full decision element
            phase_count: Current phase count

        Returns:
            Tuple of (decision_id, decision_value) or None if can't decide
        """
        # Parse parameters
        parameters = decision_element.findall('.//parameter')
        blueprints = []
        card_ids = []
        selectable = []

        for param in parameters:
            name = param.get('name', '')
            value = param.get('value', '')
            if name == 'blueprintId':
                blueprints.append(value)
            elif name == 'cardId':
                card_ids.append(value)
            elif name == 'selectable':
                selectable.append(value.lower() == 'true')

        is_setup = "Choose starting location" in text or phase_count <= 1

        # Build list of selectable cards
        choices = []
        for i in range(len(card_ids)):
            if i < len(selectable) and selectable[i]:
                choices.append(card_ids[i])
                if i < len(blueprints):
                    # Check for forced choices in starting location
                    if is_setup and "13_32" in blueprints[i]:  # Main Power Generators
                        logger.info(f"✅ Forcing Main Power Generators as starting location")
                        return (decision_id, card_ids[i])

        # Default: pick first selectable card
        if choices:
            logger.info(f"✅ Selecting first available option (card {choices[0]}) from {len(choices)} choices")
            return (decision_id, choices[0])
        else:
            # No selectable cards - send empty string to cancel/pass
            logger.warning(f"⚠️  No selectable cards found for '{text}' - sending empty string to cancel/pass")
            logger.info(f"Total cardIds: {len(card_ids)}, Selectable: {selectable.count(True) if selectable else 0}")
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
        no_pass = True  # Default: cannot pass (must select an action)

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

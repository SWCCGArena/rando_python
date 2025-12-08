"""
Base Classes for Evaluator System

Defines the core architecture for evaluating actions and making decisions.
Ported from Unity C# BotAIHelper.cs ranking system.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Set
from enum import Enum
import logging
import random

logger = logging.getLogger(__name__)


class ActionType(Enum):
    """Types of actions the bot can take"""
    # Core actions
    DEPLOY = "deploy"
    PASS = "pass"
    ACTIVATE = "activate_force"
    ACTIVATE_FORCE = "activate_force"  # Alias
    BATTLE = "battle"
    MOVE = "move"
    DRAW = "draw"
    DRAW_DESTINY = "draw_destiny"
    SELECT_CARD = "select_card"
    ARBITRARY = "arbitrary"
    PLAY_CARD = "play_card"  # Generic "play a card" action

    # Combat related
    FIRE_WEAPON = "fire_weapon"
    BATTLE_DESTINY = "battle_destiny"
    SUBSTITUTE_DESTINY = "substitute_destiny"
    CANCEL_DAMAGE = "cancel_damage"

    # Special actions
    FORCE_DRAIN = "force_drain"
    RACE_DESTINY = "race_destiny"
    REACT = "react"
    STEAL = "steal"
    SABACC = "sabacc"
    CANCEL = "cancel"
    EMBARK = "embark"

    # Unknown/fallback
    UNKNOWN = "unknown"


@dataclass
class DecisionContext:
    """
    Context information for evaluating a decision.

    Contains all information an evaluator needs to score actions:
    - Current game state (board, resources, power)
    - Available actions
    - Decision type and text
    - Phase information
    """
    # Board state
    board_state: Any  # BoardState object (avoid circular import)

    # Decision info
    decision_type: str  # CARD_ACTION_CHOICE, CARD_SELECTION, etc.
    decision_text: str  # Human-readable prompt
    decision_id: str

    # Phase info
    phase: str  # DEPLOY, BATTLE, MOVE, DRAW, CONTROL
    turn_number: int
    is_my_turn: bool

    # Available actions (raw from XML)
    action_ids: List[str] = field(default_factory=list)
    action_texts: List[str] = field(default_factory=list)

    # Parameters
    no_pass: bool = True  # Can we pass/cancel?
    card_ids: List[str] = field(default_factory=list)  # For CARD_SELECTION
    blueprints: List[str] = field(default_factory=list)  # For ARBITRARY_CARDS
    selectable: List[bool] = field(default_factory=list)  # Which cards are selectable

    # Additional context
    extra: Dict[str, Any] = field(default_factory=dict)

    # Blocked responses (for loop prevention)
    # Actions in this set should be heavily penalized to avoid re-selection
    blocked_responses: Set[str] = field(default_factory=set)


@dataclass
class EvaluatedAction:
    """
    An action that has been scored by evaluators.

    Represents a possible decision with:
    - The action to take (action_id or card_id)
    - Score (higher = better)
    - Reasoning (for debugging/logging)
    """
    action_id: str  # The ID to send to server
    action_type: ActionType
    score: float  # Higher = better
    reasoning: List[str] = field(default_factory=list)  # Why this score?

    # Optional metadata
    display_text: str = ""  # Human-readable action
    card_name: str = ""  # If deploying/selecting a card
    blueprint_id: str = ""  # Card blueprint ID (for deploy tracking)
    deploy_cost: int = 0
    expected_value: float = 0.0  # Expected strategic value

    def add_reasoning(self, reason: str, score_delta: float = 0.0):
        """Add reasoning with optional score adjustment"""
        if score_delta != 0:
            self.reasoning.append(f"{reason} ({score_delta:+.1f})")
            self.score += score_delta
        else:
            self.reasoning.append(reason)

    def __repr__(self):
        return f"EvaluatedAction(id={self.action_id}, score={self.score:.1f}, {self.display_text})"


class ActionEvaluator(ABC):
    """
    Base class for action evaluators.

    Each evaluator implements logic for scoring actions in a specific context
    (e.g., deploy phase, battle decisions, card selection).

    Evaluators are composable - multiple can score the same action and
    their scores are combined.
    """

    def __init__(self, name: str):
        self.name = name
        self.enabled = True
        self.logger = logging.getLogger(f"{__name__}.{name}")

    @abstractmethod
    def can_evaluate(self, context: DecisionContext) -> bool:
        """
        Check if this evaluator applies to the given context.

        Returns:
            True if this evaluator should score actions for this decision
        """
        pass

    @abstractmethod
    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """
        Evaluate all possible actions and return scored list.

        Args:
            context: Decision context with game state and available actions

        Returns:
            List of evaluated actions with scores and reasoning
        """
        pass

    def log_evaluation(self, action: EvaluatedAction):
        """Log evaluation for debugging"""
        reasons = " | ".join(action.reasoning)
        self.logger.debug(f"  [{self.name}] {action.display_text}: {action.score:.1f} - {reasons}")


class PassEvaluator(ActionEvaluator):
    """
    Simple evaluator that creates a PASS action.

    Used when we want to pass/cancel instead of taking an action.
    Score is typically low (0-10) unless we really want to pass.

    IMPORTANT: For ACTION_CHOICE decisions, empty string may not be valid!
    We need to find a "Cancel" or "Done" action from available options instead.
    """

    def __init__(self):
        super().__init__("Pass")

    def can_evaluate(self, context: DecisionContext) -> bool:
        # Can only pass if:
        # 1. noPass=false (passing is allowed)
        # 2. AND min=0 (no minimum selection required)
        # 3. For "Required responses", only pass if there's an explicit cancel action
        # This matches C# logic in ParseArbritraryCardDecision
        min_required = context.extra.get('min', 0)

        # Basic requirement: noPass must be false and no minimum selection
        if context.no_pass or min_required > 0:
            return False

        # Check for "Required responses" in decision text - this semantically means
        # we MUST respond, unless there's an explicit cancel/done action available
        if 'required' in context.decision_text.lower():
            # Only allow "passing" if there's a cancel action we can select
            cancel_id = self._find_cancel_action(context)
            if cancel_id is None:
                return False  # No cancel option = can't pass on required responses

        return True

    def _find_cancel_action(self, context: DecisionContext) -> Optional[str]:
        """
        Find a "Cancel" or "Done" action from available actions.

        For ACTION_CHOICE decisions, we can't use empty string to pass.
        Instead, we need to find and select a cancel/done action.

        Returns the action_id of the cancel action, or None if not found.
        """
        # Priority 1: Actions that START with cancel/done keywords (most explicit)
        priority_1_keywords = ['cancel', 'done', 'pass', 'decline', 'no response', 'no further']

        for i, action_text in enumerate(context.action_texts):
            text_lower = action_text.lower().strip()
            for keyword in priority_1_keywords:
                if text_lower.startswith(keyword):
                    if i < len(context.action_ids):
                        return context.action_ids[i]

        # Priority 2: Actions that contain "- cancel" or "- done" patterns (suffix style)
        for i, action_text in enumerate(context.action_texts):
            text_lower = action_text.lower()
            if ' - cancel' in text_lower or ' - done' in text_lower or ' - no ' in text_lower:
                if i < len(context.action_ids):
                    return context.action_ids[i]

        # Priority 3: Actions that are ONLY the keyword (exact match or near-exact)
        for i, action_text in enumerate(context.action_texts):
            text_lower = action_text.lower().strip()
            # Check for near-exact matches like "Cancel" or "Done" or "Cancel retrieval"
            for keyword in priority_1_keywords:
                # Match "Cancel X" but not "X Cancel Y" or "X or cancel Y"
                if text_lower.startswith(keyword) and ' or ' not in text_lower:
                    if i < len(context.action_ids):
                        return context.action_ids[i]

        return None

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Create a PASS action with low default score"""

        # For ACTION_CHOICE, we may need to use a "Cancel" action instead of empty string
        # Empty string doesn't work for all decision types
        pass_action_id = ""
        pass_display = "Pass / Do nothing"

        # Check if this is an ACTION_CHOICE decision with available actions
        # These often require selecting a "Cancel" action rather than empty string
        if context.decision_type == "ACTION_CHOICE" and context.action_texts:
            cancel_id = self._find_cancel_action(context)
            if cancel_id is not None:
                pass_action_id = cancel_id
                # Find the display text for this action
                for i, aid in enumerate(context.action_ids):
                    if aid == cancel_id and i < len(context.action_texts):
                        pass_display = f"Cancel: {context.action_texts[i]}"
                        break
                self.logger.debug(f"ACTION_CHOICE: Using cancel action '{cancel_id}' instead of empty string")

        action = EvaluatedAction(
            action_id=pass_action_id,
            action_type=ActionType.PASS,
            score=5.0,  # Low default score
            display_text=pass_display
        )

        action.add_reasoning("Default pass option")

        # Increase pass score if we're low on resources
        if context.board_state:
            bs = context.board_state

            # CRITICAL: Don't apply "save force" logic during certain decisions!
            # - ACTIVATE: Costs nothing, just moves cards from reserve to force pile
            # - DRAW: Drawing IS what we want to do
            # - BATTLE: We just deployed, we WANT to battle! Don't discourage it.
            # - FOLLOW-THROUGH: "Choose where to move/deploy" - we already committed!
            decision_text_lower = (context.decision_text or "").lower()
            phase_lower = (context.phase or "").lower()
            is_activate_decision = "activate" in decision_text_lower
            is_draw_decision = "draw" in decision_text_lower and "action" in decision_text_lower
            # Detect battle-related decisions:
            # - "Initiate battle" = explicit battle initiation
            # - "Battle action" during Battle phase = also battle initiation (GEMP phrasing varies)
            is_initiate_battle_decision = "initiate battle" in decision_text_lower
            is_battle_phase_action = "battle" in phase_lower and "battle action" in decision_text_lower

            # Follow-through decisions: We already decided to move/deploy, now just picking target
            # Cancelling here wastes the decision we already made - don't encourage it!
            is_followthrough_decision = (
                "choose where to move" in decision_text_lower or
                "choose where to deploy" in decision_text_lower
            )

            # For battle initiation decisions, pass should have VERY low score
            # We deployed for a reason - we want to fight!
            # This includes both "Initiate battle" and "Choose Battle action" during Battle phase
            if is_initiate_battle_decision or is_battle_phase_action:
                action.add_reasoning("Battle phase - should fight, not pass", -10.0)
                return [action]  # Don't add any other bonuses for pass during battle

            # For follow-through decisions, pass should also have low score
            # We already decided to move/deploy, cancelling now wastes that decision
            if is_followthrough_decision:
                action.add_reasoning("Already committed to action - follow through", -15.0)
                return [action]  # Don't add resource-based bonuses

            if bs.force_pile < 3 and not is_activate_decision and not is_draw_decision:
                action.add_reasoning("Low on Force - prefer to pass", +5.0)

            if bs.reserve_deck_low():
                action.add_reasoning("Reserve deck low - conserve cards", +3.0)

            # Hand management: If hand is small, save force for drawing
            # BUT NOT DURING ACTIVATE or DRAW - we're already drawing!
            hand_size = bs.hand_size if bs.hand_size > 0 else len(bs.cards_in_hand)
            if not is_activate_decision and not is_draw_decision:
                if hand_size < 5:
                    # Small hand - strongly prefer passing to save force for draw phase
                    action.add_reasoning(f"Small hand ({hand_size}) - save force for drawing", +15.0)
                elif hand_size < 7:
                    # Below target hand size - moderately prefer passing
                    action.add_reasoning(f"Hand below target ({hand_size}/7) - conserve force", +8.0)

            # During Move phase, be more conservative to save force for drawing
            phase_lower = (context.phase or "").lower()
            if "move" in phase_lower and bs.force_pile <= 4 and hand_size < 7:
                action.add_reasoning("Move phase + low force + small hand - pass to draw", +10.0)

        return [action]


class CombinedEvaluator:
    """
    Combines multiple evaluators to make a final decision.

    Each applicable evaluator scores the actions, then we pick the best.
    """

    def __init__(self, evaluators: List[ActionEvaluator]):
        self.evaluators = evaluators
        self.logger = logging.getLogger(__name__)

    def track_action(self, action: EvaluatedAction, card_id: str = None, decision_text: str = None):
        """
        Track that an action was chosen.

        This allows evaluators to avoid selecting the same action again
        if the game presents the same decision (avoid loops).

        Args:
            action: The action that was chosen
            card_id: Optional card ID associated with the action
            decision_text: Optional decision text (for detecting deploy target selection)
        """
        # Track deploys
        if action.action_type == ActionType.DEPLOY and card_id:
            for evaluator in self.evaluators:
                if hasattr(evaluator, 'track_deploy'):
                    evaluator.track_deploy(card_id)
                    self.logger.debug(f"📝 Tracked deploy of card {card_id}")

                # NOTE: We do NOT call record_deployment() here because:
                # CARD_ACTION_CHOICE ("Deploy Yularen") is followed by
                # CARD_SELECTION ("Choose where to deploy Yularen").
                # The instruction must remain in the plan so CARD_SELECTION
                # knows the planned target location.
                # record_deployment is called below when target is selected.

        # Track deployment target selection (CARD_SELECTION for "Choose where to deploy")
        # This is when the deployment is actually committed
        if action.action_type == ActionType.SELECT_CARD and decision_text:
            if "choose where to deploy" in decision_text.lower():
                # Extract blueprint_id from decision text: value='212_6'>•Allegiant General Pryde
                import re
                match = re.search(r"value='([^']+)'", decision_text)
                if match:
                    blueprint_id = match.group(1)
                    for evaluator in self.evaluators:
                        if hasattr(evaluator, 'planner') and hasattr(evaluator.planner, 'record_deployment'):
                            evaluator.planner.record_deployment(blueprint_id)
                            self.logger.info(f"📝 Recorded deployment of {blueprint_id} (target selected)")
                            break

        # Track moves
        if action.action_type == ActionType.MOVE and card_id:
            for evaluator in self.evaluators:
                if hasattr(evaluator, 'track_move'):
                    evaluator.track_move(card_id)
                    self.logger.debug(f"📝 Tracked move of card {card_id}")

    def evaluate_decision(self, context: DecisionContext) -> Optional[EvaluatedAction]:
        """
        Run all applicable evaluators and return the best action.

        Args:
            context: Decision context

        Returns:
            Best evaluated action, or None if no evaluators apply
        """
        all_actions = []

        for evaluator in self.evaluators:
            if not evaluator.enabled:
                continue

            if evaluator.can_evaluate(context):
                self.logger.debug(f"🔍 Running evaluator: {evaluator.name}")
                actions = evaluator.evaluate(context)
                all_actions.extend(actions)

                # Log all actions from this evaluator
                for action in actions:
                    evaluator.log_evaluation(action)

        if not all_actions:
            self.logger.warning("⚠️  No evaluators produced actions!")
            return None

        # Pick the best action
        best_action = max(all_actions, key=lambda a: a.score)

        # =====================================================
        # If ALL actions are terrible (score < -100), consider passing
        # This prevents the bot from always taking bad actions.
        # 50% of the time we'll pass, 50% we'll take the least-bad action.
        #
        # BUT: We can only pass if:
        # - no_pass is False (passing is allowed)
        # - min is 0 or not set (no minimum selection required)
        # =====================================================
        BAD_ACTION_THRESHOLD = -100.0
        if best_action.score < BAD_ACTION_THRESHOLD:
            # Check if we're allowed to pass
            min_required = context.extra.get('min', 0)
            can_pass = not context.no_pass and min_required == 0

            if can_pass and random.random() < 0.5:
                self.logger.info(f"🛑 All actions bad (best: {best_action.score:.1f}), choosing to PASS")
                # Return a Pass action instead of the bad action
                pass_action = EvaluatedAction(
                    action_id="",  # Empty = pass
                    action_type=ActionType.PASS,
                    score=0.0,
                    display_text="Pass (all actions were bad)"
                )
                pass_action.add_reasoning(f"Best action was {best_action.score:.1f}, deciding to pass instead")
                return pass_action
            elif can_pass:
                self.logger.info(f"⚠️  All actions bad (best: {best_action.score:.1f}), but taking least-bad action anyway")
            else:
                # Must take an action (noPass=true or min >= 1)
                self.logger.info(f"⚠️  All actions bad (best: {best_action.score:.1f}), but MUST choose (noPass={context.no_pass}, min={min_required})")

        self.logger.info(f"✅ Best action: {best_action.display_text} (score: {best_action.score:.1f})")
        self.logger.info(f"   Reasoning: {' | '.join(best_action.reasoning)}")

        return best_action

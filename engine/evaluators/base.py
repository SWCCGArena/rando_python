"""
Base Classes for Evaluator System

Defines the core architecture for evaluating actions and making decisions.
Ported from Unity C# BotAIHelper.cs ranking system.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
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
    """

    def __init__(self):
        super().__init__("Pass")

    def can_evaluate(self, context: DecisionContext) -> bool:
        # Can only pass if:
        # 1. noPass=false (passing is allowed)
        # 2. AND min=0 (no minimum selection required)
        # This matches C# logic in ParseArbritraryCardDecision
        min_required = context.extra.get('min', 0)
        return not context.no_pass and min_required == 0

    def evaluate(self, context: DecisionContext) -> List[EvaluatedAction]:
        """Create a PASS action with low default score"""
        action = EvaluatedAction(
            action_id="",  # Empty string = pass
            action_type=ActionType.PASS,
            score=5.0,  # Low default score
            display_text="Pass / Do nothing"
        )

        action.add_reasoning("Default pass option")

        # Increase pass score if we're low on resources
        if context.board_state:
            bs = context.board_state

            # CRITICAL: Don't apply "save force" logic during certain decisions!
            # - ACTIVATE: Costs nothing, just moves cards from reserve to force pile
            # - DRAW: Drawing IS what we want to do
            # - BATTLE: We just deployed, we WANT to battle! Don't discourage it.
            decision_text_lower = (context.decision_text or "").lower()
            is_activate_decision = "activate" in decision_text_lower
            is_draw_decision = "draw" in decision_text_lower and "action" in decision_text_lower
            is_battle_decision = "battle action" in decision_text_lower or "initiate battle" in decision_text_lower

            # For battle decisions, pass should have VERY low score
            # We deployed for a reason - we want to fight!
            if is_battle_decision:
                action.add_reasoning("Battle decision - should fight, not pass", -10.0)
                return [action]  # Don't add any other bonuses for pass during battle

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

    def track_action(self, action: EvaluatedAction, card_id: str = None):
        """
        Track that an action was chosen.

        This allows evaluators to avoid selecting the same action again
        if the game presents the same decision (avoid loops).

        Args:
            action: The action that was chosen
            card_id: Optional card ID associated with the action
        """
        # Track deploys
        if action.action_type == ActionType.DEPLOY and card_id:
            for evaluator in self.evaluators:
                if hasattr(evaluator, 'track_deploy'):
                    evaluator.track_deploy(card_id)
                    self.logger.debug(f"📝 Tracked deploy of card {card_id}")

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

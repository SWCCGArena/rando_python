"""
Static Brain Implementation

Uses the evaluator system (ported from Unity C# ranking logic)
to make strategic decisions based on card value, board position,
and resource availability.

This is the default brain - it plays "reasonably well" using
static rules and heuristics.
"""

import logging
from typing import List
from .interface import Brain, BrainContext, BrainDecision, DecisionType
from engine.evaluators import (
    DeployEvaluator, PassEvaluator, CombinedEvaluator, ActionTextEvaluator,
    ForceActivationEvaluator, CardSelectionEvaluator, BattleEvaluator,
    MoveEvaluator, DrawEvaluator
)
from engine.evaluators.base import DecisionContext as EvaluatorContext, ActionType

logger = logging.getLogger(__name__)


class StaticBrain(Brain):
    """
    Static rule-based brain using the evaluator system.

    Uses ranking algorithms ported from the Unity C# implementation
    to evaluate each possible action and choose the best one.
    """

    def __init__(self):
        """Initialize brain with evaluators"""
        self.evaluators = [
            DeployEvaluator(),           # Deploy actions
            CardSelectionEvaluator(),    # CARD_SELECTION decisions (sabacc, forfeit, etc.)
            BattleEvaluator(),           # Battle initiation
            MoveEvaluator(),             # Movement decisions
            DrawEvaluator(),             # Card draw decisions
            ActionTextEvaluator(),       # Text-based action ranking (catch-all)
            ForceActivationEvaluator(),  # INTEGER decisions for force activation
            PassEvaluator(),             # Pass/do nothing option
        ]
        self.combined_evaluator = CombinedEvaluator(self.evaluators)

        # Game tracking
        self.opponent_name = "Unknown"
        self.my_deck = ""
        self.their_deck_type = ""
        self.decisions_made = []

    def make_decision(self, context: BrainContext) -> BrainDecision:
        """
        Make a decision using the evaluator system.

        Args:
            context: BrainContext with board state and decision request

        Returns:
            BrainDecision with choice and reasoning
        """
        # Convert BrainContext to EvaluatorContext
        evaluator_context = self._convert_to_evaluator_context(context)

        # Use evaluator system to rank options
        best_action = self.combined_evaluator.evaluate_decision(evaluator_context)

        if best_action:
            # Track decision for learning
            self.decisions_made.append((
                context.decision_request.decision_type.value,
                best_action.action_id,
                best_action.score
            ))

            # Track action for loop prevention (deploys, moves)
            # Find card_id from the chosen action
            card_id = None
            for opt in context.decision_request.options:
                if opt.option_id == best_action.action_id and opt.card:
                    card_id = opt.card.card_id
                    break
            if card_id:
                self.combined_evaluator.track_action(best_action, card_id)

            # Build reasoning from evaluator output
            reasoning = best_action.display_text
            if best_action.reasoning:
                reasoning += " | " + " | ".join(best_action.reasoning)

            logger.debug(f">👾 StaticBrain chose: {best_action.display_text} (score: {best_action.score:.1f})")

            return BrainDecision(
                choice=best_action.action_id,
                reasoning=reasoning,
                confidence=min(1.0, best_action.score / 100.0),  # Normalize score to 0-1
                alternative_considered=None  # TODO: Track 2nd best option
            )
        else:
            # No action evaluated - pass or pick first option
            logger.warning("�  StaticBrain: No action evaluated, defaulting to first option")

            if context.decision_request.options:
                first_option = context.decision_request.options[0]
                return BrainDecision(
                    choice=first_option.option_id,
                    reasoning="Fallback: Selected first option (evaluators failed)",
                    confidence=0.5
                )
            else:
                return BrainDecision(
                    choice="",
                    reasoning="Fallback: No options available, passing",
                    confidence=0.0
                )

    def _convert_to_evaluator_context(self, brain_context: BrainContext) -> EvaluatorContext:
        """
        Convert BrainContext (brain interface) to EvaluatorContext (evaluator system).

        This bridges the brain interface and the evaluator implementation.
        """
        req = brain_context.decision_request
        board_state = brain_context.board_state

        # Extract data from DecisionRequest
        action_ids = [opt.option_id for opt in req.options]
        action_texts = [opt.display_text for opt in req.options]

        # Extract card-related data
        card_ids = []
        blueprints = []
        selectable = []

        for opt in req.options:
            if opt.card and opt.card.card_id:
                card_ids.append(opt.card.card_id)
                blueprints.append(opt.card.blueprint_id)
            elif opt.option_id:
                # For CARD_SELECTION, option_id is often the card_id
                card_ids.append(opt.option_id)
                blueprints.append("")
            selectable.append(True)  # All options in DecisionRequest should be selectable

        # Map DecisionType to evaluator format
        decision_type_map = {
            DecisionType.MULTIPLE_CHOICE: "MULTIPLE_CHOICE",
            DecisionType.CARD_SELECTION: "CARD_SELECTION",
            DecisionType.CARD_ACTION: "CARD_ACTION_CHOICE",
            DecisionType.INTEGER_INPUT: "INTEGER",
            DecisionType.ARBITRARY_CARDS: "ARBITRARY_CARDS",
        }

        evaluator_decision_type = decision_type_map.get(
            req.decision_type,
            "CARD_ACTION_CHOICE"  # Default
        )

        # Build extra context dict for INTEGER decisions
        extra = {}
        if req.min_value or req.max_value or req.default_value:
            extra['min'] = req.min_value
            extra['max'] = req.max_value
            extra['defaultValue'] = req.default_value

        # Build EvaluatorContext
        return EvaluatorContext(
            board_state=board_state,  # Pass through the actual engine BoardState
            decision_type=evaluator_decision_type,
            decision_text=req.prompt,
            decision_id=req.decision_id,
            phase=brain_context.board_state.phase if hasattr(brain_context.board_state, 'phase') else "",
            turn_number=brain_context.board_state.turn_number if hasattr(brain_context.board_state, 'turn_number') else 0,
            is_my_turn=brain_context.board_state.current_player == "me" if hasattr(brain_context.board_state, 'current_player') else True,
            action_ids=action_ids,
            action_texts=action_texts,
            no_pass=req.no_pass,  # From XML noPass parameter (True = must select, False = can pass)
            card_ids=card_ids,
            blueprints=blueprints,
            selectable=selectable,
            extra=extra,
        )

    def on_game_start(self, opponent_name: str, my_deck: str, their_deck_type: str):
        """Called when game starts - initialize tracking"""
        self.opponent_name = opponent_name
        self.my_deck = my_deck
        self.their_deck_type = their_deck_type
        self.decisions_made = []
        logger.info(f">� StaticBrain: Game started vs {opponent_name} ({their_deck_type})")

    def on_game_end(self, won: bool, final_state):
        """Called when game ends - log statistics"""
        logger.info(f">� StaticBrain: Game ended - {'Won' if won else 'Lost'}")
        logger.info(f"=� Decisions made: {len(self.decisions_made)}")

        # TODO: Update statistics database
        # TODO: Check for achievements

    def get_personality_name(self) -> str:
        """Return personality name"""
        return "Static"

    def on_turn_start(self, turn_number: int, board_state):
        """Called at turn start - log status"""
        if hasattr(board_state, 'total_my_power'):
            my_power = board_state.total_my_power()
            their_power = board_state.total_their_power()
            logger.debug(f"= Turn {turn_number}: Power {my_power}/{their_power}, Force {board_state.force_pile}")

    def get_welcome_message(self, opponent_name: str, deck_name: str) -> str:
        """Get welcome message for opponent"""
        return f"Hello {opponent_name}! GL HF! (StaticBrain v1.0)"

    def get_game_end_message(self, won: bool, score: int = None) -> str:
        """Get game end message"""
        if won:
            return "GG! Victory achieved."
        else:
            return "GG! Well played."

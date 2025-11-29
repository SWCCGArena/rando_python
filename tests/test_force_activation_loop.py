"""
Force Activation Loop Prevention Test Suite

Tests that the bot correctly skips force activation when it would
result in activating 0 force (which causes a loop with the server).

The specific loop scenario from the logs:
1. CARD_ACTION_CHOICE: "Choose Activate action or Pass"
   - Bot chose: "Activate Force" (score 100)
2. INTEGER: "Choose amount of Force to activate"
   - Bot chose: 0 (because force pile was 16)
3. Server sends Decision 1 again → infinite loop

The fix: Check if we'd activate 0 force BEFORE choosing to activate.

Run with: python -m pytest tests/test_force_activation_loop.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock
from engine.evaluators.action_text_evaluator import ActionTextEvaluator
from engine.evaluators.base import DecisionContext


class MockBoardState:
    """Mock board state for testing"""
    def __init__(self, force_pile=0, reserve_deck=20, used_pile=0,
                 activation=10, force_activated_this_turn=0):
        self.force_pile = force_pile
        self.reserve_deck = reserve_deck
        self.used_pile = used_pile
        self.activation = activation
        self.force_activated_this_turn = force_activated_this_turn
        self.strategy_controller = None


def create_activate_context(board_state):
    """Create a decision context for 'Activate Force or Pass'"""
    context = DecisionContext(
        board_state=board_state,
        decision_type='CARD_ACTION_CHOICE',
        decision_text='Choose Activate action or Pass',
        decision_id='1',
        phase='ACTIVATE',
        turn_number=5,
        is_my_turn=True,
        action_ids=['0', ''],
        action_texts=['Activate Force', 'Pass'],
        card_ids=['132', ''],
        no_pass=False,
        extra={'noPass': 'false'}
    )
    return context


class TestForceActivationLoopPrevention:
    """Tests that we correctly skip activation when it would cause a loop"""

    def test_high_force_pile_skips_activation(self):
        """When force pile is high (16) and already activated, should skip"""
        # This is the exact scenario from the log:
        # - force_pile = 16 (already high)
        # - force_activated_this_turn = 2 (already activated this turn)
        bs = MockBoardState(
            force_pile=16,
            reserve_deck=20,
            activation=10,
            force_activated_this_turn=2
        )
        context = create_activate_context(bs)

        evaluator = ActionTextEvaluator()
        actions = evaluator.evaluate(context)

        # Find the Activate Force action
        activate_action = next((a for a in actions if 'Activate Force' in a.display_text), None)
        pass_action = next((a for a in actions if 'Pass' in a.display_text), None)

        assert activate_action is not None
        assert pass_action is not None

        # Activate Force should have lower score than Pass (or be negative)
        assert activate_action.score < pass_action.score, \
            f"Expected Activate ({activate_action.score}) < Pass ({pass_action.score})"

        # Check reasoning mentions the skip
        reasoning_text = ' '.join(activate_action.reasoning)
        assert 'Skip' in reasoning_text or 'high' in reasoning_text.lower(), \
            f"Expected skip reasoning, got: {reasoning_text}"

    def test_force_pile_at_cap_skips_activation(self):
        """When force pile is at max (20), should skip activation"""
        bs = MockBoardState(
            force_pile=20,
            reserve_deck=20,
            activation=10,
            force_activated_this_turn=0
        )
        context = create_activate_context(bs)

        evaluator = ActionTextEvaluator()
        actions = evaluator.evaluate(context)

        activate_action = next((a for a in actions if 'Activate Force' in a.display_text), None)
        pass_action = next((a for a in actions if 'Pass' in a.display_text), None)

        assert activate_action.score < pass_action.score
        reasoning_text = ' '.join(activate_action.reasoning)
        assert 'max' in reasoning_text.lower()

    def test_low_reserve_for_destiny_skips_activation(self):
        """When reserve is at destiny threshold (3), should skip activation"""
        bs = MockBoardState(
            force_pile=5,
            reserve_deck=3,  # Need 3 for destiny
            used_pile=10,
            activation=10,
            force_activated_this_turn=0
        )
        context = create_activate_context(bs)

        evaluator = ActionTextEvaluator()
        actions = evaluator.evaluate(context)

        activate_action = next((a for a in actions if 'Activate Force' in a.display_text), None)
        pass_action = next((a for a in actions if 'Pass' in a.display_text), None)

        assert activate_action.score < pass_action.score
        reasoning_text = ' '.join(activate_action.reasoning)
        assert 'destiny' in reasoning_text.lower() or 'reserve' in reasoning_text.lower()

    def test_endgame_low_reserve_skips_activation(self):
        """In endgame (life < 10), reserve of 2 should be preserved"""
        bs = MockBoardState(
            force_pile=3,
            reserve_deck=2,  # Need 2 for destiny in endgame
            used_pile=3,     # Total life = 8 < 10
            activation=2,
            force_activated_this_turn=0
        )
        context = create_activate_context(bs)

        evaluator = ActionTextEvaluator()
        actions = evaluator.evaluate(context)

        activate_action = next((a for a in actions if 'Activate Force' in a.display_text), None)
        pass_action = next((a for a in actions if 'Pass' in a.display_text), None)

        assert activate_action.score < pass_action.score
        reasoning_text = ' '.join(activate_action.reasoning)
        assert 'endgame' in reasoning_text.lower() or 'destiny' in reasoning_text.lower()

    def test_normal_activation_still_works(self):
        """Normal case: should still activate when conditions are good"""
        bs = MockBoardState(
            force_pile=5,
            reserve_deck=15,
            activation=10,
            force_activated_this_turn=0
        )
        context = create_activate_context(bs)

        evaluator = ActionTextEvaluator()
        actions = evaluator.evaluate(context)

        activate_action = next((a for a in actions if 'Activate Force' in a.display_text), None)
        pass_action = next((a for a in actions if 'Pass' in a.display_text), None)

        # Should want to activate
        assert activate_action.score > pass_action.score, \
            f"Expected Activate ({activate_action.score}) > Pass ({pass_action.score})"

    def test_moderate_force_pile_still_activates(self):
        """When force pile is moderate (8), should still activate"""
        bs = MockBoardState(
            force_pile=8,
            reserve_deck=15,
            activation=10,
            force_activated_this_turn=0
        )
        context = create_activate_context(bs)

        evaluator = ActionTextEvaluator()
        actions = evaluator.evaluate(context)

        activate_action = next((a for a in actions if 'Activate Force' in a.display_text), None)
        pass_action = next((a for a in actions if 'Pass' in a.display_text), None)

        assert activate_action.score > pass_action.score


class TestLoopScenarioFromLogs:
    """Exact recreation of the loop from the game log"""

    def test_exact_log_scenario(self):
        """
        Exact scenario from rando_20251128_221842_vs_mserisman_loss.log:
        - force_pile = 16
        - Decision showed "8 of 10 remaining" = force_activated = 2
        - Bot should have passed but chose to activate, then activated 0
        """
        bs = MockBoardState(
            force_pile=16,
            reserve_deck=20,
            used_pile=10,
            activation=10,
            force_activated_this_turn=2  # 10 - 8 = 2
        )
        context = create_activate_context(bs)

        evaluator = ActionTextEvaluator()
        actions = evaluator.evaluate(context)

        activate_action = next((a for a in actions if 'Activate Force' in a.display_text), None)
        pass_action = next((a for a in actions if 'Pass' in a.display_text), None)

        # The fix: Activate should score lower than Pass
        assert activate_action.score < pass_action.score, \
            f"BUG: Activate ({activate_action.score}) should be < Pass ({pass_action.score}). " \
            f"Activate reasoning: {activate_action.reasoning}"

        # Verify the best action would be Pass
        best_action = max(actions, key=lambda a: a.score)
        assert 'Pass' in best_action.display_text, \
            f"Expected best action to be Pass, got: {best_action.display_text}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

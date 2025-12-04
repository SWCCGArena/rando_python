"""
Tests for ACTION_CHOICE pass behavior.

The key issue: For ACTION_CHOICE decisions, empty string may not be valid
even when noPass=false. The server expects one of the action_ids to be selected.
When we want to "pass", we need to find and select a "Cancel" or "Done" action.

This is different from CARD_ACTION_CHOICE where empty string works as pass.
"""

import pytest
import xml.etree.ElementTree as ET
from engine.evaluators.base import PassEvaluator, DecisionContext, ActionType
from engine.decision_safety import DecisionSafety


class MockBoardState:
    """Minimal mock board state for testing"""
    def __init__(self):
        self.force_pile = 5
        self.reserve_deck = 30
        self.hand_size = 6
        self.cards_in_hand = []

    def reserve_deck_low(self):
        return self.reserve_deck < 14


class TestPassEvaluatorActionChoice:
    """Test that PassEvaluator handles ACTION_CHOICE correctly"""

    def test_action_choice_with_cancel_action(self):
        """ACTION_CHOICE should use the Cancel action, not empty string"""
        context = DecisionContext(
            board_state=MockBoardState(),
            decision_type="ACTION_CHOICE",
            decision_text="About to retrieve 1 Force - Required responses",
            decision_id="1",
            phase="Deploy (turn #3)",
            turn_number=3,
            is_my_turn=True,
            action_ids=["0", "1"],
            action_texts=["Use Force or cancel retrieval", "Cancel retrieval"],
            no_pass=False,  # Passing is allowed
        )

        evaluator = PassEvaluator()
        assert evaluator.can_evaluate(context)

        actions = evaluator.evaluate(context)
        assert len(actions) == 1

        pass_action = actions[0]
        # Should use action_id "1" (Cancel retrieval), NOT empty string
        assert pass_action.action_id == "1", f"Expected '1' for Cancel, got '{pass_action.action_id}'"
        assert pass_action.action_type == ActionType.PASS
        assert "Cancel" in pass_action.display_text

    def test_action_choice_forfeited_card_response(self):
        """ACTION_CHOICE for 'Just forfeited X' should find cancel/done action"""
        context = DecisionContext(
            board_state=MockBoardState(),
            decision_type="ACTION_CHOICE",
            decision_text="Just forfeited <div>Supreme Leader Snoke</div> - Optional responses",
            decision_id="1",
            phase="Battle (turn #6)",
            turn_number=6,
            is_my_turn=True,
            action_ids=["0", "1"],
            action_texts=["Play interrupt response", "Done - no response"],
            no_pass=False,
        )

        evaluator = PassEvaluator()
        actions = evaluator.evaluate(context)

        pass_action = actions[0]
        # Should use "1" (Done - no response)
        assert pass_action.action_id == "1", f"Expected '1' for Done, got '{pass_action.action_id}'"

    def test_action_choice_no_cancel_uses_fallback(self):
        """ACTION_CHOICE with no cancel keyword should still work"""
        context = DecisionContext(
            board_state=MockBoardState(),
            decision_type="ACTION_CHOICE",
            decision_text="Some action choice",
            decision_id="1",
            phase="Deploy",
            turn_number=3,
            is_my_turn=True,
            action_ids=["0", "1"],
            action_texts=["Do something", "Do something else"],  # No cancel keyword
            no_pass=False,
        )

        evaluator = PassEvaluator()
        actions = evaluator.evaluate(context)

        pass_action = actions[0]
        # No cancel found - should use empty string (fallback)
        # The decision_safety layer will then force a selection
        assert pass_action.action_id == ""

    def test_card_action_choice_still_uses_empty_string(self):
        """CARD_ACTION_CHOICE should still use empty string for pass"""
        context = DecisionContext(
            board_state=MockBoardState(),
            decision_type="CARD_ACTION_CHOICE",
            decision_text="Choose Deploy action or Pass",
            decision_id="1",
            phase="Deploy",
            turn_number=3,
            is_my_turn=True,
            action_ids=["0", "1"],
            action_texts=["Deploy Vader", "Deploy Luke"],
            no_pass=False,
        )

        evaluator = PassEvaluator()
        actions = evaluator.evaluate(context)

        pass_action = actions[0]
        # CARD_ACTION_CHOICE should still use empty string
        assert pass_action.action_id == ""

    def test_action_choice_nopass_true_not_evaluated(self):
        """ACTION_CHOICE with noPass=true should not be evaluated by PassEvaluator"""
        context = DecisionContext(
            board_state=MockBoardState(),
            decision_type="ACTION_CHOICE",
            decision_text="Must choose an action",
            decision_id="1",
            phase="Deploy",
            turn_number=3,
            is_my_turn=True,
            action_ids=["0", "1"],
            action_texts=["Action A", "Action B"],
            no_pass=True,  # Cannot pass
        )

        evaluator = PassEvaluator()
        # Should not be able to evaluate when noPass=true
        assert not evaluator.can_evaluate(context)


class TestDecisionSafetyActionChoice:
    """Test that DecisionSafety handles ACTION_CHOICE empty responses"""

    def _create_decision_xml(self, decision_type: str, action_ids: list, action_texts: list,
                             no_pass: bool = False) -> ET.Element:
        """Helper to create decision XML element"""
        root = ET.Element("ge")
        root.set("decisionType", decision_type)
        root.set("id", "1")
        root.set("text", "Test decision")

        for action_id in action_ids:
            param = ET.SubElement(root, "parameter")
            param.set("name", "actionId")
            param.set("value", action_id)

        for action_text in action_texts:
            param = ET.SubElement(root, "parameter")
            param.set("name", "actionText")
            param.set("value", action_text)

        if no_pass:
            param = ET.SubElement(root, "parameter")
            param.set("name", "noPass")
            param.set("value", "true")

        return root

    def test_safety_forces_cancel_action(self):
        """Empty response for ACTION_CHOICE should be corrected to cancel action"""
        xml = self._create_decision_xml(
            "ACTION_CHOICE",
            ["0", "1"],
            ["Use Force", "Cancel retrieval"],
            no_pass=False
        )

        corrected, reason = DecisionSafety.ensure_valid_response(xml, "")

        # Should be corrected to "1" (Cancel retrieval)
        assert corrected == "1", f"Expected '1', got '{corrected}'"
        assert "SAFETY FORCED" in reason
        assert "cancel" in reason.lower()

    def test_safety_uses_last_action_when_no_cancel(self):
        """Empty response with no cancel action should use last action"""
        xml = self._create_decision_xml(
            "ACTION_CHOICE",
            ["0", "1", "2"],
            ["Action A", "Action B", "Action C"],  # No cancel keyword
            no_pass=False
        )

        corrected, reason = DecisionSafety.ensure_valid_response(xml, "")

        # Should use last action "2" as fallback
        assert corrected == "2", f"Expected '2' (last action), got '{corrected}'"
        assert "SAFETY FORCED" in reason

    def test_safety_does_not_modify_valid_response(self):
        """Valid non-empty response should not be modified"""
        xml = self._create_decision_xml(
            "ACTION_CHOICE",
            ["0", "1"],
            ["Use Force", "Cancel"],
            no_pass=False
        )

        corrected, reason = DecisionSafety.ensure_valid_response(xml, "0")

        # Should not be modified
        assert corrected == "0"
        assert reason == ""

    def test_card_action_choice_empty_allowed(self):
        """CARD_ACTION_CHOICE with noPass=false should allow empty response"""
        xml = self._create_decision_xml(
            "CARD_ACTION_CHOICE",
            ["0", "1"],
            ["Deploy", "Move"],
            no_pass=False
        )

        corrected, reason = DecisionSafety.ensure_valid_response(xml, "")

        # Empty should be allowed for CARD_ACTION_CHOICE
        assert corrected == ""
        assert reason == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

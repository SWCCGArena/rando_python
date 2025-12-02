"""
Forfeit Evaluator Test Suite

Tests for the forfeit decision logic in battles.

Key scenarios:
1. Prefer lowest forfeit value cards
2. Forfeit pilots before ships (pilots are lost when ships die)
3. Consider attrition remaining - exact matches get bonus
4. High-power cards should be kept if possible
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field
from typing import List, Optional

from engine.evaluators.card_selection_evaluator import CardSelectionEvaluator
from engine.evaluators.base import DecisionContext


@dataclass
class MockCardInPlay:
    """Mock card in play for testing"""
    card_id: str
    blueprint_id: str
    card_title: str = ""
    zone: str = "AT_LOCATION"
    owner: str = "rando_cal"
    location_index: int = 0
    target_card_id: Optional[str] = None  # Set if attached to another card (pilot on ship)
    attached_cards: List['MockCardInPlay'] = field(default_factory=list)  # Cards attached to this
    power: int = 0
    forfeit: int = 0


class MockBoardState:
    """Mock board state for testing"""
    def __init__(self):
        self.my_side = "dark"
        self.dark_attrition_remaining = 0
        self.light_attrition_remaining = 0
        self.cards_in_play = {}
        self.hit_cards = set()  # Track cards that have been "hit" in battle

    def add_card(self, card: MockCardInPlay):
        self.cards_in_play[card.card_id] = card

    def is_card_hit(self, card_id: str) -> bool:
        """Check if a card has been hit this battle"""
        return card_id in self.hit_cards

    def mark_card_hit(self, card_id: str):
        """Mark a card as hit"""
        self.hit_cards.add(card_id)


def create_forfeit_context(board_state, card_ids: List[str], attrition=0):
    """Create a decision context for 'Choose a card from battle to forfeit'"""
    if board_state.my_side == "dark":
        board_state.dark_attrition_remaining = attrition
    else:
        board_state.light_attrition_remaining = attrition

    context = DecisionContext(
        board_state=board_state,
        decision_type='CARD_SELECTION',
        decision_text='Choose a card from battle to forfeit',
        decision_id='1',
        phase='BATTLE',
        turn_number=5,
        is_my_turn=True,
        action_ids=[],
        action_texts=[],
        card_ids=card_ids,
        no_pass=True,
        extra={'min': '1', 'max': '1'}
    )
    return context


class TestForfeitLowestValue:
    """Tests that lowest forfeit value cards are preferred"""

    def test_prefer_lowest_forfeit_value(self):
        """Chiraneau (forfeit 4) should score higher than Vader (forfeit 7)"""
        bs = MockBoardState()

        # Add cards - Chiraneau with low forfeit, Vader with high
        chiraneau = MockCardInPlay(
            card_id="165",
            blueprint_id="9_97",
            card_title="Admiral Chiraneau",
            forfeit=4,
            power=2
        )
        vader = MockCardInPlay(
            card_id="163",
            blueprint_id="108_6",
            card_title="Darth Vader With Lightsaber",
            forfeit=7,
            power=6
        )
        bs.add_card(chiraneau)
        bs.add_card(vader)

        context = create_forfeit_context(bs, ["163", "165"], attrition=2)

        evaluator = CardSelectionEvaluator()
        actions = evaluator.evaluate(context)

        # Find the actions
        chiraneau_action = next((a for a in actions if a.action_id == "165"), None)
        vader_action = next((a for a in actions if a.action_id == "163"), None)

        assert chiraneau_action is not None
        assert vader_action is not None

        # Chiraneau should score higher (lower forfeit = better)
        assert chiraneau_action.score > vader_action.score, \
            f"Chiraneau ({chiraneau_action.score}) should score > Vader ({vader_action.score})"

    def test_forfeit_zero_is_best(self):
        """A card with forfeit 0 should be preferred over all others"""
        bs = MockBoardState()

        card_zero = MockCardInPlay(card_id="1", blueprint_id="1_1", card_title="Fodder", forfeit=0, power=1)
        card_five = MockCardInPlay(card_id="2", blueprint_id="1_2", card_title="Medium", forfeit=5, power=3)
        bs.add_card(card_zero)
        bs.add_card(card_five)

        context = create_forfeit_context(bs, ["1", "2"])

        evaluator = CardSelectionEvaluator()
        actions = evaluator.evaluate(context)

        zero_action = next(a for a in actions if a.action_id == "1")
        five_action = next(a for a in actions if a.action_id == "2")

        assert zero_action.score > five_action.score


class TestPilotBeforeShip:
    """Tests that pilots are forfeited before ships"""

    def test_pilot_on_ship_forfeited_first(self):
        """A pilot attached to a ship should be forfeited before the ship"""
        bs = MockBoardState()

        # Ship with pilot attached
        ship = MockCardInPlay(
            card_id="ship1",
            blueprint_id="1_100",
            card_title="Star Destroyer",
            forfeit=6,
            power=5
        )
        pilot = MockCardInPlay(
            card_id="pilot1",
            blueprint_id="1_50",
            card_title="TIE Pilot",
            forfeit=3,
            power=1,
            target_card_id="ship1"  # Pilot is attached to ship
        )
        ship.attached_cards = [pilot]

        bs.add_card(ship)
        bs.add_card(pilot)

        context = create_forfeit_context(bs, ["ship1", "pilot1"])

        evaluator = CardSelectionEvaluator()
        actions = evaluator.evaluate(context)

        ship_action = next(a for a in actions if a.action_id == "ship1")
        pilot_action = next(a for a in actions if a.action_id == "pilot1")

        # Pilot should score higher than ship
        assert pilot_action.score > ship_action.score, \
            f"Pilot ({pilot_action.score}) should score > Ship ({ship_action.score})"

        # Check reasoning mentions pilot on ship
        pilot_reasoning = ' '.join(pilot_action.reasoning)
        assert 'PILOT ON SHIP' in pilot_reasoning

    def test_ship_with_pilots_penalized(self):
        """A ship with pilots attached should be heavily penalized"""
        bs = MockBoardState()

        ship = MockCardInPlay(
            card_id="ship1",
            blueprint_id="1_100",
            card_title="Star Destroyer",
            forfeit=6,
            power=5
        )
        pilot = MockCardInPlay(
            card_id="pilot1",
            blueprint_id="1_50",
            card_title="TIE Pilot",
            forfeit=3,
            power=1,
            target_card_id="ship1"
        )
        ship.attached_cards = [pilot]

        standalone_char = MockCardInPlay(
            card_id="char1",
            blueprint_id="1_60",
            card_title="Stormtrooper",
            forfeit=4,
            power=2
        )

        bs.add_card(ship)
        bs.add_card(pilot)
        bs.add_card(standalone_char)

        context = create_forfeit_context(bs, ["ship1", "char1"])

        evaluator = CardSelectionEvaluator()
        actions = evaluator.evaluate(context)

        ship_action = next(a for a in actions if a.action_id == "ship1")
        char_action = next(a for a in actions if a.action_id == "char1")

        # Standalone char should score higher than ship with pilots
        assert char_action.score > ship_action.score


class TestAttritionMatching:
    """Tests for optimal attrition satisfaction"""

    def test_exact_attrition_match_bonus(self):
        """A card that exactly matches attrition remaining should get bonus"""
        bs = MockBoardState()

        exact_match = MockCardInPlay(
            card_id="exact",
            blueprint_id="1_1",
            card_title="Exact Match",
            forfeit=3,
            power=2
        )
        over_match = MockCardInPlay(
            card_id="over",
            blueprint_id="1_2",
            card_title="Over Match",
            forfeit=6,
            power=4
        )
        bs.add_card(exact_match)
        bs.add_card(over_match)

        # Set attrition to 3, matching exact_match's forfeit
        context = create_forfeit_context(bs, ["exact", "over"], attrition=3)

        evaluator = CardSelectionEvaluator()
        actions = evaluator.evaluate(context)

        exact_action = next(a for a in actions if a.action_id == "exact")
        over_action = next(a for a in actions if a.action_id == "over")

        # Exact match should score higher
        assert exact_action.score > over_action.score

        # Check reasoning mentions attrition
        exact_reasoning = ' '.join(exact_action.reasoning)
        assert 'attrition' in exact_reasoning.lower()


class TestPowerConsideration:
    """Tests that high-power cards are preserved when possible"""

    def test_low_power_preferred_over_high_power(self):
        """Low power cards should be preferred for forfeit when forfeit values equal"""
        bs = MockBoardState()

        low_power = MockCardInPlay(
            card_id="low",
            blueprint_id="1_1",
            card_title="Weak Guy",
            forfeit=4,
            power=2
        )
        high_power = MockCardInPlay(
            card_id="high",
            blueprint_id="1_2",
            card_title="Strong Guy",
            forfeit=4,  # Same forfeit
            power=6
        )
        bs.add_card(low_power)
        bs.add_card(high_power)

        context = create_forfeit_context(bs, ["low", "high"])

        evaluator = CardSelectionEvaluator()
        actions = evaluator.evaluate(context)

        low_action = next(a for a in actions if a.action_id == "low")
        high_action = next(a for a in actions if a.action_id == "high")

        # Low power should score higher (keep the strong one)
        assert low_action.score > high_action.score


class TestRealScenarioFromLog:
    """Recreate the exact scenario from the game log"""

    def test_vader_veers_chiraneau_with_2_attrition(self):
        """
        From log rando_20251129_042331_vs_elanz_win.log:
        - Cards: Vader (forfeit 7, power 6), Veers (forfeit 5, power 3), Chiraneau (forfeit 4, power 2)
        - Attrition: 2
        - Correct choice: Chiraneau (lowest forfeit, still covers 2 attrition)
        """
        bs = MockBoardState()

        vader = MockCardInPlay(
            card_id="163",
            blueprint_id="108_6",
            card_title="Darth Vader With Lightsaber",
            forfeit=7,
            power=6
        )
        veers = MockCardInPlay(
            card_id="164",
            blueprint_id="200_81",
            card_title="General Veers (V)",
            forfeit=5,
            power=3
        )
        chiraneau = MockCardInPlay(
            card_id="165",
            blueprint_id="9_97",
            card_title="Admiral Chiraneau",
            forfeit=4,
            power=2
        )

        bs.add_card(vader)
        bs.add_card(veers)
        bs.add_card(chiraneau)

        context = create_forfeit_context(bs, ["163", "164", "165"], attrition=2)

        evaluator = CardSelectionEvaluator()
        actions = evaluator.evaluate(context)

        vader_action = next(a for a in actions if a.action_id == "163")
        veers_action = next(a for a in actions if a.action_id == "164")
        chiraneau_action = next(a for a in actions if a.action_id == "165")

        # Chiraneau should be the best choice
        best_action = max(actions, key=lambda a: a.score)
        assert best_action.action_id == "165", \
            f"Expected Chiraneau to be best, got {best_action.display_text}"

        # Ranking should be: Chiraneau > Veers > Vader
        assert chiraneau_action.score > veers_action.score, \
            f"Chiraneau ({chiraneau_action.score}) should > Veers ({veers_action.score})"
        assert veers_action.score > vader_action.score, \
            f"Veers ({veers_action.score}) should > Vader ({vader_action.score})"


class TestMetadataFallback:
    """Tests that forfeit is loaded from card_loader if not on CardInPlay"""

    @patch('engine.card_loader.get_card')
    def test_falls_back_to_card_loader(self, mock_get_card):
        """If card.forfeit is 0, should fall back to card_loader"""
        bs = MockBoardState()

        # Card with forfeit=0 (not loaded)
        card = MockCardInPlay(
            card_id="1",
            blueprint_id="108_6",  # Vader
            card_title="Darth Vader With Lightsaber",
            forfeit=0,  # Not loaded!
            power=0
        )
        bs.add_card(card)

        # Mock card_loader to return the real forfeit
        mock_card_meta = MagicMock()
        mock_card_meta.forfeit_value = 7
        mock_card_meta.power_value = 6
        mock_card_meta.is_unique = True
        mock_card_meta.ability_value = 6
        mock_get_card.return_value = mock_card_meta

        context = create_forfeit_context(bs, ["1"])

        evaluator = CardSelectionEvaluator()
        actions = evaluator.evaluate(context)

        # Check that card_loader was called
        mock_get_card.assert_called()

        # The forfeit value should have been loaded
        action = actions[0]
        assert "Forfeit value 7" in ' '.join(action.reasoning)


class TestHitCardPrioritization:
    """Tests that hit cards are forfeited first"""

    def test_hit_card_forfeited_first(self):
        """A card that has been 'hit' in weapons segment should be forfeited first"""
        bs = MockBoardState()

        # Card with high forfeit but HIT
        vader = MockCardInPlay(
            card_id="1",
            blueprint_id="108_6",
            card_title="Darth Vader With Lightsaber",
            forfeit=7,
            power=6
        )
        # Card with low forfeit, NOT hit
        stormtrooper = MockCardInPlay(
            card_id="2",
            blueprint_id="1_300",
            card_title="Stormtrooper",
            forfeit=2,
            power=2
        )

        bs.add_card(vader)
        bs.add_card(stormtrooper)

        # Mark Vader as hit!
        bs.mark_card_hit("1")

        context = create_forfeit_context(bs, ["1", "2"])

        evaluator = CardSelectionEvaluator()
        actions = evaluator.evaluate(context)

        vader_action = next(a for a in actions if a.action_id == "1")
        stormtrooper_action = next(a for a in actions if a.action_id == "2")

        # Despite Vader having higher forfeit, he should score higher because he's HIT
        assert vader_action.score > stormtrooper_action.score, \
            f"Hit Vader ({vader_action.score}) should score > unhit Stormtrooper ({stormtrooper_action.score})"

        # Check reasoning mentions "HIT"
        vader_reasoning = ' '.join(vader_action.reasoning)
        assert 'ALREADY HIT' in vader_reasoning, \
            f"Expected 'ALREADY HIT' in reasoning, got: {vader_reasoning}"

    def test_multiple_hit_cards_prefer_lowest_forfeit(self):
        """When multiple cards are hit, still prefer the one with lowest forfeit"""
        bs = MockBoardState()

        # High forfeit, hit
        vader = MockCardInPlay(
            card_id="1",
            blueprint_id="108_6",
            card_title="Darth Vader With Lightsaber",
            forfeit=7,
            power=6
        )
        # Low forfeit, hit
        trooper = MockCardInPlay(
            card_id="2",
            blueprint_id="1_300",
            card_title="Stormtrooper",
            forfeit=2,
            power=2
        )
        # Medium forfeit, NOT hit
        veers = MockCardInPlay(
            card_id="3",
            blueprint_id="200_81",
            card_title="General Veers",
            forfeit=5,
            power=3
        )

        bs.add_card(vader)
        bs.add_card(trooper)
        bs.add_card(veers)

        # Mark both Vader and Trooper as hit
        bs.mark_card_hit("1")
        bs.mark_card_hit("2")

        context = create_forfeit_context(bs, ["1", "2", "3"])

        evaluator = CardSelectionEvaluator()
        actions = evaluator.evaluate(context)

        vader_action = next(a for a in actions if a.action_id == "1")
        trooper_action = next(a for a in actions if a.action_id == "2")
        veers_action = next(a for a in actions if a.action_id == "3")

        # Both hit cards should score higher than unhit Veers
        assert vader_action.score > veers_action.score
        assert trooper_action.score > veers_action.score

        # Among hit cards, the one with lower forfeit should score higher
        assert trooper_action.score > vader_action.score, \
            f"Hit Trooper (forfeit 2) should score > Hit Vader (forfeit 7)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
